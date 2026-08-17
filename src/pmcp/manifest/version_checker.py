"""Package version checking for npm, PyPI, crates.io, and Docker Hub packages."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Literal
from urllib.parse import quote

import aiohttp
from packaging.version import InvalidVersion, Version

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


# `get_docker_version` returns a BARE hex digest -- it strips the `sha256:`
# prefix and truncates to 12 chars (see its implementation, pinned by
# TestGetDockerVersion). Matching on a `sha256:` prefix therefore never fires
# against the real producer; an earlier version of this guard did exactly that
# and silenced every docker update notice. Accept the bare form, and the
# prefixed form for robustness if the producer ever stops truncating.
# The `[a-f]` lookahead is load-bearing: without it a long all-numeric version
# (`202612180000`, a plausible calendar/build stamp) matches as a "digest" and
# is then never compared against a dotted release, silently dropping a real
# update. A digest in practice always contains at least one hex letter; a purely
# numeric string is a version, so let `packaging` order it.
_DIGEST_RE = re.compile(r"^(?:sha256:)?(?=[0-9a-f]*[a-f])[0-9a-f]{12,64}$")


def _is_digest(value: str) -> bool:
    """Whether *value* is a content digest rather than an orderable version.

    A digest is an identity: "newer" can only mean "different". Ordering hex is
    meaningless, so digests are compared for inequality instead.
    """
    return bool(value) and bool(_DIGEST_RE.match(value.strip()))


def _parse_version(value: str) -> Version | None:
    """Parse *value* as a release version, or ``None`` if it is not one.

    Uses ``packaging.version``, which implements PEP 440 ordering (including
    pre-release, post-release and dev precedence) and raises on anything that
    is not a version. Hand-rolled digit extraction was tried three times and
    produced a fabricated-notice bug every time: it silently turned `build-1`
    into `(1,)`, `2026-08-17-nightly` into `(2026, 8, 17)`, and `""` into `()`,
    each of which compared as OLDER than a real release.

    A leading ``v`` is accepted (``v1.2.3``) since registries commonly emit it.
    """
    candidate = (value or "").strip()
    if not candidate:
        return None
    candidate = re.sub(r"^[vV]", "", candidate)
    try:
        return Version(candidate)
    except InvalidVersion:
        return None


def is_version_newer(current: str, latest: str) -> bool:
    """Whether *latest* is a strictly newer release than *current*.

    FAILS CLOSED. Returning ``True`` tells an operator their server is out of
    date, so anything this function cannot actually order returns ``False``
    (Consiliency/pmcp#150 board review). An unreadable version produces no
    notice rather than a false one.

    * Both sides parse as versions -> PEP 440 ordering, so `1.0.0-rc1` is
      correctly OLDER than `1.0.0`, and build metadata is not precedence.
    * Both sides are digests -> any difference is a new image. This is the
      docker lane: `get_docker_version` returns a bare 12-hex digest.
    * Anything else -- unparseable, empty (the mcp 2.x default
      ``serverInfo.version``), or a digest compared against a version -> no
      update.
    """
    if current == latest:
        return False

    current_is_digest = _is_digest(current)
    latest_is_digest = _is_digest(latest)
    if current_is_digest or latest_is_digest:
        # Comparable only when BOTH are digests; then any difference is an update.
        # A digest and a version describe different things and are not ordered.
        return current_is_digest and latest_is_digest

    current_version = _parse_version(current)
    latest_version = _parse_version(latest)
    if current_version is None or latest_version is None:
        return False

    # Compare on the PUBLIC segment: a local/build-metadata difference
    # (`1.0.0+build.4` -> `+build.5`) is not a new release, and announcing one
    # would be the same fabricated notice this function exists to prevent.
    # Pre-release precedence is retained, since `.public` keeps it.
    return Version(latest_version.public) > Version(current_version.public)


def clear_version_cache() -> None:
    """Clear the version cache (useful for testing)."""
    _version_cache.clear()
