"""Shared PMCP credential env-file storage helpers."""

from __future__ import annotations

import errno
import os
import re
from collections.abc import Iterable, Mapping
from pathlib import Path

from dotenv import dotenv_values

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
    """Read .env key/value pairs from path."""
    if not path.exists():
        return {}

    parsed = dotenv_values(path, interpolate=False)
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
    """
    if path.exists() and not path.is_file():
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


def write_env_file(path: Path, values: dict[str, str]) -> None:
    """Write key/value pairs to .env file and lock permissions to 0600."""
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
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as env_file:
        env_file.write(content)


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
    the file is opened. Every one of those fails closed. What is real:

    * :func:`write_env_file` truncates before it writes (``O_TRUNC``, then a
      separate write), so a write that fails partway -- ``ENOSPC``, a quota, a
      kill signal -- leaves the file truncated and its remaining entries gone;
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
    values = read_env_file(path)
    values[key] = value
    write_env_file(path, values)
    return path
