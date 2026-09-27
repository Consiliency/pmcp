"""`pmcp.keyword_matcher` against the regex it replaces.

The keyword rule in `sanitize_auth_diagnostic` used to run as one regular
expression; it now runs through `keyword_matches`, which must produce exactly
the same matches. These tests compare the two on a seeded corpus of short
strings built from the characters the rule cares about (key fragments,
identifier joiners, separator characters, value characters, case-fold
letters), where the regex itself is fast, and check that long inputs stay
fast.
"""

from __future__ import annotations

import random
import re
import time

import pytest

from pmcp.auth import AUTH_DIAGNOSTIC_SECRET_KEYS, sanitize_auth_diagnostic
from pmcp.keyword_matcher import (
    key_start_pattern,
    keys_alternation,
    keyword_matches,
    redact_keyword_values,
)

_KEYS = AUTH_DIAGNOSTIC_SECRET_KEYS
_REGEX = re.compile(
    rf"(?i)\b([A-Za-z0-9_-]*(?:{keys_alternation(_KEYS)})[A-Za-z0-9_-]*)"
    r"([\s:=]+)([A-Za-z0-9._~+/=-]{3,})"
)
_KEY_START = key_start_pattern(_KEYS)

_FRAGMENTS = [
    *sorted(_KEYS),
    "api-key",
    "API_KEY",
    "Token",
    "PASSWORD",
    "paſſword",  # the long s case-folds to s
    "a",
    "b1",
    "x",
    "-",
    "_",
    "--",
    ".",
    "/",
    "+",
    "~",
    ":",
    "=",
    "==",
    ": ",
    " = ",
    " ",
    "\t",
    "\n",
    " ",
    ",",
    ";",
    '"',
    "'",
    "(",
    "hunter2",
    "abc",
    "ab",
    "x9",
    "K",  # the Kelvin sign case-folds to k
]


def _corpus(seed: int, size: int) -> list[str]:
    rng = random.Random(seed)
    return [
        "".join(rng.choice(_FRAGMENTS) for _ in range(rng.randint(1, 12)))
        for _ in range(size)
    ]


@pytest.mark.parametrize("seed", [1, 2, 3, 4])
def test_the_matches_are_the_regex_matches(seed: int) -> None:
    for text in _corpus(seed, 5000):
        expected = [
            (m.start(), m.end(1), m.end(2), m.end()) for m in _REGEX.finditer(text)
        ]
        assert list(keyword_matches(text, _KEY_START)) == expected, repr(text)


@pytest.mark.parametrize("seed", [5, 6])
def test_the_redaction_is_the_regex_substitution(seed: int) -> None:
    for text in _corpus(seed, 5000):
        expected = _REGEX.sub(r"\1\2[REDACTED]", text)
        assert redact_keyword_values(text, _KEY_START) == expected, repr(text)


def test_sanitize_auth_diagnostic_uses_the_linear_matcher() -> None:
    assert sanitize_auth_diagnostic("password=hunter22 ok", max_length=None) == (
        "password=[REDACTED] ok"
    )
    assert sanitize_auth_diagnostic("my-api-key: abc123 x", max_length=None) == (
        "my-api-key: [REDACTED] x"
    )


@pytest.mark.parametrize(
    "text",
    [
        "a-" * 33000,
        "a_" * 33000,
        "token-" * 11000,
        "x" + "-a" * 33000 + " password",
    ],
)
def test_long_identifier_runs_stay_fast(text: str) -> None:
    started = time.perf_counter()
    sanitize_auth_diagnostic(text, max_length=None)
    assert time.perf_counter() - started < 2.0
