"""Consiliency/pmcp#401: restore escaped URL tails after every redaction pass.

`sanitize_auth_diagnostic` and `PolicyManager.redact_secrets` record, during the
URL pass, which URLs only had a trailing `).,;\\` run encoded, and the exact
text after that pass. After every later pass they restore the raw run, but
only when the final text is byte-for-byte that snapshot. Everything else is
main's output.

Expectations named ``MAIN_*`` are literal outputs of pmcp main
2a13d471ead38d5f9bcac2115b55c9ee029d5a2a, computed from a `git archive` of
that commit, never from this tree. `tests/_main_redactor.py` is the frozen
pre-additive oracle and is not modified.
"""

from __future__ import annotations

import hashlib
import json
import re

import pytest

from pmcp import auth as A
from pmcp.policy import policy as policy_module
from pmcp.policy.policy import PolicyManager
from tests import _main_redactor as M
from tests.test_redaction_additive import _is_additive_of

SECRET = "Qz9hunterwombat"
U = "https://example.test/watch?v=abc"


def _diag(text: str, max_length: int | None = None) -> str:
    return A.sanitize_auth_diagnostic(text, max_length=max_length)


def _policy(text: str) -> str:
    return PolicyManager().redact_secrets(text)


def _envelope(url: str) -> dict[str, object]:
    rows = {"results": [{"snippet": f'<Card href="{url}">Person</Card>'}]}
    return {
        "content": [{"type": "text", "text": json.dumps(rows)}],
        "structuredContent": rows,
    }


# --- Literal outputs of pmcp main 2a13d47 (see the module docstring) ---

MAIN_ELIGIBILITY = {
    "token_value_backslash": (
        'https://example.test/p?token=Zq9secretwombat\\"',
        [
            (
                'https://example.test/p?token=%5BREDACTED%5D"',
                'https://example.test/p?token=%5BREDACTED%5D"',
            ),
            (
                '"https://example.test/p?token=%5BREDACTED%5D""',
                '"https://example.test/p?token=%5BREDACTED%5D""',
            ),
            (
                '"\\"https://example.test/p?token=%5BREDACTED%5D"\\""',
                '"\\"https://example.test/p?token=%5BREDACTED%5D"\\""',
            ),
        ],
    ),
    "token_empty_backslash": (
        'https://example.test/p?token=\\"',
        [
            (
                'https://example.test/p?token=%5BREDACTED%5D"',
                'https://example.test/p?token=%5BREDACTED%5D"',
            ),
            (
                '"https://example.test/p?token=%5BREDACTED%5D""',
                '"https://example.test/p?token=%5BREDACTED%5D""',
            ),
            (
                '"\\"https://example.test/p?token=%5BREDACTED%5D"\\""',
                '"\\"https://example.test/p?token=%5BREDACTED%5D"\\""',
            ),
        ],
    ),
    "token_value_dot": (
        "https://example.test/p?token=Zq9secretwombat.",
        [
            (
                "https://example.test/p?token=%5BREDACTED%5D.",
                "https://example.test/p?token=%5BREDACTED%5D.",
            ),
            (
                '"https://example.test/p?token=%5BREDACTED%5D."',
                '"https://example.test/p?token=%5BREDACTED%5D."',
            ),
            (
                '"\\"https://example.test/p?token=%5BREDACTED%5D""',
                '"\\"https://example.test/p?token=%5BREDACTED%5D""',
            ),
        ],
    ),
    "password_semicolon_backslash_paren": (
        "https://example.test/p?password=Zq9secretwombat;\\)",
        [
            (
                "https://example.test/p?password=%5BREDACTED%5D)",
                "https://example.test/p?password= [REDACTED]",
            ),
            (
                '"https://example.test/p?password=%5BREDACTED%5D)"',
                '"https://example.test/p?password= [REDACTED]"',
            ),
            (
                '"\\"https://example.test/p?password=%5BREDACTED%5D""',
                '"\\"https://example.test/p?password= [REDACTED]""',
            ),
        ],
    ),
    "userinfo_tail": (
        'https://user:Zq9secretwombat@example.test/p\\"',
        [
            ('https://example.test/p\\"', 'https://example.test/p\\"'),
            ('"https://example.test/p\\\\\\""', '"https://example.test/p\\\\\\""'),
            (
                '"\\"https://example.test/p\\\\\\\\\\\\\\"\\""',
                '"\\"https://example.test/p\\\\\\\\\\\\\\"\\""',
            ),
        ],
    ),
    "fragment_tail": (
        'https://example.test/p#Zq9secretwombat\\"',
        [
            ('https://example.test/p"', 'https://example.test/p"'),
            ('"https://example.test/p""', '"https://example.test/p""'),
            ('"\\"https://example.test/p"\\""', '"\\"https://example.test/p"\\""'),
        ],
    ),
}
MAIN_LIMITATION = {
    "pct41": (
        "https://example.com/p?q=%41",
        'https://example.com/p?q=A%5C" end',
        'https://example.com/p?q=A%5C" end',
        "7ce37b62d741518ccf40281688e7f0012534d6a0ad9a11ae9ba2c4310afe9d06",
    ),
    "pct2B": (
        "https://example.com/p?q=a%2Bb",
        'https://example.com/p?q=a%2Bb%5C" end',
        'https://example.com/p?q=a%2Bb%5C" end',
        "41d3305541218b9dabe81c5a697a5f76411d464bc985146a1135c49796e57329",
    ),
    "plus": (
        "https://example.com/q?q=a+b",
        'https://example.com/q?q=a%20b%5C" end',
        'https://example.com/q?q=a%20b%5C" end',
        "bb12aaf44c4c7a596efcf4bfe927314b3162d18a817b1d7c96bdfa096e98d813",
    ),
    "pct20": (
        "https://example.com/q?q=a%20b",
        'https://example.com/q?q=a%20b%5C" end',
        'https://example.com/q?q=a%20b%5C" end',
        "f56ca689512d5ca4178b8ce6e7d2bcbb67220a337d82ee5b44da1e8ac35fe801",
    ),
    "key_token": (
        "https://example.com/p?token=abc",
        'https://example.com/p?token=%5BREDACTED%5D" end',
        'https://example.com/p?token=%5BREDACTED%5D" end',
        "b982c70d35a60b6c96f0a21cf19637cfea6dc795c5cbbc39a42c79751bd364ee",
    ),
    "key_password": (
        "https://example.com/p?v=1&password=abc",
        'https://example.com/p?v=1&password=%5BREDACTED%5D" end',
        'https://example.com/p?v=1&password= [REDACTED]" end',
        "f1f497129e7ccbb7419781a7e36c7da0938a6851e7cda9bfaf32cf03f5f2ba43",
    ),
    "key_client_secret": (
        "https://example.com/p?client_secret=abc",
        'https://example.com/p?client_secret=%5BREDACTED%5D" end',
        'https://example.com/p?client_secret= [REDACTED]" end',
        "97190cfcaacf101e341995df3e01435a3bb741cda3a464c0a3810481a42f065a",
    ),
    "key_api_key": (
        "https://example.com/p?api_key=abc",
        'https://example.com/p?api_key=%5BREDACTED%5D" end',
        'https://example.com/p?api_key= [REDACTED]" end',
        "f31d79719b3de9c570bf2effc3235481dec321ee40f14c15bdb033fc7356806a",
    ),
    "key_code": (
        "https://example.com/watch?code=abc",
        'https://example.com/watch?code=%5BREDACTED%5D" end',
        'https://example.com/watch?code=%5BREDACTED%5D" end',
        "39058b41b18338e2282e057feb43f52a6070ba82f79a4430abd306703043756e",
    ),
    "key_token_plus": (
        "https://example.com/p?token+=abc",
        'https://example.com/p?token%20=[REDACTED]" end',
        'https://example.com/p?token%20=[REDACTED]" end',
        "cc1b37e6cad91f317c9378baa1a4d54ee5dbfb2473bbd55f00648f5f2af3bdad",
    ),
    "weak_my_token": (
        "https://example.com/p?my_token=abc",
        'https://example.com/p?my_token=[REDACTED][REDACTED]" end',
        'https://example.com/p?my_token=[REDACTED][REDACTED]" end',
        "1ef063a26eec19fcc3428236a4ac920b9306f2eeb01b25abf54952b4ad5e709f",
    ),
    "weak_token_type": (
        "https://example.com/p?token_type=abc",
        'https://example.com/p?token_type=[REDACTED][REDACTED]" end',
        'https://example.com/p?token_type=[REDACTED][REDACTED]" end',
        "4a05f6f70bebec7c0b07558a56f33025f967363e49eb1b32d9cd25f730580eba",
    ),
    "weak_Token": (
        "https://example.com/p?Token=abc",
        'https://example.com/p?Token=%5BREDACTED%5D" end',
        'https://example.com/p?Token=%5BREDACTED%5D" end',
        "3f2c5b0ac1f3f735a82a2aa7ba2c9ad1f5417b07528c7554e1b6cb30e4ad6de8",
    ),
    "weak_apikey": (
        "https://example.com/p?apikey=abc",
        'https://example.com/p?apikey=%5BREDACTED%5D" end',
        'https://example.com/p?apikey= [REDACTED]" end',
        "4ee4d56c075852d5be88e0f595e6185282bc1d4565e3c7645e175e5399fa7343",
    ),
    "weak_access_dash_token": (
        "https://example.com/p?access-token=abc",
        'https://example.com/p?access-token=[REDACTED][REDACTED]" end',
        'https://example.com/p?access-token=[REDACTED][REDACTED]" end',
        "e2e6e5991ee5b1b3155ef2137202c99baab9e827d39024765c352dad1deda417",
    ),
    "enc_amp": (
        "https://example.com/p?a=1%26b=2",
        'https://example.com/p?a=1%26b%3D2%5C" end',
        'https://example.com/p?a=1%26b%3D2%5C" end',
        "9c8778e1c6d1a7176a3b0072c96f54973bf857c0b2de19f62e01ac1ff9196a38",
    ),
    "enc_eq": (
        "https://example.com/p?a%3D1",
        'https://example.com/p?a%3D1%5C=" end',
        'https://example.com/p?a%3D1%5C=" end',
        "9628633f91e5bb861a352b9cfa069df885b00820c2d0c6b12b76ebee38df5444",
    ),
    "enc_semicolon": (
        "https://example.com/p?a=1%3Bb",
        'https://example.com/p?a=1%3Bb%5C" end',
        'https://example.com/p?a=1%3Bb%5C" end',
        "b986bebe216e8c62316420ccd0c13a9cd5b592d0f4689cdea8f47592a7fc6d90",
    ),
    "enc_comma": (
        "https://example.com/p?a=1%2Cb",
        'https://example.com/p?a=1%2Cb%5C" end',
        'https://example.com/p?a=1%2Cb%5C" end',
        "40bb4b4f831edc50ca168551349440b401eeb3c2a9073f34c63fc9b8cb9ac0bd",
    ),
    "enc_slash": (
        "https://example.com/a%2Fb",
        'https://example.com/a%2Fb\\" end',
        'https://example.com/a%2Fb\\" end',
        "5e3fcedc4baa7f8689ccd07c1ae6a8629b1ebd2f3be27a1075678e58f318a361",
    ),
    "userinfo_user": (
        "https://user@example.com/p",
        'https://example.com/p\\" end',
        'https://example.com/p\\" end',
        "a47c2b0f5d25596bad9b1dbaa2a63341a74e769b1d3562c85695e2ee726b3c6b",
    ),
    "userinfo_password": (
        "https://user:pw@example.com/p",
        'https://example.com/p\\" end',
        'https://example.com/p\\" end',
        "3ef2de7798d842194adf3fa8eac92dea40611c966456e2a7dad99c3ba8c54982",
    ),
    "port_too_big": (
        "https://example.com:99999/p",
        'https://example.com:99999/p\\" end',
        'https://example.com:99999/p\\" end',
        "c88a15091eded4ca74d19827db6de1b6ee77d9c8f057b9d0019ab1e68db4ca06",
    ),
    "port_empty": (
        "https://example.com:/p",
        'https://example.com/p\\" end',
        'https://example.com/p\\" end',
        "864f9c7c84423e7084dbd197729ad036d2807a6c7cae417770b0edff8912da5b",
    ),
    "port_alpha": (
        "https://example.com:abc/p",
        'https://example.com:abc/p\\" end',
        'https://example.com:abc/p\\" end',
        "0f523821c6cf4e11dd625ecd3ce11b2c47d8d49e2ed1ef01b15f7462efd6841d",
    ),
    "port_valid": (
        "https://example.com:8080/p",
        'https://example.com:8080/p\\" end',
        'https://example.com:8080/p\\" end',
        "bfddb6f02d1df73bb86745ae3539827a7cf58ea93438c9c161225ec539955f57",
    ),
    "fragment": (
        "https://example.com/p#frag",
        'https://example.com/p" end',
        'https://example.com/p" end',
        "b3f4d3f74ccd4644b4ebabb8c1eebcc9de92bbdc88ad4cae58921e1c79cd7a82",
    ),
    "fragment_empty": (
        "https://example.com/p#",
        'https://example.com/p" end',
        'https://example.com/p" end',
        "e62b68667d18ecdb206cbf83d728b7837cd12aa4068f46083cbc4bedb76d6a7c",
    ),
    "fragment_after_query": (
        "https://example.com/p?v=1#t=30",
        'https://example.com/p?v=1" end',
        'https://example.com/p?v=1" end',
        "80fa812fc8d8e23a5b3e8a6c6e7e123859e01929a425c94becb6e19f02ea586b",
    ),
    "empty_leading_pair": (
        "https://example.com/p?&v=1",
        'https://example.com/p?v=1%5C" end',
        'https://example.com/p?v=1%5C" end',
        "4ddfda2b545ce3c9845590fe0fe3d070bc1b54d515606ce8298054787d1091c7",
    ),
}
RESTORED_WHERE_MAIN_PRINTS_THE_CORE = {
    "empty_pairs": "https://example.com/p?a=&b=",
    "weak_api_dot_key": "https://example.com/p?api.key=abc",
}
MAIN_CHANGED_SNAPSHOT = {
    "bearer_after": (
        'https://example.test/watch?v=abc\\" Bearer abcdefghijklmnop',
        'https://example.test/watch?v=abc%5C" Bearer [REDACTED]',
        'https://example.test/watch?v=abc%5C" Bearer [REDACTED]',
    ),
    "keyword_after": (
        'https://example.test/watch?v=abc\\" password=Zq9secretwombat',
        'https://example.test/watch?v=abc%5C" password=[REDACTED]',
        'https://example.test/watch?v=abc%5C" password= [REDACTED]',
    ),
    "keyword_before": (
        'password=Zq9secretwombat https://example.test/watch?v=abc\\"',
        'password=[REDACTED] https://example.test/watch?v=abc%5C"',
        'password= [REDACTED] https://example.test/watch?v=abc%5C"',
    ),
    # The Basic credential survives on main: a baseline defect pinned here for
    # parity, not endorsed (Consiliency/pmcp#478).
    "authorization_after": (
        'https://example.test/watch?v=abc\\" Authorization: Basic Zm9vOmJhcg',
        'https://example.test/watch?v=abc%5C" Authorization: [REDACTED] Zm9vOmJhcg',
        'https://example.test/watch?v=abc%5C" Authorization: [REDACTED] Zm9vOmJhcg',
    ),
    "json_password_after": (
        '{"u": "https://example.test/watch?v=abc\\"", "password": "Zq9secretwombat"}',
        '{"u": "https://example.test/watch?v=abc%5C"", "password": "[REDACTED]"}',
        '{"u": "https://example.test/watch?v=abc%5C"", "password": "[REDACTED]"}',
    ),
}


# --- Secrecy: shapes around a restorable URL (r5 L1-L4, preceding keys) ---

L = [
    "Authorization: " + U + ';\\"' + SECRET + '"',
    "Bearer " + U + ',\\"' + SECRET + '"',
    "token=" + U + "\\>" + SECRET,
    '{"password": "' + U + '\\"password":"' + SECRET + '"}',
]


PRECEDING = L + [
    p + 'https://example.test/a?v=quokkawombat\\" more'
    for p in [
        '"token": "',
        '"api_key": "',
        '"password":"',
        'secret = "',
        '{"client_secret": "',
        'token: "',
    ]
]


def _preceding_case(index: int, depth: int) -> object:
    # Known baseline leak, identical before and after #401: once the text is
    # JSON-encoded, the keyword rule no longer matches a secret-named key
    # followed by a value (Consiliency/pmcp#478). Strict xfail, so fixing #478
    # turns these into failures that prompt removing the marker.
    if index >= 3 and depth >= 1:
        return pytest.param(
            index,
            depth,
            marks=pytest.mark.xfail(
                reason="baseline leak, Consiliency/pmcp#478", strict=True
            ),
        )
    return pytest.param(index, depth)


@pytest.mark.parametrize(
    ("index", "depth"),
    [_preceding_case(i, d) for i in range(len(PRECEDING)) for d in range(3)],
)
def test_preceding_keys(index: int, depth: int) -> None:
    text = PRECEDING[index]
    secrets = [s for s in (SECRET, "quokkawombat") if s in text]
    for _ in range(depth):
        text = json.dumps(text)
    for output in (_diag(text), _policy(text)):
        for secret in secrets:
            assert secret not in output


@pytest.mark.parametrize("text", L)
def test_l1_l4_unconditional(text: str) -> None:
    assert SECRET not in _diag(text)
    assert SECRET not in _policy(text)


# --- Eligibility: a URL whose core the URL pass changed keeps main's output ---


@pytest.mark.parametrize("name", sorted(MAIN_ELIGIBILITY))
@pytest.mark.parametrize("depth", [0, 1, 2])
def test_ineligible_url_keeps_main_exactly(name: str, depth: int) -> None:
    """A secret value, an empty secret value, userinfo or a fragment ahead of
    the trailing run: the URL pass changed the core, so nothing is restored,
    at every JSON depth, on both entry points."""
    text, expected = MAIN_ELIGIBILITY[name]
    for _ in range(depth):
        text = json.dumps(text)
    main_diag, main_policy = expected[depth]
    assert _diag(text) == main_diag
    assert _policy(text) == main_policy


# --- Limitations: outside the grammar or changed by the URL pass -> main ---


@pytest.mark.parametrize("name", sorted(MAIN_LIMITATION))
def test_limitation_keeps_main_exactly(name: str) -> None:
    """`%XX`, `+`, secret and weak keys, encoded delimiters, userinfo, ports,
    fragments and dropped empty pairs are not restored: each keeps main's
    output exactly, including through `process_output`."""
    url, main_diag, main_policy, main_envelope_sha256 = MAIN_LIMITATION[name]
    text = url + '\\" end'
    assert _diag(text) == main_diag
    assert _policy(text) == main_policy
    out = PolicyManager().process_output(_envelope(url), redact=True, max_bytes=10**6)
    rendered = json.dumps(out, sort_keys=True)
    assert hashlib.sha256(rendered.encode()).hexdigest() == main_envelope_sha256, (
        rendered
    )


@pytest.mark.parametrize("name", sorted(RESTORED_WHERE_MAIN_PRINTS_THE_CORE))
def test_restored_where_main_already_prints_the_core(name: str) -> None:
    """Main prints these URLs' cores in clear (`a=&b=` is kept, `api.key` is
    not a secret key on main), so only the trailing run comes back."""
    url = RESTORED_WHERE_MAIN_PRINTS_THE_CORE[name]
    text = url + '\\" end'
    assert _diag(text) == text
    assert _policy(text) == text


@pytest.mark.parametrize("name", sorted(MAIN_CHANGED_SNAPSHOT))
def test_changed_snapshot_keeps_main_exactly(name: str) -> None:
    """Any change by a pass after the URL pass, anywhere in the text, cancels
    the restore for the whole text."""
    text, main_diag, main_policy = MAIN_CHANGED_SNAPSHOT[name]
    assert "%5C" in main_diag
    assert _diag(text) == main_diag
    assert _policy(text) == main_policy


def test_restore_guard() -> None:
    text = U + '\\" password=' + SECRET
    out = _policy(text)
    assert "%5C" in out
    assert SECRET not in out


def test_custom_policy_cancels_restore() -> None:
    """An operator pattern that changes text outside the URL still cancels the
    restore: the snapshot no longer matches, so the URL keeps its encoded tail."""
    p = PolicyManager()
    p._redaction_regexes = [re.compile("ZZZ")]
    assert p.redact_secrets(U + '\\" ZZZ') == U + '%5C" [REDACTED]'


def test_url_pass_redaction_does_not_cancel_restore() -> None:
    """A secret the URL pass itself redacts is inside the snapshot: the benign
    URLs beside it are still restored, the redacted one is not."""
    text = U + '\\" https://example.test/watch?v=abc&token=Zq1\\" ' + U + '\\"'
    main = (
        U + '%5C" https://example.test/watch?v=abc&token=%5BREDACTED%5D" ' + U + '%5C"'
    )
    expected = (
        U + '\\" https://example.test/watch?v=abc&token=%5BREDACTED%5D" ' + U + '\\"'
    )
    assert A.redact_additive(A._sanitize_base(text)) == main
    assert _diag(text) == expected
    assert _policy(text) == expected


# --- Positions: only recorded spans, never a search ---


def test_literal_elsewhere_and_repeated_occurrences() -> None:
    text = U + '%5C " ' + U + '\\" ' + U + "%5C"
    assert _policy(text) == text


@pytest.mark.parametrize("tail", ["\\", ");\\", ",\\", ";;;\\", "\\\\"])
def test_literal_tail_difference(tail: str) -> None:
    text = U + tail + '"'
    assert _policy(text) == text


def test_restored_run_includes_the_backslash() -> None:
    """Main's own suffix set is `).,;`, so the URL pass encodes a trailing
    backslash as `%5C`. The restorable run deliberately adds the backslash:
    it is the `\\"` escaping a JSON string's closing quote."""
    assert A._RESTORABLE_URL_TAIL == ").,;\\"
    text = U + '\\"'
    assert A._sanitize_base(text) == U + '%5C"'
    assert _diag(text) == text
    assert _policy(text) == text
    obj = {"t": U + "\\"}
    assert _policy(json.dumps(obj)) == json.dumps(obj)
    assert json.loads(_policy(json.dumps(obj))) == obj


# --- Truncation ---


def test_length_budget_and_context_reset() -> None:
    text = U + '\\" suffix'
    main = M.sanitize_auth_diagnostic(text, max_length=None)
    for limit in range(len(main) + 1):
        out = _diag(text, limit)
        assert len(out) <= limit
        if "suffix" not in main[:limit]:
            assert "suffix" not in out
    assert A._URL_TAIL_RECORD.get() is None
    assert A._sanitize_base(text) == M.sanitize_auth_diagnostic(text, max_length=None)


def test_partially_cut_span_keeps_main_cut() -> None:
    """A span the cut reaches into keeps main's partial canonical bytes; a
    span wholly inside the cut is restored. Nothing past main's cut appears,
    so a cut diagnostic need not be a prefix of the uncut one."""
    text = U + '\\" suffix'
    main = U + '%5C" suffix'
    span_end = len(U) + len("%5C")
    for limit in range(len(main) + 2):
        out = _diag(text, limit)
        if limit < span_end:
            assert out == main[:limit]
        else:
            assert out == U + "\\" + main[span_end:limit]


@pytest.mark.parametrize("limit", [-100, -30, -1, 0, 1, 30, 35, 400])
def test_negative_and_zero_cutoff(limit: int) -> None:
    text = U + '\\" suffix'
    if limit <= 0:
        assert _diag(text, limit) == M.sanitize_auth_diagnostic(text, max_length=limit)
        assert _diag(text, limit) == (U + '%5C" suffix')[:limit]
    else:
        assert len(_diag(text, limit)) <= limit


# --- The record's lifetime ---


def test_context_reset_on_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(text: str) -> str:
        raise RuntimeError("test")

    monkeypatch.setattr(A, "redact_additive", fail)
    with pytest.raises(RuntimeError):
        _diag(U + '\\"', 400)
    assert A._URL_TAIL_RECORD.get() is None


def test_second_base_call_under_one_record_fails_closed() -> None:
    text = U + '\\"'
    with A._url_tail_restore() as record:
        first = A._sanitize_base(text)
        assert record.spans and not record.poisoned
        A._sanitize_base("again")
        assert record.poisoned
        assert A._restore_url_tails(first, record) == first == U + '%5C"'
    assert A._URL_TAIL_RECORD.get() is None


@pytest.mark.parametrize("entry", ["diagnostic", "policy"])
def test_second_base_call_inside_an_entry_point_keeps_main(
    monkeypatch: pytest.MonkeyPatch, entry: str
) -> None:
    text = U + '\\"'
    module = A if entry == "diagnostic" else policy_module
    redact = _diag if entry == "diagnostic" else _policy
    assert redact(text) == text
    real = module.redact_additive

    def twice(*args: object, **kwargs: object) -> str:
        A._sanitize_base("again")
        return real(*args, **kwargs)

    monkeypatch.setattr(module, "redact_additive", twice)
    assert redact(text) == U + '%5C"'
    assert A._URL_TAIL_RECORD.get() is None


def test_direct_base_caller_gets_main() -> None:
    text = U + '\\"'
    assert A._URL_TAIL_RECORD.get() is None
    assert A._sanitize_base(text) == U + '%5C"'


# --- Real MCP shapes ---


def test_falsifier() -> None:
    p = PolicyManager()
    row = {
        "snippet": '<Card href="https://example.test/watch?x%3Auth=quokkawombat">Person</Card>'
    }
    payload = {
        "content": [{"type": "text", "text": json.dumps(row)}],
        "structuredContent": row,
    }
    assert "quokkawombat" not in p.redact_secrets(json.dumps(payload))
    assert "quokkawombat" not in json.dumps(
        p.process_output(payload, redact=True, max_bytes=1000000)
    )


BENIGN_URLS = [
    "https://www.youtube.com/watch?v=kQmXtrmQ5Zg",
    "https://www.youtube.com/embed/kQmXtrmQ5Zg",
    "https://example.test/watch?v=abc",
    "https://www.youtube.com/watch?v=kQmXtrmQ5Zg&t=30s",
    "https://www.youtube.com/watch?v=kQmXtrmQ5Zg&list=PLabc123",
    "https://youtu.be/kQmXtrmQ5Zg?si=AbCdEf123",
    "https://www.youtube.com/watch?t=30&v=kQmXtrmQ5Zg",
    "https://github.com/o/r?tab=readme-ov-file",
    "https://example.com/docs?page=2",
    "https://example.com/?utm_source=x&utm_medium=y",
    "https://example.com/search?q=foo",
    "https://example.com/p?id=123",
    "https://example.com/p?lang=en",
    "https://example.com/x?ref=main",
    "https://example.com/a?v=abc&v=def",
    "https://example.com/path/only",
]


@pytest.mark.parametrize("url", BENIGN_URLS)
def test_benign_envelope(url: str) -> None:
    payload = _envelope(url)
    out = PolicyManager().process_output(payload, redact=True, max_bytes=1000000)
    assert out["result"] == payload
    assert not out["truncated"]


# Code-Index-MCP `ai_docs/MCP.md` lines 3940-3941 at
# 1a61533c5421b4c0329a658f95ccad657cbb7239 (file sha256
# 9c59bb612738c929eb2b21bc2d4cdb8fbcd3c4d7f3e3f0b9acc228b0efb23ed8). A
# source-reconstructed search row: this proves preservation, not retrieval.
PERSON_SNIPPET = (
    '  <Card title="MCP Workshop (Video, 2hr)" icon="person-chalkboard" '
    'href="https://www.youtube.com/watch?v=kQmXtrmQ5Zg">\n'
    '    <iframe src="https://www.youtube.com/embed/kQmXtrmQ5Zg" />'
)


def test_person_fixture_round_trips() -> None:
    rows = {
        "results": [
            {"file_path": "ai_docs/MCP.md", "line": 3940, "snippet": PERSON_SNIPPET}
        ]
    }
    payload = {
        "content": [{"type": "text", "text": json.dumps(rows)}],
        "structuredContent": rows,
    }
    assert (
        hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        == "964f61181fba1b52c6ce211f649ffe47815688b935e58669ea2dc7cacca63cc8"
    )
    out = PolicyManager().process_output(payload, redact=True, max_bytes=1000000)
    assert out["result"] == payload
    assert not out["truncated"]


# --- The 17 #423 R5 names against the frozen pre-additive oracle's floor ---

R5_PAIRS = [
    (n, "quokkawombat", "\\")
    for n in [
        "token+",
        "password+",
        "x,ode",
        "x%2Cookie",
        "x:ssertion",
        "x:uth",
        "x%3Auth",
        "x,redential",
        "x|redentials",
    ]
] + [
    (n, "hunterword", t)
    for n in ["credential+", "x+credential+", "private_key+", "token+"]
    for t in ["\\", ";;;\\"]
]


@pytest.mark.parametrize("name,secret,tail", R5_PAIRS)
def test_verbatim_oracle_floor(name: str, secret: str, tail: str) -> None:
    p = PolicyManager()
    text = f"see https://example.test/p?{name}={secret}" + tail + " next"
    for t in [text, json.dumps({"t": text})]:
        assert _is_additive_of(_diag(t), M.sanitize_auth_diagnostic(t, max_length=None))
        assert _is_additive_of(p.redact_secrets(t), M.redact_secrets(t))
