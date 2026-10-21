"""A peer's text is matched against the MCP SDK's message templates in
linear time (Consiliency/pmcp#388 round 1, codex F001).

``_downstream_error`` classifies a downstream's error message while the
response is dispatched, on the event loop. The templates used to be regexes
of lazy wildcards (``f'{method} requires params[{key!r}] for Mcp-Name'``),
which backtrack polynomially on a message that repeats a template's prefix
without its suffix: codex measured 11.77 s at 288 KB. Now a message longer
than :data:`MAX_CLASSIFIED_LENGTH` is never matched (it is treated as
unreviewed, so its code's fixed phrase is shown), and every template is a
:class:`LiteralTemplate`, matched by prefix, suffix and ``str.find``.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable

import pytest

from pmcp import sdk_rejections as sr
from pmcp.client.manager import _downstream_error

#: Far above the measured cost (well under 10 ms on a loaded host), far below
#: what the old regexes took at these sizes (seconds).
_BUDGET_S = 0.05


def _templates() -> list[sr.LiteralTemplate]:
    _literals, patterns = sr._sdk_message_texts()
    return [*patterns, *(pattern for pattern, _masked in sr._log_patterns())]


def _codex(size: int) -> str:
    unit = "x requires params["
    return (unit * (size // len(unit) + 1))[:size]


def _assert_bounded(
    call: Callable[..., object],
    *args: object,
    input_class: str,
    size: int,
    budget: float = _BUDGET_S,
    template_index: int | None = None,
) -> None:
    """Retain the wall oracle; report work and scheduling separately.

    Only fixed class names, size and worker identity appear in diagnostics,
    never peer text, template contents or exception values.
    """
    wall_start = time.perf_counter()
    process_start = time.process_time()
    thread_start = time.thread_time()
    call(*args)
    thread_cpu = time.thread_time() - thread_start
    process_cpu = time.process_time() - process_start
    wall = time.perf_counter() - wall_start
    diagnostics: dict[str, object] = {
        "wall_s": wall,
        "process_cpu_s": process_cpu,
        "thread_cpu_s": thread_cpu,
        "worker": os.environ.get("PYTEST_XDIST_WORKER", "controller"),
        "input_class": input_class,
        "size": size,
        "budget_s": budget,
    }
    if template_index is not None:
        diagnostics["template_index"] = template_index
    assert wall < budget, diagnostics


@pytest.fixture(autouse=True, scope="module")
def _warm() -> None:
    # Build every registry once, outside the timed calls.
    sr._sdk_message_texts()
    sr._message_registry()
    sr._log_patterns()


@pytest.mark.parametrize("size", [1 << 20, 10 << 20])
def test_codex_input_is_classified_in_bounded_time(size: int) -> None:
    message = _codex(size)
    _assert_bounded(
        _downstream_error,
        {"code": -32602, "message": message},
        input_class="sdk-prefix",
        size=size,
    )
    _assert_bounded(
        sr.sdk_built_message, message, -32602, input_class="sdk-prefix", size=size
    )
    # Not the peer's text: too long to be one the SDK formats, so the
    # code's fixed phrase, and why (round 2, claude N-B).
    assert str(_downstream_error({"code": -32602, "message": message})) == (
        f"{sr.sdk_message_phrase(-32602)} (the server's message was not shown: "
        f"over {sr.MAX_CLASSIFIED_LENGTH} characters)"
    )


@pytest.mark.parametrize("size", [64 << 10, 1 << 20, 10 << 20])
def test_every_template_matches_an_adversarial_input_in_bounded_time(
    size: int,
) -> None:
    """Each template's prefix repeated without its suffix, matched directly
    (past the length cap), at 64 KiB, 1 MiB and 10 MiB. Fails at the first
    slow template: a backtracking matcher takes seconds per template here."""
    for template_index, template in enumerate(_templates()):
        head = template.parts[0] + (
            template.parts[1] if len(template.parts) > 2 else ""
        )
        unit = head or "x"
        text = (unit * (size // len(unit) + 1))[:size]
        _assert_bounded(
            template.fullmatch,
            text,
            input_class="template-prefix",
            size=size,
            template_index=template_index,
        )


def test_every_template_still_matches_its_own_message() -> None:
    """A message built from each template, with every placeholder filled,
    is matched -- and one missing its last literal part is not."""
    templates = _templates()
    assert len(templates) > 50
    for template in templates:
        message = "v1".join(template.parts)
        assert template.fullmatch(message), template
        if len(template.parts) > 1 and template.parts[-1]:
            assert not template.fullmatch(message[: -len(template.parts[-1])]), template


def test_a_formatted_sdk_message_is_recognised() -> None:
    """The template codex used still classifies its real message."""
    message = "tools/call requires params['name'] for Mcp-Name"
    assert sr.sdk_built_message(message, -32602)


@pytest.mark.parametrize(
    ("parts", "text", "expected"),
    [
        (("a", "b", "c"), "abc", True),
        (("a", "b", "c"), "axbxc", True),
        (("a", "b", "c"), "abbc", True),
        (("a", "b", "c"), "ac", False),
        (("a", "", "c"), "ac", True),
        (("ab", "ba"), "aba", False),
        (("ab", "ba"), "abba", True),
        (("x",), "x", True),
        (("x",), "xx", False),
        (("", "b", ""), "zzbzz", True),
        (("", ""), "", True),
    ],
)
def test_literal_template_fullmatch_agrees_with_the_regex(
    parts: tuple[str, ...], text: str, expected: bool
) -> None:
    import re

    template = sr.LiteralTemplate(parts)
    assert template.fullmatch(text) is expected
    assert bool(re.fullmatch(template.pattern, text, re.DOTALL)) is expected


def test_a_message_over_the_cap_is_never_read_as_reviewed_or_relayed() -> None:
    """The length cap itself: a message that matches a template but is
    longer than :data:`MAX_CLASSIFIED_LENGTH` is SDK-built (its code's
    phrase is shown), never reviewed, and an SDK log record carrying one is
    masked whole."""
    filler = "y" * (sr.MAX_CLASSIFIED_LENGTH + 1)
    reviewed_shape = f"Unknown method: {filler}"
    assert not sr._reviewed_message(reviewed_shape, -32601)
    # Matches no template, so only the cap can make it SDK-built.
    assert sr.sdk_built_message(filler, -32000)
    assert not sr.sdk_built_message("y" * sr.MAX_CLASSIFIED_LENGTH, -32000)

    template, masked = next((p, m) for p, m in sr._log_patterns() if len(p.parts) > 1)
    long_message = filler.join(template.parts)
    record = logging.LogRecord(
        "mcp.server.lowlevel", logging.DEBUG, "f", 1, long_message, None, None
    )
    sr.scrub_sdk_record(record)
    assert record.msg != masked
    assert filler not in str(record.msg)


# --- every other pattern the audit found superlinear on a peer's text ---------


def test_the_elicitation_payload_object_is_found_as_the_regex_found_it() -> None:
    """``_embedded_object_text`` returns what ``re.search(r"(\\{.*\\})")``
    did, on every short text over the characters that matter."""
    import itertools
    import re

    from pmcp.auth import _embedded_object_text

    for size in range(7):
        for chars in itertools.product("{}a\n", repeat=size):
            text = "".join(chars)
            found = re.search(r"(\{.*\})", text)
            assert _embedded_object_text(text) == (found.group(1) if found else None)


@pytest.mark.parametrize(
    ("input_class", "unit"),
    [
        ("open-only", "{"),
        ("open-text", "{a"),
        ("open-lines", "{\n"),
        ("close-lines", "}\n"),
        ("text-open-lines", "a{\n"),
    ],
    ids=["open-only", "open-text", "open-lines", "close-lines", "text-open-lines"],
)
def test_the_elicitation_payload_object_is_found_in_bounded_time(
    input_class: str, unit: str
) -> None:
    from pmcp.auth import _embedded_object_text, parse_url_elicitation_error

    for size in (64 << 10, 1 << 20):  # the small size first: fail fast
        text = unit * (size // len(unit))
        _assert_bounded(
            _embedded_object_text, text, input_class=input_class, size=len(text)
        )
        _assert_bounded(
            parse_url_elicitation_error,
            text,
            input_class=input_class,
            size=len(text),
            budget=_BUDGET_S * 4,
        )


@pytest.mark.parametrize("unit", ["+.", "a", "a@", "a@a.", "-"])
def test_feedback_email_scrubbing_is_linear(unit: str) -> None:
    from pmcp.tools.handlers import GatewayTools

    text = unit * ((1 << 20) // len(unit))
    scrub = GatewayTools._scrub_sensitive_text

    class _Policy:
        @staticmethod
        def redact_secrets(value: str) -> str:
            return value

    class _Self:
        _policy_manager = _Policy()

    for size in (64 << 10, 1 << 20):  # the small size first: fail fast
        _assert_bounded(
            scrub,
            _Self(),
            text[:size],
            input_class="email-adversary",
            size=size,
            budget=_BUDGET_S * 4,
        )
    assert scrub(_Self(), "write to dev.null+x@example.org now") == (
        "write to [REDACTED_EMAIL] now"
    )
