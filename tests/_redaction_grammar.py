"""The grammar-derived never-worse-than-main differential (Consiliency/pmcp#234, rev 10).

Revs 8 and 9 each passed a differential whose axes were listed by hand from
past findings, with zero unaccepted rows, and each was then blocked by inputs
on an axis nobody had listed. This corpus is derived from the ORACLE's
grammar instead -- main's rules plus what `json.dumps` emits -- so an axis
cannot be missing because nobody thought of it.

Main's rules (`main:src/pmcp/auth.py`, `main:src/pmcp/policy/policy.py`):

* the keyword rule
  `(?i)\\b[A-Za-z0-9_-]*(?:KEYS|api[_-]?key)[A-Za-z0-9_-]*[\\s:=]+[A-Za-z0-9._~+/=-]{3,}`;
* `(?i)authorization\\s*[:=]\\s*(bearer\\s+)?[^\\s,;]+` and `(?i)\\bbearer\\s+[^\\s,;]+`;
* the policy defaults' key words (`passwd`, `pwd`, `aws_secret`, `aws_access`);
* `process_output(dict)`: `json.dumps(obj, indent=2)` (`ensure_ascii=True`),
  whose escapes are `\\" \\\\ \\b \\f \\n \\r \\t` and `\\uXXXX`, and whose bare
  scalars are `null true false`, integers, floats, `NaN` and `+-Infinity`.

A text row is `pre + qual + name + suffix + sep + value`, each part drawn from
the construct it stands for:

=========  ================================================================
axis       alphabet / grammar
=========  ================================================================
pre        the character before the key: none; every ASCII punctuation
           character but the joiners (`-`, `_`); `:`; `\\`; all 29
           `str.isspace()` characters; control characters (serialised as
           `\\u0000 \\u0001 \\b \\u001b \\u007f`); non-ASCII letters,
           punctuation and an astral character (serialised as `\\uXXXX` or a
           surrogate pair); an `arn:` / `urn:` resource-name context. Each
           optionally after the word `abc`.
qual       main's `[A-Za-z0-9_-]*` prefix: joined (`x_`, `x-`), glued in
           random case (1-8), glued past the 24 bound (25-32), a leading
           joiner run (`_ - -- --- _-`), 2-10 joined segments.
name       main's 20 keys plus the policy keys, lower / UPPER / Title /
           rAnDoM case; 30% of name-focus rows are `code` under a
           qualifier: a random word, a diagnostic one
           (`DIAGNOSTIC_CODE_QUALIFIERS`, class C12) or a credential one
           (`key otp mfa sms verification recovery invite promo coupon
           discount api access secret`), in any case, `_`- or `-`-joined.
suffix     main's `[A-Za-z0-9_-]*` suffix: declared (`s _id _key Key -id id
           key`), a trailing joiner or joiner run, descriptive (`_new 2 Hash
           _type _length ized ...`), random, and past the 24 bound.
sep        main's `[\\s:=]+`: EVERY 1- and 2-character string over the 31
           characters `str.isspace()` + `:` + `=`, plus sampled 3-character
           ones; focus rows draw 1-3 characters, half operator, half space.
value      main's value class `[A-Za-z0-9._~+/=-]{3,14}`, plain words,
           integers, quoted, unterminated-quote, bracketed, JSON literals and
           punctuated values.
bearer     `bearer\\s+[^\\s,;]+` after 13 contexts (`X-Auth: `, `x=`,
           `token: `, ...) or a random punctuation/non-ASCII/space character,
           a 1-3 character `\\s` run, a credential optionally wrapped in
           `"" '' () [] {} <>`.
authz      `authorization\\s*[:=]\\s*(bearer\\s+)?[^\\s,;]+`: a prefix, 0-2
           `\\s` characters each side of the operator, a scheme (or none, or
           one followed by a blank line), a credential or plain value,
           optionally wrapped.
wrap       where the pair sits: bare; in prose (a word and one ASCII
           punctuation or `str.isspace()` character on each side -- what
           ends main's `\\b` and value class); or in each position of a URL
           main's URL rule reads: path, query pair, query value, fragment.
url        main's URL grammar itself (`https?://[^\\s"'<>]+`, trailing
           `).,;` handed back, then `redact_auth_url`): userinfo (user,
           user:password, :password), hosts (one whose port does not parse,
           which makes main keep the URL as written), 0-3 query pairs whose
           key is or is not in `AUTH_SECRET_QUERY_KEYS` (any case, optionally
           percent-encoded) with a credential, plain, percent-encoded or
           empty value, and a fragment (none, a word, a secret,
           `access_token=` + a secret), in prose. The secrets are what main
           removes.
scalars    every scalar `json.dumps` emits under 37 keys (the 24 above plus
           `auth credentials authorization Authorization bearer tokens
           input_tokens max_tokens token_type Password API_KEY privateKey
           aws_secret_access_key`), top-level, nested and in a list of
           objects.
=========  ================================================================

Every text row is observed on 12 surfaces: `E`/`P` (`sanitize_auth_diagnostic`,
`PolicyManager.redact_secrets`) on the raw text; `E`/`P` on each of the four
spellings `json.dumps({"t": text}, ensure_ascii in (True, False), indent in
(None, 2))` (`EjAC EjAI EjUC EjUI PjAC PjAI PjUC PjUI`); `process_output(text)`
(`POs`); and `process_output({"t": text})` (`POd`, the dict-leaf path), whose
result type is recorded too. A scalar row is observed on `POo`, with its type.
A piece counts as present in an output as written or percent-decoded (a query
value's `+` read as a space): main's URL rule re-encodes what it keeps, and
a re-encoded secret is still there.

Sampling density (`corpus(tier)`; tier 2 is tier 1 followed by the rest):

===============  =====================================  ======================
block            tier 1 (the default suite)             tier 2 adds (`slow`)
===============  =====================================  ======================
sep              the 992 1- and 2-character separators  the 992 x `secret`,
                 x `password`, `token` x {credential,   `api_key`, `session`
                 plain}: 3 968                          x 2, and 3 000 sampled
                                                        3-character ones x 5
                                                        keys x 2: 35 952
pre              each of the 77 pre characters x        lead `abc` x 5 keys,
                 `password`, `token` x (`=`, `: `,      no lead x 3 more
                 ` `) x {credential, plain}: 924        keys: 3 696
code             `code` under each of the 37 diagnostic  --
                 and 13 credential qualifiers x (`=`,
                 `: `) x {credential, plain}: 200
focus:<axis>     500 per axis (pre qual name suffix     9 500 per axis:
                 sep value wrap bearer authz url):      95 000
                 5 000
mix              1 000 (every axis at once, the wrap    19 000
                 included)
scalar           13 scalars x 37 keys x 3 shapes:       --
                 1 443
===============  =====================================  ======================

Tier 1 is 12 535 rows; tier 2 is 166 183. A focus row varies one axis and
keeps the others benign (no pre, qualifier, suffix or wrap; `=` or `: `; a
credential value), so a failure is attributed to one axis; mix rows vary
them all at once (`-password hunter22x` was found only there).

How it runs: tier 1 is `test_grammar_differential_never_worse_than_main_
except_by_stated_class` in the default suite. Tier 2 is
`test_grammar_differential_full_set`, marked `slow`: `pytest
tests/test_redaction.py -m slow` (about 2-3 minutes). `addopts` excludes
`slow` by default, so CI -- which runs `pytest tests/` with `addopts` --
does NOT run it.

This module is stdlib-only and imports nothing from `pmcp`: the fixture is
recorded by running it against `main`'s tree.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
from collections.abc import Callable
from typing import Any
from urllib.parse import unquote

# ---------------------------------------------------------------- alphabets

#: Every character `str.isspace()` accepts -- Python's `\s` (pinned against a
#: full enumeration by the axis test, not computed here: the enumeration
#: depends on the interpreter's Unicode version).
SPACES = (
    "\t\n\x0b\x0c\r\x1c\x1d\x1e\x1f \x85\xa0\u1680\u2000\u2001\u2002\u2003"
    "\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000"
)
#: main's `[\s:=]`.
SEP_ALPHABET = SPACES + ":="
#: Every ASCII punctuation character but the identifier joiners `-`/`_`
#: (the `qual` axis) and `:`/`\` (classes of their own).
ASCII_PUNCT = "!\"#$%&'()*+,./;<=>?@[]^`{|}~"
#: -> `\u0000 \u0001 \b \u001b \u007f` in `json.dumps` output.
CONTROL = "\x00\x01\x08\x1b\x7f"
#: A letter, CJK, bullet, curly quote, dash, middle dot, astral emoji (a
#: surrogate pair when escaped), sharp s, Greek and Cyrillic.
NON_ASCII = "\xe9\u5bc6\u2022\u201c\u2014\xb7\U0001f642\xdf\u03a9\u0418"
#: A resource-name context: a key inside it names a resource.
RESOURCE = ("arn:aws:secretsmanager:us-east-1:1:secret:", "urn:ietf:params:oauth:")
PRE_CHARS: dict[str, tuple[str, ...]] = {
    "start": ("",),
    "ascii-punct": tuple(ASCII_PUNCT),
    "colon": (":",),
    "backslash": ("\\",),
    "isspace": tuple(SPACES),
    "control": tuple(CONTROL),
    "non-ascii": tuple(NON_ASCII),
    "resource": RESOURCE,
}
MAIN_KEYS = (
    "access_token",
    "api_key",
    "api-key",
    "apikey",
    "assertion",
    "client_secret",
    "code",
    "cookie",
    "id_token",
    "jwt",
    "password",
    "refresh_token",
    "saml",
    "secret",
    "session",
    "set-cookie",
    "sid",
    "tenant-id",
    "tenant_id",
    "token",
)
POLICY_KEYS = ("passwd", "pwd", "aws_secret", "aws_access")
KEYS = MAIN_KEYS + POLICY_KEYS
IDENT = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
MAIN_VALUE = IDENT + "._~+/=-"
PLAIN_WORDS = (
    "letmeinnow",
    "expired",
    "required",
    "bucket",
    "none",
    "hunter",
    "changeme",
    "Basic",
)
DIGITS = "0123456789"
SCALARS: dict[str, object] = {
    "null": None,
    "true": True,
    "false": False,
    "0": 0,
    "-1": -1,
    "2**100": 2**100,
    "1.5": 1.5,
    "-0.0": -0.0,
    "1e+20": 1e20,
    "1e-07": 1e-7,
    "NaN": float("nan"),
    "Infinity": float("inf"),
    "-Infinity": float("-inf"),
}
SCALAR_KEYS = KEYS + (
    "auth",
    "credentials",
    "authorization",
    "Authorization",
    "bearer",
    "tokens",
    "input_tokens",
    "max_tokens",
    "token_type",
    "Password",
    "API_KEY",
    "privateKey",
    "aws_secret_access_key",
)
BEARER_PRE = (
    "",
    "X-Auth: ",
    "X-Auth:",
    "x=",
    "token: ",
    "session=",
    "Cookie: session=",
    "(",
    '"',
    "use a ",
    "headers: {X-Auth: ",
    "X-Api-Token: ",
    "auth: ",
)
FOCI = ("pre", "qual", "name", "suffix", "sep", "value", "wrap")
#: main's `AUTH_SECRET_QUERY_KEYS`: the query keys `redact_auth_url` redacts
#: (after `parse_qsl` decodes them, case-insensitively).
MAIN_QUERY_KEYS = (
    "access_token",
    "api_key",
    "apikey",
    "auth",
    "auth_code",
    "authorization",
    "bearer",
    "client_secret",
    "code",
    "id_token",
    "assertion",
    "key",
    "password",
    "refresh_token",
    "saml",
    "secret",
    "session",
    "sid",
    "ticket",
    "token",
    "jwt",
)
#: The stated diagnostic class (C12): `code` under one of these qualifiers
#: (its last `_`/`-` segment) names a status or a descriptive code and keeps
#: its value. Pinned equal to `pmcp.auth._STATUS_CODE_QUALIFIERS` by a test.
DIAGNOSTIC_CODE_QUALIFIERS = frozenset(
    {
        "area",
        "byte",
        "char",
        "color",
        "colour",
        "country",
        "currency",
        "err",
        "errno",
        "error",
        "event",
        "exception",
        "exit",
        "fault",
        "http",
        "iso",
        "item",
        "lang",
        "language",
        "locale",
        "op",
        "opcode",
        "postal",
        "product",
        "rc",
        "reason",
        "region",
        "response",
        "result",
        "ret",
        "return",
        "sku",
        "source",
        "sqlstate",
        "state",
        "status",
        "zip",
    }
)
#: Qualifiers under which `code` names a credential or a redeemable value.
CREDENTIAL_CODE_QUALIFIERS = (
    "key",
    "otp",
    "mfa",
    "sms",
    "verification",
    "recovery",
    "invite",
    "promo",
    "coupon",
    "discount",
    "api",
    "access",
    "secret",
)
EXHAUSTIVE_KEYS = ("password", "token", "secret", "api_key", "session")

Row = dict[str, Any]


def recase(rng: random.Random, word: str, how: str | None = None) -> str:
    how = how or rng.choice(["lower", "upper", "title", "random"])
    if how == "lower":
        return word.lower()
    if how == "upper":
        return word.upper()
    if how == "title":
        return word[:1].upper() + word[1:]
    return "".join(c.upper() if rng.random() < 0.5 else c.lower() for c in word)


def cred(rng: random.Random) -> str:
    """A credential-shaped value from main's value class: alphanumerics with a
    digit after the first character, which is a lower-case letter."""
    n = rng.randint(8, 16)
    s = [rng.choice(IDENT) for _ in range(n)]
    s[0] = rng.choice("abcdefghijkmnpqrstuvwxyz")
    s[rng.randrange(1, n)] = rng.choice(DIGITS)
    return "".join(s)


def ident_run(rng: random.Random, lo: int, hi: int, joiners: bool = True) -> str:
    alphabet = IDENT + ("_-" if joiners else "")
    return "".join(rng.choice(alphabet) for _ in range(rng.randint(lo, hi)))


def sep_kind(sep: str) -> str:
    if any(c in "\r\n" for c in sep):
        return "line-break"
    if sep.count("=") >= 2 or sep.count(":") >= 2:
        return "operator-run"
    if sep.isspace():
        return "whitespace-only"
    return "mixed"


def _sample_pre(rng: random.Random, focus: bool) -> tuple[str, str]:
    if not focus:
        return "start", ""
    cls = rng.choice(list(PRE_CHARS))
    lead = "" if cls == "resource" else rng.choice(["", "abc"])
    return cls, lead + rng.choice(PRE_CHARS[cls])


def _sample_qual(rng: random.Random, focus: bool) -> tuple[str, str]:
    if not focus:
        return "none", ""
    kind = rng.choice(
        ["joined", "glued", "glued-long", "leading-joiner", "multi-segment"]
    )
    if kind == "joined":
        return kind, ident_run(rng, 1, 6, False) + rng.choice("_-")
    if kind == "glued":
        return kind, recase(rng, ident_run(rng, 1, 8, False))
    if kind == "glued-long":
        return kind, ident_run(rng, 25, 32, False)
    if kind == "leading-joiner":
        return kind, rng.choice(["_", "-", "--", "---", "_-"])
    segments = rng.randint(2, 10)
    return kind, "".join(
        ident_run(rng, 1, 3, False) + rng.choice("_-") for _ in range(segments)
    )


def _sample_suffix(rng: random.Random, focus: bool) -> tuple[str, str]:
    if not focus:
        return "none", ""
    kind = rng.choice(["declared", "trailing-joiner", "descriptive", "random", "long"])
    if kind == "declared":
        return kind, rng.choice(["s", "_id", "_key", "Key", "-id", "id", "key"])
    if kind == "trailing-joiner":
        return kind, rng.choice(["_", "-", "__", "_-", "--"])
    if kind == "descriptive":
        return kind, rng.choice(
            [
                "_new",
                "_old",
                "2",
                "Hash",
                "_hash",
                "_PROD",
                "_type",
                "_length",
                "ized",
                "_value",
            ]
        )
    if kind == "long":
        return kind, "_" + ident_run(rng, 25, 30, False)
    return kind, ident_run(rng, 1, 8)


def _sample_sep(rng: random.Random, focus: bool) -> tuple[str, str]:
    if not focus:
        return "default", rng.choice(["=", ": "])
    n = rng.choice([1, 2, 2, 3, 3])
    sep = "".join(
        rng.choice(":=") if rng.random() < 0.5 else rng.choice(SPACES) for _ in range(n)
    )
    return sep_kind(sep), sep


def _sample_value(rng: random.Random, focus: bool) -> tuple[str, str]:
    if not focus:
        return "default", cred(rng)
    kind = rng.choice(
        [
            "main-class",
            "plain",
            "number",
            "quoted",
            "unterminated-quote",
            "bracketed",
            "json-literal",
            "punctuated",
        ]
    )
    if kind == "main-class":
        v = "".join(rng.choice(MAIN_VALUE) for _ in range(rng.randint(3, 14)))
    elif kind == "plain":
        v = rng.choice(PLAIN_WORDS)
    elif kind == "number":
        v = str(rng.randint(0, 10 ** rng.randint(1, 12)))
    elif kind == "quoted":
        q = rng.choice("\"'")
        v = q + cred(rng) + q
    elif kind == "unterminated-quote":
        v = rng.choice("\"'") + cred(rng)
    elif kind == "bracketed":
        a, b = rng.choice(["()", "[]", "{}", "<>"])
        v = a + cred(rng) + b
    elif kind == "json-literal":
        v = rng.choice(
            ["null", "true", "false", "NaN", "Infinity", "-Infinity", "-0.0", "1e+20"]
        )
    else:
        v = cred(rng) + rng.choice(["!", "@x", "#1", "$", "%2F", "&x", "*"])
    return kind, v


def _text(f: dict[str, str]) -> str:
    return (
        f"{f.get('left', '')}{f['pre']}{f['qual']}{f['name']}{f['suffix']}"
        f"{f['sep']}{f['value']}{f.get('right', '')}"
    )


def _pct(rng: random.Random, text: str) -> str:
    """Percent-encode one to three characters of ``text`` (as `parse_qsl`
    decodes it)."""
    chars = list(text)
    for i in rng.sample(range(len(chars)), min(len(chars), rng.randint(1, 3))):
        chars[i] = "%{:02X}".format(ord(chars[i]))
    return "".join(chars)


_URL_BASE = ("https://h.example", "http://h.example:8443", "https://127.0.0.1")


def _sample_wrap(rng: random.Random, focus: bool) -> tuple[str, str, str]:
    """Where the pair sits: bare, in prose (a word and a delimiter each side:
    what ends main's `\\b` and value class), or in each position of a URL
    main's URL rule reads (path, query pair, query value, fragment)."""
    if not focus:
        return "none", "", ""
    kind = rng.choice(
        [
            "none",
            "none",
            "prose",
            "prose",
            "url-path",
            "url-query-pair",
            "url-query-value",
        ]
        + ["url-fragment"]
    )
    if kind == "none":  # so the mix block keeps unwrapped pairs too
        return kind, "", ""
    base = rng.choice(_URL_BASE)
    if kind == "prose":
        left = rng.choice(["", "abc", "error:", "log"]) + rng.choice(
            ASCII_PUNCT + SPACES
        )
        right = rng.choice(ASCII_PUNCT + SPACES) + rng.choice(["", "tail", "x=1"])
    elif kind == "url-path":
        left, right = base + "/p/", rng.choice(["", "/v1", "?q=1"])
    elif kind == "url-query-pair":
        left = base + "/?" + rng.choice(["", "x=1&"])
        right = rng.choice(["", "&y=2", "#f"])
    elif kind == "url-query-value":
        left, right = base + "/?q=", rng.choice(["", "&y=2"])
    else:
        left, right = base + "/#", ""
    return kind, left, right


def _url_row(rng: random.Random) -> Row:
    """A URL from main's URL grammar (`https?://[^\\s"'<>]+`, trailing `).,;`
    handed back, then `redact_auth_url`): userinfo (dropped), a host (one
    whose port does not parse makes main keep the URL as written), query
    pairs whose key is or is not in `AUTH_SECRET_QUERY_KEYS` (any case,
    optionally percent-encoded) with a credential, plain, percent-encoded or
    empty value, and a fragment (dropped). The pieces are what main removes:
    userinfo passwords, values under a secret key, fragment secrets -- one
    per secret, so at most five."""
    secrets: list[str] = []

    def secret(value: str) -> str:
        secrets.append(value)
        return value

    kinds = []
    ui = rng.choice(["none", "user", "user-pass", "pass-only"])
    userinfo = {"none": "", "user": "user@"}.get(ui) or (
        ("user:" if ui == "user-pass" else ":") + secret(cred(rng)) + "@"
    )
    if ui != "none":
        kinds.append("userinfo")
    host = rng.choice(
        ["h.example", "h.example:8443", "127.0.0.1", "[::1]", "h.example:99999"]
    )
    path = rng.choice(["", "/", "/v1/items", "/cb"])
    pairs = []
    for _ in range(rng.randint(0, 3)):
        key_kind = rng.choice(["secret", "secret", "secret-encoded", "other"])
        if key_kind == "other":
            key = rng.choice(["q", "page", "access", "next", "redirect", "u"])
        else:
            key = recase(rng, rng.choice(MAIN_QUERY_KEYS))
            if key_kind == "secret-encoded":
                key = _pct(rng, key)
        value_kind = rng.choice(["cred", "plain", "encoded", "empty"])
        value = {
            "cred": cred(rng),
            # a non-secret key's plain value never repeats a secret's word,
            # or a kept `redirect=Basic` would read as a kept secret `Basic`
            "plain": rng.choice(
                PLAIN_WORDS if key_kind != "other" else ("home", "en", "2")
            ),
            "encoded": _pct(rng, cred(rng)),
            "empty": "",
        }[value_kind]
        if key_kind != "other" and value:
            secret(value)
        kinds.append(f"query-{key_kind}-{value_kind}")
        pairs.append(f"{key}={value}")
    query = ("?" + "&".join(pairs)) if pairs else rng.choice(["", "?"])
    fragment = rng.choice(["", "#top", "#access_token=", "#"])
    if fragment in ("#access_token=", "#"):
        fragment += secret(cred(rng))
        kinds.append("fragment")
    left = rng.choice(["", "GET ", "see ", "(", '"', "url="])
    right = rng.choice(["", ".", ")", ",", ";", " failed", '"'])
    text = f"{left}{rng.choice(['http', 'https'])}://{userinfo}{host}{path}{query}{fragment}{right}"
    return {
        "block": "focus:url",
        "bucket": "url",
        "kinds": kinds,
        "f": None,
        "t": text,
        "value": " ".join(secrets),
        # one piece per secret: the longest run of an encoded one
        "pieces": [max(PIECE_RE.findall(v) or [v], key=len) for v in secrets],
    }


def _text_row(rng: random.Random, focus: str | None, block: str) -> Row:
    pre_k, pre = _sample_pre(rng, focus == "pre")
    qual_k, qual = _sample_qual(rng, focus == "qual")
    name_k = "case" if focus == "name" else "lower"
    name = rng.choice(KEYS)
    if focus == "name" and rng.random() < 0.3:
        name = "code"
        name_k = rng.choice(["code-qualified", "code-diagnostic", "code-credential"])
        if name_k == "code-diagnostic":
            word = rng.choice(sorted(DIAGNOSTIC_CODE_QUALIFIERS))
        elif name_k == "code-credential":
            word = rng.choice(CREDENTIAL_CODE_QUALIFIERS)
        else:
            word = ident_run(rng, 2, 10, False)
        qual = recase(rng, word, rng.choice(["lower", "upper", "title"])) + rng.choice(
            "_-"
        )
    if focus == "name" and name_k == "case":
        name = recase(rng, name)
    suf_k, suffix = _sample_suffix(rng, focus == "suffix")
    sep_k, sep = _sample_sep(rng, focus == "sep")
    val_k, value = _sample_value(rng, focus == "value")
    wrap_k, left, right = _sample_wrap(rng, focus == "wrap")
    kinds = {
        "wrap": wrap_k,
        "pre": pre_k,
        "qual": qual_k,
        "name": name_k,
        "suffix": suf_k,
        "sep": sep_k,
        "value": val_k,
    }
    f = {
        "pre": pre,
        "qual": qual,
        "name": name,
        "suffix": suffix,
        "sep": sep,
        "value": value,
        "left": left,
        "right": right,
    }
    bucket = f"{focus}:{kinds[focus]}" if focus in kinds else block
    return {"block": block, "bucket": bucket, "f": f, "t": _text(f), "value": value}


def _mix_row(rng: random.Random) -> Row:
    row = _text_row(rng, None, "mix")
    for focus in FOCI:
        other = _text_row(rng, focus, "mix")["f"]
        for part in ("left", "right") if focus == "wrap" else (focus,):
            row["f"][part] = other[part]
    row["t"] = _text(row["f"])
    row["value"] = row["f"]["value"]
    row["bucket"] = "mix"
    return row


def _bearer_row(rng: random.Random) -> Row:
    pre = rng.choice(list(BEARER_PRE) + [rng.choice(ASCII_PUNCT + NON_ASCII + SPACES)])
    ws = "".join(rng.choice(SPACES) for _ in range(rng.randint(1, 3)))
    v = cred(rng)
    wrap = rng.choice(["", "", "", '""', "''", "()", "[]", "{}", "<>"])
    val = (wrap[0] + v + wrap[1]) if wrap else v
    if pre.rstrip().endswith((":", "=")):
        kind = "after-:/="
    elif any(c in "\r\n" for c in ws):
        kind = "line-break"
    else:
        kind = "wrapped-value" if wrap else "plain-context"
    f = {
        "pre": pre,
        "qual": "",
        "name": recase(rng, "bearer"),
        "suffix": "",
        "sep": ws,
        "value": val,
    }
    return {
        "block": "focus:bearer",
        "bucket": f"bearer:{kind}",
        "f": f,
        "t": _text(f),
        "value": v,
    }


def _authz_row(rng: random.Random) -> Row:
    pre = rng.choice(["", "", "Proxy-", "x", "(", '"', "X-"])
    ws1 = "".join(rng.choice(SPACES) for _ in range(rng.choice([0, 0, 1, 2])))
    ws2 = "".join(rng.choice(SPACES) for _ in range(rng.choice([0, 1, 1, 2])))
    scheme = rng.choice(
        ["", "", "Bearer ", "bearer\t", "Basic ", "Token ", "Bearer\n\n"]
    )
    v = cred(rng) if rng.random() < 0.7 else rng.choice(PLAIN_WORDS)
    wrap = rng.choice(["", "", "", '""', "''", "()", "[]", "{}"])
    val = (wrap[0] + v + wrap[1]) if wrap else v
    sep = ws1 + rng.choice(":=") + ws2
    if any(c in "\r\n" for c in sep + scheme):
        kind = "line-break"
    else:
        kind = "wrapped-value" if wrap else "scheme" if scheme else "plain"
    f = {
        "pre": pre,
        "qual": "",
        "name": recase(rng, "authorization"),
        "suffix": "",
        "sep": sep + scheme,
        "value": val,
    }
    return {
        "block": "focus:authz",
        "bucket": f"authorization:{kind}",
        "f": f,
        "t": _text(f),
        "value": v,
    }


def _fixed_row(block: str, bucket: str, value: str, **parts: str) -> Row:
    f = {
        "pre": "",
        "qual": "",
        "name": "password",
        "suffix": "",
        "sep": "=",
        "value": value,
    }
    f.update(parts)
    return {"block": block, "bucket": bucket, "f": f, "t": _text(f), "value": value}


def _sep_rows(rng: random.Random, seps: list[str], keys: tuple[str, ...]) -> list[Row]:
    return [
        _fixed_row("sep", f"sep:{sep_kind(sep)}", v, name=k, sep=sep)
        for sep in seps
        for k in keys
        for v in (cred(rng), rng.choice(PLAIN_WORDS))
    ]


def _pre_rows(
    rng: random.Random, leads: tuple[str, ...], keys: tuple[str, ...]
) -> list[Row]:
    return [
        _fixed_row("pre", f"pre:{cls}", v, pre=lead + ch, name=k, sep=sep)
        for cls, chars in PRE_CHARS.items()
        for ch in chars
        for lead in (("",) if cls == "resource" else leads)
        for k in keys
        for sep in ("=", ": ", " ")
        for v in (cred(rng), rng.choice(PLAIN_WORDS))
    ]


def _code_rows(rng: random.Random) -> list[Row]:
    """`code` under every diagnostic and every credential qualifier, with a
    credential and a plain value, after `=` and `: `."""
    return [
        _fixed_row("code", f"code:{kind}", v, qual=word + "_", name="code", sep=sep)
        for kind, words in (
            ("diagnostic", sorted(DIAGNOSTIC_CODE_QUALIFIERS)),
            ("credential", CREDENTIAL_CODE_QUALIFIERS),
        )
        for word in words
        for sep in ("=", ": ")
        for v in (cred(rng), rng.choice(PLAIN_WORDS))
    ]


def _scalar_rows() -> list[Row]:
    rows = []
    for sname, s in SCALARS.items():
        for k in SCALAR_KEYS:
            for shape in ("top", "nested", "list"):
                if shape == "top":
                    obj: object = {k: s}
                elif shape == "nested":
                    obj = {"a": 1, "x": {k: s, "n": 2}}
                else:
                    obj = {"items": [{"id": 1, k: s}, {"id": 2}]}
                rows.append(
                    {
                        "block": "scalar",
                        "bucket": f"scalar:{sname}",
                        "f": None,
                        "o": obj,
                        "value": None,
                    }
                )
    return rows


def _focus_rows(rng: random.Random, per_axis: int) -> list[Row]:
    rows = []
    for focus in FOCI:
        rows += [_text_row(rng, focus, f"focus:{focus}") for _ in range(per_axis)]
    rows += [_bearer_row(rng) for _ in range(per_axis)]
    rows += [_authz_row(rng) for _ in range(per_axis)]
    rows += [_url_row(rng) for _ in range(per_axis)]
    return rows


PAIRS = list(SEP_ALPHABET) + [a + b for a in SEP_ALPHABET for b in SEP_ALPHABET]
BLOCKS = (
    ("sep", "pre", "code")
    + tuple(f"focus:{f}" for f in (*FOCI, "bearer", "authz", "url"))
    + ("mix", "scalar")
)


def corpus(tier: int) -> list[Row]:
    """Tier 1 (the default suite), or tier 2: tier 1 followed by the rest."""
    rng = random.Random(2026_09_26)
    rows = _sep_rows(rng, PAIRS, ("password", "token"))
    rows += _pre_rows(rng, ("",), ("password", "token"))
    rows += _code_rows(rng)
    rows += _focus_rows(rng, 500)
    rows += [_mix_row(rng) for _ in range(1000)]
    rows += _scalar_rows()
    if tier == 1:
        return rows
    rng = random.Random(2026_09_27)
    triples = ["".join(rng.choice(SEP_ALPHABET) for _ in range(3)) for _ in range(3000)]
    rows += _sep_rows(rng, PAIRS, ("secret", "api_key", "session"))
    rows += _sep_rows(rng, triples, EXHAUSTIVE_KEYS)
    rows += _pre_rows(rng, ("",), ("secret", "api_key", "session"))
    rows += _pre_rows(rng, ("abc",), EXHAUSTIVE_KEYS)
    rows += _focus_rows(rng, 9500)
    rows += [_mix_row(rng) for _ in range(19000)]
    return rows


def fingerprint(rows: list[Row]) -> str:
    """A digest of the rows as written: the fixture records the digest of the
    corpus it was recorded over, so a corpus change without a new fixture
    fails instead of comparing rows against another row's oracle."""
    digest = hashlib.sha256()
    for row in rows:
        digest.update(repr(row.get("t", row.get("o"))).encode("utf-8", "surrogatepass"))
        digest.update(b"\0")
    return digest.hexdigest()


# ------------------------------------------------------------ observation

#: The pieces of a value that count as the secret: main's value class, 3+.
PIECE_RE = re.compile(r"[A-Za-z0-9_.+/~=-]{3,}")
TEXT_SURFACES = (
    "E",
    "P",
    "EjAC",
    "EjAI",
    "EjUC",
    "EjUI",
    "PjAC",
    "PjAI",
    "PjUC",
    "PjUI",
    "POs",
    "POd",
)
SERIALISED = frozenset(TEXT_SURFACES[2:10] + ("POd", "POo"))
_DIGITS32 = "0123456789abcdefghijklmnopqrstuv"
_SPELLINGS = (
    ("AC", True, None),
    ("AI", True, 2),
    ("UC", False, None),
    ("UI", False, 2),
)
BIG = 10**7


def pieces(row: Row) -> list[str]:
    if "pieces" in row:
        return list(row["pieces"])
    return PIECE_RE.findall(row["value"]) if row["value"] else []


def observe(
    row: Row,
    engine: Callable[[str], str],
    redact_secrets: Callable[[str], str],
    process_output: Callable[[object], object],
) -> str:
    """What survives on each surface, as one base-32 digit per surface (bit i:
    piece i is still in the output, as written or percent-decoded -- main's
    URL rule re-encodes what it keeps, which is not a removal) and a final type letter for the dict
    path (`d` dict, `s` str). A piece's spelling is the same on every surface:
    nothing in `PIECE_RE`'s alphabet is escaped by `json.dumps`."""
    ps = pieces(row)
    assert len(ps) <= 5, ps

    def mask(out: str) -> str:
        decoded = unquote(out)
        return _DIGITS32[
            sum(
                1 << i
                for i, p in enumerate(ps)
                # a query value's `+` is a space once decoded
                if p in out or p in decoded or p.replace("+", " ") in decoded
            )
        ]

    def po(obj: object) -> tuple[str, str]:
        r = process_output(obj)
        if isinstance(r, str):
            return "s", r
        return ("d" if isinstance(r, dict) else "o"), json.dumps(r, indent=2)

    if "o" in row:
        kind, out = po(row["o"])
        return mask(out) + kind
    t = row["t"]
    outs = [engine(t), redact_secrets(t)]
    dumped = [json.dumps({"t": t}, ensure_ascii=a, indent=i) for _, a, i in _SPELLINGS]
    outs += [engine(d) for d in dumped] + [redact_secrets(d) for d in dumped]
    outs.append(po(t)[1])
    kind, out = po({"t": t})
    outs.append(out)
    return "".join(mask(o) for o in outs) + kind


def worse_surfaces(row: Row, main: str, here: str) -> list[str]:
    """The surfaces where `here` keeps a piece `main` removed, and `POd.type`
    / `POo.type` where main's result was a dict and this one's is not."""
    names = ("POo",) if "o" in row else TEXT_SURFACES
    worse = [s for s, m, h in zip(names, main, here) if int(h, 32) & ~int(m, 32)]
    if main[-1] == "d" and here[-1] != "d":
        worse.append(names[-1] + ".type")
    return worse


def removed_by_main(row: Row, main: str) -> bool:
    """The positive control: main removed some piece on some surface."""
    full = (1 << len(pieces(row))) - 1
    return any(int(m, 32) != full for m in main[:-1])


# ---------------------------------------------------- the accepted classes


def _plain(value: str) -> bool:
    v = value.strip("\"'").rstrip(".:!?")
    return bool(
        re.fullmatch(r"[A-Za-z_]+", v)
        or re.fullmatch(r"[+-]?[0-9]+|0[xX][0-9a-fA-F]+", v)
    )


def credential(value: str) -> bool:
    """D2, restated: not a plain word or number, and digit-bearing and 6+
    characters or punctuated and 8+."""
    value = value.strip("\"'")
    if _plain(value):
        return False
    return len(value) >= (6 if any(c.isdigit() for c in value) else 8)


_DECLARED = frozenset(
    {
        "access_token",
        "api_key",
        "apikey",
        "api-key",
        "assertion",
        "auth",
        "aws_access",
        "aws_secret",
        "client_secret",
        "code",
        "cookie",
        "credential",
        "credentials",
        "id_token",
        "jwt",
        "passwd",
        "password",
        "private_key",
        "privatekey",
        "private-key",
        "pwd",
        "refresh_token",
        "saml",
        "secret",
        "secret_access_key",
        "session",
        "set-cookie",
        "sid",
        "tenant-id",
        "tenant_id",
        "token",
    }
)
_WEAK = frozenset({"code", "auth", "credential", "credentials"})


def _declared(name: str, suffix: str) -> bool:
    if (name + suffix).lower() in _DECLARED:
        return True
    return (
        bool(re.fullmatch(r"[_-]?(?:id|key)|s", suffix.lower()))
        and name.lower() in _DECLARED
    )


def _single_case(word: str) -> bool:
    word = word.lstrip("-_")
    return (
        word.isupper() or word.islower() or (word[:1].isupper() and word[1:].islower())
    )


def _acronym_title(word: str) -> bool:
    return re.fullmatch(r"[A-Z0-9]{1,8}[A-Z][a-z]+", word.lstrip("-_")) is not None


def _inner_key(name: str, suffix: str) -> bool:
    """The key as written holds another secret-key word that starts inside the
    written name and ends inside the suffix (`aws_access` + `id` holds
    `sid`): main's containing match read that inner key."""
    word = (name + suffix).lower()
    for key in KEYS:
        start = word.find(key, 1)
        while start != -1:
            if start < len(name) < start + len(key):
                return True
            start = word.find(key, start + 1)
    return False


#: The stated classes (Consiliency/pmcp#234 rev 10: the audit's accept list
#: and the maintainer's four decisions). Each is decided from the row as
#: written -- `pre`, `qual`, `name`, `suffix`, `sep`, `value` -- and the
#: surface it is read on (a serialisation is a wrap), never from any output.
CLASSES = {
    "N3": "a glued prefix or a suffix of more than 24 alphanumerics: an identifier, not a key (a cost bound)",
    "N10": "a key inside an `arn:`/`urn:` resource name names a resource",
    "C3a": "`code` never fires on a whitespace-only separator",
    "C3": "a whitespace-only separator with a non-credential-shaped value (D2)",
    "N11": "a `name=value` token after a keyword and whitespace is its own pair",
    "N4": "a glued mixed-case key before whitespace is an identifier, not a key (`Ed25519PrivateKey X509Cert`, `gby3zPassword x`)",
    "N4c": "the key as written holds another key word starting inside the written name (`aws_accessid` holds `sid`)",
    "C4": "an operator run holding `==` or `::` with a non-credential-shaped value is a comparison or a path",
    "C5": "a suffixed key with a non-credential-shaped value names metadata",
    "C6": "a glued key (no joiner before the name, any case) counts only with a credential-shaped value that is not a URL or ARN",
    "C7": "after an unquoted key a value starting on `,`/`}` is JSON structure",
    "C8": "a separator whose last line break is unindented, with a non-credential-shaped unquoted value, ends a sentence",
    "N6b": "a quoted value on a serialised surface is JSON inside a leaf (Consiliency/pmcp#290)",
    "C10": "a weak key keeps a plain word or number; `code` under a non-OAuth qualifier keeps any non-credential-shaped value (N8's gate)",
    "C11": "`Authorization`/`Bearer` followed by a plain word, bare or wrapped in quotes or brackets, is prose",
    "C12": "a `code` key qualified by a diagnostic or descriptive word (`DIAGNOSTIC_CODE_QUALIFIERS`, the qualifier's last segment) keeps its value (`error_code=E_TIMEOUT_42`, `sqlstate_code=42P01`)",
}


def accepted(f: dict[str, str], surface: str) -> str | None:
    """The accepted class of a (row, surface) this redactor is worse on than
    main, or None: a defect."""
    pre, qual, name, suffix = f["pre"], f["qual"], f["name"], f["suffix"]
    pre = f.get("left", "") + pre  # the wrap's text before the pair
    glue_m = re.search(r"[A-Za-z0-9]*$", pre)
    full_qual = (glue_m.group(0) if glue_m else "") + qual
    sep, value = f["sep"], f["value"]
    lead_m = re.match(r"[:=]*", value)
    lead = lead_m.group(0) if lead_m else ""
    sep, value = sep + lead, value[len(lead) :] or value
    lname = name.lower()
    cred = credential(value)
    glued_run = re.search(r"[A-Za-z0-9]*$", full_qual)
    glued = glued_run.group(0) if glued_run else ""
    key = glued + name + suffix
    if len(glued) > 24 or sum(c.isalnum() for c in suffix) > 24:
        return "N3"
    if re.search(r"\b[au]rn:[^\s\"'<>]*$", pre, re.IGNORECASE):
        return "N10"
    if lname == "code" and re.split(r"[_-]", full_qual.strip("_-").lower())[-1] in (
        DIAGNOSTIC_CODE_QUALIFIERS
    ):
        return "C12"
    if sep.isspace():
        if lname == "code":
            return "C3a"
        if not cred:
            return "C3"
        # the value as written runs on into the wrap (`x=1`, `=tail`)
        if re.match(r"[A-Za-z_-]+=[^=]", value + f.get("right", "")):
            return "N11"
        if glued and not _single_case(key) and not _acronym_title(key):
            return "N4"
    if ("==" in sep or "::" in sep) and not cred:
        return "C4"
    if suffix and not _declared(name, suffix) and not cred:
        return "C5"
    if glued and (not cred or "://" in value or value.lower().startswith("arn:")):
        return "C6"
    if not sep.startswith('"') and value.startswith((",", "}")):
        return "C7"
    if (
        re.search(r"[\r\n][^ \t\xa0]*$", sep)
        and not value.startswith(('"', "'"))
        and not cred
    ):
        return "C8"
    if surface in SERIALISED and re.match(
        r"(?:(?:bearer|basic|digest|negotiate|ntlm|token)\s+)?\"", value, re.IGNORECASE
    ):
        return "N6b"
    if surface in SERIALISED and '\\"' in sep:
        return "N6b"
    if lname in _WEAK and (not suffix or _declared(name, suffix)):
        if _plain(value):
            return "C10"
        qualifier = full_qual.strip("_-").lower()
        if lname == "code" and qualifier not in _OAUTH_QUALIFIERS and not cred:
            return "C10"
    if lname in ("authorization", "bearer") and _plain(_unwrap(value)):
        return "C11"
    if _inner_key(name, suffix):
        return "N4c"
    return None


_OAUTH_QUALIFIERS = frozenset({"", "auth", "authorization", "oauth", "device", "user"})


def _unwrap(value: str) -> str:
    if len(value) >= 2 and value[0] + value[-1] in ('""', "''", "()", "[]", "{}", "<>"):
        return value[1:-1]
    return value
