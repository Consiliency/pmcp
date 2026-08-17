"""Package version checking for npm, PyPI, crates.io, and Docker Hub packages."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Literal
from urllib.parse import quote

import aiohttp

from pmcp import __version__

logger = logging.getLogger(__name__)

# Cache for version lookups (avoid repeated network calls)
_version_cache: dict[str, str] = {}

_USER_AGENT = f"pmcp/{__version__} (github.com/ViperJuice/pmcp)"


def _strip_npm_tag(package: str) -> str:
    if package.startswith("@"):
        scope, sep, remainder = package.partition("/")
        if not sep:
            return package
        name, tag_sep, _tag = remainder.rpartition("@")
        return f"{scope}/{name}" if tag_sep and name else package

    name, tag_sep, _tag = package.rpartition("@")
    return name if tag_sep and name else package


def _npm_tag(package: str) -> str | None:
    """The npm dist-tag/version suffix ``_strip_npm_tag`` would discard, or
    ``None`` if *package* has no such suffix.

    Mirrors ``_strip_npm_tag``'s own scope-aware ``rpartition`` exactly (same
    guard conditions), so the two functions can never disagree about whether
    a suffix is present or where it starts. Used by gateway.update_server's
    pin detection.
    """
    if package.startswith("@"):
        scope, sep, remainder = package.partition("/")
        if not sep:
            return None
        _name, tag_sep, tag = remainder.rpartition("@")
        return tag if tag_sep and _name else None

    _name, tag_sep, tag = package.rpartition("@")
    return tag if tag_sep and _name else None


def _npm_package_arg(args: list[str]) -> str | None:
    """Return the raw npm/npx package token from *args*, or ``None``.

    "Raw" means *before* ``_strip_npm_tag`` removes any ``@tag``/``@version``
    suffix -- factored out of ``detect_package_type``'s npm branch so a
    caller that needs the untouched token (gateway.update_server's pin
    detection) can get it from the exact same scan ``detect_package_type``
    itself uses, instead of re-implementing the scan a second time and
    risking it picking a different argument as "the package".
    """
    for arg in args:
        if arg == "-y":
            continue
        # Skip flags
        if arg.startswith("-"):
            continue
        # Found package name (might have @version or @dist-tag suffix)
        pkg = _strip_npm_tag(arg)
        # Handle scoped packages like @playwright/mcp
        if pkg.startswith("@") or not pkg.startswith("-"):
            return arg
    return None


async def get_npm_version(package_name: str, timeout: float = 10.0) -> str | None:
    """
    Get the latest version of an npm package.

    Args:
        package_name: The npm package name (e.g., "@playwright/mcp")
        timeout: Request timeout in seconds

    Returns:
        Version string (e.g., "0.0.19") or None if lookup fails
    """
    cache_key = f"npm:{package_name}"
    if cache_key in _version_cache:
        return _version_cache[cache_key]

    # Handle scoped packages (@org/pkg): escape the whole name segment
    # (@ -> %40, / -> %2F) so it is a single path component.
    url = f"https://registry.npmjs.org/{quote(package_name, safe='')}"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url, timeout=aiohttp.ClientTimeout(total=timeout)
            ) as resp:
                if resp.status != 200:
                    logger.debug(
                        f"npm lookup failed for {package_name}: HTTP {resp.status}"
                    )
                    return None

                data = await resp.json()
                version = data.get("dist-tags", {}).get("latest")
                if version:
                    _version_cache[cache_key] = version
                return version

    except asyncio.TimeoutError:
        logger.debug(f"npm lookup timeout for {package_name}")
        return None
    except Exception as e:
        logger.debug(f"npm lookup error for {package_name}: {e}")
        return None


async def get_pypi_version(package_name: str, timeout: float = 10.0) -> str | None:
    """
    Get the latest version of a PyPI package.

    Args:
        package_name: The PyPI package name (e.g., "mcp-server-git")
        timeout: Request timeout in seconds

    Returns:
        Version string (e.g., "2025.12.18") or None if lookup fails
    """
    cache_key = f"pypi:{package_name}"
    if cache_key in _version_cache:
        return _version_cache[cache_key]

    url = f"https://pypi.org/pypi/{quote(package_name, safe='')}/json"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url, timeout=aiohttp.ClientTimeout(total=timeout)
            ) as resp:
                if resp.status != 200:
                    logger.debug(
                        f"PyPI lookup failed for {package_name}: HTTP {resp.status}"
                    )
                    return None

                data = await resp.json()
                version = data.get("info", {}).get("version")
                if version:
                    _version_cache[cache_key] = version
                return version

    except asyncio.TimeoutError:
        logger.debug(f"PyPI lookup timeout for {package_name}")
        return None
    except Exception as e:
        logger.debug(f"PyPI lookup error for {package_name}: {e}")
        return None


async def get_cargo_version(crate_name: str, timeout: float = 10.0) -> str | None:
    """
    Get the newest version of a crates.io package.

    Args:
        crate_name: The crate name (e.g., "mcp-server-git")
        timeout: Request timeout in seconds

    Returns:
        Version string (e.g., "1.2.3") or None if lookup fails
    """
    cache_key = f"cargo:{crate_name}"
    if cache_key in _version_cache:
        return _version_cache[cache_key]

    url = f"https://crates.io/api/v1/crates/{quote(crate_name, safe='')}"

    try:
        async with aiohttp.ClientSession(
            headers={"User-Agent": _USER_AGENT}
        ) as session:
            async with session.get(
                url, timeout=aiohttp.ClientTimeout(total=timeout)
            ) as resp:
                if resp.status != 200:
                    logger.debug(
                        f"crates.io lookup failed for {crate_name}: HTTP {resp.status}"
                    )
                    return None

                data = await resp.json()
                version = data.get("crate", {}).get("newest_version")
                if version:
                    _version_cache[cache_key] = version
                return version

    except asyncio.TimeoutError:
        logger.debug(f"crates.io lookup timeout for {crate_name}")
        return None
    except Exception as e:
        logger.debug(f"crates.io lookup error for {crate_name}: {e}")
        return None


async def get_docker_version(image_name: str, timeout: float = 10.0) -> str | None:
    """
    Get the digest of the latest tag for a Docker Hub image.

    Args:
        image_name: The image name without tag (e.g., "mcp/server" or "nginx")
        timeout: Request timeout in seconds

    Returns:
        Short digest string (e.g., "sha256:abc123") or None if lookup fails
    """
    cache_key = f"docker:{image_name}"
    if cache_key in _version_cache:
        return _version_cache[cache_key]

    # Official images have no slash; namespaced images have org/name
    if "/" in image_name:
        repo_path = image_name
    else:
        repo_path = f"library/{image_name}"

    # Keep "/" unescaped: org/name is a two-segment path on Docker Hub.
    url = f"https://hub.docker.com/v2/repositories/{quote(repo_path, safe='/')}/tags/latest"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url, timeout=aiohttp.ClientTimeout(total=timeout)
            ) as resp:
                if resp.status != 200:
                    logger.debug(
                        f"Docker Hub lookup failed for {image_name}: HTTP {resp.status}"
                    )
                    return None

                data = await resp.json()
                digest = data.get("digest") or data.get("id")
                if digest:
                    # Truncate sha256: digests to a short form for display
                    short = str(digest)
                    if short.startswith("sha256:"):
                        short = short[7:19]  # first 12 hex chars
                    _version_cache[cache_key] = short
                    return short
                return None

    except asyncio.TimeoutError:
        logger.debug(f"Docker Hub lookup timeout for {image_name}")
        return None
    except Exception as e:
        logger.debug(f"Docker Hub lookup error for {image_name}: {e}")
        return None


def detect_package_type(
    command: str, args: list[str]
) -> tuple[Literal["npm", "pypi", "cargo", "docker", "unknown"], str | None]:
    """
    Detect package type and name from server command/args.

    Args:
        command: The server command (e.g., "npx", "uvx")
        args: Command arguments

    Returns:
        Tuple of (package_type, package_name) or ("unknown", None)
    """
    if command in ("npx", "npm"):
        # Find npm package in args (usually after -y flag)
        raw = _npm_package_arg(args)
        if raw is not None:
            return ("npm", _strip_npm_tag(raw))

    elif command == "uvx":
        # First non-flag argument is the package
        for arg in args:
            if not arg.startswith("-"):
                return ("pypi", arg)

    elif command in ("pip", "pip3"):
        # pip install {package} or pip install --upgrade {package}
        for arg in args:
            if arg in ("install", "upgrade", "update", "--upgrade", "-U"):
                continue
            if arg.startswith("-"):
                continue
            return ("pypi", arg)

    elif command == "cargo":
        # cargo run -p package OR cargo run --bin binary OR cargo install package
        i = 0
        while i < len(args):
            arg = args[i]
            if arg in ("-p", "--package", "--bin"):
                if i + 1 < len(args) and not args[i + 1].startswith("-"):
                    return ("cargo", args[i + 1])
                i += 2
                continue
            if arg in ("run", "install", "build", "test", "check"):
                i += 1
                continue
            if not arg.startswith("-"):
                return ("cargo", arg)
            i += 1

    elif command == "docker":
        raw = _docker_image_arg(args)
        if raw is not None:
            # Strip the tag: docker run [options] image[:tag] [cmd...]
            image = raw.split(":")[0]
            if image:
                return ("docker", image)

    return ("unknown", None)


def _docker_image_arg(args: list[str]) -> str | None:
    """Return the raw ``docker`` image token from *args*, or ``None``.

    "Raw" means with any ``:tag`` suffix still attached -- factored out of
    ``detect_package_type``'s docker branch for the same reason
    ``_npm_package_arg`` was: gateway.update_server's pin detection needs the
    untouched token, and re-implementing this scan separately would risk the
    two disagreeing about which argument is "the image".
    """
    _value_flags = {
        "-e",
        "--env",
        "-v",
        "--volume",
        "-p",
        "--publish",
        "--name",
        "--network",
        "-u",
        "--user",
        "--entrypoint",
        "-w",
        "--workdir",
        "--label",
        "-l",
        "--memory",
        "-m",
        "--cpus",
        "--add-host",
        "--dns",
        "--hostname",
        "-h",
    }
    skip_next = False
    for arg in args:
        if skip_next:
            skip_next = False
            continue
        if arg in ("run", "exec", "start", "create", "pull", "push"):
            continue
        if arg in _value_flags:
            skip_next = True
            continue
        if arg.startswith("-"):
            continue
        # First positional arg after the subcommand is the image reference.
        return arg
    return None


def _docker_image_tag(image_ref: str) -> str | None:
    """Return the ``:tag`` on a docker image reference, or ``None``.

    Only the final path segment can carry a tag, so a registry host with a
    port (``registry:5000/img:1.2.3``) does not read as a tag of ``5000``.
    """
    last_segment = image_ref.rsplit("/", 1)[-1]
    name, sep, tag = last_segment.partition(":")
    return tag if sep and name and tag else None


async def get_package_version(
    command: str, args: list[str], timeout: float = 10.0
) -> tuple[str | None, Literal["npm", "pypi", "cargo", "docker", "unknown"]]:
    """
    Get the latest version for a package based on its command type.

    Args:
        command: The server command (e.g., "npx", "uvx", "cargo", "docker")
        args: Command arguments
        timeout: Request timeout

    Returns:
        Tuple of (version, package_type)
    """
    pkg_type, pkg_name = detect_package_type(command, args)

    if pkg_type == "npm" and pkg_name:
        version = await get_npm_version(pkg_name, timeout)
        return (version, "npm")
    elif pkg_type == "pypi" and pkg_name:
        version = await get_pypi_version(pkg_name, timeout)
        return (version, "pypi")
    elif pkg_type == "cargo" and pkg_name:
        version = await get_cargo_version(pkg_name, timeout)
        return (version, "cargo")
    elif pkg_type == "docker" and pkg_name:
        version = await get_docker_version(pkg_name, timeout)
        return (version, "docker")

    return (None, "unknown")


# A version must START with a numeric release component -- `1`, `1.0`, `1.2.3`,
# `2025.12.18` -- optionally `v`-prefixed, optionally followed by a pre-release or
# build suffix (`-rc1`, `+build.5`). Anything that merely CONTAINS a digit
# (`build-1`, `release-channel-a`) is not a version and must not be ordered:
# `build-1` parses to `(1,)` under a digits-anywhere scan and compares as older
# than every real release, which is exactly the fabricated-notice bug.
_VERSION_RE = re.compile(r"^[vV]?\d+(?:\.\d+)*(?:[-+.].*)?$")


def _parse_version_parts(value: str) -> tuple[int, ...]:
    """Numeric components of a version-shaped string, in order.

    Returns ``()`` when *value* is not version-shaped, which callers treat as
    "cannot be ordered" rather than "equal to zero".
    """
    candidate = (value or "").strip()
    if not _VERSION_RE.match(candidate):
        return ()
    stripped = re.sub(r"^[vV]", "", candidate)
    return tuple(int(part) for part in re.findall(r"\d+", stripped))


def _is_digest(value: str) -> bool:
    """Whether *value* is a content digest rather than a version.

    ``get_docker_version`` returns a digest (``sha256:...``), which is an
    identity, not an ordinal -- "newer" is only ever "different".
    """
    return bool(value) and value.startswith("sha256:")


def is_version_newer(current: str, latest: str) -> bool:
    """Whether *latest* is a newer release than *current*.

    Handles semver (``1.2.3``), 2-part (``1.0``), date-based (``2025.12.18``),
    a ``v`` prefix, and trailing pre-release markers (``1.0.0-rc1``).

    FAILS CLOSED. Returning ``True`` makes the gateway tell an operator their
    server is out of date, so anything this function cannot actually order must
    return ``False``. Previously it extracted digits from whatever it was given
    and compared them with no way to signal "unreadable", so a server reporting
    ``nightly``, ``build-1`` or ``""`` compared as OLDER than any real version
    and produced a fabricated update notice (Consiliency/pmcp#150 board review).
    Those cases now report no update.

    Specifically:

    * either side unparseable (no digits at all) -> ``False``. ``""`` is the
      mcp 2.x default ``serverInfo.version``, so this case is reached in practice.
    * digests (``sha256:...``) are compared for INEQUALITY, not order -- a
      different digest means a new image, and ordering hex is meaningless.
      Without this, the docker lane would be silenced entirely.
    * mixed digest/version -> ``False``; they are not comparable.
    """
    if current == latest:
        return False

    if _is_digest(current) or _is_digest(latest):
        # Comparable only when BOTH are digests; then any difference is an update.
        return _is_digest(current) and _is_digest(latest)

    current_parts = _parse_version_parts(current)
    latest_parts = _parse_version_parts(latest)

    # No orderable content on either side: refuse to guess rather than
    # fabricate an update notice.
    if not current_parts or not latest_parts:
        return False

    return latest_parts > current_parts


def clear_version_cache() -> None:
    """Clear the version cache (useful for testing)."""
    _version_cache.clear()
