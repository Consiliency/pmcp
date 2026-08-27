"""Name an npm/npx server's package via npm's own parser, or refuse.

Consiliency/pmcp#195. ``version_checker._npm_package_arg`` models npm's flag
grammar by hand, and that model has been repaired five times (#180 -> #192 ->
#194 -> #195 -> the 2.5.2 nullable-spelling fix). Every defect was in the rules
*around* the tables, and every one produced a **confident wrong answer** --
which ``refresher._same_package`` reads as POSITIVE CONFIRMATION that a cached
tool description still describes the configured package, so one server gets
served another server's tool descriptions.

This module drives ``_npm_resolve.js``, a persistent node child that uses the
host npm's own ``nopt``, its own ``@npmcli/config`` definitions, its own
``npm-package-arg``, and a faithful port of the ``npx-cli.js`` pre-scan.

**Tri-state, and it matters which is which.**

============ ===================================== ==========================
Outcome      Meaning                               ``_npm_package_arg`` does
============ ===================================== ==========================
``IDENTITY`` every gate passed                     return the spec raw
``REFUSED``  a gate tripped, or the request timed   return ``None`` --
             out / the child died / the response    **never scan the tables**
             failed schema
``UNAVAILABLE`` **spawn** failed: no node, no npm  fall through to the tables
             root
============ ===================================== ==========================

``UNAVAILABLE`` is reserved for the case where we learned *nothing about npm*,
only that it is not installed -- there the flag tables are the only thing a host
can do, and they are the pre-#195 behaviour. ``REFUSED`` is everything else:
falling back to the known-incomplete tables in a situation that proves the
host's parser is not modelled by this code is fail-OPEN, and is the exact
failure this change exists to remove.

**Why refusing is safe.** A refusal yields ``("unknown", None)`` from
``detect_package_type``; the cache stores ``package_type="unknown"``; and
``refresher._same_package`` treats ``"unknown"`` as unidentified, so *every*
comparison fails closed. (It is emphatically **not** safe because the coarse
``command + args`` fallback string is unique -- it is not: two refused servers
with identical argv and different env share that string. The poisoned *type*,
not the string, is what makes refusal safe.)

**The honest cost.** A refused server re-connects and regenerates its
descriptions on every refresh cycle and is listed permanently stale by
``check_staleness``. That is a cost, not a correctness problem, and it is why
the allowlist is drawn to cover the plain shape rather than as tightly as
possible. Measured: all 79 npm-family servers in ``manifest.yaml`` use the plain
``npx -y <pkg>`` shape and resolve.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import re
import subprocess
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

_HELPER = Path(__file__).with_name("_npm_resolve.js")

# A single in-flight query is bounded at 1.0 s. `detect_package_type` is
# synchronous and is called from inside async coroutines, so an unbounded read
# would stall the event loop. A blocking `readline()` cannot implement a
# timeout, hence the reader thread below.
_QUERY_TIMEOUT = 1.0

# Handshake budget. The child runs its self-test corpus before emitting the
# handshake; measured at ~43 ms on a warm host, so 10 s is enormous slack and
# exists only so a pathological host cannot hang startup forever.
_HANDSHAKE_TIMEOUT = 10.0

# A dead child re-arms after this long. A one-shot "respawn once, ever" budget
# strands a days-running gateway after two transient child deaths, which is a
# silent fleet-wide loss of auto-update behind a single WARNING. Time-based
# means a transient failure costs one cooldown window, not the process lifetime.
_RESPAWN_COOLDOWN = 60.0

# ---------------------------------------------------------------------------
# Step 1 (parent half): the gates that need no parser
# ---------------------------------------------------------------------------

# npm reads any environment variable matching `/^npm_config_/i` as a config
# option -- verified: `env npm_config_package=evil-pkg npx -y probe` fetches
# `evil-pkg`. `PATH` selects WHICH npm runs (npm 11.6.2 and 11.19.0 disagree on
# `npx --name foo probe`). `HOME` relocates `~/.npmrc`, so `HOME=/x` with
# `/x/.npmrc` containing `package=other` redirects resolution entirely.
# `PREFIX`/`NVM_*` relocate the npm installation; `NODE_PATH`/`NODE_OPTIONS`
# change what the parser itself loads.
_ENV_GATE_EXACT = frozenset({"PATH", "HOME", "NODE_PATH", "NODE_OPTIONS", "PREFIX"})
_ENV_GATE_PREFIXES = ("npm_config_", "nvm_")


def _gate_relevant_env(env: Mapping[str, str] | None) -> dict[str, str]:
    """The subset of a server's env OVERLAY that the step-1 gate inspects.

    **Overlay, not merged environment.** ``sanitized_subprocess_env`` returns
    ``os.environ`` plus the server's own keys, and ``os.environ`` always carries
    ``PATH`` and ``HOME`` -- so gating the merged environment would refuse every
    npm server on every host. The identity question is "does *this server's
    configuration* redirect npm's resolution", and only the overlay can.

    Memoisation keys on exactly this subset. Keying on the whole overlay would
    make two configs differing only in an ungated key miss each other's cache
    entry (harmless but wasteful); keying on nothing would make two configs
    differing only in a GATED key share one (a wrong answer).
    """
    if not env:
        return {}
    relevant = {}
    for key, value in env.items():
        lowered = key.lower()
        if key.upper() in _ENV_GATE_EXACT or lowered.startswith(_ENV_GATE_PREFIXES):
            relevant[key] = value
    return relevant


def _env_overlay_is_plain(env: Mapping[str, str] | None) -> str | None:
    """``None`` if the overlay sets no resolution-redirecting key, else a reason."""
    relevant = _gate_relevant_env(env)
    if relevant:
        return f"server env sets {sorted(relevant)!r}, which can redirect npm"
    return None


def _process_env_is_plain() -> str | None:
    """``None`` if the GATEWAY's own environment cannot redirect npm.

    The overlay gate is not enough: an ``npm_config_*`` variable exported into
    pmcp's own process environment redirects resolution for every server at
    once, invisibly. Verified:
    ``env npm_config_package=evil-pkg npm_config_registry=http://127.0.0.1:9
    npx -y probe`` fetches ``http://127.0.0.1:9/evil-pkg``.

    ``PATH``/``HOME`` are deliberately NOT checked here -- every process has
    them, and the child resolves npm through the very ``PATH`` this process
    runs with, so they are inputs to the answer rather than distortions of it.
    """
    for key in os.environ:
        if key.lower().startswith("npm_config_") or key.upper() == "NODE_OPTIONS":
            return f"gateway environment sets {key!r}, which can redirect npm"
    return None


def _has_local_prefix(cwd: str | None) -> str | None:
    """``None`` if npm would set no local prefix from *cwd*, else a reason.

    npm's own rule, read from ``@npmcli/config/lib/index.js:695-716``:
    ``hasPackageJson || await dirExists(p, 'node_modules')``, walking up from
    the working directory. A local prefix brings a project ``.npmrc`` into
    scope -- verified: with the working directory inside a node project whose
    ``.npmrc`` sets ``package=rcfile-pkg``, ``npx plainbin`` resolved to
    ``rcfile-pkg``. Worse, with ``node_modules/.bin/<name>`` present, npx ran
    the LOCAL bin with no registry fetch at all, so the registry name is not
    the package that runs.

    The walk starts at the **effective** cwd -- the server's own ``cwd`` if it
    declares one, else this process's. Reading only the server's ``cwd`` was
    wrong: npm resolves from the *process* cwd when a server declares none, so
    running the gateway from inside a node project reopened the whole class.
    """
    try:
        start = Path(cwd).resolve() if cwd else Path.cwd()
    except OSError as exc:  # pragma: no cover - unreadable cwd
        return f"cannot resolve the effective cwd: {exc}"
    for directory in (start, *start.parents):
        if (directory / "package.json").exists() or (
            directory / "node_modules"
        ).is_dir():
            return (
                f"npm would set a local prefix at {directory} "
                "(a project .npmrc or a local bin can redirect resolution)"
            )
    return None


# ---------------------------------------------------------------------------
# Tri-state result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NpmResolution:
    """The resolver's answer. Exactly one of the three states."""

    status: Literal["IDENTITY", "REFUSED", "UNAVAILABLE"]
    spec: str | None = None
    reason: str | None = None

    @property
    def is_identity(self) -> bool:
        return self.status == "IDENTITY"

    @property
    def is_refused(self) -> bool:
        return self.status == "REFUSED"

    @property
    def is_unavailable(self) -> bool:
        return self.status == "UNAVAILABLE"


def _refused(reason: str) -> NpmResolution:
    return NpmResolution(status="REFUSED", reason=reason)


def _unavailable(reason: str) -> NpmResolution:
    return NpmResolution(status="UNAVAILABLE", reason=reason)


_SPEC_SHAPE = re.compile(r"^[^\s]+$")


class NpmResolver:
    """A lazily spawned, persistent node child that names npm packages.

    Persistent because one-shot is unaffordable: 79 one-shot resolves measured
    4.02 s, while a persistent child costs 43 ms of startup plus ~0.5 ms per
    query. ``detect_package_type`` is synchronous and is called from inside
    async coroutines, so seconds of blocking would stall the event loop.
    """

    def __init__(self, helper: Path | None = None) -> None:
        self._helper = helper or _HELPER
        self._lock = threading.Lock()
        self._proc: subprocess.Popen[str] | None = None
        self._reader: _LineReader | None = None
        self._next_id = 0
        self._last_spawn_at = 0.0
        self._generation = 0
        # Sticky states, set once and never cleared. A sticky resolver keeps NO
        # child, so the per-resolve npm-version re-stat cannot fire and recovery
        # needs a gateway restart. That is deliberate: both sticky states mean
        # the host told us something durable (npm is absent; or npm is present
        # and this code cannot model it), not that a request went wrong.
        self._sticky: NpmResolution | None = None
        self._warned = False
        self._memo: dict[tuple[object, ...], NpmResolution] = {}
        self._npm_version: str | None = None
        # Attempts, not successes. On a node-less host `Popen` raises and no
        # child is ever created, so counting successes would make "exactly one
        # spawn attempt across 50 resolves" trivially true whatever the code
        # did.
        self.spawn_attempts = 0
        self.spawn_count = 0

    # -- lifecycle ---------------------------------------------------------

    def _warn_once(self, message: str) -> None:
        if not self._warned:
            self._warned = True
            logger.warning(
                "npm package identity is DISABLED for this process: %s. "
                "npm/npx servers will report package_type='unknown', which "
                "means their descriptions refresh every cycle and "
                "gateway.update_server cannot name a package for them. "
                "THIS DOES NOT RECOVER ON ITS OWN -- a disabled resolver keeps "
                "no child process, so nothing re-checks the host; fix the cause "
                "and RESTART THE GATEWAY. Current state is also reported as "
                "gateway.health -> gateway_diagnostics.npm_identity.",
                message,
            )

    def _terminate(self) -> None:
        """Kill the child and drop the memo. Call with the lock held."""
        proc, self._proc = self._proc, None
        reader, self._reader = self._reader, None
        # Every memoised answer was produced by the child being torn down. A
        # memo hit never reaches the child, so the per-resolve npm-version
        # re-stat would never fire again and a stale IDENTITY would be served
        # indefinitely. Dropping the memo with the child keeps the drift
        # defence live.
        self._memo.clear()
        if reader is not None:
            reader.stop()
        if proc is not None:
            try:
                proc.kill()
            except OSError:  # pragma: no cover - already reaped
                pass
            try:
                proc.wait(timeout=2.0)
            except Exception:  # pragma: no cover - stubborn child
                pass
            for stream in (proc.stdin, proc.stdout, proc.stderr):
                try:
                    if stream is not None:
                        stream.close()
                except OSError:  # pragma: no cover
                    pass

    def _spawn(self) -> NpmResolution | None:
        """Start the child and consume its handshake. Lock held. ``None`` = ok."""
        if not self._helper.is_file():
            return _unavailable(f"helper script missing: {self._helper}")
        self._last_spawn_at = time.monotonic()
        self.spawn_attempts += 1
        try:
            proc = subprocess.Popen(
                ["node", str(self._helper)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                shell=False,
                bufsize=1,
            )
        except OSError as exc:
            # node is not installed. This is the ONE case that learns nothing
            # about npm, so it is the one case that falls back to the tables.
            return _unavailable(f"cannot spawn node: {exc}")
        self.spawn_count += 1
        self._generation += 1
        self._proc = proc
        self._reader = _LineReader(proc)

        line = self._reader.read(_HANDSHAKE_TIMEOUT)
        if line is None:
            self._terminate()
            return _refused("child produced no handshake")
        try:
            handshake = json.loads(line)
        except ValueError:
            self._terminate()
            return _refused("child handshake was not JSON")
        if not isinstance(handshake, dict) or handshake.get("handshake") != 1:
            self._terminate()
            return _refused("child handshake failed schema")

        status = handshake.get("status")
        if status == "UNAVAILABLE":
            self._terminate()
            return _unavailable(str(handshake.get("reason") or "npm not found"))
        if status != "OK":
            self._terminate()
            return _refused(str(handshake.get("reason") or "child refused at startup"))

        version = handshake.get("npmVersion")
        if version != self._npm_version:
            # npm changed under us (or this is the first spawn). Nothing
            # memoised against the old parser may survive.
            self._memo.clear()
            self._npm_version = version if isinstance(version, str) else None
        return None

    def _ensure_child(self) -> NpmResolution | None:
        """Lock held. ``None`` when a live child is available."""
        if self._sticky is not None:  # pragma: no cover - `resolve` checks first
            return self._sticky
        if self._proc is not None and self._proc.poll() is None:
            return None
        if self._proc is not None:
            self._terminate()
        if self.spawn_attempts and (
            time.monotonic() - self._last_spawn_at < _RESPAWN_COOLDOWN
        ):
            # Inside the cooldown after a death: refuse cheaply rather than
            # spawn-storm. Not sticky -- the next window re-arms.
            return _refused("npm resolver child is cooling down after a failure")
        outcome = self._spawn()
        if outcome is None:
            return None
        if outcome.is_unavailable or self._sticky_worthy(outcome):
            self._sticky = outcome
            self._warn_once(outcome.reason or outcome.status)
        return outcome

    @staticmethod
    def _sticky_worthy(outcome: NpmResolution) -> bool:
        """A startup REFUSED is durable; an in-flight one is not.

        A refusal produced while *starting* the child -- failed self-test,
        unrecognised ``npx-cli.js``, a parser that will not load, a handshake
        that never came -- says something about the host that will still be
        true on the next query. An in-flight refusal says something about one
        argv.
        """
        return outcome.is_refused

    # -- the query ---------------------------------------------------------

    def resolve(
        self,
        command: str,
        args: list[str],
        env: Mapping[str, str] | None,
        cwd: str | None,
    ) -> NpmResolution:
        """Name the package *command*/*args* would run, or refuse.

        *env* is the server's environment **OVERLAY**, not a merged
        environment; *cwd* is the server's declared working directory, or
        ``None`` to mean "this process's". Both are REQUIRED and deliberately
        undefaulted: a defaulted ``None`` is the same silent fail-open this
        module exists to remove, and requiring them turns every unconverted call
        site into a type error rather than a wrong answer.
        """
        if command not in ("npx", "npm"):
            return _refused(f"command is not a bare npx/npm: {command!r}")

        # Parent-side gates first: they need no child, so a refusal here costs
        # nothing and a node-less host never even attempts a spawn for them.
        for gate in (
            _env_overlay_is_plain(env),
            _has_local_prefix(cwd),
        ):
            if gate is not None:
                return _refused(gate)

        key: tuple[object, ...] = (
            command,
            tuple(args),
            frozenset(_gate_relevant_env(env).items()),
            cwd,
        )

        with self._lock:
            process_gate = _process_env_is_plain()
            if process_gate is not None and self._sticky is None:
                self._sticky = _refused(process_gate)
                self._warn_once(process_gate)
            if self._sticky is not None:
                # Before the memo lookup, not after: a sticky refusal must win
                # over an answer this resolver produced while it was still
                # healthy.
                return self._sticky
            hit = self._memo.get(key)
            if hit is not None:
                return hit
            outcome = self._ensure_child()
            if outcome is not None:
                return outcome
            result = self._query_locked(command, args)
            # Memoise only an answer the child SURVIVED giving. `_query_locked`
            # tears the child down on a timeout, a death, a protocol violation
            # and on STALE -- which drops the memo -- and then returns REFUSED
            # for that one request. Writing that REFUSED back into the fresh
            # memo would make one transient stall permanently disable identity
            # for that argv: a gateway re-queries the same fixed server set
            # every refresh cycle, so the entry would be re-served for the
            # process lifetime even after a healthy respawn. The STALE reason
            # string literally says "retry after respawn", which a memo hit
            # makes impossible.
            #
            # A gate or parse refusal leaves the child alive and is a fact about
            # the argv, so it memoises. UNAVAILABLE never does: it is either
            # sticky (returned above) or a transient spawn state.
            child_survived = self._proc is not None
            if child_survived and (result.is_identity or result.is_refused):
                self._memo[key] = result
            return result

    def _query_locked(self, command: str, args: list[str]) -> NpmResolution:
        proc = self._proc
        reader = self._reader
        assert proc is not None and reader is not None and proc.stdin is not None
        self._next_id += 1
        request_id = self._next_id
        payload = json.dumps({"id": request_id, "command": command, "args": args})
        try:
            proc.stdin.write(payload + "\n")
            proc.stdin.flush()
        except (BrokenPipeError, ValueError, OSError) as exc:
            self._terminate()
            return _refused(f"npm resolver child died before the query: {exc}")

        line = reader.read(_QUERY_TIMEOUT)
        if line is None:
            # Timeout or death. Terminate and drop the child BEFORE releasing
            # the lock, so a later query cannot read an orphaned response and
            # attribute it to the wrong request.
            self._terminate()
            return _refused("npm resolver child timed out or died")
        try:
            response = json.loads(line)
        except ValueError:
            self._terminate()
            return _refused("npm resolver child sent malformed JSON")
        if not isinstance(response, dict) or response.get("id") != request_id:
            # A protocol violation is a desynchronised stream: whatever comes
            # next belongs to some other request. Terminate.
            self._terminate()
            return _refused("npm resolver child response did not match the request")

        status = response.get("status")
        if status == "STALE":
            # npm was upgraded in place under a running gateway. The child has
            # exited; the memo went with it.
            self._terminate()
            return _refused("npm changed under the resolver; retry after respawn")
        if status == "REFUSED":
            return _refused(str(response.get("reason") or "refused"))
        if status != "IDENTITY":
            self._terminate()
            return _refused(f"npm resolver child sent an unknown status: {status!r}")

        spec = response.get("spec")
        if not isinstance(spec, str) or not _SPEC_SHAPE.match(spec):
            self._terminate()
            return _refused("npm resolver child sent an unusable spec")
        return NpmResolution(status="IDENTITY", spec=spec)

    # -- introspection -----------------------------------------------------

    def status_summary(self) -> str:
        """One line for gateway.health. **Never spawns.**

        A fleet-wide loss of npm identity is otherwise visible only as a single
        WARNING at whatever moment the first npm server was resolved, which is
        easy to miss in a long-running gateway's log. Reporting it in
        gateway.health makes it answerable on demand.

        Deliberately reports only what is already known: forcing a spawn here
        would make a diagnostic call start a subprocess, and would report a
        healthy resolver on a gateway that has never resolved anything.
        """
        with self._lock:
            if self._sticky is not None:
                if self._sticky.is_unavailable:
                    return f"fallback to flag tables ({self._sticky.reason})"
                return f"DISABLED, refusing every query ({self._sticky.reason})"
            if self._proc is None or self._proc.poll() is not None:
                return "not started (no npm/npx server resolved yet)"
            return f"active (npm {self._npm_version})"

    # -- teardown ----------------------------------------------------------

    def close(self) -> None:
        with self._lock:
            self._terminate()


class _LineReader:
    """A bounded-wait line reader over a child's stdout.

    A blocking ``readline()`` cannot implement a timeout, so the read happens
    on a daemon thread and the caller waits on a queue instead.
    """

    def __init__(self, proc: subprocess.Popen[str]) -> None:
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._stdout = proc.stdout
        self._stopped = threading.Event()
        self._thread = threading.Thread(
            target=self._pump, name="npm-resolver-reader", daemon=True
        )
        self._thread.start()

    def _pump(self) -> None:
        stream = self._stdout
        if stream is None:  # pragma: no cover - Popen always gives one
            self._queue.put(None)
            return
        try:
            for line in stream:
                if self._stopped.is_set():
                    return
                self._queue.put(line)
        except (ValueError, OSError):  # pragma: no cover - closed under us
            pass
        finally:
            self._queue.put(None)

    def read(self, timeout: float) -> str | None:
        """The next line, or ``None`` on timeout or child exit."""
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def stop(self) -> None:
        self._stopped.set()


_resolver: NpmResolver | None = None
_resolver_lock = threading.Lock()


def get_resolver() -> NpmResolver:
    """The process-wide resolver.

    One child per process, spawned lazily on the first npm/npx server and kept
    for the process lifetime. There is deliberately **no idle reaper**: an
    earlier design had one *and* a sticky-unavailable state, so the feature died
    permanently after the first idle period.
    """
    global _resolver
    with _resolver_lock:
        if _resolver is None:
            _resolver = NpmResolver()
        return _resolver


def reset_resolver_for_tests() -> None:
    """Drop the process-wide resolver. Tests only."""
    global _resolver
    with _resolver_lock:
        if _resolver is not None:
            _resolver.close()
        _resolver = None
