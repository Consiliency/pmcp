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
import itertools
import gc
import functools
from unittest import mock
import hashlib
import json
import re
import logging
import traceback
import typing
from pathlib import Path
from typing import Any, cast

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


def _view(node: dict[str, Any]) -> dict[str, Any]:
    """`node` as the sweep reads it: a nullable union (`anyOf: [X, {"type":
    "null"}]`, Consiliency/pmcp#371) is X with `null` added to its types, so
    X's own constraints are swept (rev 20: until then such a position had no
    `type` and yielded no case)."""
    any_of = node.get("anyOf")
    if (
        isinstance(any_of, list)
        and len(any_of) == 2
        and any_of[1] == {"type": "null"}
        and isinstance(any_of[0], dict)
    ):
        inner = _view(any_of[0])
        types = _types(inner)
        rest = {k: v for k, v in node.items() if k != "anyOf"}
        return {**rest, **inner, **({"type": [*types, "null"]} if types else {})}
    return node


def _positions(
    node: dict[str, Any], path: tuple[str | int, ...] = ()
) -> list[tuple[tuple[str | int, ...], dict[str, Any]]]:
    """Every declared position below the root, depth-first, each read through
    `_view`."""
    found: list[tuple[tuple[str | int, ...], dict[str, Any]]] = []
    node = _view(node)
    for key, child in sorted((node.get("properties") or {}).items()):
        found.append(((*path, key), _view(child)))
        found.extend(_positions(child, (*path, key)))
    items = node.get("items")
    if isinstance(items, dict):
        found.append(((*path, 0), _view(items)))
        found.extend(_positions(items, (*path, 0)))
    return found


def test_every_position_the_sweep_reads_is_typed_or_open() -> None:
    """No silent shrink under a schema shape the sweep does not read: every
    declared position has a type once nullable unions are unfolded, or is
    deliberately open (`{}` -- any JSON value)."""
    untyped = [
        (name, path)
        for name, tool in _tools().items()
        for path, node in _positions(tool.input_schema)
        if not _types(node) and set(node) - {"description", "default"}
    ]
    assert not untyped, untyped


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
        # A server an earlier test left open is finalised by the collector at
        # an arbitrary moment, and its `ResourceWarning` would land inside
        # some pair. Collect now; each test here shuts its own servers down.
        gc.collect()

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
        """What must be identical for two sentinels of different length. A
        task time pmcp itself records (the current time, for a timestamp it
        dropped as unusable; Consiliency/pmcp#298) is not the pair's."""
        response = re.sub(
            r'"(created_at|updated_at)": [0-9.eE+-]+', r'"\1": <time>', self.response
        )
        # ...and so is the digest of a result holding that time.
        audit = [
            {k: v for k, v in entry.items() if k != "redacted_result_digest"}
            for entry in self.audit
        ]
        return (response, self.log, self.streams, self.warnings, audit)

    def differs(self, other: _Observed) -> list[tuple[str, Any, Any]]:
        """The channels where `self` and `other` differ, for a readable failure."""
        names = ("response", "log", "streams", "warnings", "audit")
        return [
            (name, mine, theirs)
            for name, mine, theirs in zip(names, self.stable(), other.stable())
            if mine != theirs
        ]


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
    await server.shutdown()
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
    await server.shutdown()
    assert checked > 100, checked


# --- validation errors raised inside a handler --------------------------------------


def _handler_validation_errors(s: str) -> list[BaseException]:
    """One of each validation-error type a handler can raise, built from a
    value carrying `s`: a pydantic error from a downstream-data model, a
    pydantic error from an argument model, and a jsonschema error."""
    errors: list[BaseException] = []
    for model, data in (
        (McpTaskInfo, {"task_id": {s: s}, "raw": [s]}),
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
        r"2 validation errors for McpTaskInfo: \$\.task_id: must be a string; \$\.raw: "
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
    await server.shutdown()
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
    from pmcp.tools.schema import gate_error_for

    schema = _tools()["gateway.invoke"].input_schema
    # The error the gate reports: a nullable union's refusal unfolded into what
    # X refused (Consiliency/pmcp#371's `gate_error_for`), as the server does.
    error = gate_error_for(arguments, schema)
    assert error is not None
    error.__class__ = _PoisonedSchemaError
    described = describe_schema_error(error, schema, arguments)
    assert described == expected
    assert "sk-" not in described and "SECRET" not in described


def test_a_raw_nullable_union_refusal_is_read_from_the_schema() -> None:
    """An `anyOf: [X, null]` refusal that did not come through `gate_error_for`
    is described from our schema alone (rev 20): X's type, or null."""
    from pmcp.argument_errors import describe_schema_error

    schema = _tools()["gateway.invoke"].input_schema
    arguments = {"tool_id": "a::b", "options": "sk-SECRET-VALUE"}
    error = jsonschema.exceptions.best_match(
        jsonschema.validators.validator_for(schema)(schema).iter_errors(arguments)
    )
    assert error is not None and error.validator == "anyOf"
    error.__class__ = _PoisonedSchemaError
    assert (
        describe_schema_error(error, schema, arguments)
        == "$.options: must be null or a valid object"
    )


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
            McpTaskInfo.model_validate({"task_id": {"v": s}})
        except ValidationError as inner:
            raise RuntimeError(f"task parse failed: {inner}") from inner
    except RuntimeError as outer:
        text = exception_text(outer)
        assert text.startswith(
            "RuntimeError: 1 validation error for McpTaskInfo: $.task_id: must be a string"
        ), text
        assert not any(form in text for form in _forbidden(s))
        assert safe_exc_info(outer) is None
    plain = RuntimeError("no validation here")
    assert exception_text(plain) == "no validation here"
    assert safe_exc_info(plain) is plain


# --- rev 18: a wrapper of a value-bearing error is never rendered from its
# own message (round-16 codex F001: `f"{e!r}"` evaded the substring check).
# The grid is every leaf type in the registry x every way a wrapper's text
# can be built from it x `__cause__` / `__context__` / `from None` x chain
# depth 1-3 x every rendering surface. Two sentinels: one long, and one
# shorter than any value-matching floor, so the rule cannot be value matching.


def _leaf_pydantic(s: str) -> BaseException:
    try:
        McpTaskInfo.model_validate({"task_id": {"v": s}})
    except ValidationError as error:
        return error
    raise AssertionError("no error")


def _leaf_jsonschema(s: str) -> BaseException:
    try:
        jsonschema.validate(s, {"type": "integer"})
    except jsonschema.ValidationError as error:
        return error
    raise AssertionError("no error")


def _leaf_schema(s: str) -> BaseException:
    try:
        jsonschema.Draft202012Validator.check_schema({"type": s})
    except jsonschema.SchemaError as error:
        return error
    raise AssertionError("no error")


def _leaf_yaml(s: str) -> BaseException:
    import yaml

    try:
        yaml.safe_load(f"a: b: {s}\n")
    except yaml.YAMLError as error:
        return error
    raise AssertionError("no error")


def _leaf_json(s: str) -> BaseException:
    try:
        json.loads("{" + s)
    except json.JSONDecodeError as error:
        return error
    raise AssertionError("no error")


_GRID_LEAVES: dict[str, Any] = {
    "pydantic": _leaf_pydantic,
    "jsonschema": _leaf_jsonschema,
    "schema_error": _leaf_schema,
    "yaml": _leaf_yaml,
    "json": _leaf_json,
}


def _value_attr(error: BaseException) -> Any:
    """The attribute of `error` that holds what it rejected."""
    for name in ("instance", "doc"):
        if hasattr(error, name):
            return getattr(error, name)
    if isinstance(error, ValidationError):
        return error.errors()[0]["input"]
    mark = getattr(error, "problem_mark", None)
    return getattr(mark, "buffer", None)


def _message_attr(error: BaseException) -> str:
    """The error's own message field, as a wrapper would read it."""
    if isinstance(error, ValidationError):
        return str(error.errors())
    for name in ("message", "msg"):
        if isinstance(getattr(error, name, None), str):
            return str(getattr(error, name))
    return f"{getattr(error, 'problem', '')} {getattr(error, 'problem_mark', '')}"


class _LateStr(Exception):
    """A wrapper whose text is computed when rendered, from its cause."""

    def __str__(self) -> str:
        return f"late: {self.__cause__ or self.__context__!r}"


_GRID_FORMS: dict[str, Any] = {
    "fstring": lambda e: f"failed: {e}",
    "fstring_r": lambda e: f"failed: {e!r}",
    "fstring_s": lambda e: f"failed: {e!s}",
    "fstring_a": lambda e: f"failed: {e!a}",
    "percent_s": lambda e: "failed: %s" % (e,),
    "percent_r": lambda e: "failed: %r" % (e,),
    "format": lambda e: "failed: {}".format(e),
    "format_r": lambda e: "failed: {!r}".format(e),
    "builtin_format": lambda e: format(e),
    "ascii": lambda e: ascii(e),
    "slice": lambda e: str(e)[:60],
    "repr_slice": lambda e: repr(e)[1:80],
    "value_attr": lambda e: f"bad value {_value_attr(e)!r}",
    "value_attr_s": lambda e: f"bad value {_value_attr(e)}",
    "message_attr": lambda e: f"invalid: {_message_attr(e)}",
    "args": lambda e: ("failed", e),
    "late_str": None,
    "empty": lambda e: "",
    "unrelated": lambda e: "the request failed",
}


def _exception_group() -> type[Exception]:
    """`ExceptionGroup` (3.11+), or anyio's backport on Python 3.10."""
    import builtins

    group = getattr(builtins, "ExceptionGroup", None)
    if group is None:  # Python 3.10
        from exceptiongroup import ExceptionGroup as group
    return cast(type[Exception], group)


def _wrap(form: str, inner: BaseException, link: str) -> BaseException:
    """`inner` wrapped once: raised from it, inside its handler, from None,
    or (rev 19, round-17 claude F002) held as the only member of an exception
    group whose own message is the form's text, raised outside any handler,
    so only the group's members reach it."""
    kind: type[BaseException] = _LateStr if form == "late_str" else RuntimeError
    message = None if form == "late_str" else _GRID_FORMS[form](inner)
    args = (
        message
        if isinstance(message, tuple)
        else (() if message is None else (message,))
    )
    if link == "group":
        text = message if isinstance(message, str) else f"failed: {inner!r}"
        group = _exception_group()(text, [cast(Exception, inner)])
        try:
            raise group
        except BaseException as raised:
            assert raised.__context__ is None and raised.__cause__ is None
            return raised
    try:
        try:
            raise inner
        except BaseException as caught:
            if link == "cause":
                raise kind(*args) from caught
            if link == "suppressed":
                raise kind(*args) from None
            raise kind(*args)
    except BaseException as wrapped:
        return wrapped
    raise AssertionError("not raised")


def _grid_surfaces(outer: BaseException) -> dict[str, str]:
    """Every surface that renders `outer`, rendered."""
    import asyncio

    from pmcp.argument_errors import exception_text, safe_traceback_text
    from pmcp.server import _described_errors

    factory = logging.getLogRecordFactory()
    exc_info = (type(outer), outer, outer.__traceback__)
    records = {
        "log_msg": factory("pmcp.grid", logging.ERROR, __file__, 1, outer, (), None),
        "log_args": factory(
            "pmcp.grid", logging.ERROR, __file__, 1, "failed: %r", (outer,), None
        ),
        "log_mapping": factory(
            "pmcp.grid",
            logging.ERROR,
            __file__,
            1,
            "failed: %(e)s",
            ({"e": [outer]},),
            None,
        ),
        "log_exc_info": factory(
            "pmcp.grid", logging.ERROR, __file__, 1, "failed", (), exc_info
        ),
    }
    out = {name: _record_text(record) for name, record in records.items()}
    out["exception_text"] = exception_text(outer)
    out["safe_traceback_text"] = safe_traceback_text(outer)

    async def reject() -> None:
        raise outer

    try:
        asyncio.run(_described_errors(reject)())
    except Exception as raised:
        out["described_errors"] = f"{type(raised).__name__}: {raised}"
    return out


@pytest.mark.parametrize("sentinel", ["long", "short"])
@pytest.mark.parametrize("depth", [1, 2, 3])
@pytest.mark.parametrize("link", ["cause", "context", "suppressed", "group"])
@pytest.mark.parametrize("leaf", sorted(_GRID_LEAVES))
def test_a_wrapper_of_a_value_bearing_error_is_never_rendered_from_its_message(
    leaf: str, link: str, depth: int, sentinel: str
) -> None:
    """Whatever form built the wrapper's text, however deep the chain and
    whichever link holds the error, every surface renders the outermost
    exception as `<class>: <the leaf's structural description>` and nothing
    of the value."""
    from pmcp.argument_errors import (
        _qualified_name,
        _validation_text,
        exception_text,
    )

    s = _SENTINELS[1] if sentinel == "long" else "Qx7"
    failures = []
    for form in _GRID_FORMS:
        inner = _GRID_LEAVES[leaf](s)
        description = _validation_text(inner)
        for _ in range(depth):
            inner = _wrap(form, inner, link)
        outer = inner
        expected = f"{type(outer).__name__}: {description}"
        surfaces = _grid_surfaces(outer)
        if exception_text(outer) != expected:
            failures.append((form, "exception_text", exception_text(outer)))
        if surfaces.get("described_errors") != f"ValueError: {expected}":
            failures.append(
                (form, "described_errors", surfaces.get("described_errors"))
            )
        for surface, text in surfaces.items():
            leaked = (
                s in text
                if sentinel == "short"
                else any(piece in text for piece in _forbidden(s))
            )
            if leaked:
                failures.append((form, surface, text[:200]))
        last = surfaces["safe_traceback_text"].rstrip("\n").rsplit("\n", 1)[-1]
        if last != f"{_qualified_name(type(outer))}: {description}":
            failures.append((form, "traceback last line", last))
    assert not failures, failures


def test_the_value_bearing_registry_is_the_one_decision() -> None:
    """Every leaf the grid raises is registered; pmcp's own `ParseError` is
    the exemption, and a wrapper of it -- or of nothing registered -- keeps
    its own message."""
    import yaml

    from pmcp.argument_errors import (
        _is_validation_error,
        _value_bearing_types,
        _value_free_types,
        exception_text,
    )
    from pmcp.parsing import ParseError, load_yaml

    registered = _value_bearing_types()
    for make in _GRID_LEAVES.values():
        leaf = make("Qx7")
        assert isinstance(leaf, registered) and _is_validation_error(leaf), leaf
    assert _value_free_types() == (ParseError,)
    with pytest.raises(ParseError) as raised:
        load_yaml("a: b: Qx7\n", source="grid file")
    assert isinstance(raised.value, yaml.YAMLError)
    assert not _is_validation_error(raised.value)
    for link in ("cause", "context", "suppressed"):
        wrapped = _wrap("fstring", raised.value, link)
        assert exception_text(wrapped) == f"failed: {raised.value}", link
        plain = _wrap("fstring", KeyError("missing"), link)
        assert exception_text(plain) == "failed: 'missing'", link


@pytest.mark.parametrize("wrapped", [False, True])
def test_a_group_member_value_never_reaches_a_log_or_traceback(wrapped: bool) -> None:
    """Round-17 claude F002's binding test, as filed: a validation error that
    is a member of an exception group -- bare, or inside a `{e!r}` wrapper --
    reaches neither `logger.exception`, `exception_text` nor the traceback.
    Only `_chain`'s walk over group members finds it."""
    import io

    import pmcp  # noqa: F401 - installs the record scrubber
    from pmcp.argument_errors import exception_text, safe_traceback_text

    s = "SENTINEL_GROUP_MEMBER_VALUE_9137"
    group_type = _exception_group()
    try:
        McpTaskInfo.model_validate({"task_id": {"v": s}})
    except ValidationError as error:
        member: BaseException = error
    if wrapped:
        try:
            raise member
        except BaseException as caught:
            try:
                raise RuntimeError(f"failed: {caught!r}") from caught
            except RuntimeError as wrapper:
                member = wrapper
    try:
        raise group_type("unhandled errors in a TaskGroup", [member])
    except Exception as group:
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        logger = logging.getLogger("pmcp.finding_f002")
        logger.addHandler(handler)
        try:
            logger.exception("transport owner failed")
        finally:
            logger.removeHandler(handler)
        rendered = {
            "log": stream.getvalue(),
            "exception_text": exception_text(group),
            "traceback": safe_traceback_text(group),
        }
    leaked = [name for name, text in rendered.items() if s in text]
    assert not leaked, leaked


@pytest.mark.parametrize("family", sorted(_FAMILIES))
def test_describe_exception_renders_a_grouped_validation_error_structurally(
    family: str,
) -> None:
    """`describe_exception` flattens an anyio task group into its leaves --
    the remote-transport paths' shape -- and each leaf goes through
    `exception_text`, so a validation error inside a group is described,
    not echoed."""
    import builtins

    from pmcp.client.manager import describe_exception

    group_type = getattr(builtins, "ExceptionGroup", None)
    if group_type is None:  # Python 3.10: anyio's backport
        from exceptiongroup import ExceptionGroup as group_type
    s = _FAMILIES[family][1]
    with pytest.raises(ValidationError) as raised:
        McpTaskInfo.model_validate({"task_id": {"v": s}})
    leaf = raised.value
    for group in (
        group_type("unhandled errors in a TaskGroup", [leaf]),
        group_type("unhandled errors in a TaskGroup", [RuntimeError("boom"), leaf]),
    ):
        text = describe_exception(group)
        assert "validation error for McpTaskInfo: $.task_id: must be a string" in text
        assert not any(form in text for form in _forbidden(s)), text


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
    keys = _payload_keys(_task_parser_source())
    assert {"ttl", "pollInterval", "createdAt", "status"} <= set(keys), keys
    positions = []
    for key in keys:
        for shape, value in _bad_values(_SENTINELS[0]).items():
            payload = (
                {"taskId": "t", key: value}
                if key not in ("taskId", "task_id")
                else {key: value}
            )
            if _task_outcome(manager._task_info_from_payload, payload):
                positions.append((key, shape))
    assert len(positions) > 10, positions
    return positions


def _task_outcome(parser: Any, payload: dict) -> str | None:
    """How the task parser refuses a value in `payload`: it raises
    (`rejected`), or -- since Consiliency/pmcp#298 -- drops it and names the
    field in `unusable_fields` (`dropped`). None if it accepts the value, or
    finds no task at all (no string `taskId`: the answer is then not a task,
    and is returned as the downstream's data, as before)."""
    try:
        task = parser(payload)
    except ValidationError:
        return "rejected"
    if task is not None and task.unusable_fields:
        return "dropped"
    return None


def _task_parser_source() -> Any:
    """The task parser: the manager's `_task_info_from_payload` (static from
    rev 15, when it became the only recogniser)."""
    from pmcp.client.manager import ClientManager

    return ClientManager._task_info_from_payload


def _parse_task(payload: dict[str, Any]) -> Any:
    """Run that parser (before rev 15 a method that reads nothing from
    `self`)."""
    import inspect

    from pmcp.client.manager import ClientManager

    raw = inspect.getattr_static(ClientManager, "_task_info_from_payload")
    if isinstance(raw, staticmethod):
        return ClientManager._task_info_from_payload(payload)
    return ClientManager._task_info_from_payload(cast(Any, None), payload)


def _task_id_aliases() -> list[str]:
    """Every wire name the parser takes a task's id from, found by RUNNING it
    on each key it reads (rev 15, round-14: derived from the parser's
    grammar, not from a recogniser's)."""
    return [k for k in _payload_keys(_task_parser_source()) if _parse_task({k: "t"})]


def _renamed_id(payload: dict[str, Any], alias: str) -> dict[str, Any]:
    if alias == "taskId" or "taskId" not in payload:
        return dict(payload)
    return {(alias if key == "taskId" else key): v for key, v in payload.items()}


def _task_wrap(shape: str, alias: str) -> Any:
    def wrap(p: dict[str, Any], method: str) -> Any:
        task = _renamed_id(p, alias)
        if shape == "flat":
            return task
        if shape == "nested" and method == "tasks/result":
            return {"task": task, "result": {"content": []}}
        return {"task": task}

    return wrap


#: Every shape in which a downstream answer carries a task, as the PARSER's
#: grammar accepts it (rev 15): each id alias the parser reads, nested (with a
#: sibling `result` on `tasks/result`), nested without one, and flat.
_TASK_WRAPS: dict[str, Any] = {
    f"{shape}-{alias}": _task_wrap(shape, alias)
    for alias in _task_id_aliases()
    for shape in ("nested", "nested-bare", "flat")
}


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
        wrap = state.get("wrap", "nested-taskId")
        if method == "tasks/list":
            return {"tasks": [_renamed_id(payload, wrap.rsplit("-", 1)[-1])]}
        return _TASK_WRAPS[wrap](payload, method)

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
        payload = {"taskId": "t", "status": "working", key: value}
        return _task_outcome(parser, payload) is not None

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
                # No vacuous pass: the payload was rejected, and said so --
                # or (Consiliency/pmcp#298) the value was dropped, and the
                # field is named as unusable.
                assert re.search(
                    r"validation errors? for McpTaskInfo: \$|\"unusable_fields\": \[\s*\"|"
                    r"[Tt]ask not found",
                    seen[0].response,
                ), (
                    name,
                    key,
                    seen[0].response,
                )
                assert not seen[0].differs(seen[1]), (
                    name,
                    key,
                    shape,
                    family,
                    seen[0].differs(seen[1]),
                )
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
    await server.shutdown()
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


@pytest.mark.asyncio
async def test_a_connect_failure_carrying_a_validation_error_is_described(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capfd: pytest.CaptureFixture[str],
    recwarn: pytest.WarningsRecorder,
) -> None:
    """Dynamic half of the rev 2 board seat's surviving regression (a log
    line in `_connect_with_retry` rendering `last_error`): a connect that
    fails with a validation error, through retries, `connect_server`'s
    result, the log and `gateway.health`."""
    from pmcp.types import LocalMcpServerConfig, ResolvedServerConfig

    caplog.set_level(logging.DEBUG)
    monkeypatch.setattr("pmcp.client.manager.RETRY_DELAYS", [0.0, 0.0, 0.0])
    server, _ = _server(tmp_path, audited=False)
    manager = server._client_manager
    tap = _Tap(server, None, caplog, capfd, recwarn)
    config = ResolvedServerConfig(
        name="flaky", source="custom", config=LocalMcpServerConfig(command="flaky")
    )
    seen = []
    for family, sentinels in _FAMILIES.items():
        for s in sentinels:

            async def connect(_config: Any, s: str = s) -> None:
                McpTaskInfo.model_validate({"task_id": {"v": s}})

            monkeypatch.setattr(manager, "_connect_server", connect)
            mark = tap.start()
            errors = await manager.connect_server(config)
            health = "".join(
                b.text for b in (await _call(server, "gateway.health", {})).content
            )
            observed = tap.since(mark, json.dumps(errors) + health)
            assert observed.leaks(s) == [], (family, observed)
            assert (
                "validation error for McpTaskInfo: $.task_id: must be a string"
                in (errors[0])
            ), errors
            seen.append((json.dumps(errors), observed.log))
    await server.shutdown()
    assert all(item == seen[0] for item in seen[1:])


# --- rev 12: values pmcp rejects by hand, without an exception ---------------


@pytest.mark.asyncio
@pytest.mark.parametrize("family", ("hex", "alpha", "token"))
async def test_a_value_rejected_by_hand_is_described(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_npm_registry: dict[str, str],
    family: str,
) -> None:
    """A caller value a handler rejects by hand (a cancel id, an `auth_connect`
    env var, a server's undeclarable env vars) is described, never echoed:
    response text, structured fields, audit buffer."""
    from pmcp.manifest.loader import Manifest, ServerConfig
    from pmcp.policy.policy import PolicyManager
    from pmcp.tools.handlers import GatewayTools
    from tests.conftest import MockClientManager

    s = _FAMILIES[family][1]
    forbidden = _forbidden(s)
    seen: list[str] = []

    # Rev 13 (round-12 B1): through the real `gateway.cancel`, not
    # `cancel_request` alone -- the response's `request_id` field and the
    # audit event's `server_name` copied the id the format check rejected.
    # Each rejection: the id has no `::`, or its local id is not an integer
    # (with the value on either side of the separator).
    server, audit_path = _server(tmp_path, audited=True)
    for request_id in (s, f"{s}::notint", f"srv::{s}", f"{s}::{s}"):
        result = await _call(server, "gateway.cancel", {"request_id": request_id})
        text = "".join(block.text for block in result.content)
        payload = json.loads(text)
        assert payload["status"] == "not_found", text
        assert payload["request_id"] is None, text
        event = server._gateway_tools._audit_events[-1]
        assert event.method == "gateway.cancel" and event.server_name is None, event
        seen.append(text)
        seen.append(event.model_dump_json())
    # The control: a well-formed id is accepted and kept (its lookup miss is
    # Consiliency/pmcp#315's), so the nulling above is the format rule's.
    result = await _call(server, "gateway.cancel", {"request_id": "nosuch::5"})
    payload = json.loads("".join(block.text for block in result.content))
    assert payload["request_id"] == "nosuch::5", payload
    assert server._gateway_tools._audit_events[-1].server_name == "nosuch"
    await server.shutdown()
    assert audit_path is not None
    seen.append(audit_path.read_text() if audit_path.exists() else "")

    manifest = Manifest(
        version="1.0",
        cli_alternatives={},
        servers={
            "keyed": ServerConfig(
                name="keyed",
                description="d",
                keywords=[],
                install={},
                command="uvx",
                args=["keyed"],
                requires_api_key=True,
                env_var="KEYED_API_KEY",
                env_instructions="Set KEYED_API_KEY",
            )
        },
        discovery_queue_path=".mcp-gateway/discovery_queue.json",
    )
    monkeypatch.setattr("pmcp.tools.handlers.load_manifest", lambda: manifest)
    monkeypatch.setattr("pmcp.tools.handlers.load_configs", lambda **_: [])
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    tools = GatewayTools(
        client_manager=MockClientManager(),  # type: ignore[arg-type]
        policy_manager=PolicyManager(),
    )
    upper = "".join(c for c in s.upper() if c.isalnum())
    for env_var in (f"OTHER_{upper}", f"BAD-{upper}"):
        result = await tools.auth_connect(
            {
                "server_name": "keyed",
                "env_var": env_var,
                "credential": "test-token",
                "scope": "user",
            }
        )
        assert result.ok is False
        seen.append(result.model_dump_json())
        forbidden |= _forbidden(env_var)
    fake_npm_registry["@example/discovered-mcp"] = "1.0.0"
    registered = await tools.register_discovered_server(
        {
            "server_name": "disc",
            "package": "@example/discovered-mcp",
            "env_vars": [f"NPM_CONFIG_{upper}"],
        }
    )
    assert registered.registered is False
    seen.append(registered.model_dump_json())
    forbidden |= _forbidden(f"NPM_CONFIG_{upper}")
    events = (await tools.health()).audit_events or []
    seen.extend(event.model_dump_json() for event in events)
    text = "\n".join(seen)
    assert not any(form in text for form in forbidden), text[:600]


# --- rev 13: every output or audit field that copies a caller input -----------

#: Why each field that copies a caller input may hold it (round-12 B1, as a
#: class). The rule is rev 12's: a value pmcp *rejects* is not copied back,
#: into the response or the audit event. A value it accepts may be, and a
#: lookup of an accepted value that misses is Consiliency/pmcp#315's.
_NULLED = "rejected for its format: null / None on that path (rev 13, B1)"
_REFUSED = "refused: null on the refusal paths (rev 12); copied on success"
_LOOKUP = (
    "accepted (any non-empty string); a miss is a lookup echo, Consiliency/pmcp#315"
)
_ACCEPTED = "copied only on the path that accepted it"
_NEVER = "never rejected by hand"
_DERIVED = "a value pmcp computed from the input, not the input"
_COPIED_INPUT_TRIAGE: dict[tuple[str, str], str] = {
    ("cancel", "CancelOutput.request_id"): _NULLED,
    ("cancel", "GatewayAuditEvent.server_name"): _NULLED,
    ("auth_connect", "AuthConnectOutput.env_var"): _REFUSED,
    ("auth_connect", "AuthConnectOutput.server"): _LOOKUP,
    ("auth_connect", "GatewayAuditEvent.server_name"): _LOOKUP,
    ("auth_connect", "AuthConnectOutput.url_elicitation"): _ACCEPTED,
    ("auth_connect", "UrlElicitationInfo.elicitation_id"): _ACCEPTED,
    ("auth_connect", "UrlElicitationInfo.next_step"): _ACCEPTED,
    ("auth_connect", "UrlElicitationInfo.url"): _ACCEPTED,
    ("auth_connect", "UrlElicitationInfo.url_verified"): _DERIVED,
    ("connect_server", "LifecycleServerOutput.server"): _LOOKUP,
    ("connect_server", "_lifecycle_output.server"): _LOOKUP,
    ("connect_server", "_lifecycle_output.message"): _LOOKUP,
    ("disconnect_server", "LifecycleServerOutput.server"): _LOOKUP,
    ("disconnect_server", "_lifecycle_output.server"): _LOOKUP,
    ("disconnect_server", "_lifecycle_output.active_task_count"): _DERIVED,
    ("disconnect_server", "_lifecycle_output.cancelled_task_count"): _DERIVED,
    ("restart_server", "LifecycleServerOutput.server"): _LOOKUP,
    ("restart_server", "_lifecycle_output.server"): _LOOKUP,
    ("invoke", "InvokeOutput.tool_id"): _LOOKUP,
    ("invoke", "GatewayAuditEvent.tool_id"): _LOOKUP,
    ("invoke", "GatewayAuditEvent.error"): _LOOKUP,
    ("invoke", "InvokeOutput.task"): _DERIVED,
    ("invoke", "process_output.redact"): _DERIVED,
    ("provision", "ProvisionOutput.server"): _LOOKUP,
    ("update_server", "UpdateServerOutput.server"): _LOOKUP,
    ("provision_status", "ProvisionJobStatus.job_id"): _LOOKUP,
    ("register_discovered_server", "RegisterDiscoveredServerOutput.server_name"): (
        "accepted; the refusals are of the package and env vars, described (rev 12)"
    ),
    ("register_discovered_server", "ServerConfig.env_var"): _ACCEPTED,
    ("register_discovered_server", "ServerConfig.name"): _ACCEPTED,
    ("register_discovered_server", "ServerConfig.package"): _ACCEPTED,
    ("request_capability", "CapabilityResolution.candidates"): _DERIVED,
    **{
        ("request_capability", f"CLIResolution.{field}"): _DERIVED
        for field in (
            "available",
            "check_command",
            "description",
            "examples",
            "help_command",
            "name",
            "path",
            "prefer_mcp_for",
            "reason",
        )
    },
    ("search_registry", "SearchRegistryOutput.query"): _NEVER,
    ("submit_feedback", "SubmitFeedbackOutput.issue_title"): (
        "never rejected; a refusal is of the destination or credential, and "
        "the text is returned for filing by hand"
    ),
    ("submit_feedback", "SubmitFeedbackOutput.issue_body"): (
        "never rejected; a refusal is of the destination or credential, and "
        "the text is returned for filing by hand"
    ),
    ("submit_feedback", "SubmitFeedbackOutput.issue_url"): _DERIVED,
    ("submit_feedback", "SubmitFeedbackOutput.repository"): _DERIVED,
    ("sync_environment", "SyncEnvironmentOutput.platform"): _ACCEPTED,
    ("sync_environment", "SyncEnvironmentOutput.detected_clis"): _NEVER,
    ("tasks_list", "GatewayAuditEvent.server_name"): _LOOKUP,
    ("tasks_get", "GatewayAuditEvent.server_name"): _LOOKUP,
    ("tasks_get", "GatewayAuditEvent.task_id"): _LOOKUP,
    ("tasks_result", "GatewayAuditEvent.server_name"): _LOOKUP,
    ("tasks_result", "GatewayAuditEvent.task_id"): _LOOKUP,
    ("tasks_result", "process_output.redact"): _DERIVED,
    ("tasks_cancel", "GatewayAuditEvent.server_name"): _LOOKUP,
    ("tasks_cancel", "GatewayAuditEvent.task_id"): _LOOKUP,
}


def _gateway_tools_class() -> Any:
    import ast

    import pmcp.tools.handlers as handlers_module

    tree = ast.parse(Path(handlers_module.__file__).read_text())
    return next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "GatewayTools"
    )


def _mirrored_fields() -> set[tuple[str, str]]:
    """By the models: each tool's return model's fields named as one of its
    input model's fields (`server_name` is returned as `server`)."""
    import ast

    import pmcp.types as types_module

    found: set[tuple[str, str]] = set()
    for fn in _gateway_tools_class().body:
        if not isinstance(fn, ast.AsyncFunctionDef) or fn.name.startswith("_"):
            continue
        output = getattr(types_module, ast.unparse(fn.returns), None)
        if output is None:
            continue
        for call in ast.walk(fn):
            if not (
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and call.func.attr == "model_validate"
                and isinstance(call.func.value, ast.Name)
                and call.func.value.id.endswith("Input")
            ):
                continue
            names = set(getattr(types_module, call.func.value.id).model_fields)
            if "server_name" in names:
                names.add("server")
            found |= {
                (fn.name, f"{output.__name__}.{field}")
                for field in output.model_fields
                if field in names
            }
    return found


def _copied_inputs() -> set[tuple[str, str]]:
    """By the code: each keyword of a model construction, an `*_output`
    helper call or an `_audit` call whose value is the input itself
    (`parsed.<field>`), a conditional on it, or a local bound from it by an
    attribute, boolean or conditional expression or a module-level parse
    function (`parse_request_id(parsed.request_id)`)."""
    import ast

    def reads(node: ast.AST, bound: dict[str, str]) -> set[str]:
        out: set[str] = set()
        for sub in ast.walk(node):
            if (
                isinstance(sub, ast.Attribute)
                and isinstance(sub.value, ast.Name)
                and sub.value.id == "parsed"
            ):
                out.add(sub.attr)
            elif isinstance(sub, ast.Name) and sub.id in bound:
                out.add(bound[sub.id])
        return out

    found: set[tuple[str, str]] = set()
    for fn in _gateway_tools_class().body:
        if not isinstance(fn, ast.AsyncFunctionDef) or fn.name.startswith("_"):
            continue
        bound: dict[str, str] = {}
        for node in ast.walk(fn):
            if not (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
            ):
                continue
            value = node.value
            if isinstance(value, (ast.Attribute, ast.BoolOp, ast.IfExp)) or (
                isinstance(value, ast.Call) and isinstance(value.func, ast.Name)
            ):
                for name in reads(value, dict(bound)):
                    bound[node.targets[0].id] = name
        for node in ast.walk(fn):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Name) and node.func.id[:1].isupper():
                label = node.func.id
            elif isinstance(node.func, ast.Attribute) and node.func.attr == "_audit":
                label = "GatewayAuditEvent"
            elif isinstance(node.func, ast.Attribute) and node.func.attr.endswith(
                "_output"
            ):
                label = node.func.attr
            else:
                continue
            for keyword in node.keywords:
                value = keyword.value
                if keyword.arg is None or not isinstance(
                    value, (ast.Attribute, ast.Name, ast.IfExp, ast.Subscript)
                ):
                    continue
                if reads(value, bound):
                    found.add((fn.name, f"{label}.{keyword.arg}"))
    return found


def test_every_field_that_copies_a_caller_input_is_triaged() -> None:
    """Round-12 B1 as a class: `gateway.cancel` copied a request id it had
    rejected into its response and its audit event. Every output or audit
    field that copies a caller input -- found two ways, by the models and by
    the code -- has a reason it may hold the value, and the table has no
    stale entry. A new copy fails here until it is triaged."""
    found = _mirrored_fields() | _copied_inputs()
    assert found == set(_COPIED_INPUT_TRIAGE), (
        sorted(found - set(_COPIED_INPUT_TRIAGE)),
        sorted(set(_COPIED_INPUT_TRIAGE) - found),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("wrap", sorted(_TASK_WRAPS))
async def test_no_dropped_task_hint_reaches_the_answer_in_any_task_shape(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    capfd: pytest.CaptureFixture[str],
    recwarn: pytest.WarningsRecorder,
    wrap: str,
) -> None:
    """A task hint pmcp drops as unusable is in no channel of any task tool's
    answer, in every shape the parser reads (each id alias, nested or flat),
    at every position; two sentinel lengths give one size."""
    caplog.set_level(logging.DEBUG)
    probe = {"taskId": "t", "status": "working"}
    for method in ("tools/call", "tasks/get", "tasks/result", "tasks/cancel"):
        answer = _TASK_WRAPS[wrap](probe, method)
        candidate = answer["task"] if "task" in answer else answer
        assert _parse_task(candidate) is not None, (wrap, method)
    server, audit_path, state = _task_server(tmp_path, audited=False)
    state["wrap"] = wrap
    tap = _Tap(server, audit_path, caplog, capfd, recwarn)
    parser = server._client_manager._task_info_from_payload
    dropped = [
        (key, shape)
        for key, shape in _task_positions()
        if _task_outcome(
            parser, {"taskId": "t", "status": "working", key: _bad_values("v")[shape]}
        )
        == "dropped"
    ]
    assert dropped, "no dropped position"
    cases = 0
    for name, arguments, method in _task_calls():
        for key, shape in dropped:
            for family, sentinels in _FAMILIES.items():
                # A value the parser accepts (a digits-only `createdAt` is a
                # number) is data pmcp returns by design, not a dropped hint.
                if not all(
                    _task_outcome(
                        parser,
                        {
                            "taskId": "t",
                            "status": "working",
                            key: _bad_values(s)[shape],
                        },
                    )
                    == "dropped"
                    for s in sentinels
                ):
                    continue
                seen = []
                for s in sentinels:
                    state["payload"] = {
                        "taskId": "t",
                        "status": "working",
                        key: _bad_values(s)[shape],
                    }
                    server._client_manager._tasks.clear()
                    if method == "tasks/cancel":
                        server._client_manager._record_task(
                            _DOWNSTREAM, McpTaskInfo(task_id="t", status="working")
                        )
                    mark = tap.start()
                    result = await _call(server, name, arguments)
                    response = "".join(block.text for block in result.content)
                    observed = tap.since(mark, response)
                    assert observed.leaks(s) == [], (wrap, name, key, shape, family)
                    seen.append(observed)
                assert not seen[0].differs(seen[1]), (
                    wrap,
                    name,
                    key,
                    shape,
                    family,
                    seen[0].differs(seen[1]),
                )
                cases += 1
    await server.shutdown()
    assert cases >= 5 * len(dropped) * (len(_FAMILIES) - 1), cases


# ---------------------------------------------------------------------------
# Rev 15 (round-14 board): the recogniser IS the parser
# ---------------------------------------------------------------------------


def _drops_alone(key: str, value: Any) -> bool:
    """The parser's own verdict: it drops `value` at `key`, the only hint."""
    info = _parse_task({"taskId": "t", key: value})
    return info is not None and key not in info.raw


def _usable_probe(key: str) -> Any:
    for value in (5, 5.0, "working", "a message"):
        info = _parse_task({"taskId": "t", key: value})
        if info is not None and not info.unusable_fields and key in info.raw:
            return value
    return None


def _generated_task_answers() -> list[tuple[str, dict[str, Any]]]:
    """Answers from the parser's grammar: each id it reads, and ids it does
    not (absent, empty, integer, null, object); each other key it reads with
    a dropped value, a null, a usable value, and a usable value beside a
    dropped one; nested beside result data, nested bare, and flat. Plus
    codex's round-14 falsifier (data under `task`, no id)."""
    ids = [(a, {a: "t"}) for a in _task_id_aliases()] + [
        ("no-id", {}),
        ("empty-id", {"taskId": ""}),
        ("int-id", {"taskId": 5}),
        ("null-id", {"taskId": None}),
        ("object-id", {"task_id": {"x": 1}}),
    ]
    keys = [k for k in _payload_keys(_task_parser_source()) if k not in dict(ids)]
    hints: list[tuple[str, dict[str, Any]]] = [("none", {})]
    hints.append(("business", {"statusMessage": {"detail": "business-data"}}))
    for key in keys:
        hints += [(f"{key}={s}", {key: v}) for s, v in _bad_values("S").items()]
        hints.append((f"{key}=null", {key: None}))
        usable = _usable_probe(key)
        if usable is not None:
            hints.append((f"{key}=usable", {key: usable}))
            hints += [
                (f"{key}=usable+{o}=S", {key: usable, o: "S"})
                for o in keys
                if o != key and _usable_probe(o) is not None
            ]
    answers = []
    for id_name, id_part in ids:
        for hint_name, hint_part in hints:
            task = {**id_part, "status": "working", **hint_part}
            name = f"{id_name}/{hint_name}"
            answers.append((f"nested/{name}", {"task": task, "content": []}))
            answers.append((f"nested-bare/{name}", {"task": dict(task)}))
            answers.append((f"flat/{name}", dict(task)))
    return answers


@pytest.mark.asyncio
async def test_the_normaliser_acts_iff_the_parser_accepts_on_every_task_op(
    tmp_path: Path,
) -> None:
    """For every generated answer, through every manager task op: the
    normaliser acts iff the parser parses; it removes exactly the keys the
    parser drops (per wire key) and leaves the rest."""
    from pmcp.client.manager import _TASK_WIRE_KEYS
    from pmcp.types import McpTaskInfo

    server, _, _ = _task_server(tmp_path, audited=False)
    manager = server._client_manager
    reply: dict[str, Any] = {}

    async def send_request(managed: Any, method: str, params: Any, **_: Any) -> Any:
        task = reply.get("task") if isinstance(reply.get("task"), dict) else reply
        return copy.deepcopy({"tasks": [task]} if method == "tasks/list" else reply)

    manager._send_request = send_request  # type: ignore[method-assign]
    answers = _generated_task_answers()
    acted = 0
    for name, answer in answers:
        reply.clear()
        reply.update(copy.deepcopy(answer))
        nested = isinstance(answer.get("task"), dict)
        candidate = answer["task"] if nested else answer
        info = _parse_task(candidate)
        expected: Any = answer
        if info is not None:
            acted += 1
            expected = {**answer, "task": info.raw} if nested else info.raw
            assert set(candidate) - set(info.raw) == {
                k for k in candidate if _drops_alone(k, candidate[k])
            }, name
            for field in info.unusable_fields:
                for key in _TASK_WIRE_KEYS.get(field, ()):
                    assert candidate.get(key) is None or key not in info.raw, name
        manager._tasks.clear()
        got = await manager.call_tool(f"{_DOWNSTREAM}::run", {}, task={"enabled": True})
        assert got == expected and bool(manager._tasks) is (info is not None), name
        manager._tasks.clear()
        if info is None:
            with pytest.raises(KeyError):
                await manager.get_task(_DOWNSTREAM, "t")
        else:
            assert (await manager.get_task(_DOWNSTREAM, "t")).raw == info.raw, name
        listed = await manager.list_tasks(_DOWNSTREAM)
        raws = [t["raw"] for t in listed["tasks"]]
        assert raws == ([info.raw] if info is not None else []), name
        if nested or info is not None:  # otherwise main's rule polls tasks/get
            assert await manager.get_task_result(_DOWNSTREAM, "t") == expected, name
        manager._tasks.clear()
        manager._record_task(_DOWNSTREAM, McpTaskInfo(task_id="t", status="working"))
        _, record, _ = await manager.cancel_task(_DOWNSTREAM, "t")
        assert record is not None
        assert record.raw == (info.raw if info is not None else {}), name
    await server.shutdown()
    assert 0 < acted < len(answers) and len(answers) > 1000, (acted, len(answers))


async def _answer_text(root: Path, tool: str, reply: Any) -> str:
    from pmcp.types import McpTaskInfo

    root.mkdir()
    server, _, _ = _task_server(root, audited=False)

    async def send_request(managed: Any, method: str, params: Any, **_: Any) -> Any:
        return copy.deepcopy(reply)

    server._client_manager._send_request = send_request  # type: ignore[method-assign]
    if tool == "gateway.invoke":
        args = {"tool_id": f"{_DOWNSTREAM}::run", "task": {"enabled": True}}
        args.update(_correlations())
    else:
        args = {"server_name": _DOWNSTREAM, "task_id": "t"}
        task = McpTaskInfo(task_id="t", status="working")
        server._client_manager._record_task(_DOWNSTREAM, task)
    result = await _call(server, tool, args)
    if tool == "gateway.tasks_cancel":
        record = server._client_manager.get_task_record(_DOWNSTREAM, "t")
        assert record is not None and record.raw == {}, record
    await server.shutdown()
    return "".join(block.text for block in result.content)


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ["gateway.invoke", "gateway.tasks_result"])
async def test_task_result_preserves_non_task_data(tmp_path: Path, tool: str) -> None:
    """Round-14 codex F024-F027: a `task` the parser does not read (no id) is
    the downstream's data, kept whole as on main; rev 14 deleted from it."""
    reply = {"task": {"statusMessage": {"detail": "business-data"}}, "content": []}
    assert "business-data" in await _answer_text(tmp_path / "a", tool, reply)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool", "reply"),
    [
        # round-14 grok F002/F003, claude F002: a flat snake-case task.
        ("gateway.invoke", {"task_id": "t", "status": "working", "ttl": "@S@"}),
        ("gateway.tasks_result", {"task_id": "t", "status": "working", "ttl": "@S@"}),
        # round-14 claude F001: the cancel fallback copied the whole answer.
        ("gateway.tasks_cancel", {"taskId": 5, "status": "cancelled", "ttl": "@S@"}),
        ("gateway.tasks_cancel", {"task": {"taskId": 5, "ttl": "@S@"}}),
    ],
)
async def test_a_dropped_task_hint_leaves_by_no_alias_or_fallback(
    tmp_path: Path, tool: str, reply: dict[str, Any]
) -> None:
    sizes = []
    for s in ("violetcanaryrejected", "violetcanaryrejected12345678"):
        sent = json.loads(json.dumps(reply).replace("@S@", s))
        text = await _answer_text(tmp_path / s, tool, sent)
        assert s not in text, text
        sizes.append(json.loads(text).get("raw_size_estimate"))
    assert sizes[0] == sizes[1], sizes


# ---------------------------------------------------------------------------
# Rev 17 (round-15 board): task handling gated on the call's effective mode
# ---------------------------------------------------------------------------


async def _invoke_as(
    root: Path, support: Any, requested: bool, answer: Any, recorded: bool = True
) -> Any:
    """`gateway.invoke` on `svc::run` (taskSupport `support`, None: absent)
    with task "t" recorded by another call if `recorded`; the downstream answers
    `answer`. Returns (the output, the server's policy manager)."""
    from pmcp.types import McpTaskInfo

    root.mkdir()
    server, _, _ = _task_server(root, audited=False)
    manager = server._client_manager
    tool = next(iter(manager._tools.values()))
    tool.execution = {} if support is None else {"taskSupport": support}

    async def send(managed: Any, method: str, params: Any, **_: Any) -> Any:
        return copy.deepcopy(answer)

    manager._send_request = send  # type: ignore[method-assign]
    if recorded:
        task = McpTaskInfo(task_id="t", status="working")
        manager._record_task(_DOWNSTREAM, task, tool_id="o")
    args: dict[str, Any] = {"tool_id": f"{_DOWNSTREAM}::run", **_correlations()}
    if requested:
        args["task"] = {"enabled": True}
    out = await server._gateway_tools.invoke(args)
    await server.shutdown()
    return out, server._policy_manager


def _sized_as_returned(out: Any, policy: Any) -> bool:
    return (
        out.raw_size_estimate
        == policy.process_output(out.result, redact=False)["raw_size"]
    )


@pytest.mark.asyncio
async def test_task_handling_follows_the_calls_effective_task_mode(
    tmp_path: Path,
) -> None:
    """Round-15 codex F001, claude F003: over taskSupport x requested x shape
    (nested, flat, not a task): a call that is not a task gets its answer
    back whole, sized as returned; a task call's dropped hint is neither
    returned nor sized; content is never lost to a registry lookup."""
    kept = [{"type": "text", "text": "fresh report"}]
    cases = 0
    for support, requested, shape, recorded in itertools.product(
        ("required", "optional", "forbidden", None),
        (True, False),
        ("nested", "flat", "data"),
        (True, False),
    ):
        name, outs = (support, requested, shape, recorded), []
        for s in ("violetcanaryrejected", "violetcanaryrejected12345"):
            answer = {
                "nested": {"task": {"taskId": "t", "ttl": s}, "content": kept},
                "flat": {"task_id": "t", "ttl": s, "content": kept},
                "data": {"content": kept, "structuredContent": {"ttl": s}},
            }[shape]
            root = tmp_path / f"{support}{requested}{shape}{recorded}{len(s)}"
            out = await _invoke_as(root, support, requested, answer, recorded)
            outs.append((s, answer, *out))
        if requested and support in ("forbidden", None):
            assert not any(o.ok for _, _, o, _ in outs), name
            continue
        as_task = (support == "required" or requested) and shape != "data"
        for s, answer, out, policy in outs:
            if as_task:
                assert out.result is None and out.task is not None, name
                assert s not in out.model_dump_json(), name
            else:
                assert out.result == answer and out.task is None, name
                assert _sized_as_returned(out, policy), name
        if as_task:
            assert len({o.raw_size_estimate for _, _, o, _ in outs}) == 1, name
        cases += 1
    assert cases == 36, cases


@pytest.mark.asyncio
async def test_sync_result_survives_cached_task_id(tmp_path: Path) -> None:
    """Round-15 codex F001's falsifier: a `forbidden` tool's synchronous
    answer naming a recorded task id keeps its content (as main does)."""
    payload = {"task_id": "t", "content": [{"type": "text", "text": "fresh report"}]}
    out, _ = await _invoke_as(tmp_path / "f", "forbidden", False, payload)
    assert out.ok and out.result == payload and out.task is None


@pytest.mark.asyncio
async def test_an_unrequested_task_answer_is_sized_as_returned(tmp_path: Path) -> None:
    """Round-15 claude F003, restated for rev 17's rule: an `optional` tool,
    no task requested, a colliding task answer: not a task to pmcp, returned
    whole and sized from exactly what is returned."""
    for s in ("ghp_a", "ghp_a" + "x" * 40):
        answer = {"task": {"taskId": "t", "status": "working", "ttl": s}}
        out, policy = await _invoke_as(
            tmp_path / str(len(s)), "optional", False, answer
        )
        assert out.ok and out.task is None and out.result == answer
        assert _sized_as_returned(out, policy)
