"""Shared PMCP credential env-file storage helpers."""

from __future__ import annotations

import errno
import io
import os
import re
import stat
from collections.abc import Iterable, Mapping
from pathlib import Path

from dotenv import dotenv_values

from pmcp.atomic_write import atomic_write, read_confined
from pmcp.config.loader import find_project_root

ENV_VAR_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def validate_env_var_name(name: str) -> str:
    """Validate and return a shell-compatible env var name."""
    if not ENV_VAR_NAME_PATTERN.fullmatch(name):
        raise ValueError(f"Env var name must match ^[A-Za-z_][A-Za-z0-9_]*$: {name!r}")
    return name


def resolve_project_root(project: Path | None = None) -> Path:
    """Resolve project root for project-scope secrets."""
    if project:
        return project.resolve()

    discovered = find_project_root(Path.cwd())
    if discovered:
        return discovered

    return Path.cwd().resolve()


def resolve_scope_path(scope: str, project: Path | None = None) -> Path:
    """Resolve env file path for a credential scope."""
    if scope == "user":
        return Path.home() / ".config" / "pmcp" / "pmcp.env"
    if scope == "project":
        return resolve_project_root(project) / ".env.pmcp"
    raise ValueError(f"Unsupported secret scope: {scope}")


def read_env_file(path: Path) -> dict[str, str]:
    """Read .env key/value pairs from path.

    An absent store, and one that is not a regular file (a fifo, a socket, a
    device, a directory), read as empty -- the read never blocks
    (:func:`read_env_text`). It still follows a symlink: Consiliency/pmcp#367
    (stays open).
    """
    text = read_env_text(path)
    if text is None:
        return {}
    return _env_values(dotenv_values(stream=io.StringIO(text), interpolate=False))


def read_env_text(path: Path) -> str | None:
    """The UTF-8 text of a dotenv file, or ``None`` if absent or not a regular file.

    Never blocks: a repository can ship ``.env.pmcp`` as a fifo, and every
    reader that opened it plainly -- the spawn-time ``managed_secret_keys``,
    ``pmcp secrets check``, the gateway's credential-availability check -- froze
    there. The entry is ``stat``-ed and must be a regular file, then opened
    ``O_NONBLOCK`` and re-checked with ``fstat`` against a swap in between.
    It follows a symlink (Consiliency/pmcp#367, stays open); the confined
    readers in :mod:`pmcp.atomic_write` are the ones that do not.
    """
    if not path.exists():
        return None
    if not stat.S_ISREG(os.stat(path).st_mode):
        return None
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(fd, "rb") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            return None
        return handle.read().decode("utf-8")


def read_store_for_update(
    scope: str, path: Path, verb: str = "write"
) -> dict[str, str]:
    """Read a credential store that the caller is about to rewrite.

    The user store is the operator's and is read as :func:`read_env_file` reads
    it. The project store is repository-controlled, so it is read through the
    same confined walk its write takes (:func:`pmcp.atomic_write.read_confined`):
    a link that leaves the project is refused BEFORE anything is opened, with
    the write's value-free refusal, and a target that is not a regular file (a
    fifo, a device, ``/proc/self/fd/0``) is refused instead of hanging the read.

    This covers the commands that rewrite the store -- ``pmcp secrets set``,
    ``pmcp secrets sync`` and ``gateway.auth_connect`` -- so they never read
    through a link they would refuse to write; the startup load
    (``cli._load_project_store_at_startup``) reads the same way. Other readers --
    a running gateway's spawn-time credential checks and env stripping, ``pmcp
    secrets check`` -- still follow a link on read (Consiliency/pmcp#367, stays
    open), though none of them blocks on a fifo any more (:func:`read_env_text`).
    """
    confine_to = scope_confinement(scope, path)
    if confine_to is None:
        return read_env_file(path)
    data = read_confined(path, confine_to, verb=verb)
    if data is None:
        return {}
    return _env_values(
        dotenv_values(stream=io.StringIO(data.decode("utf-8")), interpolate=False)
    )


def _env_values(parsed: Mapping[str, str | None]) -> dict[str, str]:
    values: dict[str, str] = {}
    for key, value in parsed.items():
        if value is None:
            values[key] = ""
        else:
            values[key] = value
    return values


def _read_env_file_strict(path: Path) -> dict[str, str]:
    """:func:`read_env_file`, but "I could not read it" is never "it is not there".

    Both answers are ``{}`` from :func:`read_env_file`, and for its own callers that is
    right: a failed strip is a smaller harm than a crashed spawn. For a gate deciding
    whether a credential is the operator's, the two answers are opposite -- "no store"
    means nothing was planted, and "a store I could not read" means pmcp does not know.

    Measured on this tree (python-dotenv 1.2.3), only ONE shape of unreadable store was
    actually silent. A mode-000 *file* already raises ``PermissionError`` out of
    ``dotenv_values``, so the strict lookup already failed closed for it; undecodable
    bytes already raise ``UnicodeDecodeError``. But a **directory** at a store path --
    and any other non-regular file -- satisfies ``Path.exists()`` and yields ``{}``
    with no error at all, which the gate read as "nothing is planted" and allowed a
    post. That is the hole this closes.

    So this adds exactly one refusal and delegates everything else unchanged: a path
    that **exists but is not a regular file** raises, and every other path goes to
    :func:`read_env_file` exactly as before. Both halves of the condition earn their
    place. ``exists()`` follows symlinks, so a store whose symlink target is gone is
    genuinely **not there** and must stay an allow -- ``is_file()`` alone is false for
    that and for a directory alike, and refusing both would refuse every operator who
    has never run ``auth_connect``. And the delegation must stay a real call rather
    than an early ``return {}`` for an absent path: :func:`read_env_file` is the seam
    the existing lookup tests inject a failing read at, and short-circuiting past it
    would quietly make those tests unable to see the strict lookup at all.

    Checking the shape *before* opening also keeps a FIFO at a store path from
    blocking the gate forever instead of answering.

    ``exists()`` alone is not enough to ask the question, which is why this stats the
    path itself: ``Path.exists()`` answers ``False`` for **every** failed lookup, not
    only for a missing file. A self-referential symlink raises ``ELOOP`` underneath and
    still reports ``False``, so the refusal above was skipped and ``read_env_file``
    answered ``{}`` -- "nothing is planted" -- for a store pmcp could not resolve at
    all. Only ``FileNotFoundError`` means *not there*; every other lookup error means
    *unknown*, and unknown must reach the caller's fail-closed branch.
    """
    try:
        status = path.stat()
    except FileNotFoundError:
        status = None
    if status is not None and not stat.S_ISREG(status.st_mode):
        raise OSError(
            errno.EINVAL, "Credential store path is not a regular file", str(path)
        )
    return read_env_file(path)


def _validate_env_values(values: dict[str, str]) -> None:
    for key, value in values.items():
        validate_env_var_name(key)
        if "\n" in value or "\r" in value:
            raise ValueError("Credential values must not contain newlines")


def _format_env_value(value: str) -> str:
    if value == "":
        return '""'

    needs_quotes = any(ch.isspace() for ch in value) or any(
        ch in value for ch in ["#", "=", '"', "'", "\\"]
    )
    if not needs_quotes:
        return value

    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def scope_confinement(scope: str, store_path: Path) -> Path | None:
    """The root a credential write to ``store_path`` must stay inside, or ``None``.

    The project store ``<project>/.env.pmcp`` lives in a checkout, and a cloned
    repository can ship it as a symlink to anywhere the user can write; its
    writes are confined to the project root. The root is DERIVED from the store
    path -- ``store_path.parent``, which is the root by construction of
    :func:`resolve_scope_path` -- rather than resolved a second time, so the
    path written and the root it is judged against can never disagree (a
    second project-root discovery could land on an ancestor if a marker changed
    in between). The user store ``~/.config/pmcp/pmcp.env`` is the operator's
    own, so its links -- a dotfiles repository, typically -- are followed
    wherever they point.
    """
    if scope == "project":
        return store_path.parent
    if scope == "user":
        return None
    raise ValueError(f"Unsupported secret scope: {scope}")


def store_refusal(
    store_path: Path, exc: OSError | ValueError, verb: str = "write"
) -> str:
    """The operator-facing, value-free report of a failed credential-store access.

    The one conversion the entry points (``pmcp secrets set``/``sync``,
    ``gateway.auth_connect``, the startup load) apply at their boundary, so a
    store that cannot be read, parsed or written becomes an ``ok: false`` (or a
    startup warning) instead of an uncaught traceback:

    * an ``OSError`` -- a confinement refusal, ``EISDIR``, ``ENOTDIR``,
      ``ELOOP``, ``EACCES``, ``ENOENT``, ``ENOSPC``;
    * a ``UnicodeDecodeError`` -- a store that is not UTF-8 (reported without
      the offending bytes);
    * any other ``ValueError`` -- a value with a newline, an invalid key name.

    It names only the store's file name: never a path or a link target, which
    a repository may have chosen, and never a stored value. ``verb`` says what
    was being done to THAT file ("write", "read" for a sync source, "load").
    """
    from pmcp.atomic_write import ConfinedWriteError, refusing

    if isinstance(exc, ConfinedWriteError):
        return str(exc)
    if isinstance(exc, UnicodeDecodeError):
        return f"{refusing(verb)} {store_path.name}: it is not valid UTF-8"
    if isinstance(exc, ValueError):
        return f"{refusing(verb)} {store_path.name}: {exc}"
    reason = os.strerror(exc.errno) if exc.errno else "the write failed"
    return f"{refusing(verb)} {store_path.name}: {reason}"


def write_env_file(
    path: Path, values: dict[str, str], *, confine_to: Path | None
) -> None:
    """Write key/value pairs to a .env file atomically, at mode 0600.

    The write is atomic: the content is written to a temporary file in the same
    directory, flushed and ``fsync``-ed, then ``os.replace``-d over the
    destination. A write that fails partway -- ``ENOSPC``, a quota, a kill
    signal -- leaves the existing file byte-intact rather than truncated, so an
    interrupted write can no longer lose the store's other entries
    (Consiliency/pmcp#248). ``os.replace`` is an atomic same-filesystem rename,
    which is why the temporary shares the destination's directory; the
    directory entry is ``fsync``-ed too so the rename itself survives a crash.

    A symlinked store -- ``~/.config/pmcp/pmcp.env`` kept in a dotfiles
    repository -- is written THROUGH: the replace lands on the link's target and
    the link is left in place, as 2.7.3's plain write did. A dangling link
    creates its target; a link loop is refused (:func:`pmcp.atomic_write.atomic_write`).

    ``confine_to`` is required and comes from :func:`scope_confinement`: the
    project root for the project store, whose link may only be followed while it
    stays inside the project (a link out is refused with
    ``ConfinedWriteError``, never written through and never replaced), and
    ``None`` for the user store.
    """
    _validate_env_values(values)

    lines = [f"{key}={_format_env_value(val)}" for key, val in values.items()]
    content = "\n".join(lines)
    if content:
        content += "\n"

    # Tighten only directories PMCP itself creates (e.g. ~/.config/pmcp for
    # user-scope secrets) to 0700. Never chmod a pre-existing directory such as
    # a project root, which for project-scope secrets is path.parent.
    parent = path.parent
    parent_created = not parent.exists()
    parent.mkdir(parents=True, exist_ok=True)
    if parent_created:
        try:
            os.chmod(parent, 0o700)
        except OSError:
            pass

    # Write-then-rename so the destination is never observed truncated, at 0600,
    # through a symlinked store rather than over it (see pmcp.atomic_write).
    atomic_write(
        path,
        content.encode("utf-8"),
        confine_to=confine_to,
        mode=0o600,
        prefix=".pmcp-env-",
    )


# Env-var keys PMCP itself introduced into its OWN environment from a dotenv
# file. Provenance, not file contents: see record_dotenv_keys (Consiliency/pmcp#229).
_DOTENV_SOURCED_KEYS: set[str] = set()


def record_dotenv_keys(keys: Iterable[str]) -> None:
    """Record env-var keys that PMCP itself introduced from a dotenv file.

    Callers record the delta they measured around their own ``load_dotenv``
    call -- ``set(os.environ)`` after, minus before -- so this registry holds
    *provenance*, never file contents, and never a re-parse:

    .. code-block:: python

        before = set(os.environ)
        load_dotenv(...)                       # semantics untouched
        record_dotenv_keys(set(os.environ) - before)

    Recording rather than re-deriving is what makes the strip correct.
    ``load_dotenv`` defaults to ``override=False``, so a variable the operator
    exported into their shell is left alone even when a dotenv file names it
    too; such a variable is already in ``before``, so it is never recorded and
    never stripped. Stripping by key name instead would delete the operator's
    ``PATH`` from every spawned server. Interpolation and empty-value shadowing
    need no emulation either -- whatever ``load_dotenv`` did is what is recorded.

    Additive and idempotent. An empty registry is the correct default: PMCP
    imported as a library, with ``main()`` never run, has loaded no dotenv file
    and so strips nothing extra.
    """
    _DOTENV_SOURCED_KEYS.update(keys)


def dotenv_sourced_keys() -> frozenset[str]:
    """Keys PMCP introduced into its own environment from dotenv files."""
    return frozenset(_DOTENV_SOURCED_KEYS)


def reset_dotenv_keys() -> None:
    """Clear the dotenv provenance registry. **Test-only seam.**

    Production never calls this: the registry only grows, as startup and
    availability-check loads happen. Tests need it because the registry is
    process-global -- without a reset, keys one test recorded would be stripped
    from every later test's subprocess environment. An autouse fixture in
    ``tests/conftest.py`` calls it around every test.
    """
    _DOTENV_SOURCED_KEYS.clear()


# Env-var keys PMCP itself introduced into its OWN environment, by any route:
# a runtime credential write or a load of one of PMCP's own credential stores.
# Provenance, not file contents: see record_pmcp_introduced_keys
# (Consiliency/pmcp#230).
_PMCP_INTRODUCED_KEYS: set[str] = set()


def record_pmcp_introduced_keys(keys: Iterable[str]) -> None:
    """Record env-var keys PMCP itself introduced into its OWN environment.

    Every route counts: a runtime write (``auth_connect`` setting
    ``os.environ[env_var]`` after storing the credential) and a load of one of
    PMCP's own credential-store files at startup. Callers record the delta they
    measured around their own write or load, exactly as
    :func:`record_dotenv_keys` does:

    .. code-block:: python

        before = set(os.environ)
        load_dotenv(store_path, override=False)
        record_pmcp_introduced_keys(set(os.environ) - before)

    The gate that consults this registry needs one distinction: did PMCP put
    this variable here, or did the operator's shell? A *store lookup* cannot
    answer that durably, because a store entry can vanish while the variable it
    planted stays in ``os.environ``.

    **The mechanisms, as measured rather than as first assumed.** An earlier
    revision of this docstring said :func:`set_env_value` could drop a key
    because it is a read-modify-write over a :func:`read_env_file` that returns
    ``{}`` for a file it cannot read, so *any* later ``auth_connect`` would
    erase an earlier entry. That is **not** reproducible: an unreadable file
    raises ``PermissionError`` out of the read and the file is left byte-intact,
    a directory at the path raises ``IsADirectoryError`` out of the write, and a
    store holding a key or value the writer rejects raises ``ValueError`` before
    the file is opened. Every one of those fails closed, and the partial-write route -- :func:`write_env_file` once truncated before it wrote -- is closed too: that write is now atomic (write-to-temp then ``os.replace``, Consiliency/pmcp#248). What remains real:

    * two read-modify-writes racing lose one update, reachable whenever a second
      process touches the store (a ``pmcp secrets set``/``sync`` beside a running
      gateway) -- not in-process, where :func:`set_env_value` runs synchronously
      with no await between the read and the write;
    * an operator simply deleting the store;
    * and a store written by a *previous* process, whose keys a write-only
      record never saw.

    A record of what happened decays through none of these, which is why the
    registry is named for what it means rather than for one mechanism.

    Additive and idempotent, and deliberately separate from
    :func:`record_dotenv_keys`: that registry has a merged consumer
    (:func:`sanitized_subprocess_env` strips its keys from every spawned child),
    so widening its membership would change behaviour elsewhere. An empty
    registry is the correct default: PMCP imported as a library has introduced
    nothing.
    """
    _PMCP_INTRODUCED_KEYS.update(keys)


def pmcp_introduced_keys() -> frozenset[str]:
    """Keys PMCP introduced into its own environment, by write or by load."""
    return frozenset(_PMCP_INTRODUCED_KEYS)


def reset_pmcp_introduced_keys() -> None:
    """Clear the PMCP-introduced provenance registry. **Test-only seam.**

    Production never calls this -- the registry only grows, as credential
    writes and store loads happen, and a clear reachable from a gateway tool
    would make the evidence erasable by the agent the record exists to catch.
    Tests need it because the registry is process-global: without a reset, a key
    one test recorded would still read as PMCP-introduced in every later test.
    """
    _PMCP_INTRODUCED_KEYS.clear()


def env_key_is_operator_supplied(key: str) -> bool:
    """True only when ``key`` is set in the environment AND PMCP did not put it there.

    A handful of environment variables -- ``PMCP_MANIFEST_PATH``, ``PMCP_CONFIG``
    and ``PMCP_POLICY`` -- redirect the gateway to a manifest, config or policy
    file, and the v13 trust phases treat all three as operator-supplied and so
    ungated. That premise holds only for a value the operator exported into their
    OWN shell. A checkout can set the same variable through a dotenv file the
    gateway loads on its behalf -- ``cli.load_startup_env`` reads ``.env`` and
    ``.env.pmcp`` before arg parsing, and ``GatewayTools._check_api_key_available``
    reads ``.env`` during a credential check -- and then the redirect was chosen by
    the repository, not the operator (review findings S-03 and S-11).

    Provenance already tells the two apart, so this asks nothing new of the tree:

    * :func:`dotenv_sourced_keys` holds keys a plain ``.env`` introduced (the
      availability-check load records every key it reads there);
    * :func:`pmcp_introduced_keys` holds keys PMCP's own store files -- including
      ``.env.pmcp`` -- and ``auth_connect`` introduced.

    Every dotenv load in the tree runs ``override=False``, so a variable the
    operator already exported is never overwritten and never recorded. Therefore a
    variable that is *set* but absent from BOTH registries is one the operator's
    environment supplied; a variable present in EITHER registry reached the process
    through a file PMCP loaded and must not be honoured as a trust-bearing redirect.

    A key that is not set at all returns ``False``: the caller then behaves exactly
    as if the variable were absent, which is the ungated-absence fallback the call
    sites already had. This never gates the operator's own use -- an exported value
    is honoured unchanged -- it only refuses a value a project file planted.
    """
    if key not in os.environ:
        return False
    return key not in _DOTENV_SOURCED_KEYS and key not in _PMCP_INTRODUCED_KEYS


def describe_ignored_trust_env_var(variable: str, path: str) -> str:
    """Operator-safe log line: a trust-bearing env var from a project file was ignored.

    Names the variable and the path it pointed at, and says the value was ignored
    because it came from a project file rather than the operator's environment. The
    path is agent- or checkout-controlled text, so it is rendered through
    :func:`pmcp.provision_gate.operator_safe` (escape every non-printable, then
    ``shlex.quote``) before it reaches a terminal. Imported inside the function so
    ``env_store`` -- a low-level module many others import -- keeps no import-time
    dependency on ``provision_gate``.
    """
    from pmcp.provision_gate import operator_safe

    return (
        f"Ignoring {variable}={operator_safe(path)}: it was set by a project file "
        f"(.env or .env.pmcp) rather than exported in the operator's environment, "
        f"so pmcp will not let a checkout redirect itself through it."
    )


def managed_secret_keys(project: Path | None = None) -> set[str]:
    """Env-var keys of credentials PMCP manages in its user/project secret stores.

    These are the keys ``auth_connect`` / ``pmcp secrets set`` write (and that the
    gateway loads into its own ``os.environ`` at startup). Used to avoid bleeding
    one server's PMCP-stored credentials into another server's subprocess env.
    Only PMCP-managed keys are enumerated — not secrets that reached ``os.environ``
    from the operator's shell or a plain ``.env``.
    """
    keys: set[str] = set(read_env_file(resolve_scope_path("user")))
    try:
        keys.update(read_env_file(resolve_scope_path("project", project)))
    except (OSError, ValueError):
        pass
    return keys


def managed_secret_keys_strict(project: Path | None = None) -> set[str]:
    """:func:`managed_secret_keys`, but a failed lookup raises instead of hiding.

    Same two files, same keys. Two differences, and the second was found by the
    assembled phase's review panel: the project lookup's
    ``except (OSError, ValueError): pass`` is gone, and BOTH halves read through
    :func:`_read_env_file_strict` rather than :func:`read_env_file`. That suppression
    makes "the lookup failed" indistinguishable from "the key is not planted", and the
    failure direction is *allow* -- fine for :func:`sanitized_subprocess_env`, where a
    failed strip is a smaller harm than a crashed spawn, and wrong for a gate deciding
    whether a credential is the operator's. Callers that must fail closed use this
    variant and let the exception reach their own error branch.

    Dropping the ``except`` was not enough on its own, and the *user* half being
    unguarded was never the safety it looked like: an unguarded call only fails closed
    for failures that RAISE. A directory at either store path raises nothing --
    :func:`read_env_file` returns ``{}`` for it silently -- so before
    :func:`_read_env_file_strict` both halves still answered "nothing is planted" for a
    store pmcp could not read. Both halves are strict now, so both reach rule 9.
    """
    keys: set[str] = set(_read_env_file_strict(resolve_scope_path("user")))
    keys.update(_read_env_file_strict(resolve_scope_path("project", project)))
    return keys


def sanitized_subprocess_env(
    own_env: Mapping[str, str] | None = None, project: Path | None = None
) -> dict[str, str]:
    """Build the environment for a downstream server subprocess.

    Inherits the gateway's environment MINUS PMCP-managed credentials — so a
    server never receives ANOTHER server's PMCP-stored secrets — then applies the
    server's OWN resolved credentials (``own_env``), which win over the strip
    (e.g. a server whose runtime env_var equals a managed key gets its own value
    back). Non-secret ambient vars (PATH/HOME/NODE_*/proxy/locale) are preserved.

    Also stripped: keys PMCP introduced into its own environment from a dotenv
    file (``dotenv_sourced_keys``) -- the operator's project ``.env`` reached
    the gateway through ``cli.load_startup_env`` and the availability check, and
    was inherited by every server PMCP spawned (Consiliency/pmcp#229). Those keys
    are stripped by recorded provenance, not by name, so a shell-exported
    variable that merely shares a name with a ``.env`` entry survives. A server's
    OWN declared ``env_var`` still arrives via ``own_env``, which is applied
    after the strip.

    Note: secrets the operator exported into their shell are still inherited --
    deliberately, and out of scope here.
    """
    env = os.environ.copy()
    for key in managed_secret_keys(project):
        env.pop(key, None)
    for key in dotenv_sourced_keys():
        env.pop(key, None)
    if own_env:
        env.update(own_env)
    return env


def set_env_value(
    scope: str, key: str, value: str, project: Path | None = None
) -> Path:
    """Set one env value in user or project PMCP credential storage."""
    validate_env_var_name(key)
    if "\n" in value or "\r" in value:
        raise ValueError("Credential values must not contain newlines")

    path = resolve_scope_path(scope, project)
    values = read_store_for_update(scope, path)
    values[key] = value
    write_env_file(path, values, confine_to=scope_confinement(scope, path))
    return path
