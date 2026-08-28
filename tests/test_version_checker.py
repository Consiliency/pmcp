"""Tests for package version checking functionality."""

from __future__ import annotations

import ast
import asyncio
import contextlib
import copy
import importlib.util
import io
import json
import pathlib
import sys
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pmcp.manifest import version_checker
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
from pmcp.manifest.npm_resolver import NpmResolution, get_resolver


def detect(
    command: str,
    args: list[str],
    env: dict[str, str] | None = None,
    cwd: str | None = None,
) -> tuple[str, str | None]:
    """`detect_package_type` with the identity inputs defaulted, for brevity.

    The defaults live HERE and never in production: `detect_package_type`'s
    `env`/`cwd` are undefaulted precisely so an unconverted production call site
    is a type error. A test that means "this server declares no env overlay and
    no cwd" is stating a fact, not forgetting an argument.
    """
    return detect_package_type(command, args, env, cwd)


class _UnavailableResolver:
    """Stands in for the resolver on a host with no node and no npm.

    Forcing the fallback by patching the resolver rather than by mangling
    `PATH` keeps the fixture deterministic: `PATH` state is a property of the
    machine the suite happens to run on, and a fixture that depends on it tests
    a different thing on every host.
    """

    def resolve(
        self,
        command: str,
        args: list[str],
        env: object,
        cwd: str | None,
    ) -> NpmResolution:
        return NpmResolution(status="UNAVAILABLE", reason="test: node-less host")


class _NpmIdentityPath:
    """One of the two production paths through `_npm_package_arg`."""

    def __init__(self, name: str) -> None:
        self.name = name

    @property
    def resolver_active(self) -> bool:
        return self.name == "resolver"

    def detect(
        self,
        command: str,
        args: list[str],
        env: dict[str, str] | None = None,
        cwd: str | None = None,
    ) -> tuple[str, str | None]:
        return detect_package_type(command, args, env, cwd)

    def expect(
        self,
        *,
        resolver: tuple[str, str | None],
        tables: tuple[str, str | None],
    ) -> tuple[str, str | None]:
        """The expected answer for the path under test.

        Used only where the two paths genuinely differ. Spelling BOTH sides at
        the call site is the point: it makes each divergence a reviewed,
        documented fact rather than an implicit "whatever the code does".
        """
        return resolver if self.resolver_active else tables


@pytest.fixture(params=["resolver", "tables"])
def npm_path(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> _NpmIdentityPath:
    """Run an npm identity assertion under BOTH production paths.

    Consiliency/pmcp#195. npm identity now comes from npm's own parser where
    npm is installed, and from the flag tables where it is not. Both are
    production; an earlier draft moved the whole #182/#183 regression set onto
    the fallback fixture, which would have stopped covering production on every
    node-ful host -- i.e. on every host that matters.

    Where the two paths give different answers they are asserted separately, and
    the difference is always in the same direction: the resolver refuses where
    the tables guess.
    """
    if request.param == "tables":
        monkeypatch.setattr(
            version_checker, "get_resolver", lambda: _UnavailableResolver()
        )
        return _NpmIdentityPath("tables")

    probe = get_resolver().resolve("npx", ["-y", "left-pad"], {}, None)
    if probe.is_unavailable:
        # No node/npm here. Genuinely untestable, not a soft failure.
        pytest.skip(f"npm resolver unavailable on this host: {probe.reason}")
    if not probe.is_identity:
        # REFUSED on the plainest possible form means the drift tripwire fired:
        # an unrecognised `npx-cli.js`, a failed self-test, or a parser that
        # would not load. That is the tripwire WORKING, and it must be loud --
        # skipping here would hide a host on which npm identity is disabled.
        pytest.fail(
            f"the npm resolver refused `npx -y left-pad`: {probe.reason}. "
            "npm identity is disabled on this host; investigate rather than "
            "skip."
        )
    return _NpmIdentityPath("resolver")


class TestDetectPackageType:
    """Tests for detect_package_type function."""

    def test_npx_simple_package(self, npm_path: _NpmIdentityPath) -> None:
        """Test detection of simple npx package."""
        pkg_type, pkg_name = npm_path.detect("npx", ["-y", "playwright-mcp"])
        assert pkg_type == "npm"
        assert pkg_name == "playwright-mcp"

    def test_npx_scoped_package(self, npm_path: _NpmIdentityPath) -> None:
        """Test detection of scoped npm package."""
        pkg_type, pkg_name = npm_path.detect("npx", ["-y", "@playwright/mcp"])
        assert pkg_type == "npm"
        assert pkg_name == "@playwright/mcp"

    def test_npx_package_with_latest(self, npm_path: _NpmIdentityPath) -> None:
        """Test detection strips @latest suffix."""
        pkg_type, pkg_name = npm_path.detect("npx", ["-y", "some-package@latest"])
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
        self,
        arg: str,
        expected: str,
        npm_path: _NpmIdentityPath,
    ) -> None:
        pkg_type, pkg_name = npm_path.detect("npx", ["-y", arg])
        assert pkg_type == "npm"
        assert pkg_name == expected

    def test_npx_without_y_flag(self, npm_path: _NpmIdentityPath) -> None:
        """Test detection works without -y flag."""
        pkg_type, pkg_name = npm_path.detect("npx", ["my-mcp-server"])
        assert pkg_type == "npm"
        assert pkg_name == "my-mcp-server"

    def test_npm_command(self, npm_path: _NpmIdentityPath) -> None:
        """`npm` reads its package from an allowlisted subcommand's operand.

        This test previously asserted `npm -y server-pkg` -> `server-pkg`.
        That is not a legal npm invocation -- npm requires a subcommand -- and
        the assertion encoded the parser's old permissiveness: the first
        non-flag token was taken as a package whatever it was, which is the
        Consiliency/pmcp#183 hazard (`npm run mcp` -> package `mcp`, then
        installed and executed via `npx -y`). A bare token now fails closed;
        the real form is exercised here instead.
        """
        pkg_type, pkg_name = npm_path.detect("npm", ["exec", "-y", "server-pkg"])
        assert pkg_type == "npm"
        assert pkg_name == "server-pkg"

    def test_npm_without_a_subcommand_is_not_a_package(
        self, npm_path: _NpmIdentityPath
    ) -> None:
        """A bare `npm <token>` is not legal npm, so no identity is claimed."""
        assert npm_path.detect("npm", ["-y", "server-pkg"]) == ("unknown", None)

    def test_uvx_simple_package(self) -> None:
        """Test detection of uvx (PyPI) package."""
        pkg_type, pkg_name = detect("uvx", ["mcp-server-git"])
        assert pkg_type == "pypi"
        assert pkg_name == "mcp-server-git"

    def test_uvx_with_flags(self) -> None:
        """Test uvx detection skips flags."""
        pkg_type, pkg_name = detect("uvx", ["--quiet", "my-package", "--arg"])
        assert pkg_type == "pypi"
        assert pkg_name == "my-package"

    def test_unknown_command(self) -> None:
        """Test unknown command returns unknown type."""
        pkg_type, pkg_name = detect("python", ["-m", "mymodule"])
        assert pkg_type == "unknown"
        assert pkg_name is None

    def test_docker_command(self) -> None:
        """Test docker command detects image name."""
        pkg_type, pkg_name = detect("docker", ["run", "myimage"])
        assert pkg_type == "docker"
        assert pkg_name == "myimage"

    def test_docker_run_with_flags(self) -> None:
        """Test docker run strips flags and finds image."""
        pkg_type, pkg_name = detect(
            "docker", ["run", "-i", "--rm", "mcp/server:latest"]
        )
        assert pkg_type == "docker"
        assert pkg_name == "mcp/server"

    def test_docker_run_with_env_flag(self) -> None:
        """Test docker run skips -e VALUE and finds image."""
        pkg_type, pkg_name = detect(
            "docker", ["run", "-e", "KEY=val", "--rm", "ghcr.io/org/mcp"]
        )
        assert pkg_type == "docker"
        assert pkg_name == "ghcr.io/org/mcp"

    def test_cargo_run_with_package_flag(self) -> None:
        """Test cargo run -p package detects package."""
        pkg_type, pkg_name = detect("cargo", ["run", "-p", "my-mcp-server"])
        assert pkg_type == "cargo"
        assert pkg_name == "my-mcp-server"

    def test_cargo_run_with_bin_flag(self) -> None:
        """Test cargo run --bin binary detects binary name."""
        pkg_type, pkg_name = detect("cargo", ["run", "--bin", "mcp-binary"])
        assert pkg_type == "cargo"
        assert pkg_name == "mcp-binary"

    def test_cargo_install(self) -> None:
        """Test cargo install package detects package."""
        pkg_type, pkg_name = detect("cargo", ["install", "mcp-tool"])
        assert pkg_type == "cargo"
        assert pkg_name == "mcp-tool"

    def test_pip_install(self) -> None:
        """Test pip install detects PyPI package."""
        pkg_type, pkg_name = detect("pip", ["install", "mcp-server-git"])
        assert pkg_type == "pypi"
        assert pkg_name == "mcp-server-git"

    def test_pip3_install_upgrade(self) -> None:
        """Test pip3 install --upgrade detects package."""
        pkg_type, pkg_name = detect("pip3", ["install", "--upgrade", "my-mcp-server"])
        assert pkg_type == "pypi"
        assert pkg_name == "my-mcp-server"

    def test_empty_args(self, npm_path: _NpmIdentityPath) -> None:
        """Test npx with empty args."""
        pkg_type, pkg_name = npm_path.detect("npx", [])
        assert pkg_type == "unknown"
        assert pkg_name is None

    def test_only_flags(self, npm_path: _NpmIdentityPath) -> None:
        """Test npx with only flags."""
        pkg_type, pkg_name = npm_path.detect("npx", ["-y", "--quiet"])
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
        old = detect("docker", ["run", "registry:5000/old-image"])
        new = detect("docker", ["run", "registry:5000/new-image"])
        assert old != new, (
            "two different images on one host:port registry collapsed to a "
            f"single identity: {old} == {new}"
        )
        assert old == ("docker", "registry:5000/old-image")
        assert new == ("docker", "registry:5000/new-image")

    def test_npm_exec_distinguishes_packages(self, npm_path: _NpmIdentityPath) -> None:
        """`npm exec A` and `npm exec B` are different packages.

        RED before the fix: with no subcommand skip the first non-flag token
        was `exec`, so both returned `("npm", "exec")`.
        """
        old = npm_path.detect("npm", ["exec", "old-pkg"])
        new = npm_path.detect("npm", ["exec", "new-pkg"])
        assert old != new, f"two different npm exec packages collapsed: {old} == {new}"
        assert old == ("npm", "old-pkg")
        assert new == ("npm", "new-pkg")

    def test_npm_x_alias_distinguishes_packages(
        self, npm_path: _NpmIdentityPath
    ) -> None:
        """`x` is npm's documented `exec` alias and must skip identically."""
        old = npm_path.detect("npm", ["x", "old-pkg"])
        new = npm_path.detect("npm", ["x", "new-pkg"])
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
        a = detect("uvx", ["--python", "3.12", "pkg-a"])
        b = detect("uvx", ["--python", "3.12", "pkg-b"])
        assert a != b, f"`--python`'s value collapsed two packages: {a} == {b}"
        assert a == ("pypi", "pkg-a")
        assert b == ("pypi", "pkg-b")

    def test_uvx_with_dependency_is_not_the_package(self) -> None:
        """RED before: both returned `("pypi", "requests")` -- an injected
        dependency named by `--with`, not the server being run."""
        a = detect("uvx", ["--with", "requests", "srv-a"])
        b = detect("uvx", ["--with", "requests", "srv-b"])
        assert a != b, f"`--with`'s value collapsed two packages: {a} == {b}"
        assert a == ("pypi", "srv-a")
        assert b == ("pypi", "srv-b")

    def test_pip_index_url_is_not_the_package(self) -> None:
        """RED before: both returned `("pypi", "https://x")` -- an index URL
        as a package name, which no registry lookup could ever resolve."""
        a = detect("pip", ["install", "--index-url", "https://x", "pkg-a"])
        b = detect("pip", ["install", "--index-url", "https://x", "pkg-b"])
        assert a != b, f"`--index-url`'s value collapsed two packages: {a} == {b}"
        assert a == ("pypi", "pkg-a")
        assert b == ("pypi", "pkg-b")

    def test_cargo_features_is_not_the_crate(self) -> None:
        """RED before: both returned `("cargo", "full")` -- a feature name."""
        a = detect("cargo", ["run", "--features", "full", "srv-a"])
        b = detect("cargo", ["run", "--features", "full", "srv-b"])
        assert a != b, f"`--features`'s value collapsed two crates: {a} == {b}"
        assert a == ("cargo", "srv-a")
        assert b == ("cargo", "srv-b")

    def test_docker_env_file_is_not_the_image(self) -> None:
        """RED before: both returned `("docker", ".env")`.

        `--env-file` was simply missing from `_docker_image_arg`'s value-flag
        table -- the most careful branch in the file, and still incomplete.
        """
        a = detect("docker", ["run", "--env-file", ".env", "img-a"])
        b = detect("docker", ["run", "--env-file", ".env", "img-b"])
        assert a != b, f"`--env-file`'s value collapsed two images: {a} == {b}"
        assert a == ("docker", "img-a")
        assert b == ("docker", "img-b")

    def test_docker_mount_spec_is_not_the_image(self) -> None:
        """RED before: both returned `("docker", "type=bind,src=/a")`."""
        a = detect("docker", ["run", "--mount", "type=bind,src=/a", "img-a"])
        b = detect("docker", ["run", "--mount", "type=bind,src=/a", "img-b"])
        assert a != b, f"`--mount`'s value collapsed two images: {a} == {b}"
        assert a == ("docker", "img-a")
        assert b == ("docker", "img-b")

    def test_npm_exec_package_flag_names_the_package(
        self, npm_path: _NpmIdentityPath
    ) -> None:
        """RED before: both returned `("npm", "bin")` -- the BINARY.

        The seventh collision, and the only one of the seven where identity is
        genuinely RECOVERABLE rather than merely refusable: `--package` is
        known-positive, so its value IS the package. Treating `--package=old`
        as merely "self-delimiting, so keep scanning" still returns `bin`;
        the scanner has to read the value back OUT of the flag.
        """
        a = npm_path.detect("npm", ["exec", "--package=old", "--", "bin"])
        b = npm_path.detect("npm", ["exec", "--package=new", "--", "bin"])
        assert a != b, f"two packages exposing one binary collapsed: {a} == {b}"
        assert a == ("npm", "old")
        assert b == ("npm", "new")

    def test_npm_exec_package_flag_spaced_spelling(
        self, npm_path: _NpmIdentityPath
    ) -> None:
        """npm documents both `--package=<pkg>` and `--package <pkg>`.

        **This case does not discriminate the fix, and is kept as a plain
        regression guard rather than as evidence.** Mutation-proved: with
        `--package` removed from the known-positive set entirely, the spaced
        spelling still resolves correctly, because skipping `--package` as an
        ordinary flag leaves its value as the first bare token anyway. Only
        the `=` spelling above and the repeated-flag refusal below actually
        pin the extraction.
        """
        a = npm_path.detect("npm", ["exec", "--package", "old", "--", "bin"])
        b = npm_path.detect("npm", ["exec", "--package", "new", "--", "bin"])
        assert a != b, f"spaced `--package` collapsed two packages: {a} == {b}"
        assert a == ("npm", "old")
        assert b == ("npm", "new")

    def test_npm_repeated_package_flags_refuse(
        self, npm_path: _NpmIdentityPath
    ) -> None:
        """npm allows `--package` more than once; several are not an identity.

        One package is an identity. Two DIFFERENT ones are not, and returning
        the first would be exactly the guess this change exists to stop -- the
        two configs would then confirm as one package on the strength of a
        coin flip. Repeating the SAME package is still an identity.
        """
        assert npm_path.detect(
            "npm", ["exec", "--package", "a", "--package", "b", "--", "bin"]
        ) == ("unknown", None)
        assert npm_path.detect(
            "npm", ["exec", "--package", "a", "--package", "a", "--", "bin"]
        ) == ("npm", "a")


class TestDirectReferencesStayDistinct:
    """A PEP 508 *direct reference* names a source, and sources differ.

    `pkg @ git+https://x/y` and `pkg @ git+https://x/z` are different
    repositories. An earlier form of `_pep508_base_name` listed `@` among the
    name terminators, so both truncated to `pkg` -- collapsing two repos into
    one identity, which the gate would then CONFIRM. That is the exact defect
    class this change exists to close, newly introduced by the fix for it
    (ah board review, red-team seat).

    Verified: before the fix both resolved to `('pypi', 'pkg')`.
    """

    def test_named_direct_references_to_different_repos_are_different(self) -> None:
        y = detect("uvx", ["--from", "pkg @ git+https://x/y", "tool"])
        z = detect("uvx", ["--from", "pkg @ git+https://x/z", "tool"])
        assert y != z, f"two different repositories collapsed to one identity: {y}"
        assert y == ("pypi", "pkg @ git+https://x/y")

    def test_normalization_still_strips_extras_and_versions(self) -> None:
        """The `@` carve-out must not disable the rest of the rule."""
        assert detect("uvx", ["--from", "browser-use[cli]", "t"]) == (
            "pypi",
            "browser-use",
        )
        assert detect("uvx", ["--from", "index-it-mcp==1.2.0", "t"]) == (
            "pypi",
            "index-it-mcp",
        )


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
        assert detect("uvx", ["--from", "--offline", "pkg"]) == (
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
        assert detect("uvx", ["--from"]) == ("unknown", None)
        assert detect("cargo", ["run", "--bin"]) == ("unknown", None)

    def test_positive_flag_with_an_empty_attached_value_refuses(self) -> None:
        """`--from=` attaches an empty value, which is not a package name."""
        assert detect("uvx", ["--from=", "pkg"]) == ("unknown", None)

    def test_cargo_shares_the_same_guard_arms(self) -> None:
        """cargo's `-p`/`--bin` run through the same branch."""
        assert detect("cargo", ["run", "-p", "--offline"]) == (
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
        assert detect("uvx", ["--not-a-real-uv-flag", "pkg"]) == (
            "unknown",
            None,
        )

    def test_pip_unlisted_flag_refuses(self) -> None:
        assert detect("pip", ["install", "--not-a-real-pip-flag", "p"]) == (
            "unknown",
            None,
        )

    def test_cargo_unlisted_flag_refuses(self) -> None:
        assert detect("cargo", ["run", "--not-a-real-cargo-flag", "s"]) == (
            "unknown",
            None,
        )

    def test_docker_unlisted_flag_refuses(self) -> None:
        assert detect("docker", ["run", "--not-a-real-docker-flag", "i"]) == (
            "unknown",
            None,
        )

    def test_unlisted_flag_with_attached_value_is_self_delimiting(self) -> None:
        """`--unlisted=value` is SAFE to skip and must not refuse.

        The hazard an unlisted flag creates is that it might consume the next
        token. A `--flag=value` spelling carries its value inside the token,
        so it cannot; refusing here would cost auto-update for no safety gain.
        """
        assert detect("uvx", ["--not-a-real-uv-flag=x", "pkg"]) == (
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
        a = detect("uvx", ["--not-a-real-uv-flag", "pkg-a"])
        b = detect("uvx", ["--not-a-real-uv-flag", "pkg-b"])
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
        assert detect("uvx", ["--quiet", "my-package", "--arg"]) == (
            "pypi",
            "my-package",
        )

    def test_docker_short_boolean_flags_still_find_image(self) -> None:
        assert detect("docker", ["run", "-i", "--rm", "mcp/server:latest"]) == (
            "docker",
            "mcp/server",
        )

    def test_docker_env_flag_still_finds_image(self) -> None:
        assert detect(
            "docker", ["run", "-e", "KEY=val", "--rm", "ghcr.io/org/mcp"]
        ) == ("docker", "ghcr.io/org/mcp")

    def test_docker_combined_short_booleans_still_find_image(self) -> None:
        """`-it` is one token docker never documents; it must be listed by hand."""
        assert detect("docker", ["run", "-it", "--rm", "img"]) == (
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
        assert detect("uvx", ["mypkg", "--from", "other"]) == (
            "pypi",
            "mypkg",
        )

    def test_known_positive_forms_unchanged(self, npm_path: _NpmIdentityPath) -> None:
        assert detect("uvx", ["--from", "pkg", "tool"]) == ("pypi", "pkg")
        assert detect("cargo", ["run", "-p", "pkg"]) == ("cargo", "pkg")
        assert detect("cargo", ["run", "--bin", "b"]) == ("cargo", "b")
        assert detect("pip", ["install", "pkg"]) == ("pypi", "pkg")
        assert npm_path.detect("npx", ["-y", "pkg"]) == ("npm", "pkg")
        assert detect("docker", ["run", "img"]) == ("docker", "img")

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
        assert detect("uvx", ["--from", "pkg", "tool", "--", "--from", "x"]) == (
            "pypi",
            "pkg",
        )
        assert detect("uvx", ["--", "--python", "3.12", "x"]) == (
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
        assert detect(
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
        assert detect("uvx", ["--from", "browser-use[cli]", "browser-use"]) == (
            "pypi",
            "browser-use",
        )
        assert detect("uvx", ["--from", "index-it-mcp==1.2.0", "x"]) == (
            "pypi",
            "index-it-mcp",
        )
        assert detect("uvx", ["--from", "pkg>=1.0", "x"]) == (
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
        a = detect("uvx", ["--from", "git+https://x/y", "tool"])
        b = detect("uvx", ["--from", "git+https://x/z", "tool"])
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
        assert detect("uvx", ["pkg==1.2.3"]) == ("pypi", "pkg==1.2.3")


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


class TestNpmBoardRoundTwo:
    """Two collisions the first implementation left open, found by the board.

    Both verified against npm's own parser (`nopt` + npm's type map), not
    against the type strings, and both were live: `_same_package` confirms the
    false identity in each case.
    """

    def test_nullable_boolean_consumes_a_literal_null(
        self, npm_path: _NpmIdentityPath
    ) -> None:
        """`--yes null A` -- npm consumes `null` as the flag's value.

        The generator drops `null` from a type list (correctly -- it means
        "unset"), but the SCANNER must still consume a literal `null` token
        after a nullable boolean, exactly as it does `true`/`false`. Without
        it both forms resolved to the package `null`. Affects `--yes`,
        `--optional`, `--production`, `--workspaces`, `--expect-results`.
        """
        a = npm_path.detect("npm", ["exec", "--yes", "null", "A"])
        b = npm_path.detect("npm", ["exec", "--yes", "null", "B"])
        assert a != b, f"nullable boolean swallowed the package: {a}"
        assert a == ("npm", "A")
        assert b == ("npm", "B")

    def test_attached_baked_value_shorthand_yields_its_baked_value(
        self, npm_path: _NpmIdentityPath
    ) -> None:
        """`--silent=true X` is NOT `--silent X`.

        npm expands the shorthand and the ATTACHED value becomes a positional:
        nopt leaves `["true", "X"]`, so the package is `true`, not `X`.
        Reading it as `X` collapsed `--silent=true X` and `--silent=false X`
        into one identity despite naming different packages.
        """
        t = npm_path.detect("npm", ["exec", "--silent=true", "X"])
        f = npm_path.detect("npm", ["exec", "--silent=false", "X"])
        # `--silent` is npm's shorthand for `--loglevel silent`, so npm's own
        # parser reports a `loglevel` config key. That key is outside the
        # step-1 allowlist -- it cannot redirect resolution, but the allowlist
        # is an allowlist of PLAIN things, not a denylist of dangerous ones,
        # and three board rounds on a denylist each found another way for a
        # confident answer to be wrong. The resolver therefore refuses; the
        # tables keep their 2.5.2 answer. Both are safe: refusing costs
        # auto-update for one unusual launch form, and no npm-family server in
        # manifest.yaml carries a leading shorthand.
        assert t == npm_path.expect(resolver=("unknown", None), tables=("npm", "true"))
        assert f == npm_path.expect(resolver=("unknown", None), tables=("npm", "false"))
        # The property under test holds on BOTH paths: the two forms name
        # different packages, so they must never confirm each other. Two
        # `("unknown", None)`s are equal but `_same_package` reads "unknown" as
        # unidentified, so they still cannot confirm.
        assert t != f or t == ("unknown", None)

    def test_spaced_baked_value_shorthand_still_consumes_nothing(
        self, npm_path: _NpmIdentityPath
    ) -> None:
        """The spaced form is unchanged -- this is the distinction, not a fix.

        `--silent` bakes its value in, so it consumes nothing and the next
        token IS the package; `--global` is a real boolean that swallows a
        literal `true`.
        """
        # `--silent` is npm's shorthand for `--loglevel silent`, so npm's own
        # parser reports a `loglevel` config key. That key is outside the
        # step-1 allowlist -- it cannot redirect resolution, but the allowlist
        # is an allowlist of PLAIN things, not a denylist of dangerous ones,
        # and three board rounds on a denylist each found another way for a
        # confident answer to be wrong. The resolver therefore refuses; the
        # tables keep their 2.5.2 answer. Both are safe: refusing costs
        # auto-update for one unusual launch form, and no npm-family server in
        # manifest.yaml carries a leading shorthand.
        assert npm_path.detect("npm", ["--silent", "exec", "pkg"]) == npm_path.expect(
            resolver=("unknown", None), tables=("npm", "pkg")
        )
        assert npm_path.detect("npx", ["--silent", "true", "arg"]) == npm_path.expect(
            resolver=("unknown", None), tables=("npm", "true")
        )
        # `--global` is a real npm config key, and equally outside the allowlist.
        assert npm_path.detect(
            "npm", ["exec", "--global", "true", "a"]
        ) == npm_path.expect(resolver=("unknown", None), tables=("npm", "a"))


class TestNpmSubcommandSkipFiresOnce:
    """The npm subcommand skip must not eat real package names.

    `i` and `exec` are genuine npm packages, so a skip that fired repeatedly --
    the shape the docker branch uses for its own subcommands -- would resolve
    these to `None`. And `npx` takes a package directly, so it must not skip
    at all.
    """

    def test_npm_install_of_a_package_named_i(self, npm_path: _NpmIdentityPath) -> None:
        assert npm_path.detect("npm", ["install", "i"]) == ("npm", "i")

    def test_npm_exec_of_a_package_named_exec(self, npm_path: _NpmIdentityPath) -> None:
        assert npm_path.detect("npm", ["exec", "exec"]) == ("npm", "exec")

    def test_npx_does_not_skip_a_package_named_exec(
        self, npm_path: _NpmIdentityPath
    ) -> None:
        assert npm_path.detect("npx", ["-y", "exec"]) == ("npm", "exec")

    def test_npm_without_a_subcommand_yields_no_identity(
        self, npm_path: _NpmIdentityPath
    ) -> None:
        """Renamed and inverted from ..._still_finds_the_package.

        `npm -y server-pkg` is not a legal npm invocation, and treating its
        first token as a package is the Consiliency/pmcp#183 hazard in general
        form. The allowlist now requires a recognised subcommand before
        anything is read as a package.
        """
        assert npm_path.detect("npm", ["-y", "server-pkg"]) == ("unknown", None)

    def test_npm_run_names_a_script_not_a_package(
        self, npm_path: _NpmIdentityPath
    ) -> None:
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
        assert npm_path.detect("npm", ["run", "mcp"]) == ("unknown", None)
        assert npm_path.detect("npm", ["run", "start"]) == ("unknown", None)
        # ...including when a global flag precedes the subcommand.
        assert npm_path.detect("npm", ["--silent", "run", "mcp"]) == (
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
        self,
        args: list[str],
        npm_path: _NpmIdentityPath,
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
        assert npm_path.detect("npm", args) == ("unknown", None)

    def test_npm_create_operand_is_not_the_package_npm_would_run(
        self, npm_path: _NpmIdentityPath
    ) -> None:
        """`npm create foo` resolves to the package `create-foo`, not `foo`.

        Reporting `foo` names a DIFFERENT package than the one npm runs, which
        is the same wrong-identity hazard as the script case.
        """
        assert npm_path.detect("npm", ["create", "foo"]) == ("unknown", None)

    def test_npx_can_still_run_packages_named_run_or_create(
        self, npm_path: _NpmIdentityPath
    ) -> None:
        """The refusal is npm-subcommand-scoped, not a name blocklist.

        `npx -y run` names a real registry package called `run`; nothing about
        Consiliency/pmcp#183 should make that unresolvable.
        """
        assert npm_path.detect("npx", ["-y", "run"]) == ("npm", "run")
        assert npm_path.detect("npx", ["-y", "create"]) == ("npm", "create")
        # And the operand position is still a package for the other
        # subcommands -- only `run`/`create` name something else.
        assert npm_path.detect("npm", ["exec", "run"]) == ("npm", "run")

    def test_a_leading_flag_does_not_consume_the_subcommand_skip(
        self, npm_path: _NpmIdentityPath
    ) -> None:
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
        old = npm_path.detect("npm", ["--silent", "exec", "old-pkg"])
        new = npm_path.detect("npm", ["--silent", "exec", "new-pkg"])
        # Outside the resolver's step-1 allowlist: npm's own parser reports a
        # config key other than `yes`/`package`, so with npm installed this
        # refuses. The tables keep their 2.5.2 answer for a node-less host.
        assert old == npm_path.expect(
            resolver=("unknown", None), tables=("npm", "old-pkg")
        )
        assert new == npm_path.expect(
            resolver=("unknown", None), tables=("npm", "new-pkg")
        )
        # The property survives on both paths: two refusals are equal, but
        # `_same_package` reads "unknown" as unidentified, so they cannot
        # confirm each other.
        assert old != new or old == ("unknown", None)


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
            version, pkg_type = await get_package_version(
                "npx", ["-y", "my-package"], None, None
            )
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
            version, pkg_type = await get_package_version(
                "uvx", ["my-package"], None, None
            )
            assert version == "2.0.0"
            assert pkg_type == "pypi"

    @pytest.mark.asyncio
    async def test_unknown_package(self) -> None:
        """Test unknown package type returns unknown."""
        version, pkg_type = await get_package_version(
            "python", ["-m", "mymodule"], None, None
        )
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
            version, pkg_type = await get_package_version(
                "npx", ["-y", "my-package"], None, None
            )
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
                "cargo", ["run", "-p", "my-crate"], None, None
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
                "docker", ["run", "-i", "--rm", "mcp/server:latest"], None, None
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
                "pip", ["install", "my-mcp-server"], None, None
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


class TestNpmValueFlagCollisions:
    """Two different npm servers must never collapse into one identity.

    npm was the last ecosystem still failing OPEN on an unrecognised flag
    (Consiliency/pmcp#180): the scan skipped anything starting `-` and took
    the next bare token, so a flag's VALUE became the package name. v2.4.0's
    gate reads a matching identity as a POSITIVE confirmation, so this served
    one server the other's tool descriptions.

    The last two pairs are the ones a naive boolean/value split misses, and
    they are the point of this class. A board seat implemented an earlier
    design faithfully, and its suite passed -- all three registry/loglevel
    pairs green, pinned orderings green, fails-closed green -- while
    `npm exec --color always a/b` still returned `('npm','always')` for both.
    An acceptance set that cannot see the defect its own method manufactures
    is not an acceptance set.

    **Every one of these flags is outside the resolver's step-1 allowlist**, so
    with npm installed the answer is `("unknown", None)` for both members of
    each pair rather than two distinct names. The property this class exists to
    pin -- two different servers never confirm as one package -- holds either
    way: `_same_package` reads `"unknown"` as unidentified, so two refusals
    cannot confirm each other. The table answers are pinned as the node-less
    behaviour, unchanged from 2.5.2.
    """

    @pytest.mark.parametrize(
        ("command", "args_a", "args_b", "expected", "resolver_expected"),
        [
            # A value flag whose value is an enum member.
            (
                "npm",
                ["exec", "--loglevel", "silly", "a"],
                ["exec", "--loglevel", "silly", "b"],
                [("npm", "a"), ("npm", "b")],
                [("unknown", None), ("unknown", None)],
            ),
            # A value flag whose value is a URL.
            (
                "npm",
                ["exec", "--registry", "https://r", "a"],
                ["exec", "--registry", "https://r", "b"],
                [("npm", "a"), ("npm", "b")],
                [("unknown", None), ("unknown", None)],
            ),
            # Same, reached through npx, where there is no subcommand to skip.
            (
                "npx",
                ["-y", "--registry", "https://r", "a"],
                ["-y", "--registry", "https://r", "b"],
                [("npm", "a"), ("npm", "b")],
                [("unknown", None), ("unknown", None)],
            ),
            # A BOOLEAN flag followed by a literal `true`/`false`. npm's
            # parser takes that as the flag's value -- `npm exec --global
            # false --help` exits 0 -- so a design where "boolean consumes
            # nothing" leaves this colliding on `false`.
            (
                "npm",
                ["exec", "--global", "false", "a"],
                ["exec", "--global", "false", "b"],
                [("npm", "a"), ("npm", "b")],
                [("unknown", None), ("unknown", None)],
            ),
            # A Boolean UNION (`color` is `always|Boolean`): arity depends on
            # the next token's content, so no single class is right and the
            # flag is left unlisted. Refusing is the fail-CLOSED direction;
            # what matters is that the two no longer share an identity.
            (
                "npm",
                ["exec", "--color", "always", "a"],
                ["exec", "--color", "always", "b"],
                [("unknown", None), ("unknown", None)],
                [("unknown", None), ("unknown", None)],
            ),
        ],
    )
    def test_pair_does_not_collide(
        self,
        command: str,
        args_a: list[str],
        args_b: list[str],
        expected: list[tuple[str, str | None]],
        resolver_expected: list[tuple[str, str | None]],
        npm_path: _NpmIdentityPath,
    ) -> None:
        want = resolver_expected if npm_path.resolver_active else expected
        got_a = npm_path.detect(command, args_a)
        got_b = npm_path.detect(command, args_b)
        # Pin the EXACT values, not just inequality: a pair can stop colliding
        # by both becoming wrong in different ways.
        assert [got_a, got_b] == want
        assert got_a != got_b or got_a == ("unknown", None)


class TestNpmFailsClosed:
    """An unlisted npm flag yields no identity rather than a wrong one."""

    def test_unlisted_bare_flag_refuses(self, npm_path: _NpmIdentityPath) -> None:
        assert npm_path.detect("npm", ["exec", "--not-a-real-npm-flag", "a"]) == (
            "unknown",
            None,
        )

    def test_unlisted_flag_cannot_collide(self, npm_path: _NpmIdentityPath) -> None:
        args = ["exec", "--not-a-real-npm-flag", "{}"]
        assert npm_path.detect("npm", [*args, "a"]) == ("unknown", None)
        assert npm_path.detect("npm", [*args, "b"]) == ("unknown", None)

    def test_self_delimiting_unlisted_flag_still_resolves(
        self, npm_path: _NpmIdentityPath
    ) -> None:
        """`--flag=value` cannot swallow the next token, so refusing costs
        safety nothing and auto-update something."""
        assert npm_path.detect("npm", ["exec", "--zzz=1", "a"]) == npm_path.expect(
            # npm's parser reports an unknown flag as a boolean config key, and
            # the allowlist admits only `yes`/`package` -- so `--zzz=1` refuses
            # with npm installed. Cheap: nothing in manifest.yaml carries one.
            resolver=("unknown", None),
            tables=("npm", "a"),
        )

    def test_conditional_arity_flag_refuses(self, npm_path: _NpmIdentityPath) -> None:
        """`--color` is `always|Boolean`; unlisted by construction."""
        assert npm_path.detect("npm", ["exec", "--color", "a"]) == (
            "unknown",
            None,
        )


class TestNpmPinnedOrderingSurvives:
    """The forms the fix must not regress.

    `npm --silent exec pkg` gets called out because `--silent` is a SHORTHAND
    (`--loglevel silent`), absent from `npm config list --json` entirely. Any
    table built from the config dump rather than from `shorthands` breaks
    exactly here, which makes it the single most likely regression.

    Each row carries BOTH answers. Where they differ the resolver refuses,
    because npm's own parser reports a config key outside the step-1 allowlist
    (`--silent`/`-q`/`-s` -> `loglevel`, `-g`/`--local` -> `global`,
    `--reg` -> `registry`, `-w` -> `workspace`, `--loglevel` -> `loglevel`).
    That is the allowlist working as designed: it admits the plain shape all 79
    npm-family manifest servers use and refuses everything else rather than
    modelling it.
    """

    @pytest.mark.parametrize(
        ("command", "args", "expected", "resolver_expected"),
        [
            ("npm", ["--silent", "exec", "pkg"], "pkg", None),
            ("npm", ["exec", "pkg"], "pkg", "pkg"),
            ("npm", ["install", "i"], "i", "i"),
            ("npm", ["exec", "exec"], "exec", "exec"),
            ("npx", ["-y", "exec"], "exec", "exec"),
            ("npx", ["-y", "pkg"], "pkg", "pkg"),
        ],
    )
    def test_form_still_resolves(
        self,
        command: str,
        args: list[str],
        expected: str,
        resolver_expected: str | None,
        npm_path: _NpmIdentityPath,
    ) -> None:
        assert npm_path.detect(command, args) == npm_path.expect(
            resolver=("npm", resolver_expected)
            if resolver_expected
            else ("unknown", None),
            tables=("npm", expected),
        )

    @pytest.mark.parametrize(
        ("command", "args", "expected", "resolver_expected"),
        [
            # Every shorthand class, resolved through `shorthands` rather than
            # hand-listed: expansion length >= 2 bakes in a value (boolean
            # arity), length 1 is a rename that inherits the target's arity.
            ("npm", ["-q", "exec", "pkg"], "pkg", None),  # -> --loglevel warn
            ("npm", ["-s", "exec", "pkg"], "pkg", None),  # -> --loglevel silent
            ("npm", ["-g", "exec", "pkg"], "pkg", None),  # -> --global   (boolean)
            ("npm", ["--local", "exec", "pkg"], "pkg", None),  # -> --no-global
            # -> --registry
            ("npm", ["--reg", "https://r", "exec", "pkg"], "pkg", None),
            # -> --workspace (value)
            ("npm", ["exec", "-w", "ws", "pkg"], "pkg", None),
            # `--package` IS on the allowlist, so this one resolves on both.
            ("npx", ["-y", "--package", "pkg", "--", "bin"], "pkg", "pkg"),
        ],
    )
    def test_shorthand_expands(
        self,
        command: str,
        args: list[str],
        expected: str,
        resolver_expected: str | None,
        npm_path: _NpmIdentityPath,
    ) -> None:
        assert npm_path.detect(command, args) == npm_path.expect(
            resolver=("npm", resolver_expected)
            if resolver_expected
            else ("unknown", None),
            tables=("npm", expected),
        )

    @pytest.mark.parametrize(
        ("command", "args", "expected", "resolver_expected"),
        [
            # npm exec's FIRST documented usage: `npm exec -- <pkg> [args...]`.
            # The token after `--` IS the package spec, so `--` must not end
            # the scan the way it does for uvx/pip/cargo/docker. Fail-closed
            # refusal here would be a REGRESSION -- this resolved before #180.
            ("npm", ["exec", "--", "pkg"], "pkg", "pkg"),
            ("npm", ["exec", "--", "pkg", "arg"], "pkg", "pkg"),
            ("npx", ["--", "pkg"], "pkg", "pkg"),
            # `--loglevel` is outside the step-1 allowlist, so the resolver
            # refuses this row while the tables still resolve it.
            ("npm", ["exec", "--loglevel", "silly", "--", "pkg"], "pkg", None),
            # The other documented shape: with `--package` given, the token
            # after `--` is the COMMAND, not a package, and must not win.
            ("npm", ["exec", "--package=pkg", "--", "bin"], "pkg", "pkg"),
            ("npm", ["exec", "--package", "pkg", "--", "bin"], "pkg", "pkg"),
        ],
    )
    def test_double_dash_form_still_resolves(
        self,
        command: str,
        args: list[str],
        expected: str,
        resolver_expected: str | None,
        npm_path: _NpmIdentityPath,
    ) -> None:
        assert npm_path.detect(command, args) == npm_path.expect(
            resolver=("npm", resolver_expected)
            if resolver_expected
            else ("unknown", None),
            tables=("npm", expected),
        )


class TestNpmBakedValueShorthandDoesNotConsume:
    """A baked-value shorthand is NOT a boolean, and the difference is a bug.

    `--silent` expands to `--loglevel silent` before npm parses argv, so by
    then nothing is awaiting a value and it consumes nothing. A real boolean
    like `--global` IS awaiting one and takes a literal `true`/`false`.
    Measured against npm's own parser:

        --silent true TAIL  -> remain ["true","TAIL"]   does NOT consume
        --global true TAIL  -> remain ["TAIL"]          consumes

    Classing them together made `npx --silent true <arg>` report `<arg>` as
    the package when npm's package is literally `true` -- and collapsed
    `--silent true X` with `--silent false X` onto the single identity `X`,
    which is the #180 collapse reintroduced through the fix for it.
    """

    def test_baked_value_shorthand_leaves_true_as_the_package(
        self, npm_path: _NpmIdentityPath
    ) -> None:
        assert npm_path.detect("npx", ["--silent", "true", "arg"]) == npm_path.expect(
            resolver=("unknown", None), tables=("npm", "true")
        )

    def test_baked_value_shorthand_does_not_collapse_true_and_false(
        self, npm_path: _NpmIdentityPath
    ) -> None:
        got_a = npm_path.detect("npx", ["--silent", "true", "X"])
        got_b = npm_path.detect("npx", ["--silent", "false", "X"])
        # Outside the resolver's step-1 allowlist: npm's own parser reports a
        # config key other than `yes`/`package`, so with npm installed this
        # refuses. The tables keep their 2.5.2 answer for a node-less host.
        assert got_a == npm_path.expect(
            resolver=("unknown", None), tables=("npm", "true")
        )
        assert got_b == npm_path.expect(
            resolver=("unknown", None), tables=("npm", "false")
        )
        assert got_a != got_b or got_a == ("unknown", None)

    def test_real_boolean_still_consumes_the_literal(
        self, npm_path: _NpmIdentityPath
    ) -> None:
        """The other side of the split must not regress -- under `npm`.

        This test previously used `npx` and asserted `pkg`, which **pinned the
        wrong behaviour**. Confirmed against the real binary:
        `npx --offline --global true zz` fetches `registry.npmjs.org/true`, so
        the package is `true`. npx-cli.js pre-scans argv and inserts `--`
        before the first positional, so a plain-name boolean switch never
        consumes a literal under npx (ah board review, correctness seat).
        The npx case is now covered by `TestNpxBooleanLiterals` below.
        """
        assert npm_path.detect(
            "npm", ["exec", "--global", "true", "pkg"]
        ) == npm_path.expect(resolver=("unknown", None), tables=("npm", "pkg"))

    def test_baked_value_shorthand_still_skips_an_ordinary_token(
        self, npm_path: _NpmIdentityPath
    ) -> None:
        """Not consuming a literal must not become not skipping at all.

        `--silent` expands to `--loglevel silent`, a config key outside the
        step-1 allowlist, so the resolver refuses where the tables resolve.
        """
        assert npm_path.detect("npm", ["--silent", "exec", "pkg"]) == npm_path.expect(
            resolver=("unknown", None), tables=("npm", "pkg")
        )


class TestNpxBooleanLiterals:
    """npx parses boolean switches differently from npm, so pmcp refuses.

    `bin/npx-cli.js` pre-scans argv and inserts `--` ahead of the first
    positional, so a plain-name boolean switch consumes nothing -- while the
    `--no-` family still does, because the pre-scan does not recognise `no-X`
    as a switch. Rather than model a rule that differs per spelling, refuse:
    a refusal can never mint a wrong identity, and a real config carrying a
    bare `true`/`false`/`null` after a boolean is vanishingly rare.
    """

    def test_npx_refuses_a_literal_after_a_boolean(
        self, npm_path: _NpmIdentityPath
    ) -> None:
        assert npm_path.detect("npx", ["--global", "true", "zz"]) == (
            "unknown",
            None,
        )
        # `npx --yes null zz` is the one case in this file where the RESOLVER
        # resolves and the tables refuse. The npx pre-scan inserts `--` ahead of
        # the first positional, so `--yes` never reaches `null` as a value and
        # `null` -- a real published package -- is what npm runs. The tables
        # refuse here on the blanket "npx + boolean + literal" rule that exists
        # because the `--no-` spelling behaves differently; npm's own parser
        # needs no such rule.
        assert npm_path.detect("npx", ["--yes", "null", "zz"]) == npm_path.expect(
            resolver=("npm", "null"), tables=("unknown", None)
        )

    def test_npx_boolean_without_a_literal_still_resolves(
        self, npm_path: _NpmIdentityPath
    ) -> None:
        """The refusal is scoped to the literal, not to boolean flags."""
        assert npm_path.detect("npx", ["-y", "pkg"]) == ("npm", "pkg")
        assert npm_path.detect("npx", ["-y", "exec"]) == ("npm", "exec")
        assert npm_path.detect("npx", ["--global", "pkg"]) == npm_path.expect(
            # `global` is a real npm config key and outside the allowlist.
            resolver=("unknown", None),
            tables=("npm", "pkg"),
        )


class TestOnlyNullableBooleansConsumeNull:
    """`null` is a real published npm package: 5 definitions, 18 spellings.

    Verified against nopt: `npm exec --yes null zz` leaves `zz` (nullable
    consumes), while `npm exec --global null zz` leaves `null zz` -- so `null`
    is the package. A blanket rule applying `null` to all 198 boolean entries
    minted a wrong identity for the other 180 (ah board review).

    The opposite error shipped in 2.5.1: the table was hand-written with 12 of
    the 18 spellings, so `--y -ws -n --n -no --no` each read the `null` as the
    package. The 5 nullable definitions (`yes`, `optional`, `production`,
    `workspaces`, `expect-results`) reach 18 spellings because `y`, `ws`, `n`
    and `no` are shorthands -- `n` and `no` BOTH expand to `--no-yes` -- and
    every short alias/shorthand key is legal in both `-x` and `--x` form.
    The table is generated now; these pins are what keeps a regeneration
    honest on a host without npm.
    """

    def test_nullable_boolean_consumes_null(self, npm_path: _NpmIdentityPath) -> None:
        assert npm_path.detect("npm", ["exec", "--yes", "null", "zz"]) == (
            "npm",
            "zz",
        )

    def test_non_nullable_boolean_does_not_consume_null(
        self, npm_path: _NpmIdentityPath
    ) -> None:
        assert npm_path.detect(
            "npm", ["exec", "--global", "null", "zz"]
        ) == npm_path.expect(resolver=("unknown", None), tables=("npm", "null"))

    def test_true_and_false_are_consumed_by_any_boolean(
        self, npm_path: _NpmIdentityPath
    ) -> None:
        assert npm_path.detect(
            "npm", ["exec", "--global", "false", "a"]
        ) == npm_path.expect(resolver=("unknown", None), tables=("npm", "a"))

    def test_baked_value_shorthand_still_skips_an_ordinary_token(
        self, npm_path: _NpmIdentityPath
    ) -> None:
        assert npm_path.detect("npm", ["--silent", "exec", "pkg"]) == npm_path.expect(
            resolver=("unknown", None), tables=("npm", "pkg")
        )

    # -- Consiliency/pmcp#195 note on everything below ---------------------
    #
    # These arrived with the 2.5.2 nullable-spelling fix and pin the FLAG
    # TABLES, which are now the node-less fallback. They run on both paths, and
    # where the two differ the difference is stated: npm's own parser reports a
    # config key for `-ws`/`--workspaces`/`--optional`/`--production`/
    # `--expect-results` (and their `--no-` spellings), and those keys are
    # outside the step-1 allowlist, so the resolver refuses. `yes` IS
    # allowlisted, so every `yes` spelling still resolves on both paths.
    #
    # The table-shape assertions (subset, exact set) are path-independent and
    # keep their original form.

    _NULLABLE_SPELLINGS_THE_RESOLVER_ALSO_RESOLVES = frozenset(
        {"-y", "--y", "--yes", "--no-yes", "-n", "--n", "-no", "--no"}
    )

    @pytest.mark.parametrize(
        "flag",
        [
            "-y",
            "--y",
            "--yes",
            "-n",
            "--n",
            "-no",
            "--no",
            "-ws",
            "--ws",
            "--workspaces",
            "--no-yes",
            "--optional",
            "--no-optional",
            "--production",
            "--no-production",
            "--expect-results",
            "--no-expect-results",
            "--no-workspaces",
        ],
    )
    def test_every_nullable_spelling_consumes_null(
        self, flag: str, npm_path: _NpmIdentityPath
    ) -> None:
        """All 18, one per spelling. 2.5.1 failed six of these.

        Cross-checked against nopt with npm's own definitions and shorthands:
        `nopt(types, shorthands, ["exec", flag, "null", "zz"]).argv.remain` is
        `["exec", "zz"]` for every flag here, i.e. the `null` is the flag's
        value and `zz` is the package.
        """
        resolves = flag in self._NULLABLE_SPELLINGS_THE_RESOLVER_ALSO_RESOLVES
        assert npm_path.detect("npm", ["exec", flag, "null", "zz"]) == npm_path.expect(
            resolver=("npm", "zz") if resolves else ("unknown", None),
            tables=("npm", "zz"),
        )

    @pytest.mark.parametrize("flag", ["-g", "--global", "-f", "--force", "--no-global"])
    def test_non_nullable_spellings_leave_null_as_the_package(
        self, flag: str, npm_path: _NpmIdentityPath
    ) -> None:
        """The other direction: over-broad is just as wrong as too narrow.

        nopt leaves `["exec", "null", "zz"]` for each of these, so the package
        really is `null` and consuming it would collapse two configs. The
        resolver refuses instead -- `global`/`force` are config keys outside the
        allowlist -- which is the safe direction of the same distinction.
        """
        assert npm_path.detect("npm", ["exec", flag, "null", "zz"]) == npm_path.expect(
            resolver=("unknown", None), tables=("npm", "null")
        )

    def test_nullable_spellings_do_not_collapse_two_configs(
        self, npm_path: _NpmIdentityPath
    ) -> None:
        """The identity consequence, stated directly.

        Under 2.5.1 both of these resolved to `null`, so the freshness gate
        would confirm one server's cached tools against the other's config.
        """
        got_a = npm_path.detect("npm", ["exec", "-n", "null", "server-a"])
        got_b = npm_path.detect("npm", ["exec", "-n", "null", "server-b"])
        assert got_a == ("npm", "server-a")
        assert got_b == ("npm", "server-b")
        assert got_a != got_b

    def test_nullable_table_is_a_subset_of_the_boolean_table(self) -> None:
        """A nullable entry outside the boolean table is unreachable.

        `_npm_package_arg_from_tables` only consults the nullable table from
        inside the boolean branch, so an entry missing from
        `_NPM_BOOLEAN_FLAGS` would be dead. `derive_npm_flags.py --verify` pins
        the same property against a live npm; this pins it without one.
        """
        assert (
            version_checker._NPM_NULLABLE_BOOLEAN_FLAGS
            <= version_checker._NPM_BOOLEAN_FLAGS
        )

    def test_nullable_table_is_pinned_to_the_exact_derived_set(self) -> None:
        """The table is pinned by VALUE, not just by shape.

        Every other check here is one-directional: the parametrized spellings
        prove each of the 18 consumes `null`, and the subset check proves none
        is unreachable. An *extra* entry passes both -- and an extra entry is a
        wrong identity in the other direction, since a non-nullable flag would
        then swallow a `null` that npm treats as the package.

        Pinned as a literal so a table change has to be a deliberate edit here
        too. `derive_npm_flags.py --verify` re-derives it against a live npm;
        this pins it without one (board review, adversarial seat).
        """
        assert version_checker._NPM_NULLABLE_BOOLEAN_FLAGS == frozenset(
            {
                "--expect-results",
                "--n",
                "--no",
                "--no-expect-results",
                "--no-optional",
                "--no-production",
                "--no-workspaces",
                "--no-yes",
                "--optional",
                "--production",
                "--workspaces",
                "--ws",
                "--y",
                "--yes",
                "-n",
                "-no",
                "-ws",
                "-y",
            }
        )


# ---------------------------------------------------------------------------
# `.consiliency/notes/derive_npm_flags.py --verify` must not depend on the host
# ---------------------------------------------------------------------------

_TESTS_DIR = pathlib.Path(__file__).resolve().parent
_REPO_ROOT = _TESTS_DIR.parent
_GENERATOR = _REPO_ROOT / ".consiliency" / "notes" / "derive_npm_flags.py"

# A RECORDED `read_schema()` from a real npm 11.19.0. CI has neither npm nor
# node, so a test that shelled out would never run there -- and #193 is a bug
# in a check that only ever ran on one maintainer's machine. Refresh with
# `derive_npm_flags.py --record-schema tests/fixtures/npm/schema.json`.
_SCHEMA_FIXTURE = _TESTS_DIR / "fixtures" / "npm" / "schema.json"


def _load_generator() -> Any:
    """Import the generator by path -- it is a maintainer script, not a module.

    Same `spec_from_file_location` shape as tests/test_workflow_guards.py uses
    for scripts/check_workflows.py.
    """
    spec = importlib.util.spec_from_file_location("derive_npm_flags", _GENERATOR)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


dnf = _load_generator()
_NPM_SCHEMA = json.loads(_SCHEMA_FIXTURE.read_text())

# The three shapes `local-address`'s declared type takes across machines. Every
# string member is already `'<literal>'` by the time Python sees it -- the node
# serializer maps it -- so the ONLY thing that varies host to host is how many
# there are, and whether there are any at all.
#
# `["null"]` is not a synthetic edge case: npm's `getLocalAddresses()` catches a
# `networkInterfaces()` throw and returns exactly `[null]`, and that host is the
# one that filed #193. The member rule strips `null` and calls the remainder
# UNINTERPRETABLE, which is the false drift report.
_ENUMERATION_FAILED = ["null"]
_ONE_ADDRESS = ["null", "<literal>"]
_FIFTY_ADDRESSES = ["null"] + ["<literal>"] * 50


def _no_npm() -> str:
    """Tripwire: these tests must never reach a real npm."""
    raise AssertionError(
        "shelled out to npm -- this test would then be maintainer-only and "
        "would never run in CI, which is the failure mode #193 is about"
    )


def _schema_with_local_address(type_labels: list[str]) -> dict:
    """The recorded schema, with only the host-varying member list replaced."""
    schema = copy.deepcopy(_NPM_SCHEMA)
    schema["definitions"]["local-address"]["type"] = list(type_labels)
    return schema


def _write_tables(repo: pathlib.Path, schema: dict) -> pathlib.Path:
    """Generate the four tables from `schema` into a stand-in version_checker."""
    classes, nullable, *_ = dnf.build(schema)
    source = repo / "src" / "pmcp" / "manifest" / "version_checker.py"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(dnf.emit(classes, nullable))
    return source


def _run_verify(
    monkeypatch: pytest.MonkeyPatch, repo: pathlib.Path, schema: dict
) -> tuple[int, str]:
    """Run `main() --verify` against `schema` and the tables under `repo`."""
    monkeypatch.setattr(dnf, "REPO", repo)
    monkeypatch.setattr(dnf, "read_schema", lambda: schema)
    monkeypatch.setattr(dnf, "npm_root", _no_npm)
    monkeypatch.setattr(sys, "argv", ["derive_npm_flags.py", "--verify"])
    err = io.StringIO()
    with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
        rc = dnf.main()
    return rc, err.getvalue()


class TestVerifyIsHostIndependent:
    """#193: `--verify` was green on one machine and red on another.

    npm builds `local-address`'s declared `type` from `os.networkInterfaces()`,
    so its members are facts about the machine. Its CLASS is not: it consumes
    the next token everywhere. A drift check with false positives gets ignored,
    and an ignored check is how real drift ships.
    """

    @pytest.mark.parametrize(
        "type_labels",
        [_ENUMERATION_FAILED, _ONE_ADDRESS, _FIFTY_ADDRESSES],
        ids=["enumeration-failed", "one-address", "fifty-addresses"],
    )
    def test_host_enumerated_is_value_whatever_the_host_reports(
        self, type_labels: list[str]
    ) -> None:
        """VALUE for all three -- not merely equal to each other.

        Comparing two non-empty address lists would pass against the unfixed
        code, which already classifies those two identically. The bare `["null"]`
        is the case that fails, and it is asserted here by name.
        """
        assert dnf.classify(type_labels, host_enumerated=True) == dnf.VALUE

    def test_the_bug_is_the_enumeration_failing_not_a_different_address_set(
        self,
    ) -> None:
        """Pins WHY a member-inspecting fix cannot work.

        On the host that filed #193 there is no address to find: the member
        rule sees `["null"]`, strips it, and reports UNINTERPRETABLE -- which
        is the drift line that was reported. Two non-empty address lists, by
        contrast, already agreed before this fix.
        """
        assert dnf.classify(_ENUMERATION_FAILED) == dnf.UNINTERPRETABLE
        assert dnf.classify(_ONE_ADDRESS) == dnf.VALUE
        assert dnf.classify(_FIFTY_ADDRESSES) == dnf.VALUE

    def test_recorded_npm_marks_only_local_address_as_host_enumerated(self) -> None:
        info = _NPM_SCHEMA["definitions"]["local-address"]
        assert info["typeDescription"] == "IP Address"
        assert info["hostEnumerated"] is True
        assert dnf.host_enumerated_flags(_NPM_SCHEMA) == ["local-address"]

    @pytest.mark.parametrize(
        "name,type_description",
        [
            (
                "audit-level",
                'null, "info", "low", "moderate", "high", "critical", or "none"',
            ),
            (
                "loglevel",
                '"silent", "error", "warn", "notice", "http", "info", '
                '"verbose", or "silly"',
            ),
            ("lockfile-version", 'null, 1, 2, 3, "1", "2", or "3"'),
        ],
    )
    def test_fixed_enumerations_are_not_host_enumerated(
        self, name: str, type_description: str
    ) -> None:
        """ "Many members" is not the signal; "members that are host facts" is.

        These three are the only other definitions with more than six type
        members, and they are byte-identical on every host. Asserted against
        npm's REAL `typeDescription` strings, recorded in the fixture, so this
        cannot drift into agreeing with a hand-written guess.
        """
        info = _NPM_SCHEMA["definitions"][name]
        assert info["typeDescription"] == type_description
        assert info["hostEnumerated"] is False
        assert len(info["type"]) >= 7

    def test_verify_is_byte_identical_across_host_address_counts(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The headline criterion: three hosts, one answer.

        The committed tables are generated ONCE, from the unpatched recording,
        and the same path is reused for all three runs -- `main()` prints the
        source path, so a per-run tmpdir would defeat the comparison for the
        wrong reason.
        """
        tables = _write_tables(tmp_path, _NPM_SCHEMA)
        expected_tables = tables.read_text()

        outputs: list[str] = []
        for type_labels in (_ENUMERATION_FAILED, _ONE_ADDRESS, _FIFTY_ADDRESSES):
            schema = _schema_with_local_address(type_labels)
            rc, err = _run_verify(monkeypatch, tmp_path, schema)
            assert rc == 0, err
            outputs.append(err)

            # Not just the report -- the emitted tables themselves must not
            # move, or "no drift" would be true only of what we chose to print.
            classes, nullable, *_ = dnf.build(schema)
            assert dnf.emit(classes, nullable) == expected_tables

        assert outputs[1] == outputs[0]
        assert outputs[2] == outputs[0]

    def test_verify_names_the_flag_it_normalised(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The exemption-shaped behaviour is reported, not hidden.

        And the note carries no member COUNT: that is the host fact, and
        printing it would put the #193 divergence straight back into the
        output it is meant to have removed.
        """
        _write_tables(tmp_path, _NPM_SCHEMA)
        rc, err = _run_verify(
            monkeypatch, tmp_path, _schema_with_local_address(_ENUMERATION_FAILED)
        )
        assert rc == 0, err
        assert "HOST-ENUMERATED type -> normalised to value, still verified (1):" in err
        assert "  --local-address\n" in err
        assert "51" not in err.split("nopt cross-check")[0].split("HOST-ENUMERATED")[1]

    def test_local_address_is_still_in_the_comparison(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Normalising the class must not become exempting the flag.

        An exemption would pass every test above -- no false positive can come
        from a flag that is never compared -- while blinding the tool to a real
        arity change on the flag most likely to drift. So: keep the recording
        exactly as npm reported it, corrupt the LIVE classification of whatever
        is host-enumerated to BOOLEAN, and require `--verify` to notice.

        Patching the classification rather than the schema is what makes this
        discriminating: a skip list keyed on `hostEnumerated` would still be
        skipping this flag here, and would stay green.
        """
        _write_tables(tmp_path, _NPM_SCHEMA)
        real_classify = dnf.classify

        def misclassify(
            type_labels: list[str], *, host_enumerated: bool = False
        ) -> str:
            if host_enumerated:
                return dnf.BOOLEAN
            return real_classify(type_labels, host_enumerated=False)

        monkeypatch.setattr(dnf, "classify", misclassify)
        rc, err = _run_verify(monkeypatch, tmp_path, copy.deepcopy(_NPM_SCHEMA))

        assert rc == 1
        assert "value: --local-address in table, absent from live npm" in err
        assert "boolean: --local-address in live npm, MISSING from table" in err
