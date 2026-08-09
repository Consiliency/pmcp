"""SL-4.3 — EC-P2-5, EC-P2-6: modern-era deployed-wire acceptance.

Every request is built strictly per IF-0-P2-4's modern envelope (both
`_meta` keys, matching `MCP-Protocol-Version`/`Mcp-Method`/`Mcp-Name`
headers, and an `Accept` carrying both media types) via
`harness.modern_post`, which also accepts either response framing pmcp may
choose — plain JSON or one SSE `data:` frame — depending on whether the
handler completes inside the 15s deferral window (IF-0-P2-4).
"""

from __future__ import annotations

from mcp.types import (
    CallToolResult,
    DiscoverResult,
    GetPromptResult,
    ListPromptsResult,
    ListResourcesResult,
    ListToolsResult,
    ReadResourceResult,
)
from mcp.types.version import MODERN_PROTOCOL_VERSIONS

from tests.runtime.harness import BootedGateway, modern_post


def test_server_discover_returns_typed_result_with_modern_version(
    gateway_on_spare_port: BootedGateway,
) -> None:
    """EC-P2-5: no aggregated inventory — `DiscoverResult` carries only
    `supported_versions`, `capabilities`, and `instructions`."""
    raw = modern_post(gateway_on_spare_port.base_url, "server/discover", {})
    assert "error" not in raw, raw
    result = DiscoverResult.model_validate(raw["result"])
    assert set(MODERN_PROTOCOL_VERSIONS) <= set(result.supported_versions)
    assert result.capabilities.tools is not None
    assert result.capabilities.resources is not None
    assert result.capabilities.prompts is not None


def test_modern_tools_list_returns_typed_aggregated_catalog(
    gateway_on_spare_port: BootedGateway,
) -> None:
    raw = modern_post(gateway_on_spare_port.base_url, "tools/list", {})
    assert "error" not in raw, raw
    result = ListToolsResult.model_validate(raw["result"])
    assert any(t.name == "gateway.invoke" for t in result.tools)


def test_modern_tools_call_returns_typed_result(
    gateway_on_spare_port: BootedGateway,
) -> None:
    raw = modern_post(
        gateway_on_spare_port.base_url,
        "tools/call",
        {"name": "gateway.health", "arguments": {}},
        name="gateway.health",
    )
    assert "error" not in raw, raw
    result = CallToolResult.model_validate(raw["result"])
    assert result.is_error is False


def test_modern_tools_call_invalid_arguments_fails_schema_validation(
    gateway_on_spare_port: BootedGateway,
) -> None:
    """Proves the restored `inputSchema` check (IF-0-P2-1) also holds on the
    modern era's request path, not just the handshake era's."""
    raw = modern_post(
        gateway_on_spare_port.base_url,
        "tools/call",
        {"name": "gateway.describe", "arguments": {"tool_id": 12345}},
        name="gateway.describe",
    )
    assert "error" not in raw, raw
    result = CallToolResult.model_validate(raw["result"])
    assert result.is_error is True
    text = result.content[0].text  # type: ignore[union-attr]
    assert text.startswith("Input validation error:")


def test_modern_resources_list_returns_typed_result(
    gateway_on_spare_port: BootedGateway,
) -> None:
    """EC-P2-6 headline: all six proxied handlers, not just the three the
    criterion's body enumerates. README claims modern support for resources
    and prompts, so it needs deployed-wire evidence like tools does."""
    raw = modern_post(gateway_on_spare_port.base_url, "resources/list", {})
    assert "error" not in raw, raw
    ListResourcesResult.model_validate(raw["result"])


def test_modern_resources_read_returns_typed_result(
    gateway_on_spare_port: BootedGateway,
) -> None:
    """`resources/read` is name-bearing: it requires an `Mcp-Name` header
    carrying the `uri`, a rung `tools/list` never exercises."""
    listed = ListResourcesResult.model_validate(
        modern_post(gateway_on_spare_port.base_url, "resources/list", {})["result"]
    )
    assert listed.resources, "fixture exposes no resources to read"
    uri = str(listed.resources[0].uri)
    raw = modern_post(
        gateway_on_spare_port.base_url, "resources/read", {"uri": uri}, name=uri
    )
    assert "error" not in raw, raw
    result = ReadResourceResult.model_validate(raw["result"])
    assert result.contents


def test_modern_prompts_list_returns_typed_result(
    gateway_on_spare_port: BootedGateway,
) -> None:
    raw = modern_post(gateway_on_spare_port.base_url, "prompts/list", {})
    assert "error" not in raw, raw
    ListPromptsResult.model_validate(raw["result"])


def test_modern_prompts_get_returns_typed_result(
    gateway_on_spare_port: BootedGateway,
) -> None:
    """`prompts/get` is name-bearing on `name`."""
    listed = ListPromptsResult.model_validate(
        modern_post(gateway_on_spare_port.base_url, "prompts/list", {})["result"]
    )
    assert listed.prompts, "fixture exposes no prompts to get"
    prompt = listed.prompts[0]
    # Supply every required argument: a prompt whose required args are absent
    # fails downstream, which would test the error path, not the happy one.
    arguments = {a.name: "world" for a in (prompt.arguments or []) if a.required}
    raw = modern_post(
        gateway_on_spare_port.base_url,
        "prompts/get",
        {"name": prompt.name, "arguments": arguments},
        name=prompt.name,
    )
    assert "error" not in raw, raw
    result = GetPromptResult.model_validate(raw["result"])
    assert result.messages
