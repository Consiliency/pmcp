"""Tests for package version checking functionality."""

from __future__ import annotations

import ast
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pmcp.manifest.version_checker import (
    _digest_identity,
    _docker_image_name,
    _docker_image_tag,
    _parse_version,
    _semver_parse,
    _USER_AGENT,
    _version_cache,
    clear_version_cache,
    compare_versions,
    detect_package_type,
    get_cargo_version,
    get_docker_version,
    get_npm_version,
    get_package_version,
    get_pypi_version,
    is_version_orderable,
    VersionComparison,
)
from pmcp import __version__


class TestDetectPackageType:
    """Tests for detect_package_type function."""

    def test_npx_simple_package(self) -> None:
        """Test detection of simple npx package."""
        pkg_type, pkg_name = detect_package_type("npx", ["-y", "playwright-mcp"])
        assert pkg_type == "npm"
        assert pkg_name == "playwright-mcp"

    def test_npx_scoped_package(self) -> None:
        """Test detection of scoped npm package."""
        pkg_type, pkg_name = detect_package_type("npx", ["-y", "@playwright/mcp"])
        assert pkg_type == "npm"
        assert pkg_name == "@playwright/mcp"

    def test_npx_package_with_latest(self) -> None:
        """Test detection strips @latest suffix."""
        pkg_type, pkg_name = detect_package_type("npx", ["-y", "some-package@latest"])
        assert pkg_type == "npm"
        assert pkg_name == "some-package"

    @pytest.mark.parametrize(
        ("arg", "expected"),
        [
            ("some-package@beta", "some-package"),
            ("some-package@1.2.3", "some-package"),
            ("@org/pkg@beta", "@org/pkg"),
            ("@org/pkg@1.2.3", "@org/pkg"),
            ("@org/pkg", "@org/pkg"),
        ],
    )
    def test_npx_package_strips_arbitrary_tag_or_version(
        self, arg: str, expected: str
    ) -> None:
        pkg_type, pkg_name = detect_package_type("npx", ["-y", arg])
        assert pkg_type == "npm"
        assert pkg_name == expected

    def test_npx_without_y_flag(self) -> None:
        """Test detection works without -y flag."""
        pkg_type, pkg_name = detect_package_type("npx", ["my-mcp-server"])
        assert pkg_type == "npm"
        assert pkg_name == "my-mcp-server"

    def test_npm_command(self) -> None:
        """`npm` reads its package from an allowlisted subcommand's operand.

        This test previously asserted `npm -y server-pkg` -> `server-pkg`.
        That is not a legal npm invocation -- npm requires a subcommand -- and
        the assertion encoded the parser's old permissiveness: the first
        non-flag token was taken as a package whatever it was, which is the
        Consiliency/pmcp#183 hazard (`npm run mcp` -> package `mcp`, then
        installed and executed via `npx -y`). A bare token now fails closed;
        the real form is exercised here instead.
        """
        pkg_type, pkg_name = detect_package_type("npm", ["exec", "-y", "server-pkg"])
        assert pkg_type == "npm"
        assert pkg_name == "server-pkg"

    def test_npm_without_a_subcommand_is_not_a_package(self) -> None:
        """A bare `npm <token>` is not legal npm, so no identity is claimed."""
        assert detect_package_type("npm", ["-y", "server-pkg"]) == ("unknown", None)

    def test_uvx_simple_package(self) -> None:
        """Test detection of uvx (PyPI) package."""
        pkg_type, pkg_name = detect_package_type("uvx", ["mcp-server-git"])
        assert pkg_type == "pypi"
        assert pkg_name == "mcp-server-git"

    def test_uvx_with_flags(self) -> None:
        """Test uvx detection skips flags."""
        pkg_type, pkg_name = detect_package_type(
            "uvx", ["--quiet", "my-package", "--arg"]
        )
        assert pkg_type == "pypi"
        assert pkg_name == "my-package"

    def test_unknown_command(self) -> None:
        """Test unknown command returns unknown type."""
        pkg_type, pkg_name = detect_package_type("python", ["-m", "mymodule"])
        assert pkg_type == "unknown"
        assert pkg_name is None

    def test_docker_command(self) -> None:
        """Test docker command detects image name."""
        pkg_type, pkg_name = detect_package_type("docker", ["run", "myimage"])
        assert pkg_type == "docker"
        assert pkg_name == "myimage"

    def test_docker_run_with_flags(self) -> None:
        """Test docker run strips flags and finds image."""
        pkg_type, pkg_name = detect_package_type(
            "docker", ["run", "-i", "--rm", "mcp/server:latest"]
        )
        assert pkg_type == "docker"
        assert pkg_name == "mcp/server"

    def test_docker_run_with_env_flag(self) -> None:
        """Test docker run skips -e VALUE and finds image."""
        pkg_type, pkg_name = detect_package_type(
            "docker", ["run", "-e", "KEY=val", "--rm", "ghcr.io/org/mcp"]
        )
        assert pkg_type == "docker"
        assert pkg_name == "ghcr.io/org/mcp"

    def test_cargo_run_with_package_flag(self) -> None:
        """Test cargo run -p package detects package."""
        pkg_type, pkg_name = detect_package_type(
            "cargo", ["run", "-p", "my-mcp-server"]
        )
        assert pkg_type == "cargo"
        assert pkg_name == "my-mcp-server"

    def test_cargo_run_with_bin_flag(self) -> None:
        """Test cargo run --bin binary detects binary name."""
        pkg_type, pkg_name = detect_package_type(
            "cargo", ["run", "--bin", "mcp-binary"]
        )
        assert pkg_type == "cargo"
        assert pkg_name == "mcp-binary"

    def test_cargo_install(self) -> None:
        """Test cargo install package detects package."""
        pkg_type, pkg_name = detect_package_type("cargo", ["install", "mcp-tool"])
        assert pkg_type == "cargo"
        assert pkg_name == "mcp-tool"

    def test_pip_install(self) -> None:
        """Test pip install detects PyPI package."""
        pkg_type, pkg_name = detect_package_type("pip", ["install", "mcp-server-git"])
        assert pkg_type == "pypi"
        assert pkg_name == "mcp-server-git"

    def test_pip3_install_upgrade(self) -> None:
        """Test pip3 install --upgrade detects package."""
        pkg_type, pkg_name = detect_package_type(
            "pip3", ["install", "--upgrade", "my-mcp-server"]
        )
        assert pkg_type == "pypi"
        assert pkg_name == "my-mcp-server"

    def test_empty_args(self) -> None:
        """Test npx with empty args."""
        pkg_type, pkg_name = detect_package_type("npx", [])
        assert pkg_type == "unknown"
        assert pkg_name is None

    def test_only_flags(self) -> None:
        """Test npx with only flags."""
        pkg_type, pkg_name = detect_package_type("npx", ["-y", "--quiet"])
        assert pkg_type == "unknown"
        assert pkg_name is None


class TestPackageIdentityCollisions:
    """Two different packages must never share one identity.

    Consiliency/pmcp#180. 2.4.0's identity gate decides whether a cached
    description still describes the configured package by comparing the NAMES
    this module returns, so a name that is stable across two DIFFERENT packages
    is not a cosmetic parsing wart -- `_same_package` reads it as a positive
    confirmation and the freshness short-circuit serves the wrong package's
    tool descriptions.

    Every test here asserts INEQUALITY of a real pair, not a single expected
    value. A value assertion passes against a parser that collapses both forms
    onto some other shared name; only the inequality is falsified by the
    collision itself. All of these are RED against the pre-fix parser, which
    returned an equal name for each pair.

    Scope: these are the two collisions Consiliency/pmcp#180 measured.
    `docker run --env-file X <img>`, `--mount`, and `npm exec --package=<pkg>`
    still collide (Consiliency/pmcp#182) -- a flag's VALUE being taken as the
    package -- and that is deliberately not fixed here.
    """

    def test_docker_registry_host_port_distinguishes_images(self) -> None:
        """`registry:5000/A` and `registry:5000/B` are different packages.

        RED before the fix: the branch split on the FIRST colon, so both
        returned `("docker", "registry")` -- the registry host, not an image.
        """
        old = detect_package_type("docker", ["run", "registry:5000/old-image"])
        new = detect_package_type("docker", ["run", "registry:5000/new-image"])
        assert old != new, (
            "two different images on one host:port registry collapsed to a "
            f"single identity: {old} == {new}"
        )
        assert old == ("docker", "registry:5000/old-image")
        assert new == ("docker", "registry:5000/new-image")

    def test_npm_exec_distinguishes_packages(self) -> None:
        """`npm exec A` and `npm exec B` are different packages.

        RED before the fix: with no subcommand skip the first non-flag token
        was `exec`, so both returned `("npm", "exec")`.
        """
        old = detect_package_type("npm", ["exec", "old-pkg"])
        new = detect_package_type("npm", ["exec", "new-pkg"])
        assert old != new, f"two different npm exec packages collapsed: {old} == {new}"
        assert old == ("npm", "old-pkg")
        assert new == ("npm", "new-pkg")

    def test_npm_x_alias_distinguishes_packages(self) -> None:
        """`x` is npm's documented `exec` alias and must skip identically."""
        old = detect_package_type("npm", ["x", "old-pkg"])
        new = detect_package_type("npm", ["x", "new-pkg"])
        assert old != new, f"npm x packages collapsed: {old} == {new}"
        assert old == ("npm", "old-pkg")


class TestValueFlagCollisions:
    """A flag's VALUE must never be mistaken for the package it precedes.

    Consiliency/pmcp#182. `detect_package_type` skipped flags but not the
    tokens those flags CARRY, so the first "non-flag" token was routinely a
    flag's argument. Two different servers then produced one identity, and
    2.4.0's identity gate reads an equal name as a POSITIVE confirmation --
    serving the wrong package's tool descriptions indefinitely.

    Each test asserts INEQUALITY of a real pair *and* pins both exact values.
    The inequality is what the collision falsifies; the exact values stop a
    parser that merely collapses the pair onto some third shared name from
    passing. All seven were RED on `main` @ 75eb9c7, each returning the flag's
    value as the package name.
    """

    def test_uvx_python_version_is_not_the_package(self) -> None:
        """RED before: both sides returned `("pypi", "3.12")` -- the Python
        version, read off `--python`, as the package name."""
        a = detect_package_type("uvx", ["--python", "3.12", "pkg-a"])
        b = detect_package_type("uvx", ["--python", "3.12", "pkg-b"])
        assert a != b, f"`--python`'s value collapsed two packages: {a} == {b}"
        assert a == ("pypi", "pkg-a")
        assert b == ("pypi", "pkg-b")

    def test_uvx_with_dependency_is_not_the_package(self) -> None:
        """RED before: both returned `("pypi", "requests")` -- an injected
        dependency named by `--with`, not the server being run."""
        a = detect_package_type("uvx", ["--with", "requests", "srv-a"])
        b = detect_package_type("uvx", ["--with", "requests", "srv-b"])
        assert a != b, f"`--with`'s value collapsed two packages: {a} == {b}"
        assert a == ("pypi", "srv-a")
        assert b == ("pypi", "srv-b")

    def test_pip_index_url_is_not_the_package(self) -> None:
        """RED before: both returned `("pypi", "https://x")` -- an index URL
        as a package name, which no registry lookup could ever resolve."""
        a = detect_package_type("pip", ["install", "--index-url", "https://x", "pkg-a"])
        b = detect_package_type("pip", ["install", "--index-url", "https://x", "pkg-b"])
        assert a != b, f"`--index-url`'s value collapsed two packages: {a} == {b}"
        assert a == ("pypi", "pkg-a")
        assert b == ("pypi", "pkg-b")

    def test_cargo_features_is_not_the_crate(self) -> None:
        """RED before: both returned `("cargo", "full")` -- a feature name."""
        a = detect_package_type("cargo", ["run", "--features", "full", "srv-a"])
        b = detect_package_type("cargo", ["run", "--features", "full", "srv-b"])
        assert a != b, f"`--features`'s value collapsed two crates: {a} == {b}"
        assert a == ("cargo", "srv-a")
        assert b == ("cargo", "srv-b")

    def test_docker_env_file_is_not_the_image(self) -> None:
        """RED before: both returned `("docker", ".env")`.

        `--env-file` was simply missing from `_docker_image_arg`'s value-flag
        table -- the most careful branch in the file, and still incomplete.
        """
        a = detect_package_type("docker", ["run", "--env-file", ".env", "img-a"])
        b = detect_package_type("docker", ["run", "--env-file", ".env", "img-b"])
        assert a != b, f"`--env-file`'s value collapsed two images: {a} == {b}"
        assert a == ("docker", "img-a")
        assert b == ("docker", "img-b")

    def test_docker_mount_spec_is_not_the_image(self) -> None:
        """RED before: both returned `("docker", "type=bind,src=/a")`."""
        a = detect_package_type(
            "docker", ["run", "--mount", "type=bind,src=/a", "img-a"]
        )
        b = detect_package_type(
            "docker", ["run", "--mount", "type=bind,src=/a", "img-b"]
        )
        assert a != b, f"`--mount`'s value collapsed two images: {a} == {b}"
        assert a == ("docker", "img-a")
        assert b == ("docker", "img-b")

    def test_npm_exec_package_flag_names_the_package(self) -> None:
        """RED before: both returned `("npm", "bin")` -- the BINARY.

        The seventh collision, and the only one of the seven where identity is
        genuinely RECOVERABLE rather than merely refusable: `--package` is
        known-positive, so its value IS the package. Treating `--package=old`
        as merely "self-delimiting, so keep scanning" still returns `bin`;
        the scanner has to read the value back OUT of the flag.
        """
        a = detect_package_type("npm", ["exec", "--package=old", "--", "bin"])
        b = detect_package_type("npm", ["exec", "--package=new", "--", "bin"])
        assert a != b, f"two packages exposing one binary collapsed: {a} == {b}"
        assert a == ("npm", "old")
        assert b == ("npm", "new")

    def test_npm_exec_package_flag_spaced_spelling(self) -> None:
        """npm documents both `--package=<pkg>` and `--package <pkg>`.

        **This case does not discriminate the fix, and is kept as a plain
        regression guard rather than as evidence.** Mutation-proved: with
        `--package` removed from the known-positive set entirely, the spaced
        spelling still resolves correctly, because skipping `--package` as an
        ordinary flag leaves its value as the first bare token anyway. Only
        the `=` spelling above and the repeated-flag refusal below actually
        pin the extraction.
        """
        a = detect_package_type("npm", ["exec", "--package", "old", "--", "bin"])
        b = detect_package_type("npm", ["exec", "--package", "new", "--", "bin"])
        assert a != b, f"spaced `--package` collapsed two packages: {a} == {b}"
        assert a == ("npm", "old")
        assert b == ("npm", "new")

    def test_npm_repeated_package_flags_refuse(self) -> None:
        """npm allows `--package` more than once; several are not an identity.

        One package is an identity. Two DIFFERENT ones are not, and returning
        the first would be exactly the guess this change exists to stop -- the
        two configs would then confirm as one package on the strength of a
        coin flip. Repeating the SAME package is still an identity.
        """
        assert detect_package_type(
            "npm", ["exec", "--package", "a", "--package", "b", "--", "bin"]
        ) == ("unknown", None)
        assert detect_package_type(
            "npm", ["exec", "--package", "a", "--package", "a", "--", "bin"]
        ) == ("npm", "a")


class TestPositiveFlagGuardArms:
    """The known-positive branch's guards, each arm pinned separately.

    `--from`/`-p`/`--package`/`--bin` name the package in the NEXT token, so
    the branch guards two ways it can fail to be one: the flag trails at end of
    argv (nothing follows), or what follows is itself flag-shaped. Both arms
    were *correct but unpinned* -- a board seat killed the suite's ability to
    see them by deleting the `startswith("-")` arm and running all 364 tests
    green, after which `uvx --from --offline a` and `... b` both resolved to
    `('pypi', '--offline')`. Verified end-to-end: `_same_package` returns True
    on that pair, so the mutant's collision passes the identity gate.

    Not reachable from a launchable config -- real uv rejects both forms -- but
    an unpinned guard is exactly the shape this module keeps being corrected
    for, so it is pinned rather than argued away.
    """

    def test_positive_flag_followed_by_a_flag_refuses(self) -> None:
        """The value of `--from` cannot be another flag."""
        assert detect_package_type("uvx", ["--from", "--offline", "pkg"]) == (
            "unknown",
            None,
        )

    def test_positive_flag_at_end_of_argv_refuses(self) -> None:
        """`--from` with nothing after it names no package.

        NON-DISCRIMINATING, and labelled so rather than implying a proof.
        The guard's `following is None` arm is UNREACHABLE: when a positive
        flag trails, the loop simply ends and the function falls through to
        `("unknown", None)` anyway. Deleting that arm leaves this test -- and
        every other -- green, verified. The arm is defensive, not load-bearing;
        this test pins the observable behaviour, not the branch.
        """
        assert detect_package_type("uvx", ["--from"]) == ("unknown", None)
        assert detect_package_type("cargo", ["run", "--bin"]) == ("unknown", None)

    def test_positive_flag_with_an_empty_attached_value_refuses(self) -> None:
        """`--from=` attaches an empty value, which is not a package name."""
        assert detect_package_type("uvx", ["--from=", "pkg"]) == ("unknown", None)

    def test_cargo_shares_the_same_guard_arms(self) -> None:
        """cargo's `-p`/`--bin` run through the same branch."""
        assert detect_package_type("cargo", ["run", "-p", "--offline"]) == (
            "unknown",
            None,
        )


class TestValueFlagsFailClosed:
    """An UNLISTED bare flag yields `("unknown", None)`, never a guess.

    This is the inversion that makes the classification safe, and it is the
    opposite of the denylist Consiliency/pmcp#183 rejected. Docker's
    `_value_flags` table failed OPEN: the default for an unlisted flag was
    "skip it and take the next token as the image", so an omission silently
    produced a WRONG identity. Here an omission costs auto-update for one odd
    config -- safe, loud, and fixable by adding an entry -- instead of a silent
    collision that no one can see.

    `_same_package` (refresher.py) already resolves `"unknown"` to "cannot
    confirm -> refresh", so a refusal degrades gracefully to more work rather
    than to wrong output.
    """

    def test_uvx_unlisted_flag_refuses(self) -> None:
        assert detect_package_type("uvx", ["--not-a-real-uv-flag", "pkg"]) == (
            "unknown",
            None,
        )

    def test_pip_unlisted_flag_refuses(self) -> None:
        assert detect_package_type(
            "pip", ["install", "--not-a-real-pip-flag", "p"]
        ) == (
            "unknown",
            None,
        )

    def test_cargo_unlisted_flag_refuses(self) -> None:
        assert detect_package_type(
            "cargo", ["run", "--not-a-real-cargo-flag", "s"]
        ) == (
            "unknown",
            None,
        )

    def test_docker_unlisted_flag_refuses(self) -> None:
        assert detect_package_type(
            "docker", ["run", "--not-a-real-docker-flag", "i"]
        ) == (
            "unknown",
            None,
        )

    def test_unlisted_flag_with_attached_value_is_self_delimiting(self) -> None:
        """`--unlisted=value` is SAFE to skip and must not refuse.

        The hazard an unlisted flag creates is that it might consume the next
        token. A `--flag=value` spelling carries its value inside the token,
        so it cannot; refusing here would cost auto-update for no safety gain.
        """
        assert detect_package_type("uvx", ["--not-a-real-uv-flag=x", "pkg"]) == (
            "pypi",
            "pkg",
        )

    def test_two_different_unlisted_forms_do_not_confirm_each_other(self) -> None:
        """The safety property: unknown never CONFIRMS an identity.

        Two refusals ARE equal to each other -- that is unavoidable and is
        exactly why the class property is "different, or unknown" rather than
        "always different". `_same_package` rejects `"unknown"` on either side
        before any comparison happens, so equality here can never be read as a
        positive confirmation.
        """
        a = detect_package_type("uvx", ["--not-a-real-uv-flag", "pkg-a"])
        b = detect_package_type("uvx", ["--not-a-real-uv-flag", "pkg-b"])
        assert a == b == ("unknown", None)
        from pmcp.manifest.refresher import _same_package

        assert _same_package(a[1] or "", a[0], b[1], b[0]) is False


class TestKnownPositiveValueFlags:
    """Forms that resolved correctly BEFORE the fix must be untouched.

    A rejected earlier design -- "any bare flag before the candidate makes
    identity unrecoverable" -- broke every one of the docker cases below, and
    `docker run -it --rm <image>` is the canonical shape in this repo's own
    README (`README.md:1528`). `-it` is a COMBINED short boolean that no
    value-table derived from `docker run --help` would ever list, since docker
    only documents `-i` and `-t` separately. These tests exist to keep that
    regression from being reintroduced.
    """

    def test_uvx_boolean_flag_still_finds_package(self) -> None:
        assert detect_package_type("uvx", ["--quiet", "my-package", "--arg"]) == (
            "pypi",
            "my-package",
        )

    def test_docker_short_boolean_flags_still_find_image(self) -> None:
        assert detect_package_type(
            "docker", ["run", "-i", "--rm", "mcp/server:latest"]
        ) == (
            "docker",
            "mcp/server",
        )

    def test_docker_env_flag_still_finds_image(self) -> None:
        assert detect_package_type(
            "docker", ["run", "-e", "KEY=val", "--rm", "ghcr.io/org/mcp"]
        ) == ("docker", "ghcr.io/org/mcp")

    def test_docker_combined_short_booleans_still_find_image(self) -> None:
        """`-it` is one token docker never documents; it must be listed by hand."""
        assert detect_package_type("docker", ["run", "-it", "--rm", "img"]) == (
            "docker",
            "img",
        )

    def test_uvx_from_after_the_package_does_not_win(self) -> None:
        """A LEFT-TO-RIGHT scan, not a whole-argv `--from` hunt.

        An earlier draft scanned all of argv for `--from` to rescue the README
        pin form. Measured, that turns this case from `mypkg` into `other`: a
        fail-open misidentification INTRODUCED by the fix, which would
        re-collide `uvx a --from x` with `uvx b --from x` through the new path.
        The positional wins because it comes first.
        """
        assert detect_package_type("uvx", ["mypkg", "--from", "other"]) == (
            "pypi",
            "mypkg",
        )

    def test_known_positive_forms_unchanged(self) -> None:
        assert detect_package_type("uvx", ["--from", "pkg", "tool"]) == ("pypi", "pkg")
        assert detect_package_type("cargo", ["run", "-p", "pkg"]) == ("cargo", "pkg")
        assert detect_package_type("cargo", ["run", "--bin", "b"]) == ("cargo", "b")
        assert detect_package_type("pip", ["install", "pkg"]) == ("pypi", "pkg")
        assert detect_package_type("npx", ["-y", "pkg"]) == ("npm", "pkg")
        assert detect_package_type("docker", ["run", "img"]) == ("docker", "img")

    def test_scan_stops_at_the_double_dash_separator(self) -> None:
        """Everything after `--` belongs to the SERVED tool, not to uvx.

        uv passes those through verbatim, so a server's own `--from` argument
        must never be mistaken for uvx's package identity.

        **Pins the behaviour, not the `--` branch.** Mutation-proved: deleting
        the explicit `--` terminator changes nothing observable, because `--`
        also trips the fail-closed default and refuses there instead. The
        assertions below are still worth keeping -- they are what would catch
        a future change that made tokens after `--` reachable -- but no test
        can distinguish the two implementations, and this one does not claim
        to.
        """
        assert detect_package_type(
            "uvx", ["--from", "pkg", "tool", "--", "--from", "x"]
        ) == (
            "pypi",
            "pkg",
        )
        assert detect_package_type("uvx", ["--", "--python", "3.12", "x"]) == (
            "unknown",
            None,
        )

    def test_readme_documented_pin_form_resolves_to_the_package(self) -> None:
        """`README.md:1133` recommends this exact shape for a FIRST-PARTY server.

        RED before the fix, which returned `("pypi", "3.12")` -- so #182 was
        never hypothetical for uvx: the repo mis-identified a config it
        recommends in its own README. With `--python` classified as a value
        flag, a plain left-to-right scan consumes `3.12` and `--from` then
        yields the package, with no whole-argv scan needed.

        What this pins is `--python`'s classification, which is the half that
        was broken. It does NOT pin `--from`'s: mutation-proved, ignoring
        `--from` entirely still yields `index-it-mcp` here, because the
        README's form ends with a positional that happens to equal the
        `--from` base name. `--from` is pinned by
        `test_known_positive_forms_unchanged` and the normalization test.
        """
        assert detect_package_type(
            "uvx", ["--python", "3.12", "--from", "index-it-mcp==1.2.0", "index-it-mcp"]
        ) == ("pypi", "index-it-mcp")

    def test_from_value_is_normalized_to_its_pep508_base_name(self) -> None:
        """`--from`'s value is a PEP 508 requirement, not a bare package name.

        This repairs a LIVE defect rather than introducing a behaviour change:
        `manifest.yaml:352-357` ships `--from browser-use[cli]`, and a PyPI
        lookup for `browser-use[cli]` returns None while `browser-use` returns
        a real version -- so that first-party entry's version checks have been
        silently failing. Stripping the extra repairs them.
        """
        assert detect_package_type(
            "uvx", ["--from", "browser-use[cli]", "browser-use"]
        ) == (
            "pypi",
            "browser-use",
        )
        assert detect_package_type("uvx", ["--from", "index-it-mcp==1.2.0", "x"]) == (
            "pypi",
            "index-it-mcp",
        )
        assert detect_package_type("uvx", ["--from", "pkg>=1.0", "x"]) == (
            "pypi",
            "pkg",
        )

    def test_from_url_value_keeps_the_whole_url_as_identity(self) -> None:
        """A VCS URL is its own identity -- distinct URLs are distinct packages.

        Normalizing it to a PEP 508 name would be wrong (there is no name to
        take), and refusing would be needlessly lossy. Keeping the URL keeps
        the gate correct: two different repos never confirm as one, and the
        PyPI lookup fails closed to None, which the gate already reads as
        "cannot confirm -> refresh".
        """
        a = detect_package_type("uvx", ["--from", "git+https://x/y", "tool"])
        b = detect_package_type("uvx", ["--from", "git+https://x/z", "tool"])
        assert a == ("pypi", "git+https://x/y")
        assert a != b, (
            f"two different git sources collapsed to one identity: {a} == {b}"
        )

    def test_positional_uvx_token_is_left_raw(self) -> None:
        """Only `--from` values are normalized.

        `uvx pkg==1.2.3` keeps its inline pin in the name so the existing
        "pinned server" refusal path in gateway.update_server still fires on
        the failed PyPI lookup it was written around (handlers.py:289-294).
        """
        assert detect_package_type("uvx", ["pkg==1.2.3"]) == ("pypi", "pkg==1.2.3")


class TestDockerReferenceSplitting:
    """`_docker_image_name` and `_docker_image_tag` are complements.

    They must agree about where a reference divides. An identity gate compares
    the name and pin detection reads the tag, so a divergence means the two
    disagree about which package is configured. They are written as a pair for
    this reason, the same way `_strip_npm_tag`/`_npm_tag` are.
    """

    @pytest.mark.parametrize(
        "ref,name,tag",
        [
            # The #180 case: before the last `/`, a colon is a host:port.
            ("registry:5000/old-image", "registry:5000/old-image", None),
            ("localhost:5000/a/b:tag", "localhost:5000/a/b", "tag"),
            # Ordinary tagged forms -- unchanged by the fix.
            ("img:1.2", "img", "1.2"),
            ("ghcr.io/org/img:v2", "ghcr.io/org/img", "v2"),
            ("mcp/server:latest", "mcp/server", "latest"),
            # No `/` at all: per the OCI grammar this really IS image
            # `registry` tagged `5000`, so it must NOT change.
            ("registry:5000", "registry", "5000"),
            # Digests. `@` cannot appear in a name, so it is stripped FIRST --
            # the colon inside `sha256:` belongs to the digest, and a rule that
            # reached for the last colon would yield `img:1.2@sha256` here.
            ("img@sha256:abc", "img", None),
            ("img:1.2@sha256:abc", "img", "1.2"),
            ("registry:5000/img@sha256:abc", "registry:5000/img", None),
            # Degenerate.
            ("myimage", "myimage", None),
        ],
    )
    def test_name_and_tag_agree(self, ref: str, name: str, tag: str | None) -> None:
        assert _docker_image_name(ref) == name
        assert _docker_image_tag(ref) == tag


class TestNpmSubcommandSkipFiresOnce:
    """The npm subcommand skip must not eat real package names.

    `i` and `exec` are genuine npm packages, so a skip that fired repeatedly --
    the shape the docker branch uses for its own subcommands -- would resolve
    these to `None`. And `npx` takes a package directly, so it must not skip
    at all.
    """

    def test_npm_install_of_a_package_named_i(self) -> None:
        assert detect_package_type("npm", ["install", "i"]) == ("npm", "i")

    def test_npm_exec_of_a_package_named_exec(self) -> None:
        assert detect_package_type("npm", ["exec", "exec"]) == ("npm", "exec")

    def test_npx_does_not_skip_a_package_named_exec(self) -> None:
        assert detect_package_type("npx", ["-y", "exec"]) == ("npm", "exec")

    def test_npm_without_a_subcommand_yields_no_identity(self) -> None:
        """Renamed and inverted from ..._still_finds_the_package.

        `npm -y server-pkg` is not a legal npm invocation, and treating its
        first token as a package is the Consiliency/pmcp#183 hazard in general
        form. The allowlist now requires a recognised subcommand before
        anything is read as a package.
        """
        assert detect_package_type("npm", ["-y", "server-pkg"]) == ("unknown", None)

    def test_npm_run_names_a_script_not_a_package(self) -> None:
        """`npm run <script>` has no recoverable package identity.

        Consiliency/pmcp#183. The operand is a script in the local
        package.json, so there is generally no registry package by that name.
        Returning it as one made gateway.update_server build
        `npx -y <script>@latest --help` -- and `npx -y` installs without
        prompting, so pmcp installed and executed whatever occupied that name
        on the public registry.

        `("unknown", None)` is the honest answer, and update_server already
        refuses on it before constructing any probe.
        """
        assert detect_package_type("npm", ["run", "mcp"]) == ("unknown", None)
        assert detect_package_type("npm", ["run", "start"]) == ("unknown", None)
        # ...including when a global flag precedes the subcommand.
        assert detect_package_type("npm", ["--silent", "run", "mcp"]) == (
            "unknown",
            None,
        )

    @pytest.mark.parametrize(
        "args",
        [
            # script runners
            ["run", "mcp"],
            ["run-script", "mcp"],
            ["start"],
            ["test"],
            ["stop"],
            ["restart"],
            # initializers (npm resolves `init foo`/`create foo` to create-foo)
            ["init", "foo"],
            ["create", "foo"],
            # TYPOS and anything else unrecognised -- the allowlist is what
            # makes these safe. Under the denylist that shipped first, every
            # one of these resolved to a package named after the subcommand
            # and would have been installed and executed.
            ["rum", "mcp"],
            ["urn", "mcp"],
            ["innit", "foo"],
            ["publish"],
            ["audit"],
            # ...and with a global flag in front.
            ["--silent", "run", "mcp"],
        ],
    )
    def test_a_subcommand_without_a_package_operand_fails_closed(
        self, args: list[str]
    ) -> None:
        """Anything outside the allowlist yields no identity at all.

        Consiliency/pmcp#183. `gateway.update_server` builds `npx -y
        {name}@latest --help` from whatever comes back, and `npx -y` installs
        without prompting -- so a subcommand that falls through as a package
        name gets fetched and run from the public registry.

        This is an ALLOWLIST, not a denylist of script runners. A denylist
        shipped first and was wrong: with only `run` and `create` denied,
        `npm start`, `npm test`, `npm run-script mcp`, `npm init foo` and
        typos like `npm rum mcp` all still resolved to packages named after the
        subcommand (ah board review, red-team seat). Failing closed costs only
        the ability to auto-update an unusual launch form; failing open costs
        arbitrary package execution.
        """
        assert detect_package_type("npm", args) == ("unknown", None)

    def test_npm_create_operand_is_not_the_package_npm_would_run(self) -> None:
        """`npm create foo` resolves to the package `create-foo`, not `foo`.

        Reporting `foo` names a DIFFERENT package than the one npm runs, which
        is the same wrong-identity hazard as the script case.
        """
        assert detect_package_type("npm", ["create", "foo"]) == ("unknown", None)

    def test_npx_can_still_run_packages_named_run_or_create(self) -> None:
        """The refusal is npm-subcommand-scoped, not a name blocklist.

        `npx -y run` names a real registry package called `run`; nothing about
        Consiliency/pmcp#183 should make that unresolvable.
        """
        assert detect_package_type("npx", ["-y", "run"]) == ("npm", "run")
        assert detect_package_type("npx", ["-y", "create"]) == ("npm", "create")
        # And the operand position is still a package for the other
        # subcommands -- only `run`/`create` name something else.
        assert detect_package_type("npm", ["exec", "run"]) == ("npm", "run")

    def test_a_leading_flag_does_not_consume_the_subcommand_skip(self) -> None:
        """npm accepts global flags before the subcommand.

        The skip is one-shot, so it must fire on the first non-flag token, not
        the first argv token. Spending it on `--silent` would leave `exec` to
        be read as the package -- reopening the #180 collapse for every form
        that carries a leading flag.

        This ordering was already correct but *unpinned*: swapping the flag
        skip and the subcommand check passed the entire suite, because every
        other test here puts the subcommand first (ah board review, adversarial
        seat, reported as a surviving mutant).
        """
        old = detect_package_type("npm", ["--silent", "exec", "old-pkg"])
        new = detect_package_type("npm", ["--silent", "exec", "new-pkg"])
        assert old != new, (
            f"a leading flag reopened the npm exec collapse: {old} == {new}"
        )
        assert old == ("npm", "old-pkg")
        assert new == ("npm", "new-pkg")


class TestCompareVersionsOrdering:
    """Detailed classification cases for `compare_versions`.

    Historically these exercised the deleted `is_version_newer` boolean; the
    assertions are repointed onto `compare_versions` by the exact rule
    `is True -> == "newer"`, `is False -> != "newer"` -- the sound
    (order-preserving) translation of the old boolean, not a per-case choice
    between `"not_newer"` and `"incomparable"`. The freeze-table parity cases
    that DO pin the exact tri-state value live in `TestCompareVersions` below.
    """

    def test_same_version(self) -> None:
        """Test same versions are not newer."""
        assert compare_versions("1.0.0", "1.0.0") != "newer"
        assert compare_versions("2025.1.1", "2025.1.1") != "newer"

    def test_semver_patch_newer(self) -> None:
        """Test patch version comparison."""
        assert compare_versions("1.0.0", "1.0.1") == "newer"
        assert compare_versions("1.0.1", "1.0.0") != "newer"

    def test_semver_minor_newer(self) -> None:
        """Test minor version comparison."""
        assert compare_versions("1.0.0", "1.1.0") == "newer"
        assert compare_versions("1.1.0", "1.0.0") != "newer"

    def test_semver_major_newer(self) -> None:
        """Test major version comparison."""
        assert compare_versions("1.0.0", "2.0.0") == "newer"
        assert compare_versions("2.0.0", "1.0.0") != "newer"

    def test_date_based_version(self) -> None:
        """Test date-based version comparison."""
        assert compare_versions("2025.1.1", "2025.1.2") == "newer"
        assert compare_versions("2025.1.1", "2025.2.1") == "newer"
        assert compare_versions("2025.12.1", "2025.1.1") != "newer"

    def test_version_with_v_prefix(self) -> None:
        """Test versions with v prefix."""
        assert compare_versions("v1.0.0", "v1.0.1") == "newer"
        assert compare_versions("V1.0.0", "V1.0.1") == "newer"

    def test_short_version(self) -> None:
        """Test 2-part versions."""
        assert compare_versions("1.0", "1.1") == "newer"
        assert compare_versions("0.19", "0.20") == "newer"

    def test_different_length_versions(self) -> None:
        """Test versions with different number of parts."""
        # 1.0 vs 1.0.1 - tuple comparison: (1, 0) vs (1, 0, 1)
        assert compare_versions("1.0", "1.0.1") == "newer"
        assert compare_versions("1.0.1", "1.0") != "newer"

    def test_zero_versions(self) -> None:
        """Test pre-release style versions."""
        assert compare_versions("0.0.1", "0.0.2") == "newer"
        assert compare_versions("0.0.19", "0.0.20") == "newer"

    def test_non_numeric_versions(self) -> None:
        """Test non-numeric, unparseable strings are never reported newer."""
        assert compare_versions("alpha", "beta") != "newer"
        assert compare_versions("beta", "alpha") != "newer"

    def test_mixed_versions(self) -> None:
        """Test versions with mixed numeric and text parts."""
        assert compare_versions("1.0.0-rc1", "1.0.0-rc2") == "newer"
        assert compare_versions("v2.0-beta1", "v2.0-beta2") == "newer"

    # --- fail-closed: never fabricate an "update available" ------------------

    def test_unorderable_current_reports_no_update(self) -> None:
        """A version this function cannot order must NOT read as out of date.

        Consiliency/pmcp#150 board review. Returning "newer" here makes the
        gateway tell an operator their server is stale, so an unreadable
        version has to report no update. Previously every one of these
        extracted digits (or an empty tuple) and compared as OLDER than any
        real release, fabricating a notice for any server whose version
        string is not a release number.
        """
        for unreadable in (
            "nightly",
            "release-channel-a",
            "build-1",  # contains a digit but is not a version
            "main",
            "latest",
            "abc.def",
            "2026-08-17-nightly",
        ):
            assert compare_versions(unreadable, "2.0.0") != "newer", unreadable

    def test_empty_current_reports_no_update(self) -> None:
        """The empty string is mcp 2.x's DEFAULT serverInfo.version.

        It is reached in practice by any server that does not set a version
        explicitly, so it must not compare as older than every release.
        """
        assert compare_versions("", "2.0.0") != "newer"

    def test_unorderable_latest_reports_no_update(self) -> None:
        """An unreadable *latest* is equally unusable -- refuse to guess."""
        assert compare_versions("1.0.0", "nightly") != "newer"
        assert compare_versions("1.0.0", "") != "newer"

    def test_docker_digests_compare_by_inequality(self) -> None:
        """Digests are identities, not ordinals.

        Uses the BARE 12-hex form ``get_docker_version`` actually returns -- it
        strips the ``sha256:`` prefix and truncates (see ``TestGetDockerVersion``).
        A previous version of this guard matched only ``sha256:``-prefixed values
        and was tested with invented literals, so it never fired against the real
        producer and silenced every docker update notice. Ordering hex is
        meaningless, but a DIFFERENT digest is genuinely a new image.
        """
        assert compare_versions("abcdef123456", "abcdef123456") != "newer"
        assert compare_versions("abcdef123456", "fedcba654321") == "newer"
        # The prefixed/full form is still accepted, in case the producer ever
        # stops truncating.
        assert compare_versions("sha256:abcdef123456", "sha256:fedcba654321") == "newer"

    def test_digest_and_version_are_not_comparable(self) -> None:
        """A digest and a release number describe different things."""
        assert compare_versions("1.0.0", "abcdef123456") != "newer"
        assert compare_versions("abcdef123456", "1.0.0") != "newer"

    def test_docker_producer_output_is_orderable(self) -> None:
        """Drive the PRODUCER, not a literal, so the two cannot drift apart.

        The prior bug was exactly this drift: the comparator matched a format
        the producer does not emit.
        """
        import re as _re

        from pmcp.manifest.version_checker import _digest_identity

        # Mirrors get_docker_version's truncation of a registry digest.
        produced = "sha256:abcdef1234567890"[7:19]
        assert _re.fullmatch(r"[0-9a-f]{12}", produced)
        assert _digest_identity(produced) == produced
        assert compare_versions(produced, "0123456789ab") == "newer"

    def test_long_numeric_version_is_not_mistaken_for_a_digest(self) -> None:
        """A purely numeric string is a version, not a digest.

        `202612180000` is a plausible calendar/build stamp and is 12 chars of
        `[0-9a-f]`. Without a hex-letter requirement it matches the digest
        pattern, and is then never compared against a dotted release -- silently
        dropping a real update. Found while reviewing my own digest rule.
        """
        from pmcp.manifest.version_checker import _digest_identity

        assert _digest_identity("202612180000") is None
        assert compare_versions("202612180000", "202612190000") == "newer"

    def test_semver_ecosystem_prerelease_ordering(self) -> None:
        """npm/Cargo publish SemVer, where `-1` is a PRERELEASE.

        PEP 440 -- what `packaging` implements -- reads `1.0.0-1` as the POST
        release `1.0.0.post1`, the opposite order. 79 of the manifest's 107
        servers are npm, so without an ecosystem-aware path this inverts
        precedence on a real published format: it hides the `1.0.0-1 -> 1.0.0`
        upgrade and fabricates the reverse.
        """
        assert compare_versions("1.0.0-1", "1.0.0", "npm") == "newer"
        assert compare_versions("1.0.0", "1.0.0-1", "npm") != "newer"
        assert compare_versions("1.0.0-alpha", "1.0.0-beta", "npm") == "newer"
        assert compare_versions("1.0.0+build.4", "1.0.0+build.5", "npm") != "newer"
        # cargo shares SemVer semantics.
        assert compare_versions("1.0.0-1", "1.0.0", "cargo") == "newer"

    def test_unorderable_version_is_not_reported_as_up_to_date(self) -> None:
        """A negated boolean is ambiguous under a fail-closed comparator; a
        tri-state return is not.

        Regression introduced by this PR and caught in review: `refresher`
        used to negate a fail-closed boolean, and once unreadable input
        returned False, `not(...)` read as "up to date". Since that function
        persists the literal `"unknown"` after a failed lookup, the stale
        cache would be returned forever. `compare_versions` returning
        `"incomparable"` -- distinct from `"not_newer"` -- is what makes that
        collapse unrepresentable now; `is_version_orderable` remains the
        unary discriminator for genuinely single-value questions.
        """
        assert is_version_orderable("unknown") is False
        assert is_version_orderable("") is False
        assert is_version_orderable("nightly") is False
        assert is_version_orderable("1.0.0") is True
        assert is_version_orderable("1.0.0-1", "npm") is True
        assert is_version_orderable("987654321098", "docker") is True

    def test_all_numeric_docker_digest_is_a_digest(self) -> None:
        """A truncated SHA-256 can be all digits.

        `get_docker_version` truncates to 12 hex chars, so `987654321098` is a
        perfectly valid digest. An earlier hex-letter requirement rejected it and
        silently dropped real image updates. Shape alone cannot disambiguate a
        digest from a calendar version, so the package type decides.
        """
        assert compare_versions("987654321098", "123456789012", "docker") == "newer"
        assert compare_versions("987654321098", "987654321098", "docker") != "newer"
        # Without a docker type, a numeric string stays a VERSION and is ordered.
        assert compare_versions("202612180000", "202612190000") == "newer"

    def test_invalid_semver_is_not_ordered(self) -> None:
        """SemVer 2.0.0 rules 2, 9 and 10: no leading zeros, no empty identifiers.

        These strings cannot be published to npm, and ordering them fabricated
        updates (`1.0.0-01` -> `1.0.0` reported an upgrade).
        """
        for invalid in ("1.0.0-01", "01.0.0", "1.0.0-a..b", "1.0.00", "1.0.0-"):
            assert compare_versions(invalid, "1.0.0", "npm") != "newer", invalid
            assert compare_versions("1.0.0", invalid, "npm") != "newer", invalid

    def test_semver_spec_precedence_chain(self) -> None:
        """The canonical chain from SemVer 2.0.0 spec section 11.4.

        Pins hand-written precedence against the specification rather than
        against my own intuition, in both directions. Includes `beta.2` <
        `beta.11`, which naive string ordering gets backwards.
        """
        chain = [
            "1.0.0-alpha",
            "1.0.0-alpha.1",
            "1.0.0-alpha.beta",
            "1.0.0-beta",
            "1.0.0-beta.2",
            "1.0.0-beta.11",
            "1.0.0-rc.1",
            "1.0.0",
        ]
        for older, newer in zip(chain, chain[1:]):
            assert compare_versions(older, newer, "npm") == "newer", (
                f"{older} -> {newer}"
            )
            assert compare_versions(newer, older, "npm") != "newer", (
                f"{newer} -> {older}"
            )

    def test_pypi_keeps_pep440_ordering(self) -> None:
        """PyPI publishes PEP 440; post-releases and its prerelease forms hold."""
        assert compare_versions("1.0.post1", "1.0.post2", "pypi") == "newer"
        assert compare_versions("1.0a1", "1.0", "pypi") == "newer"

    def test_same_digest_in_two_representations_is_not_an_update(self) -> None:
        """One image, two spellings, must not read as a new release.

        `get_docker_version` emits a bare truncated digest, but a prefixed or
        untruncated form can reach the comparator from a cached value or a
        registry that does not truncate. Comparing raw strings called that an
        update -- a fabricated notice for an unchanged image.
        """
        assert compare_versions("abcdef123456", "sha256:abcdef123456") != "newer"
        assert compare_versions("sha256:abcdef1234567890", "abcdef123456") != "newer"
        # A genuinely different image still registers.
        assert compare_versions("abcdef123456", "fedcba654321") == "newer"

    def test_build_metadata_is_not_a_new_release(self) -> None:
        """A local/build-metadata difference is not an update to announce."""
        assert compare_versions("1.0.0+build.4", "1.0.0+build.5") != "newer"

    def test_prerelease_precedence(self) -> None:
        """PEP 440 ordering: a prerelease is OLDER than its release."""
        assert compare_versions("1.0.0-rc1", "1.0.0") == "newer"
        assert compare_versions("1.0.0", "1.0.0-rc1") != "newer"


class TestCompareVersions:
    """`compare_versions` is the single classification path (IF-0-TRISTATE-1).

    Consiliency/pmcp#164. `is_version_newer` fails closed to a boolean, so
    `False` collapses "up to date" and "cannot be ordered" into one value --
    the exact ambiguity that made `not is_version_newer(...)` a recurring
    defect (#155, #156, #163). A tri-state return makes the collapse
    unrepresentable: a caller has to name the branch it means.
    """

    @pytest.mark.parametrize(
        ("current", "latest", "package_type", "expected"),
        [
            ("1.0.0", "2.0.0", "npm", "newer"),
            ("1.0.0-rc1", "1.0.0", "npm", "newer"),
            # SemVer, not PEP 440: `-1` is a prerelease, not a post-release.
            ("1.0.0-1", "1.0.0", "npm", "newer"),
            ("2.0.0", "1.0.0", "npm", "not_newer"),
            # Build metadata is not a release.
            ("1.0.0", "1.0.0+b", None, "not_newer"),
            # Same PEP 440 release, different rendering.
            ("1.0", "1.0.0", None, "not_newer"),
            # Same image, two spellings of the digest.
            ("abcdef123456", "sha256:abcdef123456", "docker", "not_newer"),
            ("abcdef123456", "abcdef123457", "docker", "newer"),
            # CalVer.
            ("202612180000", "202612190000", None, "newer"),
            # Version vs digest.
            ("1.0.0", "abcdef123456", "docker", "incomparable"),
            ("1.0.0", "nightly", "npm", "incomparable"),
            ("unknown", "2.0.0", "npm", "incomparable"),
        ],
    )
    def test_parity_with_removed_predicates(
        self,
        current: str,
        latest: str,
        package_type: str | None,
        expected: VersionComparison,
    ) -> None:
        """Every answer the two deleted predicates gave must survive as one of
        the three tri-state values -- this is a representation change, not a
        behaviour change."""
        assert compare_versions(current, latest, package_type) == expected

    def test_result_is_always_one_of_the_three_literal_values(self) -> None:
        """No input -- however malformed -- may produce a fourth value.

        That is the whole point of narrowing the return type to a `Literal`:
        unlike a bare `str`, a caller and a type checker can both rely on
        there being exactly three branches, ever.
        """
        allowed = {"newer", "not_newer", "incomparable"}
        for pkg_type in _CORPUS_TYPES:
            for current in _CORPUS_VALUES:
                for latest in _CORPUS_VALUES:
                    result = compare_versions(current, latest, pkg_type)
                    assert result in allowed, (
                        f"compare_versions({current!r}, {latest!r}, "
                        f"{pkg_type!r}) returned {result!r}, outside the "
                        f"three-value contract"
                    )

    def test_incomparable_is_not_the_same_as_not_newer(self) -> None:
        """The distinction the boolean could never make.

        A caller gating a short-circuit on `== "not_newer"` must NOT fire for
        an incomparable pair -- that was the exact defect `not
        is_version_newer(...)` produced by conflating the two.
        """
        assert compare_versions("1.0.0", "nightly", "npm") == "incomparable"
        assert compare_versions("1.0.0", "nightly", "npm") != "not_newer"


class TestGetNpmVersion:
    """Tests for get_npm_version function."""

    @pytest.fixture(autouse=True)
    def clear_cache(self) -> None:
        """Clear version cache before each test."""
        clear_version_cache()

    def test_user_agent_uses_pmcp_version(self) -> None:
        assert _USER_AGENT == f"pmcp/{__version__} (github.com/ViperJuice/pmcp)"

    @pytest.mark.asyncio
    async def test_successful_lookup(self) -> None:
        """Test successful npm version lookup."""
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={"dist-tags": {"latest": "1.2.3"}})
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=mock_response)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with patch("aiohttp.ClientSession", return_value=mock_session):
            version = await get_npm_version("test-package")
            assert version == "1.2.3"

    @pytest.mark.asyncio
    async def test_scoped_package_url_encoding(self) -> None:
        """Test scoped npm package URL encoding."""
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={"dist-tags": {"latest": "0.1.0"}})
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=mock_response)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with patch("aiohttp.ClientSession", return_value=mock_session):
            version = await get_npm_version("@playwright/mcp")
            assert version == "0.1.0"
            # Check URL was properly encoded
            call_args = mock_session.get.call_args
            url = call_args[0][0]
            assert "%40" in url  # @ encoded
            assert "%2F" in url  # / encoded

    @pytest.mark.asyncio
    async def test_404_response(self) -> None:
        """Test handling of 404 response."""
        mock_response = MagicMock()
        mock_response.status = 404
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=mock_response)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with patch("aiohttp.ClientSession", return_value=mock_session):
            version = await get_npm_version("nonexistent-package")
            assert version is None

    @pytest.mark.asyncio
    async def test_timeout_error(self) -> None:
        """Test handling of timeout."""
        mock_session = MagicMock()
        mock_session.get = MagicMock(side_effect=asyncio.TimeoutError())
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with patch("aiohttp.ClientSession", return_value=mock_session):
            version = await get_npm_version("test-package", timeout=0.1)
            assert version is None

    @pytest.mark.asyncio
    async def test_network_error(self) -> None:
        """Test handling of network error."""
        mock_session = MagicMock()
        mock_session.get = MagicMock(side_effect=Exception("Network error"))
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with patch("aiohttp.ClientSession", return_value=mock_session):
            version = await get_npm_version("test-package")
            assert version is None

    @pytest.mark.asyncio
    async def test_missing_dist_tags(self) -> None:
        """Test handling of response missing dist-tags."""
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={"name": "test"})
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=mock_response)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with patch("aiohttp.ClientSession", return_value=mock_session):
            version = await get_npm_version("test-package")
            assert version is None

    @pytest.mark.asyncio
    async def test_cache_hit(self) -> None:
        """Test that cached versions are returned without network call."""
        # Populate cache
        _version_cache["npm:cached-package"] = "1.0.0"

        # Should not make network call
        mock_session = MagicMock()
        mock_session.get = MagicMock(side_effect=Exception("Should not be called"))

        with patch("aiohttp.ClientSession", return_value=mock_session):
            version = await get_npm_version("cached-package")
            assert version == "1.0.0"
            mock_session.get.assert_not_called()


class TestGetPypiVersion:
    """Tests for get_pypi_version function."""

    @pytest.fixture(autouse=True)
    def clear_cache(self) -> None:
        """Clear version cache before each test."""
        clear_version_cache()

    @pytest.mark.asyncio
    async def test_successful_lookup(self) -> None:
        """Test successful PyPI version lookup."""
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={"info": {"version": "2025.12.18"}})
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=mock_response)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with patch("aiohttp.ClientSession", return_value=mock_session):
            version = await get_pypi_version("mcp-server-git")
            assert version == "2025.12.18"

    @pytest.mark.asyncio
    async def test_correct_url(self) -> None:
        """Test PyPI URL is correct."""
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={"info": {"version": "1.0.0"}})
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=mock_response)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with patch("aiohttp.ClientSession", return_value=mock_session):
            await get_pypi_version("my-package")
            call_args = mock_session.get.call_args
            url = call_args[0][0]
            assert url == "https://pypi.org/pypi/my-package/json"

    @pytest.mark.asyncio
    async def test_404_response(self) -> None:
        """Test handling of 404 response."""
        mock_response = MagicMock()
        mock_response.status = 404
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=mock_response)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with patch("aiohttp.ClientSession", return_value=mock_session):
            version = await get_pypi_version("nonexistent-package")
            assert version is None

    @pytest.mark.asyncio
    async def test_timeout_error(self) -> None:
        """Test handling of timeout."""
        mock_session = MagicMock()
        mock_session.get = MagicMock(side_effect=asyncio.TimeoutError())
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with patch("aiohttp.ClientSession", return_value=mock_session):
            version = await get_pypi_version("test-package")
            assert version is None

    @pytest.mark.asyncio
    async def test_missing_info(self) -> None:
        """Test handling of response missing info."""
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={"name": "test"})
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=mock_response)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with patch("aiohttp.ClientSession", return_value=mock_session):
            version = await get_pypi_version("test-package")
            assert version is None

    @pytest.mark.asyncio
    async def test_cache_hit(self) -> None:
        """Test that cached versions are returned without network call."""
        _version_cache["pypi:cached-package"] = "2.0.0"

        version = await get_pypi_version("cached-package")
        assert version == "2.0.0"


class TestGetPackageVersion:
    """Tests for get_package_version function."""

    @pytest.fixture(autouse=True)
    def clear_cache(self) -> None:
        """Clear version cache before each test."""
        clear_version_cache()

    @pytest.mark.asyncio
    async def test_npm_package(self) -> None:
        """Test npm package version lookup."""
        with patch(
            "pmcp.manifest.version_checker.get_npm_version",
            new_callable=AsyncMock,
            return_value="1.0.0",
        ):
            version, pkg_type = await get_package_version("npx", ["-y", "my-package"])
            assert version == "1.0.0"
            assert pkg_type == "npm"

    @pytest.mark.asyncio
    async def test_pypi_package(self) -> None:
        """Test PyPI package version lookup."""
        with patch(
            "pmcp.manifest.version_checker.get_pypi_version",
            new_callable=AsyncMock,
            return_value="2.0.0",
        ):
            version, pkg_type = await get_package_version("uvx", ["my-package"])
            assert version == "2.0.0"
            assert pkg_type == "pypi"

    @pytest.mark.asyncio
    async def test_unknown_package(self) -> None:
        """Test unknown package type returns unknown."""
        version, pkg_type = await get_package_version("python", ["-m", "mymodule"])
        assert version is None
        assert pkg_type == "unknown"

    @pytest.mark.asyncio
    async def test_npm_lookup_failure(self) -> None:
        """Test handling of npm lookup failure."""
        with patch(
            "pmcp.manifest.version_checker.get_npm_version",
            new_callable=AsyncMock,
            return_value=None,
        ):
            version, pkg_type = await get_package_version("npx", ["-y", "my-package"])
            assert version is None
            assert pkg_type == "npm"

    @pytest.mark.asyncio
    async def test_cargo_package(self) -> None:
        """Test cargo package version lookup."""
        with patch(
            "pmcp.manifest.version_checker.get_cargo_version",
            new_callable=AsyncMock,
            return_value="1.5.0",
        ):
            version, pkg_type = await get_package_version(
                "cargo", ["run", "-p", "my-crate"]
            )
            assert version == "1.5.0"
            assert pkg_type == "cargo"

    @pytest.mark.asyncio
    async def test_docker_package(self) -> None:
        """Test docker image version lookup."""
        with patch(
            "pmcp.manifest.version_checker.get_docker_version",
            new_callable=AsyncMock,
            return_value="abcdef123456",
        ):
            version, pkg_type = await get_package_version(
                "docker", ["run", "-i", "--rm", "mcp/server:latest"]
            )
            assert version == "abcdef123456"
            assert pkg_type == "docker"

    @pytest.mark.asyncio
    async def test_pip_package(self) -> None:
        """Test pip install package uses PyPI lookup."""
        with patch(
            "pmcp.manifest.version_checker.get_pypi_version",
            new_callable=AsyncMock,
            return_value="3.0.0",
        ):
            version, pkg_type = await get_package_version(
                "pip", ["install", "my-mcp-server"]
            )
            assert version == "3.0.0"
            assert pkg_type == "pypi"


class TestGetCargoVersion:
    """Tests for get_cargo_version function."""

    @pytest.fixture(autouse=True)
    def clear_cache(self) -> None:
        clear_version_cache()

    @pytest.mark.asyncio
    async def test_successful_lookup(self) -> None:
        """Test successful crates.io version lookup."""
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(
            return_value={"crate": {"newest_version": "1.5.0"}}
        )
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=mock_response)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with patch("aiohttp.ClientSession", return_value=mock_session):
            version = await get_cargo_version("my-crate")
            assert version == "1.5.0"

    @pytest.mark.asyncio
    async def test_correct_url(self) -> None:
        """Test crates.io URL is correct."""
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(
            return_value={"crate": {"newest_version": "0.1.0"}}
        )
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=mock_response)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with patch("aiohttp.ClientSession", return_value=mock_session):
            await get_cargo_version("my-crate")
            url = mock_session.get.call_args[0][0]
            assert url == "https://crates.io/api/v1/crates/my-crate"

    @pytest.mark.asyncio
    async def test_404_response(self) -> None:
        """Test handling of 404 response."""
        mock_response = MagicMock()
        mock_response.status = 404
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=mock_response)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with patch("aiohttp.ClientSession", return_value=mock_session):
            version = await get_cargo_version("nonexistent-crate")
            assert version is None

    @pytest.mark.asyncio
    async def test_timeout_error(self) -> None:
        """Test handling of timeout."""
        mock_session = MagicMock()
        mock_session.get = MagicMock(side_effect=asyncio.TimeoutError())
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with patch("aiohttp.ClientSession", return_value=mock_session):
            version = await get_cargo_version("my-crate")
            assert version is None

    @pytest.mark.asyncio
    async def test_cache_hit(self) -> None:
        """Test cached versions avoid network calls."""
        _version_cache["cargo:my-crate"] = "2.0.0"
        version = await get_cargo_version("my-crate")
        assert version == "2.0.0"


class TestGetDockerVersion:
    """Tests for get_docker_version function."""

    @pytest.fixture(autouse=True)
    def clear_cache(self) -> None:
        clear_version_cache()

    @pytest.mark.asyncio
    async def test_successful_lookup_namespaced(self) -> None:
        """Test successful Docker Hub lookup for namespaced image."""
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(
            return_value={"digest": "sha256:abcdef1234567890", "name": "latest"}
        )
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=mock_response)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with patch("aiohttp.ClientSession", return_value=mock_session):
            version = await get_docker_version("mcp/server")
            assert version == "abcdef123456"  # first 12 hex chars after "sha256:"

    @pytest.mark.asyncio
    async def test_official_image_uses_library_prefix(self) -> None:
        """Test official images route through library/ namespace."""
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(
            return_value={"digest": "sha256:aabbccdd11223344", "name": "latest"}
        )
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=mock_response)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with patch("aiohttp.ClientSession", return_value=mock_session):
            await get_docker_version("nginx")
            url = mock_session.get.call_args[0][0]
            assert "library/nginx" in url

    @pytest.mark.asyncio
    async def test_404_response(self) -> None:
        """Test handling of 404 response."""
        mock_response = MagicMock()
        mock_response.status = 404
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=mock_response)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with patch("aiohttp.ClientSession", return_value=mock_session):
            version = await get_docker_version("nonexistent/image")
            assert version is None

    @pytest.mark.asyncio
    async def test_timeout_error(self) -> None:
        """Test handling of timeout."""
        mock_session = MagicMock()
        mock_session.get = MagicMock(side_effect=asyncio.TimeoutError())
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with patch("aiohttp.ClientSession", return_value=mock_session):
            version = await get_docker_version("mcp/server")
            assert version is None

    @pytest.mark.asyncio
    async def test_cache_hit(self) -> None:
        """Test cached versions avoid network calls."""
        _version_cache["docker:mcp/server"] = "abc123def456"
        version = await get_docker_version("mcp/server")
        assert version == "abc123def456"


class TestClearVersionCache:
    """Tests for clear_version_cache function."""

    def test_clears_cache(self) -> None:
        """Test cache is cleared."""
        clear_version_cache()
        _version_cache["npm:test"] = "1.0.0"
        _version_cache["pypi:test"] = "2.0.0"
        assert len(_version_cache) == 2

        clear_version_cache()
        assert len(_version_cache) == 0


class TestVersionCheckUrlEscaping:
    """Version-check URLs escape the package-name segment for all registries."""

    @pytest.fixture(autouse=True)
    def clear_cache(self) -> None:
        clear_version_cache()

    @staticmethod
    async def _capture_url(getter, name: str, payload: dict) -> str:
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value=payload)
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=mock_response)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with patch("aiohttp.ClientSession", return_value=mock_session):
            await getter(name)
        return mock_session.get.call_args[0][0]

    @pytest.mark.asyncio
    async def test_pypi_name_is_quoted(self) -> None:
        url = await self._capture_url(
            get_pypi_version, "pkg name", {"info": {"version": "1.0.0"}}
        )
        assert "pkg%20name" in url
        assert "pkg name" not in url

    @pytest.mark.asyncio
    async def test_cargo_name_is_quoted(self) -> None:
        url = await self._capture_url(
            get_cargo_version, "crate name", {"crate": {"newest_version": "1.0.0"}}
        )
        assert "crate%20name" in url
        assert "crate name" not in url

    @pytest.mark.asyncio
    async def test_docker_name_is_quoted_but_slash_preserved(self) -> None:
        # org/name stays a two-segment path; the space in the name is escaped.
        url = await self._capture_url(
            get_docker_version, "org/img name", {"digest": "sha256:abcdef012345"}
        )
        assert "org/img%20name" in url
        assert "img name" not in url


class TestAllNumericDigestDisambiguation:
    """Consiliency/pmcp#156 item 3: an all-numeric truncated digest.

    `get_docker_version` truncates SHA-256 to 12 hex chars, which can be all
    digits -- the same shape as a calendar version. With no package type the
    shape alone cannot classify it.

    Two resolutions were tried and both rejected. Fail-closed on any bare
    12-digit string breaks CalVer, which #155 deliberately supports. Promoting
    the numeric side when its partner is a digest resolves the ambiguity by
    guessing, and the guess fabricates an update when the numeric side really
    is a calendar version. So a mixed pair stays incomparable, and the package
    type -- which every caller passes -- is what resolves it.
    """

    def test_mixed_numeric_and_lettered_pair_stays_incomparable(self) -> None:
        """An ambiguous pair must NOT be resolved by guessing.

        Promoting the all-numeric side to a digest because its partner is one
        was implemented and then rejected in board review: the guess fabricates
        an update when the numeric side is a genuine calendar version, which is
        exactly what `compare_versions`'s fail-closed contract exists to
        prevent. Incomparable is the correct answer without a package type.
        """
        assert compare_versions("202612180000", "abcdef123456") == "incomparable"
        assert compare_versions("abcdef123456", "202612180000") == "incomparable"

    def test_package_type_resolves_the_ambiguity(self) -> None:
        """The type is what makes an all-numeric digest orderable."""
        assert compare_versions("987654321098", "123456789012", "docker") == "newer"

    def test_sha256_prefix_alone_identifies_an_all_numeric_digest(self) -> None:
        """The `sha256:` prefix names a digest even with no hex letter.

        The digest pattern previously required a hex letter *even when the
        prefix was present*, so an all-numeric prefixed digest was rejected.
        """
        assert compare_versions("sha256:987654321098", "sha256:123456789012") == "newer"
        assert is_version_orderable("sha256:987654321098") is True

    def test_same_image_two_spellings_is_not_an_update(self) -> None:
        """Promotion must not break canonicalisation."""
        assert compare_versions("sha256:abcdef123456", "abcdef123456") == "not_newer"

    def test_calendar_versions_still_order(self) -> None:
        """The reverted fail-closed would have broken exactly this."""
        assert compare_versions("202612180000", "202612190000") == "newer"
        assert is_version_orderable("202612180000") is True


def test_all_compare_versions_callers_pass_package_type() -> None:
    """Every `compare_versions(...)` call must pass a package type.

    The type is what disambiguates an all-numeric truncated digest from a
    calendar version. Without it the pair is incomparable by design (see
    TestAllNumericDigestDisambiguation), so a caller that drops the type
    silently loses update detection for docker servers.

    Consiliency/pmcp#156 called that risk latent because every caller passed
    the type. This pins that, instead of leaving it as a fact that happened
    to be true when the issue was written. Repointed from
    `test_all_is_version_newer_callers_pass_package_type` (Consiliency/pmcp#164)
    onto `compare_versions`, which has the identical
    ``package_type: str | None = None`` default and so inherits the identical
    risk.
    """
    from pathlib import Path

    src_root = Path(__file__).resolve().parent.parent / "src"
    offenders: list[str] = []

    for path in sorted(src_root.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for call in ast.walk(tree):
            if not isinstance(call, ast.Call):
                continue
            if _called_name(call) != "compare_versions":
                continue
            has_type = len(call.args) >= 3 or any(
                kw.arg == "package_type" for kw in call.keywords
            )
            if not has_type:
                offenders.append(f"{path.relative_to(src_root)}:{call.lineno}")

    assert not offenders, (
        "`compare_versions(...)` called without a package_type -- an "
        "all-numeric image digest becomes indistinguishable from a calendar "
        f"version and stops being comparable: {offenders}"
    )


_CORPUS_VALUES = [
    "1.0.0",
    "2.0.0",
    "1.0.0-rc1",
    "1.0.0-1",
    "1.0.0+b",
    "v1.0.0",
    "0.0.1",
    "10.0.0",
    "nightly",
    "unknown",
    "",
    "main",
    "build-1",
    "abcdef123456",
    "abcdef123457",
    "sha256:abcdef123456",
    "a" * 64,
    "987654321098",
    "123456789012",
    "202612180000",
    "202612190000",
    "1.0",
    "1.0.0.post1",
    "2026-08-17-nightly",
    "01.0.0",
    "1.0.0-01",
    "1.0.0-a..b",
    "  1.0.0  ",
]
_CORPUS_TYPES = (None, "npm", "cargo", "docker", "pypi", "cli", "bogus")


class TestPairComparabilityCorpus:
    """The 28-value x 7-type corpus: `compare_versions`'s ``"incomparable"``
    branch is a PAIR property, not
    two unary checks.

    Board review of #163 found the earlier both-sides guard still wrong:
    `"1.0.0"` and `"abcdef123456"` are each individually orderable, so two
    unary `is_version_orderable` calls both passed -- but a version and a
    digest are mutually incomparable. Under the old fail-closed boolean that
    made `is_version_newer` return False and a negating caller read "up to
    date" for a pair it could not order; `compare_versions` now names that
    branch `"incomparable"` instead of reusing the "not newer" value.
    Reachable because refresh_all reuses a cache entry by server name without
    checking that the package type still matches.
    """

    def test_version_against_digest_is_not_comparable(self) -> None:
        """The case two unary guards let through."""
        assert is_version_orderable("1.0.0", "docker") is True
        assert is_version_orderable("abcdef123456", "docker") is True
        assert compare_versions("1.0.0", "abcdef123456", "docker") == "incomparable"

    def test_matching_kinds_are_comparable(self) -> None:
        assert compare_versions("1.0.0", "2.0.0", "npm") != "incomparable"
        assert (
            compare_versions("abcdef123456", "abcdef123457", "docker") != "incomparable"
        )

    def test_unreadable_on_either_side_is_not_comparable(self) -> None:
        assert compare_versions("1.0.0", "nightly", "npm") == "incomparable"
        assert compare_versions("nightly", "1.0.0", "npm") == "incomparable"
        assert compare_versions("unknown", "2.0.0", "npm") == "incomparable"

    def test_incomparable_pairs_are_never_ordered(self) -> None:
        """The safety invariant: incomparable => never `"newer"` either way.

        This is what makes ``"incomparable"`` a sound value rather than a
        second spelling of ``"not_newer"``. If a pair classified incomparable
        could still be ordered, the guard would suppress a real update; if a
        pair classified comparable could not be ordered, a caller reading
        `!= "newer"` as "up to date" would be wrong for something incomparable
        -- the defect this whole tri-state exists to close.

        Stated one-directionally on purpose. The converse ("comparable implies
        one side is newer") is FALSE for equal values, and writing it that way
        first produced a real failure: `abcdef123456` and
        `sha256:abcdef123456` are the same image in two spellings, so they are
        comparable and neither is newer. Textual inequality is not value
        inequality here.
        """
        for pkg_type in _CORPUS_TYPES:
            for current in _CORPUS_VALUES:
                for latest in _CORPUS_VALUES:
                    if compare_versions(current, latest, pkg_type) != "incomparable":
                        continue
                    assert compare_versions(current, latest, pkg_type) != "newer", (
                        f"{current!r} -> {latest!r} ({pkg_type!r}) reported newer "
                        f"despite being incomparable"
                    )
                    assert compare_versions(latest, current, pkg_type) != "newer", (
                        f"{latest!r} -> {current!r} ({pkg_type!r}) reported newer "
                        f"despite being incomparable"
                    )

    def test_comparable_pairs_are_always_ordered_or_equal(self) -> None:
        """The DANGEROUS drift direction, over the same corpus.

        `test_incomparable_pairs_are_never_ordered` skips every pair
        classified incomparable, so on its own it cannot catch the failure
        that actually matters (ah board review): `compare_versions`
        classifying a pair comparable when it can't actually order it. That is
        precisely what let `not is_version_newer(...)` report "up to date" for
        something incomparable, under the old boolean — the defect this
        tri-state exists to close.

        So: for every non-incomparable pair, either one direction is newer, or
        the two are canonically the SAME value. Canonical identity is computed
        here independently of `compare_versions`, so the two cannot agree by
        sharing a bug.
        """

        def canonical(value: str, pkg_type: str | None) -> object:
            """Identity of *value*, for deciding whether two spellings agree.

            Must match what `compare_versions` actually compares, which took
            two corrections to get right:

            * PARSED, not string form -- `1.0` and `1.0.0` are the same PEP 440
              release but render differently;
            * the PUBLIC segment, reparsed -- `compare_versions` compares
              `.public` on purpose, since build metadata (`1.0.0+b`) is not a
              new release, so full `Version` equality reports drift that is not
              there.

            Both failures were my helper being wrong, not the code. Digests are
            already canonicalised by `_digest_identity`
            (`abcdef123456` == `sha256:abcdef123456`).
            """
            digest = _digest_identity(value, pkg_type)
            if digest is not None:
                return ("digest", digest)
            if pkg_type in ("npm", "cargo"):
                parsed = _semver_parse(value)
                return ("semver", parsed) if parsed is not None else ("raw", value)
            release = _parse_version(value)
            if release is None:
                return ("raw", value)
            return ("pep440", _parse_version(release.public))

        for pkg_type in _CORPUS_TYPES:
            for current in _CORPUS_VALUES:
                for latest in _CORPUS_VALUES:
                    if compare_versions(current, latest, pkg_type) == "incomparable":
                        continue
                    ordered = (
                        compare_versions(current, latest, pkg_type) == "newer"
                        or compare_versions(latest, current, pkg_type) == "newer"
                    )
                    if ordered:
                        continue
                    assert canonical(current, pkg_type) == canonical(
                        latest, pkg_type
                    ), (
                        f"({current!r}, {latest!r}, {pkg_type!r}) reported "
                        f"comparable, but compare_versions orders it in neither "
                        f"direction and the two are not the same value -- a "
                        f"caller reading '!= newer' as up to date would be wrong"
                    )

    def test_comparable_pairs_that_differ_are_ordered(self) -> None:
        """A comparable pair with genuinely different values orders one way."""
        for current, latest, pkg_type in [
            ("1.0.0", "2.0.0", "npm"),
            ("1.0.0-rc1", "1.0.0", "npm"),
            ("abcdef123456", "abcdef123457", "docker"),
            ("202612180000", "202612190000", None),
        ]:
            assert compare_versions(current, latest, pkg_type) != "incomparable"
            assert (
                compare_versions(current, latest, pkg_type) == "newer"
                or compare_versions(latest, current, pkg_type) == "newer"
            )


def _called_name(call: ast.Call) -> str | None:
    """Name of the function a Call node invokes, plain or attribute."""
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


class TestOrdinalReversal:
    """An ordinal `newer` must reverse to `not_newer`. Digests must not.

    Consiliency/pmcp#170 board review: the corpus asserted the safety direction
    (incomparable => ordered in neither direction) but never that an ordinal
    result is ANTISYMMETRIC. A mutant making the SemVer lane return `"newer"`
    in both directions passed every corpus test.

    The distinction matters and is why this is not a blanket rule: a digest is
    an IDENTITY, not an ordinal. Two different digests are each "a new image"
    relative to the other, so `newer` in both directions is correct there and
    asserting universal reversal would be wrong.
    """

    ORDINAL = [
        ("1.0.0", "2.0.0", "npm"),
        ("1.0.0", "2.0.0", None),
        ("1.0.0-rc1", "1.0.0", "npm"),
        ("1.0.0-1", "1.0.0", "npm"),
        ("0.0.1", "10.0.0", "npm"),
        ("202612180000", "202612190000", None),
        ("1.0.0", "1.0.1", "pypi"),
    ]

    IDENTITY = [
        ("abcdef123456", "abcdef123457", "docker"),
        ("987654321098", "123456789012", "docker"),
    ]

    def test_ordinal_newer_reverses_to_not_newer(self) -> None:
        for current, latest, pkg_type in self.ORDINAL:
            assert compare_versions(current, latest, pkg_type) == "newer", (
                f"{current!r} -> {latest!r} ({pkg_type!r}) should be newer"
            )
            assert compare_versions(latest, current, pkg_type) == "not_newer", (
                f"{latest!r} -> {current!r} ({pkg_type!r}) must reverse to "
                f"not_newer; reporting newer both ways makes the comparison "
                f"meaningless and is undetectable by the corpus alone"
            )

    def test_digest_difference_is_newer_in_both_directions(self) -> None:
        """Pins the exemption, so nobody 'fixes' it into antisymmetry."""
        for current, latest, pkg_type in self.IDENTITY:
            assert compare_versions(current, latest, pkg_type) == "newer"
            assert compare_versions(latest, current, pkg_type) == "newer"

    def test_equal_values_are_not_newer_both_ways(self) -> None:
        for value, pkg_type in [
            ("1.0.0", "npm"),
            ("abcdef123456", "docker"),
            ("202612180000", None),
        ]:
            assert compare_versions(value, value, pkg_type) == "not_newer"
