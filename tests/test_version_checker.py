"""Tests for package version checking functionality."""

from __future__ import annotations

import ast
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pmcp.manifest.version_checker import (
    _digest_identity,
    _parse_version,
    _semver_parse,
    _USER_AGENT,
    _version_cache,
    clear_version_cache,
    detect_package_type,
    get_cargo_version,
    get_docker_version,
    get_npm_version,
    get_package_version,
    get_pypi_version,
    are_versions_comparable,
    is_version_newer,
    is_version_orderable,
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
        """Test detection with npm command picks first non-flag arg."""
        # Note: npm exec is treated as package name since code doesn't special-case it
        pkg_type, pkg_name = detect_package_type("npm", ["-y", "server-pkg"])
        assert pkg_type == "npm"
        assert pkg_name == "server-pkg"

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


class TestIsVersionNewer:
    """Tests for is_version_newer function."""

    def test_same_version(self) -> None:
        """Test same versions are not newer."""
        assert is_version_newer("1.0.0", "1.0.0") is False
        assert is_version_newer("2025.1.1", "2025.1.1") is False

    def test_semver_patch_newer(self) -> None:
        """Test patch version comparison."""
        assert is_version_newer("1.0.0", "1.0.1") is True
        assert is_version_newer("1.0.1", "1.0.0") is False

    def test_semver_minor_newer(self) -> None:
        """Test minor version comparison."""
        assert is_version_newer("1.0.0", "1.1.0") is True
        assert is_version_newer("1.1.0", "1.0.0") is False

    def test_semver_major_newer(self) -> None:
        """Test major version comparison."""
        assert is_version_newer("1.0.0", "2.0.0") is True
        assert is_version_newer("2.0.0", "1.0.0") is False

    def test_date_based_version(self) -> None:
        """Test date-based version comparison."""
        assert is_version_newer("2025.1.1", "2025.1.2") is True
        assert is_version_newer("2025.1.1", "2025.2.1") is True
        assert is_version_newer("2025.12.1", "2025.1.1") is False

    def test_version_with_v_prefix(self) -> None:
        """Test versions with v prefix."""
        assert is_version_newer("v1.0.0", "v1.0.1") is True
        assert is_version_newer("V1.0.0", "V1.0.1") is True

    def test_short_version(self) -> None:
        """Test 2-part versions."""
        assert is_version_newer("1.0", "1.1") is True
        assert is_version_newer("0.19", "0.20") is True

    def test_different_length_versions(self) -> None:
        """Test versions with different number of parts."""
        # 1.0 vs 1.0.1 - tuple comparison: (1, 0) vs (1, 0, 1)
        assert is_version_newer("1.0", "1.0.1") is True
        assert is_version_newer("1.0.1", "1.0") is False

    def test_zero_versions(self) -> None:
        """Test pre-release style versions."""
        assert is_version_newer("0.0.1", "0.0.2") is True
        assert is_version_newer("0.0.19", "0.0.20") is True

    def test_non_numeric_versions(self) -> None:
        """Test non-numeric versions parse as empty tuples (equal)."""
        # Non-numeric strings have no numeric parts, so both parse as ()
        # () > () is False, so neither is "newer"
        assert is_version_newer("alpha", "beta") is False
        assert is_version_newer("beta", "alpha") is False

    def test_mixed_versions(self) -> None:
        """Test versions with mixed numeric and text parts."""
        # "rc1" parses as (1,), "rc2" parses as (2,)
        assert is_version_newer("1.0.0-rc1", "1.0.0-rc2") is True
        assert is_version_newer("v2.0-beta1", "v2.0-beta2") is True

    # --- fail-closed: never fabricate an "update available" ------------------

    def test_unorderable_current_reports_no_update(self) -> None:
        """A version this function cannot order must NOT read as out of date.

        Consiliency/pmcp#150 board review. Returning True here makes the gateway
        tell an operator their server is stale, so an unreadable version has to
        report no update. Previously every one of these extracted digits (or an
        empty tuple) and compared as OLDER than any real release, fabricating a
        notice for any server whose version string is not a release number.
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
            assert is_version_newer(unreadable, "2.0.0") is False, unreadable

    def test_empty_current_reports_no_update(self) -> None:
        """The empty string is mcp 2.x's DEFAULT serverInfo.version.

        It is reached in practice by any server that does not set a version
        explicitly, so it must not compare as older than every release.
        """
        assert is_version_newer("", "2.0.0") is False

    def test_unorderable_latest_reports_no_update(self) -> None:
        """An unreadable *latest* is equally unusable -- refuse to guess."""
        assert is_version_newer("1.0.0", "nightly") is False
        assert is_version_newer("1.0.0", "") is False

    def test_docker_digests_compare_by_inequality(self) -> None:
        """Digests are identities, not ordinals.

        Uses the BARE 12-hex form ``get_docker_version`` actually returns -- it
        strips the ``sha256:`` prefix and truncates (see ``TestGetDockerVersion``).
        A previous version of this guard matched only ``sha256:``-prefixed values
        and was tested with invented literals, so it never fired against the real
        producer and silenced every docker update notice. Ordering hex is
        meaningless, but a DIFFERENT digest is genuinely a new image.
        """
        assert is_version_newer("abcdef123456", "abcdef123456") is False
        assert is_version_newer("abcdef123456", "fedcba654321") is True
        # The prefixed/full form is still accepted, in case the producer ever
        # stops truncating.
        assert is_version_newer("sha256:abcdef123456", "sha256:fedcba654321") is True

    def test_digest_and_version_are_not_comparable(self) -> None:
        """A digest and a release number describe different things."""
        assert is_version_newer("1.0.0", "abcdef123456") is False
        assert is_version_newer("abcdef123456", "1.0.0") is False

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
        assert is_version_newer(produced, "0123456789ab") is True

    def test_long_numeric_version_is_not_mistaken_for_a_digest(self) -> None:
        """A purely numeric string is a version, not a digest.

        `202612180000` is a plausible calendar/build stamp and is 12 chars of
        `[0-9a-f]`. Without a hex-letter requirement it matches the digest
        pattern, and is then never compared against a dotted release -- silently
        dropping a real update. Found while reviewing my own digest rule.
        """
        from pmcp.manifest.version_checker import _digest_identity

        assert _digest_identity("202612180000") is None
        assert is_version_newer("202612180000", "202612190000") is True

    def test_semver_ecosystem_prerelease_ordering(self) -> None:
        """npm/Cargo publish SemVer, where `-1` is a PRERELEASE.

        PEP 440 -- what `packaging` implements -- reads `1.0.0-1` as the POST
        release `1.0.0.post1`, the opposite order. 79 of the manifest's 107
        servers are npm, so without an ecosystem-aware path this inverts
        precedence on a real published format: it hides the `1.0.0-1 -> 1.0.0`
        upgrade and fabricates the reverse.
        """
        assert is_version_newer("1.0.0-1", "1.0.0", "npm") is True
        assert is_version_newer("1.0.0", "1.0.0-1", "npm") is False
        assert is_version_newer("1.0.0-alpha", "1.0.0-beta", "npm") is True
        assert is_version_newer("1.0.0+build.4", "1.0.0+build.5", "npm") is False
        # cargo shares SemVer semantics.
        assert is_version_newer("1.0.0-1", "1.0.0", "cargo") is True

    def test_unorderable_version_is_not_reported_as_up_to_date(self) -> None:
        """`not is_version_newer(...)` is ambiguous under a fail-closed comparator.

        Regression introduced by this PR and caught in review: `refresher` negates
        the comparator, and once unreadable input returns False, `not(...)` reads
        as "up to date". Since that function persists the literal `"unknown"`
        after a failed lookup, the stale cache would be returned forever.
        `is_version_orderable` is the discriminator callers need.
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
        assert is_version_newer("987654321098", "123456789012", "docker") is True
        assert is_version_newer("987654321098", "987654321098", "docker") is False
        # Without a docker type, a numeric string stays a VERSION and is ordered.
        assert is_version_newer("202612180000", "202612190000") is True

    def test_invalid_semver_is_not_ordered(self) -> None:
        """SemVer 2.0.0 rules 2, 9 and 10: no leading zeros, no empty identifiers.

        These strings cannot be published to npm, and ordering them fabricated
        updates (`1.0.0-01` -> `1.0.0` reported an upgrade).
        """
        for invalid in ("1.0.0-01", "01.0.0", "1.0.0-a..b", "1.0.00", "1.0.0-"):
            assert is_version_newer(invalid, "1.0.0", "npm") is False, invalid
            assert is_version_newer("1.0.0", invalid, "npm") is False, invalid

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
            assert is_version_newer(older, newer, "npm") is True, f"{older} -> {newer}"
            assert is_version_newer(newer, older, "npm") is False, f"{newer} -> {older}"

    def test_pypi_keeps_pep440_ordering(self) -> None:
        """PyPI publishes PEP 440; post-releases and its prerelease forms hold."""
        assert is_version_newer("1.0.post1", "1.0.post2", "pypi") is True
        assert is_version_newer("1.0a1", "1.0", "pypi") is True

    def test_same_digest_in_two_representations_is_not_an_update(self) -> None:
        """One image, two spellings, must not read as a new release.

        `get_docker_version` emits a bare truncated digest, but a prefixed or
        untruncated form can reach the comparator from a cached value or a
        registry that does not truncate. Comparing raw strings called that an
        update -- a fabricated notice for an unchanged image.
        """
        assert is_version_newer("abcdef123456", "sha256:abcdef123456") is False
        assert is_version_newer("sha256:abcdef1234567890", "abcdef123456") is False
        # A genuinely different image still registers.
        assert is_version_newer("abcdef123456", "fedcba654321") is True

    def test_build_metadata_is_not_a_new_release(self) -> None:
        """A local/build-metadata difference is not an update to announce."""
        assert is_version_newer("1.0.0+build.4", "1.0.0+build.5") is False

    def test_prerelease_precedence(self) -> None:
        """PEP 440 ordering: a prerelease is OLDER than its release."""
        assert is_version_newer("1.0.0-rc1", "1.0.0") is True
        assert is_version_newer("1.0.0", "1.0.0-rc1") is False


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
        exactly what `is_version_newer`'s fail-closed contract exists to
        prevent. Incomparable is the correct answer without a package type.
        """
        assert is_version_newer("202612180000", "abcdef123456") is False
        assert is_version_newer("abcdef123456", "202612180000") is False

    def test_package_type_resolves_the_ambiguity(self) -> None:
        """The type is what makes an all-numeric digest orderable."""
        assert is_version_newer("987654321098", "123456789012", "docker") is True

    def test_sha256_prefix_alone_identifies_an_all_numeric_digest(self) -> None:
        """The `sha256:` prefix names a digest even with no hex letter.

        The digest pattern previously required a hex letter *even when the
        prefix was present*, so an all-numeric prefixed digest was rejected.
        """
        assert is_version_newer("sha256:987654321098", "sha256:123456789012") is True
        assert is_version_orderable("sha256:987654321098") is True

    def test_same_image_two_spellings_is_not_an_update(self) -> None:
        """Promotion must not break canonicalisation."""
        assert is_version_newer("sha256:abcdef123456", "abcdef123456") is False

    def test_calendar_versions_still_order(self) -> None:
        """The reverted fail-closed would have broken exactly this."""
        assert is_version_newer("202612180000", "202612190000") is True
        assert is_version_orderable("202612180000") is True


def test_all_is_version_newer_callers_pass_package_type() -> None:
    """Every `is_version_newer(...)` call must pass a package type.

    The type is what disambiguates an all-numeric truncated digest from a
    calendar version. Without it the pair is incomparable by design (see
    TestAllNumericDigestDisambiguation), so a caller that drops the type
    silently loses update detection for docker servers.

    #156 called that risk latent because every caller passes the type. This
    pins that, instead of leaving it as a fact that happened to be true when
    the issue was written.
    """
    from pathlib import Path

    src_root = Path(__file__).resolve().parent.parent / "src"
    offenders: list[str] = []

    for path in sorted(src_root.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for call in ast.walk(tree):
            if not isinstance(call, ast.Call):
                continue
            if _called_name(call) != "is_version_newer":
                continue
            has_type = len(call.args) >= 3 or any(
                kw.arg == "package_type" for kw in call.keywords
            )
            if not has_type:
                offenders.append(f"{path.relative_to(src_root)}:{call.lineno}")

    assert not offenders, (
        "`is_version_newer(...)` called without a package_type -- an "
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


class TestPairComparability:
    """`are_versions_comparable` is a PAIR property, not two unary checks.

    Board review of #163 found the earlier both-sides guard still wrong:
    `"1.0.0"` and `"abcdef123456"` are each individually orderable, so two
    unary `is_version_orderable` calls both passed -- but a version and a digest
    are mutually incomparable, `is_version_newer` failed closed to False, and
    the negation reported "up to date" for a pair it could not order. Reachable
    because refresh_all reuses a cache entry by server name without checking
    that the package type still matches.
    """

    def test_version_against_digest_is_not_comparable(self) -> None:
        """The case two unary guards let through."""
        assert is_version_orderable("1.0.0", "docker") is True
        assert is_version_orderable("abcdef123456", "docker") is True
        assert are_versions_comparable("1.0.0", "abcdef123456", "docker") is False

    def test_matching_kinds_are_comparable(self) -> None:
        assert are_versions_comparable("1.0.0", "2.0.0", "npm") is True
        assert are_versions_comparable("abcdef123456", "abcdef123457", "docker") is True

    def test_unreadable_on_either_side_is_not_comparable(self) -> None:
        assert are_versions_comparable("1.0.0", "nightly", "npm") is False
        assert are_versions_comparable("nightly", "1.0.0", "npm") is False
        assert are_versions_comparable("unknown", "2.0.0", "npm") is False

    def test_incomparable_pairs_are_never_ordered(self) -> None:
        """The safety invariant: incomparable => `is_version_newer` is False BOTH ways.

        This is what makes `are_versions_comparable` a sound guard. If a pair it
        rejects could still be ordered, the guard would suppress a real update;
        if a pair it accepts could not be ordered, the negation would read "up
        to date" for something incomparable -- the defect this whole predicate
        exists to close.

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
                    if are_versions_comparable(current, latest, pkg_type):
                        continue
                    assert not is_version_newer(current, latest, pkg_type), (
                        f"{current!r} -> {latest!r} ({pkg_type!r}) reported newer "
                        f"despite being incomparable"
                    )
                    assert not is_version_newer(latest, current, pkg_type), (
                        f"{latest!r} -> {current!r} ({pkg_type!r}) reported newer "
                        f"despite being incomparable"
                    )

    def test_comparable_pairs_are_always_ordered_or_equal(self) -> None:
        """The DANGEROUS drift direction, over the same corpus.

        `test_incomparable_pairs_are_never_ordered` skips every pair the
        predicate accepts, so on its own it cannot catch the failure that
        actually matters (ah board review): the predicate reporting a pair
        comparable when `is_version_newer` cannot order it. That is precisely
        what lets `not is_version_newer(...)` report "up to date" for something
        incomparable — the defect the predicate exists to close.

        So: for every accepted pair, either one direction is newer, or the two
        are canonically the SAME value. Canonical identity is computed here
        independently of the predicate, so the two cannot agree by sharing a
        bug.
        """

        def canonical(value: str, pkg_type: str | None) -> object:
            """Identity of *value*, for deciding whether two spellings agree.

            Must match what `is_version_newer` actually compares, which took
            two corrections to get right:

            * PARSED, not string form -- `1.0` and `1.0.0` are the same PEP 440
              release but render differently;
            * the PUBLIC segment, reparsed -- `is_version_newer` compares
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
                    if not are_versions_comparable(current, latest, pkg_type):
                        continue
                    ordered = is_version_newer(
                        current, latest, pkg_type
                    ) or is_version_newer(latest, current, pkg_type)
                    if ordered:
                        continue
                    assert canonical(current, pkg_type) == canonical(
                        latest, pkg_type
                    ), (
                        f"({current!r}, {latest!r}, {pkg_type!r}) reported "
                        f"comparable, but is_version_newer orders it in neither "
                        f"direction and the two are not the same value -- a "
                        f"caller negating the comparison would read 'up to date'"
                    )

    def test_comparable_pairs_that_differ_are_ordered(self) -> None:
        """A comparable pair with genuinely different values orders one way."""
        for current, latest, pkg_type in [
            ("1.0.0", "2.0.0", "npm"),
            ("1.0.0-rc1", "1.0.0", "npm"),
            ("abcdef123456", "abcdef123457", "docker"),
            ("202612180000", "202612190000", None),
        ]:
            assert are_versions_comparable(current, latest, pkg_type) is True
            assert is_version_newer(current, latest, pkg_type) or is_version_newer(
                latest, current, pkg_type
            )


def test_no_unguarded_negation_of_is_version_newer() -> None:
    """`not is_version_newer(...)` must be gated on `is_version_orderable`.

    Consiliency/pmcp#156 item 2. `is_version_newer` fails closed, so `False`
    means EITHER "current" OR "unorderable". Negating it collapses those into
    "up to date".

    This is a recorded regression, not a hypothetical: making the function fail
    closed silently inverted the existing negated caller in `refresher.py`,
    which then treated the literal `"unknown"` it persists after a failed
    lookup as current -- pinning that cache forever. The audit at the time
    checked all eight call sites for ARITY but not for NEGATION.

    Co-occurrence in the statement is NOT enough (ah board review). Both
    conditions below have to hold, or
    ``not is_version_newer(a, b) or is_version_orderable(a)`` -- which still
    enters the "up to date" branch for an unorderable ``a`` -- would pass:

    1. the guard and the negated call share an ``and`` chain, so the guard
       actually short-circuits the negation rather than sitting in an ``or``;
    2. the guard's first argument is the same expression as the negated call's
       first argument, so guarding a DIFFERENT value does not count.

    AST rather than grep, so comments and strings that merely mention the
    pattern do not register.

    Known limitation, stated rather than hidden: this models `and` chains and
    enclosing `if` tests, NOT early-exit guards. A negative guard that returns
    early --

        if not is_version_orderable(v):
            return False
        ...
        if not is_version_newer(v, x, t):

    -- is genuinely safe but will trip this test. That is the safe direction to
    fail (it blocks and asks for a human), but if you hit it legitimately,
    restructure into the `and` chain or extend `_guaranteed_conditions` to
    model early exits rather than deleting the assertion.

    It is also UNSOUND in the other direction, and deliberately so rather than
    silently: it matches on expression syntax, so it accepts a guarded value
    that is reassigned before the comparison, and a guard whose scope has ended
    before a closure evaluates it. A syntactic check cannot prove a dataflow
    property. Consiliency/pmcp#164 replaces the fail-closed boolean with a
    tri-state result, which makes the hazard unrepresentable and this test
    unnecessary; treat this as a smoke alarm until then.
    """
    from pathlib import Path

    src_root = Path(__file__).resolve().parent.parent / "src"
    offenders: list[str] = []

    for path in sorted(src_root.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.stmt):
                continue
            for negation in _negated_is_version_newer(node):
                assert isinstance(negation.operand, ast.Call)
                if len(negation.operand.args) < 2:
                    continue
                # BOTH operands, not just the first. is_version_newer is a
                # two-sided comparison: guarding only the cached side let an
                # unorderable FETCHED version fail closed to False and read as
                # "up to date", which is the very defect this check exists to
                # catch -- and it survived three rounds of this test.
                # The guard must be the PAIR predicate over the SAME two
                # operands. Two unary is_version_orderable() calls are not
                # enough -- a version and a digest are each orderable yet
                # mutually incomparable, and that gap kept this defect alive
                # through a whole round of "fixing" it.
                if len(negation.operand.args) < 3:
                    offenders.append(
                        f"{path.relative_to(src_root)}:{node.lineno} "
                        f"(no package_type on the comparison)"
                    )
                    continue
                pair = tuple(ast.dump(a) for a in negation.operand.args[:3])
                guarded = any(
                    _is_pair_guard_for(condition, pair)
                    for condition in _guaranteed_conditions(tree, negation)
                )
                if not guarded:
                    offenders.append(
                        f"{path.relative_to(src_root)}:{node.lineno} "
                        f"({ast.unparse(negation.operand.args[0])}, "
                        f"{ast.unparse(negation.operand.args[1])})"
                    )

    assert not offenders, (
        "`not is_version_newer(a, b)` without `are_versions_comparable(a, b)` "
        "guarding the SAME pair -- an incomparable pair will read as up to "
        f"date: {offenders}"
    )


def _negated_is_version_newer(node: ast.AST) -> list[ast.UnaryOp]:
    """Every `not is_version_newer(...)` inside *node*."""
    return [
        child
        for child in ast.walk(node)
        if isinstance(child, ast.UnaryOp)
        and isinstance(child.op, ast.Not)
        and isinstance(child.operand, ast.Call)
        and _called_name(child.operand) == "is_version_newer"
    ]


def _conjuncts(expr: ast.expr) -> list[ast.expr]:
    """Sub-expressions that MUST be truthy for *expr* to be truthy.

    `a and b` contributes both. `a or b` contributes only itself: either side
    alone can carry it, so neither is guaranteed. That distinction is the whole
    point -- searching inside an `or` is what let
    `(ready or is_version_orderable(x)) and not is_version_newer(x, y)` pass two
    earlier versions of this check.
    """
    if isinstance(expr, ast.BoolOp) and isinstance(expr.op, ast.And):
        return [c for value in expr.values for c in _conjuncts(value)]
    return [expr]


def _strip(expr: ast.expr) -> ast.expr:
    """Unwrap forms whose truthiness is equivalent to their operand's."""
    while True:
        if (
            isinstance(expr, ast.Call)
            and _called_name(expr) == "bool"
            and len(expr.args) == 1
        ):
            expr = expr.args[0]
        elif (
            isinstance(expr, ast.UnaryOp)
            and isinstance(expr.op, ast.Not)
            and isinstance(expr.operand, ast.UnaryOp)
            and isinstance(expr.operand.op, ast.Not)
        ):
            expr = expr.operand.operand
        elif isinstance(expr, ast.NamedExpr):
            # `(ok := guard)` is truthy exactly when `guard` is.
            expr = expr.value
        else:
            return expr


def _is_pair_guard_for(expr: ast.expr, pair: tuple[str, ...]) -> bool:
    """Whether *expr* is `are_versions_comparable(...)` for exactly *pair*.

    The package type is part of the pair, not an optional extra: it decides how
    both values are classified. `are_versions_comparable("202612180000",
    "1.0.0")` is True, while the same pair classified as docker is
    incomparable -- so a guard that drops the type would pass while the
    comparator beside it fails closed, recreating the unsafe short-circuit
    (ah board review).
    """
    call = _strip(expr)
    return (
        isinstance(call, ast.Call)
        and _called_name(call) == "are_versions_comparable"
        and len(call.args) >= 3
        and tuple(ast.dump(a) for a in call.args[:3]) == pair
    )


def _guaranteed_conditions(tree: ast.AST, negation: ast.UnaryOp) -> list[ast.expr]:
    """Conditions that must have held wherever *negation* is evaluated.

    Two sources, both real control flow rather than proximity:

    * an enclosing ``and`` chain -- operands BEFORE the one holding the
      negation, since a later operand only runs if every earlier one was
      truthy;
    * an enclosing ``if`` whose BODY (not ``orelse``) contains the negation.

    Modelling the `if` form matters: a guard in an enclosing `if` is genuinely
    safe, and an earlier version of this check rejected it, which would have
    blocked a legitimate refactor.
    """
    parents: dict[int, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[id(child)] = parent

    def contains(node: ast.AST) -> bool:
        return any(negation is child for child in ast.walk(node))

    conditions: list[ast.expr] = []
    seen = id(negation)
    node: ast.AST | None = negation
    while node is not None:
        parent = parents.get(id(node))
        if parent is None:
            break
        if isinstance(parent, ast.BoolOp) and isinstance(parent.op, ast.And):
            holder = next(
                (i for i, v in enumerate(parent.values) if id(v) == seen), None
            )
            if holder is not None:
                for value in parent.values[:holder]:
                    conditions.extend(_conjuncts(value))
        elif isinstance(parent, ast.If) and any(contains(b) for b in parent.body):
            conditions.extend(_conjuncts(parent.test))
        seen = id(parent)
        node = parent
    return conditions


def _called_name(call: ast.Call) -> str | None:
    """Name of the function a Call node invokes, plain or attribute."""
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None
