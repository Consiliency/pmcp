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
import hashlib
import json
import logging
import string
import typing
from pathlib import Path
from typing import Any

import jsonschema
import pytest
from mcp.types import CallToolRequestParams
from pydantic import BaseModel, ValidationError

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

#: Two high-entropy sentinels of different length (ASCII alphanumerics, so no
#: renderer escapes them out of a substring search).
_SENTINELS = (
    "Sq" + hashlib.sha256(b"pmcp-297-a").hexdigest()[:22] + "Zx",
    "Sq" + hashlib.sha256(b"pmcp-297-b").hexdigest()[:38] + "Zx",
)

_GATE_PREFIX = "Input validation error: $"
_MODEL_PREFIX = "Invalid arguments: $"


#: pydantic truncates a long `input_value` repr in the middle
#: (`'Sq3b2ee216cab4bcafa8599...e216cab4bcafa85997Zx'`), so the whole sentinel
#: never appears in such a leak; every window of this many characters is
#: searched for instead (48 bits of hex: no accidental match).
_WINDOW = 12


def _forbidden(sentinel: str) -> set[str]:
    """Every window of the sentinel, and each hash of it, a log or record
    could carry."""
    forms = {sentinel[i : i + _WINDOW] for i in range(len(sentinel) - _WINDOW + 1)}
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
    return [
        (label, value)
        for label, value in candidates
        if not jsonschema.validators.validator_for(node)(node).is_valid(value)
    ]


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
    jsonschema.validate(arguments, tool.input_schema)
    return arguments


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
                    value = dict(_invalid_values(node, s))[label]
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
                            arguments[field_name] = dict(_invalid_values(node, s))[
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


class _Observed(typing.NamedTuple):
    response: str
    log: str
    raw_log: str
    audit: list[dict[str, Any]]
    raw_audit: str


async def _observe(
    server: GatewayServer,
    audit_path: Path | None,
    caplog: pytest.LogCaptureFixture,
    case: _Case,
    sentinel: str,
) -> _Observed:
    arguments = case.build(sentinel)
    before_log = len(caplog.records)
    before_audit = audit_path.read_text() if audit_path and audit_path.exists() else ""
    result = await _call(server, case.tool, arguments)
    records = caplog.records[before_log:]
    raw_log = "\n".join(
        formatter.format(record)
        for record in records
        for formatter in _pmcp_formatters()
    )
    log = "\n".join(
        _normalized(record, formatter)
        for record in records
        for formatter in _pmcp_formatters()
    )
    raw_audit = (audit_path.read_text() if audit_path else "")[len(before_audit) :]
    audit = [_stable(json.loads(line)) for line in raw_audit.splitlines() if line]
    return _Observed(
        response=_rejection(result, case.layer),
        log=log,
        raw_log=raw_log,
        audit=audit,
        raw_audit=raw_audit,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("audited", [False, True], ids=["plain", "scoped-audit"])
async def test_no_rejected_argument_value_reaches_a_response_log_or_audit(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, audited: bool
) -> None:
    caplog.set_level(logging.DEBUG)
    server, audit_path = _server(tmp_path, audited=audited)
    for case in _CASES:
        seen = [
            await _observe(server, audit_path, caplog, case, sentinel)
            for sentinel in _SENTINELS
        ]
        for sentinel, observed in zip(_SENTINELS, seen):
            for form in _forbidden(sentinel):
                assert form not in observed.response, (case.label, "response")
                assert form not in observed.raw_log, (
                    case.label,
                    "log",
                    observed.raw_log,
                )
                assert form not in observed.raw_audit, (case.label, "audit")
        # Useful: the rejection names where, and why.
        _useful(seen[0].response, case.layer, case.expected)
        # Nothing else about the value -- length, count or hash -- either.
        assert seen[0].response == seen[1].response, case.label
        assert seen[0].log == seen[1].log, (case.label, seen[0].log, seen[1].log)
        assert seen[0].audit == seen[1].audit, case.label
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
        assert s in str(error)  # the raw text does carry it
    return errors


class _RaisingTools:
    def __init__(self, error: BaseException) -> None:
        self.error = error

    def __getattr__(self, name: str) -> Any:
        async def handler(*args: Any, **kwargs: Any) -> dict:
            raise self.error

        return handler


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "index", [0, 1, 2], ids=["downstream-model", "argument-model", "jsonschema"]
)
async def test_a_validation_error_raised_by_a_handler_is_described_not_echoed(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, index: int
) -> None:
    caplog.set_level(logging.DEBUG)
    server, _ = _server(tmp_path, audited=False)
    tool = _tools()["gateway.catalog_search"]
    texts, logs = [], []
    for s in _SENTINELS:
        server._gateway_tools = _RaisingTools(_handler_validation_errors(s)[index])  # type: ignore[assignment]
        start = len(caplog.records)
        result = await _call(server, tool.name, {"query": "q"})
        text = "".join(block.text for block in result.content)
        payload = json.loads(text)
        assert payload["error"] is True, text
        log = "\n".join(
            formatter.format(record)
            for record in caplog.records[start:]
            for formatter in _pmcp_formatters()
        )
        for form in _forbidden(s):
            assert form not in text and form not in log, form
        # Not catalog_search's own argument model: a validation error, not
        # "invalid arguments".
        assert payload["message"].startswith("Validation error: $"), text
        assert "validation error for gateway.catalog_search: $" in log
        texts.append(text)
        logs.append(
            "\n".join(
                _normalized(r, f)
                for r in caplog.records[start:]
                for f in _pmcp_formatters()
            )
        )
    assert texts[0] == texts[1] and logs[0] == logs[1]


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


def test_a_model_phrase_reads_only_constraint_context() -> None:
    """Every placeholder a phrase names is a constraint the model declares;
    none is a value-derived `ctx` entry pydantic also offers."""
    from pmcp.argument_errors import _CONSTRAINT_CONTEXT, _MODEL_PHRASES

    placeholders = {
        name
        for phrase in _MODEL_PHRASES.values()
        for _, name, _, _ in string.Formatter().parse(phrase)
        if name
    }
    assert placeholders <= _CONSTRAINT_CONTEXT
    assert _CONSTRAINT_CONTEXT == {
        "min_length",
        "max_length",
        "pattern",
        "expected",
        "gt",
        "ge",
        "lt",
        "le",
    }
    # Offered by pydantic, derived from the input: never read.
    for derived in (
        "actual_length",
        "error",
        "tag",
        "input",
        "attribute",
        "class_name",
    ):
        assert derived not in _CONSTRAINT_CONTEXT
