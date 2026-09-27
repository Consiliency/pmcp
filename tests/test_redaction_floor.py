"""Main's rules as the redactor's floor (Consiliency/pmcp#234).

`pmcp.redaction_floor` replays main's redaction rules and hands the redactor
every span main removed; the redactor applies each one unless a named
suppression predicate drops it. The contract -- never worse than main except
by a named suppression -- is proven here in four parts:

* **Fidelity.** The replay's text equals main's output, byte for byte, on
  every input (`tests/_main_redactor.py` is main's code, vendored verbatim).
  Its keyword step is a linear matcher: it yields exactly the matches main's
  real regex does.
* **Construction.** On every input and surface, every character a floor span
  covers is removed from the output unless a named predicate dropped that
  span, and the JSON adjustment keeps syntax only.
* **Differential.** Wherever a piece main removes survives here, a named
  predicate fired on a floor span over that piece -- no hand classification.
* **Predicates.** Each fires on its false positive and not on a
  credential-shaped value.

Then the board's B1-B5 against rev 10 (each red on `e79d75f`, green here,
with a mutant that turns it red again) and the timing guards.
"""

from __future__ import annotations

import multiprocessing
import json
import random
import re
import time
from collections import Counter
from collections.abc import Callable
from urllib.parse import unquote

import pytest

import pmcp.redaction_floor as F
from pmcp import keyword_matcher
from pmcp.auth import (
    AUTH_SECRET_QUERY_KEYS,
    collect_redaction_spans,
    merge_redaction_spans,
    sanitize_auth_diagnostic,
)
from pmcp.policy import policy as policy_module
from pmcp.policy.policy import DEFAULT_REDACTION_PATTERNS, PolicyManager
from tests import _main_redactor as M
from tests import _redaction_grammar as G
from tests.test_redaction import _json_fuzz_corpus

MAIN_PATTERNS = M.compiled_default_patterns()


def _engine(text: str) -> str:
    return sanitize_auth_diagnostic(text, max_length=None)


def _policy(text: str) -> str:
    return PolicyManager().redact_secrets(text)


def _process(obj: object) -> object:
    return PolicyManager().process_output(obj, redact=True, max_bytes=G.BIG)["result"]


# ============================================================ fidelity ==== #


def test_main_constants_are_frozen_as_main_wrote_them() -> None:
    """The floor's copies of main's key sets and default patterns are main's,
    and the live default list is main's again (rev 10's forms are additive)."""
    assert F.MAIN_AUTH_SECRET_QUERY_KEYS == frozenset(M.AUTH_SECRET_QUERY_KEYS)
    assert F.MAIN_DIAGNOSTIC_SECRET_KEYS == frozenset(M.AUTH_DIAGNOSTIC_SECRET_KEYS)
    assert tuple(DEFAULT_REDACTION_PATTERNS) == F.MAIN_DEFAULT_REDACTION_PATTERNS
    assert F.MAIN_DEFAULT_REDACTION_PATTERNS == tuple(M.DEFAULT_REDACTION_PATTERNS)
    assert F.MAIN_AUTH_SECRET_QUERY_KEYS == frozenset(AUTH_SECRET_QUERY_KEYS)
    assert set(G.DIAGNOSTIC_CODE_QUALIFIERS) == set(F.DIAGNOSTIC_CODE_QUALIFIERS)


_MAIN_KEYWORD_RE = re.compile(
    rf"(?i)\b([A-Za-z0-9_-]*(?:{F._main_keys_alternation(F.MAIN_DIAGNOSTIC_SECRET_KEYS)})"
    r"[A-Za-z0-9_-]*)([\s:=]+)([A-Za-z0-9._~+/=-]{3,})"
)


def _real_keyword_matches(text: str) -> list[tuple[int, int, int, int]]:
    """Main's keyword regex itself (module level: it runs in a worker)."""
    return [
        (m.start(), m.end(1), m.end(2), m.end())
        for m in _MAIN_KEYWORD_RE.finditer(text)
    ]


def _linear_matches(text: str) -> list[tuple[int, int, int, int]]:
    return list(F.main_keyword_matches(text))


def test_the_keyword_regex_is_main_s_verbatim() -> None:
    """The regex compared against is main's own expression, rebuilt from
    main's key set as main's `sanitize_auth_diagnostic` builds it: on every
    tier-1 text no other rule of main's touches, main's output is exactly
    this regex's substitution."""
    checked = 0
    for row in G.corpus(1):
        text = row.get("t", "")
        if re.search(
            r"(?i)https?://|bearer|authorization", text
        ) or F._MAIN_JWT_RE.search(text):
            continue
        checked += 1
        assert M.sanitize_auth_diagnostic(
            text, max_length=None
        ) == _MAIN_KEYWORD_RE.sub(r"\1\2[REDACTED]", text), text
    assert checked > 5000, checked


#: Fragments that exercise every decision the matcher makes: joiners and
#: key fragments in a run (a word boundary at every joiner),
#: separators over `[\s:=]` whose greedy backtracking hands `=` back to the
#: value, value characters, Unicode `\w` characters glued before a run (no
#: `\b`), and the case-fold partners `(?i)` admits (`ſ K ı İ`).
_MATCHER_FRAGMENTS = (
    "token", "password", "sid", "code", "api-key", "Api_Key", "set-cookie",
    "SECRET", "tenant-id", "a", "b", "x1", "-", "_", "-_", "=", ":", " ",
    "\t", "\n", "　", "\xe9", "ſ", "K", "ı", "İ",
    ".", "/", "~", "+", '"', "'", "\\", "[", "é",
)  # fmt: skip


def _matcher_corpus(
    seed: int, count: int, lengths: tuple[int, ...] = (1, 10, 100, 300)
) -> list[str]:
    rng = random.Random(seed)
    texts = [
        "".join(rng.choice(_MATCHER_FRAGMENTS) for _ in range(rng.randint(1, 16)))
        for _ in range(count)
    ]
    # adversarial runs: long joiner-rich runs with and without a key, at
    # every offset, ending in every separator/value shape
    for base in ("a-", "a_", "a-_", "-a", "_-", "token-", "x-token", "tok-en", "a-b_c"):
        for length in lengths:
            for tail in (
                "",
                "=",
                " = ab",
                "==ab",
                "=== ab",
                "==== x",
                ": abc",
                "token=abc",
            ):
                texts.append(base * length + tail)
                texts.append("password-" + base * length + tail)
    return texts


def _compare_with_timeout(
    texts: list[str],
    timeout: float,
    reference: Callable[[str], list[tuple[int, int, int, int]]] | None = None,
) -> list[str]:
    """Differences between the linear matcher and the regex (``reference``),
    the regex run in worker processes so a long input cannot hang the test:
    past ``timeout`` the remaining input is reported (not skipped), and the
    pool is terminated -- a running regex cannot be cancelled any other
    way."""
    reference = reference or _real_keyword_matches
    problems: list[str] = []
    pool = multiprocessing.get_context("spawn").Pool(4)
    try:
        pending = [(text, pool.apply_async(reference, (text,))) for text in texts]
        deadline = time.monotonic() + timeout
        for text, result in pending:
            try:
                expected = result.get(timeout=max(1.0, deadline - time.monotonic()))
            except multiprocessing.TimeoutError:
                problems.append(f"TIMEOUT {text[:60]!r} ({len(text)} chars)")
                break
            if _linear_matches(text) != expected:
                problems.append(f"{text!r}: {expected} != {_linear_matches(text)}")
    finally:
        pool.terminate()
        pool.join()
    return problems


def test_the_linear_keyword_matcher_equals_main_s_regex() -> None:
    """20 000 random fragment strings, then 576 adversarial runs (up to 2.7
    KB; main's regex in workers under a 60 s budget): the same
    (start, key end, separator end, end) tuples as `finditer` of main's
    regex."""
    texts = _matcher_corpus(234, 20_000)
    short, long = texts[:20_000], texts[20_000:]
    assert [t for t in short if _linear_matches(t) != _real_keyword_matches(t)] == []
    assert _compare_with_timeout(long, timeout=60) == []


def test_the_linear_keyword_matcher_equals_main_s_regex_on_the_grammar_tier_1() -> None:
    texts = [
        spelling
        for row in G.corpus(1)
        if "t" in row
        for spelling in (row["t"], json.dumps({"t": row["t"]}))
    ]
    mismatches = [t for t in texts if _linear_matches(t) != _real_keyword_matches(t)]
    assert mismatches == []


@pytest.mark.slow
def test_the_linear_keyword_matcher_equals_main_s_regex_on_the_grammar_tier_2() -> None:
    mismatches = []
    for row in G.corpus(2):
        if "t" not in row:
            continue
        for a in (True, False):
            for i in (None, 2):
                for text in (
                    row["t"],
                    json.dumps({"t": row["t"]}, ensure_ascii=a, indent=i),
                ):
                    if _linear_matches(text) != _real_keyword_matches(text):
                        mismatches.append(text)
    assert mismatches == []
    texts = _matcher_corpus(2026, 200_000, lengths=(600, 1000))
    assert [
        t for t in texts[:200_000] if _linear_matches(t) != _real_keyword_matches(t)
    ] == []
    # the long adversarial runs: main's regex takes seconds on each
    assert _compare_with_timeout(texts[200_000:], timeout=400) == []


def _surface_texts(row: G.Row) -> list[tuple[str, str, bool]]:
    """(surface, the text its redactor reads, policy?) for one row."""
    if "o" in row:
        return [("POo", json.dumps(row["o"], indent=2), True)]
    t = row["t"]
    dumped = [
        (name, json.dumps({"t": t}, ensure_ascii=a, indent=i))
        for name, a, i in G._SPELLINGS
    ]
    return [
        ("E", t, False),
        ("P", t, True),
        *[("Ej" + name, d, False) for name, d in dumped],
        *[("Pj" + name, d, True) for name, d in dumped],
        ("POs", t, True),
        ("POd", json.dumps({"t": t}, indent=2), True),
    ]


def _fidelity(texts: list[tuple[str, bool]]) -> tuple[list[str], int]:
    bad = []
    fallbacks = 0
    for text, policy in texts:
        patterns = MAIN_PATTERNS if policy else None
        replayed, spans = F.replay(text, patterns)
        fallbacks += sum(1 for s in spans if s.rule == "url.fallback")
        main = (
            M.redact_secrets(text, MAIN_PATTERNS)
            if policy
            else M.sanitize_auth_diagnostic(text, max_length=None)
        )
        if replayed != main:
            bad.append(f"{'P' if policy else 'E'} {text!r}")
    return bad, fallbacks


def test_the_replay_reproduces_main_s_output_on_the_grammar_tier_1() -> None:
    """Every text every tier-1 row puts on a surface: the replay's final text
    is main's output, and no URL needed the whole-URL fallback."""
    texts = {
        (text, policy) for row in G.corpus(1) for _, text, policy in _surface_texts(row)
    }
    bad, fallbacks = _fidelity(sorted(texts))
    assert bad == [] and fallbacks == 0, (bad[:10], fallbacks)


@pytest.mark.slow
def test_the_replay_reproduces_main_s_output_on_the_grammar_tier_2() -> None:
    texts = {
        (text, policy) for row in G.corpus(2) for _, text, policy in _surface_texts(row)
    }
    bad, fallbacks = _fidelity(sorted(texts))
    assert bad == [] and fallbacks == 0, (bad[:10], fallbacks)


def test_the_replay_reproduces_main_s_output_on_the_fuzz_and_random_text() -> None:
    rng = random.Random(9)
    alphabet = (
        "password token Bearer Authorization: code= https://u:p@h.example/p?token=x&a=%41#f "
        "api_key sk-abcdef1234 ghp_abcdefghij123 \\ \" ' = : ; , \n \t 　 é a- _ "
        "aaaaaaaaaa.bbbbbbbbbb.cccccccccc [REDACTED] http://[::1 :99999/ "
    ).split(" ")
    texts = [
        (json.dumps(obj, indent=2), policy)
        for obj in _json_fuzz_corpus()
        for policy in (False, True)
    ]
    for _ in range(5000):
        text = "".join(rng.choice(alphabet) + rng.choice(("", " ")) for _ in range(12))
        texts += [(text, False), (text, True)]
    bad, fallbacks = _fidelity(texts)
    assert bad == [], bad[:10]
    assert fallbacks == 0


def test_the_url_step_labels_what_main_drops() -> None:
    """Main's `ValueError` arm (a port it cannot parse) keeps the first 400
    characters of the URL, fragment dropped; the floor labels both, and the
    redactor drops them as main did."""
    text = "see https://h.example:99999/" + "a" * 420 + "?q=MYSECRETVAL#frag x"
    _, spans = F.replay(text)
    rules = {s.rule for s in spans}
    assert {"url.overflow", "url.fragment"} <= rules, rules
    out = _engine(text)
    assert "MYSECRETVAL" not in out and "frag" not in out and out.endswith(" x")
    userinfo = "https://user:pw123456@h.example/p?token=abc&x=1#f"
    assert {s.rule for s in F.replay(userinfo)[1]} == {
        "url.userinfo",
        "url.query",
        "url.fragment",
    }


# ========================================================= construction ==== #


def _board_rows() -> list[G.Row]:
    """The board's multi-pair and wide-separator grids (rev 10's B1, B2, B4,
    B5), which the grammar's one-pair rows cannot produce."""
    rows: list[G.Row] = []

    def add(text: str, *secrets: str) -> None:
        rows.append({"t": text, "pieces": list(secrets), "f": None, "value": "x"})

    values = ("hunter2x", "q7Zp2Lk9Wx4R", "s3cr3tvalue")
    for k1 in (
        "password",
        "api_key",
        "token",
        "secret",
        "code",
        "sid",
        "Authorization",
        "cookie",
    ):
        for op in ("=", ":", ": "):
            for brk in ("\n", "\t", "\r\n", "\n\n", "\xe9", " \n"):
                for k2 in ("password", "token", "cookie", "api_key", "session"):
                    for v in values:
                        add(f"{k1}{op}{brk}{k2}: {v}", v)
                        add(f"{k1}{op}{brk}{k2}={v} rest", v)
    for pre in ("--", "-", "x-", "X-", "x_", "proxy_", "access_", "auth-", "api.", ""):
        for v in (
            "hunter2x",
            "s3cr3tvalue",
            "abc-def_1.2",
            "tok_12345",
            "q7Zp2Lk9Wx4R",
            "Zm9vOmJhcg==",
        ):
            add(f"mycli {pre}bearer {v} --verbose", v)
            add(f"mycli {pre}Bearer {v}", v)
    for head in ("abcdef", "q7Zp2Lk9Wx4R"):
        for ch in "\"'()[]{}<>=|\\!@#$%^&*`?/:":
            add(f"x Bearer {head}{ch}SECRETPART end", "SECRETPART")
            add(f"Authorization: {head}{ch}SECRETPART end", "SECRETPART")
            add(f"Authorization: Bearer {head}{ch}SECRETPART end", "SECRETPART")
    add("x Bearer 'hunter2x null", "hunter2x")
    # URLs: userinfo, fragments, a port main cannot parse (its `ValueError`
    # arm keeps the first 400 characters), every query key main redacts
    add("see https://user:pa@ssw0rdXq@host.example/p ok", "ssw0rdXq")
    add("https://tok3nABCDEF@github.com/x", "tok3nABCDEF")
    add(
        "https://h.example/cb#state=xyzQ12&access_token=abcDEF123",
        "abcDEF123",
        "xyzQ12",
    )
    add("https://h.example:99999/p?q=1#frag_secretXYZ", "frag_secretXYZ")
    add("https://h.example:99999/" + "a" * 420 + "?q=MYSECRETVAL", "MYSECRETVAL")
    add("https://[::1/p?token=hunter2&x=y#zz_secret", "zz_secret", "hunter2")
    for key in sorted(F.MAIN_AUTH_SECRET_QUERY_KEYS):
        add(f"https://h.example/p?{key}=plainword&n=1", "plainword")
        add(f"https://h.example/p?{key.upper()}=12345", "12345")
    add("https://h.example/p?to%6Ben=hunter2", "hunter2")
    add("https://h.example/p?token=a+b+cdef", "cdef")
    # multi-pair configuration files (.env, YAML)
    add(
        "DB_HOST=db.internal\nDB_PASSWORD=\nSESSION_SECRET=s3cr3tvalue\n", "s3cr3tvalue"
    )
    add("DB_PASSWORD=\nAPI_KEY=s3cr3tvalue\n", "s3cr3tvalue")
    add("password:\n  session: s3cr3tvalue\n", "s3cr3tvalue")
    add("smtp_password=\ncookie=s3cr3tvalue\n", "s3cr3tvalue")
    add("password=\tsecret=hunter2", "hunter2")
    add("db:\n  host: x\n  password:\n  client_secret: s3cr3tvalue\n", "s3cr3tvalue")
    add("db:\n  password:\n  session: q7Zp2Lk9Wx4R\n", "q7Zp2Lk9Wx4R")
    alphabet = (" ", ":", "=", "\n", "\t")
    rng = random.Random(55)
    for _ in range(3000):
        sep = "".join(rng.choice(alphabet) for _ in range(rng.randint(3, 5)))
        add(f"k {rng.choice(('password', 'token'))}{sep}hunter2x e", "hunter2x")
    return rows  # fmt: skip


def _main_observe(row: G.Row) -> str:
    """Main's observation code of a row, from main's vendored code."""

    def process(obj: object) -> object:
        if isinstance(obj, str):
            return M.redact_secrets(obj, MAIN_PATTERNS)
        out = M.redact_secrets(json.dumps(obj, indent=2), MAIN_PATTERNS)
        try:
            return json.loads(out)
        except json.JSONDecodeError:
            return out

    return G.observe(
        row,
        lambda t: M.sanitize_auth_diagnostic(t, max_length=None),
        lambda t: M.redact_secrets(t, MAIN_PATTERNS),
        process,
    )


def _construction(rows: list[G.Row]) -> tuple[Counter[str], list[str]]:
    """Parts A and B for ``rows``: the per-predicate count of suppressed floor
    spans (A) and of worse pieces each explained (B), and every violation."""
    policy = PolicyManager()
    counts: Counter[str] = Counter()
    problems: list[str] = []
    for row in rows:
        floors: dict[str, tuple[str, list[F.FloorSpan]]] = {}
        for surface, text, is_policy in _surface_texts(row):
            spans, _ = F.floor_spans(
                text, policy._redaction_regexes if is_policy else None
            )
            floors[surface] = (text, spans)
            applied = (
                policy.redaction_spans(text)
                if is_policy
                else collect_redaction_spans(text)
            )
            covered = bytearray(len(text))
            for start, end, _ in merge_redaction_spans(text, applied):
                covered[start:end] = b"\x01" * (end - start)
            for span in spans:
                if span.suppressed_by is not None:
                    counts["A:" + span.suppressed_by] += 1
                    if span.suppressed_by not in F.SUPPRESSIONS:
                        problems.append(f"unnamed {span.suppressed_by}")
                    continue
                kept = set()
                for a, b in span.json_kept:
                    kept.update(range(a, b))
                    if any(c not in F.JSON_SYNTAX and c != "\\" for c in text[a:b]):
                        problems.append(
                            f"{surface} json kept content {text[a:b]!r} in {text!r}"
                        )
                missing = [
                    p
                    for p in range(span.start, span.end)
                    if p not in kept and not covered[p]
                ]
                if missing:
                    problems.append(
                        f"{surface} {span.rule}/{span.part} {text[span.start : span.end]!r} "
                        f"not removed from {text!r}"
                    )
        # B: the differential, explained by the report alone
        main = _main_observe(row)
        here = G.observe(row, _engine, policy.redact_secrets, _process_with(policy))
        pieces = G.pieces(row)
        for surface in G.worse_surfaces(row, main, here):
            if surface.endswith(".type"):
                problems.append(
                    f"{surface}: {row.get('t', row.get('o'))!r} lost its dict"
                )
                continue
            names = ("POo",) if "o" in row else G.TEXT_SURFACES
            index = names.index(surface)
            text, spans = floors[surface]
            for i, piece in enumerate(pieces):
                if (
                    not (int(here[index], 32) >> i) & 1
                    or (int(main[index], 32) >> i) & 1
                ):
                    continue
                explained = {
                    s.suppressed_by
                    for s in spans
                    if s.suppressed_by is not None
                    for m in re.finditer(re.escape(piece), text)
                    if s.start < m.end() and m.start() < s.end
                }
                if not explained and _kept_with_insertions(
                    piece, _main_output(surface, text)
                ):
                    counts["R:main kept it, re-encoded"] += 1
                    continue
                if not explained:
                    problems.append(
                        f"{surface} {piece!r} unexplained in {row.get('t', row.get('o'))!r}"
                    )
                counts.update("B:" + name for name in explained)
    return counts, problems


def _main_output(surface: str, text: str) -> str:
    """Main's output for the text a surface's redactor reads (no truncation
    in these corpora, so `process_output` is `redact_secrets`)."""
    if surface.startswith("E"):
        return M.sanitize_auth_diagnostic(text, max_length=None)
    return M.redact_secrets(text, MAIN_PATTERNS)


def _kept_with_insertions(piece: str, out: str) -> bool:
    """Is ``piece`` in main's output with a few characters inserted into it
    (as written or percent-decoded)? Main's URL rewrite gives a bare query
    key an `=` (`?urn:x:tok.` becomes `?urn%3Ax%3Atok=.`): the piece's
    characters are all still there, which the piece metric reads as a
    removal. Decided from main's own output, not from the floor."""
    pattern = re.compile(
        re.escape(piece[0])
        + "".join(r"[^\s]{0,3}?" + re.escape(char) for char in piece[1:])
    )
    return any(pattern.search(form) for form in (out, unquote(out)))


def _process_with(policy: PolicyManager) -> Callable[[object], object]:
    return lambda obj: policy.process_output(obj, redact=True, max_bytes=G.BIG)[
        "result"
    ]


def test_the_vendored_main_is_the_recorded_oracle() -> None:
    """`_main_observe` (main's vendored code) reproduces the recorded oracle on
    tier 1, so part B's `main` is main."""
    oracle = json.loads(
        __import__("gzip").decompress(
            __import__("base64").b64decode(
                (
                    __import__("pathlib").Path(__file__).parent
                    / "fixtures"
                    / "redaction_main_oracle.b64"
                ).read_text()
            )
        )
    )
    rows = G.corpus(1)
    assert [_main_observe(row) for row in rows] == oracle["grammar"][: len(rows)]


@pytest.mark.parametrize("rows", ["tier1", "board"])
def test_construction_holds_on_the_grammar_tier_1_and_the_board_rows(rows: str) -> None:
    """A: every unsuppressed floor span is removed, on every surface; B:
    every piece main removes and this keeps is explained by a named
    predicate. Both over tier 1 and the board's grids, with no hand
    classification anywhere."""
    counts, problems = _construction(G.corpus(1) if rows == "tier1" else _board_rows())
    assert problems == [], "\n".join(problems[:25])
    assert {
        name.split(":", 1)[1] for name in counts if not name.startswith("R:")
    } <= set(F.SUPPRESSIONS)
    assert sum(v for k, v in counts.items() if k.startswith("A:")) > 100, counts


@pytest.mark.slow
@pytest.mark.parametrize("block", G.BLOCKS)
def test_construction_holds_on_the_grammar_tier_2(block: str) -> None:
    rows = [row for row in G.corpus(2) if row["block"] == block]
    counts, problems = _construction(rows)
    assert problems == [], "\n".join(problems[:25])
    assert {
        name.split(":", 1)[1] for name in counts if not name.startswith("R:")
    } <= set(F.SUPPRESSIONS)


def test_construction_holds_on_the_json_fuzz() -> None:
    rows = [
        {"o": obj, "pieces": [], "f": None, "value": ""} for obj in _json_fuzz_corpus()
    ]
    _, problems = _construction(rows)
    assert problems == [], "\n".join(problems[:25])
    for obj in _json_fuzz_corpus():
        for is_policy in (False, True):
            text = json.dumps(obj, indent=2)
            out = _policy(text) if is_policy else _engine(text)
            json.loads(out)
        assert isinstance(_process(obj), dict)


# =========================================================== predicates ==== #

#: The named suppressions, frozen: adding one is a reviewed change.
NAMED = (
    "separator_syntax",
    "policy_keyword_word",
    "scheme_word",
    "wrapper_syntax",
    "N3",
    "N10",
    "C12",
    "C3a",
    "C3",
    "N11",
    "N4",
    "C4",
    "C5",
    "C6",
    "C7",
    "C8",
    "C10",
    "C11",
)


def test_the_suppressions_are_the_named_set() -> None:
    assert tuple(F.SUPPRESSIONS) == NAMED
    stated = set(G.CLASSES)
    syntax = {
        "separator_syntax",
        "policy_keyword_word",
        "scheme_word",
        "wrapper_syntax",
    }
    assert set(NAMED) - syntax <= stated, set(NAMED) - syntax - stated


def _fired(text: str, policy: bool) -> dict[str, list[str]]:
    spans, _ = F.floor_spans(text, MAIN_PATTERNS if policy else None)
    fired: dict[str, list[str]] = {}
    for span in spans:
        if span.suppressed_by is not None:
            fired.setdefault(span.suppressed_by, []).append(span.removed)
    return fired


#: name -> (its false positive, the surface, what it keeps; a credential-
#: shaped neighbour it must not fire on, and the credential). For the classes
#: that are not about the value (N3 N10 C12 C3a N4 N11: an identifier, a
#: resource name, a diagnostic qualifier, a whitespace `code`, a pair), the
#: neighbour is the same credential under a real key.
PREDICATE_CASES: dict[str, tuple[str, bool, str, str, str]] = {
    "separator_syntax": ('password: "abc"', True, ' "', 'password: "hunter22x"', "hunter22x"),
    "policy_keyword_word": ("token v2", True, "token ", "token abc123def456", "abc123def456"),
    "scheme_word": ('Authorization: Bearer "abc123def456"', False, "Bearer ", "Authorization: abc123def456", "abc123def456"),
    "wrapper_syntax": ('Bearer "hunter22x"', False, '"', 'Bearer abcdef"SECRETPART', "SECRETPART"),
    "N3": ("a" * 26 + "password=hunter", False, "hunter", "dbpassword=hunter22x", "hunter22x"),
    "N10": ("arn:aws:iam::1:secret:hunter22x", False, "hunter22x", "x secret:hunter22x", "hunter22x"),
    "C12": ("error_code=E_TIMEOUT_42", False, "E_TIMEOUT_42", "otp_code=abc123def", "abc123def"),
    "C3a": ("the code abc123def", False, "abc123def", "code=abc123def", "abc123def"),
    "C3": ("token bucket", False, "bucket", "token abc123def456", "abc123def456"),
    "N11": ("token expires_in=3600", False, "expires_in=3600", "token abc123def456", "abc123def456"),
    "N4": ("gby3zPassword x9y8z7w6", False, "x9y8z7w6", "CLIENTSECRET abc123def456", "abc123def456"),
    "C4": ("if token == expected", False, "expected", "password == hunter22x", "hunter22x"),
    "C5": ("token_type=Bearer", False, "Bearer", "token_type=abc123def456", "abc123def456"),
    "C6": ("unicode=input", False, "input", "dbpassword=hunter22x", "hunter22x"),
    "C7": ('{"a": "secret: ", "b": "y"}', True, ",", "secret: hunter22x", "hunter22x"),
    "C8": ("token:\nthe end", False, "the", "password:\nhunter22x", "hunter22x"),
    "C10": ("code: -32601", False, "-32601", "code=abc123def", "abc123def"),
    "C11": ("Missing bearer token", False, "token", "Bearer abc123def456", "abc123def456"),
}  # fmt: skip


def test_every_named_suppression_has_a_case() -> None:
    assert set(PREDICATE_CASES) == set(NAMED)


@pytest.mark.parametrize("name", NAMED)
def test_each_suppression_fires_on_its_false_positive_only(name: str) -> None:
    fp, policy, kept, credential_text, credential = PREDICATE_CASES[name]
    fired = _fired(fp, policy)
    assert kept in fired.get(name, []), (name, fired)
    surface = _policy if policy else _engine
    assert kept in surface(fp)
    # the credential-shaped neighbour: this predicate keeps none of the
    # credential (a syntax predicate still keeps the neighbour's syntax), and
    # the credential is gone
    neighbour = _fired(credential_text, policy).get(name, [])
    assert not any(credential in kept for kept in neighbour), neighbour
    assert credential not in surface(credential_text), surface(credential_text)


@pytest.mark.parametrize(
    "name", ["separator_syntax", "policy_keyword_word", "scheme_word", "wrapper_syntax"]
)
def test_a_syntax_suppression_never_keeps_content(name: str) -> None:
    """The four syntax predicates decide from the removed text alone, which
    must be the pattern's own vocabulary or quote/bracket/whitespace: a
    credential in that part is never kept."""
    part = {"separator_syntax": "syntax", "policy_keyword_word": "keyword",
            "scheme_word": "scheme", "wrapper_syntax": "wrap_close"}[name]  # fmt: skip
    for removed in ("hunter22x", '"abc123"', "Bearer x", "token1", "(q7Zp2Lk9Wx4R)"):
        span = F.FloorSpan(0, len(removed), "bearer", part=part, removed=removed)
        assert not F.SUPPRESSIONS[name](span), (name, removed)


# ================================================== the board's B1-B5 ==== #
#
# Each is red on rev 10 (`e79d75f`) and green here; each mutant disables the
# mechanism that closes it and turns it red again.


def _b1() -> None:
    """B1: a key with an empty value, a line break, then another key -- on
    the dict-leaf path the second pair's value survived rev 10."""
    for leaf in (
        "[db]\npassword =\nsecret = hunter2\n",
        "[smtp]\npassword =\ncookie = hunter2",
        "password:\npassword: hunter2",
        "secret:\n\npassword: hunter2",
    ):
        out = json.dumps(_process({"t": leaf}))
        assert "hunter2" not in out, (leaf, out)
        for spelling in (json.dumps({"t": leaf}), json.dumps({"t": leaf}, indent=2)):
            assert "hunter2" not in _engine(spelling), spelling
            assert "hunter2" not in _policy(spelling), spelling


def _b2() -> None:
    """B2: `bearer` after `-` or `_` (a CLI flag, an `X-` header)."""
    for text, secret in (
        ("mycli --bearer s3cr3tvalue --verbose", "s3cr3tvalue"),
        ("X-Bearer Zm9vOmJhcg==", "Zm9vOmJhcg=="),
        ("access_bearer tok_12345", "tok_12345"),
        ("-----END PRIVATE KEY-----Bearer hunter2x", "hunter2x"),
    ):
        assert secret not in _policy(text), text
        if not text.startswith("access_"):
            assert secret not in _engine(text), text


def _b3_split(split: Callable[[str], int]) -> float:
    started = time.perf_counter()
    split("password" + ":=" * 33_000 + "x")
    return time.perf_counter() - started


def _b3() -> None:
    """B3: the policy split loop is linear (rev 10: 3.5 s on 66 KB)."""
    policy = PolicyManager()
    started = time.perf_counter()
    policy.redact_secrets("password" + ":=" * 33_000)
    assert time.perf_counter() - started < 1.0
    assert _b3_split(policy_module._value_separator) < 0.1


def _b4() -> None:
    """B4: a Bearer/Authorization value runs past a quote or bracket, as
    main's `[^\\s,;]+` did."""
    for text in (
        'x Bearer q7Zp2Lk9Wx4R"SECRETPART end',
        "x Bearer abcdef(SECRETPART end",
        'Authorization: q7Zp2Lk9Wx4R"SECRETPART end',
        "Authorization: Bearer abcdef'SECRETPART end",
        'x Bearer abcdef"SECRETPART end',
    ):
        for surface in (_engine, _policy):
            assert "SECRETPART" not in surface(text), text
    assert "hunter2x" not in _engine("x Bearer 'hunter2x null")


def _b5() -> None:
    """B5: separators of 4-5 characters mixing operators and whitespace."""
    for text in (
        "k password: : hunter2x e",
        "password = = hunter2x",
        "token\t= : hunter2x",
    ):
        assert "hunter2x" not in _engine(text), text
        assert "hunter2x" not in _policy(text), text


@pytest.mark.parametrize("check", [_b1, _b2, _b3, _b4, _b5], ids=lambda f: f.__name__)
def test_rev_10_board_findings_are_closed(check: Callable[[], None]) -> None:
    check()


def _quadratic_split(full_match: str) -> int:
    for i, char in enumerate(full_match):
        if char in ":=" and full_match[i + 1 :].strip(" \t:="):
            return i
    return -1


MUTANTS: dict[str, tuple[Callable[[], None], Callable[[pytest.MonkeyPatch], None]]] = {
    # B1: no floor at all (rev 10's rules alone)
    "B1": (
        _b1,
        lambda mp: mp.setattr(
            "pmcp.auth.floor_spans", lambda text, patterns=None: ([], [])
        ),
    ),
    # B2: main's bearer step gated as rev 10 gated it
    "B2": (
        _b2,
        lambda mp: mp.setattr(
            F,
            "_MAIN_BEARER_RE",
            re.compile(r"(?i)((?<![A-Za-z0-9_-])bearer\s+)[^\s,;]+"),
        ),
    ),
    # B3: the quadratic split
    "B3": (
        _b3,
        lambda mp: mp.setattr(policy_module, "_value_separator", _quadratic_split),
    ),
    # B4: main's Bearer and Authorization values cut at a quote or bracket,
    # as rev 10 cut them
    "B4": (
        _b4,
        lambda mp: (
            mp.setattr(
                F,
                "_MAIN_BEARER_RE",
                re.compile(r"(?i)(\bbearer\s+)[^\s,;\"'()\[\]{}<>]+"),
            ),
            mp.setattr(
                F,
                "_MAIN_AUTHORIZATION_RE",
                re.compile(
                    r"(?i)(authorization\s*[:=]\s*)(bearer\s+)?[^\s,;\"'()\[\]{}<>]+"
                ),
            ),
        ),
    ),
    # B5: the floor's separator capped at three characters
    "B5": (
        _b5,
        lambda mp: mp.setattr(
            keyword_matcher, "_SEP_RUN_RE", re.compile(r"[\s:=]{1,3}")
        ),
    ),
}


@pytest.mark.parametrize("finding", sorted(MUTANTS))
def test_each_board_finding_has_a_killing_mutant(
    finding: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    check, mutate = MUTANTS[finding]
    mutate(monkeypatch)
    with pytest.raises(AssertionError):
        check()


def _n11_after_bearer() -> None:
    """N11 narrowed (maintainer, after rev 11's report): a `name=value` token
    right after `Bearer`/`Authorization` is the credential, and a pair whose
    value is credential-shaped is not prose -- both redacted, as on main;
    `token expires_in=3600` stays prose."""
    for text, secret in (
        ("x Bearer abcdef=SECRETPART end", "SECRETPART"),
        ("Authorization: Bearer abcdef=SECRETPART", "SECRETPART"),
        ("token code=abcdefg1234x", "abcdefg1234x"),
    ):
        for surface in (_engine, _policy):
            assert secret not in surface(text), (text, surface(text))
    for prose in ("token expires_in=3600", "token code=404"):
        assert _engine(prose) == prose and _policy(prose) == prose


def _old_n11(span: F.FloorSpan) -> bool:
    return (
        span.rule in ("keyword", "policy:2", "bearer")
        and span.part == "value"
        and F._whitespace_sep(span)
        and re.match(r"[A-Za-z_-]+=[^=]", F._sep_and_value(span)[1] + span.after)
        is not None
    )


def test_n11_never_fires_after_a_scheme_or_on_a_credential() -> None:
    _n11_after_bearer()


def test_the_old_n11_is_killed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(F.SUPPRESSIONS, "N11", _old_n11)
    with pytest.raises(AssertionError):
        _n11_after_bearer()


# ============================================================== timing ==== #


_ADVERSARIAL = {
    "a-": "a-" * 33_000,
    "a=": "a=" * 33_000,
    "a-b=": "a-b=" * 16_500,
    "token-=": "token-" * 11_000 + "=abc",
    "password=b": "password=b " * 6000,
    "a_": "a_" * 33_000,
    "token-": "token-" * 11_000,
    "password:=": "password" + ":=" * 33_000,
    "pw_": ("password_" * 7334)[:66_000],
    "escaped": "\\u00a0password" * 4400,
    "url": "https://h.example/?" + "token=%41&" * 6600,
}


@pytest.mark.parametrize("name", sorted(_ADVERSARIAL))
def test_timing_guard_on_every_surface(name: str) -> None:
    """66 KB of each adversarial shape on the engine, the policy surface and
    `process_output` (string and dict): under 1 s for `a-`, 2 s for the
    rest. Rev 10 took 3.5 s on `password:=` (B3)."""
    text = _ADVERSARIAL[name]
    policy = PolicyManager()
    for label, run in (
        ("E", lambda: sanitize_auth_diagnostic(text, max_length=None)),
        ("P", lambda: policy.redact_secrets(text)),
        ("POs", lambda: policy.process_output(text, redact=True)),
        ("POd", lambda: policy.process_output({"t": text}, redact=True)),
    ):
        started = time.perf_counter()
        run()
        elapsed = time.perf_counter() - started
        # `a-` is held to 1 s, as the keyword matcher's own guard; the rest
        # to rev 10's 2 s bound (its additive keyword rules cost ~0.6 s on
        # `token-` x 11 000 here)
        assert elapsed < (1.0 if name == "a-" else 2.0), (name, label, elapsed)


def test_the_floor_itself_is_linear() -> None:
    """The replay alone, doubling the input: the time at 132 KB is under 3x
    the time at 66 KB (a quadratic replay would be 4x)."""
    timings = []
    for n in (33_000, 66_000):
        text = "a-" * n
        started = time.perf_counter()
        F.replay(text, MAIN_PATTERNS)
        timings.append(time.perf_counter() - started)
    assert timings[1] < 3 * timings[0] + 0.05, timings


def _never_returns(text: str) -> list[tuple[int, int, int, int]]:
    """A stand-in reference that outlives any budget."""
    time.sleep(3600)
    return []


def test_the_regex_comparison_reports_a_timeout_instead_of_hanging() -> None:
    """A reference past the budget is reported as a TIMEOUT within it, and
    its worker is terminated."""
    started = time.perf_counter()
    problems = _compare_with_timeout(["x"], timeout=2, reference=_never_returns)
    assert problems and problems[0].startswith("TIMEOUT"), problems
    assert time.perf_counter() - started < 30
