"""Privacy-safe durable audit for scoped advisor research sessions."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any, TextIO
from urllib.parse import urlsplit, urlunsplit

SCOPED_ADVISOR_AUDIT_CAPABILITY = "scoped_advisor_audit.v1"
_CORRELATION_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_TOOL_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+::[A-Za-z0-9_.-]{1,192}$")


class ScopedAdvisorAuditError(RuntimeError):
    """Raised when the trusted audit channel cannot be completed."""


def validate_scoped_advisor_audit(path: Path) -> list[dict[str, Any]]:
    """Validate contiguous records and the single terminal completeness marker."""
    try:
        records = [
            json.loads(line)
            for line in Path(path).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except Exception as exc:
        raise ScopedAdvisorAuditError("scoped advisor audit is unreadable") from exc
    if not records:
        raise ScopedAdvisorAuditError("scoped advisor audit is empty")
    starts = [record for record in records if record.get("event") == "audit.started"]
    if len(starts) != 1 or records[0] is not starts[0]:
        raise ScopedAdvisorAuditError(
            "scoped advisor audit start is missing or duplicated"
        )
    if [record.get("sequence") for record in records] != list(
        range(1, len(records) + 1)
    ):
        raise ScopedAdvisorAuditError("scoped advisor audit sequence gap")
    completions = [
        record for record in records if record.get("event") == "audit.completed"
    ]
    if len(completions) != 1 or records[-1] is not completions[0]:
        raise ScopedAdvisorAuditError(
            "scoped advisor audit completion is missing or duplicated"
        )
    terminal = completions[0]
    if (
        terminal.get("first_sequence") != 1
        or terminal.get("last_sequence") != len(records)
        or terminal.get("record_count") != len(records)
    ):
        raise ScopedAdvisorAuditError("scoped advisor audit completion bounds mismatch")
    session_ids = {record.get("audit_session_id") for record in records}
    policy_digests = {record.get("policy_digest") for record in records}
    if len(session_ids) != 1 or None in session_ids:
        raise ScopedAdvisorAuditError("scoped advisor audit session mismatch")
    if len(policy_digests) != 1 or None in policy_digests:
        raise ScopedAdvisorAuditError("scoped advisor audit policy mismatch")
    return records


def _digest(value: Any) -> str:
    try:
        payload = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            default=str,
        )
    except Exception:
        payload = type(value).__name__
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _public_source_hash(value: Any) -> str | None:
    """Hash the first public HTTP(S) source without retaining its raw value."""
    candidates: list[str] = []

    def collect(candidate: Any) -> None:
        if isinstance(candidate, str):
            candidates.extend(re.findall(r"https?://[^\s\"'<>]+", candidate))
        elif isinstance(candidate, dict):
            for child in candidate.values():
                collect(child)
        elif isinstance(candidate, list):
            for child in candidate:
                collect(child)

    collect(value)
    for candidate in candidates:
        try:
            parsed = urlsplit(candidate)
            hostname = (parsed.hostname or "").lower()
            if (
                not hostname
                or parsed.username
                or parsed.password
                or hostname == "localhost"
            ):
                continue
            try:
                if not ipaddress.ip_address(hostname).is_global:
                    continue
            except ValueError:
                if "." not in hostname:
                    continue
            port = f":{parsed.port}" if parsed.port else ""
            normalized = urlunsplit(
                (parsed.scheme.lower(), f"{hostname}{port}", parsed.path or "/", "", "")
            )
            return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        except (TypeError, ValueError):
            continue
    return None


class ScopedAdvisorAudit:
    """Append-only JSONL writer with a single fsynced terminal marker."""

    def __init__(self, path: Path, *, policy_digest: str) -> None:
        self.path = Path(path)
        self.policy_digest = policy_digest
        self.audit_session_id = uuid.uuid4().hex
        self._sequence = 0
        self._completed = False
        self._failed = False
        self._file: TextIO | None = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._file = self.path.open("a", encoding="utf-8")
            self._write(
                {
                    "event": "audit.started",
                    "schema": SCOPED_ADVISOR_AUDIT_CAPABILITY,
                    "audit_session_id": self.audit_session_id,
                    "timestamp": time.time(),
                    "policy_digest": self.policy_digest,
                }
            )
        except Exception as exc:
            self._close_quietly()
            raise ScopedAdvisorAuditError(
                "scoped advisor audit sink unavailable"
            ) from exc

    def _write(self, record: dict[str, Any], *, terminal: bool = False) -> None:
        if self._file is None or self._completed or self._failed:
            raise ScopedAdvisorAuditError("scoped advisor audit sink is closed")
        self._sequence += 1
        record = {"sequence": self._sequence, **record}
        try:
            self._file.write(json.dumps(record, sort_keys=True, separators=(",", ":")))
            self._file.write("\n")
            self._file.flush()
            if terminal:
                os.fsync(self._file.fileno())
        except Exception as exc:
            self._failed = True
            self._close_quietly()
            raise ScopedAdvisorAuditError("scoped advisor audit write failed") from exc

    def record_invocation(
        self,
        *,
        gateway_tool: str,
        terminal_status: str,
        arguments: dict[str, Any] | None,
        result: Any,
    ) -> None:
        arguments = arguments or {}
        downstream_tool_id = arguments.get("tool_id")
        run_correlation_id = arguments.get("run_correlation_id")
        seat_correlation_id = arguments.get("seat_correlation_id")
        evidence_label_digest = arguments.get("evidence_label_digest")
        self._write(
            {
                "event": "audit.invocation",
                "schema": SCOPED_ADVISOR_AUDIT_CAPABILITY,
                "audit_session_id": self.audit_session_id,
                "timestamp": time.time(),
                "policy_digest": self.policy_digest,
                "run_correlation_id": run_correlation_id
                if isinstance(run_correlation_id, str)
                and _CORRELATION_PATTERN.fullmatch(run_correlation_id)
                else None,
                "seat_correlation_id": seat_correlation_id
                if isinstance(seat_correlation_id, str)
                and _CORRELATION_PATTERN.fullmatch(seat_correlation_id)
                else None,
                "gateway_tool": gateway_tool,
                "downstream_tool_id": downstream_tool_id
                if isinstance(downstream_tool_id, str)
                and _TOOL_ID_PATTERN.fullmatch(downstream_tool_id)
                else None,
                "terminal_status": terminal_status,
                "redacted_result_digest": _digest(result),
                "source_reference_hash": _public_source_hash(result)
                or _public_source_hash(arguments),
                "evidence_label_digest": evidence_label_digest
                if isinstance(evidence_label_digest, str)
                and _DIGEST_PATTERN.fullmatch(evidence_label_digest)
                else None,
            }
        )

    def complete(self) -> None:
        if self._completed:
            return
        terminal_sequence = self._sequence + 1
        self._write(
            {
                "event": "audit.completed",
                "schema": SCOPED_ADVISOR_AUDIT_CAPABILITY,
                "audit_session_id": self.audit_session_id,
                "timestamp": time.time(),
                "policy_digest": self.policy_digest,
                "first_sequence": 1,
                "last_sequence": terminal_sequence,
                "record_count": terminal_sequence,
            },
            terminal=True,
        )
        self._completed = True
        self._close_quietly()

    def _close_quietly(self) -> None:
        if self._file is not None:
            try:
                self._file.close()
            except Exception:
                pass
            self._file = None
