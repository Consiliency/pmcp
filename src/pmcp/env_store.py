"""Shared PMCP credential env-file storage helpers."""

from __future__ import annotations

import errno
import io
import os
import re
import stat
import sys
from dataclasses import dataclass
from collections.abc import Callable, Collection, Iterable, Mapping
from pathlib import Path
from typing import Literal

from dotenv import dotenv_values

from pmcp.atomic_write import (
    is_absent,
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
    """Resolve project root for project-scope secrets.

    ``project`` when given. Without one, the project this process SERVES
    (:func:`serve_project_root`: ``--project``, else the root discovered from
    the working directory at startup) -- so a project or tenant store located
    with no explicit project is the served project's, never the working
    directory's when they differ (Consiliency/pmcp#372 round 11, board round 10
    codex F001). Before anything is served: discovered from the working
    directory, as always.
    """
    if project:
        # Never realpath, strict or not (strict collapses `file/..` on 3.12,
        # non-strict `missing/..`): the root is kept as the operator spelled it,
        # absolute, and the kernel resolves it at every use. A root that does
        # not exist yet is created by make_store_dirs before a write -- only as
        # a plain tail of new directories; `missing/../x`, `file/../x`, a loop
        # or no permission is refused there, never treated as absent.
        return project if project.is_absolute() else Path.cwd() / project

    if _DEFAULT_ROOT is not None:
        return _DEFAULT_ROOT
    return _discover_project_root()


def _discover_project_root() -> Path:
    """The project root found from the working directory (markers), else the cwd.

    THE one place pmcp derives a project from where it was started
    (``tests/test_store_reader_inventory.py`` forbids any other). Its answer
    becomes the served root (:func:`serve_project_root`), and every
    project-scoped input -- credentials, tenant stores, ``.mcp.json``, the
    project manifest overlay, the project policy -- follows the served root or
    an explicit project, so an endpoint and its credential always come from the
    same project (Consiliency/pmcp#372 round 12, board round 11 codex F001).
    """
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
    try:
        where = os.path.realpath(path)
    except OSError:  # e.g. a working directory that no longer exists
        where = os.fspath(path)
    key = (where, message, _store_identity(path))
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


#: Credentials loaded from repository-controlled files, kept OUT of
#: ``os.environ``. Filtering what such a file may put into the process
#: environment was a denylist, and a denylist fails open: a proxy variable in
#: another case, ``SSLKEYLOGFILE``, ``LD_PRELOAD`` for a child. Instead nothing
#: it says reaches ``os.environ``; only explicit credential lookups consult
#: these values, through :func:`credential_value`.
#:
#: Keyed by PROJECT ROOT -- the root directory's resolved path plus its
#: ``(st_dev, st_ino)`` (:func:`_root_key`): a lookup for root R answers only
#: from R's own project files -- ``R/.env`` then ``R/.env.pmcp``, first wins --
#: so one project's credential never fills another project's header
#: (Consiliency/pmcp#372 round 8). Each entry is built by ONE builder
#: (:func:`_build_root_entry`) and records the root directory's ``st_ctime_ns``
#: and each source file's identity; a lookup rebuilds the entry, with the same
#: builder, when any of them changed, so a rotated token is picked up without a
#: restart and a new directory that reuses a deleted one's inode never inherits
#: its entry (round 9 N-1). Test-only reset: :func:`reset_repo_credentials`.
@dataclass
class _RootEntry:
    root: Path
    values: dict[str, str]
    #: ``(file name, identity)`` per source file; identity ``None`` when absent.
    #: A NAME, not a path: a cached entry is checked against the files under
    #: the root it is ASKED about, never against a path recorded earlier
    #: (board round 9, codex F002: a renamed root was answered from the file
    #: moved back to its old path).
    sources: tuple[tuple[str, object], ...]
    #: The root directory's ``st_ctime_ns`` when the entry was built.
    stamp: object = None


_REPO_CREDENTIALS: dict[object, _RootEntry] = {}

#: The root a lookup without an explicit project answers for: the SERVED
#: project root (:func:`serve_project_root` -- ``--project`` when given, else
#: the root discovered from the working directory); ``None`` until a startup
#: load or ``--project`` serves one, and lookups then use the discovered root
#: (:func:`resolve_project_root`). A consumer that knows a more specific root
#: passes it as ``root=`` (Consiliency/pmcp#372 rounds 9 and 12).
_DEFAULT_ROOT: Path | None = None

#: The project files of a root, in precedence order.
_ROOT_FILES = (".env", ".env.pmcp")

#: An identity that never equals a real one: the file changed while it was read.
_CHANGED = object()


def reset_repo_credentials() -> None:
    """Forget every repository-file credential and the default root. **Test-only seam.**"""
    global _DEFAULT_ROOT
    _REPO_CREDENTIALS.clear()
    _DEFAULT_ROOT = None


def set_default_root(root: Path) -> None:
    """The root an unqualified lookup answers for (see :func:`serve_project_root`)."""
    global _DEFAULT_ROOT
    _DEFAULT_ROOT = root


def served_project_root() -> Path | None:
    """The served project root (:func:`serve_project_root`), or ``None`` before one is set."""
    return _DEFAULT_ROOT


def project_scope_root(project: Path | None = None) -> Path | None:
    """The root project-scoped CONFIGURATION is read from, or ``None`` for none.

    ``project`` when given, else the served project root
    (:func:`resolve_project_root`). ``None`` when that root is the operator's
    home directory: home's ``.mcp.json`` and ``.pmcp/manifest.yaml`` are the
    USER sources, already loaded as such, and must not be attributed to a
    project as well (the stop ``find_project_root`` and the overlay walk always
    had). Used by ``.mcp.json`` loading and the project manifest overlay.

    A CLASSIFICATION, never a credential root: its ``None`` means "no project
    config source here", not "the served project". Credentials come from the
    project the caller named (Consiliency/pmcp#372 round 13);
    ``tests/test_store_reader_inventory.py`` fails if this function's answer
    reaches a credential lookup.
    """
    root = resolve_project_root(project)
    try:
        home = Path.home().resolve()
        if os.path.realpath(root) == os.fspath(home):
            return None
    except (OSError, RuntimeError):
        pass
    return root


def ensure_served_project_root() -> Path:
    """The served project root, discovering it from the working directory ONLY if unset.

    Loading the environment never chooses the project (Consiliency/pmcp#372
    round 14, board round 13 codex F001): a lazy startup load -- the first
    credential lookup in a library process -- used to call
    ``serve_project_root(None)`` unconditionally, replacing a root the caller
    had already served (``serve_project_root(B)``) with the launch directory's
    project. Only the first, unset state discovers one; an explicit
    :func:`serve_project_root` call always wins.
    """
    if _DEFAULT_ROOT is None:
        return serve_project_root(None)
    return _DEFAULT_ROOT


def serve_project_root(project: Path | None) -> Path:
    """Make the project this process serves the root every unqualified lookup answers for.

    ``project`` is ``--project`` (``None``: the root discovered from the working
    directory, :func:`resolve_project_root` -- the same root the gateway loads
    ``.mcp.json`` from). A gateway serving project B started from inside
    project A therefore answers B's install child, provision gate, injected
    server credential and availability checks from B's files, as it answers
    B's headers (Consiliency/pmcp#372 round 9).
    """
    root = resolve_project_root(project) if project else _discover_project_root()
    set_default_root(root)
    return root


def repo_credential_names(root: Path | None = None) -> frozenset[str]:
    """The names (never the values) of the credentials root ``root``'s files supply."""
    entry = _root_entry(root)
    return frozenset(entry.values) if entry is not None else frozenset()


def _root_key(root: Path) -> object:
    """``(resolved path, st_dev, st_ino)`` of a root directory.

    The path as well as the inode: a deleted directory's inode can be reused by
    a new directory elsewhere, which must not find the old entry. The entry's
    ``stamp`` (:func:`_root_stamp`) covers a directory recreated at the same
    path.
    """
    identity = _identity(root)
    where = os.path.realpath(root)
    return (where, *identity) if identity is not None else ("path", where)


def _root_stamp(root: Path) -> object:
    """The root directory's ``st_ctime_ns``, or ``None`` when it cannot be looked at."""
    try:
        return os.stat(root).st_ctime_ns
    except OSError:
        return None


def _file_identity(path: Path) -> object:
    """``(st_dev, st_ino, st_mtime_ns, st_ctime_ns, st_size)``, or ``None`` when absent/unreadable.

    ``st_ctime_ns`` as well as the modification time: a file replaced by one
    that reuses its inode, with the old modification time restored, still has
    a new change time, which no one can set (board round 9, grok).
    """
    try:
        if is_absent(path):
            return None
        status = os.lstat(path)
    except OSError:
        return None
    return (
        status.st_dev,
        status.st_ino,
        status.st_mtime_ns,
        status.st_ctime_ns,
        status.st_size,
    )


def _build_root_entry(root: Path) -> _RootEntry:
    """THE builder of a root's repository credentials.

    ``root/.env`` then ``root/.env.pmcp``, each read confined to ``root``
    (:func:`_read_confined_text`; a refused file contributes nothing, with one
    warning), expanded within its own file (:func:`_repository_values`), first
    loaded wins. The identity is taken before and after each read; a file that
    changed in between is recorded as changed, so the next lookup rebuilds.
    """
    values: dict[str, str] = {}
    sources: list[tuple[str, object]] = []
    stamp = _root_stamp(root)
    root_identity = _identity(root)
    # In the home directory, or above it, a `.env` is the operator's own
    # (load_discovered_dotenv loads it into the environment), not a project file.
    operators = root_identity is not None and root_identity in _home_and_its_ancestors()
    for name in _ROOT_FILES:
        if operators and name == ".env":
            continue
        store_path = root / name
        before = _file_identity(store_path)
        text = _read_confined_text(
            store_path, (root, "project"), strict=False, verb="load"
        )
        after = _file_identity(store_path)
        sources.append((name, after if after == before else _CHANGED))
        if text is None:
            continue
        for key, value in _repository_values(text, store_path).items():
            if not repository_may_supply(key):
                # Warned here; refused where it would be answered -- the one gate
                # is credential_value, whatever put the name into the entry.
                _warn_store_refused(
                    store_path, describe_ignored_store_env_var(key, store_path.name)
                )
            values.setdefault(key, value)
    return _RootEntry(root=root, values=values, sources=tuple(sources), stamp=stamp)


def _root_entry(root: Path | None) -> _RootEntry | None:
    """Root ``root``'s entry (default: the served project), rebuilt if it changed.

    No root means the served project root (:func:`resolve_project_root`):
    ``--project``, else the root discovered from the working directory -- the
    same root every other project-scoped input follows. A load never makes
    its own root the default as a side effect (Consiliency/pmcp#372 round 12:
    that made the project of whichever store happened to load first the
    process's project).
    """
    if root is None:
        root = resolve_project_root(None)
    key = _root_key(root)
    entry = _REPO_CREDENTIALS.get(key)
    if (
        entry is None
        or entry.stamp != _root_stamp(root)
        or any(_file_identity(root / name) != ident for name, ident in entry.sources)
    ):
        entry = _build_root_entry(root)
        _REPO_CREDENTIALS[key] = entry
    return entry


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


#: pmcp's own variable namespace, in any case (a regex rather than
#: ``str.startswith``: the store modules' AST ban reads ``startswith`` as a path
#: containment test).
_PMCP_NAMESPACE = re.compile(r"PMCP_", re.IGNORECASE)


def repository_may_supply(name: str) -> bool:
    """May a repository-controlled file supply the variable ``name``? THE one rule.

    No for anything that decides what pmcp, or a program it starts, loads,
    trusts or connects to: a variable pmcp reads from its own environment
    (:func:`is_pmcp_environment_name`: the classified names in any case, any
    ``*_proxy``), any name in the ``PMCP_`` namespace (any case, classified or
    not), a code-loading variable (``validation.is_dangerous_env_var``:
    ``LD_*``, ``DYLD_*``, ``PYTHON*``, ``NODE_OPTIONS``, ``PATH`` ...), and a
    package-manager or runtime family (``validation.is_package_manager_env_var``:
    ``NPM_CONFIG_*``, ``NODE_*``, ``COREPACK_*`` ...). Everything else is a
    candidate credential. A DENYLIST, so it is the rule only where a value goes
    to the server a repository configured: :func:`credential_value` asks it
    before answering from a repository source (a remote header, a server's
    declared credential), and the repository loader before keeping a binding
    quietly. Anything that moves a repository value into an operator-trusted
    place -- the user store, the environment -- uses the allowlist
    :func:`copy_allowed`, with this as a second layer (Consiliency/pmcp#372
    rounds 5-6).
    """
    from pmcp.validation import is_dangerous_env_var, is_package_manager_env_var

    return not (
        is_pmcp_environment_name(name)
        # The whole PMCP_ prefix, in any case: a variable pmcp starts reading
        # later is refused before anyone classifies it (Consiliency/pmcp#372
        # round 7, N-1).
        or _PMCP_NAMESPACE.match(name) is not None
        or is_dangerous_env_var(name)
        or is_package_manager_env_var(name)
    )


def describe_uncopied_store_entry(variable: str, store_name: str, reason: str) -> str:
    """Value-free: an entry of a repository store that was not copied."""
    return f"Not copying {variable} from {store_name}: {reason}."


def declared_credential_names() -> frozenset[str]:
    """Every name a manifest server declares as its credential (lookup keys).

    The packaged manifest plus any approved overlay (``load_manifest``). This is
    how a credential that is not credential-shaped by name -- ``POSTGRES_URL``,
    ``AWS_ACCESS_KEY_ID`` -- may still be copied. A manifest that cannot be
    loaded declares nothing: only credential-shaped names are copied then.
    """
    try:
        from pmcp.manifest.loader import credential_lookup_keys, load_manifest

        names: set[str] = set()
        for server in load_manifest().servers.values():
            names.update(credential_lookup_keys(server))
        return frozenset(names)
    except Exception:  # pragma: no cover - a broken manifest copies less, never more
        return frozenset()


def copy_allowed(name: str, declared: Collection[str]) -> bool:
    """May a repository store's ``name`` move into an operator-trusted store? An ALLOWLIST.

    Only a credential: a name that is credential-shaped by the one pattern pmcp
    already uses (``validation.is_credential_shaped``: ``*_TOKEN``, ``*_KEY``,
    ``*_SECRET(S)``, ``*_PASSWORD``, ``*_CREDENTIAL(S)``, ``*_PAT``, ``*_DSN``,
    ``*_AUTH``), or one a manifest server declares as its credential
    (:func:`declared_credential_names`). Anything else -- ``UV_INDEX_URL``,
    ``PIP_CONFIG_FILE``, ``JAVA_TOOL_OPTIONS``, a new ``PMCP_*`` -- is refused,
    whatever it is: the user store loads into pmcp's environment, and pmcp's own
    spawns (``pmcp upgrade`` runs uv or pip) keep that environment. The
    lookup-side denylist (:func:`repository_may_supply`) stays as a second
    layer: ``NODE_AUTH_TOKEN`` is credential-shaped and still refused
    (Consiliency/pmcp#372 round 6).
    """
    from pmcp.validation import is_credential_shaped

    if not repository_may_supply(name):
        return False
    return is_credential_shaped(name) or name in declared


def copyable_from_repository(
    values: Mapping[str, str],
    store_name: str,
    declared: Collection[str] | None = None,
) -> tuple[dict[str, str], list[str]]:
    """The entries of a repository store that may move into an operator's store.

    The user store loads into pmcp's own environment at every start, from any
    directory, so a value copied there from a project file must be a credential
    (:func:`copy_allowed`, an allowlist). It must also not hold ``${``: the user
    store is loaded with expansion, so ``X=${GITHUB_TOKEN}`` would turn into the
    operator's secret under a new name. Each refused entry gets one value-free
    warning; its value is never copied. Returns ``(copyable, refused names)``.
    """
    allowed_names = declared_credential_names() if declared is None else declared
    copyable: dict[str, str] = {}
    refused: list[str] = []
    for key, value in values.items():
        if not copy_allowed(key, allowed_names):
            reason = (
                "only credentials are copied out of a project file -- a "
                "credential-shaped name or one a server declares -- and this is "
                "not one"
            )
        elif "${" in value:
            reason = (
                "its value refers to a variable, and your user store would "
                "expand it from your environment"
            )
        elif "\n" in value or "\r" in value:
            # The store writer refuses a multi-line value for the whole file;
            # skipping it here lets the other credentials still be copied.
            reason = "its value spans more than one line, which a store cannot hold"
        else:
            copyable[key] = value
            continue
        refused.append(key)
        print(
            f"pmcp: {describe_uncopied_store_entry(key, store_name, reason)}",
            file=sys.stderr,
        )
    return copyable, refused


def credential_value(
    key: str,
    *,
    environ: bool = True,
    repository: Mapping[str, str] | None = None,
    startup_files: bool = True,
    root: Path | None = None,
) -> str | None:
    """The credential named ``key``: THE one gate every credential read goes through.

    Every lookup that resolves a name in ``src/pmcp`` -- a server's declared
    credential, a remote ``${VAR}`` header (project and tenant), the
    availability checks, ``pmcp secrets check`` -- calls this (directly, or
    through :func:`credential_lookup`), and nothing else reads the credential
    map or a store's values (``tests/test_store_reader_inventory.py``).
    Sources, in order:

    1. the process environment (``environ``) -- the shell, then the user store,
       which the startup load puts there with ``override=False``;
    2. ``repository`` -- a TENANT store's values (``remote_auth``), the one
       layer between the user store and the project credentials;
    3. the project files of ONE root (``startup_files``): ``root``, else the
       served project root (:func:`serve_project_root`) -- its ``.env``, then
       its ``.env.pmcp``, first wins -- never another root's.

    Precedence is decided by MEMBERSHIP, not truthiness: the first source that
    HAS the name decides, and an empty value there means "unavailable" -- an
    operator's exported ``BRAVE_API_KEY=""`` is never filled from a project file.
    A name a repository may not supply (:func:`repository_may_supply`: pmcp's
    own variables in any case, any ``*_proxy``, code-loading and package-manager
    families) is never answered from a repository source.

    No answer is given before the startup load has put the user store into
    the environment (:func:`ensure_startup_load`; idempotent, and it never
    changes the served root): otherwise a library caller's first lookup --
    ``GatewayServer.initialize`` building configs before anything loaded --
    answered from the project file and later lookups from the user store
    (Consiliency/pmcp#372 round 15, board round 14 codex F001).
    """
    ensure_startup_load()
    if environ and key in os.environ:
        return os.environ[key] or None
    repository_may_answer = repository_may_supply(key)
    if repository_may_answer and repository is not None and key in repository:
        return repository[key] or None
    if repository_may_answer and startup_files:
        entry = _root_entry(root)
        if entry is not None and key in entry.values:
            return entry.values[key] or None
    return None


#: Has this process run the startup load (``cli.load_startup_env``)? Set by it;
#: a diagnostic in a process that never ran it runs it first, so every reader
#: sees the same map. Test-only reset: :func:`reset_startup_load`.
_STARTUP_LOADED = False


def mark_startup_loaded() -> None:
    """Record that ``cli.load_startup_env`` built the credential map."""
    global _STARTUP_LOADED
    _STARTUP_LOADED = True


def reset_startup_load() -> None:
    """Forget that the startup load ran. **Test-only seam.**"""
    global _STARTUP_LOADED
    _STARTUP_LOADED = False


def ensure_startup_load() -> None:
    """Build the credential map the way a ``pmcp`` process does, once.

    The runtime reads repository credentials from ONE map, built by the startup
    load (the discovered ``.env``, the user store into the environment, the
    served project's ``.env.pmcp``) and extended by the same loader
    (:func:`load_store`) whenever another store is consulted. A diagnostic that
    runs in a process which never ran the startup load runs it here rather than
    reading the stores itself, so it cannot merge them in a different order
    (Consiliency/pmcp#372 round 7).
    """
    if _STARTUP_LOADED:
        return
    from pmcp.cli import load_startup_env

    # Lazy: load what the startup load loads, but choose no served root --
    # only ``pmcp`` itself (main) or an explicit serve_project_root does.
    load_startup_env(choose_root=False)


def credential_lookup(project: Path | None = None) -> Callable[[str], str | None]:
    """The one credential lookup: runtime and diagnostics read the same entries.

    :func:`credential_value` for root ``project`` (``None``: the served project
    root), which runs the startup load before it answers -- exactly what the provision gate, the install child and the
    gateway's credential check read for that root. Remote ``${VAR}`` headers,
    ``pmcp doctor`` and ``pmcp secrets check`` call this;
    ``tests/test_credential_parity.py`` checks their verdicts against the
    runtime's (Consiliency/pmcp#372 rounds 5-8).
    """
    root = None if project is None else resolve_project_root(project)

    def lookup(key: str) -> str | None:
        return credential_value(key, root=root)

    return lookup


def describe_ignored_store_env_var(variable: str, store_name: str) -> str:
    """Value-free: a variable a repository file may not supply (:func:`repository_may_supply`)."""
    return (
        f"Ignoring {variable} in {store_name}: a project file supplies credentials "
        "only, and this variable decides what pmcp or a program it starts loads, "
        "trusts or connects to, so export it in the shell that starts pmcp (or set "
        "it in ~/.config/pmcp/pmcp.env)."
    )


def describe_unresolved_store_value(variable: str, store_name: str) -> str:
    """Value-free: a repository file's value refers to a variable it does not define."""
    return (
        f"Ignoring {variable} in {store_name}: its value refers to a variable the "
        "file does not define, and a project file's values are never expanded from "
        "your environment or your user store."
    )


def _repository_values(text: str, store_path: Path) -> dict[str, str]:
    """A repository file's values, ``${X}`` expanded from the SAME file only.

    Parsed with ``interpolate=False``, then each ``${X}`` is expanded from the
    keys defined EARLIER in the same file (python-dotenv's own order), or from
    its ``${X:-default}``. A reference to anything else -- the operator's
    environment, the user store, another file -- is never expanded: the whole
    binding is unavailable, with one value-free warning. Otherwise
    ``LEAK=${PMCP_AUTH_TOKEN}`` would copy an operator secret into a new,
    unrestricted name that a repository-configured header then sends
    (Consiliency/pmcp#372 round 3). ``$X`` without braces is a literal, as
    python-dotenv reads it.
    """
    from dotenv.variables import Variable, parse_variables

    resolved: dict[str, str] = {}
    raw = dotenv_values(stream=io.StringIO(text), interpolate=False)
    for key, value in raw.items():
        if value is None:
            continue
        parts: list[str] = []
        for atom in parse_variables(value):
            if isinstance(atom, Variable):
                if atom.name in resolved:
                    parts.append(resolved[atom.name])
                elif atom.default is not None:
                    parts.append(atom.default)
                else:
                    _warn_store_refused(
                        store_path,
                        describe_unresolved_store_value(key, store_path.name),
                    )
                    break
            else:
                parts.append(atom.resolve({}))
        else:
            resolved[key] = "".join(parts)
    return resolved


def repository_values(
    scope: StoreScope,
    *,
    project: Path | None = None,
    tenant_id: str | None = None,
) -> dict[str, str]:
    """A project or tenant store's credentials, for ``credential_value(repository=...)``.

    Read as :func:`read_store` reads it (confined; a refused store is empty with
    one warning), expanded within the file only (:func:`_repository_values`).
    """
    if scope == "user":
        raise ValueError("the user store is the operator's: use read_store")
    located = _locate_or_warn(
        scope, project, tenant_id, None, strict=False, verb="read"
    )
    if located is None:
        return {}
    store_path, confinement = located
    assert confinement is not None
    text = _read_confined_text(store_path, confinement, strict=False, verb="read")
    if text is None:
        return {}
    return _repository_values(text, store_path)


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
    # A repository file is one of its root's project files: (re)build that
    # root's entry with THE builder (_build_root_entry).
    root = confinement[0]
    _REPO_CREDENTIALS[_root_key(root)] = _build_root_entry(root)


def _home_and_its_ancestors() -> set[tuple[int, int]]:
    """``(st_dev, st_ino)`` of the operator's home directory and every ancestor.

    The home directory is the user store's (:func:`resolve_scope_path`): the
    pinned one once the startup load has pinned it. Asking does not pin -- the
    pin is the startup load's, taken before any file loads
    (``cli.load_startup_env``), and a lookup must not take it as a side effect
    at some later moment. Identity, not path strings: a repository can choose
    path spellings, not inodes.
    """
    home = resolve_scope_path("user").parent.parent.parent
    identities: set[tuple[int, int]] = set()
    for directory in (home, *home.parents):
        identity = _identity(directory)
        if identity is not None:
            identities.add(identity)
    return identities


def _identity(path: Path) -> tuple[int, int] | None:
    """``(st_dev, st_ino)``, or ``None`` when absent or when it cannot be looked at.

    Absence is :func:`pmcp.atomic_write.is_absent`'s; any other failure to look
    means "not provably the operator's", so a file is then treated as a
    repository's -- the safer reading.
    """
    try:
        if is_absent(path):
            return None
        status = os.stat(path)
    except OSError:
        return None
    return (status.st_dev, status.st_ino)


def load_discovered_dotenv(path: Path) -> None:
    """Load the ``.env`` python-dotenv's startup walk found, by WHERE it is.

    The walk starts at pmcp's installed files. The file is the operator's only
    when it sits in the home directory itself or in an ANCESTOR of it -- a
    ``uv tool`` or ``pip --user`` install's walk reaches ``~/.env`` -- and then
    it loads into the environment as before, following its link, with no
    warning. Anywhere else it is a repository's: pmcp installed in a checkout's
    ``.venv`` (``uv run``, ``pip install -e``, ``pip install -r
    requirements.txt``) reaches that checkout's ``.env`` whatever marker files
    the checkout has, so it is read confined, into the credential map
    (Consiliency/pmcp#372 round 3: a marker-file test let a ``setup.py`` or
    ``requirements.txt`` checkout's ``.env`` through).
    """
    from dotenv import load_dotenv

    directory = _identity(path.parent)
    operators = directory is not None and directory in _home_and_its_ancestors()
    if not operators:
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
        # Strict, and the store the caller is about to write: an unreadable
        # user store must not be rewritten from an empty read.
        return _read_env_file_strict(path)
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


def operator_managed_secret_keys() -> frozenset[str]:
    """The names in the operator's own user store: what a spawn strips by name.

    Only the user store. A repository's ``.env.pmcp`` names nothing pmcp strips
    from a child's environment (:func:`sanitized_subprocess_env`).
    """
    return frozenset(read_store("user"))


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

    No repository file decides what is stripped (Consiliency/pmcp#372 round
    11, board round 10 grok F001). The strip used to include every NAME the
    project's ``.env.pmcp`` listed, so a checkout that merely named
    ``NO_PROXY`` or ``SSL_CERT_FILE`` deleted the operator's own exported value
    from every server pmcp spawned. A repository store's values never enter
    the environment (:func:`load_store`), so it has nothing to strip. What is
    stripped: the names in the operator's own user store, the PMCP-managed
    secrets of Consiliency/pmcp#230 (:func:`operator_managed_secret_keys`), and,
    by provenance, the keys pmcp itself put into this environment -- a
    credential ``auth_connect`` set, whichever store it wrote
    (:func:`pmcp_introduced_keys`), and what a ``~/.env`` the startup walk
    loaded introduced (:func:`dotenv_sourced_keys`). ``project`` no longer
    changes the result; it is kept for callers.
    """
    del project  # see above: a project store contributes nothing to strip
    env = child_process_env()
    strip = (
        operator_managed_secret_keys() | pmcp_introduced_keys() | dotenv_sourced_keys()
    )
    for key in strip:
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
