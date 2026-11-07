"""Consiliency/pmcp#478: keyword secrets in JSON-encoded text, and the
credential in `Authorization: <scheme> <credential>`.

Surfaces: ``E`` is `sanitize_auth_diagnostic(max_length=None)`, ``P`` is
`PolicyManager.redact_secrets`, ``POs`` and ``POd`` are `process_output` of a
string and of ``{"t": text}``. A text at depth *d* is ``json.dumps`` applied
*d* times. The oracles (`_main_e`, `_main_p`, `_main_process`) are the frozen
pre-additive redactor (`tests/_main_redactor.py`); benign texts must equal
the oracle's output, never merely the input.

All secrets are synthetic (`quokkawombat`, `quokkaWombat9`, `Zm9vOmJhcg`,
`QZHUNTERWOMBAT`, `hunter2wombat`).
"""

from __future__ import annotations

import hashlib
import inspect
import itertools
import json
import random
import re
import textwrap
from collections.abc import Callable, Iterator

import pytest

import pmcp.auth as AU
import pmcp.redaction_additive as A
from pmcp.policy.policy import PolicyManager
from tests import _main_redactor as M
from tests.test_redaction_additive import (
    CREDENTIALS,
    PROSE,
    G,
    _is_additive_of,
    _json_fuzz_corpus,
    _main_e,
    _texts,
)

_POLICY: PolicyManager | None = None


@pytest.fixture(autouse=True)
def _one_policy_per_test() -> Iterator[None]:
    global _POLICY
    _POLICY = None
    yield
    _POLICY = None


def _policy() -> PolicyManager:
    global _POLICY
    if _POLICY is None:
        _POLICY = PolicyManager()
    return _POLICY


def E(text: str) -> str:
    return AU.sanitize_auth_diagnostic(text, max_length=None)


def dumps(text: str, depth: int) -> str:
    for _ in range(depth):
        text = json.dumps(text)
    return text


#: The surfaces the Authorization rule is asserted on, by commit.
SURFACES: dict[str, Callable[[str], str]] = {"E": E}
#: The text depths the Authorization rule is asserted at, by commit.
DEPTHS = (0,)
#: Whether texts holding a JSON escape are asserted (the escape views).
ESCAPED = False


def _leaks(text: str, secret: str) -> list[str]:
    return [name for name, run in SURFACES.items() if secret in run(text)]


# =================================================== recorded edits (S4) ==== #


_LEAD_RE = re.compile(r"(?:\s|\[REDACTED\]|%5BREDACTED%5D)*")


def _edit_errors(before: str, after: str, edits: list[A.Edit]) -> list[str]:
    """Do ``edits`` describe exactly the pass from ``before`` to ``after``?
    Sorted and disjoint, every gap byte-equal, each kept prefix and suffix
    byte-equal, ``lead`` right, and the lengths add up."""
    errors: list[str] = []
    previous = out = 0
    for start, end, new_len, kept_prefix, kept_suffix, lead in edits:
        if start < previous:
            return [f"unsorted or overlapping at {start}"]
        gap = before[previous:start]
        if after[out : out + len(gap)] != gap:
            return [f"gap differs before {start}"]
        out += len(gap)
        old, new = before[start:end], after[out : out + new_len]
        if len(new) != new_len:
            return [f"replacement at {start} is short"]
        if kept_prefix + kept_suffix > min(len(old), new_len):
            errors.append(f"kept parts overlap at {start}")
        if old[:kept_prefix] != new[:kept_prefix]:
            errors.append(f"kept prefix differs at {start}")
        if (
            kept_suffix
            and old[len(old) - kept_suffix :] != new[new_len - kept_suffix :]
        ):
            errors.append(f"kept suffix differs at {start}")
        middle = _LEAD_RE.match(new, kept_prefix, new_len - kept_suffix)
        assert middle is not None
        if middle.end() - kept_prefix != lead:
            errors.append(f"lead wrong at {start}")
        out += new_len
        previous = end
    if after[out:] != before[previous:]:
        errors.append("tail differs")
    return errors


_REPLAY_EXTRA = [
    "see https://a.test/x?q=1\ntoken: quokkawombat",
    "GET https://a.test/x#frag\ntoken: quokkawombat",
    "Authorization: Bearer xyz9abc12 (expired)",
    "x eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abcDEF123_- y",
    "GET https://a.test/x?q=1\nAuthorization: Basic Zm9vOmJhcg\nHost: a",
    "https://u:p@a.test/x?token=1&b=2).",
    "password=[REDACTED] Bearer [REDACTED] https://a.test/?token=%5BREDACTED%5D",
]


def _replay_corpus() -> list[str]:
    texts = _texts(G.corpus(1)[::5]) + [PROSE] + [c[1] for c in CREDENTIALS]
    texts += [json.dumps(o, indent=2) for o in _json_fuzz_corpus()[::4]]
    texts += _REPLAY_EXTRA
    return [dumps(t, d) for t in texts for d in range(3)]


class _Recorder:
    """Wraps each base pass so the test sees its input and output."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.log: list[tuple[str, str]] = []
        real_re, real_keywords, real_jwt = AU.re, AU.redact_keyword_values, AU._JWT_RE
        log = self.log

        class _Re:
            def __getattr__(self, name: str) -> object:
                return getattr(real_re, name)

            def sub(self, pattern: str, repl: object, text: str) -> str:
                out = real_re.sub(pattern, repl, text)  # type: ignore[call-overload]
                log.append((text, out))
                return out

        class _Jwt:
            def sub(self, repl: object, text: str) -> str:
                out = real_jwt.sub(repl, text)  # type: ignore[call-overload]
                log.append((text, out))
                return out

        def keywords(text: str, *args: object, **kwargs: object) -> str:
            out = real_keywords(text, *args, **kwargs)  # type: ignore[arg-type]
            log.append((text, out))
            return out

        monkeypatch.setattr(AU, "re", _Re())
        monkeypatch.setattr(AU, "redact_keyword_values", keywords)
        monkeypatch.setattr(AU, "_JWT_RE", _Jwt())


def test_recorded_base_edits_replay_every_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    """Under a live record, `_sanitize_base` still returns the frozen
    redactor's output, and each pass's recorded edits describe that pass
    exactly (re-check S4)."""
    recorder = _Recorder(monkeypatch)
    bad: list[str] = []
    passes = edits = 0
    for text in _replay_corpus():
        recorder.log.clear()
        with AU._url_tail_restore() as record:
            base = AU._sanitize_base(text)
        if base != _main_e(text):
            bad.append(f"output differs: {text[:80]!r}")
        assert record.base_edits is not None
        assert len(recorder.log) == len(record.base_edits) == 5
        for (before, after), recorded in zip(recorder.log, record.base_edits):
            passes += 1
            edits += len(recorded)
            bad.extend(
                f"{e}: {text[:80]!r}" for e in _edit_errors(before, after, recorded)
            )
    assert bad == [], bad[:10]
    assert passes > 5_000 and edits > 500, (passes, edits)


def test_no_record_records_nothing() -> None:
    """A direct `_sanitize_base` caller has no record: nothing is recorded,
    and the views read the base output instead (fail safe)."""
    text = "Authorization: Basic Zm9vOmJhcg"
    base = AU._sanitize_base(text)
    assert base == _main_e(text) == "Authorization: [REDACTED] Zm9vOmJhcg"
    assert AU._usable_edits(None) is None
    spans = A.carried_view_spans(text, base, None)
    assert (
        A.apply_redaction_spans(base, spans) == "Authorization: [REDACTED] [REDACTED]"
    )


def test_a_poisoned_record_is_not_used() -> None:
    with AU._url_tail_restore() as record:
        AU._sanitize_base("Authorization: Basic Zm9vOmJhcg")
        assert AU._usable_edits(record) is not None
        AU._sanitize_base("again")
        assert record.poisoned and AU._usable_edits(record) is None


# ============================================================ transport ==== #


@pytest.mark.parametrize(
    ("old", "new", "expected"),
    [
        # (kept_prefix, kept_suffix, lead)
        ("Authorization: Basic", "Authorization: [REDACTED]", (15, 0, 10)),
        ("password=hunter2", "password= [REDACTED]", (9, 0, 11)),
        ("token=[REDACTED]x", "token=[REDACTED]", (16, 0, 0)),
        ("abc", "abc", (3, 0, 0)),
        ("[REDACTED]a", "[REDACTED]", (10, 0, 0)),
        ("x[RED", "x[REDACTED]", (1, 0, 10)),
    ],
)
def test_make_edit(old: str, new: str, expected: tuple[int, int, int]) -> None:
    assert A.make_edit(5, 5 + len(old), old, new) == (
        5,
        5 + len(old),
        len(new),
        *expected,
    )


def test_transport_maps_kept_bytes_and_replaced_stretches() -> None:
    old = "aa password=hunter2 bb"
    new = "aa password= [REDACTED] bb"
    edits = [A.make_edit(3, 19, old[3:19], new[3:23])]
    assert new == old[:3] + new[3:23] + old[19:]
    spans = [(0, 2, "R"), (3, 11, "R"), (12, 19, "R"), (20, 22, "R"), (14, 16, "R")]
    out = A._transport_one(spans, edits)
    # before; the kept key; the value (past the marker: empty, dropped);
    # after; inside the value (dropped)
    assert out == [(0, 2, "R"), (3, 11, "R"), (24, 26, "R")]


def test_transport_never_starts_a_span_on_a_marker_the_pass_wrote() -> None:
    old = "k=v Zm9vOmJhcg tail"
    new = "k=v [REDACTED] tail"
    edits = [A.make_edit(0, len(old), old, new)]
    # a span over the replaced value and the tail: starts after the marker
    assert A._transport_one([(4, 19, "R")], edits) == [(14, 19, "R")]


def test_transport_shifts_spans_after_length_changes() -> None:
    text = "https://a.test/x?token=abcdef then Zm9vOmJhcg"
    base = "https://a.test/x?token=%5BREDACTED%5D then Zm9vOmJhcg"
    url_end = len("https://a.test/x?token=abcdef")
    edits = [A.make_edit(0, url_end, text[:url_end], base[: url_end + 8])]
    start = text.index("Zm9v")
    assert A.transport_spans([(start, start + 10, "R")], [edits], base) == [
        (base.index("Zm9v"), base.index("Zm9v") + 10, "R")
    ]


def test_transport_short_circuits_on_no_spans() -> None:
    assert A.transport_spans([], [[A.make_edit(0, 1, "a", "b")]], "b") == []


def test_a_span_nested_across_a_json_string_boundary_is_kept() -> None:
    """`_clip_to_json_strings` cuts an outer span that starts on an opening
    quote to start ON the marker after it, and a span on a marker replaces
    only the run glued to it: the nested span is what withholds the rest
    (review of b5e1e12)."""
    text = '{"a": "[REDACTED] quokkawombat"}'
    outer = (text.index('"[RED'), text.index("wombat") + 6, A.REDACTED)
    nested = (text.index("quokka"), text.index("wombat") + 6, A.REDACTED)
    assert A._drop_nested(text, [outer, nested]) == [outer, nested]
    assert "quokka" not in A.redact_additive(text, extra=[outer, nested])
    # in the same string, the outer span covers it, and only one is kept
    plain = '{"a": "x quokkawombat"}'
    outer = (plain.index("x"), plain.index("wombat") + 6, A.REDACTED)
    nested = (plain.index("quokka"), plain.index("wombat") + 6, A.REDACTED)
    assert A._drop_nested(plain, [outer, nested]) == [outer]


_DROP_ATOMS = ['"', '\\"', "\\\\", "\\n", "\\u0041", "[REDACTED]", "%5BREDACTED%5D", " ", "ab", "Zx9", ":", ",", "=", "&", "{", "}", "[", "]", "arn", ":aws:", "token="]  # fmt: skip


def _drop_nested_cases(count: int, seed: int) -> Iterator[tuple[str, list[A.Span]]]:
    rng = random.Random(seed)
    for _ in range(count):
        if rng.random() < 0.5:
            pieces = [
                "".join(rng.choice(_DROP_ATOMS[3:]) for _ in range(rng.randint(0, 6)))
                for _ in range(rng.randint(1, 4))
            ]
            text = json.dumps({f"k{i}": v for i, v in enumerate(pieces)})
            if rng.random() < 0.5:
                text = json.dumps([text, pieces[0]])
        else:
            text = "".join(rng.choice(_DROP_ATOMS) for _ in range(rng.randint(1, 14)))
        spans = []
        for _ in range(rng.randint(1, 5)):
            start = rng.randint(0, len(text))
            spans.append((start, rng.randint(start, len(text)), A.REDACTED))
        yield text, A.widen_over_escapes(text, spans)


def _drop_nested_changes(
    drop: Callable[[str, list[A.Span]], list[A.Span]],
) -> list[str]:
    return [
        text
        for text, spans in _drop_nested_cases(4_000, 478)
        if A.redact_additive(text, extra=drop(text, spans))
        != A.redact_additive(text, extra=spans)
    ]


def test_drop_nested_applies_as_the_spans_do() -> None:
    """Dropping nested spans never changes what `redact_additive` writes: on
    JSON documents (string clipping), markers, escapes and plain text."""
    assert _drop_nested_changes(A._drop_nested) == []


def test_mutant_drop_nested_ignores_string_boundaries() -> None:
    """The rule before the review of b5e1e12 (any span nested in one that
    does not start on a marker) changes the output, leaving bytes in clear."""
    source = textwrap.dedent(inspect.getsource(A._drop_nested))
    old = "return start, (min(end, next_quote(start)) if may_be_json else end)"
    assert source.count(old) == 1
    namespace = dict(vars(A))
    exec(source.replace(old, "return start, end"), namespace)
    assert len(_drop_nested_changes(namespace["_drop_nested"])) >= 1


def test_transport_can_keep_nested_spans() -> None:
    """`drop_nested=False` (a later pass still rewrites the text) keeps them."""
    text = "aaaa bbbb"
    spans = [(0, 9, "R"), (5, 9, "R")]
    assert A.transport_spans(spans, [], text, drop_nested=False) == spans
    assert A.transport_spans(spans, [], text) == [(0, 9, "R")]


# ============================================ C: Authorization credential ==== #

SCHEMES = ["Basic", "basic", "BASIC", "Bearer", "Negotiate", "NTLM", "Custom-Scheme", "Token", "ApiKey"]  # fmt: skip
SPELLINGS = [
    "Authorization: ",
    "authorization:",
    "AUTHORIZATION : ",
    "authorization=",
    "Proxy-Authorization: ",
    "Authorization:\t",
]
CREDS = [
    ("Zm9vOmJhcg", "Zm9vOmJhcg"),
    ("Zm9vOmJhcg==", "Zm9vOmJhcg"),
    ("dXNlcjpwYXNz", "dXNlcjpwYXNz"),
    ("QZHUNTERWOMBAT", "QZHUNTERWOMBAT"),
    ('"Zm9vOmJhcg"', "Zm9vOmJhcg"),
    ("'Zm9vOmJhcg'", "Zm9vOmJhcg"),
    ("(Zm9vOmJhcg)", "Zm9vOmJhcg"),
]


def _header(spelling: str, scheme: str, cred: str) -> str:
    return f"{spelling}{scheme} {cred}"


CONTEXTS: dict[str, Callable[[str, str, str], str]] = {
    "line": _header,
    "block": lambda sp, s, c: f"GET / HTTP/1.1\r\n{_header(sp, s, c)}\r\nHost: x",
    "json_value": lambda sp, s, c: json.dumps({sp.strip(" :=\t") or "Authorization": f"{s} {c}"}),
    "json_leaf": lambda sp, s, c: json.dumps({"h": _header(sp, s, c)}),
    "url_line": lambda sp, s, c: f"GET https://a.test/x?q=1\n{_header(sp, s, c)}\nHost: a",
    "folded": lambda sp, s, c: f"{sp}{s}\r\n {c}",
    "log": lambda sp, s, c: f"ts=1 level=warn {_header(sp, s, c)}",
    "list": lambda sp, s, c: f"{_header(sp, s, c)}, Accept: */*",
}  # fmt: skip
_AXES = [SCHEMES, SPELLINGS, list(CONTEXTS), [0, 1, 2], CREDS]


def fast_tier() -> tuple[
    list[tuple[int, int, int, int, int]], set[tuple[int, int, int, int]]
]:
    """scheme x context x depth in full (216 rows); spelling and credential
    chosen greedily, deterministically, so that every pair of values of any
    two of the five axes occurs in some row (owner decision Q4). Returns the
    rows (axis indices) and the pairs left uncovered."""
    sizes = [len(axis) for axis in _AXES]
    pairs = list(itertools.combinations(range(5), 2))
    need = {
        (i, a, j, b) for i, j in pairs for a in range(sizes[i]) for b in range(sizes[j])
    }
    rows = []
    for scheme, context, depth in itertools.product(
        range(sizes[0]), range(sizes[2]), range(sizes[3])
    ):
        best: tuple[int, tuple[int, int, int, int, int]] | None = None
        for spelling in range(sizes[1]):
            for cred in range(sizes[4]):
                row = (scheme, spelling, context, depth, cred)
                gain = sum((i, row[i], j, row[j]) in need for i, j in pairs)
                if best is None or gain > best[0]:
                    best = (gain, row)
        assert best is not None
        rows.append(best[1])
        for i, j in pairs:
            need.discard((i, best[1][i], j, best[1][j]))
    return rows, need


_FAST_ROWS, _FAST_UNCOVERED = fast_tier()


def _case(row: tuple[int, int, int, int, int]) -> tuple[str, str]:
    scheme, spelling, context, depth, cred = row
    raw, secret = CREDS[cred]
    text = CONTEXTS[list(CONTEXTS)[context]](SPELLINGS[spelling], SCHEMES[scheme], raw)
    return dumps(text, depth), secret


def _row_id(row: tuple[int, int, int, int, int]) -> str:
    scheme, spelling, context, depth, cred = row
    return f"{SCHEMES[scheme]}-{list(CONTEXTS)[context]}-d{depth}-sp{spelling}-c{cred}"


def test_the_fast_tier_covers_every_pair() -> None:
    """216 rows, and every pair of axis values occurs: a later edit cannot
    silently thin the matrix."""
    assert len(_FAST_ROWS) == len(SCHEMES) * len(CONTEXTS) * 3 == 216
    assert _FAST_UNCOVERED == set()


@pytest.mark.parametrize(
    "row",
    [r for r in _FAST_ROWS if r[3] in DEPTHS and (ESCAPED or "\\" not in _case(r)[0])],
    ids=_row_id,
)
def test_authorization_credential_fast_tier(
    row: tuple[int, int, int, int, int],
) -> None:
    text, secret = _case(row)
    assert _leaks(text, secret) == []


@pytest.mark.slow
@pytest.mark.parametrize("depth", DEPTHS)
@pytest.mark.parametrize("context", range(len(CONTEXTS)), ids=list(CONTEXTS))
def test_authorization_credential_full_matrix(context: int, depth: int) -> None:
    """The full product (9 schemes x 6 spellings x 7 credentials per
    context and depth; 9,072 cases over 24 IDs)."""
    bad = []
    for scheme, spelling, cred in itertools.product(
        range(len(SCHEMES)), range(len(SPELLINGS)), range(len(CREDS))
    ):
        text, secret = _case((scheme, spelling, context, depth, cred))
        if not ESCAPED and "\\" in text:
            continue
        if leaked := _leaks(text, secret):
            bad.append((SCHEMES[scheme], SPELLINGS[spelling], CREDS[cred][0], leaked))
    assert bad == [], bad[:10]


#: Synthetic digests shaped like real ones: a SigV4 signature (64 hex), a
#: Digest MD5 response (32 hex) and a cnonce (16 hex).
_SIGV4 = hashlib.sha256(b"synthetic-sig-484").hexdigest()
_RESPONSE = hashlib.md5(b"synthetic-digest-484").hexdigest()
_CNONCE = hashlib.md5(b"synthetic-cnonce").hexdigest()[:16]
#: The layout of RFC 7616, section 3.9.1.
_DIGEST_RFC7616 = (
    'Authorization: Digest username="Mufasa",\n    realm="http-auth@example.org",\n'
    '    uri="/dir/index.html",\n    algorithm=SHA-256,\n'
    '    nonce="7ypf/xlj9XXwfDPEoM4URrv/xwf94BcCAzFZH4GiTo0v",\n    nc=00000001,\n'
    f'    qop=auth,\n    cnonce="{_CNONCE}",\n    response="{_RESPONSE}",\n'
    '    opaque="FQhe/qaU925kfnzjCev0ciny7QMkPqMAFRtzCUYo5tdS"'
)

AUTH_PARAM_CASES = [
    ('Authorization: Digest username="u1", response="ab12cd"', ["u1", "ab12cd"]),
    ('Authorization: Digest username="u1", response="ab12cd', ["u1", "ab12cd"]),
    ('Authorization: Digest response="ab12cd"', ["ab12cd"]),
    (
        "Authorization: AWS4-HMAC-SHA256 Credential=AKIAQZHUNTER/20261107/us-east-1/s3/aws4_request, "
        "SignedHeaders=host, Signature=ab12cd34ef",
        ["AKIAQZHUNTER", "ab12cd34ef"],
    ),
    ('Authorization: OAuth oauth_signature="quokkaWombat9"', ["quokkaWombat9"]),
    (
        'Authorization: Signature keyId="k1", signature="quokkaWombat9"',
        ["quokkaWombat9"],
    ),
    # review of f706ab5 (A-B1): a real SigV4 header signs several headers,
    # and RFC 7616 lays Digest params out one per folded line
    (
        "Authorization: AWS4-HMAC-SHA256 Credential=AKIAQZHUNTER/20261107/us-east-1/s3/aws4_request, "
        f"SignedHeaders=host;range;x-amz-date, Signature={_SIGV4}",
        ["AKIAQZHUNTER", _SIGV4],
    ),
    (
        "Authorization: AWS4-HMAC-SHA256 Credential=AKIAQZHUNTER/20261107/us-east-1/s3/aws4_request,"
        f"SignedHeaders=host;x-amz-date,Signature={_SIGV4}",
        ["AKIAQZHUNTER", _SIGV4],
    ),
    (
        "Authorization: AWS4-HMAC-SHA256 Credential=AKIAQZHUNTER/20261107/us-east-1/s3/aws4_request,\n"
        f"  SignedHeaders=host;x-amz-date,\n  Signature={_SIGV4}",
        ["AKIAQZHUNTER", _SIGV4],
    ),
    (_DIGEST_RFC7616, [_CNONCE, _RESPONSE]),
    (_DIGEST_RFC7616.replace("\n", "\r\n"), [_CNONCE, _RESPONSE]),
    (_DIGEST_RFC7616.replace(",\n    ", "\n    , "), [_CNONCE, _RESPONSE]),
    (
        'Authorization: Signature keyId="k1",\n  algorithm="hmac-sha256",\n'
        '  headers="(request-target) date",\n  signature="quokkaWombat9"',
        ["quokkaWombat9"],
    ),
]


@pytest.mark.parametrize("depth", DEPTHS)
@pytest.mark.parametrize(("text", "secrets"), AUTH_PARAM_CASES)
def test_auth_param_lists_are_withheld(
    text: str, secrets: list[str], depth: int
) -> None:
    """Nothing after the scheme survives: two or more comma-joined params,
    one quoted param with a known auth-param name, or a quoted value cut
    off before its closing quote."""
    x = dumps(text, depth)
    for secret in secrets:
        assert _leaks(x, secret) == [], secret


@pytest.mark.parametrize(
    ("text", "secret"),
    [
        ("Authorization: Basic Zm9vOmJhcg<br>next", "Zm9vOmJhcg"),
        ("| Authorization: Basic Zm9vOmJhcg|", "Zm9vOmJhcg"),
        ("Authorization: Basic Zm9vOmJhcg!", "Zm9vOmJhcg"),
        ("Authorization: Basic Zm9vOmJhcg&x=1", "Zm9vOmJhcg"),
        ("Authorization: Basic Zm9vOmJhcg`", "Zm9vOmJhcg"),
        ("Authorization: Basic Zm9vOmJhcg:", "Zm9vOmJhcg"),
        ("Authorization: Token keyid:quokkaWombat9", "quokkaWombat9"),
        ("Authorization: SharedKey acct:quokkaWombat9=", "quokkaWombat9"),
        ("Authorization: Token qzhunterwombat", "qzhunterwombat"),
        ("Authorization: ApiKey Qzhunterwombat", "Qzhunterwombat"),
        ("Authorization: Basic QZHUNTERWOMBAT", "QZHUNTERWOMBAT"),
        ("Authorization: Basic\r\n Zm9vOmJhcg", "Zm9vOmJhcg"),
        ("Authorization: Basic [Zm9vOmJhcg]", "Zm9vOmJhcg"),
        ("Authorization: Basic {Zm9vOmJhcg}", "Zm9vOmJhcg"),
        ("Authorization: Basic <Zm9vOmJhcg>", "Zm9vOmJhcg"),
    ],
)
def test_single_word_credentials_behind_any_terminator(text: str, secret: str) -> None:
    """A token68 credential ends at any character outside its run (re-check
    S2), may be `id:secret`, wrapped, or after an obs-fold."""
    for depth in DEPTHS:
        assert _leaks(dumps(text, depth), secret) == [], depth


#: A credential before a backslash that starts no JSON escape: no decoded
#: view reads it, so the raw text must (review of f706ab5, A-S1).
NON_JSON_ESCAPE_TAILS = [
    "Authorization: Basic {c}\\x0d\\x0a",
    "Authorization: Basic {c}\\'",
    "Authorization: Token {c}\\ more",
    "b'Authorization: Basic {c}\\x00'",
    "Authorization: Basic {c}\\0",
    "Authorization: Basic {c}\\",
]


@pytest.mark.parametrize("cred", ["zmvvomjhcgxyz", "abc123def", "Zm9vOmJhcg"])
@pytest.mark.parametrize("template", NON_JSON_ESCAPE_TAILS)
def test_a_credential_before_a_non_json_escape(template: str, cred: str) -> None:
    for depth in DEPTHS:
        assert _leaks(dumps(template.format(c=cred), depth), cred) == [], depth


#: Benign Authorization lines: every surface equals the frozen redactor's
#: output at every depth (review B-B1, A-S7, A-Q3, re-check S1).
BENIGN_AUTH = [
    "authorization=none user=bob status=200",
    "ts=1 level=info authorization=missing request_id=abc123 path=/x",
    "Authorization: denied 403",
    "Authorization: failed (401) for user bob",
    "Authorization: none -- skipping",
    "Proxy-Authorization: none",
    "Authorization: denied for user bob",
    "Set the Authorization: Basic header",
    "Authorization: Basic authentication required",
    "Authorization: none",
    "Supported: Authorization: Basic auth",
    "authorization=none user=bob",
    "authorization=none level=warn msg=retry",
    "Authorization: required, see docs",
    "Authorization:\n  required for admin",
    "Authorization: denied 403 (retry in 30s)",
    "Authorization: required for admin",
    "Authorization: Basic auth is not supported",
    "Authorization: Bearer xyz9abc12 (expired)",
    "Authorization: Bearer xyz9abc12 then retry2x",
    "Authorization: failed at 10:30 retry",
    "Authorization: expired 2026-11-07",
    # re-check S1: a single quoted field is not a credential
    "authorization=none reason=\"no header\"",
    "authorization=denied user=\"bob\"",
    "level=warn authorization=missing msg=\"token refresh failed\"",
    "authorization=missing msg=\"retry later\" request_id=abc",
    "Authorization: none detail=\"see docs\"",
    "authorization=none reason='no header'",
    # a bracketed placeholder after the scheme (review of f706ab5, A-N2)
    "Authorization: Basic (none)",
    "Authorization: Basic [empty]",
    "Authorization: none; retry later",
]  # fmt: skip

BENIGN_ORACLES: dict[str, Callable[[str], object]] = {"E": _main_e}
BENIGN_SURFACES: dict[str, Callable[[str], object]] = {"E": E}


@pytest.mark.parametrize("depth", [0, 1, 2])
@pytest.mark.parametrize("text", BENIGN_AUTH)
def test_benign_authorization_lines_equal_main(text: str, depth: int) -> None:
    x = dumps(text, depth)
    for name, run in BENIGN_SURFACES.items():
        assert run(x) == BENIGN_ORACLES[name](x), name


def test_benign_authorization_log_lines(monkeypatch: pytest.MonkeyPatch) -> None:
    """`_LOG_TAILS` after an `authorization` key, on every surface: the next
    field is kept as main keeps it (review B-B1). A sibling of
    `test_code_and_log_text_keep_the_next_field`; `_LOG_KEYS` is unchanged."""
    from tests import test_redaction_additive as T

    bad = []
    for tail in T._LOG_TAILS:
        for prefix in T._LOG_PREFIXES:
            for value in ("none", "missing", "denied"):
                text = f"{prefix}authorization={value}{tail}"
                for name, run in BENIGN_SURFACES.items():
                    if run(text) != BENIGN_ORACLES[name](text):
                        bad.append((name, text))
    assert bad == [], bad[:10]


_AUTH_PROSE = [
    "the Authorization: {w} {x} was not accepted",
    "Authorization: {w} {x} please",
    "check that Authorization: {w} is set",
    "Authorization: {w}, {x} or none",
]


def test_benign_authorization_prose() -> None:
    """`Authorization: <word> <words>` clauses keep main's output (review
    A-N3; `_random_prose` itself is unchanged)."""
    words = ["required", "missing", "denied", "Basic", "none", "header", "Expired", "OPTIONAL"]  # fmt: skip
    bad = []
    for template, w, x in itertools.product(_AUTH_PROSE, words, words):
        text = template.format(w=w, x=x)
        for name, run in BENIGN_SURFACES.items():
            if run(text) != BENIGN_ORACLES[name](text):
                bad.append((name, text))
    assert bad == [], bad[:10]


# --- residuals (owner-accepted; documented in docs/redaction-401-narrow.md) ---

#: Credentials the rule keeps, as main does (review of f706ab5, B-S2, B-S5).
KEPT_RESIDUALS = [
    # an all-letter single-case word followed by another word (owner Q3)
    ("Authorization: Basic qzhunterwombat was rejected", "qzhunterwombat"),
    # an all-letter single-case word of 7 letters or fewer (owner Q3)
    ("Authorization: Basic qzhuntr", "qzhuntr"),
    # a credential that is a bare number, a date or a time (owner Q2)
    ("Authorization: Pin 12345678", "12345678"),
    ("Authorization: Pin 2026-11-07", "2026-11-07"),
    ("Authorization: Pin 10:30:15", "10:30:15"),
    # a malformed token68 split by a comma: the rest reads as a list item
    ("Authorization: Basic Zm9v,OmJhcg", "OmJhcg"),
    # one unquoted auth-param (owner Q2: two or more, or a quoted known name)
    ("Authorization: Digest response=ab12cd34ef", "ab12cd34ef"),
    ("Authorization: AWS4-HMAC-SHA256 Signature=ab12cd34ef", "ab12cd34ef"),
    # auth-params joined by spaces, not commas (not RFC 7616): only the
    # first, quoted, known-name param is taken
    ('Authorization: Digest username="bob" realm="r" response="ab12cd34ef"', "ab12cd34ef"),
]  # fmt: skip

#: Benign words the rule redacts, which main keeps (review of f706ab5, B-S5).
OVER_REDACTED_RESIDUALS = [
    # a token68-shaped word after the scheme word
    ("Authorization: denied user_id", "user_id"),
    # two or more `name=value` fields after the scheme word (owner Q2)
    ("authorization=none user=bob, retry=2", "bob"),
    # one quoted field with a known auth-param name (owner r2 decision)
    ('authorization=none realm="internal"', "internal"),
]


def _residual_outputs(text: str) -> Iterator[tuple[str, int, str, str]]:
    for depth in DEPTHS:
        x = dumps(text, depth)
        for name, run in SURFACES.items():
            yield name, depth, run(x), str(BENIGN_ORACLES[name](x))


@pytest.mark.parametrize(("text", "kept"), KEPT_RESIDUALS)
def test_disclosed_residuals(text: str, kept: str) -> None:
    """Pinned so a fix has to flip them on purpose; main keeps them too."""
    for name, depth, ours, main in _residual_outputs(text):
        assert kept in ours, (name, depth)
        assert kept in main, (name, depth)


@pytest.mark.parametrize(("text", "word"), OVER_REDACTED_RESIDUALS)
def test_disclosed_over_redactions(text: str, word: str) -> None:
    """Pinned the same way: main keeps the word, the rule withholds it."""
    for name, depth, ours, main in _residual_outputs(text):
        assert word not in ours, (name, depth)
        assert word in main, (name, depth)


# --- mutants (review B-S4, re-check N4) ---


def _benign_changes() -> list[str]:
    return [t for t in BENIGN_AUTH if E(t) != _main_e(t)]


def test_mutant_credential_gate_always_true(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _benign_changes() == []
    monkeypatch.setattr(A, "_credential_shaped", lambda text, cred, after: True)
    assert len(_benign_changes()) >= 5


def _patched_rule(old: str, new: str) -> Callable[[str], list[A.Span]]:
    source = textwrap.dedent(inspect.getsource(A._authorization_credential_spans))
    assert source.count(old) == 1, old
    namespace = dict(vars(A))
    exec(source.replace(old, new), namespace)
    return namespace["_authorization_credential_spans"]  # type: ignore[no-any-return]


def test_mutant_params_gate_accepts_one_bare_param(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A source-patched rule that accepts any single param: the logfmt
    guards change (`authorization=none user=bob status=200`)."""
    mutant = _patched_rule('if params.group("more") or (', "if True or (")
    monkeypatch.setattr(A, "_authorization_credential_spans", mutant)
    assert E("authorization=none user=bob status=200") != _main_e(
        "authorization=none user=bob status=200"
    )


def test_mutant_any_quoted_param_counts(monkeypatch: pytest.MonkeyPatch) -> None:
    """The r2 prototype's rule (any single quoted value): re-check S1's
    quoted logfmt fields change."""
    mutant = _patched_rule(
        'and (name in _KNOWN_AUTH_PARAMS or name.startswith("oauth_"))', "and True"
    )
    monkeypatch.setattr(A, "_authorization_credential_spans", mutant)
    text = 'authorization=none reason="no header"'
    assert E(text) != _main_e(text)


# ======================================================== composition ==== #


def test_the_floor_holds_for_the_new_corpora() -> None:
    """Every output is the frozen redactor's output with markers written in."""
    texts = [_case(r)[0] for r in _FAST_ROWS] + BENIGN_AUTH
    texts += [t for t, _ in AUTH_PARAM_CASES]
    bad = [t for t in texts if not _is_additive_of(E(t), M.sanitize_auth_diagnostic(t, max_length=None))]  # fmt: skip
    assert bad == [], bad[:5]


def test_idempotent_on_the_named_corpora() -> None:
    """f(f(t)) == f(t) on these corpora (not a general property: re-check N2)."""
    texts = [_case(r)[0] for r in _FAST_ROWS[::3]] + BENIGN_AUTH
    bad = [t for t in texts for run in SURFACES.values() if run(run(t)) != run(t)]
    assert bad == [], bad[:5]
