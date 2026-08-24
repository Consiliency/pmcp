"""Tests for description refresher functionality."""

from __future__ import annotations

import tempfile
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

from pmcp.manifest.loader import Manifest, ServerConfig
from pmcp.manifest.refresher import (
    GATEWAY_VERSION,
    _escape_yaml_string,
    _extract_tags,
    _indent_multiline,
    _infer_risk,
    check_staleness,
    get_cache_path,
    load_descriptions_cache,
    refresh_server,
    refresh_all,
    save_descriptions_cache,
)
from pmcp.types import (
    DescriptionsCache,
    GeneratedServerDescriptions,
    PrebuiltToolInfo,
)


class TestGetCachePath:
    """Tests for get_cache_path function."""

    def test_default_path(self) -> None:
        """Test default cache path."""
        path = get_cache_path()
        assert path == Path(".mcp-gateway") / "descriptions.yaml"

    def test_custom_path(self) -> None:
        """Test custom cache directory."""
        path = get_cache_path(Path("/custom/dir"))
        assert path == Path("/custom/dir") / "descriptions.yaml"


class TestIndentMultiline:
    """Tests for _indent_multiline function."""

    def test_single_line(self) -> None:
        """Test single line indentation."""
        result = _indent_multiline("Hello world", 4)
        assert result == "    Hello world"

    def test_multiline(self) -> None:
        """Test multiline indentation."""
        text = "Line 1\nLine 2\nLine 3"
        result = _indent_multiline(text, 2)
        assert result == "  Line 1\n  Line 2\n  Line 3"

    def test_empty_string(self) -> None:
        """Test empty string."""
        result = _indent_multiline("", 4)
        assert result == "    "

    def test_whitespace_stripped(self) -> None:
        """Test whitespace is stripped."""
        result = _indent_multiline("  text  \n", 2)
        assert result == "  text"


class TestEscapeYamlString:
    """Tests for _escape_yaml_string function."""

    def test_no_escaping_needed(self) -> None:
        """Test string without special characters."""
        result = _escape_yaml_string("Simple text")
        assert result == "Simple text"

    def test_escape_quotes(self) -> None:
        """Test double quote escaping."""
        result = _escape_yaml_string('He said "hello"')
        assert result == 'He said \\"hello\\"'

    def test_newline_replaced(self) -> None:
        """Test newlines replaced with spaces."""
        result = _escape_yaml_string("Line 1\nLine 2")
        assert result == "Line 1 Line 2"

    def test_whitespace_stripped(self) -> None:
        """Test leading/trailing whitespace stripped."""
        result = _escape_yaml_string("  text  ")
        assert result == "text"


class TestExtractTags:
    """Tests for _extract_tags function."""

    def test_browser_tag(self) -> None:
        """Test browser-related tag extraction."""
        tags = _extract_tags("navigate_to", "Navigate to URL in browser")
        assert "browser" in tags

    def test_file_tag(self) -> None:
        """Test file-related tag extraction."""
        tags = _extract_tags("read_file", "Read contents of a file")
        assert "file" in tags

    def test_db_tag(self) -> None:
        """Test database-related tag extraction."""
        tags = _extract_tags("run_query", "Execute SQL query on database")
        assert "db" in tags

    def test_git_tag(self) -> None:
        """Test git-related tag extraction."""
        tags = _extract_tags("create_commit", "Create a git commit")
        assert "git" in tags

    def test_http_tag(self) -> None:
        """Test HTTP-related tag extraction."""
        tags = _extract_tags("fetch_url", "Fetch content from URL")
        assert "http" in tags

    def test_search_tag(self) -> None:
        """Test search-related tag extraction."""
        tags = _extract_tags("search_issues", "Search for issues")
        assert "search" in tags

    def test_docs_tag(self) -> None:
        """Test docs-related tag extraction."""
        tags = _extract_tags("get_docs", "Get library documentation")
        assert "docs" in tags

    def test_code_tag(self) -> None:
        """Test code-related tag extraction."""
        tags = _extract_tags("analyze_function", "Analyze function code")
        assert "code" in tags

    def test_multiple_tags(self) -> None:
        """Test extraction of multiple tags."""
        tags = _extract_tags("git_search", "Search git repository for code")
        assert "git" in tags
        assert "search" in tags
        assert "code" in tags

    def test_default_general_tag(self) -> None:
        """Test default 'general' tag when no keywords match."""
        tags = _extract_tags("do_something", "Perform an action")
        assert tags == ["general"]


class TestInferRisk:
    """Tests for _infer_risk function."""

    def test_high_risk_delete(self) -> None:
        """Test high risk for delete operations."""
        assert _infer_risk("delete_file", "Delete a file") == "high"

    def test_high_risk_remove(self) -> None:
        """Test high risk for remove operations."""
        assert _infer_risk("remove_item", "Remove an item") == "high"

    def test_high_risk_execute(self) -> None:
        """Test high risk for execute operations."""
        assert _infer_risk("execute_command", "Execute shell command") == "high"

    def test_high_risk_write(self) -> None:
        """Test high risk for write operations."""
        assert _infer_risk("write_data", "Write data to disk") == "high"

    def test_high_risk_create(self) -> None:
        """Test high risk for create operations."""
        assert _infer_risk("create_file", "Create a new file") == "high"

    def test_medium_risk_navigate(self) -> None:
        """Test medium risk for navigate operations."""
        assert _infer_risk("navigate_to", "Navigate to URL") == "medium"

    def test_medium_risk_click(self) -> None:
        """Test medium risk for click operations."""
        assert _infer_risk("click_button", "Click a button") == "medium"

    def test_medium_risk_submit(self) -> None:
        """Test medium risk for submit operations."""
        assert _infer_risk("submit_form", "Submit form data") == "medium"

    def test_low_risk_read(self) -> None:
        """Test low risk for read operations."""
        # Note: "information" contains "input" which triggers medium risk
        # Use different description
        assert _infer_risk("get_status", "Retrieve status") == "low"

    def test_low_risk_list(self) -> None:
        """Test low risk for list operations."""
        assert _infer_risk("list_items", "Show all items") == "low"


class TestLoadDescriptionsCache:
    """Tests for load_descriptions_cache function."""

    def test_file_not_exists(self) -> None:
        """Test loading when file doesn't exist."""
        result = load_descriptions_cache(Path("/nonexistent/path.yaml"))
        assert result is None

    def test_valid_cache_file(self, temp_dir: Path) -> None:
        """Test loading valid cache file."""
        cache_file = temp_dir / "descriptions.yaml"
        cache_file.write_text(
            """
generated_at: "2025-01-01T00:00:00Z"
gateway_version: "1.0.0"
servers:
  test-server:
    package: "@test/mcp"
    version: "1.0.0"
    generated_at: "2025-01-01T00:00:00Z"
    capability_summary: "Test capabilities"
    tools:
      - name: "test_tool"
        description: "A test tool"
        short_description: "A test tool"
        tags:
          - test
        risk_hint: "low"
"""
        )

        result = load_descriptions_cache(cache_file)
        assert result is not None
        assert result.generated_at == "2025-01-01T00:00:00Z"
        assert result.gateway_version == "1.0.0"
        assert "test-server" in result.servers
        assert result.servers["test-server"].package == "@test/mcp"
        assert len(result.servers["test-server"].tools) == 1
        assert result.servers["test-server"].tools[0].name == "test_tool"

    def test_empty_cache_file(self, temp_dir: Path) -> None:
        """Test loading empty cache file."""
        cache_file = temp_dir / "empty.yaml"
        cache_file.write_text("")

        result = load_descriptions_cache(cache_file)
        assert result is None

    def test_invalid_yaml(self, temp_dir: Path) -> None:
        """Test loading invalid YAML file."""
        cache_file = temp_dir / "invalid.yaml"
        cache_file.write_text("{ invalid yaml ][")

        result = load_descriptions_cache(cache_file)
        assert result is None

    def test_missing_servers_section(self, temp_dir: Path) -> None:
        """Test loading cache with no servers section."""
        cache_file = temp_dir / "no_servers.yaml"
        cache_file.write_text(
            """
generated_at: "2025-01-01T00:00:00Z"
gateway_version: "1.0.0"
"""
        )

        result = load_descriptions_cache(cache_file)
        assert result is not None
        assert result.servers == {}


class TestSaveDescriptionsCache:
    """Tests for save_descriptions_cache function."""

    def test_save_creates_directory(self, temp_dir: Path) -> None:
        """Test save creates parent directory."""
        cache_path = temp_dir / "subdir" / "descriptions.yaml"

        cache = DescriptionsCache(
            generated_at="2025-01-01T00:00:00Z",
            gateway_version="1.0.0",
            servers={},
        )

        save_descriptions_cache(cache, cache_path)
        assert cache_path.exists()

    def test_save_and_load_roundtrip(self, temp_dir: Path) -> None:
        """Test save and load roundtrip."""
        cache_path = temp_dir / "descriptions.yaml"

        original = DescriptionsCache(
            generated_at="2025-01-01T00:00:00Z",
            gateway_version="1.0.0",
            servers={
                "my-server": GeneratedServerDescriptions(
                    package="@my/mcp",
                    version="2.0.0",
                    generated_at="2025-01-01T00:00:00Z",
                    capability_summary="My capabilities:\n• Feature 1\n• Feature 2",
                    tools=[
                        PrebuiltToolInfo(
                            name="my_tool",
                            description="My tool description",
                            short_description="My tool",
                            tags=["test", "example"],
                            risk_hint="low",
                        )
                    ],
                )
            },
        )

        save_descriptions_cache(original, cache_path)
        loaded = load_descriptions_cache(cache_path)

        assert loaded is not None
        assert loaded.generated_at == original.generated_at
        assert loaded.gateway_version == original.gateway_version
        assert "my-server" in loaded.servers
        assert loaded.servers["my-server"].package == "@my/mcp"
        assert loaded.servers["my-server"].version == "2.0.0"
        assert len(loaded.servers["my-server"].tools) == 1
        assert loaded.servers["my-server"].tools[0].name == "my_tool"

    def test_save_escapes_special_chars(self, temp_dir: Path) -> None:
        """Test special characters are escaped."""
        cache_path = temp_dir / "descriptions.yaml"

        cache = DescriptionsCache(
            generated_at="2025-01-01T00:00:00Z",
            gateway_version="1.0.0",
            servers={
                "test": GeneratedServerDescriptions(
                    package="test",
                    version="1.0.0",
                    generated_at="2025-01-01T00:00:00Z",
                    capability_summary="Test",
                    tools=[
                        PrebuiltToolInfo(
                            name="tool",
                            description='Includes "quotes" and newlines\nhere',
                            short_description='Has "quotes"',
                            tags=["test"],
                            risk_hint="low",
                        )
                    ],
                )
            },
        )

        save_descriptions_cache(cache, cache_path)

        # Verify file can be loaded
        loaded = load_descriptions_cache(cache_path)
        assert loaded is not None

    def test_save_hostile_values_cannot_inject_yaml_keys(self, temp_dir: Path) -> None:
        cache_path = temp_dir / "descriptions.yaml"
        hostile_server = "server:\n  injected-server"
        hostile_tool = 'tool":\n      risk_hint: high\n      - name: injected'

        cache = DescriptionsCache(
            generated_at="2025-01-01T00:00:00Z",
            gateway_version="1.0.0",
            servers={
                hostile_server: GeneratedServerDescriptions(
                    package='@scope/pkg": "value',
                    version="1.0.0",
                    generated_at="2025-01-01T00:00:00Z",
                    capability_summary="Summary:\n  risk_hint: high",
                    tools=[
                        PrebuiltToolInfo(
                            name=hostile_tool,
                            description='desc":\n  injected: true',
                            short_description="short: value\nother: no",
                            tags=["tag: value", "servers:\n  fake"],
                            risk_hint="low:\n  injected: high",
                        )
                    ],
                )
            },
        )

        save_descriptions_cache(cache, cache_path)
        raw = yaml.safe_load(cache_path.read_text())
        loaded = load_descriptions_cache(cache_path)

        assert set(raw["servers"]) == {hostile_server}
        assert loaded is not None
        loaded_desc = loaded.servers[hostile_server]
        assert loaded_desc.tools[0].name == hostile_tool
        assert loaded_desc.tools[0].risk_hint == "low:\n  injected: high"
        assert "injected-server" not in loaded.servers

    def test_save_multiple_servers(self, temp_dir: Path) -> None:
        """Test saving multiple servers."""
        cache_path = temp_dir / "descriptions.yaml"

        # Note: The save function writes "tools:" with nothing after for empty lists
        # which loads as None. So we need at least one tool per server.
        cache = DescriptionsCache(
            generated_at="2025-01-01T00:00:00Z",
            gateway_version="1.0.0",
            servers={
                "server1": GeneratedServerDescriptions(
                    package="pkg1",
                    version="1.0.0",
                    generated_at="2025-01-01T00:00:00Z",
                    capability_summary="Server 1",
                    tools=[
                        PrebuiltToolInfo(
                            name="tool1",
                            description="Tool 1",
                            short_description="Tool 1",
                            tags=["test"],
                            risk_hint="low",
                        )
                    ],
                ),
                "server2": GeneratedServerDescriptions(
                    package="pkg2",
                    version="2.0.0",
                    generated_at="2025-01-01T00:00:00Z",
                    capability_summary="Server 2",
                    tools=[
                        PrebuiltToolInfo(
                            name="tool2",
                            description="Tool 2",
                            short_description="Tool 2",
                            tags=["test"],
                            risk_hint="medium",
                        )
                    ],
                ),
            },
        )

        save_descriptions_cache(cache, cache_path)
        loaded = load_descriptions_cache(cache_path)

        assert loaded is not None
        assert len(loaded.servers) == 2
        assert "server1" in loaded.servers
        assert "server2" in loaded.servers


class TestCheckStaleness:
    """Tests for check_staleness function."""

    @pytest.fixture
    def mock_manifest(self) -> MagicMock:
        """Create mock manifest."""
        manifest = MagicMock()
        manifest.servers = {"server1": MagicMock()}

        server_config = MagicMock()
        server_config.command = "npx"
        server_config.args = ["-y", "@test/mcp"]
        manifest.get_server.return_value = server_config

        return manifest

    @pytest.mark.asyncio
    async def test_no_cache_returns_empty(self, mock_manifest: MagicMock) -> None:
        """Test returns empty dict when no cache exists."""
        with patch(
            "pmcp.manifest.refresher.load_descriptions_cache",
            return_value=None,
        ):
            with patch(
                "pmcp.manifest.refresher.load_manifest",
                return_value=mock_manifest,
            ):
                result = await check_staleness()
                assert result == {}

    @pytest.mark.asyncio
    async def test_stale_server_detected(self, mock_manifest: MagicMock) -> None:
        """Test stale server is detected."""
        cache = DescriptionsCache(
            generated_at="2025-01-01T00:00:00Z",
            gateway_version="1.0.0",
            servers={
                "server1": GeneratedServerDescriptions(
                    package="@test/mcp",
                    # Matches `npx -y @test/mcp` in `mock_manifest`. Without a
                    # recorded type the identity gate cannot confirm the entry
                    # and reports it stale, which is a different code path from
                    # the version comparison these tests exist to pin.
                    package_type="npm",
                    version="1.0.0",
                    generated_at="2025-01-01T00:00:00Z",
                    capability_summary="Test",
                    tools=[],
                )
            },
        )

        with patch(
            "pmcp.manifest.refresher.load_descriptions_cache",
            return_value=cache,
        ):
            with patch(
                "pmcp.manifest.refresher.load_manifest",
                return_value=mock_manifest,
            ):
                with patch(
                    "pmcp.manifest.refresher.get_package_version",
                    new_callable=AsyncMock,
                    return_value=("2.0.0", "npm"),
                ):
                    result = await check_staleness()
                    assert "server1" in result
                    assert result["server1"] == ("1.0.0", "2.0.0")

    @pytest.mark.asyncio
    async def test_up_to_date_server_not_flagged(
        self, mock_manifest: MagicMock
    ) -> None:
        """Test up-to-date server is not flagged."""
        cache = DescriptionsCache(
            generated_at="2025-01-01T00:00:00Z",
            gateway_version="1.0.0",
            servers={
                "server1": GeneratedServerDescriptions(
                    package="@test/mcp",
                    # Matches `npx -y @test/mcp` in `mock_manifest`. Without a
                    # recorded type the identity gate cannot confirm the entry
                    # and reports it stale, which is a different code path from
                    # the version comparison these tests exist to pin.
                    package_type="npm",
                    version="1.0.0",
                    generated_at="2025-01-01T00:00:00Z",
                    capability_summary="Test",
                    tools=[],
                )
            },
        )

        with patch(
            "pmcp.manifest.refresher.load_descriptions_cache",
            return_value=cache,
        ):
            with patch(
                "pmcp.manifest.refresher.load_manifest",
                return_value=mock_manifest,
            ):
                with patch(
                    "pmcp.manifest.refresher.get_package_version",
                    new_callable=AsyncMock,
                    return_value=("1.0.0", "npm"),
                ):
                    result = await check_staleness()
                    assert "server1" not in result

    @pytest.mark.asyncio
    async def test_version_lookup_failure(self, mock_manifest: MagicMock) -> None:
        """Test handling of version lookup failure."""
        cache = DescriptionsCache(
            generated_at="2025-01-01T00:00:00Z",
            gateway_version="1.0.0",
            servers={
                "server1": GeneratedServerDescriptions(
                    package="@test/mcp",
                    # Matches `npx -y @test/mcp` in `mock_manifest`. Without a
                    # recorded type the identity gate cannot confirm the entry
                    # and reports it stale, which is a different code path from
                    # the version comparison these tests exist to pin.
                    package_type="npm",
                    version="1.0.0",
                    generated_at="2025-01-01T00:00:00Z",
                    capability_summary="Test",
                    tools=[],
                )
            },
        )

        with patch(
            "pmcp.manifest.refresher.load_descriptions_cache",
            return_value=cache,
        ):
            with patch(
                "pmcp.manifest.refresher.load_manifest",
                return_value=mock_manifest,
            ):
                with patch(
                    "pmcp.manifest.refresher.get_package_version",
                    new_callable=AsyncMock,
                    return_value=(None, "npm"),
                ):
                    result = await check_staleness()
                    # No error, but server not flagged as stale
                    assert "server1" not in result


class TestUpToDateShortCircuit:
    """The "already up to date" short-circuit must not fire on unorderable input.

    Consiliency/pmcp#156 item 2, retained after the tri-state migration.
    Historically `is_version_newer` failed closed, so `False` meant either
    "current" or "could not be ordered"; the short-circuit negated it, and an
    orderability guard was bolted on to stop an unreadable value reading as
    current. That function no longer exists (Consiliency/pmcp#164) -- the
    short-circuit now tests `compare_versions(...) == "not_newer"`, so an
    `incomparable` pair simply is not `not_newer` and falls through to a
    refresh. This test is kept because the OUTCOME it pins is the same one
    that defect produced, and it must keep holding under the new
    representation.

    That guard checked only the CACHED side. Board review proved the defect was
    therefore still live: with a cached `1.0.0` (orderable) and a FETCHED
    `nightly` (not), the guard passes, the comparator fails closed, and the
    negation returns "up to date" -- pinning the cache exactly as before.
    """

    @pytest.mark.asyncio
    async def test_unorderable_fetched_version_does_not_short_circuit(
        self, temp_dir: Path
    ) -> None:
        """A cached release vs an unorderable fetched version must refresh."""
        existing = GeneratedServerDescriptions(
            package="srv",
            version="1.0.0",
            generated_at="2025-01-01T00:00:00Z",
            capability_summary="stale summary",
            tools=[],
        )
        server = ServerConfig(
            name="srv",
            description="",
            keywords=[],
            install={},
            command="npx",
            args=["srv"],
        )

        calls: list[tuple] = []

        async def fake_version(command, args, timeout=None):
            calls.append((command, tuple(args)))
            return ("nightly", "npm")

        # `refresh_server` calls get_package_version ONCE for the up-to-date
        # check, and again on the refresh path just before connecting. So a
        # second call is the observable proof it did not short-circuit --
        # asserting on the return value alone cannot tell "refreshed" from
        # "returned the stale cache", since both yield a cache object.
        with patch(
            "pmcp.manifest.refresher.get_package_version", side_effect=fake_version
        ):
            with patch(
                "mcp.client.stdio.stdio_client",
                side_effect=RuntimeError("stop after the short-circuit decision"),
            ):
                await refresh_server(server, existing_cache=existing)

        assert len(calls) >= 2, (
            "short-circuited as up-to-date against an unorderable fetched "
            f"version -- the stale cache would be pinned forever (calls={calls})"
        )

    @pytest.mark.asyncio
    async def test_incomparable_pair_does_not_short_circuit(
        self, temp_dir: Path
    ) -> None:
        """A cached npm version vs a fetched digest must refresh, not skip.

        The case two unary orderability guards let through: `1.0.0` and
        `abcdef123456` are each orderable, so both guards passed, but the pair
        is incomparable, the comparator failed closed, and the negation
        reported "up to date" -- returning the stale npm cache for what is now
        a docker server. `refresh_all` reuses a cache entry by server NAME and
        never checks that the package still matches, which is what makes this
        reachable.
        """
        existing = GeneratedServerDescriptions(
            package="srv",
            version="1.0.0",
            generated_at="2025-01-01T00:00:00Z",
            capability_summary="stale npm summary",
            tools=[],
        )
        server = ServerConfig(
            name="srv",
            description="",
            keywords=[],
            install={},
            command="docker",
            args=["run", "srv"],
        )
        calls: list[tuple] = []

        async def fake_version(command, args, timeout=None):
            calls.append((command, tuple(args)))
            return ("abcdef123456", "docker")

        with patch(
            "pmcp.manifest.refresher.get_package_version", side_effect=fake_version
        ):
            with patch(
                "mcp.client.stdio.stdio_client",
                side_effect=RuntimeError("stop after the short-circuit decision"),
            ):
                await refresh_server(server, existing_cache=existing)

        assert len(calls) >= 2, (
            "short-circuited as up-to-date across an incomparable "
            f"version/digest pair (calls={calls})"
        )


class TestShortCircuitUsesCompareVersions:
    """SL-1.3 (Consiliency/pmcp#164) migrates the short-circuit onto the
    tri-state `compare_versions` directly, replacing the `are_versions_
    comparable(...) and not is_version_newer(...)` pair.

    RED until `refresher.py` imports and calls `compare_versions` -- `patch`
    with the default `create=False` refuses to stand in for an attribute
    that does not exist yet, which is the point: this fails for the right
    reason (the migration hasn't happened) rather than a wrong one.
    """

    @pytest.mark.asyncio
    async def test_short_circuit_is_a_single_compare_versions_call(
        self, temp_dir: Path
    ) -> None:
        existing = GeneratedServerDescriptions(
            package="srv",
            # The identity gate added in Consiliency/pmcp#178 runs ahead of the
            # comparison, so the pair has to be confirmably the same package
            # for the short-circuit to be reached at all. `result is existing`
            # below is what catches a degenerate always-False gate.
            package_type="npm",
            version="1.0.0",
            generated_at="2025-01-01T00:00:00Z",
            capability_summary="stale summary",
            tools=[],
        )
        server = ServerConfig(
            name="srv",
            description="",
            keywords=[],
            install={},
            command="npx",
            args=["srv"],
        )

        async def fake_version(command, args, timeout=None):
            return ("1.0.0", "npm")

        calls: list[tuple] = []

        def fake_compare(current, latest, package_type=None):
            calls.append((current, latest, package_type))
            return "not_newer"

        with patch(
            "pmcp.manifest.refresher.get_package_version", side_effect=fake_version
        ):
            with patch(
                "pmcp.manifest.refresher.compare_versions", side_effect=fake_compare
            ):
                result = await refresh_server(server, existing_cache=existing)

        assert calls == [("1.0.0", "1.0.0", "npm")], (
            "the short-circuit must resolve to exactly one compare_versions "
            f"call over (cached, fetched, package_type), got {calls}"
        )
        assert result is existing


class TestRefreshAll:
    @pytest.mark.asyncio
    async def test_refresh_all_uses_bounded_concurrency_for_version_checks(
        self, temp_dir: Path
    ) -> None:
        active = 0
        peak_active = 0
        calls: list[str] = []

        async def fake_refresh(
            server_config: ServerConfig,
            existing_cache: GeneratedServerDescriptions | None = None,
            force: bool = False,
        ) -> GeneratedServerDescriptions:
            nonlocal active, peak_active
            active += 1
            peak_active = max(peak_active, active)
            calls.append(server_config.name)
            await asyncio.sleep(0.01)
            active -= 1
            return GeneratedServerDescriptions(
                package=server_config.name,
                version="1.0.0",
                generated_at="2025-01-01T00:00:00Z",
                capability_summary=server_config.name,
                tools=[],
            )

        manifest = Manifest(
            version="1.0",
            cli_alternatives={},
            servers={
                f"server-{i}": ServerConfig(
                    name=f"server-{i}",
                    description="",
                    keywords=[],
                    install={},
                    command="npx",
                    args=[f"server-{i}"],
                )
                for i in range(4)
            },
            discovery_queue_path=".mcp-gateway/discovery_queue.json",
        )

        with patch("pmcp.manifest.refresher.refresh_server", side_effect=fake_refresh):
            cache = await refresh_all(
                manifest=manifest, cache_path=temp_dir / "cache.yaml"
            )

        assert peak_active > 1
        assert calls == ["server-0", "server-1", "server-2", "server-3"]
        assert list(cache.servers) == calls


class TestGeneratedDescriptionsTypes:
    """Tests for description type structures."""

    def test_prebuilt_tool_info_creation(self) -> None:
        """Test PrebuiltToolInfo creation."""
        tool = PrebuiltToolInfo(
            name="test_tool",
            description="A test tool",
            short_description="A test",
            tags=["test"],
            risk_hint="low",
        )
        assert tool.name == "test_tool"
        assert tool.risk_hint == "low"

    def test_generated_server_descriptions_creation(self) -> None:
        """Test GeneratedServerDescriptions creation."""
        desc = GeneratedServerDescriptions(
            package="@test/mcp",
            version="1.0.0",
            generated_at="2025-01-01T00:00:00Z",
            capability_summary="Test capabilities",
            tools=[
                PrebuiltToolInfo(
                    name="tool1",
                    description="Tool 1",
                    short_description="Tool 1",
                    tags=["test"],
                    risk_hint="low",
                )
            ],
        )
        assert desc.package == "@test/mcp"
        assert len(desc.tools) == 1

    def test_descriptions_cache_creation(self) -> None:
        """Test DescriptionsCache creation."""
        cache = DescriptionsCache(
            generated_at="2025-01-01T00:00:00Z",
            gateway_version=GATEWAY_VERSION,
            servers={},
        )
        assert cache.gateway_version == GATEWAY_VERSION
        assert cache.servers == {}


def _server_config(command: str, args: list[str], name: str = "srv") -> ServerConfig:
    """A minimal manifest server entry."""
    return ServerConfig(
        name=name,
        description="",
        keywords=[],
        install={},
        command=command,
        args=args,
    )


def _cached_entry(
    package: str,
    package_type: str | None,
    version: str = "1.0.0",
) -> GeneratedServerDescriptions:
    """A cached description entry for `package`, as the cache would hold it."""
    return GeneratedServerDescriptions(
        package=package,
        package_type=package_type,
        version=version,
        generated_at="2025-01-01T00:00:00Z",
        capability_summary=f"stale {package or '<empty>'} summary",
        tools=[],
    )


def _write_cache_file(
    cache_path: Path,
    entries: dict[str, tuple[str, str | None]],
) -> None:
    """Write a descriptions cache holding `name -> (package, package_type)`.

    Written as raw YAML rather than through `save_descriptions_cache` so the
    fixture does not depend on the writer this lane is also changing.
    """
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(
        yaml.safe_dump(
            {
                "generated_at": "2025-01-01T00:00:00Z",
                "gateway_version": "1.0.0",
                "servers": {
                    name: {
                        "package": package,
                        "package_type": package_type,
                        "version": "1.0.0",
                        "generated_at": "2025-01-01T00:00:00Z",
                        "capability_summary": f"stale {package} summary",
                        "tools": [],
                    }
                    for name, (package, package_type) in entries.items()
                },
            }
        )
    )


def _manifest(servers: dict[str, ServerConfig]) -> Manifest:
    return Manifest(
        version="1.0",
        cli_alternatives={},
        servers=servers,
        discovery_queue_path=".mcp-gateway/discovery_queue.json",
    )


async def _equal_version(command: str, args: list[str], timeout: float | None = None):
    """Every package resolves to the same npm version -- so only identity can
    distinguish the cached entry from the configured one."""
    return ("1.0.0", "npm")


class TestPackageIdentityGate:
    """A cached entry is paired with a server config by NAME, and freshness is
    then decided by comparing VERSIONS only. Nothing asks whether the cache
    still describes the same PACKAGE (Consiliency/pmcp#178, EC-UPDPATH-1..3).

    Two swaps slip past a versions-only check at an equal version:

    1. **Same ecosystem** -- `old-pkg@1.0.0` cached, `new-pkg@1.0.0`
       configured, both npm, both orderable, equal.
    2. **Cross ecosystem** -- `GeneratedServerDescriptions.package` is a bare
       name with no ecosystem, so pypi `foo@1.0.0` reads as the same package as
       npm `foo@1.0.0`, and npm/pypi/cargo all produce orderable *release*
       versions so `incomparable` never fires.

    The docker case needs no test here: a version against a digest classifies
    as `compare_versions(...) == "incomparable"`, which is not `"not_newer"`,
    so the short-circuit already does not fire (`TestUpToDateShortCircuit`).

    `refresh_server` returning `None` is the discriminator throughout: a
    short-circuit returns the cached object itself, while a real refresh
    reaches `stdio_client` -- patched here to raise -- and is caught into
    `None`. Asserting on the return value alone cannot otherwise tell
    "refreshed" from "returned the stale cache", since both yield a cache
    object.
    """

    @pytest.mark.asyncio
    async def test_same_ecosystem_swap_refreshes_via_refresh_server(self) -> None:
        existing = _cached_entry("old-pkg", "npm")
        server = _server_config("npx", ["-y", "new-pkg"])

        with patch(
            "pmcp.manifest.refresher.get_package_version", side_effect=_equal_version
        ):
            with patch(
                "mcp.client.stdio.stdio_client",
                side_effect=RuntimeError("stop after the identity decision"),
            ):
                result = await refresh_server(server, existing_cache=existing)

        assert result is not existing, (
            "short-circuited as up to date across an npm package swap at an "
            "equal version -- old-pkg's descriptions would be served for "
            "new-pkg forever"
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_cross_ecosystem_swap_refreshes_via_refresh_server(self) -> None:
        """Same bare name, different ecosystem, equal orderable versions."""
        existing = _cached_entry("foo", "pypi")
        server = _server_config("npx", ["-y", "foo"])

        with patch(
            "pmcp.manifest.refresher.get_package_version", side_effect=_equal_version
        ):
            with patch(
                "mcp.client.stdio.stdio_client",
                side_effect=RuntimeError("stop after the identity decision"),
            ):
                result = await refresh_server(server, existing_cache=existing)

        assert result is not existing, (
            "short-circuited across a pypi -> npm swap of the same bare name: "
            "a name alone cannot express package identity"
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_same_ecosystem_swap_is_reported_stale_by_check_staleness(
        self,
    ) -> None:
        """`pmcp refresh --check-versions` must not print "up to date" here."""
        cache = DescriptionsCache(
            generated_at="2025-01-01T00:00:00Z",
            gateway_version="1.0.0",
            servers={"srv": _cached_entry("old-pkg", "npm")},
        )
        manifest = _manifest({"srv": _server_config("npx", ["-y", "new-pkg"])})

        with patch(
            "pmcp.manifest.refresher.load_descriptions_cache", return_value=cache
        ):
            with patch(
                "pmcp.manifest.refresher.get_package_version",
                new_callable=AsyncMock,
                return_value=("1.0.0", "npm"),
            ):
                result = await check_staleness(manifest=manifest)

        assert "srv" in result, (
            "a cache for old-pkg against a config for new-pkg reported as up "
            "to date -- operator-visible via `pmcp refresh --check-versions`"
        )
        assert result["srv"] == ("1.0.0", "1.0.0")

    @pytest.mark.asyncio
    async def test_cross_ecosystem_swap_is_reported_stale_by_check_staleness(
        self,
    ) -> None:
        cache = DescriptionsCache(
            generated_at="2025-01-01T00:00:00Z",
            gateway_version="1.0.0",
            servers={"srv": _cached_entry("foo", "pypi")},
        )
        manifest = _manifest({"srv": _server_config("npx", ["-y", "foo"])})

        with patch(
            "pmcp.manifest.refresher.load_descriptions_cache", return_value=cache
        ):
            with patch(
                "pmcp.manifest.refresher.get_package_version",
                new_callable=AsyncMock,
                return_value=("1.0.0", "npm"),
            ):
                result = await check_staleness(manifest=manifest)

        assert "srv" in result, (
            "a pypi cache against an npm config of the same bare name "
            "reported as up to date"
        )

    @pytest.mark.asyncio
    async def test_swap_does_not_survive_refresh_all(self) -> None:
        """EC-3: `refresh_all` assembles the (cached, configured) pair itself,
        so it must not be able to bypass the gate."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "descriptions.yaml"
            _write_cache_file(cache_path, {"srv": ("old-pkg", "npm")})
            manifest = _manifest({"srv": _server_config("npx", ["-y", "new-pkg"])})

            with patch(
                "pmcp.manifest.refresher.get_package_version",
                side_effect=_equal_version,
            ):
                with patch(
                    "mcp.client.stdio.stdio_client",
                    side_effect=RuntimeError("no server to connect to"),
                ):
                    cache = await refresh_all(manifest=manifest, cache_path=cache_path)

            returned = cache.servers.get("srv")
            assert returned is None or returned.package != "old-pkg", (
                "refresh_all returned old-pkg's descriptions for a server now "
                "configured to run new-pkg"
            )

            reloaded = load_descriptions_cache(cache_path)
            assert reloaded is not None
            saved = reloaded.servers.get("srv")
            assert saved is None or saved.package != "old-pkg", (
                "refresh_all wrote old-pkg's descriptions back to disk"
            )

    @pytest.mark.asyncio
    async def test_cross_ecosystem_swap_does_not_survive_refresh_all(self) -> None:
        """The cross-ecosystem swap at the third site: same bare name, so only
        the recorded type distinguishes the cached entry from the config."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "descriptions.yaml"
            _write_cache_file(cache_path, {"srv": ("foo", "pypi")})
            manifest = _manifest({"srv": _server_config("npx", ["-y", "foo"])})

            with patch(
                "pmcp.manifest.refresher.get_package_version",
                side_effect=_equal_version,
            ):
                with patch(
                    "mcp.client.stdio.stdio_client",
                    side_effect=RuntimeError("no server to connect to"),
                ):
                    cache = await refresh_all(manifest=manifest, cache_path=cache_path)

            # The bare name is identical on both sides, so asserting on
            # `package` cannot tell the entries apart -- the pypi entry either
            # survived regeneration or it did not.
            assert "srv" not in cache.servers, (
                "refresh_all kept the pypi cache for a server now configured "
                "to run the npm package of the same name"
            )

            reloaded = load_descriptions_cache(cache_path)
            assert reloaded is None or "srv" not in reloaded.servers, (
                "refresh_all wrote the pypi descriptions back to disk"
            )

    def test_package_type_survives_a_cache_round_trip(self) -> None:
        """IF-0-UPDPATH-2: populated where the entry is WRITTEN, not only where
        it is read -- otherwise every refreshed entry reloads as unknown and
        the gate refreshes forever."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "descriptions.yaml"
            save_descriptions_cache(
                DescriptionsCache(
                    generated_at="2025-01-01T00:00:00Z",
                    gateway_version=GATEWAY_VERSION,
                    servers={"srv": _cached_entry("new-pkg", "npm")},
                ),
                cache_path,
            )

            reloaded = load_descriptions_cache(cache_path)
            assert reloaded is not None
            assert reloaded.servers["srv"].package_type == "npm"

    def test_absent_package_type_reads_as_none_not_empty_string(self) -> None:
        """An absent value must stay distinguishable from a recorded empty one
        (IF-0-UPDPATH-2), so pre-phase caches read as unknown."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "descriptions.yaml"
            cache_path.write_text(
                yaml.safe_dump(
                    {
                        "generated_at": "2025-01-01T00:00:00Z",
                        "gateway_version": "1.0.0",
                        "servers": {
                            "srv": {
                                "package": "new-pkg",
                                "version": "1.0.0",
                                "generated_at": "2025-01-01T00:00:00Z",
                                "capability_summary": "legacy entry",
                                "tools": [],
                            }
                        },
                    }
                )
            )

            reloaded = load_descriptions_cache(cache_path)
            assert reloaded is not None
            assert reloaded.servers["srv"].package_type is None

    @pytest.mark.asyncio
    async def test_regenerated_entry_records_its_package_type(self) -> None:
        """A freshly generated entry carries the type it was generated for."""
        server = _server_config("npx", ["-y", "new-pkg"])

        class _FakeStdio:
            async def __aenter__(self):
                return (None, None)

            async def __aexit__(self, *exc):
                return False

        tools_result = MagicMock()
        tools_result.tools = []
        fake_session = MagicMock()
        fake_session.initialize = AsyncMock()
        fake_session.list_tools = AsyncMock(return_value=tools_result)

        class _FakeSession:
            def __init__(self, *args, **kwargs) -> None:
                pass

            async def __aenter__(self):
                return fake_session

            async def __aexit__(self, *exc):
                return False

        async def fake_version(command, args, timeout=None):
            return ("2.0.0", "npm")

        with patch(
            "pmcp.manifest.refresher.get_package_version", side_effect=fake_version
        ):
            with patch("mcp.client.stdio.stdio_client", return_value=_FakeStdio()):
                with patch("mcp.ClientSession", _FakeSession):
                    result = await refresh_server(server)

        assert result is not None
        assert result.package == "new-pkg"
        assert result.package_type == "npm"


class TestUnknownPackageForcesRefresh:
    """EC-UPDPATH-7. The gate must read an unknown side as "cannot confirm
    identity -> refresh", NEVER as "cannot compare -> skip the check". The
    second phrasing is the natural one to reach for, passes a naive suite, and
    is the same fail-open collapse as `not is_version_newer(...)`, which
    shipped three times (Consiliency/pmcp#155, #156, #163) before the tri-state
    migration deleted the wrappers to make it unrepresentable.

    `test_absent_cached_package_type_forces_refresh` is the discriminating one:
    the package NAMES match and the VERSIONS match, so only the unknown type
    can force the refresh. Under "only compare when both sides are known" it
    short-circuits and the test fails.

    The configured-unknown arms have no possible behavioural test --
    `refresh_server` falls back to `pkg_name = f"{command} {args}"`, so the
    configured name is never empty -- which is why the predicate is asserted
    directly below.
    """

    @pytest.mark.asyncio
    async def test_absent_cached_package_type_forces_refresh(self) -> None:
        existing = _cached_entry("new-pkg", None)
        server = _server_config("npx", ["-y", "new-pkg"])

        with patch(
            "pmcp.manifest.refresher.get_package_version", side_effect=_equal_version
        ):
            with patch(
                "mcp.client.stdio.stdio_client",
                side_effect=RuntimeError("stop after the identity decision"),
            ):
                result = await refresh_server(server, existing_cache=existing)

        assert result is not existing, (
            "an unrecorded package_type was read as 'cannot compare, skip the "
            "check' rather than 'cannot confirm identity, refresh'"
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_unknown_cached_package_type_forces_refresh(self) -> None:
        existing = _cached_entry("new-pkg", "unknown")
        server = _server_config("npx", ["-y", "new-pkg"])

        with patch(
            "pmcp.manifest.refresher.get_package_version", side_effect=_equal_version
        ):
            with patch(
                "mcp.client.stdio.stdio_client",
                side_effect=RuntimeError("stop after the identity decision"),
            ):
                result = await refresh_server(server, existing_cache=existing)

        assert result is not existing
        assert result is None

    @pytest.mark.asyncio
    async def test_empty_cached_package_forces_refresh(self) -> None:
        existing = _cached_entry("", "npm")
        server = _server_config("npx", ["-y", "new-pkg"])

        with patch(
            "pmcp.manifest.refresher.get_package_version", side_effect=_equal_version
        ):
            with patch(
                "mcp.client.stdio.stdio_client",
                side_effect=RuntimeError("stop after the identity decision"),
            ):
                result = await refresh_server(server, existing_cache=existing)

        assert result is not existing
        assert result is None

    @pytest.mark.asyncio
    async def test_absent_cached_package_type_is_stale_via_check_staleness(
        self,
    ) -> None:
        """Every cache written before this phase reads as unknown, so the
        migration forces one refresh rather than silently trusting the entry."""
        cache = DescriptionsCache(
            generated_at="2025-01-01T00:00:00Z",
            gateway_version="1.0.0",
            servers={"srv": _cached_entry("new-pkg", None)},
        )
        manifest = _manifest({"srv": _server_config("npx", ["-y", "new-pkg"])})

        with patch(
            "pmcp.manifest.refresher.load_descriptions_cache", return_value=cache
        ):
            with patch(
                "pmcp.manifest.refresher.get_package_version",
                new_callable=AsyncMock,
                return_value=("1.0.0", "npm"),
            ):
                result = await check_staleness(manifest=manifest)

        assert "srv" in result

    def test_predicate_matches_a_fully_known_identical_pair(self) -> None:
        from pmcp.manifest.refresher import _same_package

        assert _same_package("x", "npm", "x", "npm") is True

    def test_predicate_refuses_every_unknown_arm(self) -> None:
        from pmcp.manifest.refresher import _same_package

        # cached name unknown
        assert _same_package("", None, "x", "npm") is False
        assert _same_package("", "npm", "x", "npm") is False
        assert _same_package("unknown", "npm", "unknown", "npm") is False
        # cached type unknown, name known on both sides
        assert _same_package("x", None, "x", "npm") is False
        assert _same_package("x", "", "x", "npm") is False
        assert _same_package("x", "unknown", "x", "unknown") is False
        # configured name unknown
        assert _same_package("x", "npm", None, "npm") is False
        assert _same_package("x", "npm", "", "npm") is False
        # configured type unknown
        assert _same_package("x", "npm", "x", None) is False
        assert _same_package("x", "npm", "x", "") is False
        assert _same_package("x", "npm", "x", "unknown") is False

    def test_predicate_refuses_a_differing_pair(self) -> None:
        from pmcp.manifest.refresher import _same_package

        assert _same_package("x", "npm", "y", "npm") is False
        assert _same_package("foo", "pypi", "foo", "npm") is False


class TestMismatchedCacheNeverSurvivesFailure:
    """`refresh_all` has two paths that put a cached entry into the saved cache
    without regenerating it: the `None`/raise fallback inside `refresh_target`,
    and the final merge loop that re-adds every cached entry missing from the
    new set. An identity-mismatched entry must survive neither.

    Both tests below require both fixes: closing only the fallback lets the
    merge loop write the entry back, and closing only the merge loop leaves the
    fallback returning it as a live result. Dropping the entry on a failed
    regeneration is acceptable; writing the wrong package's descriptions back
    is not.
    """

    @pytest.mark.asyncio
    async def test_failed_refresh_does_not_write_mismatched_entry_back(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "descriptions.yaml"
            _write_cache_file(cache_path, {"srv": ("old-pkg", "npm")})
            manifest = _manifest({"srv": _server_config("npx", ["-y", "new-pkg"])})

            async def failing_refresh(server_config, existing_cache=None, force=False):
                return None

            with patch(
                "pmcp.manifest.refresher.refresh_server", side_effect=failing_refresh
            ):
                cache = await refresh_all(manifest=manifest, cache_path=cache_path)

            assert "srv" not in cache.servers, (
                "a failed regeneration handed old-pkg's descriptions back for a "
                "server now configured to run new-pkg"
            )
            reloaded = load_descriptions_cache(cache_path)
            assert reloaded is None or "srv" not in reloaded.servers

    @pytest.mark.asyncio
    async def test_raising_refresh_does_not_write_mismatched_entry_back(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "descriptions.yaml"
            _write_cache_file(cache_path, {"srv": ("old-pkg", "npm")})
            manifest = _manifest({"srv": _server_config("npx", ["-y", "new-pkg"])})

            async def raising_refresh(server_config, existing_cache=None, force=False):
                raise RuntimeError("regeneration blew up")

            with patch(
                "pmcp.manifest.refresher.refresh_server", side_effect=raising_refresh
            ):
                cache = await refresh_all(manifest=manifest, cache_path=cache_path)

            assert "srv" not in cache.servers
            reloaded = load_descriptions_cache(cache_path)
            assert reloaded is None or "srv" not in reloaded.servers

    @pytest.mark.asyncio
    async def test_matching_entry_still_survives_a_failed_refresh(self) -> None:
        """The gate must drop mismatched entries only -- a degenerate
        always-drop would pass the two tests above for the wrong reason."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "descriptions.yaml"
            _write_cache_file(cache_path, {"srv": ("new-pkg", "npm")})
            manifest = _manifest({"srv": _server_config("npx", ["-y", "new-pkg"])})

            async def failing_refresh(server_config, existing_cache=None, force=False):
                return None

            with patch(
                "pmcp.manifest.refresher.refresh_server", side_effect=failing_refresh
            ):
                cache = await refresh_all(manifest=manifest, cache_path=cache_path)

            assert cache.servers["srv"].package == "new-pkg"

    @pytest.mark.asyncio
    async def test_untargeted_entry_still_survives_the_merge(self) -> None:
        """The merge loop's purpose -- keeping servers outside the target list
        -- must be preserved."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "descriptions.yaml"
            _write_cache_file(
                cache_path,
                {"srv": ("new-pkg", "npm"), "other": ("other-pkg", "npm")},
            )
            manifest = _manifest(
                {
                    "srv": _server_config("npx", ["-y", "new-pkg"]),
                    "other": _server_config("npx", ["-y", "other-pkg"], name="other"),
                }
            )

            async def failing_refresh(server_config, existing_cache=None, force=False):
                return None

            with patch(
                "pmcp.manifest.refresher.refresh_server", side_effect=failing_refresh
            ):
                cache = await refresh_all(
                    manifest=manifest, cache_path=cache_path, servers=["srv"]
                )

            assert cache.servers["other"].package == "other-pkg"


# Pytest fixture from conftest
@pytest.fixture
def temp_dir() -> Path:
    """Create a temporary directory for test files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)
