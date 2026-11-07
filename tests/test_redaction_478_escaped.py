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
from tests.test_redaction_401_restore import BENIGN_URLS, _envelope
from tests.test_redaction_additive import (
    CREDENTIALS,
    PROSE,
    G,
    _is_additive_of,
    _json_fuzz_corpus,
    _main_e,
    _main_p,
    _main_process,
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


def P(text: str) -> str:
    return _policy().redact_secrets(text)


def POs(text: str) -> str:
    out = _policy().process_output(text, redact=True, max_bytes=G.BIG)["result"]
    assert isinstance(out, str)
    return out


def _pod(text: str) -> object:
    return _policy().process_output({"t": text}, redact=True, max_bytes=G.BIG)["result"]


def POd(text: str) -> str:
    out = _pod(text)
    return out if isinstance(out, str) else json.dumps(out, ensure_ascii=False)


def dumps(text: str, depth: int) -> str:
    for _ in range(depth):
        text = json.dumps(text)
    return text


#: The surfaces every #478 assertion runs on.
SURFACES: dict[str, Callable[[str], str]] = {"E": E, "P": P, "POs": POs, "POd": POd}
#: The text depths every #478 assertion runs at.
DEPTHS = (0, 1, 2)


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
    [r for r in _FAST_ROWS if r[3] in DEPTHS],
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
    # a JSON escape (`\\\\`) whose decoded `\\'` no view decodes further
    "'Authorization: Basic {c}\\\\'",
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

BENIGN_ORACLES: dict[str, Callable[[str], object]] = {
    "E": _main_e,
    "P": _main_p,
    "POs": _main_p,
    "POd": lambda t: _main_process({"t": t}),
}
BENIGN_SURFACES: dict[str, Callable[[str], object]] = {
    "E": E,
    "P": P,
    "POs": POs,
    "POd": _pod,
}


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
    runs = [run for name, run in SURFACES.items() if name != "POd"]  # POd wraps
    bad = [t for t in texts for run in runs if run(run(t)) != run(t)]
    assert bad == [], bad[:5]


# ============================================== A: keyed values, escaped ==== #

KEYS = [
    "token",
    "api_key",
    "apikey",
    "password",
    "secret",
    "client_secret",
    "access_token",
    "refresh_token",
    "private_key",
]
VALUE = "quokkaWombat9"
KEYED_FORMS = [
    '"{k}": "{v}"',
    '"{k}":"{v}"',
    '{k}: "{v}"',
    '{k} = "{v}"',
    '{{"{k}": "{v}"}}',
    '{{"{k}": ["{v}"]}}',
    '"{k}": "https://example.test/a?v={v}\\" more',
    '"{k}": "a\\"{v}"',
    '{k}: "{v} two"',
    '"{k}": "{v}\\\\"',
    # the URL pass runs into the next line's key in escaped text (review A-B1)
    "see https://a.test/x?q=1\n{k}: {v}",
]


@pytest.mark.parametrize("depth", [0, 1, 2])
@pytest.mark.parametrize("form", KEYED_FORMS)
def test_keyed_values_are_withheld_at_every_depth(form: str, depth: int) -> None:
    bad = []
    for key in KEYS:
        text = dumps(form.format(k=key, v=VALUE), depth)
        bad.extend((key, name) for name in _leaks(text, VALUE))
    assert bad == []


def test_keyed_values_in_other_json_spellings() -> None:
    """`ensure_ascii=False` and `indent=2` spellings, a non-ASCII value, at
    depths 1-2 (review B-N6)."""
    forms = [
        '"{k}": "{v}"',
        '{k}: "{v}"',
        '{{"{k}": "{v}"}}',
        '"{k}": "https://example.test/a?v={v}\\" more',
    ]
    bad = []
    for key in ["token", "password", "client_secret", "private_key"]:
        for form in forms:
            text = form.format(k=key, v="quokkaW\u00f6mbat9\u00e9")
            for _, ascii_only, indent in G._SPELLINGS:
                for depth in (1, 2):
                    x = text
                    for _ in range(depth):
                        x = json.dumps(x, ensure_ascii=ascii_only, indent=indent)
                    bad.extend((key, form, depth, n) for n in _leaks(x, "quokka"))
    assert bad == [], bad[:5]


# ======================================================= B: issue repros ==== #


@pytest.mark.parametrize(
    "prefix",
    ['"token": "', '"api_key": "', '"password": "', '"secret": "', '"client_secret": "', 'token: "'],
)  # fmt: skip
def test_issue_repro_keyword_depth_loop(prefix: str) -> None:
    """Consiliency/pmcp#478, verbatim: the depth loop over both entry points."""
    text = prefix + 'https://example.test/a?v=quokkawombat\\" more'
    for depth in range(3):
        assert _leaks(dumps(text, depth), "quokkawombat") == [], depth


def test_issue_repro_password_after_a_url() -> None:
    text = '{"password": "https://example.test/a?v=1\\"password":"SECRET"}'
    for depth in range(3):
        assert _leaks(dumps(text, depth), "SECRET") == [], depth


def test_issue_repro_basic_credential() -> None:
    for name, run in SURFACES.items():
        expected = "Authorization: [REDACTED] [REDACTED]"
        if name == "POd":
            expected = json.dumps({"t": expected})
        assert run("Authorization: Basic Zm9vOmJhcg") == expected, name


# ======================================== escape views: layers and gating ==== #


def test_a_layer_maps_back_over_whole_escapes() -> None:
    raw = 'a\\"b\\u00e9c\\\\d'
    view, layer = A._unescape_layer(raw)  # type: ignore[misc]
    assert view == 'a"b\u00e9c\\d'
    # a start on a decoded character maps to its backslash; an end after it
    # maps past the whole escape
    assert [layer.to_outer(i) for i in range(len(view) + 1)] == [0, 1, 3, 4, 10, 11, 13, 14]  # fmt: skip
    assert A._unescape_layer("no escapes \\x here") is None


@pytest.mark.parametrize(
    ("text", "secret"),
    [
        ('{\\"\\u0070assword\\": \\"quokkawombat\\"}', "quokkawombat"),
        ('\\"pass\\u0077ord\\": \\"quokkawombat\\"', "quokkawombat"),
        ('\\"Authorizatio\\u006e\\": \\"Basic Zm9vOmJhcg\\"', "Zm9vOmJhcg"),
        ('{"u":"https:\\/\\/example.test\\/cb?ticket=quokkaWombat9"}', "quokkaWombat9"),
        ('{"a": "password: \\"quokkawombat\\""}', "quokkawombat"),
        ('"password: \\"quokkawombat\\""', "quokkawombat"),
    ],
)
def test_the_prefilter_reads_each_decoded_view(text: str, secret: str) -> None:
    """A key spelled with escapes is found at the layer where it decodes
    (review A-S4, B-N4); an in-text escape layer is one more (A-N1)."""
    for depth in DEPTHS:
        assert _leaks(dumps(text, depth), secret) == [], depth


def test_non_json_escape_dialects_are_not_decoded() -> None:
    """Only JSON string escapes are decoded (review A-N5): `\\x`, `\\'`,
    `\\U` and `\\u{...}` are copied as they are."""
    for text in ["\\x41\\'\\U0001F600\\u{41}", "\\q\\z"]:
        assert A._unescape_layer(text) is None


# ===================================================== D: #401 composition ==== #


def test_a_view_redaction_inside_a_restorable_url_cancels_the_restore() -> None:
    """The URL was eligible for a restore; a carried span changed the text
    after the snapshot, so the output is the unrestored text."""
    text = json.dumps('"token": "https://example.test/watch?v=quokkawombat\\\\" more')
    with AU._url_tail_restore() as record:
        base = AU._sanitize_base(text)
        carried = A.carried_view_spans(text, base, AU._usable_edits(record))
    assert record.spans and carried
    out = E(text)
    assert out == A.redact_additive(base, extra=carried)
    assert "quokkawombat" not in out


def test_restore_certificate_mutant(monkeypatch: pytest.MonkeyPatch) -> None:
    """A restore that trusted the URL pass's snapshot instead of checking the
    final text would re-expose what the carried spans removed."""
    real = AU._restore_url_tails

    def from_snapshot(
        text: str, record: AU._UrlTailRecord, max_length: int | None = None
    ) -> str:
        return real(record.snapshot or text, record, max_length)

    text = json.dumps('"token": "https://example.test/watch?v=quokkawombat\\\\" more')
    assert "quokkawombat" not in E(text)
    monkeypatch.setattr(AU, "_restore_url_tails", from_snapshot)
    assert "quokkawombat" in E(text)


@pytest.mark.parametrize("depth", [0, 1])
def test_a_benign_url_beside_a_benign_keyed_sibling_still_restores(depth: int) -> None:
    text = dumps(
        '{"href": "https://www.youtube.com/watch?v=kQmXtrmQ5Zg\\"", "token_type": "Bearer"}',
        depth,
    )
    assert E(text) == text


ISSUE_417 = [
    ('{"u":"https:\\/\\/example.test\\/cb?ticket=probe_escaped_query_A7"}', "probe_escaped_query_A7"),
    ('{"u":"https:\\/\\/example.test\\/cb?key=probe_escaped_query_A7"}', "probe_escaped_query_A7"),
]  # fmt: skip
ISSUE_417_CONTROLS = [
    '{"u":"https:\\/\\/example.test\\/cb?page=2"}',
    '{"u":"https:\\/\\/www.youtube.com\\/watch?v=kQmXtrmQ5Zg\\\\"}',
]


@pytest.mark.parametrize(("text", "secret"), ISSUE_417)
def test_issue_417_repro_is_withheld(text: str, secret: str) -> None:
    """Partly addresses Consiliency/pmcp#417: its repro keys are withheld at
    every depth, and the document stays a document."""
    for depth in DEPTHS:
        x = dumps(text, depth)
        assert _leaks(x, secret) == [], depth
        json.loads(E(x))


@pytest.mark.parametrize("text", ISSUE_417_CONTROLS)
def test_issue_417_negative_controls_equal_main(text: str) -> None:
    for depth in (0, 1, 2):
        x = dumps(text, depth)
        for name, run in BENIGN_SURFACES.items():
            assert run(x) == BENIGN_ORACLES[name](x), (name, depth)


# ================================================ F: depth consistency ==== #

#: Excluded from this property (named, review B-N9): the context-free shapes
#: (`_shape_spans`, which read the base output), the default and operator
#: policy patterns and their rev-10 forms (owner decision,
#: Consiliency/pmcp#482), escape depth 5 and more, and non-JSON escape
#: dialects.
_SWALLOWED = [
    "GET https://a.test/x?q=1\nAuthorization: {v}\nHost: a",
    "GET https://a.test/x#frag\nAuthorization: {v}",
    "GET https://a.test/x?q=1\nBearer {v}",
    "GET https://a.test/x?q=1\nX-Api-Key: {v}",
    "GET https://a.test/x?q=1\ntoken: {v}",
    "GET https://a.test/x?q=1\npassword: {v}",
    "GET https://a.test/x?q=1\nauth: {v}",
]
_SWALLOWED_VALUES = [
    "hunter2",
    "secretvalue",
    "Zm9vOmJhcg",
    "qzhunterwombat",
    "Secretvalue",
]


def _depth_violations(texts: list[str]) -> list[tuple[str, int, str]]:
    bad = []
    for text in texts:
        gone = [
            w for w in set(re.findall(r"[A-Za-z0-9]{8,}", text)) if w not in E(text)
        ]
        if not gone:
            continue
        for depth in (1, 2):
            x = dumps(text, depth)
            for name, run in SURFACES.items():
                out = run(x)
                if any(w in out for w in gone):
                    bad.append((text[:60], depth, name))
    return bad


#: Escaped-only residuals inside the depth cap (review of f706ab5, A-N1;
#: documented): withheld at depth 0, kept at depth 1, where main keeps them
#: too. A second `Authorization` key taken as the first one's quoted value,
#: and a Bearer value opening with `\\"`, which the view rules stop at.
ESCAPED_RESIDUALS = [
    ('Authorization: Basic "Authorization": Basic keyid:QQhP9ZfhpvFq, x', "QQhP9ZfhpvFq"),
    ('bearer\t\\\\"QRj6hgQwHzFk; x', "QRj6hgQwHzFk"),
]  # fmt: skip


@pytest.mark.parametrize(("text", "secret"), ESCAPED_RESIDUALS)
def test_disclosed_escaped_residuals(text: str, secret: str) -> None:
    assert secret not in E(text)
    assert secret in E(dumps(text, 1))
    assert secret in _main_e(dumps(text, 1))


def test_what_depth_0_removes_is_removed_at_depths_1_and_2() -> None:
    """Whatever `E` removes at depth 0 is absent at depths 1-2, on the
    credentials, the board rows of four review rounds, and the shapes whose
    key the URL pass swallows in escaped text (re-check S3)."""
    from tests.test_redaction_additive import _board_texts

    texts = [c[1] for c in CREDENTIALS] + _board_texts()
    texts += [t.format(v=v) for t in _SWALLOWED for v in _SWALLOWED_VALUES]
    assert _depth_violations(texts) == []


def test_mutant_swallowed_keys_not_read(monkeypatch: pytest.MonkeyPatch) -> None:
    texts = [t.format(v=v) for t in _SWALLOWED[:3] for v in _SWALLOWED_VALUES]
    assert _depth_violations(texts) == []
    monkeypatch.setattr(A, "_swallowed_key_spans", lambda view, layers, edits: [])
    assert len(_depth_violations(texts)) >= 10


def test_base_rules_in_views_are_the_base_pass_rules() -> None:
    """`_BASE_AUTHORIZATION_RE` and `_BASE_BEARER_RE` are the base pass's
    patterns, with a value group that stops at a quote or backslash."""
    source = inspect.getsource(AU._sanitize_base)
    for pattern in (A._BASE_AUTHORIZATION_RE, A._BASE_BEARER_RE):
        base = pattern.pattern.replace(r"""(?P<value>[^\s,;\"'\\]+)""", r"[^\s,;]+")
        assert base != pattern.pattern
        assert f'r"{base}"' in source


# ========================================== G: the 400-character cut ==== #


def _478_corpus() -> list[str]:
    texts = [f.format(k=k, v=VALUE) for f in KEYED_FORMS for k in KEYS[::2]]
    texts += [t for t, _ in AUTH_PARAM_CASES] + [t for t, _ in ISSUE_417]
    texts += ['{"password": "https://example.test/a?v=1\\"password":"SECRET"}']
    return [dumps(t, d) for t in texts for d in range(3)]


def test_floor_with_the_default_400_character_cut() -> None:
    """The A, B and D corpora under the default cut: every stretch outside a
    marker occurs, in order, in the frozen redactor's full output."""
    bad = [
        t[:80]
        for t in _478_corpus()
        if not _is_additive_of(AU.sanitize_auth_diagnostic(t), _main_e(t))
    ]
    assert bad == [], bad[:5]


@pytest.mark.parametrize(
    "text",
    [
        "x" * 380 + '"token": "quokkawombatquokkawombat"',
        "x" * 380 + "Authorization: Basic Zm9vOmJhcgZm9vOmJhcg",
    ],
)
@pytest.mark.parametrize("depth", [1, 2])
def test_a_secret_straddling_the_cut_is_withheld(text: str, depth: int) -> None:
    out = AU.sanitize_auth_diagnostic(dumps(text, depth))
    assert len(out) == 400
    assert "quokka" not in out and "Zm9v" not in out


# ============================================= H: mutants of the views ==== #

_MUTANT_FORMS = [
    '"{k}": "{v}"',
    '{k}: "{v}"',
    '{{"{k}": "{v}"}}',
    '"{k}": "https://example.test/a?v={v}\\" more',
    "see https://a.test/x?q=1\n{k}: {v}",
    '{{"a": "{k}: \\"{v}\\""}}',
    # the URL pass drops a long fragment: spans after it must shift
    "GET https://a.test/x#frag-0123456789abcdef\n{k}: {v}",
]


def _mutant_effects() -> tuple[int, int]:
    """Leaks, and crashes (a span carried past the end of the text), over
    the mutant forms, counted apart so a test says which it saw."""
    leaks = crashes = 0
    for key in ["token", "api_key", "password", "secret", "client_secret"]:
        for form in _MUTANT_FORMS:
            for depth in range(3):
                text = dumps(form.format(k=key, v=VALUE), depth)
                try:
                    leaks += len(_leaks(text, VALUE))
                except IndexError:
                    crashes += 1
    return leaks, crashes


def _mutant_leaks() -> int:
    """A mutant is killed by a leak or a crash; the unmutated code has
    neither (`test_the_mutant_forms_neither_leak_nor_crash`)."""
    leaks, crashes = _mutant_effects()
    return leaks + crashes


def test_the_mutant_forms_neither_leak_nor_crash() -> None:
    assert _mutant_effects() == (0, 0)


def test_mutant_views_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _mutant_leaks() == 0
    monkeypatch.setattr(
        A,
        "escape_view_spans",
        lambda raw, covers=None, url_edits=None: A._authorization_credential_spans(raw),
    )
    assert _mutant_leaks() >= 50


def test_mutant_identity_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(A, "_transport_one", lambda spans, edits: spans)
    assert _mutant_leaks() >= 10


def test_mutant_escape_view_cap_2(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(A, "_MAX_ESCAPE_VIEWS", 2)
    assert _mutant_leaks() >= 5


def test_mutant_escape_view_cap_3(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cap 4 (owner decision): an in-text escape at text depth 2 through the
    structured twin is escape depth 4."""
    assert _mutant_leaks() == 0
    monkeypatch.setattr(A, "_MAX_ESCAPE_VIEWS", 3)
    assert _mutant_leaks() >= 1


def test_mutant_no_bare_value_filter_in_views(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without the view filter, `Basic auth is not supported` loses prose at
    depth 2 that depth 0 keeps (plan section 2.2)."""
    text = dumps('"Authorization: Basic auth is not supported"', 2)
    assert E(text) == _main_e(text)
    monkeypatch.setattr(A, "_authorization_view_spans", A._authorization_spans)
    assert E(text) != _main_e(text)


@pytest.mark.parametrize("depth", [0, 1, 2])
def test_preceding_rows_of_the_401_file(depth: int) -> None:
    """Every `PRECEDING` row of `tests/test_redaction_401_restore.py` (its 14
    strict #478 xfails included) is withheld on these surfaces."""
    from tests.test_redaction_401_restore import PRECEDING, SECRET

    bad = []
    for text in PRECEDING:
        secrets = [s for s in (SECRET, "quokkawombat") if s in text]
        x = dumps(text, depth)
        bad.extend((text, s, n) for s in secrets for n in _leaks(x, s))
    assert bad == []


def test_nested_carried_spans_are_dropped_unless_the_outer_starts_on_a_marker() -> None:
    text = "aaaa [REDACTED]bbbb cccc"
    on_marker = text.index("[")
    assert A._drop_nested(text, [(0, 24, "R"), (2, 4, "R"), (0, 24, "R")]) == [
        (0, 24, "R")
    ]
    # an outer span on a marker replaces only the glued run: keep what it nests
    assert A._drop_nested(text, [(on_marker, 24, "R"), (20, 24, "R")]) == [
        (on_marker, 24, "R"),
        (20, 24, "R"),
    ]


def test_mutant_carried_spans_not_deduplicated(monkeypatch: pytest.MonkeyPatch) -> None:
    """The slow sweep's shape: every query value of one long URL after an
    escape is a view span inside the same URL edit, and all are carried onto
    one span. Without deduplication the merge walks every marker of that
    span once per copy: quadratic."""
    from tests.test_redaction_additive import _work

    def shape(n: int) -> str:
        return "https://h/?\\\\" + "&token=%2541" * (n // 12)

    def growth() -> float:
        small, large = (_work(E, shape(n)) for n in (2_048, 8_192))
        return large / small

    assert growth() < 5

    def undeduplicated(
        spans: list[A.Span],
        edit_lists: list[list[A.Edit]],
        final: str,
        drop_nested: bool = True,
    ) -> list[A.Span]:
        for edits in edit_lists:
            spans = A._transport_one(spans, edits)
        return A.widen_over_escapes(final, spans) if spans else []

    monkeypatch.setattr(A, "transport_spans", undeduplicated)
    assert growth() > 6  # linear is ~4


# ================================================= policy surface (A-S5) ==== #


@pytest.mark.parametrize(
    ("text", "secret"),
    [
        ('password: "quokka secretwombat"', "secretwombat"),
        ('api_key = "quokka secretwombat"', "secretwombat"),
        ('pwd: "a quokkawombat"', "quokkawombat"),
        ('password: "token abc more secretwombat"', "secretwombat"),
    ],
)
def test_policy_damage_at_depth_0_is_repaired(text: str, secret: str) -> None:
    """A default pattern rewrites `password: "a b"` to `password:
    [REDACTED]"a b"`; the context rules' spans of the base output are
    carried through its edits (review A-S5)."""
    for depth in DEPTHS:
        assert _leaks(dumps(text, depth), secret) == [], depth


def test_base_output_spans_only_when_a_pattern_changed_something(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Re-check N3: with no pattern edit, `redact_additive` already reads the
    base output, so its context spans are not computed twice."""
    from pmcp.policy import policy as policy_module

    def fail(*args: object, **kwargs: object) -> list[A.Span]:
        raise AssertionError("computed without a pattern edit")

    monkeypatch.setattr(policy_module, "_context_spans", fail)
    assert P("plain text with nothing to hide") == "plain text with nothing to hide"
    with pytest.raises(AssertionError):
        P('password: "quokka secretwombat"')


def test_operator_pattern_that_changes_length_before_a_view_span() -> None:
    """An operator pattern rewrites text before the escaped pair: the span is
    shifted exactly (owner decision Q1 keeps operator patterns out of the
    views; their edits are still carried, Consiliency/pmcp#482)."""
    policy = PolicyManager()
    policy._redaction_regexes = [
        *policy._redaction_regexes,
        re.compile(r"ACCT-[0-9]+", re.IGNORECASE),
    ]
    text = 'ACCT-123456789 then \\"token\\": \\"quokkawombat\\"'
    out = policy.redact_secrets(text)
    assert out == '[REDACTED] then \\"token\\": \\"[REDACTED]\\"'
    assert _is_additive_of(out, M.redact_secrets(text, policy._redaction_regexes))


def test_operator_pattern_that_overlaps_a_view_span() -> None:
    policy = PolicyManager()
    policy._redaction_regexes = [
        *policy._redaction_regexes,
        re.compile("quokka", re.IGNORECASE),
    ]
    text = '\\"token\\": \\"quokkawombat\\"'
    out = policy.redact_secrets(text)
    assert "wombat" not in out
    assert _is_additive_of(out, M.redact_secrets(text, policy._redaction_regexes))


def test_operator_patterns_are_consulted_on_a_query_value_in_a_view() -> None:
    """Operator patterns are not matched against the decoded views, but, as
    at depth 0, they are asked about a percent-decoded query value of a URL
    found in a view (`_url_spans` -> `covers`; review of f706ab5, B-S1,
    Consiliency/pmcp#482)."""
    text = '{"u":"https:\\/\\/a.test\\/cb?q=%71uokka1"}'
    assert P(text) == text
    assert A.escape_view_spans(text, _policy()._pattern_matches) == []
    policy = PolicyManager()
    policy._redaction_regexes = [
        *policy._redaction_regexes,
        re.compile("quokka", re.IGNORECASE),
    ]
    assert policy.redact_secrets(text).endswith('?q=[REDACTED]"}')
    assert A.escape_view_spans(text, policy._pattern_matches) != []


def test_recorded_pattern_edits_replay_every_pattern(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each policy pattern's recorded edits describe that pattern's pass
    exactly, and the policy output stays additive of the frozen redactor's
    (re-check S4)."""
    from pmcp.policy import policy as policy_module

    policy = PolicyManager()
    log: list[tuple[str, str]] = []

    class _Logged:
        def __init__(self, regex: re.Pattern[str]) -> None:
            self.regex = regex

        def sub(self, repl: Callable[[re.Match[str]], str], text: str) -> str:
            out = self.regex.sub(repl, text)
            log.append((text, out))
            return out

        def search(self, text: str) -> re.Match[str] | None:
            return self.regex.search(text)

    regexes = list(policy._redaction_regexes)
    policy._redaction_regexes = [_Logged(r) for r in regexes]  # type: ignore[misc]
    policy._additive_regexes = lambda: [  # type: ignore[method-assign]
        policy_module._ADDITIVE_FOR_DEFAULT.get(r.pattern, r) for r in regexes
    ]
    captured: list[list[list[A.Edit]]] = []
    real = policy_module.transport_spans

    def capture(
        spans: list[A.Span],
        edit_lists: list[list[A.Edit]],
        final: str,
        drop_nested: bool = True,
    ) -> list[A.Span]:
        captured.append(edit_lists)
        return real(spans, edit_lists, final, drop_nested=drop_nested)

    monkeypatch.setattr(policy_module, "transport_spans", capture)
    bad: list[str] = []
    checked = 0
    for text in _replay_corpus()[::2]:
        log.clear()
        captured.clear()
        out = policy.redact_secrets(text)
        if not _is_additive_of(out, _main_p(text)):
            bad.append(f"not additive: {text[:80]!r}")
        if not captured:
            assert all(before == after for before, after in log), text[:80]
            continue
        for (before, after), edits in zip(log, captured[0]):
            checked += 1
            bad.extend(
                f"{e}: {text[:80]!r}" for e in _edit_errors(before, after, edits)
            )
    assert bad == [], bad[:10]
    assert checked > 200, checked


# ====================================== D: the dict -> string trade (#483) ==== #


@pytest.mark.parametrize("url", BENIGN_URLS)
@pytest.mark.parametrize("depth", [1, 2])
def test_benign_url_envelopes_still_restore(url: str, depth: int) -> None:
    payload = {"t": dumps(json.dumps(_envelope(url)), depth - 1)}
    out = _policy().process_output(payload, redact=True, max_bytes=G.BIG)
    assert out["result"] == payload
    assert not out["truncated"]


@pytest.mark.parametrize("key", ["token", "password", "client_secret"])
@pytest.mark.parametrize("depth", [0, 1, 2])
def test_dict_to_string_trade_is_pinned(
    key: str, depth: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Accepted trade (owner decision; follow-up Consiliency/pmcp#483): a
    `process_output(dict)` result holding a #401-restorable URL tail AND a
    value #478 now redacts cancels the restore, so main's encoded `%5C"`
    stays and the result is a string -- with the secret withheld. A
    provenance-tracked restore has to flip this test on purpose.

    Without the #478 spans the result is main's: a dict, the value in clear
    (review of f706ab5, B-N10). So the string comes from those spans."""
    from pmcp.policy import policy as policy_module

    text = dumps(json.dumps({key: "https://example.test/a?v=1\\", "b": "c"}), depth)
    out = _pod(text)
    assert isinstance(out, str)
    assert "example.test/a?v=1" not in out
    monkeypatch.setattr(policy_module, "carried_view_spans", lambda *a, **k: [])
    before = _pod(text)
    assert isinstance(before, dict)
    assert "example.test/a?v=1" in json.dumps(before)


def test_dict_to_string_trade_on_a_mixed_search_result() -> None:
    """Review B's document: a snippet with a restorable href beside a field
    holding a JSON-encoded password. Main returns a dict with the password
    in clear; this returns main's pre-#401 string, password withheld."""
    rows = {
        "results": [
            {
                "snippet": '<Card href="https://www.youtube.com/watch?v=kQmXtrmQ5Zg">Person</Card>',
                "cfg": json.dumps({"password": "quokkaWombat9"}),
            }
        ]
    }
    out = _policy().process_output(rows, redact=True, max_bytes=G.BIG)
    assert isinstance(out["result"], str)
    assert not out["truncated"]
    assert "%5C" in out["result"]
    assert "quokkaWombat9" not in out["result"]


def test_inner_documents_stay_documents() -> None:
    text = json.dumps({"password": "v", "a": [1, "b"]})
    expected = {"password": "[REDACTED]", "a": [1, "b"]}
    assert json.loads(POs(text)) == expected
    assert json.loads(POs(json.dumps(text))) == json.dumps(expected)
    out = _pod(text)
    assert isinstance(out, dict) and json.loads(out["t"]) == expected


# ================================================= cost: the ratio canary ==== #

_CANARY_SHAPES: dict[str, Callable[[int], str]] = {
    "dense keyed pairs at depth 3": lambda n: dumps(
        '"token": "ab12cd34", ' * (n // 80), 3
    ),
    "URL lines then Authorization, escaped": lambda n: json.dumps(
        "GET https://a.test/x?q=1\nAuthorization: Basic Zm9vOmJhcg\n" * (n // 60)
    ),
    "policy-damaged pairs": lambda n: '"password: \\"x1\\" ' * (n // 18),
    "a pure backslash run": lambda n: "\\" * n,
    "escaped keys chained in one URL run": lambda n: "https://a.test/?q=1"
    + "\\ntoken:x" * (n // 9),
}


def _best_cpu(run: Callable[[str], object], text: str) -> float:
    import gc
    import time

    times = []
    for _ in range(3):
        gc.collect()
        started = time.process_time()
        run(text)
        times.append(time.process_time() - started)
    return min(times)


@pytest.mark.slow
@pytest.mark.parametrize("name", sorted(_CANARY_SHAPES))
def test_cost_ratio_canary(name: str) -> None:
    """CPU at 256 KiB, best of 3, against the pre-#478 pipeline on the same
    input (the base pass, the patterns, then the additive pass without
    carried spans): at most 6x (measured at most 2.5x). The work counter in
    `tests/test_redaction_additive.py` stays the hard linearity bound; this
    catches a large constant factor. (The frozen oracle alone is not the
    yardstick: it is the pre-additive redactor, 10-100x cheaper than any
    additive pipeline on these shapes.)"""
    text = _CANARY_SHAPES[name](262_144)[:262_144]
    policy = _policy()

    def pre_e(t: str) -> str:
        return A.redact_additive(AU._sanitize_base(t))

    def pre_p(t: str) -> str:
        return policy._redact_additive(M.redact_secrets(t, policy._redaction_regexes))

    for ours, before in ((E, pre_e), (P, pre_p)):
        assert _best_cpu(ours, text) <= 6 * max(_best_cpu(before, text), 0.01), name
