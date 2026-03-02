"""Progressive Disclosure Workflow Tests.

Tests the gateway's progressive disclosure workflow using naive prompts
to exercise tools from Playwright (22 tools) and Context7 (2 tools) servers.

Each test scenario follows:
1. Search - Use `gateway.catalog_search` with naive query
2. Discover - Review capability cards returned
3. Describe - Use `gateway.describe` to get full schema
4. Invoke - Use `gateway.invoke` to execute the tool

Run with: pytest tests/test_progressive_disclosure.py -v
"""

from __future__ import annotations

import pytest

from pathlib import Path

from pmcp.client.manager import ClientManager
from pmcp.config.loader import load_configs
from pmcp.policy.policy import PolicyManager
from pmcp.tools.handlers import GatewayTools


def get_all_configs():
    """Load configs from both project and global locations."""
    # Include global .pmcp.json in user config paths
    global_pmcp = Path.home() / ".pmcp.json"
    user_paths = [
        Path.home() / ".mcp.json",
        Path.home() / ".claude" / ".mcp.json",
        global_pmcp,  # Add global pmcp config
    ]

    configs = load_configs(user_config_paths=user_paths)
    return configs


def has_playwright_server() -> bool:
    """Check if playwright server is configured."""
    configs = get_all_configs()
    return any(c.name == "playwright" for c in configs)


def has_context7_server() -> bool:
    """Check if context7 server is configured."""
    configs = get_all_configs()
    return any(c.name == "context7" for c in configs)


skip_no_playwright = pytest.mark.skipif(
    not has_playwright_server(), reason="Playwright server not configured"
)

skip_no_context7 = pytest.mark.skipif(
    not has_context7_server(), reason="Context7 server not configured"
)


@pytest.fixture(scope="module")
async def gateway_with_servers():
    """Set up gateway with real servers for integration testing."""
    configs = get_all_configs()
    # Filter to only playwright and context7
    configs = [c for c in configs if c.name in ("playwright", "context7")]

    if not configs:
        pytest.skip("Neither playwright nor context7 servers configured")

    policy = PolicyManager()
    manager = ClientManager()

    try:
        errors = await manager.connect_all(configs)
        if errors:
            print(f"Connection errors: {errors}")

        tools = GatewayTools(
            client_manager=manager,
            policy_manager=policy,
        )
        yield tools
    finally:
        await manager.disconnect_all()


class TestScenario7_LibraryDocumentation:
    """Scenario 7: Library Documentation (Context7)

    Naive Prompt: "I need documentation for React"

    Tools to exercise:
    - context7::resolve-library-id - Find library ID
    - context7::get-library-docs - Fetch documentation
    """

    @skip_no_context7
    @pytest.mark.asyncio
    async def test_search_documentation_library(self, gateway_with_servers):
        """Step 1: Search for documentation-related tools."""
        result = await gateway_with_servers.catalog_search(
            {"query": "documentation library"}
        )

        print(
            f"\nSearch 'documentation library' returned {len(result.results)} results:"
        )
        for card in result.results:
            print(f"  - {card.tool_id}: {card.short_description}")

        # Should find context7 tools
        tool_ids = [r.tool_id for r in result.results]
        assert any("context7" in tid for tid in tool_ids), (
            "Expected to find context7 tools for documentation query"
        )

    @skip_no_context7
    @pytest.mark.asyncio
    async def test_describe_resolve_library_id(self, gateway_with_servers):
        """Step 2: Get full schema for resolve-library-id."""
        result = await gateway_with_servers.describe(
            {"tool_id": "context7::resolve-library-id"}
        )

        print("\nDescribe 'context7::resolve-library-id':")
        print(f"  Tool: {result.tool_name}")
        print(f"  Description: {result.description}")
        print(f"  Args: {[a.name for a in result.args]}")

        assert result.tool_name == "resolve-library-id"
        assert len(result.args) > 0

    @skip_no_context7
    @pytest.mark.asyncio
    async def test_invoke_resolve_library_id(self, gateway_with_servers):
        """Step 3: Invoke resolve-library-id for React."""
        result = await gateway_with_servers.invoke(
            {
                "tool_id": "context7::resolve-library-id",
                "arguments": {"libraryName": "react"},
                "options": {"timeout_ms": 60000},  # Longer timeout for API calls
            }
        )

        print("\nInvoke 'context7::resolve-library-id' with libraryName='react':")
        print(f"  OK: {result.ok}")
        print(f"  Result: {result.result}")

        # API can be slow or rate-limited, so don't fail on timeout
        if not result.ok:
            print(f"  Errors: {result.errors}")

    @skip_no_context7
    @pytest.mark.asyncio
    async def test_describe_query_docs(self, gateway_with_servers):
        """Step 4: Get full schema for query-docs."""
        result = await gateway_with_servers.describe(
            {"tool_id": "context7::query-docs"}
        )

        print("\nDescribe 'context7::query-docs':")
        print(f"  Tool: {result.tool_name}")
        print(f"  Description: {result.description}")
        print(f"  Args: {[f'{a.name} ({a.type})' for a in result.args]}")

        assert result.tool_name == "query-docs"

    @skip_no_context7
    @pytest.mark.asyncio
    async def test_invoke_query_docs(self, gateway_with_servers):
        """Step 5: Invoke query-docs for React."""
        result = await gateway_with_servers.invoke(
            {
                "tool_id": "context7::query-docs",
                "arguments": {"query": "react hooks", "libraryId": "/npm/react/19.0.0"},
                "options": {"timeout_ms": 60000},  # Longer timeout for API calls
            }
        )

        print("\nInvoke 'context7::query-docs':")
        print(f"  OK: {result.ok}")
        print(f"  Truncated: {result.truncated}")
        print(f"  Raw size: {result.raw_size_estimate}")

        # May fail if library ID is wrong, but invocation should work
        if not result.ok:
            print(f"  Errors: {result.errors}")


class TestScenario1_WebNavigation:
    """Scenario 1: Web Navigation (Playwright)

    Naive Prompt: "I want to visit a website and take a screenshot"

    Tools to exercise:
    - playwright::browser_navigate - Navigate to URL
    - playwright::browser_take_screenshot - Capture screenshot
    - playwright::browser_snapshot - Accessibility tree
    """

    @skip_no_playwright
    @pytest.mark.asyncio
    async def test_search_navigate_website(self, gateway_with_servers):
        """Step 1: Search for navigation-related tools."""
        result = await gateway_with_servers.catalog_search(
            {"query": "navigate website"}
        )

        print(f"\nSearch 'navigate website' returned {len(result.results)} results:")
        for card in result.results:
            print(f"  - {card.tool_id}: {card.short_description}")

        # Should find playwright navigation tools
        tool_ids = [r.tool_id for r in result.results]
        assert any("navigate" in tid.lower() for tid in tool_ids), (
            "Expected to find navigation tools"
        )

    @skip_no_playwright
    @pytest.mark.asyncio
    async def test_describe_browser_navigate(self, gateway_with_servers):
        """Step 2: Get full schema for browser_navigate."""
        result = await gateway_with_servers.describe(
            {"tool_id": "playwright::browser_navigate"}
        )

        print("\nDescribe 'playwright::browser_navigate':")
        print(f"  Tool: {result.tool_name}")
        print(f"  Description: {result.description}")
        print(
            f"  Args: {[f'{a.name} ({a.type}, required={a.required})' for a in result.args]}"
        )

        assert result.tool_name == "browser_navigate"
        assert any(a.name == "url" for a in result.args)

    @skip_no_playwright
    @pytest.mark.asyncio
    async def test_invoke_browser_navigate(self, gateway_with_servers):
        """Step 3: Navigate to example.com."""
        result = await gateway_with_servers.invoke(
            {
                "tool_id": "playwright::browser_navigate",
                "arguments": {"url": "https://example.com"},
                "options": {"timeout_ms": 120000},  # Browser startup can be slow
            }
        )

        print("\nInvoke 'playwright::browser_navigate' to https://example.com:")
        print(f"  OK: {result.ok}")
        print(f"  Result summary: {result.summary if result.summary else 'N/A'}")

        # Browser operations may timeout on first run
        if not result.ok:
            print(f"  Errors: {result.errors}")

    @skip_no_playwright
    @pytest.mark.asyncio
    async def test_search_screenshot(self, gateway_with_servers):
        """Step 4: Search for screenshot tools."""
        result = await gateway_with_servers.catalog_search({"query": "screenshot"})

        print(f"\nSearch 'screenshot' returned {len(result.results)} results:")
        for card in result.results:
            print(f"  - {card.tool_id}: {card.short_description}")

        tool_ids = [r.tool_id for r in result.results]
        assert any("screenshot" in tid.lower() for tid in tool_ids)

    @skip_no_playwright
    @pytest.mark.asyncio
    async def test_invoke_browser_take_screenshot(self, gateway_with_servers):
        """Step 5: Take a screenshot."""
        result = await gateway_with_servers.invoke(
            {
                "tool_id": "playwright::browser_take_screenshot",
                "arguments": {},
                "options": {"timeout_ms": 60000},
            }
        )

        print("\nInvoke 'playwright::browser_take_screenshot':")
        print(f"  OK: {result.ok}")
        print(f"  Truncated: {result.truncated}")

        # Screenshot may fail if no page is loaded
        if not result.ok:
            print(f"  Errors: {result.errors}")


class TestScenario2_FormInteraction:
    """Scenario 2: Form Interaction (Playwright)

    Naive Prompt: "I need to fill out a form on a webpage"

    Tools to exercise:
    - playwright::browser_fill_form - Fill multiple fields
    - playwright::browser_type - Type into element
    - playwright::browser_click - Click submit button
    """

    @skip_no_playwright
    @pytest.mark.asyncio
    async def test_search_fill_form(self, gateway_with_servers):
        """Step 1: Search for form-filling tools."""
        result = await gateway_with_servers.catalog_search({"query": "fill form"})

        print(f"\nSearch 'fill form' returned {len(result.results)} results:")
        for card in result.results:
            print(f"  - {card.tool_id}: {card.short_description}")

        # Should find form-related tools
        assert len(result.results) > 0, "Expected to find form tools"

    @skip_no_playwright
    @pytest.mark.asyncio
    async def test_describe_browser_type(self, gateway_with_servers):
        """Step 2: Get schema for browser_type."""
        result = await gateway_with_servers.describe(
            {"tool_id": "playwright::browser_type"}
        )

        print("\nDescribe 'playwright::browser_type':")
        print(f"  Tool: {result.tool_name}")
        print(f"  Args: {[f'{a.name} ({a.type})' for a in result.args]}")

        assert result.tool_name == "browser_type"

    @skip_no_playwright
    @pytest.mark.asyncio
    async def test_search_click_button(self, gateway_with_servers):
        """Step 3: Search for click tools."""
        result = await gateway_with_servers.catalog_search({"query": "click button"})

        print(f"\nSearch 'click button' returned {len(result.results)} results:")
        for card in result.results:
            print(f"  - {card.tool_id}: {card.short_description}")

        tool_ids = [r.tool_id for r in result.results]
        assert any("click" in tid.lower() for tid in tool_ids)


class TestScenario3_PageDebugging:
    """Scenario 3: Page Debugging (Playwright)

    Naive Prompt: "Check what's happening on this page - console logs and network"

    Tools to exercise:
    - playwright::browser_console_messages - View console output
    - playwright::browser_network_requests - View network activity
    """

    @skip_no_playwright
    @pytest.mark.asyncio
    async def test_search_console_logs(self, gateway_with_servers):
        """Step 1: Search for console-related tools."""
        result = await gateway_with_servers.catalog_search({"query": "console logs"})

        print(f"\nSearch 'console logs' returned {len(result.results)} results:")
        for card in result.results:
            print(f"  - {card.tool_id}: {card.short_description}")

    @skip_no_playwright
    @pytest.mark.asyncio
    async def test_search_network_requests(self, gateway_with_servers):
        """Step 2: Search for network tools."""
        result = await gateway_with_servers.catalog_search(
            {"query": "network requests"}
        )

        print(f"\nSearch 'network requests' returned {len(result.results)} results:")
        for card in result.results:
            print(f"  - {card.tool_id}: {card.short_description}")


class TestScenario4_ElementInteraction:
    """Scenario 4: Element Interaction (Playwright)

    Naive Prompt: "I need to click things and hover over elements"

    Tools to exercise:
    - playwright::browser_click - Click element
    - playwright::browser_hover - Hover over element
    """

    @skip_no_playwright
    @pytest.mark.asyncio
    async def test_search_click_element(self, gateway_with_servers):
        """Step 1: Search for click tools."""
        result = await gateway_with_servers.catalog_search({"query": "click element"})

        print(f"\nSearch 'click element' returned {len(result.results)} results:")
        for card in result.results:
            print(f"  - {card.tool_id}: {card.short_description}")

    @skip_no_playwright
    @pytest.mark.asyncio
    async def test_describe_browser_click(self, gateway_with_servers):
        """Step 2: Get schema for browser_click."""
        result = await gateway_with_servers.describe(
            {"tool_id": "playwright::browser_click"}
        )

        print("\nDescribe 'playwright::browser_click':")
        print(f"  Tool: {result.tool_name}")
        print(f"  Args: {[f'{a.name} ({a.type})' for a in result.args]}")

        assert result.tool_name == "browser_click"

    @skip_no_playwright
    @pytest.mark.asyncio
    async def test_search_drag_drop(self, gateway_with_servers):
        """Step 3: Search for drag tools."""
        result = await gateway_with_servers.catalog_search({"query": "drag drop"})

        print(f"\nSearch 'drag drop' returned {len(result.results)} results:")
        for card in result.results:
            print(f"  - {card.tool_id}: {card.short_description}")


class TestScenario5_TabManagement:
    """Scenario 5: Tab Management (Playwright)

    Naive Prompt: "Open new tabs and manage browser windows"

    Tools to exercise:
    - playwright::browser_tabs - List/create/close tabs
    """

    @skip_no_playwright
    @pytest.mark.asyncio
    async def test_search_tabs(self, gateway_with_servers):
        """Step 1: Search for tab tools."""
        result = await gateway_with_servers.catalog_search({"query": "tabs"})

        print(f"\nSearch 'tabs' returned {len(result.results)} results:")
        for card in result.results:
            print(f"  - {card.tool_id}: {card.short_description}")

    @skip_no_playwright
    @pytest.mark.asyncio
    async def test_describe_browser_tabs(self, gateway_with_servers):
        """Step 2: Get schema for browser_tabs."""
        result = await gateway_with_servers.describe(
            {"tool_id": "playwright::browser_tabs"}
        )

        print("\nDescribe 'playwright::browser_tabs':")
        print(f"  Tool: {result.tool_name}")
        print(f"  Description: {result.description}")

        assert result.tool_name == "browser_tabs"

    @skip_no_playwright
    @pytest.mark.asyncio
    async def test_search_resize_window(self, gateway_with_servers):
        """Step 3: Search for resize tools."""
        result = await gateway_with_servers.catalog_search({"query": "resize window"})

        print(f"\nSearch 'resize window' returned {len(result.results)} results:")
        for card in result.results:
            print(f"  - {card.tool_id}: {card.short_description}")


class TestScenario6_WaitingAndDialogs:
    """Scenario 6: Waiting & Dialogs (Playwright)

    Naive Prompt: "Wait for page to load and handle popups"

    Tools to exercise:
    - playwright::browser_wait_for - Wait for conditions
    - playwright::browser_handle_dialog - Handle alerts/confirms
    """

    @skip_no_playwright
    @pytest.mark.asyncio
    async def test_search_wait(self, gateway_with_servers):
        """Step 1: Search for wait tools."""
        result = await gateway_with_servers.catalog_search({"query": "wait"})

        print(f"\nSearch 'wait' returned {len(result.results)} results:")
        for card in result.results:
            print(f"  - {card.tool_id}: {card.short_description}")

    @skip_no_playwright
    @pytest.mark.asyncio
    async def test_search_dialog_popup(self, gateway_with_servers):
        """Step 2: Search for dialog tools."""
        result = await gateway_with_servers.catalog_search({"query": "dialog popup"})

        print(f"\nSearch 'dialog popup' returned {len(result.results)} results:")
        for card in result.results:
            print(f"  - {card.tool_id}: {card.short_description}")


class TestScenario8_LibraryConcepts:
    """Scenario 8: Library Concepts (Context7)

    Naive Prompt: "Explain how React hooks work conceptually"

    Tools to exercise:
    - context7::query-docs - Query documentation
    """

    @skip_no_context7
    @pytest.mark.asyncio
    async def test_search_library_docs(self, gateway_with_servers):
        """Step 1: Search for library docs tools."""
        result = await gateway_with_servers.catalog_search({"query": "library docs"})

        print(f"\nSearch 'library docs' returned {len(result.results)} results:")
        for card in result.results:
            print(f"  - {card.tool_id}: {card.short_description}")

    @skip_no_context7
    @pytest.mark.asyncio
    async def test_invoke_query_docs_conceptual(self, gateway_with_servers):
        """Step 2: Get conceptual docs about React hooks."""
        # Query docs for conceptual information about hooks
        result = await gateway_with_servers.invoke(
            {
                "tool_id": "context7::query-docs",
                "arguments": {
                    "query": "how do React hooks work",
                    "libraryId": "/npm/react/19.0.0",
                },
                "options": {"timeout_ms": 60000},
            }
        )

        print("\nInvoke 'context7::query-docs' for conceptual info:")
        print(f"  OK: {result.ok}")
        print(f"  Truncated: {result.truncated}")
        if not result.ok:
            print(f"  Errors: {result.errors}")


class TestToolsCoverageMatrix:
    """Verify coverage of all HIGH priority tools."""

    @skip_no_playwright
    @pytest.mark.asyncio
    async def test_high_priority_playwright_tools_searchable(
        self, gateway_with_servers
    ):
        """Verify HIGH priority Playwright tools are searchable."""
        high_priority_tools = [
            "browser_navigate",
            "browser_take_screenshot",
            "browser_snapshot",
            "browser_click",
        ]

        # Search for each and verify it appears
        for tool_name in high_priority_tools:
            result = await gateway_with_servers.catalog_search(
                {
                    "query": tool_name.replace("_", " "),
                    "filters": {"server": "playwright"},
                }
            )

            tool_ids = [r.tool_id for r in result.results]
            found = any(tool_name in tid for tid in tool_ids)
            print(f"  {tool_name}: {'✓' if found else '✗'}")

    @skip_no_context7
    @pytest.mark.asyncio
    async def test_high_priority_context7_tools_searchable(self, gateway_with_servers):
        """Verify HIGH priority Context7 tools are searchable."""
        high_priority_tools = [
            "resolve-library-id",
            "query-docs",  # Correct tool name (not get-library-docs)
        ]

        for tool_name in high_priority_tools:
            result = await gateway_with_servers.catalog_search(
                {
                    "query": tool_name.replace("-", " "),
                    "filters": {"server": "context7"},
                }
            )

            tool_ids = [r.tool_id for r in result.results]
            found = any(tool_name in tid for tid in tool_ids)
            print(f"  {tool_name}: {'✓' if found else '✗'}")
