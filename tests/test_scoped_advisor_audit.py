from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import subprocess
import sys
import time
import traceback
import zlib
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import jsonschema
import pytest
from mcp.server.connection import Connection
from mcp.server.context import ServerRequestContext
from mcp.server.session import ServerSession
from mcp.types import CallToolRequestParams, PaginatedRequestParams
from pydantic import ValidationError

from pmcp.policy.policy import PolicyManager
from pmcp.identity import acquire_singleton_lock, release_singleton_lock
from pmcp.scoped_advisor_audit import (
    SCOPED_ADVISOR_AUDIT_CAPABILITY,
    ScopedAdvisorAudit,
    ScopedAdvisorAuditError,
    validate_scoped_advisor_audit,
)
from pmcp.server import GatewayServer
from pmcp.types import InvokeInput
from tests.conftest import MockClientManager, create_tool_info

#: Repo root, so repo-relative reads do not depend on the working directory
#: (tests run from an isolated cwd -- see tests/conftest.py::isolate_cwd).
_REPO_ROOT = Path(__file__).resolve().parents[1]


def _write_scoped_policy(path: Path) -> Path:
    path.write_text(
        json.dumps(
            {
                "servers": {"allowlist": ["firecrawl", "brightdata"]},
                "gateway_tools": {
                    "allowlist": [
                        "gateway.health",
                        "gateway.catalog_search",
                        "gateway.describe",
                        "gateway.invoke",
                    ]
                },
                "tools": {
                    "allowlist": [
                        "firecrawl::*search*",
                        "firecrawl::*scrape*",
                        "brightdata::*search*",
                        "brightdata::*scrape*",
                    ]
                },
                "resources": {"denylist": ["*"]},
                "prompts": {"denylist": ["*"]},
            }
        )
    )
    return path


#: The fields agent-harness's research reducer
#: (`phase_loop_runtime/advisor_board/research.py` @ 18a324a4) reads from a
#: `gateway.invoke` invocation record, or checks on every record. Each must
#: survive on an invoke record, or the seat's ledger fails. It also reads
#: `_COMPLETION_RECORD_FIELDS`, from the `audit.completed` record only.
#: Consiliency/pmcp#296's plan derives the union of both sets from that file
#: with `ledger_fields.py` (every string-keyed read on any name holding audit
#: data, in the reducer and its helpers) and compares it with this file.
_LEDGER_READ_FIELDS = frozenset(
    {
        "sequence",
        "event",
        "audit_session_id",
        "policy_digest",
        "gateway_tool",
        "downstream_tool_id",
        "terminal_status",
        "source_reference_hash",
        "evidence_label_digest",
        "run_correlation_id",
        "seat_correlation_id",
    }
)


_COMPLETION_RECORD_FIELDS = frozenset(
    {"first_sequence", "last_sequence", "record_count"}
)


def _correlations() -> dict[str, str]:
    return {
        "run_correlation_id": "run-103",
        "seat_correlation_id": "seat-codex",
        "evidence_label_digest": "a" * 64,
    }


# A HANDSHAKE_PROTOCOL_VERSIONS member; the specific value is irrelevant to
# these handlers, which read no protocol-version-gated behaviour off `ctx`.
# Mirrors tests/mcp2x/test_server_handlers.py's `_make_ctx` — duplicated
# rather than imported, since that module belongs to a different lane.
_PROTOCOL_VERSION = "2025-11-25"


def _make_ctx() -> ServerRequestContext[Any, Any]:
    connection = Connection.from_envelope(_PROTOCOL_VERSION, None, None)
    session = ServerSession(MagicMock(), connection)
    return ServerRequestContext(
        session=session,
        lifespan_context={},
        protocol_version=_PROTOCOL_VERSION,
        method="test",
    )


def test_explicit_policy_failures_are_fatal_but_an_unparseable_default_is_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Best-effort discovery survives, but only for a file the parser *rejects*.

    Renamed from ``..._but_default_discovery_is_best_effort``: since
    Consiliency/pmcp#202 an auto-discovered file that *parses* but is not a valid
    policy terminates startup, so the old name asserted something broader than
    the code does. The unparseable case below is unchanged and still pins the
    deliberate fallback; ``tests/test_policy_fail_open.py`` covers the other side.
    """
    missing = tmp_path / "missing.json"
    with pytest.raises(ValueError, match="explicit policy"):
        PolicyManager(missing)

    malformed = tmp_path / "malformed.json"
    malformed.write_text("{")
    with pytest.raises(ValueError, match="explicit policy"):
        PolicyManager(malformed)

    invalid = tmp_path / "invalid.json"
    invalid.write_text(json.dumps({"gateway_tools": {"unknown": []}}))
    with pytest.raises(ValueError, match="explicit policy"):
        PolicyManager(invalid)

    monkeypatch.setattr("pmcp.policy.policy.DEFAULT_POLICY_PATHS", [malformed])
    fallback = PolicyManager()
    assert fallback.is_gateway_tool_allowed("gateway.provision") is True

    cli = subprocess.run(
        [sys.executable, "-m", "pmcp", "--policy", str(missing), "--quiet"],
        capture_output=True,
        text=True,
    )
    assert cli.returncode != 0
    assert "explicit policy" in cli.stderr


def test_gateway_tool_policy_is_case_sensitive_and_scoped() -> None:
    policy = PolicyManager(_REPO_ROOT / "examples/scoped-advisor-policy.yaml")
    assert policy.is_scoped_advisor_policy() is True
    assert policy.scoped_advisor_active is False
    assert policy.is_gateway_tool_allowed("gateway.invoke") is True
    assert policy.is_gateway_tool_allowed("Gateway.invoke") is False
    assert policy.is_gateway_tool_allowed("gateway.provision") is False
    assert policy.is_tool_allowed("firecrawl::web_search") is True
    assert policy.is_tool_allowed("brightdata::scrape_page") is True
    assert policy.is_tool_allowed("github::create_issue") is False

    policy.activate_scoped_advisor()
    assert policy.scoped_advisor_active is True


def test_invoke_correlations_are_typed_and_atomic() -> None:
    with pytest.raises(ValidationError, match="supplied together"):
        InvokeInput(tool_id="firecrawl::search", run_correlation_id="run-only")
    with pytest.raises(ValidationError):
        InvokeInput(
            tool_id="firecrawl::search",
            **{**_correlations(), "evidence_label_digest": "not-a-digest"},
        )
    with pytest.raises(ValidationError):
        InvokeInput(
            tool_id="firecrawl::search",
            **{**_correlations(), "run_correlation_id": "rún-103"},
        )
    parsed = InvokeInput(tool_id="firecrawl::search", **_correlations())
    assert parsed.seat_correlation_id == "seat-codex"


@pytest.mark.asyncio
async def test_scoped_server_filters_controls_and_writes_private_complete_audit(
    tmp_path: Path,
) -> None:
    policy_path = _write_scoped_policy(tmp_path / "policy.json")
    audit_path = tmp_path / "audit.jsonl"
    server = GatewayServer(policy_path=policy_path, audit_jsonl=audit_path)
    manager = MockClientManager(
        [
            create_tool_info("firecrawl", "web_search"),
            create_tool_info("brightdata", "scrape_page"),
            create_tool_info("github", "create_issue"),
        ]
    )
    for server_name in ("firecrawl", "brightdata", "github"):
        manager.set_server_online(server_name)
    manager.get_all_resources = lambda: []  # type: ignore[attr-defined]
    manager.get_all_prompts = lambda: []  # type: ignore[attr-defined]
    manager.set_call_tool_response(
        {
            "content": [
                {
                    "type": "text",
                    "text": "https://source.example/article raw page contents",
                }
            ]
        }
    )
    server._client_manager = manager  # type: ignore[assignment]
    server._gateway_tools._client_manager = manager  # type: ignore[assignment]
    server._create_server()
    assert server._server is not None
    list_entry = server._server.get_request_handler("tools/list")
    call_tool_entry = server._server.get_request_handler("tools/call")
    resources_entry = server._server.get_request_handler("resources/list")
    prompts_entry = server._server.get_request_handler("prompts/list")
    assert list_entry is not None
    assert call_tool_entry is not None
    assert resources_entry is not None
    assert prompts_entry is not None

    async def call_handler(name: str, arguments: dict) -> Any:
        return await call_tool_entry.handler(
            _make_ctx(), CallToolRequestParams(name=name, arguments=arguments)
        )

    listed = await list_entry.handler(_make_ctx(), PaginatedRequestParams())
    assert {tool.name for tool in listed.tools} == {
        "gateway.health",
        "gateway.catalog_search",
        "gateway.describe",
        "gateway.invoke",
    }
    resources = await resources_entry.handler(_make_ctx(), PaginatedRequestParams())
    prompts = await prompts_entry.handler(_make_ctx(), PaginatedRequestParams())
    assert resources.resources == []
    assert prompts.prompts == []

    async def fail_registry_discovery(*args, **kwargs):
        raise AssertionError("scoped catalog attempted registry discovery")

    server._gateway_tools._registry_candidates_for_query = fail_registry_discovery  # type: ignore[method-assign]
    catalog = await call_handler(
        "gateway.catalog_search",
        {"query": "native cli execution path", "include_offline": True},
    )
    catalog_payload = json.loads(catalog.content[0].text)
    assert catalog_payload["cli_hints"] == []
    assert catalog_payload["registry_candidates"] == []
    assert catalog_payload["manifest_candidates"] == []

    invoked = await call_handler(
        "gateway.invoke",
        {
            "tool_id": "firecrawl::web_search",
            "arguments": {
                "url": "https://example.com/private/path?token=secret",
                "query": "super secret query",
                "credential": "sk-private-value",
            },
            **_correlations(),
        },
    )
    assert json.loads(invoked.content[0].text)["ok"] is True

    brightdata = await call_handler(
        "gateway.invoke",
        {
            "tool_id": "brightdata::scrape_page",
            "arguments": {"query": "current benchmark evidence"},
            **{**_correlations(), "seat_correlation_id": "seat-gemini"},
        },
    )
    assert json.loads(brightdata.content[0].text)["ok"] is True

    downstream_denied = await call_handler(
        "gateway.invoke",
        {
            "tool_id": "github::create_issue",
            "arguments": {"title": "must not mutate"},
            **_correlations(),
        },
    )
    denied_payload = json.loads(downstream_denied.content[0].text)
    assert denied_payload["ok"] is False
    assert denied_payload["auth_state"] == "policy_denied"

    async def fail_lazy_start(tool_id: str) -> bool:
        raise AssertionError(f"policy denial attempted lazy start for {tool_id}")

    server._gateway_tools._ensure_server_for_tool = fail_lazy_start  # type: ignore[method-assign]
    lazy_denied = await call_handler(
        "gateway.invoke",
        {
            "tool_id": "github::unregistered_mutation",
            "arguments": {},
            **_correlations(),
        },
    )
    assert json.loads(lazy_denied.content[0].text)["auth_state"] == ("policy_denied")

    describe_denied = await call_handler(
        "gateway.describe", {"tool_id": "github::unregistered_mutation"}
    )
    assert "blocked by policy" in json.loads(describe_denied.content[0].text)["message"]

    denied = await call_handler(
        "gateway.provision",
        {
            "server_name": "not-allowlisted-server",
            "tool_id": "raw credential value",
            "run_correlation_id": "secret query with spaces",
            "seat_correlation_id": "seat secret with spaces",
            "evidence_label_digest": "not-a-digest-secret",
        },
    )
    assert "blocked by policy" in json.loads(denied.content[0].text)["message"]

    raw_tool_name = "gateway.sk-secret-token"
    raw_name_denied = await call_handler(raw_tool_name, {})
    assert "blocked by policy" in json.loads(raw_name_denied.content[0].text)["message"]

    health = await server._gateway_tools.health()
    assert health.gateway_diagnostics.capabilities == [SCOPED_ADVISOR_AUDIT_CAPABILITY]
    await server.shutdown()

    records = validate_scoped_advisor_audit(audit_path)
    invocations = [r for r in records if r["event"] == "audit.invocation"]
    assert [record["terminal_status"] for record in invocations] == [
        "success",
        "success",
        "success",
        "denied",
        "denied",
        "denied",
        "denied",
        "denied",
    ]
    firecrawl_record = next(
        record
        for record in invocations
        if record["downstream_tool_id"] == "firecrawl::web_search"
    )
    assert firecrawl_record["run_correlation_id"] == "run-103"
    assert firecrawl_record["seat_correlation_id"] == "seat-codex"
    assert firecrawl_record["source_reference_hash"]
    # Everything the board ledger reads survives the declared-keys filter.
    for field in _LEDGER_READ_FIELDS:
        assert firecrawl_record[field] is not None, field
    assert firecrawl_record["evidence_label_digest"] == "a" * 64
    assert records[-1]["record_count"] == len(records)
    for field in _COMPLETION_RECORD_FIELDS:
        assert records[-1][field] is not None, field
    raw_audit = audit_path.read_text()
    for forbidden in (
        "example.com",
        "private/path",
        "super secret query",
        "sk-private-value",
        "raw page contents",
        "current benchmark evidence",
        "must not mutate",
        "raw credential value",
        "secret query with spaces",
        "seat secret with spaces",
        "not-a-digest-secret",
        raw_tool_name,
    ):
        assert forbidden not in raw_audit
    assert invocations[-1]["gateway_tool"] is None
    assert invocations[-1]["gateway_tool_digest"]


@pytest.mark.asyncio
async def test_scoped_server_rejects_uncorrelated_invoke_before_dispatch(
    tmp_path: Path,
) -> None:
    policy_path = _write_scoped_policy(tmp_path / "policy.json")
    server = GatewayServer(
        policy_path=policy_path, audit_jsonl=tmp_path / "audit.jsonl"
    )
    server._create_server()
    called = False

    async def fake_invoke(arguments: dict) -> dict:
        nonlocal called
        called = True
        return {"ok": True}

    server._gateway_tools.invoke = fake_invoke  # type: ignore[method-assign]
    assert server._server is not None
    call_tool_entry = server._server.get_request_handler("tools/call")
    assert call_tool_entry is not None
    result = await call_tool_entry.handler(
        _make_ctx(),
        CallToolRequestParams(
            name="gateway.invoke",
            arguments={"tool_id": "firecrawl::web_search"},
        ),
    )
    assert called is False
    assert json.loads(result.content[0].text)["error"] is True
    await server.shutdown()


def test_audit_validator_rejects_truncation_gaps_and_duplicate_completion(
    tmp_path: Path,
) -> None:
    valid = tmp_path / "valid.jsonl"
    audit = ScopedAdvisorAudit(valid, policy_digest="b" * 64)
    audit.record_invocation(
        gateway_tool="gateway.invoke",
        terminal_status="success",
        arguments={"tool_id": "firecrawl::search", **_correlations()},
        result={"ok": True},
    )
    audit.complete()
    records = validate_scoped_advisor_audit(valid)

    truncated = tmp_path / "truncated.jsonl"
    truncated.write_text("\n".join(json.dumps(r) for r in records[:-1]) + "\n")
    with pytest.raises(ScopedAdvisorAuditError, match="completion"):
        validate_scoped_advisor_audit(truncated)

    gap = tmp_path / "gap.jsonl"
    gap_records = [dict(r) for r in records]
    gap_records[1]["sequence"] = 7
    gap.write_text("\n".join(json.dumps(r) for r in gap_records) + "\n")
    with pytest.raises(ScopedAdvisorAuditError, match="sequence gap"):
        validate_scoped_advisor_audit(gap)

    duplicate = tmp_path / "duplicate.jsonl"
    duplicate_records = [dict(r) for r in records]
    extra = dict(duplicate_records[-1])
    extra["sequence"] = len(duplicate_records) + 1
    duplicate_records.append(extra)
    duplicate.write_text("\n".join(json.dumps(r) for r in duplicate_records) + "\n")
    with pytest.raises(ScopedAdvisorAuditError, match="completion"):
        validate_scoped_advisor_audit(duplicate)

    with pytest.raises(ScopedAdvisorAuditError, match="sink unavailable"):
        ScopedAdvisorAudit(valid, policy_digest="b" * 64)


@pytest.mark.skipif(
    sys.platform == "win32", reason="stdio process timing differs on Windows"
)
def test_four_scoped_stdio_instances_use_unique_lock_and_audit_dirs(
    tmp_path: Path,
) -> None:
    policy_path = _write_scoped_policy(tmp_path / "policy.json")
    processes: list[subprocess.Popen[bytes]] = []
    try:
        for index in range(4):
            root = tmp_path / f"seat-{index}"
            root.mkdir()
            config = root / "mcp.json"
            config.write_text(json.dumps({"mcpServers": {}}))
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "pmcp",
                    "--project",
                    str(root),
                    "--config",
                    str(config),
                    "--policy",
                    str(policy_path),
                    "--audit-jsonl",
                    str(root / "audit.jsonl"),
                    "--lock-dir",
                    str(root / "locks"),
                    "--quiet",
                ],
                cwd=root,
                env={
                    **os.environ,
                    "PYTHONPATH": str(Path(__file__).parents[1] / "src"),
                },
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            processes.append(process)

        time.sleep(1.5)
        assert all(process.poll() is None for process in processes)
    finally:
        for process in processes:
            if process.stdin:
                process.stdin.close()
        for process in processes:
            try:
                assert process.wait(timeout=15) == 0
            finally:
                if process.poll() is None:
                    process.terminate()

    for index in range(4):
        validate_scoped_advisor_audit(tmp_path / f"seat-{index}" / "audit.jsonl")


@pytest.mark.asyncio
async def test_lock_conflict_still_closes_started_audit(tmp_path: Path) -> None:
    lock_dir = tmp_path / "locks"
    assert acquire_singleton_lock(lock_dir) is True
    try:
        server = GatewayServer(
            policy_path=_write_scoped_policy(tmp_path / "policy.json"),
            audit_jsonl=tmp_path / "audit.jsonl",
            lock_dir=lock_dir,
        )
        with pytest.raises(RuntimeError, match="already running"):
            await server._run_stdio()
    finally:
        release_singleton_lock()
    records = validate_scoped_advisor_audit(tmp_path / "audit.jsonl")
    assert [record["event"] for record in records] == [
        "audit.started",
        "audit.completed",
    ]


def test_audit_sink_failure_fails_closed(tmp_path: Path) -> None:
    audit = ScopedAdvisorAudit(tmp_path / "audit.jsonl", policy_digest="c" * 64)
    assert audit._file is not None
    audit._file.close()
    with pytest.raises(ScopedAdvisorAuditError, match="write failed"):
        audit.record_invocation(
            gateway_tool="gateway.health",
            terminal_status="success",
            arguments={},
            result={"ok": True},
        )


@pytest.mark.asyncio
async def test_failed_audit_latches_before_later_dispatch(tmp_path: Path) -> None:
    server = GatewayServer(
        policy_path=_write_scoped_policy(tmp_path / "policy.json"),
        audit_jsonl=tmp_path / "audit.jsonl",
    )
    manager = MockClientManager([create_tool_info("firecrawl", "web_search")])
    manager.set_server_online("firecrawl")
    called = False

    async def track_call(tool_id: str, args: dict, timeout_ms: int) -> dict:
        nonlocal called
        called = True
        return {"ok": True}

    manager.call_tool = track_call  # type: ignore[method-assign]
    server._client_manager = manager  # type: ignore[assignment]
    server._gateway_tools._client_manager = manager  # type: ignore[assignment]
    server._create_server()
    assert server._server is not None
    assert server._scoped_advisor_audit is not None
    assert server._scoped_advisor_audit._file is not None
    server._scoped_advisor_audit._file.close()

    call_tool_entry = server._server.get_request_handler("tools/call")
    assert call_tool_entry is not None
    result = await call_tool_entry.handler(
        _make_ctx(),
        CallToolRequestParams(
            name="gateway.invoke",
            arguments={
                "tool_id": "firecrawl::web_search",
                "arguments": {"query": "must not dispatch"},
                **_correlations(),
            },
        ),
    )
    # `_handle_call_tool`'s outer except maps every `ScopedAdvisorAuditError`
    # (including "scoped advisor audit sink is unavailable") to this fixed,
    # non-leaking message — server.py:379/386, unchanged by the mcp 2.x port.
    assert "Scoped advisor audit channel failed" in result.content[0].text
    assert called is False


def test_terminal_completion_is_fsynced_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[int] = []
    monkeypatch.setattr("pmcp.scoped_advisor_audit.os.fsync", calls.append)
    audit = ScopedAdvisorAudit(tmp_path / "audit.jsonl", policy_digest="d" * 64)
    audit.complete()
    audit.complete()
    assert len(calls) == 1
    validate_scoped_advisor_audit(tmp_path / "audit.jsonl")


def test_capability_probe_is_machine_readable() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "pmcp", "capabilities", "--json"],
        capture_output=True,
        text=True,
        check=True,
    )
    payload = json.loads(result.stdout)
    assert payload["pmcp_version"]
    assert payload["capabilities"][0]["name"] == SCOPED_ADVISOR_AUDIT_CAPABILITY
    assert (
        "terminal_completion_fsync" in payload["capabilities"][0]["activation_requires"]
    )


# --- gate rejections reach the audit (Consiliency/pmcp#296) --------------------

#: The exact key set of every ``audit.rejection`` record. A new key is a new
#: channel out of an unvalidated payload, so it must be added here on purpose.
_REJECTION_RECORD_KEYS = frozenset(
    {
        "sequence",
        "event",
        "schema",
        "audit_session_id",
        "timestamp",
        "policy_digest",
        "gateway_tool",
        "gateway_tool_digest",
        "terminal_status",
        "rejected_argument_path",
        "rejected_argument_validator",
    }
)


def _scoped_server(tmp_path: Path) -> tuple[GatewayServer, Path]:
    audit_path = tmp_path / "audit.jsonl"
    server = GatewayServer(
        policy_path=_write_scoped_policy(tmp_path / "policy.json"),
        audit_jsonl=audit_path,
    )
    server._create_server()
    return server, audit_path


async def _call(server: GatewayServer, name: str, arguments: dict) -> Any:
    assert server._server is not None
    entry = server._server.get_request_handler("tools/call")
    assert entry is not None
    return await entry.handler(
        _make_ctx(), CallToolRequestParams(name=name, arguments=arguments)
    )


@pytest.mark.asyncio
async def test_gate_rejections_are_audited_without_argument_values(
    tmp_path: Path,
) -> None:
    server, audit_path = _scoped_server(tmp_path)
    dispatched: list[dict] = []

    async def recording_invoke(arguments: dict) -> dict:
        dispatched.append(arguments)
        return {"ok": True}

    server._gateway_tools.invoke = recording_invoke  # type: ignore[method-assign]
    cases = [
        # (arguments, expected path, expected keyword)
        (
            {"tool_id": "firecrawl::web_search", "arguments": "sk-TYPE-SECRET"},
            ["arguments"],
            "type",
        ),
        (
            {
                "tool_id": "firecrawl::web_search",
                **_correlations(),
                "evidence_label_digest": "sk-PATTERN-SECRET" + "Z" * 47,
            },
            ["evidence_label_digest"],
            "pattern",
        ),
        (
            {
                "tool_id": "firecrawl::web_search",
                **_correlations(),
                "run_correlation_id": ["run-LIST-SECRET"],
            },
            ["run_correlation_id"],
            "type",
        ),
        (
            {"tool_id": "firecrawl::web_search", "task": {"enabled": "sk-ENUM"}},
            ["task", "enabled"],
            "type",
        ),
        ({"arguments": {"q": "sk-MISSING-SECRET"}}, [], "required"),
    ]
    for arguments, _, _ in cases:
        result = await _call(server, "gateway.invoke", arguments)
        # The caller still gets the gate's own message, unchanged.
        assert result.is_error is True
        assert result.content[0].text.startswith("Input validation error: ")
    assert dispatched == []
    await server.shutdown()

    records = validate_scoped_advisor_audit(audit_path)
    # No `audit.invocation` at all: agent-harness's board ledger
    # (`advisor_board/research.py:465-481` @ b9627d53) correlates every
    # `gateway.invoke` invocation to its run, and a record without
    # correlations would fail the seat as `audit_correlation_mismatch`.
    assert [r for r in records if r["event"] == "audit.invocation"] == []
    rejections = [r for r in records if r["event"] == "audit.rejection"]
    assert [r["terminal_status"] for r in rejections] == ["invalid_arguments"] * len(
        cases
    )
    assert [
        (r["rejected_argument_path"], r["rejected_argument_validator"])
        for r in rejections
    ] == [(path, keyword) for _, path, keyword in cases]
    for record in rejections:
        assert set(record) == _REJECTION_RECORD_KEYS
        assert record["gateway_tool"] == "gateway.invoke"
    raw_audit = audit_path.read_text()
    for forbidden in (
        "sk-TYPE-SECRET",
        "sk-PATTERN-SECRET",
        "run-LIST-SECRET",
        "sk-ENUM",
        "sk-MISSING-SECRET",
        "run-103",
        "seat-codex",
        "firecrawl::web_search",
        "is not of type",
        "does not match",
    ):
        assert forbidden not in raw_audit


@pytest.mark.asyncio
async def test_policy_is_judged_before_the_gate(tmp_path: Path) -> None:
    """A malformed call to a blocked tool is `denied`, not `invalid_arguments`.

    `gateway.provision` is outside the scoped policy. Its argument is the
    wrong type, which the gate would reject, but policy answers first and
    the blocked tool's schema is not disclosed.
    """
    server, audit_path = _scoped_server(tmp_path)
    result = await _call(
        server, "gateway.provision", {"server_name": 12345, "api_key": "sk-X"}
    )
    text = result.content[0].text
    assert "blocked by policy" in json.loads(text)["message"]
    assert "Input validation error" not in text
    await server.shutdown()
    records = validate_scoped_advisor_audit(audit_path)
    assert [
        r["terminal_status"] for r in records if r["event"] == "audit.invocation"
    ] == ["denied"]
    assert "sk-X" not in audit_path.read_text()


@pytest.mark.asyncio
async def test_the_policy_verdict_is_read_once_per_call(tmp_path: Path) -> None:
    """No name can skip the gate as blocked and then be dispatched as allowed.

    The stub answers "blocked" the first time and "allowed" after. Reading
    the verdict twice would skip the schema gate and then dispatch the
    malformed arguments to the handler.
    """
    server, _ = _scoped_server(tmp_path)
    verdicts = iter([False, True, True, True])
    server._policy_manager.is_gateway_tool_allowed = (  # type: ignore[method-assign]
        lambda name: next(verdicts)
    )
    dispatched: list[dict] = []

    async def recording_describe(arguments: dict) -> dict:
        dispatched.append(arguments)
        return {"ok": True}

    server._gateway_tools.describe = recording_describe  # type: ignore[method-assign]
    result = await _call(server, "gateway.describe", {"tool_id": ""})
    assert "blocked by policy" in json.loads(result.content[0].text)["message"]
    assert dispatched == []
    await server.shutdown()


@pytest.mark.asyncio
async def test_gate_rejection_with_a_dead_sink_fails_closed(tmp_path: Path) -> None:
    server, _ = _scoped_server(tmp_path)
    assert server._scoped_advisor_audit is not None
    assert server._scoped_advisor_audit._file is not None
    server._scoped_advisor_audit._file.close()
    result = await _call(server, "gateway.describe", {"tool_id": ""})
    assert json.loads(result.content[0].text) == {
        "error": True,
        "message": "Scoped advisor audit channel failed",
    }
    assert not result.is_error


def _first_error(instance: Any, schema: dict[str, Any]) -> jsonschema.ValidationError:
    error = jsonschema.exceptions.best_match(
        jsonschema.validators.validator_for(schema)(schema).iter_errors(instance)
    )
    assert error is not None
    return error


def _record_one(
    tmp_path: Path,
    error: jsonschema.ValidationError,
    schema: dict[str, Any],
    arguments: Any,
) -> dict[str, Any]:
    path = tmp_path / f"audit-{len(list(tmp_path.iterdir()))}.jsonl"
    audit = ScopedAdvisorAudit(path, policy_digest="e" * 64)
    audit.record_rejected_arguments(
        gateway_tool="gateway.invoke", error=error, schema=schema, arguments=arguments
    )
    audit.complete()
    records = validate_scoped_advisor_audit(path)
    assert "sk-" not in path.read_text()
    return records[1]


class _EqualsEverything(str):
    """A ``str`` subclass that claims to equal any declared name."""

    def __eq__(self, other: object) -> bool:
        return True

    def __hash__(self) -> int:
        return hash("tool_id")


_CALLER_KEYED_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "env": {"type": "object", "additionalProperties": {"type": "integer"}},
        "hdrs": {
            "type": "object",
            "patternProperties": {"^x-": {"type": "integer"}},
        },
        "names": {"type": "array", "items": {"type": "string"}},
        "tool_id": {"type": "string"},
        "a": {
            "anyOf": [
                {"type": "object", "properties": {"b": {"type": "string"}}},
                {"type": "integer"},
            ]
        },
    },
}


@pytest.mark.parametrize(
    ("arguments", "expected_path", "keyword"),
    [
        # A key the caller chose under additionalProperties is itself redacted.
        ({"env": {"sk-KEY-SECRET": "v"}}, ["env", None], "type"),
        # ... whatever its shape: an identifier-like key is no more public.
        ({"env": {"caller_chosen_key_value": "v"}}, ["env", None], "type"),
        ({"env": {"CallerChosenKey42": "v"}}, ["env", None], "type"),
        # A schema keyword is not a declared property name either.
        ({"env": {"additionalProperties": "v"}}, ["env", None], "type"),
        # ... and so is one matched by patternProperties.
        ({"hdrs": {"x-sk-HEADER": "v"}}, ["hdrs", None], "type"),
        # A caller key that equals a declared name elsewhere discloses nothing.
        ({"env": {"tool_id": "v"}}, ["env", "tool_id"], "type"),
        # An in-process int key is not an array index.
        ({"env": {12345: "v"}}, ["env", None], "type"),
        # A str subclass cannot smuggle its content past the membership test.
        ({"env": {_EqualsEverything("sk-EQ-SECRET"): "v"}}, ["env", None], "type"),
        # An array index survives, as a position.
        ({"names": ["ok", 7]}, ["names", 1], "type"),
        # best_match returns an anyOf child: its `.path` is relative (["b"]).
        ({"a": {"b": 1}}, ["a", "b"], "type"),
    ],
)
def test_rejected_argument_path_redacts_caller_chosen_keys(
    tmp_path: Path, arguments: dict, expected_path: list, keyword: str
) -> None:
    error = _first_error(arguments, _CALLER_KEYED_SCHEMA)
    record = _record_one(tmp_path, error, _CALLER_KEYED_SCHEMA, arguments)
    assert record["rejected_argument_path"] == expected_path
    assert record["rejected_argument_validator"] == keyword


@pytest.mark.parametrize("keyword", ["sk-KEYWORD-SECRET", "caller_chosen_keyword"])
def test_an_unknown_validator_keyword_is_not_recorded(
    tmp_path: Path, keyword: str
) -> None:
    error = jsonschema.ValidationError("m", validator=keyword, path=["tool_id"])
    record = _record_one(tmp_path, error, _CALLER_KEYED_SCHEMA, {"tool_id": "x"})
    assert record["rejected_argument_validator"] is None
    assert record["rejected_argument_path"] == ["tool_id"]


class _PoisonedError(jsonschema.ValidationError):
    """Every attribute that can carry the rejected value raises on access."""

    def _poisoned(self: Any) -> Any:
        raise AssertionError("the audit read a value-bearing error attribute")

    message = property(_poisoned)  # type: ignore[assignment]
    instance = property(_poisoned)  # type: ignore[assignment]
    validator_value = property(_poisoned)  # type: ignore[assignment]
    context = property(_poisoned)  # type: ignore[assignment]
    cause = property(_poisoned)  # type: ignore[assignment]
    json_path = property(_poisoned)  # type: ignore[assignment]
    schema = property(_poisoned)  # type: ignore[assignment]


def test_the_rejection_record_never_reads_the_message_or_the_instance(
    tmp_path: Path,
) -> None:
    arguments = {"env": {"sk-KEY": "sk-VALUE"}}
    error = _first_error(arguments, _CALLER_KEYED_SCHEMA)
    error.__class__ = _PoisonedError
    record = _record_one(tmp_path, error, _CALLER_KEYED_SCHEMA, arguments)
    assert set(record) == _REJECTION_RECORD_KEYS
    assert record["rejected_argument_path"] == ["env", None]


# --- rev 2: nothing the caller chose reaches a denied record (Consiliency/pmcp#296) ---

#: Everything in a record that does not come from the writer's clock or counter.
_VOLATILE_KEYS = frozenset({"sequence", "timestamp"})


def _caller_values(tag: str) -> dict[str, str]:
    """Values shaped to pass every filter `record_invocation` applies.

    Each fits the correlation charset, the tool-id pattern, the 64-hex digest
    pattern or the public-URL scan, so a record that reads the field at all
    carries it. Identifier-like on purpose: a secret need not contain `-`.
    """
    digit = {"a": "0", "b": "1"}[tag]
    return {
        "run_correlation_id": f"caller_chosen_run_value_{tag}",
        "seat_correlation_id": f"caller_chosen_seat_value_{tag}",
        "evidence_label_digest": digit * 64,
        "tool_id": f"caller::chosen_tool_value_{tag}",
        "note": f"https://caller-chosen-host-{tag}.example.com/caller_chosen_path",
    }


def _format_tag(value: Any, tag: str) -> Any:
    if isinstance(value, str):
        return value.format(tag=tag)
    if isinstance(value, list):
        return [_format_tag(item, tag) for item in value]
    return value


def _stable(record: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in record.items() if k not in _VOLATILE_KEYS}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("label", "names", "base", "allow_everything", "response"),
    [
        (
            "blocked, malformed",
            ("gateway.provision", "gateway.provision"),
            # Malformed (not a string) yet URL-bearing, in the one key
            # `provision` declares: a filter run before the gate would read it.
            {"server_name": ["https://caller-chosen-server-{tag}.example.com/p"]},
            False,
            "blocked by policy",
        ),
        (
            "blocked, well-formed",
            ("gateway.provision", "gateway.provision"),
            {"server_name": "https://caller-chosen-server-{tag}.example.com/p"},
            False,
            "blocked by policy",
        ),
        (
            "unregistered name, blocked",
            ("gateway.caller_chosen_name_a", "gateway.caller_chosen_name_b"),
            {},
            False,
            "blocked by policy",
        ),
        (
            # Unreachable under the scoped policy (it allows four registered
            # names), so pinned with a policy that allows every name: the
            # arguments never met a schema, so they never reach the record.
            "unregistered name, allowed",
            ("gateway.caller_chosen_name_a", "gateway.caller_chosen_name_b"),
            {},
            True,
            "Unknown tool",
        ),
    ],
    ids=[
        "blocked-malformed",
        "blocked-wellformed",
        "unregistered",
        "unregistered-allowed",
    ],
)
async def test_an_ungated_call_records_nothing_the_caller_chose(
    tmp_path: Path,
    label: str,
    names: tuple[str, str],
    base: dict[str, Any],
    allow_everything: bool,
    response: str,
) -> None:
    """Two calls that differ only in what the caller chose leave equal records.

    The differential is the oracle: it does not depend on knowing which field
    `record_invocation` reads, so a new channel fails it too. Every value is
    shaped to survive that method's charset filters.
    """
    server, audit_path = _scoped_server(tmp_path)
    if allow_everything:
        server._policy_manager.is_gateway_tool_allowed = (  # type: ignore[method-assign]
            lambda name: True
        )
    for name, tag in zip(names, ("a", "b")):
        tagged = {key: _format_tag(value, tag) for key, value in base.items()}
        result = await _call(server, name, {**tagged, **_caller_values(tag)})
        assert response in result.content[0].text, label
    await server.shutdown()

    records = validate_scoped_advisor_audit(audit_path)
    first, second = [r for r in records if r["event"] == "audit.invocation"]
    assert _stable(first) == _stable(second), label
    for field in (
        "run_correlation_id",
        "seat_correlation_id",
        "evidence_label_digest",
        "downstream_tool_id",
        "source_reference_hash",
    ):
        assert first[field] is None, (label, field)
    raw_audit = audit_path.read_text()
    for forbidden in ("caller_chosen", "caller::chosen", "caller-chosen", "0" * 64):
        assert forbidden not in raw_audit, (label, forbidden)


@pytest.mark.asyncio
async def test_a_gated_call_records_only_the_keys_its_schema_declares(
    tmp_path: Path,
) -> None:
    """The gate ignores undeclared keys (until piece B of Consiliency/pmcp#236).

    `gateway.describe` declares only `tool_id`, so the correlation-shaped keys
    and the URL below pass the gate unexamined. They must not reach the record.
    """
    server, audit_path = _scoped_server(tmp_path)

    async def stub_describe(arguments: dict) -> dict:
        return {"ok": True}

    server._gateway_tools.describe = stub_describe  # type: ignore[method-assign]
    for tag in ("a", "b"):
        undeclared = {k: v for k, v in _caller_values(tag).items() if k != "tool_id"}
        result = await _call(
            server,
            "gateway.describe",
            {"tool_id": "firecrawl::web_search", **undeclared},
        )
        assert json.loads(result.content[0].text) == {"ok": True}
    await server.shutdown()

    records = validate_scoped_advisor_audit(audit_path)
    first, second = [r for r in records if r["event"] == "audit.invocation"]
    assert _stable(first) == _stable(second)
    assert first["terminal_status"] == "success"
    # A declared, gate-checked field still reaches the record.
    assert first["downstream_tool_id"] == "firecrawl::web_search"
    for field in (
        "run_correlation_id",
        "seat_correlation_id",
        "evidence_label_digest",
        "source_reference_hash",
    ):
        assert first[field] is None, field
    raw_audit = audit_path.read_text()
    for forbidden in ("caller_chosen", "caller-chosen", "0" * 64):
        assert forbidden not in raw_audit, forbidden


@pytest.mark.asyncio
async def test_rejected_and_denied_calls_never_log_argument_values(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """No rejection or denial path logs a value or the jsonschema message."""
    caplog.set_level(logging.DEBUG)
    secret = "caller_chosen_logged_value"
    (tmp_path / "live").mkdir()
    (tmp_path / "dead").mkdir()
    live, _ = _scoped_server(tmp_path / "live")
    await _call(  # E1: an allowed tool's gate rejection echoes the value
        live,
        "gateway.invoke",
        {"tool_id": "firecrawl::web_search", "arguments": secret, **_correlations()},
    )
    await _call(  # E4: blocked, malformed
        live, "gateway.provision", {"server_name": 12345, "run_correlation_id": secret}
    )
    await _call(  # E4: blocked, well-formed
        live, "gateway.provision", {"server_name": "x", "run_correlation_id": secret}
    )
    await _call(  # E4: unregistered name
        live, f"gateway.{secret}", {"run_correlation_id": secret}
    )
    await live.shutdown()

    dead, _ = _scoped_server(tmp_path / "dead")
    assert dead._scoped_advisor_audit is not None
    assert dead._scoped_advisor_audit._file is not None
    dead._scoped_advisor_audit._file.close()
    result = await _call(  # E2: the same rejection with a dead sink
        dead,
        "gateway.invoke",
        {"tool_id": "firecrawl::web_search", "arguments": secret, **_correlations()},
    )
    assert "Scoped advisor audit channel failed" in result.content[0].text

    assert caplog.records, "nothing was logged, so this test proves nothing"
    for forbidden in (secret, "is not of type", "Input validation error"):
        assert forbidden not in caplog.text, forbidden


def test_the_rejection_names_only_a_scoped_gateway_tool(tmp_path: Path) -> None:
    arguments = {"tool_id": 1}
    error = _first_error(arguments, _CALLER_KEYED_SCHEMA)
    path = tmp_path / "audit.jsonl"
    audit = ScopedAdvisorAudit(path, policy_digest="e" * 64)
    audit.record_rejected_arguments(
        gateway_tool="gateway.provision",
        error=error,
        schema=_CALLER_KEYED_SCHEMA,
        arguments=arguments,
    )
    audit.complete()
    record = validate_scoped_advisor_audit(path)[1]
    assert record["gateway_tool"] is None
    assert record["rejected_argument_path"] == ["tool_id"]


class _RaisingDict(dict):
    """A mapping an in-process caller built, whose lookups raise."""

    def __getitem__(self, key: Any) -> Any:
        raise ValueError("caller_chosen_lookup_failure")


def _raising_path_error(exc_type: type[Exception]) -> jsonschema.ValidationError:
    """An error whose `absolute_path` raises `exc_type` when read."""

    class _RaisingPath(jsonschema.ValidationError):
        @property  # type: ignore[override]
        def absolute_path(self) -> Any:
            raise exc_type("caller_chosen_lookup_failure")

    error = _first_error({"env": {"k": "v"}}, _CALLER_KEYED_SCHEMA)
    error.__class__ = _RaisingPath
    return error


@pytest.mark.parametrize("exc_type", [ValueError, KeyError, TypeError])
def test_a_rejection_that_cannot_be_described_fails_closed_and_keeps_the_sink(
    tmp_path: Path, exc_type: type[Exception]
) -> None:
    """Any exception while describing the rejection is an audit failure.

    It surfaces as `ScopedAdvisorAuditError`, which the gate answers with
    "channel failed", instead of escaping `_handle_call_tool` unaudited; it
    writes nothing, and the sink stays live for the next call. `ValueError`
    comes from a caller's mapping during the path walk; `KeyError` and
    `TypeError` -- which the walk itself tolerates on a lookup -- from reading
    the error's path.
    """
    error = _first_error({"env": {"k": "v"}}, _CALLER_KEYED_SCHEMA)
    path = tmp_path / "audit.jsonl"
    audit = ScopedAdvisorAudit(path, policy_digest="e" * 64)
    if exc_type is ValueError:
        failing_error = error
        arguments: dict[str, Any] = {"env": _RaisingDict({"k": "v"})}
    else:
        failing_error = _raising_path_error(exc_type)
        arguments = {"env": {"k": "v"}}
    with pytest.raises(ScopedAdvisorAuditError) as raised:
        audit.record_rejected_arguments(
            gateway_tool="gateway.invoke",
            error=failing_error,
            schema=_CALLER_KEYED_SCHEMA,
            arguments=arguments,
        )
    # Not in the message, and not in a logged traceback either: the original
    # exception is neither the cause nor displayed as the context.
    logged = "".join(traceback.format_exception(raised.value))
    assert "caller_chosen" not in logged
    audit.require_available()
    audit.record_rejected_arguments(
        gateway_tool="gateway.invoke",
        error=error,
        schema=_CALLER_KEYED_SCHEMA,
        arguments={"env": {"k": "v"}},
    )
    audit.complete()
    records = validate_scoped_advisor_audit(path)
    assert [r["event"] for r in records] == [
        "audit.started",
        "audit.rejection",
        "audit.completed",
    ]


@pytest.mark.asyncio
async def test_an_invoke_source_hash_comes_from_its_declared_arguments(
    tmp_path: Path,
) -> None:
    """`invoke.arguments` is declared, so its URL still reaches the record.

    The downstream result carries no URL here, so the only source for
    `source_reference_hash` -- which the ledger needs to verify a claim -- is
    the declared `arguments` object.
    """
    server, audit_path = _scoped_server(tmp_path)

    async def stub_invoke(arguments: dict) -> dict:
        return {"ok": True, "result": "a page with no link in it"}

    server._gateway_tools.invoke = stub_invoke  # type: ignore[method-assign]
    result = await _call(
        server,
        "gateway.invoke",
        {
            "tool_id": "firecrawl::web_search",
            "arguments": {"url": "https://source.example/article"},
            **_correlations(),
        },
    )
    assert json.loads(result.content[0].text)["ok"] is True
    await server.shutdown()

    records = validate_scoped_advisor_audit(audit_path)
    (record,) = [r for r in records if r["event"] == "audit.invocation"]
    assert (
        record["source_reference_hash"]
        == hashlib.sha256(b"https://source.example/article").hexdigest()
    )
    for field in _LEDGER_READ_FIELDS:
        assert record[field] is not None, field


@pytest.mark.asyncio
async def test_an_allowed_call_that_raises_records_only_declared_keys(
    tmp_path: Path,
) -> None:
    """The `except` arm (E9) of a registered, allowed tool reads the filter too."""
    server, audit_path = _scoped_server(tmp_path)

    async def failing_describe(arguments: dict) -> dict:
        raise ValueError("downstream describe failed")

    server._gateway_tools.describe = failing_describe  # type: ignore[method-assign]
    for tag in ("a", "b"):
        undeclared = {k: v for k, v in _caller_values(tag).items() if k != "tool_id"}
        await _call(
            server,
            "gateway.describe",
            {"tool_id": "firecrawl::web_search", **undeclared},
        )
    await server.shutdown()

    records = validate_scoped_advisor_audit(audit_path)
    first, second = [r for r in records if r["event"] == "audit.invocation"]
    assert first["terminal_status"] == "failure"
    assert _stable(first) == _stable(second)
    assert first["downstream_tool_id"] == "firecrawl::web_search"
    for field in (
        "run_correlation_id",
        "seat_correlation_id",
        "evidence_label_digest",
        "source_reference_hash",
    ):
        assert first[field] is None, field
    raw_audit = audit_path.read_text()
    for forbidden in ("caller_chosen", "caller-chosen", "0" * 64):
        assert forbidden not in raw_audit, forbidden


# --- rev 4/5: a generated sweep of caller values over every tool and path --
#
# Rounds 1-4 of the board each found a mutant the caller values missed. This
# sweep generates them, for every registered gateway tool on every path
# through `_handle_call_tool` that writes a record, over these axes:
#
# - key spelling: every name `record_invocation` reads (`_RECORD_READ_NAMES`),
#   every name the tool declares, `meta`/`_meta`, and a caller key whose own
#   name varies per call -- each as is, `_`-prefixed, upper-case, title-case,
#   with a Cyrillic look-alike letter, and with a zero-width suffix; minus the
#   names the tool declares where the gate vouches for them;
# - value shape: a correlation-shaped string, a `tool::id`-shaped string, a
#   64-hex digest, a public URL, a dict and a list nesting a URL, a number;
# - position: top level; nested inside every declared object-typed key the
#   gate accepts extra content in (rev 5); nested in a declared key on
#   ungated paths;
# - the two calls' size: the tags differ in length and the second call
#   carries more keys, so a length or a key count of the input is not
#   invariant across the pair (rev 5);
# - exception type on the `except` arm: every exception class raised anywhere
#   in `src/pmcp`, found by AST, and `GatewayException` with every
#   `ErrorCode` (rev 5);
# - exit E6: a scoped `gateway.invoke` without correlations (rev 5).
#
# The oracle is differential, so it needs no list of channels: two calls that
# differ only in the generated values must leave equal records, and on an
# allowed tool that record must equal the one for the declared arguments
# alone. On top, no generated marker, URL, or hash of either appears in the
# audit, and no marker appears in any log record as pmcp's own text and JSON
# formatters render it, traceback included (rev 5).
#
# One documented exception (F1, *Non-goals*): a URL nested under a declared
# object key on an allowed path still reaches `source_reference_hash` through
# the recursive URL scan, as on main, until piece B of Consiliency/pmcp#236
# forbids the undeclared nested key. That one field is exempt only there.

#: Argument names `record_invocation` reads by name.
_RECORD_READ_NAMES = (
    "tool_id",
    "run_correlation_id",
    "seat_correlation_id",
    "evidence_label_digest",
)
_SHAPES = ("correlation", "tool_id", "digest", "url", "dict", "list", "number")
#: Two tags of different length; the second call also carries more keys.
_TAGS = ("a", "bbbbbbb")
_NUMBERS = {"a": 7, "bbbbbbb": 40353607}
_MARKERS = (
    "caller_marker",
    "caller::marker",
    "caller-host",
    "caller_path",
    "caller_key",
)
#: The index of the one generated value that sits in a declared key the gate
#: rejects (see `_invalid_declared`).
_INVALID_INDEX = 99
#: The one pre-existing log line this plan does not own, and exactly it:
#: `call_tool`'s `except Exception` logs `f"Tool execution error: {e}"`, which
#: echoes a pydantic `InvokeInput` error's input -- e.g. a non-dict `meta`
#: reaching the model by alias, on main too (Consiliency/pmcp#297 names it) --
#: and an unknown tool's name (unreachable under the scoped policy).
_FOREIGN_LOG_PREFIX = "Tool execution error: "
_FOREIGN_LOG_CHANNELS = ("validation error for InvokeInput", "Unknown tool:")
#: Only for `gateway.invoke` may a generated key change the status and so the
#: result digest: an undeclared `meta` reaches `InvokeInput` by alias.
_INVOKE_ONLY_EXEMPT = frozenset({"terminal_status", "redacted_result_digest"})


def _gateway_tools_by_name() -> dict[str, Any]:
    from pmcp.tools.handlers import get_gateway_tool_definitions

    return {tool.name: tool for tool in get_gateway_tool_definitions()}


def _spellings(name: str) -> set[str]:
    return {
        name,
        "_" + name,
        name.upper(),
        name.title(),
        name.replace("e", "\u0435", 1).replace("a", "\u0430", 1),
        name + "\u200b",
    }


def _generated_keys(declared: set[str], tag: str) -> list[str]:
    """Key spellings; the caller's own key names vary with `tag`, and the
    second tag brings one more of them, so the key count differs too."""
    names = set(_RECORD_READ_NAMES) | declared | {"meta", "_meta", f"caller_key_{tag}"}
    if tag == _TAGS[1]:
        names.add(f"caller_key_{tag}_extra")
    keys: set[str] = set()
    for name in names:
        keys |= _spellings(name)
    return sorted(keys - declared)


def _generated_value(shape: str, tag: str, index: int) -> Any:
    url = f"https://caller-host-{tag}{index}.example.com/caller_path_{tag}{index}"
    if shape == "correlation":
        return f"caller_marker_{tag}{index}"
    if shape == "tool_id":
        return f"caller::marker_{tag}{index}"
    if shape == "digest":
        return hashlib.sha256(f"caller_marker_{tag}{index}".encode()).hexdigest()
    if shape == "url":
        return url
    if shape == "dict":
        return {"caller_marker_inner": {"deep": url}}
    if shape == "list":
        return [f"caller_marker_{tag}{index}", [url]]
    assert shape == "number"
    return _NUMBERS[tag] + index


def _generated(keys: list[str], shape: str, tag: str) -> dict[str, Any]:
    return {key: _generated_value(shape, tag, i) for i, key in enumerate(keys)}


def _mixed(keys: list[str], tag: str) -> dict[str, Any]:
    """Every shape in one call. A key's shape follows from its name, so a key
    both tags carry (e.g. `meta`) has the same shape in both calls."""
    return {
        key: _generated_value(_SHAPES[zlib.crc32(key.encode()) % len(_SHAPES)], tag, i)
        for i, key in enumerate(keys)
    }


def _url_hash(url: str) -> str:
    return hashlib.sha256(url.lower().split("?")[0].encode()).hexdigest()


def _forbidden_in_audit(count: int, tag: str, *, url_hash: bool = True) -> set[str]:
    """Every generated string, and each hash of one, the audit could carry."""
    forbidden = set(_MARKERS)
    for i in [*range(count), _INVALID_INDEX]:
        forbidden.add(_generated_value("digest", tag, i))
        if url_hash:
            forbidden.add(_url_hash(_generated_value("url", tag, i)))
        for shape in (
            ("correlation", "tool_id", "url")
            if url_hash
            else ("correlation", "tool_id")
        ):
            value = _generated_value(shape, tag, i)
            forbidden.add(hashlib.sha256(value.encode()).hexdigest())
            forbidden.add(hashlib.sha256(json.dumps(value).encode()).hexdigest())
    return forbidden


def _declared_baseline(tool: Any, *, correlated: bool = True) -> dict[str, Any]:
    """The smallest arguments the tool's schema accepts, from its schema."""
    schema = tool.input_schema
    baseline: dict[str, Any] = {}
    for name in schema.get("required") or []:
        prop = schema["properties"][name]
        if "enum" in prop:
            baseline[name] = prop["enum"][0]
        else:
            baseline[name] = "x" * max(prop.get("minLength", 1), 1)
    if tool.name == "gateway.invoke" and correlated:
        # Correlated, so the scoped `InvokeInput` check passes and the
        # allowed path reaches the handler.
        baseline.update(_correlations())
    jsonschema.validate(baseline, schema)
    return baseline


def _open_containers(tool: Any, baseline: dict[str, Any]) -> list[str]:
    """Declared object-typed keys the gate lets generated content into."""
    containers = []
    for name, prop in sorted((tool.input_schema.get("properties") or {}).items()):
        types = prop.get("type")
        types = types if isinstance(types, list) else [types]
        if "object" not in types:
            continue
        probe = {**baseline, name: _mixed(_generated_keys(set(), _TAGS[1]), _TAGS[1])}
        try:
            jsonschema.validate(probe, tool.input_schema)
        except jsonschema.ValidationError:
            continue
        containers.append(name)
    return containers


#: Constructor arguments for raised exception classes whose `__init__` needs
#: more than a message. A new such class fails `_raised_exceptions` loudly.
_EXCEPTION_ARGS: dict[str, tuple[Any, ...]] = {
    "HTTPError": ("https://example.invalid/", 500, "stub handler failed", None, None),
    "ResourceServerAuthError": ("invalid_token", "stub handler failed"),
    "MissingRemoteHeaderAuthError": ("stub", ["STUB_VAR"]),
    "MissingApiKeyError": ("STUB_VAR", "stub", "stub"),
}


def _raised_exceptions() -> list[BaseException]:
    """One instance of every exception class `src/pmcp` raises (by AST), and
    a `GatewayException` for every `ErrorCode`.

    `ScopedAdvisorAuditError` is left out: from a handler it takes E8, which
    writes no record by design and has its own tests.
    """
    import ast
    import importlib

    from pmcp.errors import ErrorCode, GatewayException

    root = _REPO_ROOT / "src"
    classes: dict[type, None] = {}
    for path in sorted((root / "pmcp").rglob("*.py")):
        if "baml_client" in path.parts:
            continue
        expressions = {
            ast.unparse(node.exc.func if isinstance(node.exc, ast.Call) else node.exc)
            for node in ast.walk(ast.parse(path.read_text()))
            if isinstance(node, ast.Raise)
            and node.exc is not None
            and isinstance(
                node.exc.func if isinstance(node.exc, ast.Call) else node.exc,
                (ast.Name, ast.Attribute),
            )
        }
        if not expressions:
            continue
        parts = path.relative_to(root).with_suffix("").parts
        module = importlib.import_module(
            ".".join(parts[:-1] if parts[-1] == "__init__" else parts)
        )
        for expression in sorted(expressions):
            try:
                value = eval(expression, vars(module))  # noqa: S307
            except Exception:
                continue  # a re-raised variable, not a class
            if isinstance(value, type) and issubclass(value, Exception):
                classes[value] = None
    assert GatewayException in classes and KeyError in classes, sorted(
        map(str, classes)
    )
    instances: list[BaseException] = []
    for cls in classes:
        if cls is ScopedAdvisorAuditError:
            continue
        if cls is GatewayException:
            instances.extend(GatewayException(code) for code in ErrorCode)
            continue
        instances.append(
            cls(*_EXCEPTION_ARGS.get(cls.__name__, ("stub handler failed",)))
        )
    return instances


class _StubGatewayTools:
    """Stands in for `GatewayTools`: every handler returns, or raises `error`."""

    def __init__(self, error: BaseException | None = None) -> None:
        self.error = error

    def __getattr__(self, name: str) -> Any:
        async def handler(*args: Any, **kwargs: Any) -> dict:
            if self.error is not None:
                raise self.error
            return {"ok": True}

        return handler


def _invocations(audit_path: Path, event: str) -> list[dict[str, Any]]:
    return [
        _stable(r)
        for r in validate_scoped_advisor_audit(audit_path)
        if r["event"] == event
    ]


def _assert_nothing_generated_leaked(
    raw_audit: str, log_text: str, count: int, *, url_hash: bool = True
) -> None:
    """Neither the audit nor the formatted log carries a generated value or
    any hash of one (the same set for both, per the review panel on
    Consiliency/pmcp#304). Lengths and key counts are covered by the pair
    differential on each call's log (`_LoggedCalls`)."""
    for tag in _TAGS:
        for forbidden in _forbidden_in_audit(count + 12, tag, url_hash=url_hash):
            assert forbidden not in raw_audit, forbidden
            assert forbidden not in log_text, ("log", forbidden)


def _is_foreign(message: str) -> bool:
    return message.startswith(_FOREIGN_LOG_PREFIX) and any(
        channel in message for channel in _FOREIGN_LOG_CHANNELS
    )


_PMCP_FORMATTERS: list[logging.Formatter] = []


def _pmcp_formatters() -> list[logging.Formatter]:
    """pmcp's own text and JSON formatters, as `setup_logging` builds them."""
    if not _PMCP_FORMATTERS:
        from pmcp.cli import setup_logging

        root = logging.getLogger()
        level, before = root.level, list(root.handlers)
        for log_format in ("text", "json"):
            setup_logging("DEBUG", log_to_file=False, log_format=log_format)
            for handler in [h for h in root.handlers if h not in before]:
                assert handler.formatter is not None
                _PMCP_FORMATTERS.append(handler.formatter)
                root.removeHandler(handler)
        root.setLevel(level)
    return _PMCP_FORMATTERS


def _log_text(caplog: pytest.LogCaptureFixture) -> str:
    """Every log record as pmcp's formatters render it -- message, and any
    `exc_info` traceback -- bar the one foreign line shape above."""
    # The capture is live (DEBUG, root), so an empty result is not vacuous
    # by accident: the gateway logs on start-up and shutdown.
    assert caplog.records, "nothing was logged, so the log oracle proves nothing"
    return "\n".join(
        formatter.format(record)
        for record in caplog.records
        if not _is_foreign(record.getMessage())
        for formatter in _pmcp_formatters()
    )


_VOLATILE_LOG = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:[.,]\d+)?|0x[0-9a-fA-F]+")


def _normalized_log(records: list[logging.LogRecord]) -> str:
    """Records as pmcp's formatters render them, timestamps and addresses
    removed, the one foreign #297 line shape left out."""
    return "\n".join(
        _VOLATILE_LOG.sub("<v>", formatter.format(record))
        for record in records
        if not _is_foreign(record.getMessage())
        for formatter in _pmcp_formatters()
    )


class _LoggedCalls:
    """Makes calls and keeps the log each one produced, for a pair
    differential on logs as on records: two calls that differ only in the
    generated content -- whose tags differ in length and key count -- must
    log the same thing, so no value, hash, length or count of it is logged."""

    def __init__(self, server: GatewayServer, caplog: pytest.LogCaptureFixture) -> None:
        self.server = server
        self.caplog = caplog

    async def pair(self, label: str, calls: list[tuple[str, dict]]) -> list[Any]:
        logs, results = [], []
        for name, arguments in calls:
            start = len(self.caplog.records)
            results.append(await _call(self.server, name, arguments))
            logs.append(_normalized_log(self.caplog.records[start:]))
        assert all(log == logs[0] for log in logs), (label, logs)
        return results


def _assert_pair(
    tool_name: str, label: str, first: dict, second: dict, exempt: frozenset
) -> None:
    for field in set(first) | set(second):
        if field not in exempt:
            assert first.get(field) == second.get(field), (tool_name, label, field)


def _allowed_server(tmp_path: Path) -> tuple[GatewayServer, Path, Any]:
    server, audit_path = _scoped_server(tmp_path)
    server._policy_manager.is_gateway_tool_allowed = (  # type: ignore[method-assign]
        lambda name: True
    )
    real_tools = server._gateway_tools
    server._gateway_tools = _StubGatewayTools()  # type: ignore[assignment]
    return server, audit_path, real_tools


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", sorted(_gateway_tools_by_name()))
async def test_generated_caller_values_never_reach_an_allowed_record(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, tool_name: str
) -> None:
    """Allowed paths E7 (handler returns) and E9 (handler raises every raised
    exception type), with generated keys at top level and inside every open
    declared container.

    Scope: this sweep runs against `_StubGatewayTools`, whose handlers return
    a constant or raise, so it reaches every tool's record site without a
    downstream but never sees a real handler's result. The four tools the
    scoped policy allows are also swept against the real `GatewayTools` in
    `test_generated_caller_values_on_the_real_scoped_handlers`."""
    caplog.set_level(logging.DEBUG)
    tool = _gateway_tools_by_name()[tool_name]
    declared = set(tool.input_schema.get("properties") or {})
    baseline = _declared_baseline(tool)
    exempt = _INVOKE_ONLY_EXEMPT if tool_name == "gateway.invoke" else frozenset()
    server, audit_path, real_tools = _allowed_server(tmp_path)
    stub = server._gateway_tools
    calls = _LoggedCalls(server, caplog)

    # (label, exempt fields, payload for each tag); the reference is `baseline`.
    cases: list[tuple[str, frozenset, dict[str, dict]]] = []
    for shape in _SHAPES:
        cases.append(
            (
                f"top-level {shape}",
                exempt,
                {
                    tag: {
                        **baseline,
                        **_generated(_generated_keys(declared, tag), shape, tag),
                    }
                    for tag in _TAGS
                },
            )
        )
    containers = _open_containers(tool, baseline)
    for container in containers:
        for shape in _SHAPES:
            cases.append(
                (
                    f"inside {container} {shape}",
                    exempt | {"source_reference_hash"},  # F1
                    {
                        tag: {
                            **baseline,
                            container: {
                                **(baseline.get(container) or {}),
                                **_generated(_generated_keys(set(), tag), shape, tag),
                            },
                        }
                        for tag in _TAGS
                    },
                )
            )

    records_per_case = []
    for error in [None, *_raised_exceptions()]:
        stub.error = error
        reference = await _call(server, tool_name, baseline)
        del reference
        if error is None:
            for label, _, payloads in cases:
                await calls.pair(label, [(tool_name, payloads[tag]) for tag in _TAGS])
        else:
            await calls.pair(
                f"raises {error!r}",
                [
                    (
                        tool_name,
                        {**baseline, **_mixed(_generated_keys(declared, tag), tag)},
                    )
                    for tag in _TAGS
                ],
            )
        records_per_case.append(error)
    server._gateway_tools = real_tools
    await server.shutdown()

    records = _invocations(audit_path, "audit.invocation")
    ok_count = 1 + len(_TAGS) * len(cases)
    ok, raised = records[:ok_count], records[ok_count:]
    reference, generated = ok[0], ok[1:]
    for (label, case_exempt, _), first, second in zip(
        cases, generated[::2], generated[1::2]
    ):
        _assert_pair(tool_name, label, first, second, case_exempt - _INVOKE_ONLY_EXEMPT)
        _assert_pair(tool_name, label, first, reference, case_exempt)
    errors = records_per_case[1:]
    assert len(raised) == 3 * len(errors)
    for error, (reference, first, second) in zip(
        errors, zip(raised[::3], raised[1::3], raised[2::3])
    ):
        label = f"raises {error!r}"
        _assert_pair(tool_name, label, first, second, frozenset())
        _assert_pair(tool_name, label, first, reference, exempt)
    _assert_nothing_generated_leaked(
        audit_path.read_text(),
        _log_text(caplog),
        len(_generated_keys(declared, _TAGS[1])),
        url_hash=not containers,
    )


@pytest.mark.asyncio
async def test_generated_caller_values_never_reach_an_uncorrelated_invoke_record(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Exit E6: a scoped `gateway.invoke` without correlations raises before
    dispatch, inside the audited path; its record and log are caller-free."""
    caplog.set_level(logging.DEBUG)
    tool = _gateway_tools_by_name()["gateway.invoke"]
    declared = set(tool.input_schema.get("properties") or {})
    baseline = _declared_baseline(tool, correlated=False)
    server, audit_path = _scoped_server(tmp_path)
    calls = _LoggedCalls(server, caplog)
    await _call(server, "gateway.invoke", baseline)
    for shape in _SHAPES:
        await calls.pair(
            f"E6 {shape}",
            [
                (
                    "gateway.invoke",
                    {
                        **baseline,
                        **_generated(_generated_keys(declared, tag), shape, tag),
                    },
                )
                for tag in _TAGS
            ],
        )
    await server.shutdown()

    records = _invocations(audit_path, "audit.invocation")
    assert len(records) == 1 + len(_TAGS) * len(_SHAPES)
    reference, generated = records[0], records[1:]
    assert reference["terminal_status"] == "failure"
    for shape, first, second in zip(_SHAPES, generated[::2], generated[1::2]):
        _assert_pair("gateway.invoke", f"E6 {shape}", first, second, frozenset())
        _assert_pair(
            "gateway.invoke", f"E6 {shape}", first, reference, _INVOKE_ONLY_EXEMPT
        )
    _assert_nothing_generated_leaked(
        audit_path.read_text(),
        _log_text(caplog),
        len(_generated_keys(declared, _TAGS[1])),
    )


def _invalid_declared(tool: Any, baseline: dict[str, Any], tag: str) -> dict | None:
    """The baseline with one declared value the gate rejects, or None."""
    for name in sorted(tool.input_schema.get("properties") or {}):
        for shape in ("dict", "list", "number", "correlation"):
            candidate = {**baseline, name: _generated_value(shape, tag, _INVALID_INDEX)}
            try:
                jsonschema.validate(candidate, tool.input_schema)
            except jsonschema.ValidationError:
                return candidate
    return None


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", sorted(_gateway_tools_by_name()))
async def test_generated_caller_values_never_reach_a_rejection_record(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, tool_name: str
) -> None:
    caplog.set_level(logging.DEBUG)
    tool = _gateway_tools_by_name()[tool_name]
    declared = set(tool.input_schema.get("properties") or {})
    baseline = _declared_baseline(tool)
    if _invalid_declared(tool, baseline, _TAGS[0]) is None:
        # No declared property -- the gate accepts any object until piece B
        # of Consiliency/pmcp#236 -- so this tool has no rejection path.
        assert not declared, tool_name
        return
    server, audit_path = _scoped_server(tmp_path)
    server._policy_manager.is_gateway_tool_allowed = (  # type: ignore[method-assign]
        lambda name: True
    )
    calls = _LoggedCalls(server, caplog)
    for shape in _SHAPES:
        payloads = []
        for tag in _TAGS:
            invalid = _invalid_declared(tool, baseline, tag)
            assert invalid is not None
            keys = _generated_keys(declared, tag)
            payloads.append((tool_name, {**invalid, **_generated(keys, shape, tag)}))
        for result in await calls.pair(f"rejected {shape}", payloads):
            assert result.is_error is True
    await server.shutdown()

    assert _invocations(audit_path, "audit.invocation") == []
    records = _invocations(audit_path, "audit.rejection")
    assert len(records) == len(_TAGS) * len(_SHAPES)
    for shape, first, second in zip(_SHAPES, records[::2], records[1::2]):
        assert first == second, (tool_name, shape)
    _assert_nothing_generated_leaked(
        audit_path.read_text(),
        _log_text(caplog),
        len(_generated_keys(declared, _TAGS[1])),
    )


#: Pairs of unregistered names over the same spelling axes as the keys.
_UNREGISTERED_NAME_PAIRS = (
    ("gateway.caller_marker_a", "gateway.caller_marker_bbbbbbb"),
    ("GATEWAY.HEALTH", "Gateway.Health"),
    ("_gateway.health", "gateway.health\u200b"),
    ("gateway.h\u0435alth", "gateway.he\u0430lth"),
    ("gateway.run_correlation_id", "gateway.tool_id"),
)


@pytest.mark.asyncio
@pytest.mark.parametrize("position", ["top-level", "nested-in-declared"])
@pytest.mark.parametrize(
    ("tool_name", "policy"),
    [
        *((name, "blocked") for name in sorted(_gateway_tools_by_name())),
        ("<unregistered>", "blocked"),
        ("<unregistered>", "allowed"),
    ],
)
async def test_generated_caller_values_never_reach_an_ungated_record(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    tool_name: str,
    position: str,
    policy: str,
) -> None:
    """Blocked tools, and unregistered names blocked or allowed: nothing met a
    schema, so every argument -- declared keys included -- and the name are
    the caller's alone."""
    caplog.set_level(logging.DEBUG)
    tools = _gateway_tools_by_name()
    tool = tools.get(tool_name)
    declared = set(tool.input_schema.get("properties") or {}) if tool else set()
    host = sorted(declared)[0] if declared else "arguments"
    server, audit_path = _scoped_server(tmp_path)
    server._policy_manager.is_gateway_tool_allowed = (  # type: ignore[method-assign]
        lambda name: policy == "allowed"
    )
    names = _UNREGISTERED_NAME_PAIRS if tool is None else ((tool_name, tool_name),)
    calls = _LoggedCalls(server, caplog)
    for shape in _SHAPES:
        for pair in names:
            payloads = []
            for name, tag in zip(pair, _TAGS):
                keys = _generated_keys(declared, tag) + sorted(declared)
                generated = _generated(keys, shape, tag)
                arguments = generated if position == "top-level" else {host: generated}
                payloads.append((name, arguments))
            await calls.pair(f"{pair} {shape}", payloads)
    await server.shutdown()

    records = _invocations(audit_path, "audit.invocation")
    assert len(records) == len(_TAGS) * len(_SHAPES) * len(names)
    # Nothing in any record depends on the call: all are equal.
    assert all(record == records[0] for record in records), tool_name
    for field in (*_RECORD_READ_NAMES, "downstream_tool_id", "source_reference_hash"):
        assert records[0].get(field) is None, field
    _assert_nothing_generated_leaked(
        audit_path.read_text(),
        _log_text(caplog),
        len(_generated_keys(declared, _TAGS[1])) + len(declared),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool_name",
    ["gateway.health", "gateway.catalog_search", "gateway.describe", "gateway.invoke"],
)
async def test_generated_caller_values_on_the_real_scoped_handlers(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, tool_name: str
) -> None:
    """The allowed-path sweep once more, against the real `GatewayTools` under
    the real scoped policy (no stubs, no downstream servers), for the four
    tools that policy allows: generated keys at top level, every shape, each
    pair of calls compared on records and on logs, and each record against
    the declared arguments alone (review panel on Consiliency/pmcp#304)."""
    caplog.set_level(logging.DEBUG)
    tool = _gateway_tools_by_name()[tool_name]
    declared = set(tool.input_schema.get("properties") or {})
    baseline = _declared_baseline(tool)
    exempt = _INVOKE_ONLY_EXEMPT if tool_name == "gateway.invoke" else frozenset()
    server, audit_path = _scoped_server(tmp_path)
    calls = _LoggedCalls(server, caplog)
    await _call(server, tool_name, baseline)
    for shape in _SHAPES:
        await calls.pair(
            f"real {shape}",
            [
                (
                    tool_name,
                    {
                        **baseline,
                        **_generated(_generated_keys(declared, tag), shape, tag),
                    },
                )
                for tag in _TAGS
            ],
        )
    await server.shutdown()

    records = _invocations(audit_path, "audit.invocation")
    assert len(records) == 1 + len(_TAGS) * len(_SHAPES)
    reference, generated = records[0], records[1:]
    for shape, first, second in zip(_SHAPES, generated[::2], generated[1::2]):
        _assert_pair(tool_name, f"real {shape}", first, second, frozenset())
        _assert_pair(tool_name, f"real {shape}", first, reference, exempt)
    _assert_nothing_generated_leaked(
        audit_path.read_text(),
        _log_text(caplog),
        len(_generated_keys(declared, _TAGS[1])),
    )
