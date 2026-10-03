"""Consiliency/pmcp#326: every fixed operator-facing auth message survives
pmcp's own diagnostic sanitiser unchanged -- every message `auth.py` and
`transport/http.py` raise or send as an auth rejection; not the metadata diagnostics lists, `UNVERIFIED_URL_CAVEAT`, `fetch_json_metadata`'s strings, the plain 413/429/504 bodies or the log templates,
which the plan leaves out of scope (its non-goals).

`ResourceServerAuthError.__init__` runs its description through
`sanitize_auth_diagnostic`, so a fixed text the sanitiser reads as a
credential was stored mangled: "Token could not be verified with the
published key." became "Token [REDACTED] not be verified with the published
key.".

Round 4: the texts live in ONE registry, `pmcp.auth.AuthMessage`, and the
code cannot use anything else -- the class fix after three rounds in which a
static collector was beaten by a new code shape. So:

1. every registry member, rendered with sample configuration fields,
   survives the redactor's own rules (`_sanitize_base`), the additive rules
   (`redact_additive`, #234) and the composed `sanitize_auth_diagnostic`;
2. the auth error classes and `_auth_response` raise `TypeError` for a
   message that is not a member (or the narrow `pyjwt_text` pass-through),
   whatever shape produced it -- a literal, a variable, a subclass constant,
   a walrus, an alias;
3. a static check -- syntax only, no resolver -- requires every raise and
   `_reject` site in `auth.py` and `transport/http.py` to name
   `AuthMessage.<NAME>` directly, which covers sites no test reaches;
4. logs are checked end to end through the real `setup_logging`.

What is guaranteed, and what is a guard rail (round 8): the guarantee is
the RUNTIME checks in (2) -- identity membership, exact placeholder fields,
field-value shapes, and arity at the constructors and `_auth_response` --
through which every raised or sent auth message passes (the scope above),
plus the
startup refusal of configuration a message could not carry. The static
check in (3) is a guard rail for the listed shapes only (see
`_site_violations`); it is not exhaustive against deliberate obfuscation.

A whole `WWW-Authenticate` header is the one deliberate exception, pinned
below: the base `Bearer` rule redacts the challenge's first parameter (it
cannot tell `Bearer resource="..."` from `Bearer <token>`), the base rules
are frozen (additive-only, #234), and pmcp never runs its own challenge
through the sanitiser. Its parameters must survive one by one, and pmcp's own
challenge parser must read them back intact.
"""

from __future__ import annotations

import ast
import asyncio
import re
import functools
import string
from pathlib import Path
import time
from typing import Any, Callable
from unittest.mock import AsyncMock, MagicMock, patch

import jwt
import pytest
from starlette.testclient import TestClient

from pmcp import auth as auth_mod
from pmcp.auth import (
    AsyncJWKS,
    AuthMessage,
    AuthText,
    PyJwtText,
    ResourceServerAuthError,
    ResourceServerJWKSUnavailable,
    _sanitize_base,
    auth_messages,
    parse_www_authenticate,
    pyjwt_text,
    render_auth_message,
    sanitize_auth_diagnostic,
)
from pmcp.redaction_additive import redact_additive
from pmcp.transport import http as http_mod
from pmcp.transport.http import _auth_response, create_http_app

_SAMPLE_FIELDS = {
    "url": "https://issuer.example/.well-known/jwks.json",
    "scopes": "mcp:read mcp:write",
}
_MESSAGES = auth_messages()
_MODULES = {"auth.py": auth_mod, "transport/http.py": http_mod}


def _layers(text: str) -> dict[str, str]:
    return {
        "base": _sanitize_base(text),
        "additive": redact_additive(text),
        "composed": sanitize_auth_diagnostic(text, max_length=None),
    }


def _placeholders(member: str) -> set[str]:
    return {f for _, f, _, _ in string.Formatter().parse(member) if f}


def _fields(member: AuthText) -> dict[str, str]:
    """Sample values for exactly the member's placeholders."""
    names = {f for _, f, _, _ in string.Formatter().parse(member) if f}
    return {k: v for k, v in _SAMPLE_FIELDS.items() if k in names}


def _rendered(member: AuthText) -> str:
    return render_auth_message(member, **_fields(member))


# --- 1. the registry: every member survives the sanitiser -----------------------


def test_the_registry_is_complete_and_typed() -> None:
    """The registry is the list now (no derivation to shrink): 29 members on
    the round-4 spike, each an `AuthText`, no two with the same text."""
    assert len(_MESSAGES) == 32, sorted(_MESSAGES)
    assert len(set(_MESSAGES.values())) == len(_MESSAGES)
    assert all(type(v) is AuthText for v in _MESSAGES.values())
    assert _MESSAGES["KEY_CANNOT_VERIFY_TOKEN"] == (
        "The published key cannot verify this token."
    )


@pytest.mark.parametrize("name", sorted(_MESSAGES))
def test_every_registry_message_survives_the_sanitiser(name: str) -> None:
    text = _rendered(_MESSAGES[name])
    mangled = {k: v for k, v in _layers(text).items() if v != text}
    assert mangled == {}, f"AuthMessage.{name}: {text!r} is rewritten: {mangled}"


@pytest.mark.parametrize("name", sorted(_MESSAGES))
def test_a_stored_description_is_the_text_written(name: str) -> None:
    """End to end through the class that stores it (and sanitises it)."""
    text = _rendered(_MESSAGES[name])
    exc = ResourceServerAuthError(
        "invalid_token", _MESSAGES[name], **_fields(_MESSAGES[name])
    )
    assert exc.description == text and str(exc) == text


# --- 2. the constructors refuse anything else -----------------------------------


class _ClassConstant(ResourceServerAuthError):
    message = "Token could not be verified."  # claude round 3 B1 / codex 1

    def __init__(self) -> None:
        super().__init__("invalid_token", self.message)  # type: ignore[arg-type]


class _InitAttribute(ResourceServerAuthError):
    def __init__(self) -> None:
        self.message = "Token expired."
        super().__init__("invalid_token", self.message)  # type: ignore[arg-type]


class _AugmentedParameter(ResourceServerAuthError):
    def __init__(self, message: str = "Empty token.") -> None:
        message += " Token expired."
        super().__init__("invalid_token", message)  # type: ignore[arg-type]


def _walrus_message() -> ResourceServerAuthError:
    message = "Empty token."
    [(message := "Token expired.") for _ in range(1)]  # codex round 3 (2)
    return ResourceServerAuthError("invalid_token", message)  # type: ignore[arg-type]


_INNER = ResourceServerAuthError
_OUTER = _INNER  # codex round 3 (3): an alias raised from a shadowing scope


def _aliased_message() -> ResourceServerAuthError:
    _INNER = ValueError  # noqa: F841 - the shadow the static resolver got wrong
    return _OUTER("invalid_token", "Token expired.")  # type: ignore[arg-type]


_REFUSED: dict[str, Callable[[], Any]] = {
    # rounds 1-3 shapes
    "literal": lambda: ResourceServerAuthError("invalid_token", "Token expired."),
    "keyword": lambda: ResourceServerAuthError(
        "invalid_token", description="Token audience mismatch."
    ),
    "percent": lambda: ResourceServerAuthError(
        "invalid_token", "JWKS URL is required for token %s." % "checks"
    ),
    "format": lambda: ResourceServerAuthError(
        "invalid_token", "Token {} rejected.".format("checks")
    ),
    "f_string": lambda: ResourceServerAuthError("invalid_token", f"Token {1}"),
    "jwks_unavailable_str": lambda: ResourceServerJWKSUnavailable("Token expired."),
    "plain_str_subclass_of_str": lambda: ResourceServerAuthError(
        "invalid_token", str(AuthMessage.EMPTY_TOKEN)
    ),
    # round 3 shapes
    "class_constant": _ClassConstant,
    "init_attribute": _InitAttribute,
    "augmented_parameter": _AugmentedParameter,
    "comprehension_walrus": _walrus_message,
    "shadowed_alias": _aliased_message,
}


@pytest.mark.parametrize("shape", sorted(_REFUSED))
def test_a_message_outside_the_registry_is_refused_at_construction(shape: str) -> None:
    with pytest.raises(TypeError, match="AuthMessage member"):
        _REFUSED[shape]()  # type: ignore[no-untyped-call]


def test_an_auth_response_body_outside_the_registry_is_refused() -> None:
    with pytest.raises(TypeError, match="AuthMessage member"):
        _auth_response(401, "Unauthorized")  # type: ignore[arg-type]
    assert _auth_response(401, AuthMessage.UNAUTHORIZED).body == b"Unauthorized"


def test_the_pyjwt_pass_through_is_narrow() -> None:
    with pytest.raises(TypeError, match="fixed-text pyjwt error"):
        pyjwt_text(jwt.InvalidTokenError("Token payload: secret"))
    with pytest.raises(TypeError):
        pyjwt_text(ValueError("Token expired."))
    kept = jwt.ExpiredSignatureError("Signature has expired")
    exc = ResourceServerAuthError("invalid_token", pyjwt_text(kept))
    assert exc.description == "Signature has expired"


# --- round 5: membership is identity; fields are exactly the placeholders ------


_NOT_MEMBERS: dict[str, Callable[[], Any]] = {
    # codex round 4 (1) / claude N2: the right type, not a member
    "minted_auth_text": lambda: ResourceServerAuthError(
        "invalid_token", AuthText("Token expired.")
    ),
    "str_new_auth_text": lambda: ResourceServerAuthError(
        "invalid_token", str.__new__(AuthText, "Token expired.")
    ),
    "copy_of_a_member": lambda: ResourceServerAuthError(
        "invalid_token", AuthText(str(AuthMessage.EMPTY_TOKEN))
    ),
    "str_new_pyjwt_text": lambda: ResourceServerAuthError(
        "invalid_token", str.__new__(PyJwtText, "Token expired.")
    ),
}


@pytest.mark.parametrize("shape", sorted(_NOT_MEMBERS))
def test_a_value_of_the_right_type_that_is_not_a_member_is_refused(shape: str) -> None:
    with pytest.raises(TypeError):
        _NOT_MEMBERS[shape]()


def test_pyjwt_text_cannot_be_constructed_directly() -> None:
    with pytest.raises(TypeError, match="minted only by pyjwt_text"):
        PyJwtText("Token expired.")


_BAD_FIELDS: dict[str, Callable[[], Any]] = {
    # claude round 4 B1
    "missing": lambda: ResourceServerJWKSUnavailable(AuthMessage.JWKS_FETCH_FAILED),
    "misnamed": lambda: ResourceServerJWKSUnavailable(
        AuthMessage.JWKS_FETCH_FAILED, uri="https://issuer.example/jwks"
    ),
    "extra": lambda: ResourceServerAuthError(
        "invalid_token", AuthMessage.EMPTY_TOKEN, scopes="admin"
    ),
    "field_on_pyjwt": lambda: ResourceServerAuthError(
        "invalid_token",
        pyjwt_text(jwt.ExpiredSignatureError("Signature has expired")),
        url="x",
    ),
}


@pytest.mark.parametrize("shape", sorted(_BAD_FIELDS))
def test_fields_must_be_exactly_the_placeholders(shape: str) -> None:
    with pytest.raises(TypeError, match="fields|no fields"):
        _BAD_FIELDS[shape]()


async def test_a_programming_error_in_the_fetch_is_not_a_503() -> None:
    """A `TypeError` (e.g. a refused message) inside the JWKS fetch used to be
    re-wrapped as `JWKS_FETCH_FAILED` and open the backoff for every waiter.
    It now propagates, and no backoff opens."""
    jwks = AsyncJWKS("https://issuer.example/jwks.json")

    async def broken_fetch() -> dict[str, object]:
        raise TypeError("auth messages must be an AuthMessage member, not str")

    jwks._fetch = broken_fetch  # type: ignore[method-assign]
    with pytest.raises(TypeError):
        await jwks.get()
    assert jwks._last_refresh_failure == float("-inf")


async def test_a_programming_error_inside_fetch_itself_is_not_rewrapped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    jwks = AsyncJWKS("https://issuer.example/jwks.json")

    def broken_session(*_a: Any, **_k: Any) -> Any:
        raise TypeError("programming error")

    monkeypatch.setattr(auth_mod.aiohttp, "ClientSession", broken_session)
    with pytest.raises(TypeError, match="programming error"):
        await jwks._fetch()


# --- round 6: one guard; field values must have their placeholder's shape --------

_PRETEND_URL = "Token could not be verified."  # claude round 5 N2


def _reason_bound_to_prose() -> Any:
    reason = "Token expired."  # codex round 5 (2)
    return ResourceServerJWKSUnavailable(AuthMessage.JWKS_FETCH_FAILED, url=reason)


_BAD_VALUES: dict[str, Callable[[], Any]] = {
    "url_variable_bound_to_prose": _reason_bound_to_prose,
    "url_constant_bound_to_prose": lambda: ResourceServerJWKSUnavailable(
        AuthMessage.JWKS_FETCH_FAILED, url=_PRETEND_URL
    ),
    "redirect_site_prose": lambda: ResourceServerJWKSUnavailable(
        AuthMessage.JWKS_REDIRECT_REFUSED, url="Token expired."
    ),
    "url_relative": lambda: ResourceServerJWKSUnavailable(
        AuthMessage.JWKS_FETCH_FAILED, url="/jwks.json"
    ),
    "url_with_space": lambda: ResourceServerJWKSUnavailable(
        AuthMessage.JWKS_FETCH_FAILED, url="https://issuer.example/ Token x"
    ),
    "url_not_str": lambda: ResourceServerJWKSUnavailable(
        AuthMessage.JWKS_FETCH_FAILED,
        url=AuthMessage.EMPTY_TOKEN,  # type: ignore[arg-type]
    ),
    "scopes_quoted_prose": lambda: ResourceServerAuthError(
        "insufficient_scope", AuthMessage.MISSING_SCOPES, scopes='Token "expired"'
    ),
    "scopes_empty": lambda: ResourceServerAuthError(
        "insufficient_scope", AuthMessage.MISSING_SCOPES, scopes=""
    ),
    "scopes_double_space": lambda: ResourceServerAuthError(
        "insufficient_scope", AuthMessage.MISSING_SCOPES, scopes="read  write"
    ),
}


@pytest.mark.parametrize("shape", sorted(_BAD_VALUES))
def test_a_field_value_without_its_placeholder_shape_is_refused(shape: str) -> None:
    with pytest.raises(TypeError, match="expected shape"):
        _BAD_VALUES[shape]()


def test_real_field_values_are_accepted() -> None:
    for url in (
        "https://issuer.example/.well-known/jwks.json",
        "http://127.0.0.1:8080/jwks",
        "https://[2606:4700::1]:8443/jwks?token=%5BREDACTED%5D",
    ):
        ResourceServerJWKSUnavailable(AuthMessage.JWKS_FETCH_FAILED, url=url)
    for scopes in ("mcp:read", "mcp:read mcp:write", "secret:read admin"):
        ResourceServerAuthError(
            "insufficient_scope", AuthMessage.MISSING_SCOPES, scopes=scopes
        )


def test_every_registry_placeholder_has_a_validator_and_no_odd_braces() -> None:
    """Claude round 5 N4: only named identifier fields -- no `{}`, no escaped
    `{{`/`}}`, no index, attribute, conversion or format spec -- and every
    field name has a shape validator (an unknown one fails at import)."""
    for name, member in _MESSAGES.items():
        for literal, field, spec, conv in string.Formatter().parse(member):
            assert "{" not in literal and "}" not in literal, name
            if field is None:
                continue
            assert field.isidentifier() and not spec and conv is None, (name, field)
            assert field in auth_mod._FIELD_VALIDATORS, (name, field)


def test_an_auth_response_body_of_the_right_type_that_is_not_a_member_is_refused() -> (
    None
):
    """Codex round 5 (1): `_auth_response` checked type, not membership."""
    for body in (
        AuthText("Token expired."),
        str.__new__(AuthText, "Token expired."),
        AuthText(str(AuthMessage.UNAUTHORIZED)),
    ):
        with pytest.raises(TypeError):
            _auth_response(401, body)


def test_isinstance_auth_text_is_only_used_to_enumerate_the_registry() -> None:
    """One guard: membership is decided by `render_auth_message` (identity).
    `isinstance(..., AuthText)` anywhere else would be a second, weaker
    guard -- the round-5 hole in `_auth_response`."""
    src = Path(auth_mod.__file__ or "").parent
    found = []
    for path in sorted(src.rglob("*.py")):
        tree = ast.parse(path.read_text())
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for call in ast.walk(fn):
                if (
                    isinstance(call, ast.Call)
                    and getattr(call.func, "id", "") == "isinstance"
                    and len(call.args) == 2
                    and "AuthText" in ast.unparse(call.args[1])
                    and fn.name != "auth_messages"
                ):
                    found.append(f"{path.relative_to(src)}:{call.lineno} in {fn.name}")
    assert found == [], found


async def test_the_jwks_redirect_path_raises_its_registry_message() -> None:
    """Claude round 5 N1: nothing drove a JWKS redirect, so a misnamed field
    there would have passed every test and been a 500 in production."""

    async def handle(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        await reader.readuntil(b"\r\n\r\n")
        writer.write(
            b"HTTP/1.1 302 Found\r\nLocation: http://127.0.0.1:1/x\r\n"
            b"Content-Length: 0\r\nConnection: close\r\n\r\n"
        )
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        jwks = AsyncJWKS("https://issuer.example/jwks.json")
        jwks._raw_url = f"http://127.0.0.1:{port}/jwks"
        with pytest.raises(ResourceServerJWKSUnavailable) as caught:
            await asyncio.wait_for(jwks._fetch(), 10)
    finally:
        server.close()
        await server.wait_closed()
    assert caught.value.description == (
        "JWKS endpoint returned a redirect for https://issuer.example/jwks.json; "
        "refusing to follow."
    )


# --- 3. every site names a registry member: a static check, no resolver ----------

# Callee -> (index, keyword) of its message argument. A `raise` of any other
# callee in these two modules fails the check.
_SITES: dict[str, tuple[int, str | None]] = {
    "ResourceServerAuthError": (1, "description"),
    "ResourceServerJWKSUnavailable": (0, "description"),
    "ValueError": (0, None),
    "HTTPError": (2, "msg"),
    "_reject": (1, "body"),
    "_auth_response": (1, "body"),
}
# The only exception classes these modules may define.
_EXCEPTION_CLASSES = {"ResourceServerAuthError", "ResourceServerJWKSUnavailable"}
_ERROR_CODES = {"invalid_token", "insufficient_scope", "temporarily_unavailable"}
# The registry's guards, the only functions that may raise `TypeError`.
_GUARDS = {
    "render_auth_message",
    "pyjwt_text",
    "_check_registry_fields",
    "_auth_response",  # refuses non-mapping headers
}


def _is_member(node: ast.expr | None) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "AuthMessage"
        and node.attr in _MESSAGES
    )


def _is_pyjwt_pass_through(node: ast.expr | None) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "pyjwt_text"
        and len(node.args) == 1
        and isinstance(node.args[0], ast.Name)
    )


def _parents(tree: ast.Module) -> dict[int, ast.AST]:
    return {id(c): n for n in ast.walk(tree) for c in ast.iter_child_nodes(n)}


def _enclosing_handlers(
    node: ast.AST, parents: dict[int, ast.AST]
) -> list[ast.ExceptHandler]:
    out: list[ast.ExceptHandler] = []
    cur = parents.get(id(node))
    while cur is not None:
        if isinstance(cur, ast.ExceptHandler):
            out.append(cur)
        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            break  # a handler outside the function does not bind inside it
        cur = parents.get(id(cur))
    return out


def _rebinds(handler: ast.ExceptHandler, name: str) -> bool:
    """Whether anything inside the handler body rebinds ``name``."""
    return any(
        isinstance(n, ast.Name)
        and n.id == name
        and isinstance(n.ctx, (ast.Store, ast.Del))
        for stmt in handler.body
        for n in ast.walk(stmt)
    )


def _bound_by_handler(
    node: ast.AST,
    name: str,
    parents: dict[int, ast.AST],
    type_source: str | None = None,
) -> bool:
    """``name`` is the `except ... as name` of a handler that lexically
    encloses ``node`` (optionally of the given exception type), and nothing in
    that handler rebinds it."""
    for handler in _enclosing_handlers(node, parents):
        if handler.name != name:
            continue
        if type_source is not None and (
            handler.type is None or ast.unparse(handler.type) != type_source
        ):
            return False
        return not _rebinds(handler, name)
    return False


# Exact arity per message site (codex round 6): the message argument is the
# only message-bearing one. (positional count, allowed keywords). The auth
# error classes may pass the message as `description=` instead, and carry
# their placeholder fields as further keywords (checked separately).
_ARITY: dict[str, tuple[int, frozenset[str]]] = {
    "ValueError": (1, frozenset()),
    "HTTPError": (5, frozenset()),  # (url, code, msg, hdrs, fp)
    "ResourceServerAuthError": (2, frozenset({"description"})),
    "ResourceServerJWKSUnavailable": (1, frozenset({"description"})),
    "_auth_response": (2, frozenset({"headers"})),
    "_reject": (2, frozenset({"headers"})),
}


def _arity_violation(name: str, node: ast.Call, keyword: str | None) -> str:
    positional, allowed = _ARITY[name]
    if any(isinstance(a, ast.Starred) for a in node.args) or any(
        k.arg is None for k in node.keywords
    ):
        return "star-arguments"
    count = len(node.args) + sum(1 for k in node.keywords if k.arg == keyword)
    if count != positional:
        return f"{count} message-position arguments, expected exactly {positional}"
    stray = {
        k.arg
        for k in node.keywords
        if k.arg not in allowed
        and k.arg != keyword
        and name not in ("ResourceServerAuthError", "ResourceServerJWKSUnavailable")
    }
    return f"unexpected keywords {sorted(stray)}" if stray else ""


# Methods that change an exception's payload in place (codex round 7).
_MUTATING_METHODS = {
    "__setattr__",
    "__delattr__",
    "__init__",
    "add_note",
    "with_traceback",
}


def _root_name(node: ast.expr) -> str | None:
    while isinstance(node, (ast.Attribute, ast.Subscript)):
        node = node.value
    return node.id if isinstance(node, ast.Name) else None


def _payload_mutations(rel: str, tree: ast.Module) -> list[str]:
    """Inside an `except ... as <name>` handler, nothing may change the
    bound exception's payload: no assignment, augmented assignment or `del`
    whose target is an attribute or item of `<name>` (`exc.args = ...`,
    `exc.description = ...`, `exc.__dict__[...] = ...`), no
    `setattr`/`delattr`/`object.__setattr__(<name>, ...)`, and no mutating
    method on it. A permitted `raise <name>` then re-raises what was
    raised -- a registry-checked message -- and nothing else."""
    out: list[str] = []
    for handler in ast.walk(tree):
        if not (isinstance(handler, ast.ExceptHandler) and handler.name):
            continue
        bound = handler.name
        for node in ast.walk(handler):
            targets: list[ast.expr] = []
            if isinstance(node, ast.Assign):
                targets = list(node.targets)
            elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
                targets = [node.target]
            elif isinstance(node, ast.Delete):
                targets = list(node.targets)
            flat: list[ast.expr] = []
            while targets:
                t = targets.pop()
                if isinstance(t, (ast.Tuple, ast.List)):
                    targets.extend(t.elts)
                elif isinstance(t, ast.Starred):
                    targets.append(t.value)
                else:
                    flat.append(t)
            for t in flat:
                if (
                    isinstance(t, (ast.Attribute, ast.Subscript))
                    and _root_name(t) == bound
                ):
                    out.append(
                        f"{rel}:{node.lineno} {ast.unparse(t)[:40]}: payload mutation"
                    )
            if isinstance(node, ast.Call):
                fn = node.func
                fname = ast.unparse(fn)
                if (
                    fname
                    in {
                        "setattr",
                        "delattr",
                        "object.__setattr__",
                        "object.__delattr__",
                        "BaseException.__init__",
                        "Exception.__init__",
                    }
                    and node.args
                    and _root_name(node.args[0]) == bound
                ):
                    out.append(
                        f"{rel}:{node.lineno} {fname}({bound}, ...): payload mutation"
                    )
                if (
                    isinstance(fn, ast.Attribute)
                    and fn.attr in _MUTATING_METHODS
                    and _root_name(fn.value) == bound
                ):
                    out.append(f"{rel}:{node.lineno} {fname}(...): payload mutation")
    return out


# Builtin exceptions cannot refuse a message, so their message argument
# must be `render_auth_message(AuthMessage.<NAME>, **fields)` (round 9).
_BUILTIN_SITES = {"ValueError", "HTTPError"}


def _rendered_member(arg: ast.expr | None) -> ast.Call | None:
    """``arg`` if it is `render_auth_message(AuthMessage.<NAME>, ...)` with
    exactly one positional and no `**`, else None."""
    if (
        isinstance(arg, ast.Call)
        and isinstance(arg.func, ast.Name)
        and arg.func.id == "render_auth_message"
        and len(arg.args) == 1
        and _is_member(arg.args[0])
        and all(k.arg is not None for k in arg.keywords)
    ):
        return arg
    return None


def _render_field_violations(rel: str, render: ast.Call) -> list[str]:
    member = render.args[0]
    assert isinstance(member, ast.Attribute)
    out: list[str] = []
    given = {k.arg for k in render.keywords}
    wanted = _placeholders(_MESSAGES[member.attr])
    if given != wanted:
        out.append(
            f"{rel}:{render.lineno} AuthMessage.{member.attr} fields "
            f"{sorted(k for k in given if k)} != placeholders {sorted(wanted)}"
        )
    for kw in render.keywords:
        if not isinstance(kw.value, (ast.Name, ast.Attribute)):
            out.append(
                f"{rel}:{render.lineno} field {kw.arg}= is "
                f"{ast.unparse(kw.value)[:40]}, not a runtime name"
            )
    return out


def _site_violations(rel: str, tree: ast.Module) -> list[str]:
    """Syntax only: every raise/`_reject` site passes `AuthMessage.<NAME>`
    directly, its keyword fields are exactly the member's placeholders and
    each is a runtime name, a named raise re-raises its own enclosing
    handler's exception, and `pyjwt_text` appears only in the allowlisted
    handler. No resolver, so no scope rule to get wrong.

    A guard rail, not a guarantee (round 8). It refuses exactly these
    shapes, each pinned in `_STATIC_SHAPES`: (1) a raise of anything but a
    known site or a handler-bound re-raise, and any new exception class;
    (2) a message site whose message is not `AuthMessage.<NAME>`; (3) wrong
    arity -- extra positionals, stray keywords, star-arguments; (4) field
    names that differ from the member's placeholders, and literal field
    values; (5) `raise <name>` outside the handler that binds it, or after
    rebinding it; (6) `pyjwt_text` outside `except _FIXED_TEXT_CLAIM_ERRORS
    as <name>`; (7) payload mutation of an except-bound exception. It is not
    exhaustive against deliberate obfuscation (`getattr` chains, `exec`,
    `vars(exc)[...]`, a helper that mutates its argument); the runtime
    registry checks are what every raised or sent auth message passes
    through (the module docstring's scope).

    Known limit (claude round 5, N2): a field value that is a *name bound to
    prose* (`url=_PRETEND_URL`) is syntactically a runtime name. That is
    caught at run time instead: every field value must have its
    placeholder's shape (an absolute http(s) URL, an RFC 6749 scope list)."""
    out: list[str] = []
    parents = _parents(tree)
    guard_raises = {
        id(n)
        for fn in ast.walk(tree)
        if (isinstance(fn, ast.FunctionDef) and fn.name in _GUARDS)
        or (isinstance(fn, ast.ClassDef) and fn.name == "PyJwtText")
        for n in ast.walk(fn)
        if isinstance(n, ast.Raise)
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            bases = {ast.unparse(b) for b in node.bases}
            if node.name not in _EXCEPTION_CLASSES and (
                bases & _EXCEPTION_CLASSES
                or any(b.endswith(("Error", "Exception")) for b in bases)
            ):
                out.append(
                    f"{rel}:{node.lineno} class {node.name}: new exception class"
                )
    out.extend(_payload_mutations(rel, tree))
    for node in ast.walk(tree):
        if isinstance(node, ast.Raise) and node.exc is not None:
            exc = node.exc
            if isinstance(exc, ast.Name):
                if not _bound_by_handler(node, exc.id, parents):
                    out.append(f"{rel}:{node.lineno} raise {exc.id}: not a re-raise")
                continue
            if not (isinstance(exc, ast.Call) and isinstance(exc.func, ast.Name)):
                out.append(f"{rel}:{node.lineno} raise {ast.unparse(exc)[:40]}")
                continue
            if exc.func.id == "TypeError" and id(node) in guard_raises:
                continue  # the registry's own refusal, a programmer error
            if exc.func.id not in _SITES:
                out.append(f"{rel}:{node.lineno} raise {exc.func.id}: unknown callee")
        if isinstance(node, ast.Call):
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name == "pyjwt_text" and not (
                len(node.args) == 1
                and isinstance(node.args[0], ast.Name)
                and _bound_by_handler(
                    node, node.args[0].id, parents, "_FIXED_TEXT_CLAIM_ERRORS"
                )
            ):
                out.append(
                    f"{rel}:{node.lineno} pyjwt_text(...) outside "
                    "`except _FIXED_TEXT_CLAIM_ERRORS as <name>`"
                )
            if name not in _SITES:
                continue
            index, keyword = _SITES[name]
            arg = next((k.value for k in node.keywords if k.arg == keyword), None)
            if arg is None and len(node.args) > index:
                arg = node.args[index]
            if name in _BUILTIN_SITES:
                # round 9 (claude + codex round 8): a builtin cannot check
                # its message, so it must be rendered at the site
                ok = False
                render = _rendered_member(arg)
                if render is not None:
                    ok = True
                    out.extend(_render_field_violations(rel, render))
            else:
                ok = _is_member(arg) or (
                    name == "ResourceServerAuthError" and _is_pyjwt_pass_through(arg)
                )
            if name in ("_reject", "_auth_response") and isinstance(arg, ast.Name):
                ok = ok or arg.id == "body"  # the helper's own parameter
            arity = _arity_violation(name, node, keyword)
            if arity:
                out.append(f"{rel}:{node.lineno} {name}(...): {arity}")
            if not ok:
                out.append(
                    f"{rel}:{node.lineno} {name}(...) message is "
                    f"{ast.unparse(arg) if arg is not None else '<missing>'}"
                )
            if name in (
                "ResourceServerAuthError",
                "ResourceServerJWKSUnavailable",
            ) and (_is_member(arg)):
                assert isinstance(arg, ast.Attribute)
                given = {k.arg for k in node.keywords if k.arg not in (keyword, None)}
                wanted = _placeholders(_MESSAGES[arg.attr])
                if given != wanted:
                    out.append(
                        f"{rel}:{node.lineno} AuthMessage.{arg.attr} fields "
                        f"{sorted(given)} != placeholders {sorted(wanted)}"
                    )
            if name in ("ResourceServerAuthError", "ResourceServerJWKSUnavailable"):
                for kw in node.keywords:
                    if kw.arg in (keyword, None):
                        continue
                    if not isinstance(kw.value, (ast.Name, ast.Attribute)):
                        out.append(
                            f"{rel}:{node.lineno} field {kw.arg}= is "
                            f"{ast.unparse(kw.value)[:40]}, not a runtime name"
                        )
            if name == "ResourceServerAuthError" and node.args:
                code = node.args[0]
                if not (isinstance(code, ast.Constant) and code.value in _ERROR_CODES):
                    if not (isinstance(code, ast.Name) and code.id == "error"):
                        out.append(
                            f"{rel}:{node.lineno} error code {ast.unparse(code)}"
                        )
    return sorted(set(out))


def _module_trees() -> list[tuple[str, ast.Module]]:
    return [
        (rel, ast.parse(Path(module.__file__ or "").read_text()))
        for rel, module in _MODULES.items()
    ]


def test_every_message_site_names_a_registry_member() -> None:
    violations = [
        v for rel, tree in _module_trees() for v in _site_violations(rel, tree)
    ]
    assert violations == [], violations


def test_the_site_check_sees_every_site() -> None:
    """Guards the check itself: it visits every raise and `_reject` in the
    two modules (counted on the round-7 spike: 39 raises -- named re-raises
    included -- 6 of them the guards' own `TypeError`s, 3 the startup
    check's `ValueError`s, and 7 `_reject`
    calls)."""
    counts = {"raise": 0, "_reject": 0}
    for _rel, tree in _module_trees():
        for node in ast.walk(tree):
            if isinstance(node, ast.Raise) and node.exc is not None:
                counts["raise"] += 1
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "_reject":
                counts["_reject"] += 1
    assert counts == {"raise": 39, "_reject": 7}, counts


_STATIC_SHAPES = {
    # every shape the panel found in rounds 1-3, as source the check reads
    "literal": 'def f():\n    raise ResourceServerAuthError("invalid_token", "Token x.")\n',
    "keyword": "def f():\n    raise ResourceServerAuthError("
    '"invalid_token", description="Token x.")\n',
    "variable": 'def f():\n    message = AuthMessage.EMPTY_TOKEN + " Token x."\n'
    '    raise ResourceServerAuthError("invalid_token", message)\n',
    "percent": "def f():\n    raise ValueError(\"Token %s.\" % 'x')\n",
    "runtime_error": 'def f():\n    raise RuntimeError("Unsupported secret mode.")\n',
    "raise_name": 'def f():\n    error = LookupError("Token expired.")\n    raise error\n',
    "alias": "_OUTER = ResourceServerAuthError\n"
    'def f():\n    raise _OUTER("invalid_token", AuthMessage.EMPTY_TOKEN)\n',
    "subclass": "class _TokenRejected(ResourceServerAuthError):\n"
    "    message = 'Token expired.'\n",
    "walrus": "def f():\n    raise ResourceServerAuthError("
    "'invalid_token', [(m := 'Token x.') for _ in range(1)][0])\n",
    "reject_literal": 'def f():\n    return _reject(401, "Unauthorized")\n',
    "attribute_not_member": "def f(self):\n    raise ResourceServerAuthError("
    "'invalid_token', self.message)\n",
    "unknown_member": "def f():\n    raise ValueError(AuthMessage.NOT_A_MEMBER)\n",
    # round 4 of the panel
    "field_literal": "def f():\n    raise ResourceServerJWKSUnavailable("
    "AuthMessage.JWKS_FETCH_FAILED, url='Token expired.')\n",
    "field_f_string": "def f(x):\n    raise ResourceServerAuthError("
    "'insufficient_scope', AuthMessage.MISSING_SCOPES, scopes=f'Token {x}')\n",
    "named_raise_outside_its_handler": "def f():\n    try:\n        g()\n"
    "    except ValueError as exc:\n        pass\n"
    "    exc = RuntimeError('Token expired.')\n    raise exc\n",
    "named_raise_other_function": "def f():\n    try:\n        g()\n"
    "    except ValueError as exc:\n        raise exc\n"
    "def h():\n    exc = RuntimeError('Token expired.')\n    raise exc\n",
    "named_raise_rebound_in_handler": "def f():\n    try:\n        g()\n"
    "    except ValueError as exc:\n        exc = RuntimeError('Token x.')\n"
    "        raise exc\n",
    "pyjwt_text_at_invalid_token": "def f():\n    try:\n        g()\n"
    "    except jwt.InvalidTokenError as exc:\n"
    "        raise ResourceServerAuthError('invalid_token', pyjwt_text(exc))\n",
    "extra_positional_value_error": "def f():\n    raise ValueError("
    "render_auth_message(AuthMessage.UNSUPPORTED_AUTH_MODE), 'Token expired.')\n",
    "extra_positional_auth_error": "def f():\n    raise ResourceServerAuthError("
    "'invalid_token', AuthMessage.INVALID_TOKEN, 'Token expired.')\n",
    "extra_positional_jwks_error": "def f():\n    raise ResourceServerJWKSUnavailable("
    "AuthMessage.JWKS_NO_USABLE_KEYS, 'Token expired.')\n",
    "extra_positional_http_error": "def f(u, c, h, fp):\n    raise HTTPError("
    "u, c, render_auth_message(AuthMessage.REDIRECTS_NOT_ALLOWED), h, fp, 'Token expired.')\n",
    "extra_positional_reject": "def f():\n    return _reject("
    "401, AuthMessage.UNAUTHORIZED, 'Token expired.')\n",
    "extra_positional_auth_response": "def f():\n    return _auth_response("
    "401, AuthMessage.UNAUTHORIZED, 'Token expired.')\n",
    "stray_keyword_value_error": "def f():\n    raise ValueError("
    "render_auth_message(AuthMessage.UNSUPPORTED_AUTH_MODE), note='Token expired.')\n",
    # round 8 (codex round 7): changing a caught exception's payload, then
    # re-raising it from its own handler
    "payload_args": "def f():\n    try:\n        g()\n    except ResourceServerAuthError as exc:\n"
    "        exc.args = ('Token expired.',)\n        raise exc\n",
    "payload_attribute": "def f():\n    try:\n        g()\n    except ResourceServerAuthError as exc:\n"
    "        exc.description = 'Token expired.'\n        raise exc\n",
    "payload_tuple_target": "def f():\n    try:\n        g()\n    except ResourceServerAuthError as exc:\n"
    "        exc.error, exc.description = 'x', 'Token expired.'\n        raise exc\n",
    "payload_augassign": "def f():\n    try:\n        g()\n    except ResourceServerAuthError as exc:\n"
    "        exc.args += ('Token expired.',)\n        raise exc\n",
    "payload_item": "def f():\n    try:\n        g()\n    except ResourceServerAuthError as exc:\n"
    "        exc.__dict__['description'] = 'Token expired.'\n        raise exc\n",
    "payload_setattr": "def f():\n    try:\n        g()\n    except ResourceServerAuthError as exc:\n"
    "        setattr(exc, 'description', 'Token expired.')\n        raise exc\n",
    "payload_object_setattr": "def f():\n    try:\n        g()\n    except ResourceServerAuthError as exc:\n"
    "        object.__setattr__(exc, 'args', ('Token expired.',))\n        raise exc\n",
    "payload_add_note": "def f():\n    try:\n        g()\n    except ResourceServerAuthError as exc:\n"
    "        exc.add_note('Token expired.')\n        raise exc\n",
    # round 9 (claude + codex round 8): a builtin message must be rendered
    "builtin_bare_member": "def f():\n    raise ValueError(AuthMessage.PUBLIC_URL_INVALID)\n",
    "http_error_bare_member": "def f(u, c, h, fp):\n    raise HTTPError("
    "u, c, AuthMessage.REDIRECTS_NOT_ALLOWED, h, fp)\n",
    "builtin_swapped_member_missing_field": "def f():\n    raise ValueError("
    "render_auth_message(AuthMessage.JWKS_FETCH_FAILED))\n",
    "builtin_render_literal_field": "def f():\n    raise ValueError("
    "render_auth_message(AuthMessage.JWKS_FETCH_FAILED, url='Token expired.'))\n",
    "builtin_render_extra_positional": "def f():\n    raise ValueError("
    "render_auth_message(AuthMessage.PUBLIC_URL_INVALID, 'Token expired.'))\n",
    "builtin_render_star_fields": "def f(d):\n    raise ValueError("
    "render_auth_message(AuthMessage.JWKS_FETCH_FAILED, **d))\n",
    "misnamed_field": "def f(self):\n    raise ResourceServerJWKSUnavailable("
    "AuthMessage.JWKS_REDIRECT_REFUSED, uri=self.url)\n",
    "missing_field": "def f():\n    raise ResourceServerJWKSUnavailable("
    "AuthMessage.JWKS_REDIRECT_REFUSED)\n",
    "pyjwt_text_outside_a_handler": "def f(exc):\n"
    "    raise ResourceServerAuthError('invalid_token', pyjwt_text(exc))\n",
}


@pytest.mark.parametrize("shape", sorted(_STATIC_SHAPES))
def test_the_site_check_refuses_every_shape(shape: str) -> None:
    assert _site_violations("x.py", ast.parse(_STATIC_SHAPES[shape])) != []


def test_auth_text_is_built_only_in_the_registry() -> None:
    """`AuthText(...)` outside `AuthMessage` would mint a member-typed value
    the static check cannot see."""
    misplaced = []
    for rel, tree in _module_trees():
        allowed: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == "AuthMessage":
                allowed |= {id(n) for n in ast.walk(node)}
            if isinstance(node, ast.FunctionDef) and node.name == "pyjwt_text":
                allowed |= {id(n) for n in ast.walk(node)}
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and getattr(node.func, "id", "") in ("AuthText", "PyJwtText")
                and id(node) not in allowed
            ):
                misplaced.append(f"{rel}:{node.lineno}")
    assert misplaced == [], misplaced


# --- 4. logs: no sink applies the sanitiser, end to end ---------------------------


_NON_CONSTANT_LOGS: list[str] = []


def _log_templates() -> list[str]:
    """Every `logger.<level>("constant", ...)` template in the two modules. A
    non-constant template is recorded and fails
    `test_the_log_templates_are_collected` (the only static rule logs
    need)."""
    out: list[str] = []
    for rel, tree in _module_trees():
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in ("logger", "log", "logging")
                and node.func.attr
                in (
                    "debug",
                    "info",
                    "warning",
                    "warn",
                    "error",
                    "exception",
                    "critical",
                )
            ):
                arg = node.args[0] if node.args else None
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    out.append(arg.value)
                else:
                    _NON_CONSTANT_LOGS.append(f"{rel}:{node.lineno}")
    return out


_LOG_TEMPLATES = _log_templates()


def test_the_log_templates_are_collected() -> None:
    assert _NON_CONSTANT_LOGS == [], f"non-constant log templates: {_NON_CONSTANT_LOGS}"
    assert len(_LOG_TEMPLATES) == 16, _LOG_TEMPLATES
    assert "Streamable-HTTP session manager started" in _LOG_TEMPLATES


@pytest.mark.parametrize("log_format", ["text", "json"])
def test_no_log_sink_applies_the_sanitiser(
    log_format: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End to end (round 4, claude N3): run the real `setup_logging`, log
    every template through the real `pmcp.transport.http` logger, and read
    what actually reached stderr and the log file. Any redaction -- a filter,
    a record factory, a formatter, or a handler's own `emit()` -- shows up
    here. Today none applies, so the log templates are not reworded (two of
    them, `Streamable-HTTP session manager started/stopped`, would read
    `session [REDACTED]` through the sanitiser)."""
    import io
    import logging
    import sys

    from pmcp import cli

    stderr = io.StringIO()
    monkeypatch.setattr(sys, "stderr", stderr)
    monkeypatch.chdir(tmp_path)
    log_file = tmp_path / "logs" / "gateway.log"
    monkeypatch.setattr(cli, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(cli, "LOG_FILE", log_file)
    root = logging.getLogger()
    before, level = list(root.handlers), root.level
    logger = logging.getLogger("pmcp.transport.http")
    expected = []
    try:
        cli.setup_logging("DEBUG", log_to_file=True, log_format=log_format)
        added = [h for h in root.handlers if h not in before]
        assert len(added) == 2, added  # stderr + rotating file
        for template in _LOG_TEMPLATES:
            holes = template.count("%s") + template.count("%r")
            args = ("req-1",) * holes
            expected.append(template % args if holes else template)
            logger.warning(template, *args)
        for handler in added:
            handler.flush()
    finally:
        for handler in [h for h in root.handlers if h not in before]:
            root.removeHandler(handler)
            handler.close()
        root.setLevel(level)
    outputs = {"stderr": stderr.getvalue(), "file": log_file.read_text()}
    for sink, text in outputs.items():
        missing = [m for m in expected if m not in text]
        assert missing == [], f"{sink} rewrote: {missing}"


def test_no_redacting_log_hook_in_the_source() -> None:
    """Supplement: the hooks that would redact a record outside
    `setup_logging`."""
    src = Path(auth_mod.__file__ or "").parent
    hits = [
        f"{p.relative_to(src)}: {needle}"
        for p in sorted(src.rglob("*.py"))
        for needle in ("addFilter(", "logging.Filter", "setLogRecordFactory(")
        if needle in p.read_text()
    ]
    # Consiliency/pmcp#297's record scrubber is a record factory, but it only
    # rewrites a record that carries a validation or parse error; the sink
    # test above shows every auth message passes through it intact.
    assert hits == ["argument_errors.py: setLogRecordFactory("]


# --- pyjwt's fixed texts, kept verbatim by `_FIXED_TEXT_CLAIM_ERRORS` ----------

_KEY = "k" * 32


def _pyjwt_error(payload: dict[str, Any], **decode: Any) -> jwt.InvalidTokenError:
    token = jwt.encode(payload, _KEY, algorithm="HS256")
    try:
        jwt.decode(
            token, _KEY, algorithms=decode.pop("algorithms", ["HS256"]), **decode
        )
    except jwt.InvalidTokenError as exc:
        return exc
    raise AssertionError("pyjwt accepted the token")


def _now() -> int:
    """Read at test time, not import time: a full-suite run can execute this
    module long after collecting it."""
    return int(time.time())


def _missing(claim: str) -> jwt.InvalidTokenError:
    """A token valid except that it lacks `claim`, decoded with pmcp's
    `require` list (`_decode_with_key`)."""
    payload = {"iss": "i", "aud": "a", "exp": _now() + 300, "nbf": _now() - 10}
    del payload[claim]
    return _pyjwt_error(
        payload,
        audience="a",
        issuer="i",
        options={"require": ["iss", "exp", "nbf", "aud"]},
    )


# One producer per class in `_FIXED_TEXT_CLAIM_ERRORS`; a class added there
# without a producer fails `test_every_kept_pyjwt_class_has_a_producer`.
_PYJWT_PRODUCERS: dict[type[jwt.InvalidTokenError], list[Callable[[], Any]]] = {
    jwt.ExpiredSignatureError: [lambda: _pyjwt_error({"exp": _now() - 100})],
    jwt.ImmatureSignatureError: [lambda: _pyjwt_error({"nbf": _now() + 1000})],
    jwt.InvalidIssuerError: [lambda: _pyjwt_error({"iss": "a"}, issuer="b")],
    jwt.InvalidIssuedAtError: [lambda: _pyjwt_error({"iat": "x"})],
    jwt.MissingRequiredClaimError: [
        (lambda c=claim: _missing(c)) for claim in ("iss", "exp", "nbf", "aud")
    ],
    jwt.InvalidAlgorithmError: [lambda: _pyjwt_error({}, algorithms=["RS256"])],
}


def test_every_kept_pyjwt_class_has_a_producer() -> None:
    assert set(auth_mod._FIXED_TEXT_CLAIM_ERRORS) == set(_PYJWT_PRODUCERS)


@pytest.mark.parametrize(
    "produce",
    [p for ps in _PYJWT_PRODUCERS.values() for p in ps],
)
def test_every_kept_pyjwt_text_survives_the_sanitiser(produce: Any) -> None:
    exc = produce()
    assert isinstance(exc, auth_mod._FIXED_TEXT_CLAIM_ERRORS)
    text = str(exc)
    mangled = {k: v for k, v in _layers(text).items() if v != text}
    assert mangled == {}, f"{type(exc).__name__}: {text!r} is rewritten: {mangled}"


# --- the challenge: parameters survive, the header is read back intact --------

_ISSUER = "https://issuer.example"
_AUDIENCE = "https://pmcp.example/mcp"
_META = "https://pmcp.example/.well-known/oauth-protected-resource"


def _client(**overrides: Any) -> TestClient:
    with patch("pmcp.transport.http.StreamableHTTPSessionManager", autospec=True) as m:
        instance = m.return_value
        instance.run.return_value.__aenter__ = AsyncMock(return_value=None)
        instance.run.return_value.__aexit__ = AsyncMock(return_value=False)
        instance.handle_request = AsyncMock(return_value=None)
        kwargs: dict[str, Any] = {
            "auth_mode": "resource-server",
            "resource_server_issuer": _ISSUER,
            "resource_server_jwks_url": _SAMPLE_FIELDS["url"],
            "resource_server_audience": _AUDIENCE,
        }
        kwargs.update(overrides)
        app = create_http_app(MagicMock(), **kwargs)
    return TestClient(app, base_url="http://127.0.0.1", raise_server_exceptions=False)


def _challenges() -> list[tuple[str, int, str | None]]:
    body = {"jsonrpc": "2.0", "method": "notifications/initialized"}
    hs_token = jwt.encode({"iss": _ISSUER}, _KEY, algorithm="HS256")
    out: list[tuple[str, int, str | None]] = []
    for meta in (None, _META):
        extra = {"protected_resource_metadata_url": meta} if meta else {}
        label = "metadata" if meta else "audience"
        plain = _client(**extra)
        scoped = _client(required_scopes=["admin"], **extra)
        unavailable = AsyncMock(
            side_effect=ResourceServerJWKSUnavailable(
                AuthMessage.JWKS_FETCH_FAILED, url="https://issuer.example/jwks"
            )
        )
        missing_scope = AsyncMock(
            side_effect=ResourceServerAuthError(
                "insufficient_scope", AuthMessage.MISSING_SCOPES, scopes="admin"
            )
        )
        cases: list[tuple[str, TestClient, dict[str, str], Any, Any]] = [
            ("401-missing", plain, {}, None, None),
            ("401-invalid", plain, {"Authorization": "Bearer x.y.z"}, None, None),
            (
                "403-scope",
                scoped,
                {"Authorization": f"Bearer {hs_token}"},
                None,
                missing_scope,
            ),
            (
                "503-jwks",
                plain,
                {"Authorization": f"Bearer {hs_token}"},
                unavailable,
                None,
            ),
        ]
        for name, client, headers, jwks_mock, validate_mock in cases:
            jwks = jwks_mock or AsyncMock(return_value={"keys": []})
            with patch("pmcp.transport.http.AsyncJWKS.get_for_token", new=jwks):
                if validate_mock is not None:
                    with patch(
                        "pmcp.transport.http.validate_resource_server_token",
                        new=MagicMock(side_effect=validate_mock.side_effect),
                    ):
                        r = client.post("/mcp", json=body, headers=headers)
                else:
                    r = client.post("/mcp", json=body, headers=headers)
            out.append(
                (f"{name}-{label}", r.status_code, r.headers.get("www-authenticate"))
            )
    return out


_CHALLENGE_CASES = [
    f"{name}-{label}"
    for label in ("audience", "metadata")
    for name in ("401-missing", "401-invalid", "403-scope", "503-jwks")
]
_STATUS = {"401": 401, "403": 403, "503": 503}


@functools.lru_cache(maxsize=1)
def _challenge_map() -> dict[str, tuple[int, str | None]]:
    """Built lazily, inside a test, so a wiring change (a 500 with no
    challenge) fails these tests instead of erroring the whole module at
    collection."""
    out: dict[str, tuple[int, str | None]] = {}
    for case, status, header in _challenges():
        out[case] = (status, header)
    return out


def _challenge(case: str) -> tuple[int, str]:
    status, header = _challenge_map()[case]
    assert status == _STATUS[case[:3]], f"{case}: got HTTP {status}"
    assert header, f"{case}: no WWW-Authenticate"
    return status, header


def test_the_challenges_cover_401_403_503() -> None:
    statuses = {_challenge(case)[0] for case in _CHALLENGE_CASES}
    assert sorted(statuses) == [401, 403, 503]


@pytest.mark.parametrize("case", _CHALLENGE_CASES)
def test_challenge_parameters_survive_the_sanitiser(case: str) -> None:
    _status, header = _challenge(case)
    scheme, _, params = header.partition(" ")
    assert scheme == "Bearer"
    for param in (p.strip() for p in params.split(",")):
        _key, _, value = param.partition("=")
        for text in (param, value.strip('"')):
            mangled = {k: v for k, v in _layers(text).items() if v != text}
            assert mangled == {}, f"{case}: {text!r} is rewritten: {mangled}"


@pytest.mark.parametrize("case", _CHALLENGE_CASES)
def test_pmcp_reads_its_own_challenge_back(case: str) -> None:
    status, header = _challenge(case)
    parsed = parse_www_authenticate(header)
    assert parsed is not None
    if "metadata" in case:
        assert parsed.resource_metadata_url == _META
    expected = {
        401: "invalid_token",
        403: "insufficient_scope",
        503: "temporarily_unavailable",
    }
    if case != "401-missing-audience" and case != "401-missing-metadata":
        assert parsed.error == expected[status]
    if status == 403:
        assert parsed.scope == "admin"


def test_the_whole_header_is_redacted_by_the_base_bearer_rule_by_design() -> None:
    """Pinned so a change to it is a decision, not an accident: the base
    `Bearer` rule cannot tell a challenge from a credential, and it is frozen
    (additive-only, #234). pmcp never sanitises its own challenge."""
    header = f'Bearer resource_metadata="{_META}", error="invalid_token"'
    assert (
        sanitize_auth_diagnostic(header) == 'Bearer [REDACTED], error="invalid_token"'
    )


# --- `_canonical_resource()` can never publish "" ------------------------------


def test_metadata_route_refuses_to_start_without_a_canonical_resource() -> None:
    """Unreachable through `normalize_auth_metadata` (it drops any metadata
    URL that is not absolute), so force it: a route with an empty `resource`
    fails at startup instead of serving invalid RFC 9728 metadata."""
    real = http_mod.normalize_auth_metadata

    def relative(*a: Any, **k: Any) -> Any:
        info = real(*a, **k)
        return info.model_copy(
            update={
                "protected_resource_metadata_url": "/.well-known/oauth-protected-resource"
            }
        )

    # Round 7: the startup URL check now refuses this relative URL first, so
    # bypass it here to keep the route's own fail-closed check under test.
    with (
        patch("pmcp.transport.http.normalize_auth_metadata", relative),
        patch("pmcp.transport.http.check_auth_config", lambda **k: None),
    ):
        with pytest.raises(ValueError, match="canonical resource"):
            _client(
                auth_mode="none",
                resource_server_audience=None,
                protected_resource_metadata_url="https://pmcp.example/.well-known/oauth-protected-resource",
            )


# --- the README's prefix-stripping proxy case, measured ------------------------


def _metadata_client(meta: str, audience: str | None = None) -> TestClient:
    return _client(
        auth_mode="none",
        resource_server_audience=audience,
        protected_resource_metadata_url=meta,
        authorization_server_metadata_url=(
            "https://auth.example/.well-known/oauth-authorization-server"
        ),
    )


def test_readme_prefixed_metadata_url_404s_behind_a_stripping_proxy() -> None:
    client = _metadata_client(
        "https://pub.example/pmcp/.well-known/oauth-protected-resource",
        "https://pub.example/pmcp/mcp",
    )
    # What a proxy that strips `/pmcp` forwards:
    assert client.get("/.well-known/oauth-protected-resource").status_code == 404
    # ...and the route is still mounted, at the URL's literal path only. This
    # is the behaviour the follow-up (Consiliency/pmcp#334) changes; its fix
    # must invert the 404 above.
    response = client.get("/pmcp/.well-known/oauth-protected-resource")
    assert response.status_code == 200
    assert response.json()["resource"] == "https://pub.example/pmcp/mcp"


def test_readme_rfc9728_form_with_audience_serves_the_public_resource() -> None:
    client = _metadata_client(
        "https://pub.example/.well-known/oauth-protected-resource/pmcp/mcp",
        "https://pub.example/pmcp/mcp",
    )
    response = client.get("/.well-known/oauth-protected-resource/pmcp/mcp")
    assert response.status_code == 200
    assert response.json()["resource"] == "https://pub.example/pmcp/mcp"


# --- Round 7: exact arity at every site, and startup refusal of any config
# --- the renderer's field validators would refuse (Consiliency/pmcp#326).

_BAD_JWKS_URLS = [
    "https://issuer.example/key set.json",
    "https://issuer.example/jwks.json ",
    "ftp://issuer.example/jwks.json",
]
_BAD_SCOPES = ["", 'a"b', "a\\b", "café", "a b"]
_GOOD_JWKS = "https://issuer.example/.well-known/jwks.json"
# A non-http(s) scheme is already refused by `sanitize_public_auth_url`; a
# tab or newline is stripped by `urlsplit` before anything stores the URL,
# so the stored value never carries it (and is not in the table).
_JWKS_REFUSAL = "JWKS URL must be an absolute|Public auth URL must be an absolute"
# Round 9 (claude N1): a URL `sanitize_public_auth_url` refuses keeps its own,
# more specific registry message.
_METADATA_REFUSAL = (
    "metadata URL must be an absolute|Public auth URL must be an absolute"
)


@pytest.mark.parametrize(
    "build",
    [
        lambda: auth_mod.ResourceServerAuthError(
            "invalid_token", AuthMessage.INVALID_TOKEN, "Token expired."
        ),
        lambda: auth_mod.ResourceServerJWKSUnavailable(
            AuthMessage.JWKS_NO_USABLE_KEYS, "Token expired."
        ),
        lambda: _auth_response(401, AuthMessage.UNAUTHORIZED, "Token expired."),
        lambda: _auth_response(401, AuthMessage.UNAUTHORIZED, {"X-Note": "x"}),
        lambda: _auth_response(401, AuthMessage.UNAUTHORIZED, headers="Token expired."),
    ],
    ids=[
        "auth_error",
        "jwks_error",
        "auth_response_positional",
        "auth_response_positional_mapping",
        "auth_response_str",
    ],
)
def test_an_extra_argument_cannot_carry_a_second_message(build: Any) -> None:
    """The runtime half of the arity rule: the auth error classes take exactly
    their code and message positionally, and `_auth_response` / `_reject`
    take `headers` keyword-only and as a mapping -- a stray second string is a
    `TypeError`, never a response or an exception that reaches a log."""
    with pytest.raises(TypeError):
        build()


def test_reject_takes_headers_keyword_only() -> None:
    """`_reject` is a closure, so read its signature from the source."""
    tree = ast.parse(Path(http_mod.__file__).read_text())
    (fn,) = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "_reject"
    ]
    assert [a.arg for a in fn.args.args] == ["status_code", "body"]
    assert [a.arg for a in fn.args.kwonlyargs] == ["headers"]
    assert fn.args.vararg is None and fn.args.kwarg is None


@pytest.mark.parametrize("url", _BAD_JWKS_URLS)
def test_startup_refuses_a_jwks_url_the_renderer_would_refuse(url: str) -> None:
    assert not auth_mod._url_field(url)
    with pytest.raises(ValueError, match=_JWKS_REFUSAL):
        auth_mod.AsyncJWKS(url)
    with pytest.raises(ValueError, match=_JWKS_REFUSAL):
        _client(resource_server_jwks_url=url)


@pytest.mark.parametrize(
    "url",
    [
        "https://pmcp.example/.well-known/oauth-protected-resource x",
        "https://pmcp.example/.well-known/oauth protected-resource",
        # round 8 (codex round 7): normalisation would turn these into None
        # and silently omit the route, so they are checked as configured
        "/.well-known/oauth-protected-resource",
        "ftp://pmcp.example/.well-known/oauth-protected-resource",
    ],
)
def test_startup_refuses_a_metadata_url_the_renderer_would_refuse(url: str) -> None:
    with pytest.raises(ValueError, match=_METADATA_REFUSAL):
        _client(protected_resource_metadata_url=url)


@pytest.mark.parametrize("scope", _BAD_SCOPES)
def test_startup_refuses_a_required_scope_the_renderer_would_refuse(
    scope: str,
) -> None:
    with pytest.raises(ValueError, match="Each required scope"):
        _client(required_scopes=["pmcp.read", scope])
    with pytest.raises(ValueError, match="Each required scope"):
        auth_mod.check_auth_config(required_scopes=[scope])


def test_a_valid_config_starts_and_renders_every_challenge() -> None:
    client = _client(
        protected_resource_metadata_url=_META,
        required_scopes=["pmcp.read", "mcp:write", "a!#$%&'()*+,-./:;<=>?@[]^_`{|}~"],
    )
    resp = client.post("/mcp", json={"jsonrpc": "2.0", "method": "x"})
    assert resp.status_code == 401
    assert resp.text == AuthMessage.UNAUTHORIZED
    auth_mod.AsyncJWKS(_GOOD_JWKS)
    auth_mod.check_auth_config(
        jwks_url=_GOOD_JWKS, metadata_url=_META, required_scopes=["pmcp.read"]
    )


def _run_cli(
    monkeypatch: pytest.MonkeyPatch, argv: list[str], transport: str = "http"
) -> Any:
    from pmcp.cli import parse_args, run_server

    for name in ("PMCP_TRANSPORT", "PMCP_AUTH_MODE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("sys.argv", ["pmcp", "--transport", transport, *argv])
    args = parse_args()
    with patch("pmcp.server.GatewayServer") as gs:
        gs.return_value.run = AsyncMock()
        asyncio.run(run_server(args))
    return gs


_CLI_BASE = [
    "--auth-mode",
    "resource-server",
    "--oauth-issuer",
    "https://issuer.example",
    "--oauth-audience",
    "https://pmcp.example/mcp",
]


@pytest.mark.parametrize(
    ("argv", "env", "message"),
    [
        *[(["--oauth-jwks-url", url], {}, _JWKS_REFUSAL) for url in _BAD_JWKS_URLS],
        *[
            (
                ["--oauth-jwks-url", _GOOD_JWKS, "--required-scope", s],
                {},
                "required scope",
            )
            for s in _BAD_SCOPES
        ],
        *[([], {"PMCP_OAUTH_JWKS_URL": url}, _JWKS_REFUSAL) for url in _BAD_JWKS_URLS],
        *[
            (
                ["--oauth-jwks-url", _GOOD_JWKS],
                {"PMCP_REQUIRED_SCOPES": f"pmcp.read,{s}"},
                "required scope",
            )
            for s in _BAD_SCOPES
            if s.strip()  # the env path drops empty entries by design
        ],
    ],
    ids=lambda v: repr(v)[:40] if isinstance(v, (list, dict)) else None,
)
def test_cli_and_env_paths_refuse_at_startup(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    argv: list[str],
    env: dict[str, str],
    message: str,
) -> None:
    for name in ("PMCP_OAUTH_JWKS_URL", "PMCP_REQUIRED_SCOPES"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    with pytest.raises(SystemExit) as exc:
        _run_cli(monkeypatch, [*_CLI_BASE, *argv])
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert re.search(message, err) and err.startswith("error: ")


def test_cli_and_env_paths_start_a_valid_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PMCP_REQUIRED_SCOPES", "pmcp.read, mcp:write")
    gs = _run_cli(monkeypatch, [*_CLI_BASE, "--oauth-jwks-url", _GOOD_JWKS])
    assert gs.call_args.kwargs["required_scopes"] == ["pmcp.read", "mcp:write"]
    assert gs.call_args.kwargs["resource_server_jwks_url"] == _GOOD_JWKS


# --- Round 8: what must still start (claude round 7, B1). The CLI check runs
# --- only where the values are used, on the value as it will be stored.

_JWKS_ENV_CLEAN = ["PMCP_OAUTH_JWKS_URL", "PMCP_REQUIRED_SCOPES"]


@pytest.mark.parametrize(
    ("transport", "argv", "env"),
    [
        # (a) a trailing newline, as from a Kubernetes Secret built from a file
        ("http", _CLI_BASE, {"PMCP_OAUTH_JWKS_URL": _GOOD_JWKS + "\n"}),
        # (a') a trailing carriage return, as from a CRLF env file
        ("http", _CLI_BASE, {"PMCP_OAUTH_JWKS_URL": _GOOD_JWKS + "\r"}),
        # (b) a leading space on the flag
        ("http", [*_CLI_BASE, "--oauth-jwks-url", " " + _GOOD_JWKS], {}),
        # (c) stdio never reads PMCP_REQUIRED_SCOPES
        (
            "stdio",
            [*_CLI_BASE, "--oauth-jwks-url", _GOOD_JWKS],
            {"PMCP_REQUIRED_SCOPES": 'a"b'},
        ),
        # (d) auth mode `none` never reads the JWKS URL
        (
            "http",
            ["--auth-mode", "none"],
            {"PMCP_OAUTH_JWKS_URL": "https://issuer.example/key set.json"},
        ),
    ],
    ids=[
        "env_trailing_lf",
        "env_trailing_cr",
        "flag_leading_space",
        "stdio_unused_scopes",
        "mode_none_unused_jwks",
    ],
)
def test_configs_that_start_on_main_still_start(
    monkeypatch: pytest.MonkeyPatch,
    transport: str,
    argv: list[str],
    env: dict[str, str],
) -> None:
    for name in _JWKS_ENV_CLEAN:
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    gs = _run_cli(monkeypatch, argv, transport=transport)
    assert gs.called and gs.return_value.run.await_count == 1


@pytest.mark.parametrize(
    "raw", [_GOOD_JWKS + "\n", _GOOD_JWKS + "\r", " " + _GOOD_JWKS]
)
def test_a_value_the_store_cleans_is_checked_as_stored(raw: str) -> None:
    """The one validator sanitises first, so the CLI, `AsyncJWKS` and
    `create_http_app` agree on these: all start, and the stored URL is clean."""
    auth_mod.check_auth_config(jwks_url=raw, metadata_url=raw)
    assert auth_mod.AsyncJWKS(raw).url == _GOOD_JWKS
    _client(resource_server_jwks_url=raw)


def test_create_http_app_ignores_scopes_it_never_reads() -> None:
    """`GatewayServer` passes the CLI's required scopes in every auth mode;
    outside resource-server mode they are never read, so a stray bad
    `PMCP_REQUIRED_SCOPES` must not stop an HTTP gateway in mode `none`."""
    _client(
        auth_mode="none",
        resource_server_issuer=None,
        resource_server_jwks_url=None,
        resource_server_audience=None,
        required_scopes=['a"b', ""],
    )


# --- Round 9 (claude B1 + codex, round 8): builtin message sites render
# --- through the registry's one guard; sanitize's own message comes through.


def _spy_renderer(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Any, str]]:
    calls: list[tuple[Any, str]] = []
    real = auth_mod.render_auth_message

    def spy(message: Any, **fields: str) -> str:
        text = real(message, **fields)
        calls.append((message, text))
        return text

    monkeypatch.setattr(auth_mod, "render_auth_message", spy)
    monkeypatch.setattr(http_mod, "render_auth_message", spy)
    return calls


def _redirect() -> None:
    auth_mod._NoRedirectHandler().redirect_request(
        MagicMock(), None, 302, "Found", {}, "https://elsewhere.example/"
    )


_STARTUP_REFUSALS: dict[str, tuple[Callable[[], Any], str, type[Exception]]] = {
    "UNSUPPORTED_AUTH_MODE": (
        lambda: _client(auth_mode="bogus"),
        "UNSUPPORTED_AUTH_MODE",
        ValueError,
    ),
    "SHARED_SECRET_NEEDS_TOKEN": (
        lambda: _client(auth_mode="shared-secret", auth_token=None),
        "SHARED_SECRET_NEEDS_TOKEN",
        ValueError,
    ),
    "RESOURCE_SERVER_NEEDS_CONFIG": (
        lambda: _client(resource_server_jwks_url=None),
        "RESOURCE_SERVER_NEEDS_CONFIG",
        ValueError,
    ),
    "JWKS_URL_NOT_USABLE": (
        lambda: auth_mod.AsyncJWKS("https://issuer.example/key set.json"),
        "JWKS_URL_NOT_USABLE",
        ValueError,
    ),
    "METADATA_URL_NOT_USABLE": (
        lambda: _client(protected_resource_metadata_url=_META + " x"),
        "METADATA_URL_NOT_USABLE",
        ValueError,
    ),
    "REQUIRED_SCOPE_INVALID": (
        lambda: _client(required_scopes=['a"b']),
        "REQUIRED_SCOPE_INVALID",
        ValueError,
    ),
    "PUBLIC_URL_INVALID": (
        lambda: auth_mod.AsyncJWKS("https://issuer.example:not-a-port/jwks"),
        "PUBLIC_URL_INVALID",
        ValueError,
    ),
    "PUBLIC_URL_NOT_ABSOLUTE": (
        lambda: _client(
            protected_resource_metadata_url="/.well-known/oauth-protected-resource"
        ),
        "PUBLIC_URL_NOT_ABSOLUTE",
        ValueError,
    ),
    "PUBLIC_URL_HTTP_LOOPBACK_ONLY": (
        lambda: auth_mod.AsyncJWKS("http://issuer.example/jwks.json"),
        "PUBLIC_URL_HTTP_LOOPBACK_ONLY",
        ValueError,
    ),
    "PUBLIC_URL_NOT_PUBLIC": (
        lambda: auth_mod.AsyncJWKS("https://10.0.0.5/jwks.json"),
        "PUBLIC_URL_NOT_PUBLIC",
        ValueError,
    ),
    "ELICITATION_URL_INVALID": (
        lambda: auth_mod.sanitize_url_elicitation_url("ftp://x.example/"),
        "ELICITATION_URL_INVALID",
        ValueError,
    ),
    "REDIRECTS_NOT_ALLOWED": (_redirect, "REDIRECTS_NOT_ALLOWED", Exception),
}


@pytest.mark.parametrize("case", sorted(_STARTUP_REFUSALS))
def test_every_builtin_refusal_goes_through_the_renderer(
    monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    """Each builtin `ValueError`/`HTTPError` site, driven for real: the
    message the exception carries is the renderer's output for the expected
    member -- not a member handed straight to the builtin."""
    trigger, member, exc_type = _STARTUP_REFUSALS[case]
    calls = _spy_renderer(monkeypatch)
    with pytest.raises(exc_type) as raised:
        trigger()
    rendered = [
        text for message, text in calls if message is getattr(AuthMessage, member)
    ]
    assert rendered, [getattr(m, "__str__", lambda: m)() for m, _ in calls]
    carried = getattr(raised.value, "msg", None) or str(raised.value)
    assert carried == rendered[-1] == getattr(AuthMessage, member)


def test_every_builtin_message_site_is_driven() -> None:
    """The table above drives every builtin site in the two modules."""
    members = set()
    for _rel, tree in _module_trees():
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and getattr(node.func, "id", "") in _BUILTIN_SITES
            ):
                for arg in node.args:
                    render = _rendered_member(arg)
                    if render is not None:
                        members.add(render.args[0].attr)  # type: ignore[attr-defined]
    assert members - {"METADATA_NEEDS_RESOURCE"} == set(_STARTUP_REFUSALS)


def test_a_swapped_member_at_a_builtin_site_is_a_type_error_not_a_placeholder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """codex round 8: put `JWKS_FETCH_FAILED` (which needs `url`) at the
    URL-validation raise. Startup fails with the renderer's `TypeError`; the
    literal `{url}` is never published."""
    monkeypatch.setattr(
        AuthMessage, "PUBLIC_URL_INVALID", AuthMessage.JWKS_FETCH_FAILED
    )
    for build in (
        lambda: auth_mod.AsyncJWKS("https://issuer.example:not-a-port/jwks"),
        lambda: _client(
            resource_server_jwks_url="https://issuer.example:not-a-port/jwks"
        ),
    ):
        with pytest.raises(TypeError, match="fields must be exactly") as raised:
            build()
        assert "{url}" not in str(raised.value)


def test_an_ip_literal_jwks_url_keeps_its_specific_message_through_the_cli(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """claude round 8, N1: `https://10.0.0.5/...` is refused for its
    non-public host, and says so -- not "must be an absolute http(s) URL"."""
    for name in _JWKS_ENV_CLEAN:
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(SystemExit) as exc:
        _run_cli(
            monkeypatch, [*_CLI_BASE, "--oauth-jwks-url", "https://10.0.0.5/jwks.json"]
        )
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert err == f"error: {AuthMessage.PUBLIC_URL_NOT_PUBLIC}\n"


def test_an_https_metadata_url_with_a_credential_like_query_starts() -> None:
    """claude round 8, N2: pins today's behaviour. `normalize_auth_metadata`
    keeps an https URL with `?token=…` (redacted where it is shown), so it is
    not one of the URLs refused at startup; the route is served."""
    client = _client(
        auth_mode="none",
        resource_server_issuer=None,
        resource_server_jwks_url=None,
        resource_server_audience=None,
        protected_resource_metadata_url=_META + "?token=secret",
    )
    resp = client.get("/.well-known/oauth-protected-resource")
    assert resp.status_code == 200
    assert "secret" not in resp.text
