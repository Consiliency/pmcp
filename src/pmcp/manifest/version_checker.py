"""Package version checking for npm, PyPI, crates.io, and Docker Hub packages."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Literal
from urllib.parse import quote

import aiohttp
from packaging.version import InvalidVersion, Version
from semver import Version as SemverVersion

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


# npm subcommands whose operand IS a registry package name -- an ALLOWLIST.
#
# This is deliberately an allowlist and not a denylist of script-running
# subcommands, because the consequence of being wrong is asymmetric.
# gateway.update_server builds its probe from whatever name comes back --
# `npx -y {name}@latest --help` -- and `npx -y` INSTALLS WITHOUT PROMPTING
# (Consiliency/pmcp#183). So an unrecognised subcommand that falls through as
# a package name gets fetched and executed from the public registry.
#
# A denylist shipped first and was wrong for exactly that reason (ah board
# review, red-team seat): with only `run` and `create` denied, `npm start`,
# `npm test`, `npm stop`, `npm restart`, `npm run-script mcp` and `npm init foo`
# all still resolved to packages named after the SUBCOMMAND -- and so did
# typos like `npm rum mcp`. Every npm subcommand that is not listed below, and
# every misspelling of one, would have been installed and run.
#
# Failing closed costs only the ability to auto-update a server launched by an
# unusual form; failing open costs arbitrary package execution. Adding a
# subcommand here is a deliberate act that says "the token after this really is
# a registry package".
_NPM_SUBCOMMANDS_WITH_A_PACKAGE_OPERAND = frozenset(
    {"exec", "x", "install", "i", "add", "dlx"}
)

# Retained under its historical name for the skip logic below: the operand of a
# subcommand NOT in the allowlist is not a package, so identity is
# unrecoverable and `("unknown", None)` is the honest answer -- the same rule
# the identity gate follows, cannot confirm -> do not act on a guess.
# `npm create foo` is a good illustration of why synthesising is not safe
# either: npm resolves it to the package `create-foo`, so `foo` names a
# DIFFERENT package than the one npm would run.
_NPM_SUBCOMMANDS = _NPM_SUBCOMMANDS_WITH_A_PACKAGE_OPERAND


# ---------------------------------------------------------------------------
# Flag classification (Consiliency/pmcp#182)
# ---------------------------------------------------------------------------
#
# The bug this closes: the scans below skipped *flags* but not the *values
# those flags carry*, so the first "non-flag" token was routinely a flag's
# argument. `uvx --python 3.12 pkg-a` and `uvx --python 3.12 pkg-b` both
# resolved to `("pypi", "3.12")`, and 2.4.0's identity gate reads an equal name
# as a POSITIVE confirmation -- serving pkg-a's cached tool descriptions for
# pkg-b indefinitely.
#
# Every flag is classified into exactly one of three kinds, and **anything
# unlisted defaults to refusing**:
#
#   value    -- consumes the next token; that token is NOT the package
#   boolean  -- takes nothing; the next token is still a candidate
#   positive -- its value IS the package (`uvx --from`, `cargo -p`)
#   unlisted -- `("unknown", None)`
#
# **Why this is not the denylist Consiliency/pmcp#183 rejected.** Docker's
# `_value_flags` table below has the same shape, and it failed OPEN: the
# default for an unlisted flag was "skip it and take the next token as the
# image", so an omission silently produced a WRONG identity -- which is exactly
# how `--env-file` and `--mount` became two of the seven collisions. Inverting
# the default is the entire point. An omission now costs auto-update for one
# odd config (safe, loud, fixable by adding an entry) instead of a silent
# collision (unsafe, invisible).
#
# **The honest residual: a WRONG entry still fails open.** Classify a flag as
# boolean when it actually takes a value, and that value becomes the package
# name. `docker run --pull <policy>` is the live example -- it reads like a
# boolean and is not.
#
# So every entry below was transcribed from the tool's own `--help` and then
# checked back against it MECHANICALLY, one entry at a time (296 entries; the
# script lives in the #182 working notes, not in the suite, because help text
# is version-specific and CI has none of these CLIs). Drafting from memory got
# `--pull`, uv's `--quiet`/`--verbose` (repeatable counters, printed as
# `-q, --quiet...`) and pip's `--platform` wrong, which is why the claim is
# mechanical rather than asserted.
#
# Three entries are exempt, each for a stated reason: `-it`/`-ti` are combined
# short booleans docker only ever documents separately, and npm prints a bare
# `--package` in its option list with the metavar only in its usage block.
# **Ten entries that could not be verified were REMOVED rather than kept** --
# removal is fail-closed, so it costs auto-update for an unusual config and
# never correctness. Among them, pip's `--break-system-packages` and
# `--dry-run` are real modern-pip booleans absent from the pip 22.0.2 used to
# verify; re-add them against a newer pip rather than from memory.
#
# The tables are deliberately kept SMALL -- limited to what real MCP launch
# configs use -- because fail-closed makes an omission cheap while every extra
# entry is one more chance at a wrong arity.

# uv (`uvx` == `uv tool run`), verified against `uv tool run --help`.
_UVX_VALUE_FLAGS = frozenset(
    {
        "--python",
        "-p",
        "--with",
        "--with-editable",
        "--with-requirements",
        "--index",
        "--index-url",
        "-i",
        "--extra-index-url",
        "--default-index",
        "--find-links",
        "-f",
        "--constraints",
        "-c",
        "--overrides",
        "--build-constraints",
        "-b",
        "--config-setting",
        "-C",
        "--exclude-newer",
        "--refresh-package",
        "--reinstall-package",
        "--upgrade-package",
        "-P",
        "--no-binary-package",
        "--no-build-package",
        "--resolution",
        "--prerelease",
        "--index-strategy",
        "--fork-strategy",
        "--keyring-provider",
        "--link-mode",
        "--python-platform",
        "--allow-insecure-host",
        "--cache-dir",
        "--config-file",
        "--directory",
        "--project",
        "--env-file",
        "--color",
    }
)
_UVX_BOOLEAN_FLAGS = frozenset(
    {
        # `-q, --quiet...` / `-v, --verbose...` -- the trailing `...` marks a
        # repeatable COUNTER, not a value. Reading them as value flags would
        # eat the package in `uvx --quiet my-package`, which is a documented
        # form and a pinned test.
        "--quiet",
        "-q",
        "--verbose",
        "-v",
        "--isolated",
        "--no-cache",
        "-n",
        "--offline",
        "--refresh",
        "--reinstall",
        "--upgrade",
        "-U",
        "--no-config",
        "--no-index",
        "--no-progress",
        "--no-sources",
        "--no-binary",
        "--no-build",
        "--no-build-isolation",
        "--compile-bytecode",
        "--managed-python",
        "--no-managed-python",
        "--no-python-downloads",
        "--system-certs",
        "--no-env-file",
        "--help",
        "-h",
    }
)
# `--from` names the package to install when it differs from the command being
# run -- so its value IS the identity.
_UVX_POSITIVE_FLAGS = frozenset({"--from"})

# pip, verified against `pip install --help`.
_PIP_VALUE_FLAGS = frozenset(
    {
        "--index-url",
        "-i",
        "--extra-index-url",
        "--find-links",
        "-f",
        "--constraint",
        "-c",
        "--requirement",
        "-r",
        "--editable",
        "-e",
        "--target",
        "-t",
        "--prefix",
        "--root",
        "--src",
        # These four read like booleans and are not (`--platform <platform>`).
        "--platform",
        "--python-version",
        "--implementation",
        "--abi",
        "--proxy",
        "--cert",
        "--client-cert",
        "--trusted-host",
        "--log",
        "--cache-dir",
        "--timeout",
        "--retries",
        "--progress-bar",
        "--exists-action",
        "--upgrade-strategy",
        "--no-binary",
        "--only-binary",
        "--global-option",
        "--install-option",
        "--use-feature",
        "--use-deprecated",
    }
)
_PIP_BOOLEAN_FLAGS = frozenset(
    {
        "--upgrade",
        "-U",
        "--user",
        "--pre",
        "--no-deps",
        "--force-reinstall",
        "--ignore-installed",
        "--ignore-requires-python",
        "--no-cache-dir",
        "--no-index",
        "--no-build-isolation",
        "--use-pep517",
        "--compile",
        "--no-compile",
        "--no-warn-script-location",
        "--no-warn-conflicts",
        "--require-hashes",
        "--require-virtualenv",
        "--isolated",
        "--no-clean",
        "--prefer-binary",
        "--no-input",
        "--no-color",
        "--disable-pip-version-check",
        "--no-python-version-warning",
        "--quiet",
        "-q",
        "--verbose",
        "-v",
        "--debug",
        "--help",
        "-h",
        "--version",
        "-V",
    }
)
_PIP_SUBCOMMANDS = frozenset({"install", "upgrade", "update"})

# cargo, verified against `cargo run --help` and `cargo install --help`.
_CARGO_VALUE_FLAGS = frozenset(
    {
        "--features",
        "-F",
        "--target",
        "--target-dir",
        "--manifest-path",
        "--profile",
        "--jobs",
        "-j",
        "--message-format",
        "--color",
        "--config",
        "-Z",
        "--example",
        "--git",
        "--branch",
        "--tag",
        "--rev",
        "--path",
        "--root",
        "--registry",
        "--index",
        # `cargo install --version <ver>` -- a pin, never the crate name.
        "--version",
    }
)
_CARGO_BOOLEAN_FLAGS = frozenset(
    {
        "--release",
        "-r",
        "--all-features",
        "--no-default-features",
        "--offline",
        "--locked",
        "--frozen",
        "--timings",
        "--keep-going",
        "--ignore-rust-version",
        "--unit-graph",
        "--bins",
        "--examples",
        "--force",
        "-f",
        "--debug",
        "--dry-run",
        "-n",
        "--list",
        "--no-track",
        "--quiet",
        "-q",
        "--verbose",
        "-v",
        "--help",
        "-h",
    }
)
# `-p`/`--package`/`--bin` all name the thing being built or run.
_CARGO_POSITIVE_FLAGS = frozenset({"-p", "--package", "--bin"})
_CARGO_SUBCOMMANDS = frozenset({"run", "install", "build", "test", "check"})

# npm. `--package` is the ONLY spelling: verified against `npm exec --help`,
# whose options line reads `[--package <package-spec> ...]`. There is no `-p`
# alias -- in npm `-p` means `--parseable` -- so listing one would be a WRONG
# entry, which is the failure direction that stays open.
_NPM_POSITIVE_FLAGS = frozenset({"--package"})

# docker, verified against `docker run --help`.
_DOCKER_BOOLEAN_FLAGS = frozenset(
    {
        "-i",
        "--interactive",
        "-t",
        "--tty",
        "-d",
        "--detach",
        # Combined short booleans. docker documents only `-i` and `-t`
        # separately, so no table derived from `--help` can ever contain these
        # -- they must be listed by hand. `docker run -it --rm <image>` is the
        # canonical MCP shape and this repo's own README uses it (`:1528`);
        # a design that refused it broke essentially every real docker server.
        "-it",
        "-ti",
        "--rm",
        "--init",
        "--privileged",
        "--read-only",
        "--publish-all",
        "-P",
        "--sig-proxy",
        "--no-healthcheck",
        "--oom-kill-disable",
        "--use-api-socket",
        "--quiet",
        "-q",
        "--help",
    }
)
_DOCKER_SUBCOMMANDS = frozenset({"run", "exec", "start", "create", "pull", "push"})

_DOCKER_VALUE_FLAGS = frozenset(
    {
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
        # The two omissions #182 measured.
        "--env-file",
        "--mount",
        # `--pull <policy>` reads like a boolean and is NOT one -- the
        # live example of the residual hazard that a WRONG entry still
        # fails open, so its value would become the image name.
        "--pull",
        "--device",
        "--tmpfs",
        "--sysctl",
        "--ulimit",
        "--cap-add",
        "--cap-drop",
        "--security-opt",
        "--restart",
        "--platform",
        "--gpus",
        "--runtime",
        "--isolation",
        "--log-driver",
        "--log-opt",
        "--label-file",
        "--health-cmd",
        "--health-interval",
        "--health-retries",
        "--health-start-period",
        "--health-timeout",
        "--shm-size",
        "--pid",
        "--ipc",
        "--userns",
        "--uts",
        "--cgroupns",
        "--cgroup-parent",
        "--stop-signal",
        "--stop-timeout",
        "--volumes-from",
        "--volume-driver",
        "--link",
        "--expose",
        "--group-add",
        "--cidfile",
        "--detach-keys",
        "--annotation",
        "--domainname",
        "--mac-address",
        "--ip",
        "--ip6",
        "--dns-option",
        "--dns-search",
        "--network-alias",
        "--memory-reservation",
        "--memory-swap",
        "--memory-swappiness",
        "--oom-score-adj",
        "--pids-limit",
        "--storage-opt",
        "--blkio-weight",
        "--cpu-shares",
        "-c",
        "--cpu-period",
        "--cpu-quota",
        "--cpuset-cpus",
        "--cpuset-mems",
        "--attach",
        "-a",
    }
)


def _takes_a_value_ambiguously(arg: str) -> bool:
    """Whether *arg* is a flag that might silently swallow the next token.

    One home for the fail-closed rule. True for a token that starts with ``-``
    and is **not** in the ``--flag=value`` form -- meaning its arity cannot be
    read off the token itself, so the following token may be its value or may
    be the package, and nothing here can tell which.

    A ``--flag=value`` spelling is *self-delimiting*: the value rides inside
    the token, so an unrecognised one cannot consume anything and is safe to
    skip. Refusing on it would cost auto-update for no safety gain at all.

    Callers apply this only **after** consulting their ecosystem's three
    tables; a classified flag is never ambiguous by definition.
    """
    return arg.startswith("-") and arg != "-" and "=" not in arg


def _scan_for_package_token(
    args: list[str],
    *,
    value_flags: frozenset[str] = frozenset(),
    boolean_flags: frozenset[str] = frozenset(),
    positive_flags: frozenset[str] = frozenset(),
    subcommands: frozenset[str] = frozenset(),
) -> tuple[str | None, bool]:
    """Left-to-right scan for the package token in *args*.

    Returns ``(token, came_from_a_positive_flag)``. A ``None`` token means the
    caller must report ``("unknown", None)`` -- either nothing was found or
    something unclassifiable was hit.

    **Left-to-right, deliberately.** An earlier draft scanned the whole of
    argv for ``--from`` to rescue the README's documented pin form. Measured,
    that turns ``uvx mypkg --from other`` from ``mypkg`` into ``other`` -- a
    fail-open misidentification *introduced by the fix*, which would re-collide
    ``uvx a --from x`` with ``uvx b --from x`` through the new path. With
    ``--python`` classified as a value flag the plain scan resolves the README
    form anyway, so no such hunt is needed.

    The scan stops at ``--``: everything after it belongs to the tool being
    run, not to the runner, and a served tool's own ``--from`` argument must
    never be mistaken for the runner's package identity.
    """
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--":
            # End of the runner's own arguments: everything after belongs to
            # the tool being run.
            #
            # Measured as REDUNDANT rather than load-bearing, and kept
            # deliberately. `--` also satisfies `_takes_a_value_ambiguously`
            # below, so removing this branch changes no observable result --
            # no test can distinguish the two, and none pretends to. It stays
            # because it states the boundary explicitly at the point where a
            # reader looks for it, and because it keeps the boundary correct
            # if `--` is ever added to one of the tables above.
            return (None, False)
        if arg.startswith("-") and arg != "-":
            name, separator, attached = arg.partition("=")
            if separator:
                if name in positive_flags:
                    return (attached or None, True)
                # Ask the predicate rather than re-deciding here, so the
                # fail-closed rule has exactly ONE home. A `--flag=value`
                # spelling is self-delimiting -- it cannot swallow the next
                # token -- so the predicate returns False and the token is
                # skipped whether or not it is a flag we know.
                if _takes_a_value_ambiguously(arg):
                    return (None, False)
                index += 1
                continue
            if arg in positive_flags:
                following = args[index + 1] if index + 1 < len(args) else None
                # A positive flag with nothing usable after it names no
                # package; guessing at the token after a missing value is how
                # a flag's value became an identity in the first place.
                if following is None or following.startswith("-"):
                    return (None, False)
                return (following, True)
            if arg in value_flags:
                index += 2
                continue
            if arg in boolean_flags:
                index += 1
                continue
            if _takes_a_value_ambiguously(arg):
                return (None, False)
            index += 1
            continue
        if arg in subcommands:
            index += 1
            continue
        return (arg, False)
    return (None, False)


# A PEP 508 requirement ends its NAME at the first character that can begin an
# extras group, a version specifier, an environment marker, or a URL.
_PEP508_NAME = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:[\[<>=!~;,(].*)?$")


def _pep508_base_name(requirement: str) -> str:
    """The bare package name inside a PEP 508 *requirement*.

    ``browser-use[cli]`` -> ``browser-use``; ``index-it-mcp==1.2.0`` ->
    ``index-it-mcp``; ``pkg>=1.0`` -> ``pkg``.

    Applied only to ``--from`` values, which are requirements rather than bare
    names. This **repairs a live defect** rather than introducing a behaviour
    change: `manifest.yaml` ships ``--from browser-use[cli]``, and a PyPI
    lookup for ``browser-use[cli]`` returns None while ``browser-use`` returns
    a real version -- so that first-party entry's version checks have been
    silently failing.

    Anything that is not a plain requirement name -- most importantly a
    ``git+https://...`` URL -- is returned **unchanged**. A VCS URL is its own
    identity: there is no name to extract, distinct URLs are distinct
    packages so the gate stays correct, and the PyPI lookup fails closed to
    None, which the gate already reads as "cannot confirm -> refresh".

    ``@`` is deliberately NOT a name terminator, unlike the other PEP 508
    separators. A *direct reference* (``pkg @ git+https://x/y``) names a
    specific source, so truncating at ``@`` would return ``pkg`` for BOTH
    ``pkg @ git+https://x/y`` and ``pkg @ .../z`` -- collapsing two different
    repositories into one identity, which is the exact defect class this
    change exists to close, newly introduced by the fix for it (ah board
    review, red-team seat). A direct reference is returned whole and is its
    own identity, exactly like a bare URL.
    """
    match = _PEP508_NAME.match(requirement.strip())
    return match.group(1) if match else requirement


def _uvx_package_arg(args: list[str]) -> tuple[str | None, bool]:
    """Return ``(raw uvx package token, came_from_--from)``.

    "Raw" means before ``_pep508_base_name`` normalisation -- shared between
    ``detect_package_type`` and gateway.update_server's pin detection for
    exactly the reason ``_npm_package_arg`` is shared: two independent scans
    disagreeing about which argument is "the package" is how a real pin got
    missed. Before this existed, pin detection skipped every ``-``-prefixed
    token and read ``==`` off the first bare one, so
    ``--from=index-it-mcp==1.2.0`` reported **no pin** (identity resolved
    fine, so update_server would have probed for latest and updated a server
    the operator had explicitly pinned), while ``--with requests==2.0 pkg``
    reported an injected dependency's version as the server's own pin.
    """
    return _scan_for_package_token(
        args,
        value_flags=_UVX_VALUE_FLAGS,
        boolean_flags=_UVX_BOOLEAN_FLAGS,
        positive_flags=_UVX_POSITIVE_FLAGS,
    )


def _npm_package_arg(args: list[str], command: str) -> str | None:
    """Return the raw npm/npx package token from *args*, or ``None``.

    "Raw" means *before* ``_strip_npm_tag`` removes any ``@tag``/``@version``
    suffix -- factored out of ``detect_package_type``'s npm branch so a
    caller that needs the untouched token (gateway.update_server's pin
    detection) can get it from the exact same scan ``detect_package_type``
    itself uses, instead of re-implementing the scan a second time and
    risking it picking a different argument as "the package".

    *command* is REQUIRED, and deliberately not defaulted. ``npm`` takes a
    subcommand before the package (``npm exec pkg``) while ``npx`` takes the
    package directly (``npx pkg``), so the scan cannot be correct for both
    without knowing which it is reading. A default would let one of the two
    call sites keep the old behaviour silently while type-checking clean --
    the exact drift this function's shared-scan design exists to prevent.

    The subcommand skip fires **once, on the first non-flag token, for ``npm``
    only**:

    * Once, not repeatedly, because ``i`` and ``exec`` are also real package
      names: a loop that skipped subcommand tokens anywhere -- the shape the
      docker branch uses for its own subcommands -- would turn
      ``npm install i`` and ``npm exec exec`` into ``None``.
    * ``npm`` only, because ``npx -y exec`` genuinely names a package called
      ``exec``.

    ``--package`` is read as **known-positive** (Consiliency/pmcp#182): its
    value IS the package, so it is extracted rather than skipped. Before that,
    ``npm exec --package=<pkg> -- <bin>`` returned the BINARY, and two
    different packages exposing the same binary name confirmed as one
    identity. Note that merely treating ``--package=old`` as "self-delimiting,
    so keep scanning" does not fix it -- the scan still reaches ``<bin>``; the
    value has to be read back out of the flag.

    Known limitation, deliberately left open: unlike the other four
    ecosystems this scan does **not** fail closed on an unrecognised flag, so
    ``npm exec --loglevel silly <pkg>`` still reads ``silly`` as the package.
    Inverting the default here would break the pinned ordering below, where a
    leading global flag such as ``npm --silent exec <pkg>`` must be skipped
    rather than refused. Tracked on Consiliency/pmcp#180, which stays OPEN
    for this residual -- NOT on #182, which the change carrying this note
    closes on merge and would leave this pointer dangling.
    """
    skip_subcommand = command == "npm"
    packages: list[str] = []
    for index, arg in enumerate(args):
        if arg == "-y":
            continue
        name, separator, attached = arg.partition("=")
        if separator and name in _NPM_POSITIVE_FLAGS:
            if attached:
                packages.append(attached)
            continue
        if arg in _NPM_POSITIVE_FLAGS:
            following = args[index + 1] if index + 1 < len(args) else None
            if following is not None and not following.startswith("-"):
                packages.append(following)
            continue
        if packages:
            # A `--package` value outranks any later positional, which is the
            # command npm runs FROM that package, not a package itself.
            continue
        # Skip flags. This MUST precede the subcommand check: npm accepts
        # global flags before the subcommand (`npm --silent exec pkg`), so
        # spending the one-shot skip on a flag token would leave `exec` to be
        # read as the package and reopen the #180 collapse for every form
        # carrying a leading flag. The ordering is already correct; the test
        # below exists because nothing PINNED it (ah board review, adversarial
        # seat: a surviving mutant, not a live defect).
        if arg.startswith("-"):
            continue
        if skip_subcommand:
            # Only the first non-flag token can be the subcommand; whatever
            # follows is a candidate package even if it repeats the word.
            skip_subcommand = False
            if arg in _NPM_SUBCOMMANDS_WITH_A_PACKAGE_OPERAND:
                continue
            # ANY other subcommand -- `run`, `start`, `test`, `init`,
            # `run-script`, or a typo like `rum` -- does not put a registry
            # package in the next position, so there is no identity to
            # recover. Fail CLOSED (None -> "unknown") rather than handing
            # back a token that gateway.update_server would install and
            # execute via `npx -y` (Consiliency/pmcp#183).
            #
            # Note this refuses the subcommand form even when the FIRST token
            # is itself a plausible package name: `npm somepkg` is not a legal
            # npm invocation anyway (npm requires a subcommand), so nothing
            # real is lost.
            return None
        # Found package name (might have @version or @dist-tag suffix)
        pkg = _strip_npm_tag(arg)
        # Handle scoped packages like @playwright/mcp
        if pkg.startswith("@") or not pkg.startswith("-"):
            return arg
    if packages:
        # npm allows `--package` to be repeated. One package is an identity;
        # several DIFFERENT ones are not, and picking the first would be a
        # guess of exactly the kind this change exists to stop.
        return packages[0] if len(set(packages)) == 1 else None
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
        raw = _npm_package_arg(args, command)
        if raw is not None:
            return ("npm", _strip_npm_tag(raw))

    elif command == "uvx":
        raw, from_positive_flag = _uvx_package_arg(args)
        if raw is not None:
            # Only a `--from` value is a PEP 508 requirement; a bare positional
            # is left raw so `uvx pkg==1.2.3` keeps its inline pin in the name
            # and the existing "pinned server" refusal path still fires.
            return ("pypi", _pep508_base_name(raw) if from_positive_flag else raw)

    elif command in ("pip", "pip3"):
        raw, _ = _scan_for_package_token(
            args,
            value_flags=_PIP_VALUE_FLAGS,
            boolean_flags=_PIP_BOOLEAN_FLAGS,
            subcommands=_PIP_SUBCOMMANDS,
        )
        if raw is not None:
            return ("pypi", raw)

    elif command == "cargo":
        raw, _ = _scan_for_package_token(
            args,
            value_flags=_CARGO_VALUE_FLAGS,
            boolean_flags=_CARGO_BOOLEAN_FLAGS,
            positive_flags=_CARGO_POSITIVE_FLAGS,
            subcommands=_CARGO_SUBCOMMANDS,
        )
        if raw is not None:
            return ("cargo", raw)

    elif command == "docker":
        raw = _docker_image_arg(args)
        if raw is not None:
            # Strip tag and digest via the shared splitter, NOT `split(":")[0]`
            # -- that took the FIRST colon, so `registry:5000/img` read as the
            # image `registry` and two different images on the same host:port
            # registry confirmed as one package (Consiliency/pmcp#180).
            image = _docker_image_name(raw)
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

    The value table below was the most careful branch in this file and was
    **still incomplete twice**: ``--env-file`` and ``--mount`` were missing, so
    ``docker run --env-file .env <image>`` resolved to ``.env`` and any two
    images launched that way confirmed as one package
    (Consiliency/pmcp#182). Completing the table would not have closed the
    class -- what closes it is that an *unlisted* bare flag now refuses
    instead of falling through to "take the next token as the image".
    """
    raw, _ = _scan_for_package_token(
        args,
        value_flags=_DOCKER_VALUE_FLAGS,
        boolean_flags=_DOCKER_BOOLEAN_FLAGS,
        subcommands=_DOCKER_SUBCOMMANDS,
    )
    return raw


def _docker_image_tag(image_ref: str) -> str | None:
    """Return the ``:tag`` on a docker image reference, or ``None``.

    Only the final path segment can carry a tag, so a registry host with a
    port (``registry:5000/img:1.2.3``) does not read as a tag of ``5000``.

    A ``@digest`` suffix is stripped before the tag is read, so a digest is
    never reported as a tag. Without that, ``img@sha256:abc`` returned ``abc``
    and gateway.update_server's pin detection reported a *digest fragment as a
    version pin*, while ``img:1.2@sha256:abc`` returned ``1.2@sha256:abc``
    instead of ``1.2``. Both are fixed here rather than only in the name half,
    because ``_docker_image_name`` is this function's complement and the two
    must agree about where a reference divides -- an identity gate compares the
    name, so a divergence lets two different images confirm as one package.

    **A caller deciding whether a reference is PINNED must not read this
    function alone** -- ask ``_docker_image_digest`` too. A digest-only
    reference has no tag, so this correctly returns ``None``, and a caller
    treating ``None`` as "unpinned" would conclude that the most tightly
    pinned form docker has is not pinned at all (ah board review, red-team
    seat).
    """
    base = image_ref.partition("@")[0] or image_ref
    last_segment = base.rsplit("/", 1)[-1]
    name, sep, tag = last_segment.partition(":")
    return tag if sep and name and tag else None


def _docker_image_digest(image_ref: str) -> str | None:
    """Return the ``@digest`` on a docker image reference, or ``None``.

    The third member of the reference-splitting family, beside
    ``_docker_image_name`` and ``_docker_image_tag``. A digest is an immutable
    content identity -- the TIGHTEST pin docker offers, stronger than any tag --
    so a caller asking "is this reference pinned?" must consult this as well as
    the tag. Reading the tag alone reports a digest-only reference as unpinned,
    which would let gateway.update_server pull ``image:latest``, restart the
    unchanged digest-pinned config, and record the registry's newest digest as
    though it had updated -- announcing an update while still running the old
    immutable image (ah board review, red-team seat).
    """
    _name, sep, digest = image_ref.partition("@")
    return digest if sep and digest else None


def _docker_image_name(image_ref: str) -> str:
    """Return the image NAME from a docker reference, without tag or digest.

    The paired complement of ``_docker_image_tag`` -- same scan, same split
    point, opposite half -- in the exact sense ``_strip_npm_tag``/``_npm_tag``
    are paired. Written together so the two can never disagree about where a
    reference divides; ``detect_package_type`` reads the name from here and
    gateway.update_server's pin detection reads the tag from there, and an
    identity gate compares the name, so a divergence would let two different
    images confirm as the same package.

    Two rules, in order, and the order matters:

    * **Digest first.** ``@`` cannot appear in an OCI image name, so it is an
      unambiguous separator -- unlike ``:``. A ``name:tag@sha256:...`` reference
      contains three colons and the LAST one belongs to the digest, so any rule
      that reaches for the last colon splits in the wrong place. (An earlier
      draft of this fix did exactly that and regressed
      ``img:1.2@sha256:abc`` from ``img`` to ``img:1.2@sha256``.)
    * **Then the tag**, on the FIRST colon of the final path segment only --
      identical to ``_docker_image_tag``. This is what stops
      ``registry:5000/img`` reading as image ``registry``: before the last
      ``/`` a colon is a registry ``host:port``, not a tag separator.
    """
    base = image_ref.partition("@")[0] or image_ref
    prefix, sep, last_segment = base.rpartition("/")
    name, tag_sep, tag = last_segment.partition(":")
    if tag_sep and name and tag:
        last_segment = name
    return f"{prefix}{sep}{last_segment}"


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
# Shape alone cannot separate a digest from a version: `get_docker_version`
# truncates SHA-256 to 12 hex chars, which can be ALL NUMERIC (`987654321098`),
# while `202612180000` is a plausible calendar version. An earlier attempt
# required a hex letter and thereby dropped real all-numeric digest changes.
# So shape is only a candidate test -- `package_type` decides, and callers have
# it (board review round 4).
_DIGEST_RE = re.compile(r"^(?:sha256:)?[0-9a-f]{12,64}$")
# Without a package type, a BARE hex string needs a letter to be distinguishable
# from a calendar version -- but an explicit `sha256:` prefix already names the
# value a digest, so requiring a letter there rejected legitimate all-numeric
# digests (`sha256:987654321098`). Prefix => any hex; bare => letter required.
_DIGEST_LETTER_RE = re.compile(
    r"^(?:sha256:[0-9a-f]{12,64}|(?=[0-9a-f]*[a-f])[0-9a-f]{12,64})$"
)


def _digest_identity(value: str, package_type: str | None = None) -> str | None:
    """Canonical digest identity for *value*, or ``None`` if it is not a digest.

    Canonicalises so the SAME image never reads as an update just because the
    producer's representation changed: the `sha256:` prefix is dropped and the
    hex truncated to the 12 chars `get_docker_version` emits. Without this,
    `abcdef123456` vs `sha256:abcdef123456` -- one image, two spellings --
    compares as a new release.

    A digest is an identity, not an ordinal: "newer" can only mean "different".
    """
    candidate = (value or "").strip()
    # With a known docker package type, any hex-shaped value is a digest --
    # including an all-numeric truncation. Without one, require a hex letter so
    # a numeric calendar version is not misread as a digest and silently
    # excluded from ordering.
    pattern = _DIGEST_RE if package_type == "docker" else _DIGEST_LETTER_RE
    if not pattern.match(candidate):
        return None
    hex_part = candidate[7:] if candidate.startswith("sha256:") else candidate
    return hex_part[:12]


# A truncated SHA-256 can be all digits (`987654321098`), and so can a calendar
# version (`202612180000`). With `package_type == "docker"` the type settles it;
# without one, the shape alone cannot (Consiliency/pmcp#156 item 3).
#
# Two resolutions were tried and both REJECTED, so this records why the
# ambiguity is left in place rather than "fixed":
#
#   * Fail closed on any bare 12-digit string. Breaks CalVer, which #155
#     deliberately supports and pins with
#     test_long_numeric_version_is_not_mistaken_for_a_digest.
#   * Promote an all-numeric value to a digest when its PARTNER is
#     unmistakably one. Resolves the ambiguity by GUESSING, and the guess can
#     fabricate: a genuine CalVer paired with a digest then reports an update
#     that never happened, which is exactly what `compare_versions`'s
#     fail-closed contract exists to prevent (ah board review).
#
# So a mixed pair stays incomparable, per the documented contract. The
# ambiguity is unreachable while callers pass the package type -- both live
# callers in `refresher.py` do, and
# `test_all_compare_versions_callers_pass_package_type` keeps it that way.
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


# npm and Cargo publish SemVer, where `-anything` is a PRERELEASE ordered BELOW
# the release. PEP 440 (what `packaging` implements) reads `1.0.0-1` as the
# POST-release `1.0.0.post1` -- the opposite order. 79 of the manifest's 107
# servers are npm, so using PEP 440 for them inverts precedence on a real
# format: it hides `1.0.0-1 -> 1.0.0` and fabricates the reverse.
_SEMVER_ECOSYSTEMS = frozenset({"npm", "cargo"})


def _is_semver_ecosystem(package_type: str | None) -> bool:
    return package_type in _SEMVER_ECOSYSTEMS


def _semver_parse(value: str) -> SemverVersion | None:
    """Parse *value* as SemVer 2.0.0, or ``None`` if it is not SemVer.

    Delegated to the ``semver`` package rather than hand-written here
    (Consiliency/pmcp#156). This module replaced hand-rolled digit extraction
    with ``packaging`` precisely because hand-rolled version logic produced a
    fabricated-notice bug in every form it took, and then had to hand-roll the
    SemVer lane anyway. The library enforces the rules an earlier regex got
    wrong -- rule 2/9/10 rejection of leading zeros (`01.0.0`, `1.0.0-01`) and
    empty identifiers (`1.0.0-a..b`) -- and implements §11 precedence,
    including numeric-before-alphanumeric and `beta.2 < beta.11`, which naive
    string ordering reverses.

    A leading ``v`` is stripped first: registries commonly emit ``v1.2.3``,
    which strict SemVer does not accept.
    """
    candidate = (value or "").strip()
    if not candidate:
        return None
    candidate = re.sub(r"^[vV]", "", candidate)
    try:
        return SemverVersion.parse(candidate)
    except (ValueError, TypeError):
        return None


VersionComparison = Literal["newer", "not_newer", "incomparable"]


def compare_versions(
    current: str, latest: str, package_type: str | None = None
) -> VersionComparison:
    """Classify *latest* relative to *current*: the sole classification path.

    Consiliency/pmcp#164. This replaces the fail-closed boolean
    ``is_version_newer`` and its hand-mirrored pair predicate
    ``are_versions_comparable``. Those two answered "is X newer" and "can X
    and Y be ordered at all" as separate booleans, and a caller combining them
    as ``are_versions_comparable(...) and not is_version_newer(...)`` -- or
    worse, skipping the pair guard and just negating -- collapsed "up to date"
    and "cannot be ordered" into the same ``False``. That exact collapse
    shipped three times (#155, #156, #163) and survived a lint written to
    police it (bypassed four times), because a syntactic check cannot prove a
    dataflow property. A three-way `Literal` return makes the collapse
    unrepresentable instead of merely detectable: a caller has to name the
    branch it means.

    Returns ``"newer"``, ``"not_newer"``, or ``"incomparable"`` -- never a
    fourth value, and never a plain ``bool`` a caller could negate into
    ambiguity.

    * Both sides are digests -> identity, not an ordinal: any difference in
      the CANONICAL form is a new image, and the same image in two spellings
      (``abcdef123456`` vs ``sha256:abcdef123456``) is ``"not_newer"``. This is
      the docker lane: `get_docker_version` returns a bare 12-hex digest.
    * Exactly one side is a digest -> ``"incomparable"``: a digest and a
      release number describe different things.
    * Package type names a SemVer ecosystem (npm, cargo) -> SemVer precedence,
      so ``1.0.0-1`` is correctly a PRERELEASE below ``1.0.0`` (PEP 440 would
      read it as the opposite: a post-release). Either side failing to parse
      as SemVer -> ``"incomparable"``.
    * Otherwise -> PEP 440 ordering on the PUBLIC segment, so `1.0.0-rc1` is
      OLDER than `1.0.0` and build metadata (`1.0.0+build.4` -> `+build.5`) is
      not precedence. Either side failing to parse -> ``"incomparable"``,
      which covers the unreadable case (the literal ``"unknown"`` the
      refresher persists after a failed lookup) and the empty string mcp 2.x
      defaults ``serverInfo.version`` to.
    """
    current_digest = _digest_identity(current, package_type)
    latest_digest = _digest_identity(latest, package_type)
    if current_digest is not None or latest_digest is not None:
        # Comparable only when BOTH are digests; then any difference in the
        # CANONICAL identity is a new image. A digest and a version describe
        # different things and are not ordered.
        if current_digest is None or latest_digest is None:
            return "incomparable"
        return "newer" if current_digest != latest_digest else "not_newer"

    if _is_semver_ecosystem(package_type):
        current_semver = _semver_parse(current)
        latest_semver = _semver_parse(latest)
        if current_semver is None or latest_semver is None:
            return "incomparable"
        return "newer" if latest_semver > current_semver else "not_newer"

    current_version = _parse_version(current)
    latest_version = _parse_version(latest)
    if current_version is None or latest_version is None:
        return "incomparable"

    # Compare on the PUBLIC segment: a local/build-metadata difference
    # (`1.0.0+build.4` -> `+build.5`) is not a new release, and announcing one
    # would be the same fabricated notice this function exists to prevent.
    # Pre-release precedence is retained, since `.public` keeps it.
    if Version(latest_version.public) > Version(current_version.public):
        return "newer"
    return "not_newer"


def is_version_orderable(value: str, package_type: str | None = None) -> bool:
    """Whether *value* can be ordered at all -- a release version or a digest.

    NOT sufficient as a guard for a negated newer-check. That was its original
    purpose and it was wrong: comparability is a property of the PAIR, and two
    individually-orderable values can still be incomparable (a version against
    a digest). Use :func:`compare_versions` for that -- its ``"incomparable"``
    branch is a value, not a boolean a caller can accidentally negate.

    This remains useful for genuinely unary questions -- "is this single value
    a thing I could order at all".

    Delegates to :func:`compare_versions` rather than branching again. It used
    to re-derive the digest/SemVer/PEP-440 classification itself, which left
    two classification sites that could drift apart -- exactly the hazard the
    2.2.1 wrapper-drift corpus existed to police, reintroduced in a new place
    (ah board review). Comparing a value against ITSELF is orderable precisely
    when the value is: equal values are ``"not_newer"``, and an unreadable one
    is ``"incomparable"``. Verified equivalent to the previous implementation
    across 112 value/package-type combinations.
    """
    return compare_versions(value, value, package_type) != "incomparable"


def clear_version_cache() -> None:
    """Clear the version cache (useful for testing)."""
    _version_cache.clear()
