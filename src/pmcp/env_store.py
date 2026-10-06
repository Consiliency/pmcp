"""Shared PMCP credential env-file storage helpers."""

from __future__ import annotations

import errno
import io
import os
import re
import stat
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Literal

from dotenv import dotenv_values

from pmcp.atomic_write import (
    atomic_write,
    make_store_dirs,
    read_confined,
)
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
        # Never realpath, strict or not (strict collapses `file/..` on 3.12,
        # non-strict `missing/..`): the root is kept as the operator spelled it,
        # absolute, and the kernel resolves it at every use. A root that does
        # not exist yet is created by make_store_dirs before a write -- only as
        # a plain tail of new directories; `missing/../x`, `file/../x`, a loop
        # or no permission is refused there, never treated as absent.
        return project if project.is_absolute() else Path.cwd() / project

    discovered = find_project_root(Path.cwd())
    if discovered:
        return discovered

    # os.getcwd() is already the kernel's physical path; nothing to resolve.
    return Path.cwd()


#: The user store's path, fixed by :func:`pin_user_store_path` before any
#: repository-controlled store is loaded, so nothing loaded later -- a ``HOME``
#: a project file filled in while it was unset -- can move it
#: (Consiliency/pmcp#372 round 1). ``None`` until pinned: library use and tests
#: resolve it from ``Path.home()`` on every call.
_PINNED_USER_STORE: Path | None = None


def pin_user_store_path() -> Path:
    """Resolve ``~/.config/pmcp/pmcp.env`` now and keep that answer for the process."""
    global _PINNED_USER_STORE
    if _PINNED_USER_STORE is None:
        _PINNED_USER_STORE = Path.home() / ".config" / "pmcp" / "pmcp.env"
    return _PINNED_USER_STORE


def reset_user_store_pin() -> None:
    """Forget the pinned user-store path. **Test-only seam.**"""
    global _PINNED_USER_STORE
    _PINNED_USER_STORE = None


def resolve_scope_path(scope: str, project: Path | None = None) -> Path:
    """Resolve env file path for a credential scope."""
    if scope == "user":
        if _PINNED_USER_STORE is not None:
            return _PINNED_USER_STORE
        return Path.home() / ".config" / "pmcp" / "pmcp.env"
    if scope == "project":
        return resolve_project_root(project) / ".env.pmcp"
    raise ValueError(f"Unsupported secret scope: {scope}")


TENANT_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")


def tenant_store_path(project_root: Path, tenant_id: str) -> Path:
    """``<project_root>/.pmcp/tenants/<tenant_id>/pmcp.env`` for a valid tenant id.

    An id made only of dots is refused: ``..`` would name ``.pmcp/pmcp.env`` and
    ``.`` would name ``.pmcp/tenants/pmcp.env`` -- files inside the project, but
    no tenant's store.
    """
    if not TENANT_ID_PATTERN.fullmatch(tenant_id) or not tenant_id.strip("."):
        raise ValueError(
            "Tenant id may only contain letters, numbers, dot, underscore, or dash, "
            "and may not be made only of dots."
        )
    return project_root / ".pmcp" / "tenants" / tenant_id / "pmcp.env"


# --------------------------------------------------------------------------- #
# The one way to READ a credential store (Consiliency/pmcp#367).
# --------------------------------------------------------------------------- #

#: Who controls the store. ``user`` is ``~/.config/pmcp/pmcp.env``, the
#: operator's own file, whose link (a dotfiles repository) is followed anywhere.
#: ``project`` (``<project>/.env.pmcp``, or a checkout file named by ``path=``)
#: and ``tenant`` (``<project>/.pmcp/tenants/<id>/pmcp.env``) are files a cloned
#: repository ships, read only through the confined walk.
StoreScope = Literal["user", "project", "tenant"]

#: Refusals already reported in this load cycle, keyed by WHICH store (its
#: resolved path), the reason, and the entry's identity (device, inode, change
#: time), so one store refused by every spawn warns once, while a different
#: store, or the same store after it changed, warns again. Never printed: the
#: line names only the file name. :func:`begin_store_read_cycle` clears it.
_WARNED_REFUSALS: set[tuple[str, str, tuple[int, int, int] | None]] = set()


def begin_store_read_cycle() -> None:
    """Start a new load cycle: a store still refused warns once more.

    Called at the start of each gateway configuration load
    (``config.loader.resolve_startup_configs``), so a long-running gateway
    re-reports a store that is still refused rather than going quiet forever.
    """
    _WARNED_REFUSALS.clear()


def reset_store_warnings() -> None:
    """Forget which store refusals were already reported. **Test-only seam.**"""
    _WARNED_REFUSALS.clear()


def _store_identity(path: Path) -> tuple[int, int, int] | None:
    try:
        st = os.lstat(path)
    except OSError:
        return None
    return (st.st_dev, st.st_ino, st.st_ctime_ns)


def _warn_store_refused(path: Path, message: str) -> None:
    key = (os.path.realpath(path), message, _store_identity(path))
    if key in _WARNED_REFUSALS:
        return
    _WARNED_REFUSALS.add(key)
    print(f"pmcp: {message}", file=sys.stderr)


def cwd_store_confinement(path: Path) -> tuple[Path, str]:
    """The root a checkout file named by its path (``<cwd>/.env.pmcp``) is confined to.

    Its own directory: a repository store is never read through a symlink of
    any kind (Consiliency/pmcp#366), so no other directory is walked. The
    startup load and the gateway's credential-availability check use this;
    every store whose path PMCP builds from a project root is confined to that
    root instead, so a symlinked directory below the root is refused too.
    """
    return path.parent, "project"


def _locate_store(
    scope: StoreScope,
    project: Path | None,
    tenant_id: str | None,
    path: Path | None,
) -> tuple[Path, tuple[Path, str] | None]:
    """``(store path, (confinement root, label) or None)`` for a scope.

    The root is never re-derived from the store path: a repository controls
    every directory below the project root (``.pmcp``, ``tenants``, ``<id>``),
    and :func:`read_confined` opens its root by pathname.
    """
    if scope == "user":
        if path is not None or tenant_id is not None or project is not None:
            raise ValueError("the user store takes no project, tenant or path")
        return resolve_scope_path("user"), None
    if scope == "project":
        if tenant_id is not None:
            raise ValueError("the project store takes no tenant id")
        if path is not None:
            if project is not None:
                raise ValueError("pass a project root or a path, not both")
            return path, cwd_store_confinement(path)
        root = resolve_project_root(project)
        return root / ".env.pmcp", (root, "project")
    if scope == "tenant":
        if tenant_id is None or path is not None:
            raise ValueError("the tenant store takes a tenant id and no path")
        root = resolve_project_root(project)
        return tenant_store_path(root, tenant_id), (root, "project")
    raise ValueError(f"Unsupported secret scope: {scope}")


def _locate_or_warn(
    scope: StoreScope,
    project: Path | None,
    tenant_id: str | None,
    path: Path | None,
    *,
    strict: bool,
    verb: str,
) -> tuple[Path, tuple[Path, str] | None] | None:
    """:func:`_locate_store`, where finding the project root can itself fail.

    Resolving an existing ``--project`` is strict (links before ``..``), so an
    ancestor this process cannot search raises there, before any store is
    opened. A strict reader raises it; a lenient one reads the store as empty
    with one value-free warning, as for any other unreadable store.
    """
    try:
        return _locate_store(scope, project, tenant_id, path)
    except OSError as exc:
        if strict:
            raise
        name = "pmcp.env" if scope != "project" else ".env.pmcp"
        nominal = path if path is not None else Path(name)
        _warn_store_refused(nominal, store_refusal(nominal, exc, verb=verb))
        return None


def _read_confined_text(
    path: Path, confinement: tuple[Path, str], *, strict: bool, verb: str
) -> str | None:
    """A repository-controlled store's text through the confined walk.

    ``None`` when it is not there -- absent, a dangling link that stays inside,
    a directory on the way that does not exist (a tenant never written). Every
    other failure -- a link leaving the root, a file that is not regular, a
    loop, no permission, bytes that are not UTF-8 -- raises when ``strict``;
    otherwise the store reads as empty and one value-free line names the file
    and the reason on stderr (:func:`store_refusal`).
    """
    root, label = confinement
    try:
        data = read_confined(path, root, label=label, verb=verb)
        return None if data is None else data.decode("utf-8")
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        if strict:
            raise
        _warn_store_refused(path, store_refusal(path, exc, verb=verb))
        return None


def _read_user_text(path: Path, *, verb: str) -> str | None:
    """The user store's text for a LENIENT reader, following its link.

    Absence is only ENOENT/ENOTDIR, and that rule decides what a STRICT reader
    does (it raises). A lenient reader -- startup, header lookup, the
    credential check, env stripping, ``pmcp secrets check`` -- treats a user
    store it cannot read (``EACCES``, an unsearchable ``HOME``, bytes that are
    not UTF-8) as a repository store it refuses: one value-free warning, and
    empty. pmcp still starts.
    """
    try:
        return read_env_text(path)
    except (OSError, ValueError) as exc:
        _warn_store_refused(path, store_refusal(path, exc, verb=verb))
        return None


def read_store(
    scope: StoreScope,
    *,
    project: Path | None = None,
    tenant_id: str | None = None,
    path: Path | None = None,
    strict: bool = False,
    verb: str = "read",
) -> dict[str, str]:
    """THE reader of a PMCP credential store; the scope picks how it is read.

    Every ``src/pmcp`` reader of ``pmcp.env``, ``.env.pmcp`` or a tenant
    ``pmcp.env`` goes through this or :func:`load_store`
    (``tests/test_store_reader_inventory.py`` enumerates them from the AST), so
    no call site chooses between following and confining a link:

    * ``user`` -- ``~/.config/pmcp/pmcp.env`` -- follows its link wherever it
      points (:func:`read_env_file`; ``strict``: :func:`_read_env_file_strict`).
    * ``project`` -- ``<project>/.env.pmcp`` for ``project`` (resolved as
      ``--scope project`` resolves it), or the checkout file ``path`` -- and
      ``tenant`` -- ``<project>/.pmcp/tenants/<tenant_id>/pmcp.env`` -- are read
      only through :func:`pmcp.atomic_write.read_confined`, confined to the
      project root the write side uses (for ``path``, :func:`cwd_store_confinement`).
      A link that leaves the root, or a target that is not a regular file, is
      never opened.

    A store that is not there reads as ``{}``. A repository-controlled store
    that is refused reads as ``{}`` with one warning, or raises when ``strict``
    (the feedback gate, and a read before a rewrite, must fail closed).
    """
    located = _locate_or_warn(scope, project, tenant_id, path, strict=strict, verb=verb)
    if located is None:
        return {}
    store_path, confinement = located
    if confinement is None:
        if strict:
            return _read_env_file_strict(store_path)
        text = _read_user_text(store_path, verb=verb)
        if text is None:
            return {}
        return _env_values(dotenv_values(stream=io.StringIO(text), interpolate=False))
    text = _read_confined_text(store_path, confinement, strict=strict, verb=verb)
    if text is None:
        return {}
    return _env_values(dotenv_values(stream=io.StringIO(text), interpolate=False))


# --------------------------------------------------------------------------- #
# A repository-controlled store never populates pmcp's own environment
# (Consiliency/pmcp#372 round 2).
# --------------------------------------------------------------------------- #

#: Credentials loaded from repository-controlled files -- a checkout's
#: ``.env.pmcp`` and ``.env``, the ``.env`` the startup walk finds inside a
#: project -- kept OUT of ``os.environ``. Filtering what such a file may put into
#: the process environment was a denylist, and a denylist fails open: a proxy
#: variable in another case, ``SSLKEYLOGFILE``, ``LD_PRELOAD`` for a child.
#: Instead nothing it says reaches ``os.environ``, so pmcp's own code, the
#: libraries it runs (httpx, ssl, the MCP SDK) and every child it spawns never
#: see a repository-chosen variable at all. Only explicit credential lookups
#: consult this map, through :func:`credential_value`. First loaded wins, as
#: ``load_dotenv(override=False)`` did. Test-only reset:
#: :func:`reset_repo_credentials`.
_REPO_CREDENTIALS: dict[str, str] = {}


def reset_repo_credentials() -> None:
    """Forget every repository-file credential. **Test-only seam.**"""
    _REPO_CREDENTIALS.clear()


def repo_credential_names() -> frozenset[str]:
    """The names (never the values) of the credentials repository files supplied."""
    return frozenset(_REPO_CREDENTIALS)


#: Variables pmcp -- or a library it runs -- reads from its own environment to
#: decide WHERE files are, WHAT to trust, or WHO may connect. Repository files
#: cannot set them (they never reach the environment); the list remains for ONE
#: purpose: :func:`credential_value` never answers one of these names, or any
#: other variable pmcp reads from its environment, from a repository file -- so
#: a credential lookup that happens to name ``PATH`` or ``Https_Proxy`` cannot
#: hand a repository's value to the caller either. Compared case-insensitively
#: (Windows' environment is, and urllib accepts any ``*_proxy`` in any case).
#: ``tests/test_store_reader_inventory.py`` derives every variable ``src/pmcp``
#: itself reads from the AST and fails until each is classified here; it cannot
#: see what libraries or children read, which is why repository files are kept
#: out of the environment rather than filtered.
PATH_AND_TRUST_ENV_VARS: frozenset[str] = frozenset(
    {
        # Where files are: Path.home()/expanduser, tempfile, XDG and Windows
        # profile directories, the uv tool directory, PMCP's own paths.
        "HOME",
        "USERPROFILE",
        "HOMEDRIVE",
        "HOMEPATH",
        "APPDATA",
        "LOCALAPPDATA",
        "XDG_CONFIG_HOME",
        "XDG_CACHE_HOME",
        "XDG_DATA_HOME",
        "XDG_STATE_HOME",
        "XDG_RUNTIME_DIR",
        "TMPDIR",
        "TEMP",
        "TMP",
        "UV_TOOL_DIR",
        "PMCP_AUDIT_JSONL",
        "PMCP_LOCK_DIR",
        # Which executable runs (shutil.which, every spawn).
        "PATH",
        "PATHEXT",
        # Who may connect, and with what: the gateway's own listener and auth.
        "PMCP_TRANSPORT",
        "PMCP_HOST",
        "PMCP_PORT",
        "PMCP_AUTH_MODE",
        "PMCP_AUTH_TOKEN",
        "PMCP_OAUTH_ISSUER",
        "PMCP_OAUTH_JWKS_URL",
        "PMCP_OAUTH_AUDIENCE",
        "PMCP_REQUIRED_SCOPES",
        "PMCP_ALLOWED_ORIGINS",
        # Where pmcp sends things: the gateway a CLI command talks to (a
        # credential, for `pmcp auth connect`), and the registry it trusts.
        "PMCP_GATEWAY_URL",
        "PMCP_STATUS_SSE_URL",
        "PMCP_REGISTRY_ALLOW_PRIVATE",
        "PMCP_REGISTRY_PRIVATE_ENDPOINT",
        # Read by the HTTP and TLS libraries pmcp uses (httpx, urllib, ssl): a
        # proxy sees every outbound header (any `*_proxy`, any case -- matched
        # by suffix below), a CA bundle decides whom TLS trusts, a key log
        # file receives every session key, netrc supplies credentials.
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "SSLKEYLOGFILE",
        "NETRC",
        "REQUESTS_CA_BUNDLE",
        "CURL_CA_BUNDLE",
    }
)

#: Read by pmcp to decide a path or trust, but decided at the point of use by
#: PROVENANCE instead (``env_key_is_operator_supplied``, the feedback gate's
#: ``untrusted``), which refuses a value from ANY file pmcp loaded -- the user
#: store included -- with its own "Ignoring ..." line or remedy.
PROVENANCE_GATED_ENV_VARS: frozenset[str] = frozenset(
    {
        "PMCP_MANIFEST_PATH",
        "PMCP_CONFIG",
        "PMCP_POLICY",
        "PMCP_FEEDBACK_REPO",
        "PMCP_FEEDBACK_TOKEN",
    }
)

#: Read by pmcp, and deciding nothing about paths, trust or confinement: tuning.
OPERATIONAL_ENV_VARS: frozenset[str] = frozenset(
    {
        "PMCP_LOG_LEVEL",
        "PMCP_RATE_LIMIT",
        "PMCP_REQUEST_TIMEOUT",
        "PMCP_REQUEST_CEILING_MS",
        "PMCP_STDIO_READ_LIMIT",
        "PMCP_MAX_SPAWNS",
        "PMCP_MAX_LISTEN_STREAMS",
    }
)

_PMCP_ENVIRONMENT_NAMES = frozenset(
    name.upper()
    for name in PATH_AND_TRUST_ENV_VARS
    | PROVENANCE_GATED_ENV_VARS
    | OPERATIONAL_ENV_VARS
)


def is_pmcp_environment_name(key: str) -> bool:
    """True for a variable pmcp itself reads from its environment, in any case.

    Every classified variable, compared upper-cased, and any name ending in
    ``_proxy`` in any case (urllib's own rule). A repository file never supplies
    such a variable, not even to a credential lookup.
    """
    upper = key.upper()
    return upper in _PMCP_ENVIRONMENT_NAMES or upper.endswith("_PROXY")


def credential_value(key: str) -> str | None:
    """The credential named ``key``: the process environment, then repository files.

    THE lookup every credential read in ``src/pmcp`` uses -- a server's declared
    credential, a remote ``${VAR}`` header, the availability checks -- so a
    credential a project's ``.env.pmcp`` supplies still works although it is not
    in ``os.environ``. The process environment (the shell, the user store) wins.
    ``None`` for an empty value. A name pmcp reads from its own environment
    (:func:`is_pmcp_environment_name`) is never answered from a repository file.
    """
    value = os.environ.get(key)
    if value:
        return value
    if is_pmcp_environment_name(key):
        return None
    return _REPO_CREDENTIALS.get(key) or None


def describe_ignored_store_env_var(variable: str, store_name: str) -> str:
    """Value-free: a variable pmcp reads from its environment, set in a repository file."""
    return (
        f"Ignoring {variable} in {store_name}: a project file supplies credentials "
        "only, and pmcp reads this variable from its own environment, so export it "
        "in the shell that starts pmcp (or set it in ~/.config/pmcp/pmcp.env)."
    )


def _load_repo_credentials(text: str, store_path: Path) -> None:
    """Parse a repository file's text into :data:`_REPO_CREDENTIALS`.

    ``dotenv_values(interpolate=True)``: the same parser ``load_dotenv`` uses;
    ``${X}`` resolves from the file first and then the environment (``load_dotenv``
    with ``override=False`` preferred the environment), and a key already in the
    map keeps its first value.
    """
    for key, value in dotenv_values(stream=io.StringIO(text), interpolate=True).items():
        if is_pmcp_environment_name(key):
            # Warned here; refused where it would be answered -- the one gate is
            # credential_value, whatever put the name into the map.
            _warn_store_refused(
                store_path, describe_ignored_store_env_var(key, store_path.name)
            )
        if value is not None:
            _REPO_CREDENTIALS.setdefault(key, value)


def load_store(
    scope: StoreScope,
    *,
    path: Path | None = None,
    project: Path | None = None,
    verb: str = "load",
) -> None:
    """Load a credential store: the user store into ``os.environ``, any other aside.

    The one way ``src/pmcp`` loads a dotenv file. It reads exactly as
    :func:`read_store` does (the user store follows its link; a
    repository-controlled file is read confined, and a refused one loads
    nothing, with one warning). The user store is the operator's and is loaded
    with ``load_dotenv(override=False)`` as before. A repository-controlled file
    goes into the credential map instead (:func:`credential_value`), never into
    the process environment.
    """
    from dotenv import load_dotenv

    located = _locate_or_warn(scope, project, None, path, strict=False, verb=verb)
    if located is None:
        return
    store_path, confinement = located
    if confinement is None:
        text = _read_user_text(store_path, verb=verb)
        if text is not None:
            load_dotenv(stream=io.StringIO(text), override=False)
        return
    text = _read_confined_text(store_path, confinement, strict=False, verb=verb)
    if text is not None:
        _load_repo_credentials(text, store_path)


def load_discovered_dotenv(path: Path) -> None:
    """Load the ``.env`` python-dotenv's startup walk found, by WHERE it is.

    The walk starts at pmcp's installed files. Inside a project -- pmcp
    installed in a checkout's ``.venv`` by ``uv run`` or ``pip install -e`` --
    the file is the repository's: read confined, into the credential map. Outside
    every project -- ``~/.env`` for a ``uv tool`` or ``pip --user`` install --
    it is the operator's own and loads into the environment as before, following
    its link, with no warning.
    """
    from dotenv import load_dotenv

    project_root = find_project_root(path.parent)
    cwd_root = find_project_root(Path.cwd())
    inside_a_project = project_root is not None or (
        cwd_root is not None and path.is_relative_to(cwd_root)
    )
    if inside_a_project:
        load_store("project", path=path)
        return
    text = _read_user_text(path, verb="load")
    if text is not None:
        load_dotenv(stream=io.StringIO(text), override=False)


def child_process_env(
    overlay: Mapping[str, str] | None = None,
    *,
    base: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """The environment for a subprocess pmcp spawns: THE builder.

    ``base`` (default: pmcp's own environment -- the shell plus the user store;
    a repository file never reaches it) plus ``overlay``. A downstream server's
    spawn uses :func:`sanitized_subprocess_env`, which builds on this and strips
    PMCP-managed credentials. ``tests/test_store_reader_inventory.py`` fails on
    any spawn in ``src/pmcp`` whose ``env=`` does not come from a builder.
    """
    env = dict(os.environ if base is None else base)
    if overlay:
        env.update(overlay)
    return env


def read_env_file(path: Path) -> dict[str, str]:
    """Read .env key/value pairs from path, following a symlink.

    The user-store half of :func:`read_store`; nothing in ``src/pmcp`` calls it
    for a repository-controlled file. An absent store, and one that is not a
    regular file (a fifo, a socket, a device, a directory), read as empty -- the
    read never blocks (:func:`read_env_text`).
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
    It follows a symlink, so it is only for the user store (:func:`read_store`);
    a repository-controlled store is read through :func:`read_confined`.
    """
    # The kernel decides absence: only ENOENT/ENOTDIR is "no store"; ELOOP
    # (too many links in one lookup), EACCES and the rest are raised, never
    # read as an empty store that the next write would then replace.
    try:
        entry = os.stat(path)
    except OSError as exc:
        if exc.errno in (errno.ENOENT, errno.ENOTDIR):
            return None
        raise
    if not stat.S_ISREG(entry.st_mode):
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

    :func:`read_store` for ``path`` (built by :func:`resolve_scope_path`), with
    the project store read ``strict``: a symlinked store (or one below a
    symlinked directory), or one that is not a regular file, raises the write's
    value-free refusal BEFORE anything is opened, so ``pmcp secrets set``/``sync``
    and ``gateway.auth_connect`` never read through a link they would refuse to
    write. Its root is ``path.parent`` -- the root the write is confined to
    (:func:`scope_confinement`). The user store is read strictly too.
    """
    if scope == "user":
        # Strict: an unreadable user store must not be rewritten from an
        # empty read (the lenient read would warn and return {}).
        return read_store("user", strict=True, verb=verb)
    if scope == "project":
        return read_store(
            "project", project=scope_confinement(scope, path), strict=True, verb=verb
        )
    raise ValueError(f"Unsupported secret scope: {scope}")


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


def scope_store_name(scope: str) -> str:
    """The store's file name for ``scope``, without resolving any path."""
    return {"user": "pmcp.env", "project": ".env.pmcp"}.get(scope, "pmcp.env")


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
    project root for the project store, which pmcp never reads or writes through
    a symlink (one is refused with ``ConfinedWriteError``, never written through
    and never replaced), and ``None`` for the user store.
    """
    _validate_env_values(values)

    lines = [f"{key}={_format_env_value(val)}" for key, val in values.items()]
    content = "\n".join(lines)
    if content:
        content += "\n"

    # Tighten only directories PMCP itself creates (e.g. ~/.config/pmcp for
    # user-scope secrets) to 0700. Never chmod a pre-existing directory such as
    # a project root, which for project-scope secrets is path.parent.
    # Only the plain tail of directories that do not exist yet is created
    # (make_store_dirs walks the path as the kernel would): `mkdir(parents=True)`
    # on an unresolvable `missing/../x` would create `missing` and land on `x`.
    for created in make_store_dirs(path.parent):
        try:
            os.chmod(created, 0o700)  # a umask that stripped owner bits
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

    The project half is read confined to the project (:func:`read_store`): a
    store that cannot be read -- a link out of the checkout, a fifo, no
    permission -- contributes no keys, with one warning, rather than crashing a
    spawn (Consiliency/pmcp#367).
    """
    keys: set[str] = set(read_store("user"))
    keys.update(read_store("project", project=project))
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

    Both read through :func:`read_store` with ``strict=True``: the user half as
    :func:`_read_env_file_strict`, the project half through the confined walk,
    so a project store linked out of the checkout, or not a regular file, RAISES
    here (Consiliency/pmcp#367) -- the gate fails closed rather than reading a
    file the repository pointed it at.
    """
    keys: set[str] = set(read_store("user", strict=True))
    keys.update(read_store("project", project=project, strict=True))
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
    env = child_process_env()
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
