"""1Password secret references for downstream servers (Consiliency/pmcp#487 P2).

An operator-supplied credential may be a 1Password secret reference
(``op://vault/item/field``). pmcp resolves it for each spawn with a dedicated
``op`` process authenticated by a gateway-only **service-account token** read
from a credential file (never the environment), and passes the value only to
the child it was resolved for. Nothing resolved is ever written onto a config,
``os.environ`` or a store.

This module holds:

* the reference grammar (plan 3.3): :func:`is_reference_shaped` classifies,
  :func:`parse_reference` accepts the supported subset;
* the token reader (plan 3.1): :func:`read_service_account_token`;
* the trusted ``op`` executable (plan 3.4): :func:`trusted_op_executable`;
* ``op``'s environment (:func:`op_subprocess_env`, one of the env builders
  ``tests/test_store_reader_inventory.py`` knows);
* the resolver (plan 3.4): at most two ``op`` processes, identical in-flight
  references single-flighted, bounded time and output, typed failures;
* the per-spawn plan (plan 3.2, 3.5): :func:`spawn_plan` decides, from a
  config's recorded provenance, which env values pmcp resolves, which it
  refuses, and which it passes through verbatim, without calling ``op``;
  :func:`resolve_spawn_env` resolves the plan into a fresh overlay.

Every failure is a :class:`~pmcp.credential_state.CredentialUnavailableError`
with a reason from ``credential_state``: value-free, never echoing ``op``'s
stderr or argv.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import stat
import sys
import time
import weakref
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pmcp import credential_state as cs
from pmcp.credential_state import CredentialUnavailableError
from pmcp.secret_registry import register_secret_value

if TYPE_CHECKING:
    from pmcp.types import ResolvedServerConfig

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Grammar (plan 3.3)
# --------------------------------------------------------------------------- #

_ASCII_WHITESPACE = " \t\n\r\x0b\x0c"

#: One segment: 1-64 characters of ``[A-Za-z0-9 _.-]``, a space only inside,
#: never ``.`` or ``..``.
_SEGMENT = (
    r"(?!\.\.?(?:/|\Z))"
    r"[A-Za-z0-9_.\-](?:[A-Za-z0-9 _.\-]{0,62}[A-Za-z0-9_.\-])?"
)
#: The supported subset: ``op://<vault>/<item>/<field>`` or
#: ``op://<vault>/<item>/<section>/<field>``, scheme in any case. No query,
#: fragment, ``%``, ``@``, ``:``, ``$``, ``\\``, control, NUL or bidi
#: character can match (none is in a segment's class).
REFERENCE_PATTERN = re.compile(
    rf"[oO][pP]://{_SEGMENT}/{_SEGMENT}/{_SEGMENT}(?:/{_SEGMENT})?"
)


def is_reference_shaped(value: object) -> bool:
    """Is ``value`` reference-shaped? Starts with ``op://`` after leading ASCII
    whitespace, in any case. Everything else is plain (plan 3.3)."""
    if not isinstance(value, str):
        return False
    return value.lstrip(_ASCII_WHITESPACE)[:5].lower() == "op://"


def parse_reference(value: str) -> str | None:
    """``value`` when it is a SUPPORTED reference (``re.fullmatch``), else ``None``."""
    return value if REFERENCE_PATTERN.fullmatch(value) else None


def reference_fingerprint(reference: str) -> str:
    """A short, stable, value-free id of a reference, for logs and single-flight."""
    return hashlib.sha256(reference.encode("utf-8")).hexdigest()[:12]


# --------------------------------------------------------------------------- #
# Names that never come from a file or the inherited environment (plan 3.1)
# --------------------------------------------------------------------------- #

#: Never loaded from a file, refused by ``pmcp secrets set/sync`` and
#: ``gateway.auth_connect``, removed from ``os.environ`` at startup and
#: stripped from every INHERITED child environment.
OP_CONTROL_NAMES = frozenset(
    {
        "OP_SERVICE_ACCOUNT_TOKEN",
        "CREDENTIALS_DIRECTORY",
        "OP_CONNECT_TOKEN",
        "OP_CONNECT_HOST",
        "OP_CONFIG_DIR",
        "OP_CACHE",
        "OP_ACCOUNT",
    }
)


def is_op_control_name(name: str) -> bool:
    """``OP_SERVICE_ACCOUNT_TOKEN`` and its family, and every ``OP_SESSION_*``."""
    upper = name.upper()
    return upper in OP_CONTROL_NAMES or upper.startswith("OP_SESSION_")


#: ``CREDENTIALS_DIRECTORY`` as the process started, captured by
#: :func:`capture_startup_environment` before any file is read.
_CREDENTIALS_DIRECTORY: str | None = None
_CAPTURED = False


def capture_startup_environment() -> None:
    """Capture ``CREDENTIALS_DIRECTORY``, then drop the op names from ``os.environ``.

    Called once by the startup load before any file is read. A second call
    (a library caller, a test) never re-captures: the value a later file or
    caller could have set is not trusted.
    """
    global _CREDENTIALS_DIRECTORY, _CAPTURED
    if not _CAPTURED:
        _CAPTURED = True
        directory = os.environ.get("CREDENTIALS_DIRECTORY")
        _CREDENTIALS_DIRECTORY = directory if directory else None
    for name in [n for n in os.environ if is_op_control_name(n)]:
        os.environ.pop(name, None)


def reset_for_tests() -> None:
    """Forget the captured startup state, the token cache and resolvers. **Test-only.**"""
    global _CREDENTIALS_DIRECTORY, _CAPTURED, _OPERATOR_OP_PATH
    _CREDENTIALS_DIRECTORY = None
    _CAPTURED = False
    _OPERATOR_OP_PATH = None
    _TOKEN_CACHE.clear()
    _RESOLVERS.clear()
    _test_overrides.clear()


#: Test seams (never set in production): ``op_search_paths``,
#: ``trusted_owner_uids``, ``state_dir``.
_test_overrides: dict[str, Any] = {}


# --------------------------------------------------------------------------- #
# The service-account token (plan 3.1)
# --------------------------------------------------------------------------- #

TOKEN_FILE_NAME = "op-service-account-token"
_TOKEN_MAX_BYTES = 4096
_TOKEN_PATTERN = re.compile(r"ops_[\x21-\x7e]+")

TokenSource = Literal["systemd_credential", "credstore", "pmcp_config"]


@dataclass(frozen=True)
class _TokenCandidate:
    source: TokenSource
    path: Path
    confine_to: Path


@dataclass(frozen=True)
class TokenRead:
    """A usable token and where it came from (never printed)."""

    token: str = field(repr=False)
    source: TokenSource
    digest: str


_TOKEN_CACHE: dict[str, tuple[tuple[int, int, int, int], TokenRead]] = {}


def _platform_supported() -> bool:
    return os.name == "posix" and sys.platform != "win32"


def _xdg_config_home() -> Path | None:
    from pmcp.env_store import env_key_is_operator_supplied

    if env_key_is_operator_supplied("XDG_CONFIG_HOME"):
        value = os.environ.get("XDG_CONFIG_HOME", "")
        if value and os.path.isabs(value):
            return Path(value)
    return None


def token_candidates() -> list[_TokenCandidate]:
    """The token paths, in lookup order (plan 3.1). Never an environment variable."""
    from pmcp.home_identity import optional_operator_home

    candidates: list[_TokenCandidate] = []
    if _CREDENTIALS_DIRECTORY and os.path.isabs(_CREDENTIALS_DIRECTORY):
        directory = Path(_CREDENTIALS_DIRECTORY)
        candidates.append(
            _TokenCandidate(
                "systemd_credential", directory / TOKEN_FILE_NAME, directory
            )
        )
    home = optional_operator_home()
    xdg = _xdg_config_home()
    if xdg is not None:
        candidates.append(
            _TokenCandidate("credstore", xdg / "credstore" / TOKEN_FILE_NAME, xdg)
        )
    elif home is not None:
        candidates.append(
            _TokenCandidate(
                "credstore", home / ".config" / "credstore" / TOKEN_FILE_NAME, home
            )
        )
    if home is not None:
        candidates.append(
            _TokenCandidate(
                "pmcp_config", home / ".config" / "pmcp" / TOKEN_FILE_NAME, home
            )
        )
    return candidates


class _NotPresent(Exception):
    pass


def _open_token(candidate: _TokenCandidate) -> int:
    """Open the token leaf by a confined walk; raise ``_NotPresent`` when absent.

    Any symlink on the way (below ``confine_to``) or at the leaf is refused
    (``token_invalid``), as is a non-regular file -- without blocking on a
    FIFO or device (``O_NONBLOCK``).
    """
    from pmcp.atomic_write import ConfinedWriteError, _walk_confined

    if not os.path.isdir(candidate.confine_to):
        raise _NotPresent
    try:
        walk = _walk_confined(
            candidate.path, candidate.confine_to, "token", verb="read"
        )
    except FileNotFoundError:
        raise _NotPresent from None
    except (ConfinedWriteError, OSError) as exc:
        raise CredentialUnavailableError(cs.TOKEN_INVALID) from exc
    try:
        if walk.entry is None:
            raise _NotPresent
        flags = (
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
            | getattr(os, "O_CLOEXEC", 0)
        )
        try:
            if isinstance(walk.at, int):
                return os.open(walk.name, flags, dir_fd=walk.at)
            return os.open(os.path.join(walk.at, walk.name), flags)
        except FileNotFoundError:
            raise _NotPresent from None
        except OSError as exc:
            raise CredentialUnavailableError(cs.TOKEN_INVALID) from exc
    finally:
        walk.close()


def _trusted_owner_uids() -> frozenset[int]:
    override = _test_overrides.get("trusted_owner_uids")
    if override is not None:
        return frozenset(override)
    return frozenset({0, os.geteuid()})


def _read_candidate(candidate: _TokenCandidate) -> TokenRead:
    fd = _open_token(candidate)
    try:
        status = os.fstat(fd)
        if not stat.S_ISREG(status.st_mode):
            raise CredentialUnavailableError(cs.TOKEN_INVALID)
        if status.st_uid != os.geteuid() or status.st_mode & 0o077:
            raise CredentialUnavailableError(cs.TOKEN_INVALID)
        identity = (
            status.st_dev,
            status.st_ino,
            status.st_size,
            status.st_mtime_ns,
        )
        key = os.fspath(candidate.path)
        cached = _TOKEN_CACHE.get(key)
        if cached is not None and cached[0] == identity:
            return cached[1]
        if status.st_size > _TOKEN_MAX_BYTES:
            raise CredentialUnavailableError(cs.TOKEN_INVALID)
        chunks: list[bytes] = []
        remaining = _TOKEN_MAX_BYTES + 1
        while remaining > 0:
            try:
                chunk = os.read(fd, remaining)
            except OSError as exc:
                raise CredentialUnavailableError(cs.TOKEN_INVALID) from exc
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
    finally:
        os.close(fd)
    if len(raw) > _TOKEN_MAX_BYTES:
        raise CredentialUnavailableError(cs.TOKEN_INVALID)
    if raw.endswith(b"\r\n"):
        raw = raw[:-2]
    elif raw.endswith(b"\n"):
        raw = raw[:-1]
    try:
        token = raw.decode("ascii")
    except UnicodeDecodeError:
        raise CredentialUnavailableError(cs.TOKEN_INVALID) from None
    if not _TOKEN_PATTERN.fullmatch(token):
        raise CredentialUnavailableError(cs.TOKEN_INVALID)
    # Registered for exact-value redaction the moment it is read (plan 3.7).
    register_secret_value(token)
    read = TokenRead(
        token=token,
        source=candidate.source,
        digest=hashlib.sha256(token.encode("ascii")).hexdigest()[:16],
    )
    _TOKEN_CACHE[key] = (identity, read)
    return read


def read_service_account_token() -> TokenRead:
    """The service-account token, read on demand from the first present path.

    Raises :class:`CredentialUnavailableError`: ``platform_unsupported``,
    ``op_not_configured`` (no path present) or ``token_invalid`` (the first
    present path fails a check; the next path is NOT tried).
    """
    if not _platform_supported():
        raise CredentialUnavailableError(cs.PLATFORM_UNSUPPORTED)
    for candidate in token_candidates():
        try:
            return _read_candidate(candidate)
        except _NotPresent:
            continue
    raise CredentialUnavailableError(cs.OP_NOT_CONFIGURED)


TokenState = Literal[
    "usable", "op_not_configured", "token_invalid", "platform_unsupported"
]


def token_status() -> tuple[TokenState, TokenSource | None]:
    """``(state, source)`` of the token now: names only, never the value."""
    try:
        read = read_service_account_token()
    except CredentialUnavailableError as exc:
        reason = exc.reason
        if reason not in ("op_not_configured", "token_invalid", "platform_unsupported"):
            reason = "token_invalid"
        return reason, None  # type: ignore[return-value]
    return "usable", read.source


def token_path_report() -> list[dict[str, Any]]:
    """For ``pmcp doctor``: each searched path, whether it is present, and
    whether the systemd copy and the credstore file differ (digests compared,
    never printed)."""
    report: list[dict[str, Any]] = []
    digests: dict[str, str] = {}
    for candidate in token_candidates():
        entry: dict[str, Any] = {
            "source": candidate.source,
            "path": os.fspath(candidate.path),
        }
        try:
            read = _read_candidate(candidate)
        except _NotPresent:
            entry["state"] = "absent"
        except CredentialUnavailableError:
            entry["state"] = "invalid"
        else:
            entry["state"] = "usable"
            digests[candidate.source] = read.digest
        report.append(entry)
    if "systemd_credential" in digests and "credstore" in digests:
        report.append(
            {
                "source": "comparison",
                "systemd_and_credstore_differ": digests["systemd_credential"]
                != digests["credstore"],
            }
        )
    return report


# --------------------------------------------------------------------------- #
# The op executable (plan 3.4)
# --------------------------------------------------------------------------- #

#: Searched in order when no ``op_path`` is configured: root-owned paths first.
DEFAULT_OP_PATHS = (
    "/usr/bin/op",
    "/usr/local/bin/op",
    "/opt/homebrew/bin/op",
    "~/.local/bin/op",
)

#: ``op_path`` from an operator config (user-scope ``.mcp.json`` or ``-c``),
#: set by the config loader. Never from the environment or a repository file.
_OPERATOR_OP_PATH: str | None = None


def set_operator_op_path(path: str | None) -> None:
    """Record the operator's ``op_path`` (from a user-scope or ``-c`` config)."""
    global _OPERATOR_OP_PATH
    _OPERATOR_OP_PATH = path or None


def _expand_home(spelled: str) -> str | None:
    if spelled.startswith("~/"):
        from pmcp.home_identity import optional_operator_home

        home = optional_operator_home()
        if home is None:
            return None
        return os.path.join(os.fspath(home), spelled[2:])
    return spelled


def _trusted_node(status: os.stat_result) -> bool:
    if status.st_uid not in _trusted_owner_uids():
        return False
    return not status.st_mode & 0o022


def _check_trusted_executable(real: str) -> bool:
    """The final file and EVERY ancestor directory: owned by root or the
    gateway uid, not writable by group or other (a sticky world-writable
    directory is refused too); the file a regular executable."""
    try:
        status = os.stat(real, follow_symlinks=False)
    except OSError:
        return False
    if not stat.S_ISREG(status.st_mode) or not status.st_mode & stat.S_IXUSR:
        return False
    if not _trusted_node(status):
        return False
    # Test-only boundary (never set in production): the walk checks up to
    # and including this directory. A CI runner's scratch sits under the
    # sticky world-writable /var/tmp, which the real walk refuses.
    boundary = _test_overrides.get("trust_root")
    directory = os.path.dirname(real)
    while True:
        try:
            dstatus = os.stat(directory, follow_symlinks=False)
        except OSError:
            return False
        if not stat.S_ISDIR(dstatus.st_mode) or not _trusted_node(dstatus):
            return False
        parent = os.path.dirname(directory)
        if parent == directory or (boundary is not None and directory == boundary):
            return True
        directory = parent


def trusted_op_executable() -> str:
    """The resolved, validated path of ``op`` to execute (never a ``PATH`` search).

    An explicit ``op_path`` that is absent is ``op_missing``; one that fails
    the trust check is ``op_untrusted`` -- never a fallback to the search list.
    The search list: the first path that exists and passes; none present
    ``op_missing``; present but none trusted ``op_untrusted``.
    """
    explicit = _OPERATOR_OP_PATH
    if explicit is not None:
        candidates = [explicit]
    else:
        candidates = list(_test_overrides.get("op_search_paths", DEFAULT_OP_PATHS))
    seen_untrusted = False
    for spelled in candidates:
        expanded = _expand_home(spelled)
        if expanded is None or not os.path.isabs(expanded):
            if explicit is not None:
                raise CredentialUnavailableError(cs.OP_UNTRUSTED)
            continue
        if not os.path.lexists(expanded):
            if explicit is not None:
                raise CredentialUnavailableError(cs.OP_MISSING)
            continue
        real = os.path.realpath(expanded)
        if _check_trusted_executable(real):
            return real
        seen_untrusted = True
        if explicit is not None:
            raise CredentialUnavailableError(cs.OP_UNTRUSTED)
    raise CredentialUnavailableError(
        cs.OP_UNTRUSTED if seen_untrusted else cs.OP_MISSING
    )


# --------------------------------------------------------------------------- #
# op's environment and private directory (plan 3.4)
# --------------------------------------------------------------------------- #

#: The fixed system PATH ``op`` runs with.
OP_FIXED_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"

#: Passed through only when the operator exported them (never a file's value).
_OP_PASSTHROUGH = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "no_proxy",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
)


def op_subprocess_env(token: str, home: str) -> dict[str, str]:
    """The ONLY environment ``op`` runs with: an env builder of its own.

    A fixed system ``PATH``, ``HOME``, the token, ``OP_CACHE=false``, and the
    proxy and CA variables only when operator-supplied. Nothing else of the
    gateway's environment -- no other credential -- reaches ``op``.
    """
    from pmcp.env_store import env_key_is_operator_supplied

    env = {
        "PATH": OP_FIXED_PATH,
        "HOME": home,
        "OP_SERVICE_ACCOUNT_TOKEN": token,
        "OP_CACHE": "false",
    }
    for name in _OP_PASSTHROUGH:
        if env_key_is_operator_supplied(name):
            value = os.environ.get(name)
            if value:
                env[name] = value
    return env


def op_state_dir() -> Path:
    """``op``'s cwd and ``--config`` directory: private (0700, ours), created on demand."""
    override = _test_overrides.get("state_dir")
    if override is not None:
        directory = Path(override)
    else:
        from pmcp.env_store import env_key_is_operator_supplied
        from pmcp.home_identity import operator_home

        base: Path | None = None
        if env_key_is_operator_supplied("XDG_STATE_HOME"):
            value = os.environ.get("XDG_STATE_HOME", "")
            if value and os.path.isabs(value):
                base = Path(value)
        if base is None:
            base = operator_home() / ".local" / "state"
        directory = base / "pmcp" / "op"
    try:
        from pmcp.atomic_write import make_store_dirs

        make_store_dirs(directory, mode=0o700)
        status = os.stat(directory, follow_symlinks=False)
    except OSError as exc:
        raise CredentialUnavailableError(cs.OP_UNTRUSTED) from exc
    if (
        not stat.S_ISDIR(status.st_mode)
        or status.st_uid != os.geteuid()
        or status.st_mode & 0o077
    ):
        raise CredentialUnavailableError(cs.OP_UNTRUSTED)
    return directory


# --------------------------------------------------------------------------- #
# Running op (plan 3.4)
# --------------------------------------------------------------------------- #

OP_TIMEOUT_SECONDS = 10.0
SPAWN_RESOLUTION_SECONDS = 20.0
STDOUT_CAP = 64 * 1024
STDERR_CAP = 8 * 1024
MAX_CONCURRENT_OP = 2
MIN_VALUE_LENGTH = 8


def op_argv(executable: str, reference: str, config_dir: str) -> list[str]:
    """``op read --no-newline --no-color --cache=false --config <dir> -- <ref>``."""
    return [
        executable,
        "read",
        "--no-newline",
        "--no-color",
        "--cache=false",
        "--config",
        config_dir,
        "--",
        reference,
    ]


_STDERR_CLASSES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"rate.?limit|too many requests|\b429\b", re.I), cs.RATE_LIMITED),
    (
        re.compile(
            r"unauthori[sz]ed|authenticat|invalid (?:service account )?token|"
            r"\b401\b|\b403\b|forbidden|not signed in|session expired",
            re.I,
        ),
        cs.AUTH_FAILED,
    ),
    (
        re.compile(
            r"isn't an item|not found|no item|could not find|"
            r"isn't a vault|isn't a field|does not exist|\b404\b",
            re.I,
        ),
        cs.NOT_FOUND,
    ),
    (
        re.compile(
            r"network|connection|dial tcp|timeout|timed out|no such host|"
            r"tls|certificate|proxy|unreachable|i/o",
            re.I,
        ),
        cs.NETWORK,
    ),
)


def classify_op_failure(stderr: bytes) -> str:
    """A failure class from ``op``'s stderr; the stderr itself is discarded."""
    text = stderr.decode("utf-8", "replace")
    for pattern, reason in _STDERR_CLASSES:
        if pattern.search(text):
            return reason
    return cs.AUTH_FAILED if not text.strip() else cs.NOT_FOUND


async def _read_bounded(stream: asyncio.StreamReader | None, cap: int) -> bytes:
    """Read until EOF, failing (``OverflowError``) past ``cap`` bytes."""
    if stream is None:
        return b""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await stream.read(8192)
        if not chunk:
            return b"".join(chunks)
        total += len(chunk)
        if total > cap:
            raise OverflowError
        chunks.append(chunk)


async def _kill_and_reap(process: Any) -> None:
    from pmcp.child_output import kill_process_tree_now, release

    group = process.pid if isinstance(getattr(process, "pid", None), int) else None
    kill_process_tree_now(process, group_pgid=group)
    try:
        await asyncio.wait_for(asyncio.shield(process.wait()), timeout=5.0)
    except asyncio.TimeoutError:
        pass
    finally:
        release(process)


async def run_op_read(reference: str, timeout: float) -> str:
    """Resolve ONE supported reference with a fresh ``op`` process.

    Raises :class:`CredentialUnavailableError` with a typed reason. Every byte
    read is discarded on any failure; stderr and argv are never echoed.
    """
    from pmcp.child_output import detached, spawn_owned

    token = read_service_account_token()
    executable = trusted_op_executable()
    state = op_state_dir()
    argv = op_argv(executable, reference, os.fspath(state))
    if timeout <= 0:
        raise CredentialUnavailableError(cs.TIMEOUT)
    try:
        process = await spawn_owned(
            lambda: asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=os.fspath(state),
                env=op_subprocess_env(token.token, os.fspath(state)),
                **detached(),
            )
        )
    except FileNotFoundError:
        raise CredentialUnavailableError(cs.OP_MISSING) from None
    except PermissionError:
        raise CredentialUnavailableError(cs.OP_UNTRUSTED) from None
    except OSError:
        raise CredentialUnavailableError(cs.OP_MISSING) from None
    stdout_task = asyncio.ensure_future(_read_bounded(process.stdout, STDOUT_CAP))
    stderr_task = asyncio.ensure_future(_read_bounded(process.stderr, STDERR_CAP))
    finished = False
    try:
        try:
            stdout, stderr = await asyncio.wait_for(
                asyncio.gather(stdout_task, stderr_task), timeout=timeout
            )
            returncode = await asyncio.wait_for(
                process.wait(), timeout=max(0.1, timeout)
            )
        except asyncio.TimeoutError:
            raise CredentialUnavailableError(cs.TIMEOUT) from None
        except OverflowError:
            raise CredentialUnavailableError(cs.OUTPUT_INVALID) from None
        finished = True
    finally:
        if not finished:
            for task in (stdout_task, stderr_task):
                task.cancel()
            await _kill_and_reap(process)
        else:
            from pmcp.child_output import release

            release(process)
    if returncode != 0:
        reason = classify_op_failure(stderr)
        del stdout, stderr
        raise CredentialUnavailableError(reason)
    del stderr
    try:
        value = stdout.decode("utf-8")
    except UnicodeDecodeError:
        raise CredentialUnavailableError(cs.OUTPUT_INVALID) from None
    return _validated_value(value)


def _validated_value(value: str) -> str:
    """Refuse an empty, short, multi-line, NUL-bearing or reference-shaped value."""
    if (
        len(value) < MIN_VALUE_LENGTH
        or "\x00" in value
        or "\n" in value
        or "\r" in value
        or is_reference_shaped(value)
    ):
        raise CredentialUnavailableError(cs.OUTPUT_INVALID)
    return value


class _Resolver:
    """Per event loop: a 2-slot semaphore and single-flight by reference."""

    def __init__(self) -> None:
        self.semaphore = asyncio.Semaphore(MAX_CONCURRENT_OP)
        self.inflight: dict[str, asyncio.Task[str]] = {}

    async def _lookup(self, reference: str, deadline: float) -> str:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise CredentialUnavailableError(cs.TIMEOUT)
        try:
            await asyncio.wait_for(self.semaphore.acquire(), timeout=remaining)
        except asyncio.TimeoutError:
            raise CredentialUnavailableError(cs.TIMEOUT) from None
        try:
            remaining = min(OP_TIMEOUT_SECONDS, deadline - time.monotonic())
            return await run_op_read(reference, remaining)
        finally:
            self.semaphore.release()

    async def read(self, reference: str, deadline: float) -> str:
        task = self.inflight.get(reference)
        if task is None or task.done():
            task = asyncio.ensure_future(self._lookup(reference, deadline))
            self.inflight[reference] = task

            def _forget(done: asyncio.Task[str], ref: str = reference) -> None:
                if self.inflight.get(ref) is done:
                    self.inflight.pop(ref, None)
                if not done.cancelled():
                    done.exception()  # retrieved: never "never retrieved"

            task.add_done_callback(_forget)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise CredentialUnavailableError(cs.TIMEOUT)
        # A cancelled or timed-out waiter never cancels the shared lookup.
        try:
            return await asyncio.wait_for(asyncio.shield(task), timeout=remaining)
        except asyncio.TimeoutError:
            raise CredentialUnavailableError(cs.TIMEOUT) from None


_RESOLVERS: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, _Resolver] = (
    weakref.WeakKeyDictionary()
)


def _resolver() -> _Resolver:
    loop = asyncio.get_running_loop()
    resolver = _RESOLVERS.get(loop)
    if resolver is None:
        resolver = _Resolver()
        _RESOLVERS[loop] = resolver
    return resolver


async def resolve_reference(reference: str, deadline: float) -> str:
    """One supported reference's value, registered for redaction before return."""
    value = await _resolver().read(reference, deadline)
    register_secret_value(value)
    return value


# --------------------------------------------------------------------------- #
# Provenance and the per-spawn plan (plan 3.2, 3.5)
# --------------------------------------------------------------------------- #

#: Source kinds a reference may be resolved from (plan 3.2.2).
TRUSTED_SOURCES = frozenset(
    {
        "user_store",
        "user_config",
        "custom_config",
        "shipped_manifest",
        "operator_overlay",
        "env_overlay",
    }
)

#: Every source kind a ``credential_sources`` entry may name. A kind not in
#: :data:`TRUSTED_SOURCES` is refused (``reference_not_allowed_here``).
SOURCE_KINDS = TRUSTED_SOURCES | frozenset(
    {
        "environment",
        "home_dotenv",
        "auth_connect_project",
        "tenant",
        "project_files",
        "project_config",
        "project_overlay",
        "env_overlay_untrusted",
        "discovered",
        "unknown",
    }
)


def source_is_trusted(kind: str | None) -> bool:
    return kind in TRUSTED_SOURCES


@dataclass(frozen=True)
class SpawnPlan:
    """Which env keys of one spawn pmcp resolves: ``key -> supported reference``."""

    to_resolve: dict[str, str]

    @property
    def empty(self) -> bool:
        return not self.to_resolve


_EMPTY_PLAN = SpawnPlan({})


def spawn_plan(config: ResolvedServerConfig) -> SpawnPlan:
    """The resolution plan of ``config``'s local env, decided without ``op``.

    Only keys with a ``credential_sources`` entry are pmcp's to resolve (a
    credential lookup's key, or every own-env key of an opted-in server); any
    other value passes through verbatim, whatever its shape (plan 3.2.4).

    Raises :class:`CredentialUnavailableError` for a static failure:
    ``reference_not_allowed_here`` (an untrusted source),
    ``reference_unsupported`` (grammar), ``op_name_not_allowed`` (an opted-in
    server naming an ``OP_*`` variable).
    """
    from pmcp.types import LocalMcpServerConfig

    local = config.config
    if not isinstance(local, LocalMcpServerConfig):
        return _EMPTY_PLAN
    env = local.env or {}
    sources = config.credential_sources or {}
    if config.resolve_secret_refs:
        for key in env:
            if key.upper().startswith("OP_"):
                raise CredentialUnavailableError(cs.OP_NAME_NOT_ALLOWED)
    to_resolve: dict[str, str] = {}
    for key, value in env.items():
        if key not in sources:
            continue
        if not is_reference_shaped(value):
            continue
        if not source_is_trusted(sources[key]):
            raise CredentialUnavailableError(cs.REFERENCE_NOT_ALLOWED_HERE)
        reference = parse_reference(value)
        if reference is None:
            raise CredentialUnavailableError(cs.REFERENCE_UNSUPPORTED)
        to_resolve[key] = reference
    return SpawnPlan(to_resolve)


def static_failure(config: ResolvedServerConfig) -> str | None:
    """The reason ``config`` cannot be spawned, known WITHOUT calling ``op``.

    Grammar, source, ``OP_*`` names (:func:`spawn_plan`), then -- only when
    something is to be resolved -- the platform and the token file. ``None``
    when nothing static stands in the way.
    """
    try:
        plan = spawn_plan(config)
        if not plan.empty:
            read_service_account_token()
    except CredentialUnavailableError as exc:
        return exc.reason
    return None


@dataclass(frozen=True)
class SpawnSecrets:
    """The resolved overlay of one spawn. Never stored on a config."""

    overlay: dict[str, str] = field(repr=False)
    generation: int = 0


EMPTY_SECRETS = SpawnSecrets({}, 0)


async def resolve_spawn_env(
    config: ResolvedServerConfig, *, timeout: float = SPAWN_RESOLUTION_SECONDS
) -> dict[str, str]:
    """Resolve ``config``'s plan into a fresh ``key -> value`` overlay.

    Total time is bounded (``timeout``, 20 s by default, semaphore waits
    included). Typed failures propagate; nothing partial is returned.
    """
    plan = spawn_plan(config)
    if plan.empty:
        return {}
    read_service_account_token()  # static: fail before any op process
    deadline = time.monotonic() + timeout
    keys = list(plan.to_resolve)

    async def one(key: str) -> tuple[str, str | None]:
        # ``(value, None)`` or ``("", reason)``: a failure is its reason only.
        try:
            return await resolve_reference(plan.to_resolve[key], deadline), None
        except CredentialUnavailableError as exc:
            return "", exc.reason

    results = await asyncio.gather(*(one(key) for key in keys))
    overlay: dict[str, str] = {}
    for key, (value, reason) in zip(keys, results):
        if reason is not None:
            overlay.clear()
            raise CredentialUnavailableError(reason)
        overlay[key] = value
    return overlay


def apply_overlay(
    env: Mapping[str, str] | None, overlay: Mapping[str, str]
) -> dict[str, str]:
    """A fresh dict: ``env`` with each resolved key replaced by its value."""
    merged = dict(env or {})
    merged.update(overlay)
    return merged


def require_resolved(
    config: ResolvedServerConfig, secrets: SpawnSecrets | None
) -> dict[str, str]:
    """The overlay to spawn ``config`` with, failing CLOSED when one is due but absent.

    A spawn path that forgot to resolve first must never start the child with
    the literal reference: it gets ``resolution_required``.
    """
    plan = spawn_plan(config)
    if plan.empty:
        return {}
    if secrets is None or set(secrets.overlay) != set(plan.to_resolve):
        raise CredentialUnavailableError(cs.RESOLUTION_REQUIRED)
    return dict(secrets.overlay)


def is_retryable_reason(reason: str) -> bool:
    """Only a config change mid-resolution is retryable by the caller."""
    return reason == cs.CONFIG_CHANGED


# --------------------------------------------------------------------------- #
# The ``secret_refs`` health capability (plan 3.10)
# --------------------------------------------------------------------------- #

SECRET_REFS_CAPABILITY = "secret_refs"


def shadowing_credential_names() -> list[str]:
    """Credential-shaped names (never values) a reference write would not reach.

    Names ``copy_allowed`` accepts whose answer comes from the process
    environment (``external``) or ``~/.env`` (``home_dotenv``, including a
    skipped ``~/.env`` reference): a reference written to the user store under
    such a name would never be the answer, or would arrive through an
    untrusted source.
    """
    from pmcp.env_store import (
        copy_allowed,
        declared_credential_names,
        env_origins,
        skipped_references,
    )

    try:
        declared = declared_credential_names()
    except Exception:  # noqa: BLE001 - names only; the manifest may be unreadable
        declared = frozenset()
    names = {
        name
        for name, origin in env_origins().items()
        if origin in ("external", "home_dotenv")
        and name in os.environ
        and copy_allowed(name, declared)
    }
    names.update(
        name for name, origin in skipped_references().items() if origin == "home_dotenv"
    )
    return sorted(names)


def secret_refs_health(*, include_names: bool) -> dict[str, Any]:
    """The capability report, evaluated now: enums and names, never values.

    ``capable`` when this process resolves references (POSIX), has a usable
    token, and no credential name that was in the user store at startup
    arrived as external environment (an ``EnvironmentFile=``/``source``
    launcher still loads the store into the environment: a reference written
    there would arrive through the untrusted environment). ``include_names``
    adds per-name origins (names only); the HTTP gateway passes ``False``
    while HTTP auth is on.
    """
    from pmcp.env_store import (
        copy_allowed,
        declared_credential_names,
        env_origin,
        startup_user_store_names,
    )

    state, source = token_status()
    try:
        declared = declared_credential_names()
    except Exception:  # noqa: BLE001 - names only
        declared = frozenset()
    external_store_names = sorted(
        name
        for name in startup_user_store_names()
        if env_origin(name) == "external" and copy_allowed(name, declared)
    )
    capable = _platform_supported() and state == "usable" and not external_store_names
    report: dict[str, Any] = {
        "capable": capable,
        "token_state": state,
        "token_source": source,
        "store_names_from_environment": len(external_store_names),
    }
    if include_names:
        report["store_names_from_environment_names"] = external_store_names
        report["shadowed_names"] = shadowing_credential_names()
    return report


def apply_health_capability(diagnostics: Any, *, include_names: bool) -> Any:
    """A copy of ``diagnostics`` with ``secret_refs`` evaluated now (plan 3.10)."""
    report = secret_refs_health(include_names=include_names)
    updated = diagnostics.model_copy(deep=True)
    capabilities = [
        cap for cap in updated.capabilities if cap != SECRET_REFS_CAPABILITY
    ]
    if report["capable"]:
        capabilities.append(SECRET_REFS_CAPABILITY)
    updated.capabilities = capabilities
    updated.secret_refs = report
    return updated


__all__ = [
    "OP_CONTROL_NAMES",
    "REFERENCE_PATTERN",
    "SpawnPlan",
    "SpawnSecrets",
    "TokenRead",
    "apply_overlay",
    "capture_startup_environment",
    "is_op_control_name",
    "is_reference_shaped",
    "op_argv",
    "op_subprocess_env",
    "parse_reference",
    "read_service_account_token",
    "require_resolved",
    "resolve_spawn_env",
    "spawn_plan",
    "static_failure",
    "token_status",
    "trusted_op_executable",
]
