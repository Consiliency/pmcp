"""A rejected gateway-tool argument never echoes its value (Consiliency/pmcp#297).

The oracle is a generated sweep, not hand-picked cases. Its axes come from the
code:

- every registered gateway tool (``GATEWAY_TOOL_INPUT_MODELS``);
- every position its advertised schema declares -- nested object properties
  and list items included -- and, at each, every invalid shape that schema
  node rejects (wrong type, pattern, length, enum);
- every custom validator on every argument model the tool parses with, found
  by introspection (a new validator without a recipe fails the sweep);
- every field an argument model also accepts by name under
  ``populate_by_name`` (``InvokeInput.meta``), which the gate never sees;
- every validation-error type (pydantic and jsonschema) raised from inside a
  handler.

Each case carries one sentinel in every position the caller controls around
the invalid value -- the value itself, dict keys, list items, extra keys on
every object on the path, and every open container (``invoke.arguments``,
``_meta``, ``task.metadata``, ``requestor_context``). The sweep asserts that
each case was rejected by argument validation (no vacuous pass), that the
rejection names the failing field, and that the sentinel -- and its hashes --
reaches neither the response, the log (every record at DEBUG, rendered by
pmcp's own text and JSON formatters, tracebacks included) nor the scoped
audit. It also runs every case twice with sentinels of different length and
requires the response, log and audit to be identical, so no length, count or
hash of the value is disclosed either.
"""

from __future__ import annotations

import copy
import functools
from unittest import mock
import hashlib
import json
import re
import logging
import traceback
import typing
from pathlib import Path
from typing import Any

import jsonschema
import pytest
from mcp.types import CallToolRequestParams
from pydantic import BaseModel, ValidationError, field_validator
from pydantic_core import PydanticCustomError

from pmcp.server import GatewayServer
from pmcp.tools.handlers import GATEWAY_TOOL_INPUT_MODELS, get_gateway_tool_definitions
from pmcp.types import InvokeInput, McpTaskInfo
from tests.test_scoped_advisor_audit import (
    _correlations,
    _make_ctx,
    _normalized,
    _pmcp_formatters,
    _stable,
    _write_scoped_policy,
)


def _digest(seed: str, length: int, alphabet: str = "0123456789abcdef") -> str:
    """`length` deterministic high-entropy characters of `alphabet`."""
    out, counter = "", 0
    while len(out) < length:
        block = hashlib.sha256(f"pmcp-297:{seed}:{counter}".encode()).digest()
        out += "".join(alphabet[b % len(alphabet)] for b in block)
        counter += 1
    return out[:length]


_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"

#: Sentinel families, two lengths each. A leak can be conditional on the
#: value's shape (the rev 1 board's mutant S8 echoed only `isalpha()`
#: values), so the sweep runs every case once per family: hex, letters only,
#: a provider-token shape, a value with spaces, non-ASCII (written as `\u`
#: escapes: the test source stays ASCII), and digits only.
_FAMILIES: dict[str, tuple[str, str]] = {
    "hex": (
        "Sq" + _digest("hex-a", 22) + "Zx",
        "Sq" + _digest("hex-b", 38) + "Zx",
    ),
    "alpha": (_digest("alpha-a", 26, _LETTERS), _digest("alpha-b", 42, _LETTERS)),
    "token": ("sk-proj-" + _digest("token-a", 24), "sk-proj-" + _digest("token-b", 40)),
    "spaced": (
        "Bearer " + " ".join(_digest("sp-a", 24)[i : i + 6] for i in range(0, 24, 6)),
        "Bearer " + " ".join(_digest("sp-b", 42)[i : i + 6] for i in range(0, 42, 6)),
    ),
    "unicode": (
        "\u00e9\u4e2d" + _digest("uni-a", 24) + "\u00fc",
        "\u00e9\u4e2d" + _digest("uni-b", 40) + "\u00fc",
    ),
    "digits": (_digest("dig-a", 26, "0123456789"), _digest("dig-b", 42, "0123456789")),
}
#: The hex pair, for the tests that need one family.
_SENTINELS = _FAMILIES["hex"]

_GATE_PREFIX = "Input validation error: $"
_MODEL_PREFIX = "Invalid arguments: $"


#: pydantic truncates a long `input_value` repr in the middle
#: (`'Sq3b2ee216cab4bcafa8599...e216cab4bcafa85997Zx'`), so the whole sentinel
#: never appears in such a leak; every window of this many characters is
#: searched for instead (48 bits of hex: no accidental match).
_WINDOW = 12


def _forbidden(sentinel: str) -> set[str]:
    """Every window of the sentinel -- raw, JSON-escaped, `repr`-escaped and
    `unicode_escape`d -- and each hash of it, a log or record could carry."""
    forms: set[str] = set()
    for spelling in {
        sentinel,
        json.dumps(sentinel)[1:-1],
        repr(sentinel)[1:-1],
        sentinel.encode("unicode_escape").decode("ascii"),
    }:
        forms |= {spelling[i : i + _WINDOW] for i in range(len(spelling) - _WINDOW + 1)}
    for text in (sentinel, json.dumps(sentinel), sentinel.lower()):
        forms.add(hashlib.sha256(text.encode()).hexdigest())
        forms.add(hashlib.sha1(text.encode()).hexdigest())
        forms.add(hashlib.md5(text.encode()).hexdigest())
    return forms


# --- the generator -------------------------------------------------------------


def _tools() -> dict[str, Any]:
    return {tool.name: tool for tool in get_gateway_tool_definitions()}


def _models() -> dict[str, type[BaseModel] | None]:
    return dict(GATEWAY_TOOL_INPUT_MODELS)


def _types(node: dict[str, Any]) -> list[str]:
    declared = node.get("type")
    if declared is None:
        return []
    return declared if isinstance(declared, list) else [declared]


def _positions(
    node: dict[str, Any], path: tuple[str | int, ...] = ()
) -> list[tuple[tuple[str | int, ...], dict[str, Any]]]:
    """Every declared position below the root, depth-first."""
    found: list[tuple[tuple[str | int, ...], dict[str, Any]]] = []
    for key, child in sorted((node.get("properties") or {}).items()):
        found.append(((*path, key), child))
        found.extend(_positions(child, (*path, key)))
    items = node.get("items")
    if isinstance(items, dict):
        found.append(((*path, 0), items))
        found.extend(_positions(items, (*path, 0)))
    return found


def _invalid_values(node: dict[str, Any], s: str) -> list[tuple[str, Any]]:
    """Values this schema node rejects, each carrying `s` in every slot."""
    return [
        (label, value)
        for label, value in _candidate_values(node, s)
        if not jsonschema.validators.validator_for(node)(node).is_valid(value)
    ]


def _candidate_values(node: dict[str, Any], s: str) -> list[tuple[str, Any]]:
    """The shapes `_invalid_values` tries (unchecked: a case's build reuses
    the shape its construction already checked)."""
    types = _types(node)
    candidates: list[tuple[str, Any]] = []
    if types and "string" not in types:
        candidates.append(("type-string", s))
    if types and "object" not in types:
        candidates.append(("type-object", {s: s, "k": [s, {s: s}]}))
    if types and "array" not in types:
        candidates.append(("type-array", [s, {s: s}, [s]]))
    if "string" in types:
        if "pattern" in node:
            length = max(node.get("minLength", 0), len(s) + 1)
            candidates.append(("pattern", ("!" + s * 8)[:length]))
        if "maxLength" in node:
            candidates.append(("maxLength", s * (node["maxLength"] // len(s) + 1)))
        if "enum" in node:
            candidates.append(("enum", s))
    return candidates


def _open_containers(schema: dict[str, Any]) -> list[tuple[str | int, ...]]:
    """Declared object positions that accept any key."""
    return [
        path
        for path, node in _positions(schema)
        if "object" in _types(node)
        and node.get("additionalProperties", True) is not False
        and not node.get("properties")
    ]


def _open_content(s: str) -> dict[str, Any]:
    return {s: s, "nested": {s: [s, {s: s}]}, "list": [s, 7, {s: None}]}


def _baseline(tool: Any) -> dict[str, Any]:
    """The smallest arguments the tool's schema accepts, from its schema."""
    return copy.deepcopy(_checked_baseline(tool.name))


@functools.cache
def _checked_baseline(name: str) -> dict[str, Any]:
    tool = _tools()[name]
    schema = tool.input_schema
    baseline: dict[str, Any] = {}
    for name in schema.get("required") or []:
        prop = schema["properties"][name]
        baseline[name] = (
            prop["enum"][0]
            if "enum" in prop
            else "x" * max(prop.get("minLength", 1), 1)
        )
    if tool.name == "gateway.invoke":
        baseline["tool_id"] = "srv::tool"
        baseline.update(_correlations())
    jsonschema.validate(baseline, schema)
    return baseline


def _set(
    arguments: dict[str, Any], path: tuple[str | int, ...], value: Any, s: str
) -> None:
    """Put `value` at `path`, creating containers, and give every object on
    the way an extra key carrying `s`."""
    node: Any = arguments
    for segment, following in zip(path, path[1:]):
        existing = node[segment] if isinstance(node, dict) and segment in node else None
        if isinstance(following, int):
            child: Any = existing if isinstance(existing, list) else []
            while len(child) <= following:
                child.append(None)
        else:
            child = existing if isinstance(existing, dict) else {}
            child[f"extra_{s}"] = s
        if isinstance(node, list):
            node[segment] = child  # type: ignore[index]
        else:
            node[segment] = child
        node = child
    node[path[-1]] = value


def _decorated(tool: Any, s: str) -> dict[str, Any]:
    """The baseline with `s` in an extra top-level key and in every open
    container the schema declares."""
    arguments = copy.deepcopy(_baseline(tool))
    arguments[f"extra_{s}"] = {s: [s]}
    for path in _open_containers(tool.input_schema):
        _set(arguments, path, _open_content(s), s)
    return arguments


@pytest.mark.parametrize("name", sorted(_tools()))
def test_every_decorated_baseline_passes_the_gate(name: str) -> None:
    """Decorations alone are accepted, so a case's rejection is its own."""
    tool = _tools()[name]
    for sentinels in _FAMILIES.values():
        for s in sentinels:
            jsonschema.validate(_decorated(tool, s), tool.input_schema)


def _expected_path(path: tuple[str | int, ...]) -> str:
    rendered = "$"
    for segment in path:
        rendered += f"[{segment}]" if isinstance(segment, int) else f".{segment}"
    return rendered


def _reachable_models(model: type[BaseModel] | None) -> list[type[BaseModel]]:
    """`model` and every model nested in its fields' annotations."""
    seen: list[type[BaseModel]] = []
    pending: list[Any] = [model] if model is not None else []
    while pending:
        current = pending.pop()
        if isinstance(current, type) and issubclass(current, BaseModel):
            if current in seen:
                continue
            seen.append(current)
            pending.extend(f.annotation for f in current.model_fields.values())
        else:
            pending.extend(typing.get_args(current))
    return seen


#: How to fail each custom validator on an argument model, as top-level
#: arguments merged over the decorated baseline. Keyed by (model, validator,
#: field); the sweep asserts this table equals what introspection finds.
_VALIDATOR_RECIPES: dict[tuple[str, str, str], Any] = {
    ("InvokeInput", "_validate_correlation_id", "run_correlation_id"): (
        lambda s: {"run_correlation_id": s + "!"},
        "$.run_correlation_id: correlation IDs may contain only",
    ),
    ("InvokeInput", "_validate_correlation_id", "seat_correlation_id"): (
        lambda s: {"seat_correlation_id": s + "!"},
        "$.seat_correlation_id: correlation IDs may contain only",
    ),
    ("InvokeInput", "_reject_partial_scoped_correlation", ""): (
        lambda s: {
            "run_correlation_id": s,
            "seat_correlation_id": None,
            "evidence_label_digest": None,
        },
        "$: scoped advisor correlation fields must be supplied together",
    ),
    ("RegisterDiscoveredServerInput", "_validate_package", "package"): (
        lambda s: {"package": "-" + s},
        "$.package: package must be a valid npm/pypi identifier",
    ),
}


def _validators(model: type[BaseModel]) -> list[tuple[str, str, str]]:
    decorators = model.__pydantic_decorators__
    found = [
        (model.__name__, name, field)
        for name, decorator in decorators.field_validators.items()
        for field in decorator.info.fields
    ]
    found += [(model.__name__, name, "") for name in decorators.model_validators]
    return found


class _Case(typing.NamedTuple):
    tool: str
    label: str
    build: Any  # sentinel -> arguments
    layer: str  # "gate" or "model"
    expected: str  # the rendered location (and phrase) the rejection must name


def _cases() -> list[_Case]:
    tools, models = _tools(), _models()
    cases: list[_Case] = []
    discovered: set[tuple[str, str, str]] = set()
    for name in sorted(tools):
        tool = tools[name]
        for path, node in _positions(tool.input_schema):
            # No silent shrink: every typed position yields a case.
            assert not _types(node) or _invalid_values(node, _SENTINELS[0]), (
                name,
                path,
            )
            for label, _ in _invalid_values(node, _SENTINELS[0]):

                def build(s: str, tool=tool, path=path, node=node, label=label) -> dict:
                    arguments = _decorated(tool, s)
                    value = dict(_candidate_values(node, s))[label]
                    _set(arguments, path, value, s)
                    return arguments

                cases.append(
                    _Case(
                        name,
                        f"{_expected_path(path)}:{label}",
                        build,
                        "gate",
                        _expected_path(path) + ": ",
                    )
                )
        # A missing required key, beside the caller's own extra keys: the
        # description must name the schema's key, never one of the caller's.
        assert not [p for p, n in _positions(tool.input_schema) if n.get("required")], (
            "a nested `required` needs this axis extended",
            name,
        )
        for required in tool.input_schema.get("required") or []:

            def build(s: str, tool=tool, required=required) -> dict:
                arguments = _decorated(tool, s)
                del arguments[required]
                return arguments

            cases.append(
                _Case(
                    name,
                    f"required:{required}",
                    build,
                    "gate",
                    f"$.{required}: is required",
                )
            )
        for model in _reachable_models(models[name]):
            for key in _validators(model):
                discovered.add(key)
                recipe, expected = _VALIDATOR_RECIPES[key]

                def build(s: str, tool=tool, recipe=recipe) -> dict:
                    return {**_decorated(tool, s), **recipe(s)}

                cases.append(_Case(name, f"validator:{key}", build, "model", expected))
            if model.model_config.get("populate_by_name") and model is models[name]:
                for field_name, field in model.model_fields.items():
                    if not isinstance(field.alias, str) or field.alias == field_name:
                        continue
                    node = tool.input_schema["properties"][field.alias]
                    for label, _ in _invalid_values(node, _SENTINELS[0]):

                        def build(
                            s: str,
                            tool=tool,
                            node=node,
                            label=label,
                            field_name=field_name,
                            alias=field.alias,
                        ) -> dict:
                            arguments = _decorated(tool, s)
                            # By name only: with the alias present too,
                            # pydantic reads the alias.
                            assert arguments.pop(alias, None) is not None
                            arguments[field_name] = dict(_candidate_values(node, s))[
                                label
                            ]
                            return arguments

                        cases.append(
                            _Case(
                                name,
                                f"by-name:{field_name}:{label}",
                                build,
                                "model",
                                f"$.{field_name}: ",
                            )
                        )
    assert discovered == set(_VALIDATOR_RECIPES), (
        "every custom validator on an argument model needs a recipe, and "
        "every recipe a validator",
        discovered ^ set(_VALIDATOR_RECIPES),
    )
    return cases


_CASES = _cases()


def test_the_sweep_covers_every_tool_that_can_reject_an_argument() -> None:
    """No vacuous pass: every registered tool that declares an argument
    contributes cases, and the ones that do not declare none at all."""
    tools = _tools()
    covered = {case.tool for case in _CASES}
    for name, tool in tools.items():
        if tool.input_schema.get("properties"):
            assert name in covered, name
        else:
            assert name not in covered, name
    assert {case.layer for case in _CASES} == {"gate", "model"}
    assert len(_CASES) > 100, len(_CASES)


# --- running a case --------------------------------------------------------------


def _server(tmp_path: Path, *, audited: bool) -> tuple[GatewayServer, Path | None]:
    audit_path = tmp_path / "audit.jsonl" if audited else None
    if audited:
        server = GatewayServer(
            policy_path=_write_scoped_policy(tmp_path / "policy.json"),
            audit_jsonl=audit_path,
            cache_dir=tmp_path / "cache",
        )
    else:
        policy = tmp_path / "policy.json"
        policy.write_text("{}")
        server = GatewayServer(policy_path=policy, cache_dir=tmp_path / "cache")
    server._create_server()
    # Every registered tool reaches its real handler.
    server._policy_manager.is_gateway_tool_allowed = (  # type: ignore[method-assign]
        lambda name: True
    )
    return server, audit_path


async def _call(server: GatewayServer, name: str, arguments: dict) -> Any:
    assert server._server is not None
    entry = server._server.get_request_handler("tools/call")
    assert entry is not None
    return await entry.handler(
        _make_ctx(), CallToolRequestParams(name=name, arguments=arguments)
    )


def _rejection(result: Any, layer: str) -> str:
    """The rejection text, asserting the call was rejected by argument
    validation on `layer` (an `isError` gate result, or the `{"error": true}`
    payload of `call_tool`'s `except` arm) and not, say, accepted."""
    text = "".join(block.text for block in result.content)
    if layer == "gate":
        assert result.is_error is True, text
        return text
    payload = json.loads(text)
    assert payload.get("error") is True, text
    return str(payload["message"])


def _useful(response: str, layer: str, expected: str) -> None:
    """The rejection says where, and why, in the value-free form."""
    prefix = _GATE_PREFIX if layer == "gate" else _MODEL_PREFIX
    assert response.startswith(prefix), response
    assert expected in response, response


#: The attributes every `LogRecord` has; anything else came in via `extra=`.
_STANDARD_RECORD_KEYS = frozenset(
    logging.LogRecord("x", logging.INFO, "x", 1, "x", None, None).__dict__
) | {"message", "asctime"}


def _record_text(record: logging.LogRecord) -> str:
    """Everything a log handler could see in `record`, whatever formats it:
    pmcp's text and JSON formatters, the raw message and args, every
    attribute (`extra=` fields included), and the traceback of `exc_info`
    and `stack_info` rendered in full (pmcp's JSON formatter drops them)."""
    parts = [formatter.format(record) for formatter in _pmcp_formatters()]
    parts.append(repr(record.msg))
    parts.append(repr(record.args))
    parts.append(record.getMessage())
    for key, value in sorted(record.__dict__.items()):
        if key == "exc_info" and value and value[1] is not None:
            parts.append("".join(traceback.format_exception(*value)))
        elif key not in ("msg", "args"):
            parts.append(f"{key}={value!r}")
    return "\n".join(parts)


def _record_stable(record: logging.LogRecord) -> str:
    """`record` for the pair differential: both formatters at a fixed time,
    plus every `extra=` attribute."""
    extras = {
        key: repr(value)
        for key, value in sorted(record.__dict__.items())
        if key not in _STANDARD_RECORD_KEYS
    }
    rendered = [_normalized(record, formatter) for formatter in _pmcp_formatters()]
    text = "\n".join(rendered) + (f"\nextra={extras!r}" if extras else "")
    # The one volatile token a downstream call logs: its wall-clock latency.
    return _ELAPSED.sub("elapsed_ms=N", text)


#: Exactly `elapsed_ms=<digits>` in `handlers.py`'s `tool_call` line.
_ELAPSED = re.compile(r"\belapsed_ms=[0-9]+\b")


class _Tap:
    """Every channel a call can leak into: the response (given), the log
    (`caplog` at DEBUG, root), stdout/stderr (`capfd`), warnings
    (`recwarn`), the gateway's in-memory audit-event buffer (what
    `gateway.health` exposes), and the scoped-audit JSONL."""

    def __init__(
        self,
        server: GatewayServer,
        audit_path: Path | None,
        caplog: pytest.LogCaptureFixture,
        capfd: pytest.CaptureFixture[str],
        recwarn: pytest.WarningsRecorder,
    ) -> None:
        self.server, self.audit_path = server, audit_path
        self.caplog, self.capfd, self.recwarn = caplog, capfd, recwarn

    def _audit_text(self) -> str:
        path = self.audit_path
        return path.read_text() if path is not None and path.exists() else ""

    def _buffer(self) -> list[str]:
        events = getattr(self.server._gateway_tools, "__dict__", {}).get(
            "_audit_events", []
        )
        return [event.model_dump_json() for event in events]

    def start(self) -> tuple[int, int, str, list[str]]:
        self.capfd.readouterr()
        return (
            len(self.caplog.records),
            len(self.recwarn),
            self._audit_text(),
            self._buffer(),
        )

    def since(self, mark: tuple[int, int, str, list[str]], response: str) -> _Observed:
        records = self.caplog.records[mark[0] :]
        captured = self.capfd.readouterr()
        warned = [
            f"{w.category.__name__}: {w.message} @ {w.filename}:{w.lineno}"
            for w in list(self.recwarn)[mark[1] :]
        ]
        raw_audit = self._audit_text()[len(mark[2]) :]
        buffer = self._buffer()
        new_events = [event for event in buffer if event not in mark[3]]
        return _Observed(
            response=response,
            log="\n".join(_record_stable(record) for record in records),
            raw_log="\n".join(_record_text(record) for record in records),
            streams=captured.out + captured.err,
            warnings="\n".join(warned),
            audit=[
                _stable(json.loads(line)) for line in raw_audit.splitlines() if line
            ],
            raw_audit=raw_audit,
            events="\n".join(new_events),
        )


class _Observed(typing.NamedTuple):
    response: str
    log: str
    raw_log: str
    streams: str
    warnings: str
    audit: list[dict[str, Any]]
    raw_audit: str
    events: str

    def leaks(self, sentinel: str) -> list[str]:
        """The channels that carry `sentinel` in any form."""
        channels = {
            "response": self.response,
            "log": self.raw_log,
            "stdout/stderr": self.streams,
            "warnings": self.warnings,
            "audit": self.raw_audit,
            "audit-event buffer": self.events,
        }
        forms = _forbidden(sentinel)
        return [
            name for name, text in channels.items() if any(f in text for f in forms)
        ]

    def stable(self) -> tuple[Any, ...]:
        """What must be identical for two sentinels of different length."""
        return (self.response, self.log, self.streams, self.warnings, self.audit)


def _event_shape(events: str) -> list[dict[str, Any]]:
    return [
        {
            k: v
            for k, v in json.loads(line).items()
            if k not in ("timestamp", "duration_ms", "latency_ms")
        }
        for line in events.splitlines()
        if line
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("audited", [False, True], ids=["plain", "scoped-audit"])
async def test_no_rejected_argument_value_reaches_a_response_log_or_audit(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    capfd: pytest.CaptureFixture[str],
    recwarn: pytest.WarningsRecorder,
    audited: bool,
) -> None:
    caplog.set_level(logging.DEBUG)
    server, audit_path = _server(tmp_path, audited=audited)
    tap = _Tap(server, audit_path, caplog, capfd, recwarn)
    for family, sentinels in _FAMILIES.items():
        for case in _CASES:
            seen = []
            for sentinel in sentinels:
                mark = tap.start()
                result = await _call(server, case.tool, case.build(sentinel))
                seen.append(tap.since(mark, _rejection(result, case.layer)))
            for sentinel, observed in zip(sentinels, seen):
                assert observed.leaks(sentinel) == [], (family, case.label, observed)
            if family == "hex":
                # Useful: the rejection names where, and why.
                _useful(seen[0].response, case.layer, case.expected)
            # Nothing else about the value -- length, count or hash -- either.
            assert seen[0].stable() == seen[1].stable(), (family, case.label)
            assert _event_shape(seen[0].events) == _event_shape(seen[1].events)
    # The audit oracle is live, not empty by accident.
    if audit_path is not None:
        assert '"audit.rejection"' in audit_path.read_text()
    assert caplog.records, "nothing was logged, so the log oracle proves nothing"


# --- every handler, called past the gate ------------------------------------------


@pytest.mark.asyncio
async def test_every_handler_rejects_what_the_gate_rejects_without_logging_it(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """In-process callers reach a handler without the gate. Each handler must
    then raise the model's `ValidationError` (which the server describes)
    rather than catch it and log or render its text -- the shape
    `provision_status` had, with a traceback, before Consiliency/pmcp#297."""
    caplog.set_level(logging.DEBUG)
    server, _ = _server(tmp_path, audited=False)
    tools = server._gateway_tools
    checked = 0
    for case in _CASES:
        if case.layer != "gate":
            continue
        handler = getattr(tools, case.tool.removeprefix("gateway."))
        for s in _SENTINELS:
            start = len(caplog.records)
            raised: BaseException | None = None
            try:
                await handler(case.build(s))
            except Exception as error:  # noqa: BLE001 - inspected below
                raised = error
            logged = "\n".join(
                formatter.format(record)
                for record in caplog.records[start:]
                for formatter in _pmcp_formatters()
            )
            for form in _forbidden(s):
                assert form not in logged, (case.label, logged)
            assert isinstance(raised, ValidationError), (case.label, raised)
        checked += 1
    assert checked > 100, checked


# --- validation errors raised inside a handler --------------------------------------


def _handler_validation_errors(s: str) -> list[BaseException]:
    """One of each validation-error type a handler can raise, built from a
    value carrying `s`: a pydantic error from a downstream-data model, a
    pydantic error from an argument model, and a jsonschema error."""
    errors: list[BaseException] = []
    for model, data in (
        (McpTaskInfo, {"task_id": {s: s}, "created_at": [s]}),
        (InvokeInput, {"tool_id": {s: s}, "options": s, "_meta": [s]}),
    ):
        try:
            model.model_validate(data)
        except ValidationError as error:
            errors.append(error)
    try:
        jsonschema.validate({"x": {s: s}}, {"properties": {"x": {"type": "string"}}})
    except jsonschema.ValidationError as error:
        errors.append(error)
    assert len(errors) == 3
    for error in errors:
        # the raw text does carry it (pydantic truncates it in the middle)
        assert any(form in str(error) for form in _forbidden(s)), error
    return errors


class _RaisingTools:
    def __init__(self, error: BaseException) -> None:
        self.error = error

    def __getattr__(self, name: str) -> Any:
        async def handler(*args: Any, **kwargs: Any) -> dict:
            raise self.error

        return handler


#: What `exception_text` makes of each of `_handler_validation_errors`.
_HANDLER_ERROR_TEXT = (
    re.compile(
        r"2 validation errors for McpTaskInfo: \$\.task_id: must be a string; \$\.created_at: "
    ),
    re.compile(r"3 validation errors for InvokeInput: \$\.tool_id: must be a string; "),
    # `x` is no name pmcp's models declare, so it reads as `*`.
    re.compile(r"schema validation error: \$\.\*: fails its type constraint$"),
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "index", [0, 1, 2], ids=["downstream-model", "argument-model", "jsonschema"]
)
async def test_a_validation_error_raised_by_a_handler_is_described_not_echoed(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    capfd: pytest.CaptureFixture[str],
    recwarn: pytest.WarningsRecorder,
    index: int,
) -> None:
    """Not the tool's own argument model, so not "Invalid arguments": the
    server's arm renders it with `exception_text`, against no schema (rev 2,
    N6: a jsonschema error names its keyword, never a constraint)."""
    caplog.set_level(logging.DEBUG)
    server, _ = _server(tmp_path, audited=False)
    tap = _Tap(server, None, caplog, capfd, recwarn)
    seen = []
    for family, sentinels in _FAMILIES.items():
        for s in sentinels:
            server._gateway_tools = _RaisingTools(_handler_validation_errors(s)[index])  # type: ignore[assignment]
            mark = tap.start()
            result = await _call(server, "gateway.catalog_search", {"query": "q"})
            payload = json.loads("".join(block.text for block in result.content))
            observed = tap.since(mark, json.dumps(payload))
            assert observed.leaks(s) == [], (family, observed)
            assert payload["error"] is True
            assert _HANDLER_ERROR_TEXT[index].match(payload["message"]), payload
            assert f"Tool execution error: {payload['message']}" in observed.raw_log
            seen.append(observed.stable())
    assert all(item == seen[0] for item in seen[1:])


# --- the renderers, directly ---------------------------------------------------------


class _PoisonedSchemaError(jsonschema.ValidationError):
    """Every attribute that can carry the rejected value raises on access."""

    def _poisoned(self: Any) -> Any:
        raise AssertionError("the renderer read a value-bearing error attribute")

    message = property(_poisoned)  # type: ignore[assignment]
    instance = property(_poisoned)  # type: ignore[assignment]
    validator_value = property(_poisoned)  # type: ignore[assignment]
    context = property(_poisoned)  # type: ignore[assignment]
    cause = property(_poisoned)  # type: ignore[assignment]
    schema = property(_poisoned)  # type: ignore[assignment]


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        # The issue's example, verbatim shape.
        (
            {"tool_id": "a::b", "options": "Bearer sk-SECRET-VALUE"},
            "$.options: must be of type object or null",
        ),
        (
            {"tool_id": "a::b", "evidence_label_digest": "sk-" * 30},
            "$.evidence_label_digest: must be at most 64 characters",
        ),
        (
            {"tool_id": "a::b", "evidence_label_digest": "sk-SECRET".ljust(64, "!")},
            "$.evidence_label_digest: must match the pattern ^[0-9a-f]{64}$",
        ),
        (
            {"tool_id": "a::b", "task": {"ttl": "sk-SECRET"}},
            "$.task.ttl: must be of type integer or null",
        ),
        ({"arguments": {"sk-SECRET": 1}}, "$.tool_id: is required"),
    ],
)
def test_a_schema_rejection_names_the_field_and_the_reason_only(
    arguments: dict, expected: str
) -> None:
    from pmcp.argument_errors import describe_schema_error

    schema = _tools()["gateway.invoke"].input_schema
    error = jsonschema.exceptions.best_match(
        jsonschema.validators.validator_for(schema)(schema).iter_errors(arguments)
    )
    assert error is not None
    error.__class__ = _PoisonedSchemaError
    described = describe_schema_error(error, schema, arguments)
    assert described == expected
    assert "sk-" not in described and "SECRET" not in described


def test_a_caller_chosen_key_in_a_path_is_redacted() -> None:
    from pmcp.argument_errors import describe_schema_error

    schema = {
        "type": "object",
        "properties": {
            "env": {"type": "object", "additionalProperties": {"type": "integer"}},
            "names": {"type": "array", "items": {"type": "string"}},
        },
    }
    for arguments, expected in (
        ({"env": {"sk-KEY-SECRET": "v"}}, "$.env.*: must be of type integer"),
        ({"names": ["ok", {"sk-SECRET": 1}]}, "$.names[1]: must be of type string"),
    ):
        error = jsonschema.exceptions.best_match(
            jsonschema.validators.validator_for(schema)(schema).iter_errors(arguments)
        )
        assert error is not None
        assert describe_schema_error(error, schema, arguments) == expected
    closed = {"type": "object", "properties": {"a": {}}, "additionalProperties": False}
    error = jsonschema.exceptions.best_match(
        jsonschema.validators.validator_for(closed)(closed).iter_errors({"sk-KEY": 1})
    )
    assert error is not None
    assert (
        describe_schema_error(error, closed, {"sk-KEY": 1})
        == "$: has a property that is not accepted"
    )


def test_a_model_rejection_names_the_field_and_the_reason_only() -> None:
    from pmcp.argument_errors import describe_model_error

    schema = _tools()["gateway.invoke"].input_schema
    secret = "sk-" + "S" * 77  # the issue's 80-character secret
    cases = [
        (
            {"tool_id": "a::b", "run_correlation_id": secret + "!"},
            "$.run_correlation_id: correlation IDs may contain only alphanumerics and ._:-",
        ),
        (
            {"tool_id": "a::b", "run_correlation_id": "r", "arguments": {"q": secret}},
            "$: scoped advisor correlation fields must be supplied together",
        ),
        ({"tool_id": "a::b", "meta": secret}, "$.meta: must be an object"),
        (
            {
                "tool_id": "a::b",
                "_meta": {secret: 1},
                "options": {"timeout_ms": secret},
            },
            "$.options.timeout_ms: must be an integer",
        ),
    ]
    for arguments, expected in cases:
        with pytest.raises(ValidationError) as raised:
            InvokeInput.model_validate(arguments)
        assert secret[-21:] in str(raised.value) or secret in str(raised.value)
        described = describe_model_error(raised.value, schema, arguments)
        assert described == expected
        assert "sk-" not in described and "SSS" not in described


def test_a_dict_key_in_a_model_location_is_redacted() -> None:
    from pmcp.argument_errors import describe_model_error

    class _Keyed(BaseModel):
        env: dict[str, int]

    with pytest.raises(ValidationError) as raised:
        _Keyed.model_validate({"env": {"sk-KEY-SECRET": "x"}})
    assert "sk-KEY-SECRET" in str(raised.value)
    described = describe_model_error(
        raised.value, {"properties": {"env": {}}}, {"env": {"sk-KEY-SECRET": "x"}}
    )
    assert described == "$.env.*: must be an integer"


class _CollidingErrors(BaseModel):
    """A validator author reusing pydantic's own error types with the value
    in the very `ctx` keys a naive renderer would fill a phrase from (the rev
    1 board's N2)."""

    literal: str | None = None
    short: str | None = None
    bounded: int | None = None
    nested: dict[str, str] | None = None

    @field_validator("literal")
    @classmethod
    def _literal(cls, value: str) -> str:
        raise PydanticCustomError("literal_error", "{expected}", {"expected": value})

    @field_validator("short")
    @classmethod
    def _short(cls, value: str) -> str:
        raise PydanticCustomError(
            "string_too_short", "{min_length}", {"min_length": value}
        )

    @field_validator("bounded", mode="before")
    @classmethod
    def _bounded(cls, value: Any) -> int:
        raise PydanticCustomError("greater_than", "{gt}", {"gt": value})

    @field_validator("nested")
    @classmethod
    def _nested(cls, value: dict[str, str]) -> dict[str, str]:
        raise PydanticCustomError("missing", "{input}", {"input": value})


@pytest.mark.parametrize("family", sorted(_FAMILIES))
def test_a_custom_error_cannot_fill_a_phrase_from_its_context(family: str) -> None:
    """A constraint is read from the gateway's own schema, never from `ctx`,
    so a colliding custom error renders the schema's constraint -- or none."""
    from pmcp.argument_errors import describe_model_error, exception_text

    s = _FAMILIES[family][1]
    arguments = {"literal": s, "short": s, "bounded": s, "nested": {"k": s}}
    with pytest.raises(ValidationError) as raised:
        _CollidingErrors.model_validate(arguments)
    assert any(form in str(raised.value) for form in _forbidden(s))
    schema = {
        "type": "object",
        "properties": {
            "literal": {"type": "string", "enum": ["a", "b"]},
            "short": {"type": "string", "minLength": 3},
            "bounded": {"type": "integer"},
            "nested": {"type": "object"},
        },
    }
    for rendered, expected in (
        (
            describe_model_error(raised.value, schema, arguments),
            '$.literal: must be one of ["a", "b"]; $.short: must be at least 3 '
            "characters; $.bounded: is too small; $.nested: is required",
        ),
        (
            exception_text(raised.value),
            # No schema here, and a test model's fields are no names pmcp
            # declares, so the locations read as `*`.
            "4 validation errors for _CollidingErrors: $.*: is not an "
            "allowed value; $.*: is too short; $.*: is too small; "
            "$.*: is required",
        ),
    ):
        assert rendered == expected
        assert not any(form in rendered for form in _forbidden(s))


def test_every_constrained_phrase_names_a_schema_keyword() -> None:
    """Each constraint comes from a JSON Schema keyword of the gateway's own
    schema; none from pydantic's `ctx`."""
    import jsonschema.validators

    from pmcp.argument_errors import _CONSTRAINED_PHRASES

    keywords = jsonschema.validators.Draft202012Validator.VALIDATORS
    for error_type, (keyword, with_constraint, without) in _CONSTRAINED_PHRASES.items():
        assert keyword in keywords, error_type
        assert "{" not in without, error_type
        assert with_constraint.count("{") == 1, error_type


def test_exception_text_describes_a_wrapper_that_embeds_a_validation_error() -> None:
    """`RuntimeError(f"... {e}") from e` carries the value in its own text."""
    from pmcp.argument_errors import exception_text, safe_exc_info

    s = _SENTINELS[1]
    try:
        try:
            McpTaskInfo.model_validate({"task_id": "t", "ttl": s})
        except ValidationError as inner:
            raise RuntimeError(f"task parse failed: {inner}") from inner
    except RuntimeError as outer:
        text = exception_text(outer)
        assert text.startswith(
            "RuntimeError: 1 validation error for McpTaskInfo: $.ttl: must be an integer"
        ), text
        assert not any(form in text for form in _forbidden(s))
        assert safe_exc_info(outer) is None
    plain = RuntimeError("no validation here")
    assert exception_text(plain) == "no validation here"
    assert safe_exc_info(plain) is plain


# --- every exception-to-text sink in src/pmcp (rev 2, board finding B1/N3) ------

#: Exception types an `except` clause names that also catch a pydantic
#: (`ValueError`) or jsonschema (`_Error`) `ValidationError`.
_CATCHES_VALIDATION = {
    "Exception",
    "BaseException",
    "ValueError",
    "ValidationError",
    "_Error",
}
#: Renderers that describe a validation error from its structure.
_SAFE_RENDERERS = {
    "exception_text",
    "safe_exc_info",
    "safe_traceback_text",
    "describe_exception",
    "sanitize_auth_diagnostic",
    "_sanitize_error",
    "describe_argument_error",
    "describe_schema_error",
    "describe_model_error",
    "type",
    "isinstance",
}
#: Callees that receive an exception and do not render its text, each read:
_NON_RENDERING_CALLEES = {
    # auth.py: parses a JSON-RPC elicitation payload out of `args[0]` and
    # returns structured URLs; never returns or logs the exception's text.
    "parse_url_elicitation_error",
    # manager.py: a boolean predicate over the message.
    "_is_protocol_version_initialize_error",
    # manager.py: hands the exception to the awaiting connect caller, whose
    # own `except` is checked here like any other.
    "set_exception",
    # policy.py: renders its `error` argument with `exception_text`.
    "_warn_unparseable",
    # scoped_advisor_audit.py (#296): records path and keyword only.
    "record_rejected_arguments",
}
#: Attributes of an exception that carry its text (or the value) themselves.
_TEXT_ATTRIBUTES = {
    "args",
    "message",
    "errors",
    "json",
    "instance",
    "validator_value",
    "context",
    "cause",
    "exceptions",
    "__cause__",
    "__context__",
    "__traceback__",
    "__str__",
    "__repr__",
}
_TRACEBACK_RENDERERS = {
    "format_exc",
    "format_exception",
    "print_exc",
    "print_exception",
}
#: Functions that read an exception's text to parse it, and return no text:
_NON_RENDERING_FUNCTIONS = {
    # auth.py: looks for a JSON-RPC -32042 payload in `args[0]` / `str()`
    # and returns structured `UrlElicitationInfo` (URLs the server sent).
    "parse_url_elicitation_error",
}


def _sink_sources() -> list[Path]:
    root = Path(__file__).resolve().parents[1] / "src" / "pmcp"
    return [
        path
        for path in sorted(root.rglob("*.py"))
        if not any(
            part in ("cli.py", "cli_commands", "__main__.py", "baml_client")
            for part in path.relative_to(root).parts
        )
        and path.name != "argument_errors.py"
    ]


def _caught_names(node: Any) -> set[str]:
    import ast

    if node is None:
        return {"<bare>"}
    if isinstance(node, ast.Tuple):
        return set().union(*(_caught_names(item) for item in node.elts))
    if isinstance(node, ast.Attribute):
        return {node.attr}
    if isinstance(node, ast.Name):
        return {node.id}
    return {"<expression>"}


def _exception_sinks(source: str, label: str) -> list[str]:
    """Every use of an exception that could render a validation error's text
    without going through a renderer above. An exception name is: an
    `except` clause's name, if the clause can catch a `ValidationError`; a
    name assigned `<task>.exception()`; a name narrowed by
    `isinstance(name, Exception|BaseException)`; and any alias of those."""
    import ast

    tree = ast.parse(source)
    for function in ast.walk(tree):
        if (
            isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef))
            and function.name in _NON_RENDERING_FUNCTIONS
        ):
            function.body = [ast.Pass()]
    parents = {
        child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)
    }
    found: list[str] = []
    scopes: list[tuple[list[ast.stmt], set[str]]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler) and node.name:
            if _caught_names(node.type) & (
                _CATCHES_VALIDATION | {"<bare>", "<expression>"}
            ):
                scopes.append((node.body, {node.name}))
        elif isinstance(node, ast.If):
            test = node.test
            if (
                isinstance(test, ast.Call)
                and getattr(test.func, "id", None) == "isinstance"
                and isinstance(test.args[0], ast.Name)
                and _caught_names(test.args[1]) & {"Exception", "BaseException"}
            ):
                scopes.append((node.body, {test.args[0].id}))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names = {
                target.id
                for stmt in ast.walk(node)
                if isinstance(stmt, ast.Assign)
                and isinstance(stmt.value, ast.Call)
                and isinstance(stmt.value.func, ast.Attribute)
                and stmt.value.func.attr == "exception"
                and not stmt.value.args
                for target in stmt.targets
                if isinstance(target, ast.Name)
            }
            if names:
                scopes.append((node.body, names))
    for body, names in scopes:
        module = ast.Module(body=body, type_ignores=[])
        # Aliases (`last_error = e`) are exception names too.
        for stmt in ast.walk(module):
            if (
                isinstance(stmt, ast.Assign)
                and isinstance(stmt.value, ast.Name)
                and stmt.value.id in names
            ):
                names |= {t.id for t in stmt.targets if isinstance(t, ast.Name)}
        for use in ast.walk(module):
            if not (
                isinstance(use, ast.Name)
                and use.id in names
                and isinstance(use.ctx, ast.Load)
            ):
                continue
            parent = parents.get(use)
            if isinstance(parent, ast.keyword):
                parent = parents.get(parent)
            if isinstance(parent, ast.Call):
                callee = getattr(parent.func, "id", None) or getattr(
                    parent.func, "attr", None
                )
                if callee in _SAFE_RENDERERS | _NON_RENDERING_CALLEES:
                    continue
            elif isinstance(
                parent, (ast.Raise, ast.Compare, ast.BoolOp, ast.If, ast.UnaryOp)
            ):
                continue
            elif isinstance(parent, ast.Assign) and parent.value is use:
                continue
            elif (
                isinstance(parent, ast.Attribute)
                and parent.attr not in _TEXT_ATTRIBUTES
            ):
                continue
            found.append(f"{label}:{use.lineno}: {type(parent).__name__} uses {use.id}")
    for call in ast.walk(tree):
        if not isinstance(call, ast.Call):
            continue
        callee = getattr(call.func, "attr", None) or getattr(call.func, "id", None)
        if callee == "exception" and (call.args or call.keywords):
            found.append(f"{label}:{call.lineno}: logger.exception renders a traceback")
        if callee in _TRACEBACK_RENDERERS:
            found.append(f"{label}:{call.lineno}: traceback.{callee}")
        for keyword in call.keywords:
            if keyword.arg == "exc_info" and not (
                isinstance(keyword.value, ast.Call)
                and getattr(keyword.value.func, "id", None) == "safe_exc_info"
            ):
                found.append(
                    f"{label}:{call.lineno}: exc_info= not through safe_exc_info"
                )
    return found


def test_no_exception_reaches_text_except_through_the_renderer() -> None:
    """Static half of the class (rev 2): every place `src/pmcp` (bar the
    operator's CLI) turns an exception that may be a `ValidationError` into
    text -- a response, a log line, a traceback, an audit or health field --
    goes through `exception_text` / `safe_exc_info` or another renderer
    above. The dynamic sweeps below exercise the reachable ones."""
    sources = _sink_sources()
    assert len(sources) > 40, len(sources)
    found = [
        sink
        for path in sources
        for sink in _exception_sinks(path.read_text(), str(path.name))
    ]
    assert found == [], "\n".join(found)


@pytest.mark.parametrize(
    "snippet",
    [
        "try:\n    f()\nexcept Exception as e:\n    log(f'{e}')\n",
        "try:\n    f()\nexcept ValueError as e:\n    x = str(e)\n",
        "try:\n    f()\nexcept Exception as e:\n    logger.warning('%s', e)\n",
        "try:\n    f()\nexcept Exception as e:\n    y = e.args[0]\n",
        "try:\n    f()\nexcept Exception as e:\n    last = e\n    out(last)\n",
        "try:\n    f()\nexcept Exception:\n    logger.error('x', exc_info=True)\n",
        "try:\n    f()\nexcept Exception:\n    logger.exception('x')\n",
        "def g(t):\n    exc = t.exception()\n    log(f'{exc}')\n",
        "def g(r):\n    if isinstance(r, Exception):\n        log(f'{r}')\n",
        "import traceback\ntraceback.format_exc()\n",
    ],
)
def test_the_sink_scanner_flags_each_shape(snippet: str) -> None:
    """The static check is only as wide as its rules: each rule fires."""
    assert _exception_sinks(snippet, "snippet"), snippet


def test_the_sink_scanner_passes_the_renderers() -> None:
    clean = (
        "try:\n    f()\nexcept Exception as e:\n"
        "    log(f'{exception_text(e)}', exc_info=safe_exc_info(e))\n"
        "    if e.code == 1:\n        raise\n    raise X() from e\n"
        "try:\n    f()\nexcept KeyError as e:\n    log(f'{e}')\n"
    )
    assert _exception_sinks(clean, "clean") == []


# --- downstream data, through the real handlers (rev 2, board finding B1) ------
#
# A downstream server's payload is validated by pmcp's own models
# (`McpTaskInfo` for every task payload; `ToolInfo`/`ResourceInfo`/
# `PromptInfo`/`PromptArgumentInfo` for listings). The handlers that catch the
# failure used to render `str(e)` -- pydantic text with `input_value` -- into
# the response, the log and the audit-event buffer `gateway.health` exposes.
# Only the transport is replaced here: `ClientManager._send_request`.

_DOWNSTREAM = "svc"


def _payload_keys(function: Any) -> list[str]:
    """Every literal key `function` reads from a mapping it is handed:
    `x.get("k")`, `x["k"]`, and `helper(x, "k")` -- derived from its source,
    so a new field the parser reads is swept without editing this test."""
    import ast
    import inspect
    import textwrap

    tree = ast.parse(textwrap.dedent(inspect.getsource(function)))
    keys: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "get"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                keys.add(node.args[0].value)
            if (
                len(node.args) == 2
                and isinstance(node.args[1], ast.Constant)
                and isinstance(node.args[1].value, str)
            ):
                keys.add(node.args[1].value)
        elif (
            isinstance(node, ast.Subscript)
            and isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, str)
        ):
            keys.add(node.slice.value)
    return sorted(keys)


def _bad_values(s: str) -> dict[str, Any]:
    return {
        "string": s,
        "object": {s: s, "k": [s]},
        "array": [s, {s: s}],
        "number-in-object": {"n": 7, s: [s]},
    }


def _task_positions() -> list[tuple[str, str]]:
    """(payload key, bad-value shape) pairs the real task parser rejects with
    a `ValidationError` -- found by running it, not by listing fields."""
    from pmcp.client.manager import ClientManager

    manager = ClientManager()
    keys = _payload_keys(ClientManager._task_info_from_payload)
    assert {"ttl", "pollInterval", "createdAt", "status"} <= set(keys), keys
    positions = []
    for key in keys:
        for shape, value in _bad_values(_SENTINELS[0]).items():
            payload = (
                {"taskId": "t", key: value}
                if key not in ("taskId", "task_id")
                else {key: value}
            )
            try:
                manager._task_info_from_payload(payload)
            except ValidationError:
                positions.append((key, shape))
    assert len(positions) > 10, positions
    return positions


def _task_server(
    tmp_path: Path, *, audited: bool
) -> tuple[GatewayServer, Path | None, dict]:
    """A server whose one downstream (`svc`) is task-capable and answers every
    request with `state["payload"]` as its task."""
    from unittest.mock import MagicMock

    from pmcp.client.manager import ManagedClient
    from pmcp.config.loader import make_tool_id
    from pmcp.types import (
        LocalMcpServerConfig,
        ResolvedServerConfig,
        RiskHint,
        ServerStatus,
        ServerStatusEnum,
        ToolInfo,
    )

    server, audit_path = _server(tmp_path, audited=audited)
    # The scoped policy allows two research servers; this one stands in.
    policy = server._policy_manager
    policy.is_server_allowed = lambda name: True  # type: ignore[method-assign]
    policy.is_tool_allowed = lambda tool_id: True  # type: ignore[method-assign]
    manager = server._client_manager
    tool = ToolInfo(
        tool_id=make_tool_id(_DOWNSTREAM, "run"),
        server_name=_DOWNSTREAM,
        tool_name="run",
        description="run",
        short_description="run",
        input_schema={"type": "object", "properties": {}},
        execution={"taskSupport": "required"},
        tags=["svc"],
        risk_hint=RiskHint.LOW,
    )
    manager._tools[tool.tool_id] = tool
    status = ServerStatus(
        name=_DOWNSTREAM,
        status=ServerStatusEnum.ONLINE,
        tool_count=1,
        server_capabilities={"tasks": {"listChanged": True}},
        protocol_version="2025-11-25",
    )
    manager._clients[_DOWNSTREAM] = ManagedClient(
        config=ResolvedServerConfig(
            name=_DOWNSTREAM,
            source="custom",
            config=LocalMcpServerConfig(command="svc"),
        ),
        is_remote=True,
        write_stream=MagicMock(),
        status=status,
    )
    manager._servers[_DOWNSTREAM] = status
    state: dict[str, Any] = {"payload": {}, "methods": []}

    async def send_request(managed: Any, method: str, params: Any, **_: Any) -> Any:
        state["methods"].append(method)
        payload = state["payload"]
        if method == "tasks/list":
            return {"tasks": [payload]}
        if method == "tasks/result":
            return {"task": payload, "result": {"content": []}}
        return {"task": payload}

    manager._send_request = send_request  # type: ignore[method-assign]
    return server, audit_path, state


def _task_calls() -> list[tuple[str, dict[str, Any], str]]:
    """(gateway tool, arguments, downstream method) for every gateway tool
    that parses a downstream task payload."""
    target = {"server_name": _DOWNSTREAM, "task_id": "t"}
    return [
        ("gateway.tasks_list", {"server_name": _DOWNSTREAM}, "tasks/list"),
        ("gateway.tasks_get", target, "tasks/get"),
        ("gateway.tasks_result", target, "tasks/result"),
        ("gateway.tasks_cancel", target, "tasks/cancel"),
        (
            "gateway.invoke",
            {
                "tool_id": f"{_DOWNSTREAM}::run",
                "task": {"enabled": True},
                **_correlations(),
            },
            "tools/call",
        ),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("audited", [False, True], ids=["plain", "scoped-audit"])
async def test_no_downstream_value_reaches_a_response_log_or_audit(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    capfd: pytest.CaptureFixture[str],
    recwarn: pytest.WarningsRecorder,
    audited: bool,
) -> None:
    caplog.set_level(logging.DEBUG)
    server, audit_path, state = _task_server(tmp_path, audited=audited)
    tap = _Tap(server, audit_path, caplog, capfd, recwarn)
    positions = _task_positions()
    parser = server._client_manager._task_info_from_payload

    def rejects(key: str, value: Any) -> bool:
        try:
            parser({"taskId": "t", "status": "working", key: value})
        except ValidationError:
            return True
        return False

    rejected = expected = 0
    for name, arguments, method in _task_calls():
        for key, shape in positions:
            for family, sentinels in _FAMILIES.items():
                # A value the parser accepts (a digits-only `createdAt` is a
                # number) is downstream data pmcp returns by design, not a
                # rejection; only rejected values are this sweep's subject.
                if not all(rejects(key, _bad_values(s)[shape]) for s in sentinels):
                    continue
                expected += 1
                seen = []
                for s in sentinels:
                    state["payload"] = {
                        "taskId": "t",
                        "status": "working",
                        key: _bad_values(s)[shape],
                    }
                    state["methods"].clear()
                    server._client_manager._tasks.clear()
                    if method == "tasks/cancel":
                        # Cancel asks downstream only for a live, known task.
                        server._client_manager._record_task(
                            _DOWNSTREAM, McpTaskInfo(task_id="t", status="working")
                        )
                    mark = tap.start()
                    result = await _call(server, name, arguments)
                    response = "".join(block.text for block in result.content)
                    observed = tap.since(mark, response)
                    assert method in state["methods"], (name, state["methods"])
                    assert observed.leaks(s) == [], (name, key, shape, family, observed)
                    seen.append(observed)
                # No vacuous pass: the payload was rejected, and said so.
                assert re.search(
                    r"validation errors? for McpTaskInfo: \$", seen[0].response
                ), (
                    name,
                    key,
                    seen[0].response,
                )
                assert seen[0].stable() == seen[1].stable(), (name, key, shape, family)
                assert _event_shape(seen[0].events) == _event_shape(seen[1].events)
                rejected += 1
    assert rejected == expected > len(_task_calls()) * len(positions), (
        rejected,
        expected,
    )
    # What `gateway.health` exposes (the audit-event buffer, among the rest).
    health = "".join(
        block.text for block in (await _call(server, "gateway.health", {})).content
    )
    assert "audit_events" in health
    for sentinels in _FAMILIES.values():
        for s in sentinels:
            assert not any(form in health for form in _forbidden(s))


def _listing_parsers() -> list[tuple[str, Any, dict[str, Any], str | None]]:
    """(label, real parser, a valid entry, the nested list its keys may also
    sit in) for every downstream listing pmcp parses into its own models."""
    from pmcp.client import manager

    return [
        (
            "tools",
            lambda entries: manager._parse_tool_entries("svc", entries, 100),
            {"name": "t", "inputSchema": {"type": "object"}},
            None,
        ),
        (
            "resources",
            lambda entries: manager._parse_resource_entries("svc", entries),
            {"uri": "x://r"},
            None,
        ),
        (
            "prompts",
            lambda entries: manager._parse_prompt_entries("svc", entries),
            {"name": "p", "arguments": [{"name": "a"}]},
            "arguments",
        ),
    ]


@pytest.mark.parametrize("label", ["tools", "resources", "prompts"])
def test_no_downstream_listing_value_reaches_the_log(
    label: str,
    caplog: pytest.LogCaptureFixture,
    capfd: pytest.CaptureFixture[str],
    recwarn: pytest.WarningsRecorder,
) -> None:
    """The listing parsers skip an entry pmcp's model rejects and log why."""
    from pmcp.client import manager

    caplog.set_level(logging.DEBUG)
    function = {
        "tools": manager._parse_tool_entries,
        "resources": manager._parse_resource_entries,
        "prompts": manager._parse_prompt_entries,
    }[label]
    _, parse, valid, nested = next(p for p in _listing_parsers() if p[0] == label)
    keys = [key for key in _payload_keys(function) if key]
    tap = _Tap(typing.cast(Any, MagicMockServer()), None, caplog, capfd, recwarn)

    def rejection(entry: dict[str, Any]) -> BaseException | None:
        """What the real parser raised for `entry` (its `except` hands it to
        `describe_exception`, recorded here and rendered as its type only)."""
        with mock.patch.object(
            manager, "describe_exception", side_effect=lambda exc: type(exc).__name__
        ) as seen:
            parse([entry])
        return seen.call_args.args[0] if seen.call_args else None

    rejected = 0
    for key in keys:
        for into_nested in [False, True] if nested else [False]:
            for shape in _bad_values(_SENTINELS[0]):

                def build(s: str) -> dict[str, Any]:
                    entry = copy.deepcopy(valid)
                    target = entry[nested][0] if into_nested else entry
                    target[key] = _bad_values(s)[shape]
                    return entry

                # This sweep's subject is a validation error's text. An entry
                # whose identity is unusable is skipped earlier, by
                # `_required_identity`, and logged with `_entry_label` -- the
                # downstream entry itself, by design (see the plan's
                # Non-goals); an accepted value is indexed by design.
                if not all(
                    isinstance(rejection(build(s)), ValidationError)
                    for sentinels in _FAMILIES.values()
                    for s in sentinels
                ):
                    continue
                for family, sentinels in _FAMILIES.items():
                    pair = []
                    for s in sentinels:
                        mark = tap.start()
                        parse([build(s)])
                        observed = tap.since(mark, "")
                        assert observed.leaks(s) == [], (
                            label,
                            key,
                            shape,
                            family,
                            observed,
                        )
                        assert "validation error" in observed.log, observed.log
                        pair.append(observed)
                    assert pair[0].stable() == pair[1].stable(), (
                        label,
                        key,
                        shape,
                        family,
                    )
                rejected += 1
    # No vacuous pass: pmcp's own model rejected some of the generated fields.
    assert rejected > 3, (label, rejected)


class MagicMockServer:
    """A stand-in with no gateway tools, for `_Tap` outside a server."""

    _gateway_tools = None
