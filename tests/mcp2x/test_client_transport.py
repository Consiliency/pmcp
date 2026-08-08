"""SL-3.1 — downstream Streamable HTTP client transport contract on mcp 2.x.

Covers IF-0-P2-2: the pmcp-owned httpx2.AsyncClient that ``_connect_streamable_http``
now builds and threads into ``streamable_http_client(url, http_client=...)``, and
Trap 6: ``jsonrpc_message_adapter.validate_python(...)`` replacing the removed
``JSONRPCMessage.model_validate(...)`` on every outbound remote request and
notification. Also proves (per the phase plan's SL-3 task table) that
``manifest/refresher.py`` and ``cli.py``'s two diagnostics probes still resolve
against real mcp 2.0.0 / httpx objects, without assuming it from a source read.

This module owns no production code — it is evidence for
``src/pmcp/client/manager.py``, ``src/pmcp/manifest/refresher.py``, and
``src/pmcp/cli.py``, all SL-3-owned.
"""

from __future__ import annotations

import asyncio
import http.server
import json
import sys
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx2
import pytest

import mcp.types as mcp_types
from mcp.shared.message import SessionMessage
from mcp.types.version import HANDSHAKE_PROTOCOL_VERSIONS, MODERN_PROTOCOL_VERSIONS

from pmcp.client.manager import (
    ClientManager,
    ManagedClient,
    PREFERRED_PROTOCOL_VERSION,
    _remote_headers,
)
from pmcp.types import (
    RemoteMcpServerConfig,
    ResolvedServerConfig,
    ServerStatus,
    ServerStatusEnum,
)


class _EmptyReadStream:
    """A read stream that yields nothing, for connect-then-disconnect tests."""

    def __aiter__(self) -> "_EmptyReadStream":
        return self

    async def __anext__(self) -> None:
        raise StopAsyncIteration


# === IF-0-P2-2: the owned httpx2.AsyncClient ===


class TestConnectStreamableHttpBuildsOwnedClient:
    @pytest.mark.asyncio
    async def test_client_carries_redirects_timeout_and_headers(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`_connect_streamable_http` must build the httpx2.AsyncClient that 1.x's
        removed `create_mcp_http_client` used to build internally, and pass it
        as `http_client=` — not `headers=`/`timeout=`, which 2.x's
        `streamable_http_client` no longer accepts at all.
        """
        manager = ClientManager()
        monkeypatch.setenv("PMCP_TEST_TOKEN", "test-token")

        config = ResolvedServerConfig(
            name="remote-http",
            source="custom",
            config=RemoteMcpServerConfig(
                type="streamable-http",
                url="https://example.com/mcp",
                headers={"Authorization": "Bearer ${PMCP_TEST_TOKEN}"},
            ),
        )

        captured: dict[str, Any] = {}

        @asynccontextmanager
        async def fake_streamable_http_client(
            url: str, *, http_client: httpx2.AsyncClient | None = None, **_: Any
        ):
            captured["url"] = url
            captured["http_client"] = http_client
            yield _EmptyReadStream(), MagicMock()

        monkeypatch.setattr(
            "pmcp.client.manager.streamable_http_client", fake_streamable_http_client
        )
        manager._send_initialize = AsyncMock()  # type: ignore[method-assign]

        async def fake_send_request(*args: object, **kwargs: object) -> dict:
            return {"tools": []}

        manager._send_request = AsyncMock(side_effect=fake_send_request)  # type: ignore[method-assign]
        manager._read_sse = AsyncMock()  # type: ignore[method-assign]

        await manager._connect_streamable_http(config)
        try:
            assert captured["url"] == "https://example.com/mcp"
            client = captured["http_client"]
            assert isinstance(client, httpx2.AsyncClient)
            # httpx2's own default is False; the removed create_mcp_http_client
            # set True, so a caller-supplied client must too (IF-0-P2-2).
            assert client.follow_redirects is True
            assert client.timeout == httpx2.Timeout(30.0, read=300.0)
            assert client.headers["authorization"] == "Bearer test-token"

            managed = manager._clients["remote-http"]
            assert managed.remote_http_client is client
            assert client.is_closed is False
        finally:
            await manager.disconnect_all()

        # Closing the managed connection must close the owned client too
        # (IF-0-P2-2 / EC-P2-7's is_closed-after-cleanup assertion).
        assert client.is_closed is True

    @pytest.mark.asyncio
    async def test_headers_omitted_when_remote_headers_resolves_to_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Matching 1.x's create_mcp_http_client(headers=None) passthrough: an
        unconfigured-headers server must not force an empty headers dict onto
        the built client.
        """
        manager = ClientManager()
        config = ResolvedServerConfig(
            name="remote-http-noauth",
            source="custom",
            config=RemoteMcpServerConfig(
                type="streamable-http", url="https://example.com/mcp"
            ),
        )

        captured: dict[str, Any] = {}

        @asynccontextmanager
        async def fake_streamable_http_client(
            url: str, *, http_client: httpx2.AsyncClient | None = None, **_: Any
        ):
            captured["http_client"] = http_client
            yield _EmptyReadStream(), MagicMock()

        monkeypatch.setattr(
            "pmcp.client.manager.streamable_http_client", fake_streamable_http_client
        )
        manager._send_initialize = AsyncMock()  # type: ignore[method-assign]
        manager._send_request = AsyncMock(return_value={"tools": []})  # type: ignore[method-assign]
        manager._read_sse = AsyncMock()  # type: ignore[method-assign]

        await manager._connect_streamable_http(config)
        try:
            client = captured["http_client"]
            # httpx2.AsyncClient always exposes a Headers object; assert no
            # caller-supplied auth header leaked in, not that it's literally empty.
            assert "authorization" not in client.headers
        finally:
            await manager.disconnect_all()

    @pytest.mark.asyncio
    async def test_transport_connect_failure_does_not_leak_owned_client(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If entering the transport context raises, the httpx2 client already
        entered into the exit stack must still be closed, not leaked.
        """
        manager = ClientManager()
        config = ResolvedServerConfig(
            name="remote-http-fail",
            source="custom",
            config=RemoteMcpServerConfig(
                type="streamable-http", url="https://example.com/mcp"
            ),
        )

        captured: dict[str, Any] = {}

        @asynccontextmanager
        async def failing_streamable_http_client(
            url: str, *, http_client: httpx2.AsyncClient | None = None, **_: Any
        ):
            captured["http_client"] = http_client
            raise ConnectionRefusedError("boom")
            yield  # pragma: no cover - unreachable, keeps this an async generator

        monkeypatch.setattr(
            "pmcp.client.manager.streamable_http_client", failing_streamable_http_client
        )

        with pytest.raises(ConnectionRefusedError):
            await manager._connect_streamable_http(config)

        client = captured["http_client"]
        assert client.is_closed is True

    @pytest.mark.asyncio
    async def test_reconnect_closes_prior_owned_client_via_cleanup_client(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`_connect_remote_stream` calls `_cleanup_client` on the *reconnect*
        path (an existing live connection found under the same server name) —
        not `disconnect_all`/`_shutdown_one`, which is a different code path.
        Before this lane, `_cleanup_client` closed no transport at all for
        remote clients, so a reconnect leaked the prior connection's transport
        (and, after IF-0-P2-2, would also leak its owned httpx2.AsyncClient).
        Exercise the reconnect path directly rather than inferring the close
        from `disconnect_all`.
        """
        manager = ClientManager()
        config = ResolvedServerConfig(
            name="remote-reconnect",
            source="custom",
            config=RemoteMcpServerConfig(
                type="streamable-http", url="https://example.com/mcp"
            ),
        )

        built_clients: list[httpx2.AsyncClient] = []

        @asynccontextmanager
        async def fake_streamable_http_client(
            url: str, *, http_client: httpx2.AsyncClient | None = None, **_: Any
        ):
            assert http_client is not None
            built_clients.append(http_client)
            yield _EmptyReadStream(), MagicMock()

        monkeypatch.setattr(
            "pmcp.client.manager.streamable_http_client", fake_streamable_http_client
        )
        manager._send_initialize = AsyncMock()  # type: ignore[method-assign]
        manager._send_request = AsyncMock(return_value={"tools": []})  # type: ignore[method-assign]
        manager._read_sse = AsyncMock()  # type: ignore[method-assign]

        await manager._connect_streamable_http(config)
        client_a = built_clients[0]
        assert client_a.is_closed is False

        # Second connect for the same server name hits _connect_remote_stream's
        # "Existing live connection found; cleaning up before reconnect"
        # branch, which calls _cleanup_client(name, existing) on client A's
        # managed connection before establishing client B's.
        await manager._connect_streamable_http(config)
        client_b = built_clients[1]

        try:
            assert client_a.is_closed is True
            assert client_b.is_closed is False
            assert manager._clients["remote-reconnect"].remote_http_client is client_b
        finally:
            await manager.disconnect_all()
        assert client_b.is_closed is True


# === Trap 6: JSONRPCMessage -> jsonrpc_message_adapter ===


class TestOutboundEnvelopeAdapter:
    """`_send_request`/`_send_initialize` build every outbound remote request and
    notification via `mcp_types.jsonrpc_message_adapter.validate_python(...)`
    (JSONRPCMessage has no `.model_validate` in 2.0.0 — it's a bare union).
    Assert the constructed SessionMessage's wire dump reproduces the input
    dict byte-identically, including nested `params.arguments`.
    """

    def _managed_remote_client(self) -> tuple[ManagedClient, list[SessionMessage]]:
        sent: list[SessionMessage] = []

        class _CapturingWriteStream:
            async def send(self, msg: SessionMessage) -> None:
                sent.append(msg)

        config = ResolvedServerConfig(
            name="remote-adapter",
            source="custom",
            config=RemoteMcpServerConfig(
                type="streamable-http", url="https://example.com/mcp"
            ),
        )
        managed = ManagedClient(
            config=config,
            is_remote=True,
            write_stream=_CapturingWriteStream(),
            status=ServerStatus(
                name="remote-adapter", status=ServerStatusEnum.ONLINE, tool_count=0
            ),
        )
        return managed, sent

    @pytest.mark.asyncio
    async def test_request_envelope_round_trips_with_nested_arguments(self) -> None:
        manager = ClientManager()
        managed, sent = self._managed_remote_client()

        params = {
            "name": "echo",
            "arguments": {"text": "hi", "nested": {"a": 1, "b": [1, 2, 3]}},
        }

        async def resolve_future() -> None:
            # _send_request awaits a future the (absent) reader would resolve;
            # resolve it ourselves once the request has been written so the
            # call returns instead of timing out.
            while not sent:
                await asyncio.sleep(0)
            request_id = next(iter(managed.pending_requests))
            managed.pending_requests[request_id].future.set_result({"ok": True})

        await asyncio.gather(
            manager._send_request(managed, "tools/call", params, timeout_ms=1000),
            resolve_future(),
        )

        assert len(sent) == 1
        session_msg = sent[0]
        dumped = session_msg.message.model_dump(
            by_alias=True, mode="json", exclude_none=True
        )
        assert dumped["jsonrpc"] == "2.0"
        assert dumped["method"] == "tools/call"
        assert dumped["params"] == params
        assert isinstance(dumped["id"], int)

        # And the adapter itself round-trips the exact envelope pmcp constructs.
        envelope = {
            "jsonrpc": "2.0",
            "id": dumped["id"],
            "method": "tools/call",
            "params": params,
        }
        model = mcp_types.jsonrpc_message_adapter.validate_python(envelope)
        assert (
            model.model_dump(by_alias=True, mode="json", exclude_none=True) == envelope
        )

    @pytest.mark.asyncio
    async def test_notification_envelope_round_trips(self) -> None:
        manager = ClientManager()
        managed, sent = self._managed_remote_client()

        async def fake_send_request(*_args: object, **_kwargs: object) -> dict:
            return {"protocolVersion": PREFERRED_PROTOCOL_VERSION, "capabilities": {}}

        manager._send_request = AsyncMock(side_effect=fake_send_request)  # type: ignore[method-assign]

        await manager._send_initialize(managed)

        # _send_initialize sends the "initialized" notification directly on
        # write_stream (not through _send_request, which is mocked above).
        assert len(sent) == 1
        dumped = sent[0].message.model_dump(
            by_alias=True, mode="json", exclude_none=True
        )
        assert dumped == {"jsonrpc": "2.0", "method": "notifications/initialized"}

    def test_initialize_request_envelope_round_trips(self) -> None:
        """The `initialize` envelope pmcp sends is itself a `tools/call`-shaped
        JSON-RPC request (method="initialize"); prove the adapter round-trips
        its nested params (protocolVersion/capabilities/clientInfo) too.
        """
        envelope = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": PREFERRED_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "mcp-gateway", "version": "1.0.0"},
            },
        }
        model = mcp_types.jsonrpc_message_adapter.validate_python(envelope)
        assert isinstance(model, mcp_types.jsonrpc.JSONRPCRequest)
        assert (
            model.model_dump(by_alias=True, mode="json", exclude_none=True) == envelope
        )
        # SessionMessage(<concrete model>).message.model_dump(...) is what
        # _read_sse relies on for the read side (unaffected by Trap 6).
        session_msg = SessionMessage(model)
        assert (
            session_msg.message.model_dump(
                by_alias=True, mode="json", exclude_none=True
            )
            == envelope
        )


# === Protocol version ladder: unchanged by this lane ===


class TestPreferredProtocolVersionUnchanged:
    def test_preferred_version_governs_the_handshake_not_the_modern_era(self) -> None:
        assert PREFERRED_PROTOCOL_VERSION == "2025-11-25"
        assert PREFERRED_PROTOCOL_VERSION in HANDSHAKE_PROTOCOL_VERSIONS
        assert PREFERRED_PROTOCOL_VERSION not in MODERN_PROTOCOL_VERSIONS


# === sse_client: genuinely unchanged ===


class TestConnectSseUnchanged:
    @pytest.mark.asyncio
    async def test_connect_sse_still_calls_sse_client_with_headers_kwarg(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        manager = ClientManager()
        monkeypatch.setenv("PMCP_TEST_TOKEN", "sse-token")
        config = ResolvedServerConfig(
            name="remote-sse",
            source="custom",
            config=RemoteMcpServerConfig(
                url="https://example.com/sse",
                headers={"Authorization": "Bearer ${PMCP_TEST_TOKEN}"},
            ),
        )

        captured: dict[str, Any] = {}

        @asynccontextmanager
        async def fake_sse_client(url: str, headers: dict[str, str] | None = None):
            captured["url"] = url
            captured["headers"] = headers
            yield _EmptyReadStream(), MagicMock()

        monkeypatch.setattr("pmcp.client.manager.sse_client", fake_sse_client)
        manager._send_initialize = AsyncMock()  # type: ignore[method-assign]
        manager._send_request = AsyncMock(return_value={"tools": []})  # type: ignore[method-assign]
        manager._read_sse = AsyncMock()  # type: ignore[method-assign]

        await manager._connect_sse(config)
        try:
            assert captured["url"] == "https://example.com/sse"
            assert captured["headers"] == {"Authorization": "Bearer sse-token"}
            # SSE connections never build an owned httpx2 client (2.x's
            # sse_client is unchanged and still owns its own transport).
            assert manager._clients["remote-sse"].remote_http_client is None
        finally:
            await manager.disconnect_all()


# === manifest/refresher.py: proven unchanged against a real mcp 2.0.0 server ===


_REFRESHER_FIXTURE_SERVER = '''
from mcp.server.mcpserver import MCPServer

server = MCPServer("refresher-fixture")


@server.tool()
def echo(text: str) -> str:
    """Echo back the input text."""
    return text


if __name__ == "__main__":
    server.run("stdio")
'''


class TestManifestRefresherUnaffected:
    @pytest.mark.asyncio
    async def test_refresh_server_round_trips_through_real_2x_stdio_session(
        self, tmp_path: Path
    ) -> None:
        """`refresher.py` imports `ClientSession`/`StdioServerParameters`/
        `stdio_client` unchanged from mcp 2.0.0 (all three survive per Trap 4)
        and uses none of the pydantic-model attributes that reshaped (Trap 5).
        Exercised end to end rather than assumed.
        """
        from pmcp.manifest.loader import ServerConfig
        from pmcp.manifest.refresher import refresh_server

        fixture = tmp_path / "refresher_fixture_server.py"
        fixture.write_text(_REFRESHER_FIXTURE_SERVER)

        server_config = ServerConfig(
            name="refresher-fixture",
            description="fixture",
            keywords=[],
            install={},
            command=sys.executable,
            args=[str(fixture)],
        )

        result = await refresh_server(server_config)

        assert result is not None
        assert [t.name for t in result.tools] == ["echo"]
        assert result.tools[0].description == "Echo back the input text."


# === cli.py: proven to need zero diff — httpx still resolves, probes still work ===


class _HealthStubHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - stdlib method name
        if self.path == "/health":
            body = json.dumps({"ok": True}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/sse":
            body = b"event: message\ndata: {}\n\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        pass  # keep test output quiet


@pytest.fixture
def health_stub_server() -> Any:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _HealthStubHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        thread.join(timeout=5)


class TestCliHttpxProbesUnaffectedByMcp2x:
    """cli.py does a bare `import httpx` inside these two functions; per
    Decision 2 pmcp declares `httpx` directly (pyproject.toml), so both still
    resolve after mcp 2.0.0 stops supplying it transitively. Exercised against
    a real local server rather than assumed from the source read.
    """

    @pytest.mark.asyncio
    async def test_probe_http_health_resolves_httpx_and_reaches_stub(
        self, health_stub_server: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from pmcp.cli import _probe_http_health

        port = health_stub_server.server_address[1]
        monkeypatch.setenv("PMCP_GATEWAY_URL", f"http://127.0.0.1:{port}/mcp")

        ok, detail, status_code = await _probe_http_health(timeout_s=5.0)

        assert ok is True
        assert status_code == 200
        assert "reachable" in detail

    @pytest.mark.asyncio
    async def test_probe_sse_endpoint_resolves_httpx_and_reaches_stub(
        self, health_stub_server: Any
    ) -> None:
        from pmcp.cli import _probe_sse_endpoint

        port = health_stub_server.server_address[1]
        ok, detail = await _probe_sse_endpoint(
            f"http://127.0.0.1:{port}/sse", timeout_s=5.0
        )

        assert ok is True
        assert "HTTP 200" in detail


# === _remote_headers: sanity that the None-vs-dict contract IF-0-P2-2 relies on holds ===


def test_remote_headers_returns_none_when_unconfigured() -> None:
    config = RemoteMcpServerConfig(
        type="streamable-http", url="https://example.com/mcp"
    )
    assert _remote_headers("s", config) is None
