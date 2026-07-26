from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from mcp.types import CallToolRequest, ListToolsRequest
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
            }
        )
    )
    return path


def _correlations() -> dict[str, str]:
    return {
        "run_correlation_id": "run-103",
        "seat_correlation_id": "seat-codex",
        "evidence_label_digest": "a" * 64,
    }


def test_explicit_policy_failures_are_fatal_but_default_discovery_is_best_effort(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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
    policy = PolicyManager(Path("examples/scoped-advisor-policy.yaml"))
    assert policy.is_scoped_advisor_policy() is True
    assert policy.is_gateway_tool_allowed("gateway.invoke") is True
    assert policy.is_gateway_tool_allowed("Gateway.invoke") is False
    assert policy.is_gateway_tool_allowed("gateway.provision") is False
    assert policy.is_tool_allowed("firecrawl::web_search") is True
    assert policy.is_tool_allowed("brightdata::scrape_page") is True
    assert policy.is_tool_allowed("github::create_issue") is False


def test_invoke_correlations_are_typed_and_atomic() -> None:
    with pytest.raises(ValidationError, match="supplied together"):
        InvokeInput(tool_id="firecrawl::search", run_correlation_id="run-only")
    with pytest.raises(ValidationError):
        InvokeInput(
            tool_id="firecrawl::search",
            **{**_correlations(), "evidence_label_digest": "not-a-digest"},
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
    list_handler = server._server.request_handlers[ListToolsRequest]
    call_handler = server._server.request_handlers[CallToolRequest]

    listed = await list_handler(ListToolsRequest(params={}))
    assert {tool.name for tool in listed.root.tools} == {
        "gateway.health",
        "gateway.catalog_search",
        "gateway.describe",
        "gateway.invoke",
    }

    invoked = await call_handler(
        CallToolRequest(
            params={
                "name": "gateway.invoke",
                "arguments": {
                    "tool_id": "firecrawl::web_search",
                    "arguments": {
                        "url": "https://example.com/private/path?token=secret",
                        "query": "super secret query",
                        "credential": "sk-private-value",
                    },
                    **_correlations(),
                },
            }
        )
    )
    assert json.loads(invoked.root.content[0].text)["ok"] is True

    brightdata = await call_handler(
        CallToolRequest(
            params={
                "name": "gateway.invoke",
                "arguments": {
                    "tool_id": "brightdata::scrape_page",
                    "arguments": {"query": "current benchmark evidence"},
                    **{**_correlations(), "seat_correlation_id": "seat-gemini"},
                },
            }
        )
    )
    assert json.loads(brightdata.root.content[0].text)["ok"] is True

    downstream_denied = await call_handler(
        CallToolRequest(
            params={
                "name": "gateway.invoke",
                "arguments": {
                    "tool_id": "github::create_issue",
                    "arguments": {"title": "must not mutate"},
                    **_correlations(),
                },
            }
        )
    )
    denied_payload = json.loads(downstream_denied.root.content[0].text)
    assert denied_payload["ok"] is False
    assert denied_payload["auth_state"] == "policy_denied"

    async def fail_lazy_start(tool_id: str) -> bool:
        raise AssertionError(f"policy denial attempted lazy start for {tool_id}")

    server._gateway_tools._ensure_server_for_tool = fail_lazy_start  # type: ignore[method-assign]
    lazy_denied = await call_handler(
        CallToolRequest(
            params={
                "name": "gateway.invoke",
                "arguments": {
                    "tool_id": "github::unregistered_mutation",
                    "arguments": {},
                    **_correlations(),
                },
            }
        )
    )
    assert json.loads(lazy_denied.root.content[0].text)["auth_state"] == (
        "policy_denied"
    )

    denied = await call_handler(
        CallToolRequest(
            params={
                "name": "gateway.provision",
                "arguments": {
                    "tool_id": "raw credential value",
                    "run_correlation_id": "secret query with spaces",
                    "seat_correlation_id": "seat secret with spaces",
                    "evidence_label_digest": "not-a-digest-secret",
                },
            }
        )
    )
    assert "blocked by policy" in json.loads(denied.root.content[0].text)["message"]

    health = await server._gateway_tools.health()
    assert health.gateway_diagnostics.capabilities == [SCOPED_ADVISOR_AUDIT_CAPABILITY]
    await server.shutdown()

    records = validate_scoped_advisor_audit(audit_path)
    invocations = [r for r in records if r["event"] == "audit.invocation"]
    assert [record["terminal_status"] for record in invocations] == [
        "success",
        "success",
        "denied",
        "denied",
        "denied",
    ]
    assert invocations[0]["run_correlation_id"] == "run-103"
    assert invocations[0]["seat_correlation_id"] == "seat-codex"
    assert invocations[0]["source_reference_hash"]
    assert records[-1]["record_count"] == len(records)
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
    ):
        assert forbidden not in raw_audit


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
    handler = server._server.request_handlers[CallToolRequest]
    result = await handler(
        CallToolRequest(
            params={
                "name": "gateway.invoke",
                "arguments": {"tool_id": "firecrawl::web_search"},
            }
        )
    )
    assert called is False
    assert json.loads(result.root.content[0].text)["error"] is True
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
