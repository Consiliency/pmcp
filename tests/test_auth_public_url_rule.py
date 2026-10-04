"""Consiliency/pmcp#341: which auth URLs PMCP accepts, and whether each
refusal message is true of the URL it refuses.

`sanitize_public_auth_url` is the one rule behind the JWKS URL (CLI, env,
`create_http_app`, `AsyncJWKS`), the protected-resource metadata URL
(`create_http_app`, `normalize_auth_metadata`) and URL-mode elicitation. Its
plain-http refusal used to read "Public auth URL only allows http:// URLs
for loopback hosts." -- false for `http://127.0.0.1` on every path except
the operator's elicitation URL, the only caller that allows loopback http.

The classes below come from the parser's grammar -- scheme, host kind
(name, `localhost`, IPv4/IPv6/legacy-numeric literal, public or not),
port, userinfo, no host -- not from the issue's examples. Each row carries
the result for a caller that refuses loopback http (`strict`) and for the
one that allows it (`operator`). The tests check that every entry point
applies the row, that each refusal's text is true of the URL that reaches
it (by an oracle that does not call PMCP's classifier), that the plain-http
member is reached by exactly the refused plain-http URLs, and that the
README's example table agrees with the code.

Rev 2 (the #346 panel, F001): a host is also classified *as the fetcher
reads it*. yarl/aiohttp NFKC- and IDNA-map `１２７.0.0.1`, `127。0。0。1` and
full-width `localhost` to loopback, and a WHATWG parser decodes
`127%2E0%2E0%2E1`; pmcp used to see names there. The host-encoding axis --
Unicode digits and dots, IDNA mapping, trailing dots, `%` escapes, zone ids,
bracketed non-IPv6, IPv4-mapped, legacy numeric and octal -- is generated
from that grammar, and a differential test requires pmcp to agree with
`yarl.URL(url).raw_host` (and, where `node` is installed, with a WHATWG
`new URL()`) on every generated host.
"""

from __future__ import annotations

import ast
import asyncio
import contextlib
import io
import ipaddress
import re
import shutil
import socket
import string
import subprocess
import json
from pathlib import Path
from typing import Any, Callable
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import urlparse, urlsplit

import pytest
import yarl

from pmcp import auth as auth_mod
from pmcp.auth import (
    AuthMessage,
    auth_messages,
    check_auth_config,
    normalize_auth_metadata,
    sanitize_public_auth_url,
    sanitize_url_elicitation_url,
)
from pmcp.transport.http import create_http_app

_ROOT = Path(__file__).resolve().parents[1]

INVALID = "PUBLIC_URL_INVALID"
NOT_ABSOLUTE = "PUBLIC_URL_NOT_ABSOLUTE"
PLAIN_HTTP = "PUBLIC_URL_PLAIN_HTTP_REFUSED"
NOT_PUBLIC = "PUBLIC_URL_NOT_PUBLIC"
CONTROL = "PUBLIC_URL_CONTROL_CHARACTER"
NOT_CANONICAL = "PUBLIC_URL_HOST_NOT_CANONICAL"
BACKSLASH = "PUBLIC_URL_BACKSLASH"

# (label, url, strict, operator). `None` is accepted; otherwise the member
# `sanitize_public_auth_url` raises. `strict` is every caller but one;
# `operator` is `allow_loopback_http=True` (gateway.auth_connect's URL).
CLASSES: list[tuple[str, str, str | None, str | None]] = [
    # -- https, accepted ------------------------------------------------------
    ("https public name", "https://auth.example.com/jwks.json", None, None),
    ("https upper-case scheme", "HTTPS://auth.example.com/jwks.json", None, None),
    ("https public IPv4", "https://8.8.8.8/jwks.json", None, None),
    ("https public IPv6", "https://[2606:4700:4700::1111]/jwks.json", None, None),
    ("https port 8443", "https://auth.example.com:8443/jwks.json", None, None),
    ("https port 0", "https://auth.example.com:0/jwks.json", None, None),
    ("https empty port", "https://auth.example.com:/jwks.json", None, None),
    ("https userinfo", "https://user:pass@auth.example.com/jwks.json", None, None),
    ("https query+fragment", "https://auth.example.com/j?token=s#f", None, None),
    ("https punycode IDN", "https://xn--bcher-kva.example/jwks.json", None, None),
    ("https v4-mapped public", "https://[::ffff:8.8.8.8]/jwks.json", None, None),
    ("leading space", " https://auth.example.com/jwks.json", None, None),
    ("trailing newline", "https://auth.example.com/jwks.json\n", None, None),
    ("trailing CRLF", "https://auth.example.com/jwks.json\r\n", None, None),
    # Names are not resolved (Consiliency/pmcp#211): only the exact name
    # `localhost` counts as a loopback name. Pinned as it is today; see the
    # plan's follow-up.
    ("https *.localhost", "https://app.localhost/jwks.json", None, None),
    # -- https, non-public host -----------------------------------------------
    ("https loopback IPv4", "https://127.0.0.1/jwks.json", NOT_PUBLIC, NOT_PUBLIC),
    ("https loopback 127/8", "https://127.1.2.3/jwks.json", NOT_PUBLIC, NOT_PUBLIC),
    ("https loopback IPv6", "https://[::1]/jwks.json", NOT_PUBLIC, NOT_PUBLIC),
    ("https localhost", "https://localhost/jwks.json", NOT_PUBLIC, NOT_PUBLIC),
    ("https LOCALHOST", "https://LOCALHOST/jwks.json", NOT_PUBLIC, NOT_PUBLIC),
    ("https private IPv4", "https://10.0.0.5/jwks.json", NOT_PUBLIC, NOT_PUBLIC),
    ("https link-local", "https://169.254.169.254/x", NOT_PUBLIC, NOT_PUBLIC),
    ("https unspecified", "https://0.0.0.0/jwks.json", NOT_PUBLIC, NOT_PUBLIC),
    ("https v4-mapped", "https://[::ffff:127.0.0.1]/x", NOT_PUBLIC, NOT_PUBLIC),
    # -- plain http ------------------------------------------------------------
    ("http loopback IPv4", "http://127.0.0.1:8080/jwks.json", PLAIN_HTTP, None),
    ("http loopback IPv6", "http://[::1]/jwks.json", PLAIN_HTTP, None),
    ("http localhost", "http://localhost/jwks.json", PLAIN_HTTP, None),
    ("http userinfo loopback", "http://u:p@127.0.0.1/jwks.json", PLAIN_HTTP, None),
    ("http *.localhost", "http://app.localhost/jwks.json", PLAIN_HTTP, PLAIN_HTTP),
    ("http public name", "http://auth.example.com/jwks.json", PLAIN_HTTP, PLAIN_HTTP),
    ("http public IPv4", "http://8.8.8.8/jwks.json", PLAIN_HTTP, PLAIN_HTTP),
    ("http private IPv4", "http://10.0.0.5/jwks.json", PLAIN_HTTP, PLAIN_HTTP),
    # -- not an absolute http(s) URL ---------------------------------------------
    (
        "relative path",
        "/.well-known/oauth-protected-resource",
        NOT_ABSOLUTE,
        NOT_ABSOLUTE,
    ),
    ("scheme-relative", "//auth.example.com/jwks.json", NOT_ABSOLUTE, NOT_ABSOLUTE),
    ("no scheme", "auth.example.com/jwks.json", NOT_ABSOLUTE, NOT_ABSOLUTE),
    ("ftp scheme", "ftp://auth.example.com/jwks.json", NOT_ABSOLUTE, NOT_ABSOLUTE),
    ("javascript scheme", "javascript:alert(1)", NOT_ABSOLUTE, NOT_ABSOLUTE),
    ("https no host", "https:///jwks.json", NOT_ABSOLUTE, NOT_ABSOLUTE),
    ("https port only", "https://:443/jwks.json", NOT_ABSOLUTE, NOT_ABSOLUTE),
    # -- host not written in canonical ASCII (rev 2) ------------------------------
    (
        "fullwidth digits",
        "https://\uff11\uff12\uff17.0.0.1/x",
        NOT_CANONICAL,
        NOT_CANONICAL,
    ),
    (
        "ideographic stop",
        "https://127\u30020\u30020\u30021/x",
        NOT_CANONICAL,
        NOT_CANONICAL,
    ),
    (
        "fullwidth localhost",
        "http://\uff4c\uff4f\uff43\uff41\uff4c\uff48\uff4f\uff53\uff54/x",
        NOT_CANONICAL,
        NOT_CANONICAL,
    ),
    (
        "fullwidth link-local",
        "https://\uff11\uff16\uff19.\uff12\uff15\uff14.\uff11\uff16\uff19.\uff12\uff15\uff14/x",
        NOT_CANONICAL,
        NOT_CANONICAL,
    ),
    (
        "unicode IDN",
        "https://b\u00fccher.example/jwks.json",
        NOT_CANONICAL,
        NOT_CANONICAL,
    ),
    ("soft hyphen", "https://local\u00adhost/x", NOT_CANONICAL, NOT_CANONICAL),
    ("trailing-dot IPv4", "http://127.0.0.1./x", NOT_CANONICAL, NOT_CANONICAL),
    ("trailing-dot name", "https://localhost./jwks.json", NOT_CANONICAL, NOT_CANONICAL),
    ("percent-encoded IPv4", "https://127%2E0%2E0%2E1/x", NOT_CANONICAL, NOT_CANONICAL),
    ("percent-encoded name", "http://local%68ost/x", NOT_CANONICAL, NOT_CANONICAL),
    ("legacy decimal", "https://2130706433/jwks.json", NOT_CANONICAL, NOT_CANONICAL),
    (
        "legacy decimal, http",
        "http://2130706433/jwks.json",
        NOT_CANONICAL,
        NOT_CANONICAL,
    ),
    (
        "public legacy decimal",
        "https://134744072/jwks.json",
        NOT_CANONICAL,
        NOT_CANONICAL,
    ),
    ("legacy hex", "https://0x7f.1/jwks.json", NOT_CANONICAL, NOT_CANONICAL),
    ("legacy octal", "https://0177.0.0.1/jwks.json", NOT_CANONICAL, NOT_CANONICAL),
    ("leading-zero quad", "https://127.000.0.1/x", NOT_CANONICAL, NOT_CANONICAL),
    ("ends in a number", "https://example.123/jwks.json", NOT_CANONICAL, NOT_CANONICAL),
    ("IPv6 zone id", "http://[::1%25lo]/x", NOT_CANONICAL, NOT_CANONICAL),
    (
        "public IPv6 zone id",
        "https://[2606:4700::1111%25eth0]/x",
        NOT_CANONICAL,
        NOT_CANONICAL,
    ),
    ("bracketed non-IPv6", "https://[v1.fe]/jwks.json", NOT_CANONICAL, NOT_CANONICAL),
    ("underscore", "https://my_host.example.com/x", NOT_CANONICAL, NOT_CANONICAL),
    ("empty label", "https://a..example.com/x", NOT_CANONICAL, NOT_CANONICAL),
    # rev 3: the canonical text's own clauses, at their edges
    (
        "label edge hyphen, first",
        "https://-a.example.com/x",
        NOT_CANONICAL,
        NOT_CANONICAL,
    ),
    (
        "label edge hyphen, last",
        "https://a-.example.com/x",
        NOT_CANONICAL,
        NOT_CANONICAL,
    ),
    ("inner hyphens", "https://a--b.example.com/x", None, None),
    ("63-character label", "https://" + "a" * 63 + ".example.com/x", None, None),
    (
        "64-character label",
        "https://" + "a" * 64 + ".example.com/x",
        NOT_CANONICAL,
        NOT_CANONICAL,
    ),
    (
        "last label starts with a digit",
        "https://example.1com/x",
        NOT_CANONICAL,
        NOT_CANONICAL,
    ),
    # rev 3: userinfo with several `@` -- the host follows the last one
    ("two @, public host", "https://a@b@auth.example.com/x", None, None),
    ("two @, loopback host", "https://u@x@127.0.0.1/x", NOT_PUBLIC, NOT_PUBLIC),
    # rev 3: a backslash ends the authority for a WHATWG parser only
    (
        "backslash in userinfo",
        "https://127.0.0.1\\@auth.example.com/x",
        BACKSLASH,
        BACKSLASH,
    ),
    ("backslash in path", "https://auth.example.com/a\\b", BACKSLASH, BACKSLASH),
    # rev 3: IPv6 that embeds an IPv4 address
    (
        "IPv4-translated link-local",
        "https://[::ffff:0:a9fe:a9fe]/x",
        NOT_PUBLIC,
        NOT_PUBLIC,
    ),
    ("IPv4-translated public", "https://[::ffff:0:808:808]/x", None, None),
    ("local-use NAT64", "https://[64:ff9b:1::808:808]/x", NOT_PUBLIC, NOT_PUBLIC),
    ("6to4 public", "https://[2002:808:808::1]/x", NOT_PUBLIC, NOT_PUBLIC),
    # -- a control character inside the URL (rev 2, F002) ------------------------
    ("inner tab", "https://auth.example.com/key\tset.json", CONTROL, CONTROL),
    ("inner CR", "https://auth.example.com/key\rset.json", CONTROL, CONTROL),
    ("inner LF in host", "https://auth.exam\nple.com/x", CONTROL, CONTROL),
    ("NUL", "https://auth.example.com/key\x00set.json", CONTROL, CONTROL),
    ("DEL", "https://auth.example.com/key\x7fset.json", CONTROL, CONTROL),
    # -- unparseable -----------------------------------------------------------
    ("port 65536", "https://auth.example.com:65536/jwks.json", INVALID, INVALID),
    ("port not a number", "https://auth.example.com:abc/jwks.json", INVALID, INVALID),
    ("unclosed IPv6", "https://[::1/jwks.json", INVALID, INVALID),
]
_BY_LABEL = {label: (url, strict, op) for label, url, strict, op in CLASSES}
assert len(_BY_LABEL) == len(CLASSES), "duplicate class label"

_NAMES = {str(text): name for name, text in auth_messages().items()}


def _member(exc: BaseException) -> str:
    """The registry member an exception's text is, by name."""
    return _NAMES.get(str(exc), f"<not a member: {exc!s}>")


def _outcome(call: Callable[[], object]) -> str | None:
    try:
        call()
    except ValueError as exc:
        return _member(exc)
    return None


# --- the oracle: what each refusal's TEXT claims -------------------------------
#
# Rev 3 (#346 round 2, F001): the oracle is keyed by the message text, not by
# the member name, and each entry checks what those words say -- not the
# classifier's predicate. `test_the_text_oracle_covers_every_sanitiser_message`
# requires a key for every text `sanitize_public_auth_url` can raise, so a
# rewording without a re-derived claim fails. For a strict caller the texts
# also characterise acceptance exactly: a URL is accepted if and only if no
# text's claim holds for it (`test_each_message_is_true_and_acceptance_is_exact`).

_C0_DEL = {chr(code) for code in [*range(0x20), 0x7F]}


def _stripped(url: str) -> str:
    """What the sanitiser checks: leading C0/space and trailing tab/CR/LF off
    (stated in its docstring and the README)."""
    return url.lstrip("".join(map(chr, range(0x21)))).rstrip("\t\r\n")


def _literal(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """`host` as an IP literal, legacy numeric forms included (inet_aton is
    what a resolver does with them)."""
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        try:
            return ipaddress.IPv4Address(socket.inet_aton(host))
        except OSError:
            return None


_MASK32 = 0xFFFFFFFF
# IANA IPv6 Special-Purpose Address Registry entries (and RFC 4291/6145/5214
# forms outside it) that embed an IPv4 address, located by bit arithmetic here,
# independently of `ipaddress`' properties and of pmcp's networks.
_LOW32_EMBEDDINGS = [
    (ipaddress.ip_network("::ffff:0:0/96"), "RFC 4291 IPv4-mapped"),
    (ipaddress.ip_network("::ffff:0:0:0/96"), "RFC 6145 IPv4-translated"),
    (ipaddress.ip_network("::/96"), "RFC 4291 IPv4-compatible"),
    (ipaddress.ip_network("64:ff9b::/96"), "RFC 6052 NAT64 well-known"),
]
_UNLOCATABLE = ipaddress.ip_network("64:ff9b:1::/48")  # RFC 8215 local-use NAT64


def _embedded_v4(
    address: ipaddress.IPv6Address,
) -> tuple[bool, list[ipaddress.IPv4Address]]:
    """(is a low-32-bit translator form, every embedded IPv4 address)."""
    value = int(address)
    low = ipaddress.IPv4Address(value & _MASK32)
    translated = any(address in net for net, _ in _LOW32_EMBEDDINGS) or (
        (value >> 32) & _MASK32 in (0x00005EFE, 0x02005EFE)  # RFC 5214 ISATAP
    )
    found = [low] if translated else []
    if value >> 112 == 0x2002:  # RFC 3056 6to4: bits 16..47
        found.append(ipaddress.IPv4Address((value >> 80) & _MASK32))
    if value >> 96 == 0x20010000:  # RFC 4380 Teredo: server, ~client
        found.append(ipaddress.IPv4Address((value >> 64) & _MASK32))
        found.append(ipaddress.IPv4Address(~value & _MASK32))
    return translated, found


def _public_one(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return bool(
        address.is_global
        and not address.is_multicast
        and not getattr(address, "is_site_local", False)
    )


def _is_public_literal(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(address, ipaddress.IPv6Address):
        if address in _UNLOCATABLE:
            return False
        _, embedded = _embedded_v4(address)
        if not all(_public_one(v4) for v4 in embedded):
            return False
    return _public_one(address)  # rev 4: the IPv6 address itself, always


def _is_loopback(host: str) -> bool:
    """The loopback the operator path allows: the name `localhost` or an IP
    literal that is loopback once an IPv4-mapped form is unwrapped (not a
    legacy numeric form)."""
    if host.lower() == "localhost":
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        return address.ipv4_mapped.is_loopback
    return address.is_loopback


def _written_host(url: str) -> str:
    """The host as written: after `//`, before the path, without userinfo (up
    to the LAST `@`) or port, brackets kept. Independent of `auth._raw_host`."""
    after = url.split("//", 1)[1]
    for stop in "/?#":
        after = after.split(stop, 1)[0]
    after = after.rsplit("@", 1)[-1]
    if after.startswith("["):
        return after[: after.index("]") + 1] if "]" in after else after
    return after.split(":", 1)[0]


# "Public auth URL host must be A; B; or C." -- one claim per clause, keyed
# by the clause's own words and checked against those words.
_LETTERS_DIGITS = set(string.ascii_letters + string.digits)


def _is_described_dns_name(host: str) -> bool:
    # "a DNS name of dot-separated labels of 1 to 63 ASCII letters, digits and
    # hyphens, each starting and ending with a letter or digit, the last
    # starting with a letter, so never a number (an IDN in its xn-- form)"
    labels = host.split(".")
    return all(
        1 <= len(label) <= 63
        and set(label) <= _LETTERS_DIGITS | {"-"}
        and label[0] in _LETTERS_DIGITS
        and label[-1] in _LETTERS_DIGITS
        for label in labels
    ) and labels[-1][:1] in set(string.ascii_letters)


def _is_described_dotted_quad(host: str) -> bool:
    # "a dotted-quad IPv4 address with no leading zeros"
    parts = host.split(".")
    return len(parts) == 4 and all(
        part != ""
        and set(part) <= set(string.digits)
        and int(part) <= 255
        and not (len(part) > 1 and part[0] == "0")
        for part in parts
    )


def _is_described_bracketed_ipv6(host: str) -> bool:
    # "a bracketed IPv6 address with no zone id"
    if not (host.startswith("[") and host.endswith("]")) or "%" in host:
        return False
    try:
        socket.inet_pton(socket.AF_INET6, host[1:-1])
    except (OSError, ValueError):
        return False
    return True


_HOST_FORM_CLAIMS: dict[str, Callable[[str], bool]] = {
    "a DNS name of dot-separated labels of 1 to 63 ASCII letters, digits and "
    "hyphens, each starting and ending with a letter or digit, the last "
    "starting with a letter, so never a number (an IDN in its xn-- form)": _is_described_dns_name,
    "a dotted-quad IPv4 address with no leading zeros": _is_described_dotted_quad,
    "a bracketed IPv6 address with no zone id": _is_described_bracketed_ipv6,
}
_CANONICAL_PREFIX = "Public auth URL host must be "


def _host_form_clauses(text: str) -> list[str]:
    """The clauses of "Public auth URL host must be A; B; or C."."""
    assert text.startswith(_CANONICAL_PREFIX) and text.endswith("."), text
    clauses = text[len(_CANONICAL_PREFIX) : -1].split("; ")
    clauses[-1] = clauses[-1].removeprefix("or ")
    return clauses


def _matches_a_described_form(text: str, url: str) -> bool:
    host = _written_host(_stripped(url))
    return any(_HOST_FORM_CLAIMS[clause](host) for clause in _host_form_clauses(text))


def _parse_raises(url: str) -> bool:
    try:
        parts = urlsplit(_stripped(url))
        _ = parts.hostname, parts.port
    except ValueError:
        return True
    return False


def _host(url: str) -> str:
    return urlsplit(_stripped(url)).hostname or ""


def _claim_non_public(url: str) -> bool:
    # "Public auth URL host is a non-public IP literal or loopback name."
    host = _host(url)
    if host.lower() == "localhost":
        return True
    address = _literal(host)
    return address is not None and not _is_public_literal(address)


_CANONICAL_TEXT = (
    _CANONICAL_PREFIX
    + "; ".join(list(_HOST_FORM_CLAIMS)[:2])
    + "; or "
    + list(_HOST_FORM_CLAIMS)[2]
    + "."
)

_TEXT_CLAIMS: dict[str, Callable[[str], bool]] = {
    "Public auth URL contains a control character.": lambda url: any(
        c in _C0_DEL for c in _stripped(url)
    ),
    "Public auth URL contains a backslash.": lambda url: "\\" in url,
    "Invalid public auth URL.": _parse_raises,
    "Public auth URL must be an absolute HTTP(S) URL.": lambda url: (
        urlsplit(_stripped(url)).scheme not in {"http", "https"} or not _host(url)
    ),
    _CANONICAL_TEXT: lambda url: not _matches_a_described_form(_CANONICAL_TEXT, url),
    "Plain http:// is not accepted for this public auth URL.": lambda url: (
        urlsplit(_stripped(url)).scheme == "http"
    ),
    "Public auth URL host is a non-public IP literal or loopback name.": (
        _claim_non_public
    ),
}


def _claim_holds(member: str, url: str) -> bool:
    """True if the TEXT of `member` says something true about `url`."""
    return _TEXT_CLAIMS[str(getattr(AuthMessage, member))](url)


def _sanitiser_members() -> set[str]:
    tree = ast.parse((_ROOT / "src/pmcp/auth.py").read_text())
    func = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "sanitize_public_auth_url"
    )
    return {
        node.attr
        for node in ast.walk(func)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "AuthMessage"
    }


# --- 1. the rule, at the sanitiser ------------------------------------------


@pytest.mark.parametrize("label", list(_BY_LABEL))
def test_the_sanitiser_applies_the_rule(label: str) -> None:
    url, strict, operator = _BY_LABEL[label]
    assert _outcome(lambda: sanitize_public_auth_url(url)) == strict
    assert (
        _outcome(lambda: sanitize_public_auth_url(url, allow_loopback_http=True))
        == operator
    )


def test_the_text_oracle_covers_every_sanitiser_message() -> None:
    """Every text the sanitiser can raise has a claim here, keyed by the
    text: a reworded message fails until its claim is re-derived."""
    texts = {str(getattr(AuthMessage, name)) for name in _sanitiser_members()}
    assert texts == set(_TEXT_CLAIMS)
    clauses = _host_form_clauses(str(AuthMessage.PUBLIC_URL_HOST_NOT_CANONICAL))
    assert clauses == list(_HOST_FORM_CLAIMS)


@pytest.mark.parametrize(
    "label", [label for label, _, strict, _ in CLASSES if strict is None]
)
def test_every_accepted_url_is_absolute_https(label: str) -> None:
    """The README's accepted rule, from the other side: whatever a strict
    caller accepts is `https://` with a host that is not `localhost` and not
    a non-public IP literal."""
    url = _BY_LABEL[label][0]
    parts = urlsplit(url)
    assert _matches_a_described_form(_CANONICAL_TEXT, url)
    assert parts.scheme == "https"
    assert parts.hostname and parts.hostname.lower() != "localhost"
    address = _literal(parts.hostname)
    assert address is None or _is_public_literal(address)


_HOSTS = [
    "auth.example.com",
    "8.8.8.8",
    "[2606:4700:4700::1111]",
    "127.0.0.1",
    "127.1.2.3",
    "[::1]",
    "[::ffff:127.0.0.1]",
    "[::ffff:7f00:1]",
    "localhost",
    "LOCALHOST",
    "app.localhost",
    "10.0.0.5",
    "169.254.169.254",
    "u:p@127.0.0.1",
    "127.0.0.1:8080",
]


@pytest.mark.parametrize("allow", [False, True])
@pytest.mark.parametrize("scheme", ["http", "https"])
@pytest.mark.parametrize("host", _HOSTS)
def test_the_plain_http_member_is_reached_by_exactly_the_refused_plain_http(
    host: str, scheme: str, allow: bool
) -> None:
    """Every input that reaches `PUBLIC_URL_PLAIN_HTTP_REFUSED` is a plain
    http URL the caller refuses; every plain-http URL is refused with it
    unless the caller allows loopback and the host is loopback. (These hosts
    are all canonical; a non-canonical host is refused one step earlier, see
    the host-encoding tests.)"""
    url = f"{scheme}://{host}/jwks.json"
    got = _outcome(lambda: sanitize_public_auth_url(url, allow_loopback_http=allow))
    hostname = urlsplit(url).hostname or ""
    if scheme == "https":
        assert got != PLAIN_HTTP
    elif allow and _is_loopback(hostname):
        assert got is None
    else:
        assert got == PLAIN_HTTP


def test_the_plain_http_text_makes_no_loopback_claim() -> None:
    """The old text, "only allows http:// URLs for loopback hosts", was false
    for every strict caller. The member says only what is true of every
    input that reaches it."""
    text = str(AuthMessage.PUBLIC_URL_PLAIN_HTTP_REFUSED)
    assert text == "Plain http:// is not accepted for this public auth URL."
    assert "loopback" not in text.lower()
    assert not hasattr(AuthMessage, "PUBLIC_URL_HTTP_LOOPBACK_ONLY")


def test_the_table_covers_every_member_the_sanitiser_raises() -> None:
    """A member added to `sanitize_public_auth_url` without a class here
    fails this test."""
    tree = ast.parse((_ROOT / "src/pmcp/auth.py").read_text())
    func = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "sanitize_public_auth_url"
    )
    raised = {
        node.attr
        for node in ast.walk(func)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "AuthMessage"
    }
    covered = {m for _, _, s, o in CLASSES for m in (s, o) if m is not None}
    assert raised == covered


# --- 2. every entry point applies the same row -------------------------------

_ISSUER = "https://issuer.example"
_AUDIENCE = "https://pmcp.example/mcp"
_GOOD_JWKS = "https://issuer.example/.well-known/jwks.json"


def _app(**kwargs: Any) -> None:
    with patch("pmcp.transport.http.StreamableHTTPSessionManager", autospec=True):
        create_http_app(MagicMock(), **kwargs)


def _metadata_app(url: str) -> None:
    _app(auth_mode="none", protected_resource_metadata_url=url)


def _jwks_app(url: str) -> None:
    _app(
        auth_mode="resource-server",
        resource_server_issuer=_ISSUER,
        resource_server_jwks_url=url,
        resource_server_audience=_AUDIENCE,
    )


def _normalised(url: str) -> None:
    """`normalize_auth_metadata` does not raise: it drops the URL and says
    why in a diagnostic. Re-raise that reason so the row can be compared."""
    info = normalize_auth_metadata(protected_resource_metadata_url=url)
    if info.protected_resource_metadata_url is not None:
        return
    for text in _NAMES:
        if any(
            d.startswith("protected_resource_metadata_url ignored: " + text)
            for d in info.diagnostics
        ):
            raise ValueError(text)
    raise AssertionError(f"dropped without a registry reason: {info.diagnostics}")


def _elicitation(provenance: str) -> Callable[[str], None]:
    def call(url: str) -> None:
        try:
            sanitize_url_elicitation_url(url, provenance=provenance)  # type: ignore[arg-type]
        except ValueError as exc:
            assert str(exc) == AuthMessage.ELICITATION_URL_INVALID
            assert exc.__cause__ is not None
            raise ValueError(str(exc.__cause__)) from exc

    return call


_STDERR = re.compile(r"^error: (?P<text>.*)\n$", re.S)


def _cli_factory(monkeypatch: pytest.MonkeyPatch) -> Callable[[str], None]:
    from pmcp.cli import parse_args, run_server

    def call(url: str) -> None:
        for name in (
            "PMCP_TRANSPORT",
            "PMCP_AUTH_MODE",
            "PMCP_OAUTH_JWKS_URL",
            "PMCP_REQUIRED_SCOPES",
        ):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setattr(
            "sys.argv",
            [
                "pmcp",
                "--transport",
                "http",
                "--auth-mode",
                "resource-server",
                "--oauth-issuer",
                _ISSUER,
                "--oauth-audience",
                _AUDIENCE,
                "--oauth-jwks-url",
                url,
            ],
        )
        err = io.StringIO()
        args = parse_args()
        try:
            with (
                patch("pmcp.server.GatewayServer") as gs,
                contextlib.redirect_stderr(err),
            ):
                gs.return_value.run = AsyncMock()
                asyncio.run(run_server(args))
        except SystemExit as exc:
            assert exc.code == 1
            match = _STDERR.match(err.getvalue())
            assert match, err.getvalue()
            raise ValueError(match["text"]) from None

    return call


_ENTRIES: dict[str, Callable[[pytest.MonkeyPatch], Callable[[str], None]]] = {
    "create_http_app metadata URL": lambda mp: _metadata_app,
    "create_http_app JWKS URL": lambda mp: _jwks_app,
    "AsyncJWKS": lambda mp: auth_mod.AsyncJWKS,
    "check_auth_config jwks_url": lambda mp: lambda u: check_auth_config(jwks_url=u),
    "check_auth_config metadata_url": (
        lambda mp: lambda u: check_auth_config(metadata_url=u)
    ),
    "CLI --oauth-jwks-url": _cli_factory,
    "normalize_auth_metadata": lambda mp: _normalised,
    "elicitation, remote": lambda mp: _elicitation("remote"),
    "elicitation, operator": lambda mp: _elicitation("operator"),
}


@pytest.mark.parametrize("entry", list(_ENTRIES))
@pytest.mark.parametrize("label", list(_BY_LABEL))
def test_every_entry_point_applies_the_rule(
    monkeypatch: pytest.MonkeyPatch, label: str, entry: str
) -> None:
    url, strict, operator = _BY_LABEL[label]
    expected = operator if entry == "elicitation, operator" else strict
    call = _ENTRIES[entry](monkeypatch)
    assert _outcome(lambda: call(url)) == expected


@pytest.mark.parametrize(
    ("entry", "member"),
    [
        ("create_http_app metadata URL", "METADATA_URL_NOT_USABLE"),
        ("create_http_app JWKS URL", "JWKS_URL_NOT_USABLE"),
        ("AsyncJWKS", "JWKS_URL_NOT_USABLE"),
        ("CLI --oauth-jwks-url", "JWKS_URL_NOT_USABLE"),
    ],
)
def test_inner_whitespace_is_refused_at_startup_not_by_the_sanitiser(
    monkeypatch: pytest.MonkeyPatch, entry: str, member: str
) -> None:
    """The sanitiser keeps `key set.json`; the startup check refuses it,
    because a rejection could not carry it in `{url}` (#326)."""
    url = "https://auth.example.com/key set.json"
    assert _outcome(lambda: sanitize_public_auth_url(url)) is None
    call = _ENTRIES[entry](monkeypatch)
    assert _outcome(lambda: call(url)) == member


# --- 3. the README's example table is the code's ------------------------------

_README_REASONS = {
    "accepted": {None},
    "accepted (userinfo dropped)": {None},
    "refused: plain http": {PLAIN_HTTP},
    "refused: non-public host": {NOT_PUBLIC},
    "refused: not an absolute http(s) URL": {NOT_ABSOLUTE},
    "refused: invalid URL": {INVALID},
    "refused: whitespace": {"JWKS_URL_NOT_USABLE", "METADATA_URL_NOT_USABLE"},
    "refused: host not in canonical form": {NOT_CANONICAL},
    "refused: backslash": {"PUBLIC_URL_BACKSLASH"},
}
_ROW = re.compile(r"^\| `(?P<url>[^`]+)` \| (?P<result>[^|]+?) \|$")


def _readme_rows() -> list[tuple[str, str]]:
    text = (_ROOT / "README.md").read_text()
    block = text.split("<!-- auth-url-rule:begin -->", 1)[1].split(
        "<!-- auth-url-rule:end -->", 1
    )[0]
    rows = []
    for line in block.strip().splitlines()[2:]:  # header and rule
        match = _ROW.match(line)
        assert match, f"unparsed README row: {line!r}"
        rows.append((match["url"], match["result"]))
    return rows


def test_the_readme_url_table_matches_the_code() -> None:
    rows = _readme_rows()
    assert rows
    for url, result in rows:
        assert result in _README_REASONS, f"unknown README result {result!r}"
        for kind, call in (
            ("jwks", lambda: check_auth_config(jwks_url=url)),
            ("metadata", lambda: check_auth_config(metadata_url=url)),
        ):
            got = _outcome(call)
            assert got in _README_REASONS[result], (url, kind, got)
        if result == "accepted (userinfo dropped)":
            assert "@" not in sanitize_public_auth_url(url)
    # every reason the README names is shown by at least one example
    assert {result for _, result in rows} == set(_README_REASONS)


def test_the_superseded_wording_is_gone() -> None:
    """The refused-list wording that read as if http to loopback were
    allowed, and the old message, appear nowhere current."""
    readme = (_ROOT / "README.md").read_text()
    changelog = (_ROOT / "CHANGELOG.md").read_text()
    source = (_ROOT / "src/pmcp/auth.py").read_text()
    assert "plain-http non-loopback" not in readme
    assert "plain http to a non-loopback" not in changelog
    assert 'only allows http:// URLs for loopback hosts")' not in source


# --- 4. rev 2: the host as the fetcher reads it --------------------------------
#
# Generated from the host grammar, not from the panel's examples: each axis is
# a way a fetcher's parser can read a different host than the one written.

_DIGIT_SCRIPTS = {
    "ascii": "0123456789",
    "fullwidth": "０１２３４５６７８９",
    "math-bold": "".join(chr(0x1D7CE + i) for i in range(10)),
    "arabic-indic": "".join(chr(0x0660 + i) for i in range(10)),
}
_DOTS = {
    "full stop": ".",
    "ideographic": "。",
    "fullwidth": "．",
    "halfwidth ideographic": "｡",
    "percent": "%2E",
}
_ADDRESSES = ["127.0.0.1", "169.254.169.254", "10.0.0.5", "0.0.0.0", "8.8.8.8"]


def _ipv4_forms(address: str) -> list[str]:
    value = int(ipaddress.IPv4Address(address))
    octets = address.split(".")
    return [
        str(value),  # legacy decimal
        hex(value),  # legacy hex
        ".".join(f"0{int(o):o}" for o in octets),  # dotted octal
        ".".join(hex(int(o)) for o in octets),  # dotted hex
        ".".join(f"{int(o):03d}" for o in octets),  # leading zeros
        f"{octets[0]}.{octets[1]}.{int(octets[2]) * 256 + int(octets[3])}",  # 3-part
    ]


def _generated_hosts() -> list[str]:
    hosts: list[str] = []
    for address in _ADDRESSES:
        for digits in _DIGIT_SCRIPTS.values():
            table = str.maketrans("0123456789", digits)
            for dot in _DOTS.values():
                for suffix in ("", "."):
                    hosts.append(address.translate(table).replace(".", dot) + suffix)
        hosts.extend(_ipv4_forms(address))
        hosts.append(f"[::ffff:{address}]")
        hosts.append(f"[::{address}]")
        hosts.append(f"[{address}]")
    fullwidth = str.maketrans(
        string.ascii_lowercase, "".join(chr(0xFF41 + i) for i in range(26))
    )
    for name in ("localhost", "auth.example.com", "app.localhost"):
        hosts += [
            name,
            name.upper(),
            name.translate(fullwidth),
            name + ".",
            name.replace("o", "­o", 1),  # soft hyphen (IDNA: ignored)
            name.replace("o", "‍o", 1),  # zero-width joiner
            name.replace("l", "%6C", 1),
            name.replace("l", "ⓛ", 1),  # circled l (NFKC: l)
            "." + name,
            name.replace(".", "..", 1),
        ]
    hosts += [
        "xn--bcher-kva.example",
        "bücher.example",
        "my_host.example.com",
        "example.123",
        "example.0x7f",
        "[::1]",
        "[0:0:0:0:0:0:0:1]",
        "[::ffff:7f00:1]",
        "[::1%25lo]",
        "[fe80::1%25eth0]",
        "[2606:4700:4700::1111]",
        "[2606:4700:4700::1111%25eth0]",
        "[v1.fe]",
        "[：：1]",  # fullwidth colons
    ]
    hosts += [f"[{address}]" for address in _scoped_embeddings()]
    return list(dict.fromkeys(hosts))


# Rev 4 (#346 round 2, grok F001): every IPv4-embedding interface id crossed
# with every IPv6 scope -- the IANA IPv6 Special-Purpose Address Registry's
# blocks, the scoped ranges (link-local, site-local, ULA, multicast) and a
# global unicast prefix -- so an embedded public IPv4 cannot vouch for a
# non-public IPv6 address.
_SCOPE_PREFIXES = {
    "global unicast": "2606:4700::",
    "link-local fe80::/10": "fe80::",
    "site-local fec0::/10": "fec0::",
    "ULA fc00::/7": "fd00::",
    "multicast ff02 (link)": "ff02::",
    "multicast ff0e (global)": "ff0e::",
    "documentation 2001:db8::/32": "2001:db8::",
    "documentation 3fff::/20": "3fff::",
    "discard-only 100::/64": "100::",
    "benchmarking 2001:2::/48": "2001:2::",
    "ORCHIDv2 2001:20::/28": "2001:20::",
    "AMT 2001:3::/32": "2001:3::",
    "SRv6 SIDs 5f00::/16": "5f00::",
    "6to4 2002::/16": "2002::",
    "Teredo 2001::/32": "2001::",
    "loopback/unspecified ::/64": "::",
}
_INTERFACE_IDS = {
    "ISATAP 00-00-5E-FE": 0x00005EFE,
    "ISATAP 02-00-5E-FE (u/g)": 0x02005EFE,
    "IPv4-mapped-like ::ffff": 0x0000FFFF,
    "plain low 32 bits": 0,
}


def _scoped_embeddings() -> list[ipaddress.IPv6Address]:
    out = []
    for prefix in _SCOPE_PREFIXES.values():
        base = int(ipaddress.IPv6Address(prefix)) & ~((1 << 64) - 1)
        for iid in _INTERFACE_IDS.values():
            for v4 in ("8.8.8.8", "10.0.0.5", "127.0.0.1"):
                out.append(
                    ipaddress.IPv6Address(
                        base | (iid << 32) | int(ipaddress.IPv4Address(v4))
                    )
                )
    return out


_GENERATED = _generated_hosts()


def _is_public(host: str) -> bool:
    """Whether a host a fetcher will connect to is public: `localhost` is
    not, an IP literal (legacy numeric via `inet_aton`, IPv4-mapped
    and every embedded IPv4 classified) is public only if `_is_public_literal`, and any other
    name is public by assumption (names are not resolved, #211)."""
    host = host.strip("[]")
    if host.lower().rstrip(".") == "localhost":
        return False
    address = _literal(host.split("%", 1)[0].rstrip("."))
    return address is None or _is_public_literal(address)


def _same_host(stored: str, fetched: str) -> bool:
    try:
        return ipaddress.ip_address(stored.strip("[]")) == ipaddress.ip_address(
            fetched.strip("[]")
        )
    except ValueError:
        return stored.lower() == fetched.lower()


def _stored_host(url: str) -> str:
    return urlsplit(sanitize_public_auth_url(url)).hostname or ""


def _yarl_host(url: str) -> str | None:
    try:
        return yarl.URL(url).raw_host
    except ValueError:
        return None


@pytest.mark.parametrize("host", _GENERATED)
def test_pmcp_classifies_the_host_yarl_will_connect_to(host: str) -> None:
    """The differential: whatever pmcp accepts, yarl (aiohttp's URL parser,
    which `AsyncJWKS` fetches with) reads as the same host, and that host
    is public; whatever yarl reads as a non-public host, pmcp refuses."""
    for scheme, allow in (("https", False), ("http", True)):
        url = f"{scheme}://{host}/jwks.json"
        got = _outcome(lambda: sanitize_public_auth_url(url, allow_loopback_http=allow))
        fetched = _yarl_host(url)
        if got is None:
            assert fetched is not None, url
            stored = urlsplit(
                sanitize_public_auth_url(url, allow_loopback_http=allow)
            ).hostname
            assert stored and _same_host(stored, fetched), (url, stored, fetched)
            if scheme == "https":
                assert _is_public(fetched), (url, fetched)
            else:
                assert _is_loopback(fetched), (url, fetched)
        if fetched is not None and not _is_public(fetched) and scheme == "https":
            assert got is not None, (url, fetched)
        if got is None or got == NOT_CANONICAL:
            assert (got is None) == _matches_a_described_form(_CANONICAL_TEXT, url), url


_NODE = shutil.which("node")


@pytest.mark.skipif(_NODE is None, reason="node is not installed")
def test_pmcp_classifies_the_host_a_browser_will_open() -> None:
    """The same differential against a WHATWG URL parser (`new URL()` in
    node), which is what opens an elicitation URL and what most clients use
    on a published metadata URL."""
    urls = [f"https://{host}/x" for host in _GENERATED]
    assert _NODE is not None
    out = subprocess.run(
        [
            _NODE,
            "-e",
            "const u=JSON.parse(require('fs').readFileSync(0,'utf8'));"
            "console.log(JSON.stringify(u.map(x=>{try{return new URL(x).hostname}"
            "catch(e){return null}})))",
        ],
        input=json.dumps(urls),
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    ).stdout
    for url, browser in zip(urls, json.loads(out)):
        got = _outcome(lambda: sanitize_public_auth_url(url))
        if got is None:
            assert browser is not None, url
            assert _same_host(_stored_host(url), browser), (url, browser)
            assert _is_public(browser), (url, browser)
        if browser is not None and not _is_public(browser):
            assert got is not None, (url, browser)


@pytest.mark.parametrize(
    "host",
    [
        "１２７.0.0.1",
        "127。0。0。1",
        "ｌｏｃａｌｈｏｓｔ",
        "１６９.２５４.１６９.２５４",
    ],
)
def test_the_panel_hosts_map_to_non_public_hosts_in_yarl_and_are_refused(
    host: str,
) -> None:
    """F001's four hosts, as a tripwire on the premise: yarl does map them
    to a loopback or link-local host, and every strict entry point refuses
    them as non-canonical."""
    url = f"https://{host}/jwks.json"
    fetched = _yarl_host(url)
    assert fetched is not None and not _is_public(fetched)
    assert _outcome(lambda: sanitize_public_auth_url(url)) == NOT_CANONICAL
    assert _outcome(lambda: check_auth_config(jwks_url=url)) == NOT_CANONICAL
    assert _outcome(lambda: _metadata_app(url)) == NOT_CANONICAL
    assert _outcome(lambda: _elicitation("remote")(url)) == NOT_CANONICAL


# --- 5. rev 3: every message is true, and the texts define acceptance ---------
#
# Inputs generated from the grammar the texts speak about: every C0 control
# and DEL at every position, several `@`, a backslash, labels at their
# length and hyphen edges, and every IPv4-embedding IPv6 form -- plus every
# class above and every generated host.

_CONTROLS = [*range(0x20), 0x7F]


def _control_urls() -> list[str]:
    urls = []
    for code in _CONTROLS:
        c = chr(code)
        urls += [
            f"https://auth.exa{c}mple.com/x",
            f"https://auth.example.com/k{c}s",
            f"{c}https://auth.example.com/x",
            f"https://auth.example.com/x{c}",
        ]
    return urls


def _label_urls() -> list[str]:
    labels = [
        "a",
        "a-b",
        "a--b",
        "-a",
        "a-",
        "-",
        "1a",
        "a1",
        "xn--bcher-kva",
        "0x7f",
        "123",
    ]
    labels += ["a" * 62 + "b", "a" * 63 + "b", "a" * 64]
    urls = []
    for label in labels:
        urls += [f"https://{label}.example.com/x", f"https://example.{label}/x"]
    return urls


def _netloc_urls() -> list[str]:
    urls = []
    for userinfo in ["", "u@", "u:p@", "a@b@", "u@x@", "@", "u@@"]:
        for host in ["auth.example.com", "127.0.0.1", "[::1]", "8.8.8.8"]:
            urls.append(f"https://{userinfo}{host}/x")
    for netloc in [
        "127.0.0.1\\@auth.example.com",
        "auth.example.com\\@127.0.0.1",
        "127.0.0.1:80\\@auth.example.com",
        "auth.example.com\\",
    ]:
        urls.append(f"https://{netloc}/x")
    return urls


_V4_SAMPLES = [
    "8.8.8.8",
    "127.0.0.1",
    "10.0.0.5",
    "169.254.169.254",
    "100.64.0.1",
    "0.0.0.0",
]


def _embedding_addresses() -> list[tuple[str, ipaddress.IPv6Address]]:
    """Every IPv4-embedding IPv6 form, built by bit arithmetic."""
    out = []
    for v4s in _V4_SAMPLES:
        v4 = int(ipaddress.IPv4Address(v4s))
        for net, label in _LOW32_EMBEDDINGS:
            out.append(
                (f"{label}/{v4s}", ipaddress.IPv6Address(int(net.network_address) | v4))
            )
        out.append(
            (
                f"ISATAP/{v4s}",
                ipaddress.IPv6Address((0x2606_4700 << 96) | (0x5EFE << 32) | v4),
            )
        )
        out.append(
            (f"6to4/{v4s}", ipaddress.IPv6Address((0x2002 << 112) | (v4 << 80) | 1))
        )
        out.append(
            (
                f"Teredo server/{v4s}",
                ipaddress.IPv6Address(
                    (0x2001_0000 << 96)
                    | (v4 << 64)
                    | (~int(ipaddress.IPv4Address("8.8.8.8")) & _MASK32)
                ),
            )
        )
        out.append(
            (
                f"Teredo client/{v4s}",
                ipaddress.IPv6Address(
                    (0x2001_0000 << 96)
                    | (int(ipaddress.IPv4Address("8.8.8.8")) << 64)
                    | (~v4 & _MASK32)
                ),
            )
        )
        out.append(
            (
                f"local-use NAT64/{v4s}",
                ipaddress.IPv6Address(int(_UNLOCATABLE.network_address) | v4),
            )
        )
    return out


_EMBEDDINGS = _embedding_addresses()


def _truth_inputs() -> list[str]:
    urls = [url for _, url, _, _ in CLASSES]
    for host in _GENERATED:
        urls += [f"https://{host}/x", f"http://{host}/x"]
    urls += _control_urls() + _label_urls() + _netloc_urls()
    urls += [f"https://[{address}]/x" for _, address in _EMBEDDINGS]
    return list(dict.fromkeys(urls))


_TRUTH_INPUTS = _truth_inputs()


@pytest.mark.parametrize(
    "url", _TRUTH_INPUTS, ids=[repr(u)[:60] for u in _TRUTH_INPUTS]
)
def test_each_message_is_true_and_acceptance_is_exact(url: str) -> None:
    """For a strict caller: a refusal's text is true of the URL it refuses,
    and a URL is accepted only when no refusal text is true of it. For the
    operator: the same, except that plain http to a loopback host is the one
    true claim it may accept through."""
    for allow in (False, True):
        try:
            sanitize_public_auth_url(url, allow_loopback_http=allow)
        except ValueError as exc:
            text = str(exc)
            assert text in _TEXT_CLAIMS, f"unknown refusal text {text!r}"
            assert _TEXT_CLAIMS[text](url), f"{text!r} is untrue for {url!r}"
            continue
        true_claims = {text for text, claim in _TEXT_CLAIMS.items() if claim(url)}
        loopback_http = {
            str(AuthMessage.PUBLIC_URL_PLAIN_HTTP_REFUSED),
            str(AuthMessage.PUBLIC_URL_NOT_PUBLIC),
        }
        if allow and true_claims and true_claims <= loopback_http:
            # the operator's one exception: plain http to a loopback host
            assert urlsplit(_stripped(url)).scheme == "http", url
            assert _is_loopback(_host(url)), url
            continue
        assert true_claims == set(), (url, allow, true_claims)


@pytest.mark.parametrize("code", _CONTROLS, ids=hex)
def test_every_control_character_is_refused_inside_and_stripped_only_at_the_ends(
    code: int,
) -> None:
    c = chr(code)
    for inner in (f"https://auth.exa{c}mple.com/x", f"https://auth.example.com/k{c}s"):
        assert _outcome(lambda: sanitize_public_auth_url(inner)) == CONTROL
    leading = _outcome(
        lambda: sanitize_public_auth_url(f"{c}https://auth.example.com/x")
    )
    assert leading == (None if code < 0x20 else CONTROL)  # DEL is not C0
    trailing = _outcome(
        lambda: sanitize_public_auth_url(f"https://auth.example.com/x{c}")
    )
    assert trailing == (None if c in "\t\r\n" else CONTROL)


@pytest.mark.parametrize("label", [label for label, _ in _EMBEDDINGS])
def test_every_ipv4_embedding_is_classified_on_the_embedded_address(label: str) -> None:
    """The IANA/RFC embedding list, differentially: the arithmetic here agrees
    with `ipaddress`' own `ipv4_mapped`, `sixtofour` and `teredo` where they
    exist, and pmcp accepts the literal only if every embedded IPv4 address
    is public (6to4 and Teredo: and the IPv6 address too; local-use NAT64:
    never)."""
    address = dict(_EMBEDDINGS)[label]
    translated, embedded = _embedded_v4(address)
    if address.ipv4_mapped is not None:
        assert address.ipv4_mapped in embedded
    if address.sixtofour is not None:
        assert address.sixtofour in embedded
    if address.teredo is not None:
        assert set(address.teredo) <= set(embedded)
    expected_public = _is_public_literal(address)
    got = _outcome(lambda: sanitize_public_auth_url(f"https://[{address}]/x"))
    assert got == (None if expected_public else NOT_PUBLIC), (label, str(address))
    if embedded and not all(_public_one(v4) for v4 in embedded):
        assert got == NOT_PUBLIC


def test_the_canonical_rule_has_one_wording() -> None:
    """`CANONICAL_HOST_FORMS` is the one source: the message is built from
    it, and the README rule and the sanitiser docstring carry each clause."""
    from pmcp.auth import CANONICAL_HOST_FORMS

    def flat(text: str) -> str:
        return " ".join(text.split())

    readme = flat((_ROOT / "README.md").read_text())
    doc = flat(sanitize_public_auth_url.__doc__ or "")
    assert list(CANONICAL_HOST_FORMS) == list(_HOST_FORM_CLAIMS)
    for clause in CANONICAL_HOST_FORMS:
        assert clause in readme, clause
        assert clause in doc, clause


# --- the rule does not rest on the running Python's special-purpose table ----

_ORIGINAL_IS_GLOBAL = ipaddress.IPv6Address.is_global
_ORIGINAL_IS_LOOPBACK = ipaddress.IPv6Address.is_loopback
_TABLE_DEPENDENT = [
    ipaddress.ip_network("2002::/16"),
    ipaddress.ip_network("2001::/32"),
    ipaddress.ip_network("64:ff9b:1::/48"),
]


@pytest.fixture
def _python_calls_tunnel_prefixes_global(monkeypatch: pytest.MonkeyPatch) -> None:
    """Simulate an interpreter whose `ipaddress` table calls 6to4, Teredo and
    local-use NAT64 global (CPython's table has changed across releases)."""

    def is_global(self: ipaddress.IPv6Address) -> bool:
        if any(self in net for net in _TABLE_DEPENDENT):
            return True
        return bool(_ORIGINAL_IS_GLOBAL.fget(self))  # type: ignore[attr-defined]

    monkeypatch.setattr(ipaddress.IPv6Address, "is_global", property(is_global))


@pytest.mark.usefixtures("_python_calls_tunnel_prefixes_global")
@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("[2002:a00:5::1]", NOT_PUBLIC),  # 6to4 embedding 10.0.0.5
        ("[2002:808:808::1]", None),  # 6to4 embedding 8.8.8.8: the patch bites
        ("[2001:0:808:808::f5ff:fffa]", NOT_PUBLIC),  # Teredo client 10.0.0.5
        ("[2001:0:a00:5::f7f7:f7f7]", NOT_PUBLIC),  # Teredo server 10.0.0.5
        ("[2001:0:808:808::f7f7:f7f7]", None),  # Teredo, both 8.8.8.8
        ("[64:ff9b:1::808:808]", NOT_PUBLIC),  # local-use NAT64: refused whole
    ],
)
def test_tunnel_prefixes_are_classified_on_their_embedded_addresses(
    host: str, expected: str | None
) -> None:
    assert _outcome(lambda: sanitize_public_auth_url(f"https://{host}/x")) == expected


def test_operator_loopback_does_not_rest_on_is_loopback_for_mapped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Older CPython releases called `::ffff:127.0.0.1` not loopback; the
    operator's loopback set must not depend on that."""

    def is_loopback(self: ipaddress.IPv6Address) -> bool:
        if self.ipv4_mapped is not None:
            return False
        return bool(_ORIGINAL_IS_LOOPBACK.fget(self))  # type: ignore[attr-defined]

    monkeypatch.setattr(ipaddress.IPv6Address, "is_loopback", property(is_loopback))
    url = "http://[::ffff:127.0.0.1]/x"
    assert (
        _outcome(lambda: sanitize_public_auth_url(url, allow_loopback_http=True))
        is None
    )


def test_a_trailing_newline_or_tab_is_stripped_everywhere() -> None:
    """The round-2 codex seat's falsifier (F001), verbatim in substance: a
    file-backed secret's trailing newline, CR, CRLF or tab is dropped by the
    sanitiser, the startup check and `AsyncJWKS` alike."""
    clean = "https://auth.example.com/jwks.json"
    for suffix in ("\n", "\r", "\r\n", "\t"):
        check_auth_config(jwks_url=clean + suffix, metadata_url=clean + suffix)
        assert sanitize_public_auth_url(clean + suffix) == clean
        assert auth_mod.AsyncJWKS(clean + suffix).url == clean


@pytest.mark.parametrize(
    "host",
    [
        "fe80::5efe:8.8.8.8",
        "fd00::5efe:8.8.8.8",
        "ff02::5efe:8.8.8.8",
        "fe80::200:5efe:808:808",
    ],
)
def test_an_embedded_public_ipv4_does_not_vouch_for_a_non_public_ipv6(
    host: str,
) -> None:
    """The round-2 grok seat's F001: an ISATAP interface id carrying 8.8.8.8
    under a link-local, unique-local or multicast prefix is that IPv6 address
    to aiohttp, so it is refused."""
    url = f"https://[{host}]/jwks.json"
    fetched = _yarl_host(url)
    assert fetched is not None and not _is_public(fetched)
    assert _outcome(lambda: sanitize_public_auth_url(url)) == NOT_PUBLIC


def test_the_sanitiser_strips_a_leading_space_itself() -> None:
    """The round-3 claude seat's F001 falsifier on Consiliency/pmcp#346:
    pmcp strips a leading space itself, before `urlparse` sees the URL, so
    the README's "PMCP strips leading spaces" holds on CPython 3.10.0 to
    3.10.11 too, whose `urlsplit` does not strip it (CVE-2023-24329)."""
    seen: list[str] = []

    def spy(url: str, *args: Any, **kwargs: Any) -> Any:
        seen.append(url)
        return urlparse(url, *args, **kwargs)

    with patch.object(auth_mod, "urlparse", spy):
        auth_mod.sanitize_public_auth_url(" https://auth.example.com/jwks.json")
    assert seen and not seen[0].startswith(" ")


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("[2606:4700::5efe:a00:5]", NOT_PUBLIC),  # ISATAP-shaped id, 10.0.0.5
        ("[2606:4700::5efe:808:808]", None),  # ISATAP-shaped id, 8.8.8.8
        ("[2606:4700::200:5efe:808:808]", None),
    ],
)
def test_an_isatap_shaped_interface_id_under_a_global_prefix(
    host: str, expected: str | None
) -> None:
    """The round-3 seat's F002: the documented cost. ISATAP is recognised by
    its interface id, so a global address whose id is ISATAP-shaped and
    embeds a non-public IPv4 address is refused (as on main)."""
    assert _outcome(lambda: sanitize_public_auth_url(f"https://{host}/x")) == expected
