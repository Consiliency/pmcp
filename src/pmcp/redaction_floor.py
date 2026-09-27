"""Main's redaction rules as a floor (Consiliency/pmcp#234).

Four review rounds of the redactor rewrite each found inputs it redacted less
than `main` did, because the rewrite re-implemented main's rules and every
gap in a re-implementation is a leak. This module does not re-implement them:
it REPLAYS them. Main's substitutions run in main's order, each on the
previous one's output, exactly as `main`'s `sanitize_auth_diagnostic` and
`PolicyManager.redact_secrets` ran them, and every character of the
intermediate text carries the position of the input character it came from.
What main removed is then known exactly, as spans over the input: the floor.

The redactor (`pmcp.auth`) applies every floor span, plus its own additive
rules on top. A floor span is dropped only when a named suppression predicate
below says so (`SUPPRESSIONS`); each is decided from the match's own key,
separator and value, never from any output. So "never worse than main except
by a named suppression" holds by construction, and review reduces to the
predicates.

Two things are not a verbatim call of main's code, and both are held to it by
tests (`tests/test_redaction_floor.py` against `tests/_main_redactor.py`,
main's functions vendored verbatim): the intermediate text must equal what
main's functions return, byte for byte, at the end of the replay.

* Main's keyword rule is replayed by `main_keyword_matches`: main's own
  linear matcher (`pmcp.keyword_matcher`), which yields exactly the matches
  of the rule's regular expression, with main's frozen key set.
* Main's URL rewrite (`redact_auth_url`) re-encodes what it keeps; it is
  called for real, and the output is aligned with the input component by
  component (`_url_pieces`). If the alignment does not reproduce main's
  output exactly, the whole URL becomes one floor span.

This module is standalone: it imports nothing from the rest of `pmcp`, and
main's constants are frozen here, so a change to the live key sets can never
move the floor.
"""

from __future__ import annotations

import json
import re
from typing import Any
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, quote, unquote, urlparse, urlunparse

from pmcp.keyword_matcher import key_start_pattern, keys_alternation, keyword_matches

REDACTED = "[REDACTED]"

#: Test-only work counter (Consiliency/pmcp#234). None in production; the
#: complexity tests set it to `[0]` and every loop and string build of the
#: redactor adds what it iterates over or copies, so linear time is asserted
#: on a deterministic count, not on the clock.
WORK: list[int] | None = None


def work(amount: int) -> None:
    if WORK is not None:
        WORK[0] += amount


# --------------------------------------------------------------------------
# main's constants, frozen (origin/main 1fb36f2, src/pmcp/auth.py and
# src/pmcp/policy/policy.py).

MAIN_AUTH_SECRET_QUERY_KEYS = frozenset(
    {
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
    }
)

MAIN_DIAGNOSTIC_SECRET_KEYS = frozenset(
    {
        "access_token",
        "api_key",
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
    }
)

#: main's `DEFAULT_REDACTION_PATTERNS`, verbatim.
MAIN_DEFAULT_REDACTION_PATTERNS = (
    r"(api[_-]?key|apikey)[\s]*[:=][\s]*[\"']?([^\s\"']+)",
    r"(secret|password|passwd|pwd)[\s]*[:=][\s]*[\"']?([^\s\"']+)",
    r"(bearer|token)[\s]+[a-zA-Z0-9._-]+",
    r"(aws_secret|aws_access)[\s]*[:=][\s]*[\"']?([^\s\"']+)",
    r"\bsk-[A-Za-z0-9_-]{6,}\b",
    r"\bghp_[A-Za-z0-9_]{10,}\b",
    r"\bgithub_pat_[A-Za-z0-9_]{10,}\b",
)

_MAIN_URL_RE = re.compile(r"https?://[^\s\"'<>]+")
_MAIN_AUTHORIZATION_RE = re.compile(
    r"(?i)(authorization\s*[:=]\s*)(bearer\s+)?[^\s,;]+"
)
_MAIN_BEARER_RE = re.compile(r"(?i)(\bbearer\s+)[^\s,;]+")
_MAIN_JWT_RE = re.compile(
    r"(?<![A-Za-z0-9_-])"
    r"[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"
    r"(?![A-Za-z0-9_-])"
)


# --------------------------------------------------------------------------
# main's keyword rule: `pmcp.keyword_matcher`, as main runs it.


def _main_keys_alternation(keys: frozenset[str]) -> str:
    return keys_alternation(keys)


_KEY_START_RE = key_start_pattern(MAIN_DIAGNOSTIC_SECRET_KEYS)


def main_keyword_matches(text: str) -> Iterator[tuple[int, int, int, int]]:
    """Main's keyword rule's matches over ``text`` (see
    `pmcp.keyword_matcher.keyword_matches`), with main's frozen key set."""
    return keyword_matches(text, _KEY_START_RE)


# --------------------------------------------------------------------------
# the tracked replay


@dataclass
class FloorSpan:
    """One stretch of the input main removed, from one match of one rule.

    ``key``/``sep``/``value`` are the match's own groups as main's regex read
    them (on the intermediate text); ``before`` is the intermediate text just
    before the match, which a predicate may read to tell a JSON escape's tail
    from a glued prefix. ``part`` names which piece of main's removal this is
    (a policy default's replacement also takes the separator's whitespace and
    quote, and the `token`/`bearer` word itself: those are split off so a
    predicate can keep them without keeping the value)."""

    start: int
    end: int
    rule: str
    part: str = "value"
    key: str = ""
    sep: str = ""
    value: str = ""
    before: str = ""
    #: the intermediate text just after the match (at most 256 characters)
    after: str = ""
    #: the input text this part of the match removed (all its spans)
    removed: str = ""
    #: the input just before the match's key (where the key came from the
    #: input; None where main's own text produced it)
    raw_key_before: str | None = None
    #: the spans of one part of one match share a group: the predicates are
    #: asked once per group, on its first span (every span of it carries the
    #: same key, separator and value)
    group: int = 0
    raw_key_start: int = -1
    #: `key_readings`, computed once per decision
    readings_cache: list[KeyReading] | None = None
    replacement: str = REDACTED
    suppressed_by: str | None = None
    #: characters the JSON adjustment kept (syntax only; see `adjust_for_json`)
    json_kept: list[tuple[int, int]] = field(default_factory=list)


# A piece of one step's output: ("keep", a, b) copies cur[a:b] with its
# origins; ("new", text) is synthesised (("new", text, raw_a, raw_b): standing
# for that input range only); ("atom", text, raw_a, raw_b) is text
# derived from the whole raw range (a re-encoded URL component).
_Piece = tuple


class _Tracked:
    def __init__(self, text: str) -> None:
        self.raw = text
        self.cur = text
        #: per character of `cur`: an atom id (>= 0) or -1 (synthesised)
        self.org: list[int] = list(range(len(text)))
        #: per character of `cur`: what it stands for in the input -- its
        #: origin, or for a synthesised `[REDACTED]` the input it replaced.
        #: Read only to spell a value as the input wrote it (`source`); what
        #: was removed is decided by `org` alone.
        self.rep: list[int] = list(range(len(text)))
        self.atoms: list[tuple[int, int]] = []
        self.groups = 0
        self.step_covered_to = -1

    def atom_range(self, atom: int) -> tuple[int, int]:
        n = len(self.raw)
        return (atom, atom + 1) if atom < n else self.atoms[atom - n]

    def new_atom(self, a: int, b: int) -> int:
        self.atoms.append((a, b))
        return len(self.raw) + len(self.atoms) - 1

    def raw_ranges(self, a: int, b: int) -> list[tuple[int, int]]:
        """The input ranges the characters cur[a:b] came from, merged."""
        work((b - a) * max(1, (b - a).bit_length()))  # the slice, the sort
        ranges = sorted(self.atom_range(atom) for atom in self.org[a:b] if atom >= 0)
        merged: list[tuple[int, int]] = []
        for start, end in ranges:
            if merged and start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
            else:
                merged.append((start, end))
        return merged

    def source(self, a: int, b: int) -> str:
        """cur[a:b] as the input spelled it: a synthesised marker reads as
        the input text it replaced."""
        pieces: list[str] = []
        # Every character stands for input at or after its predecessor's
        # (`rewrite` keeps that order), and a step asks in match order, so the
        # input already spelled out for an earlier match of THIS step is not
        # copied again: those characters read as the intermediate text itself.
        # Without that, a many-character piece (a re-encoded URL component, a
        # marker standing for it) was copied whole once per match inside it:
        # k x n (B-3 and B-5 of revs 12 and 13). A step copies at most the
        # input once, plus its own matches' text.
        copied = 0
        earlier = self.step_covered_to  # spelled out by an earlier match
        for position in range(a, b):
            atom = self.rep[position]
            if atom < 0:
                continue
            start, end = self.atom_range(atom)
            if end <= earlier:
                pieces.append(self.cur[position])
                copied += 1
                continue
            if end <= self.step_covered_to:
                continue  # spelled out already by this match
            piece = self.raw[max(start, self.step_covered_to) : end]
            pieces.append(piece)
            copied += len(piece)
            self.step_covered_to = end
        work(b - a + copied)
        return "".join(pieces)

    def begin_step(self) -> None:
        """A new pass reads the text afresh: its matches may spell out any
        input again, once."""
        self.step_covered_to = -1

    def rewrite(self, edits: list[tuple[int, int, list[_Piece]]]) -> None:
        """Replace each cur[start:end] by its pieces (edits ascending,
        disjoint)."""
        work(len(self.cur) + sum(len(pieces) for _, _, pieces in edits))
        out: list[str] = []
        org: list[int] = []
        rep: list[int] = []
        position = 0
        for start, end, pieces in edits:
            out.append(self.cur[position:start])
            org.extend(self.org[position:start])
            rep.extend(self.rep[position:start])
            kept = bytearray(end - start)
            for piece in pieces:
                if piece[0] == "keep":
                    kept[piece[1] - start : piece[2] - start] = b"\x01" * (
                        piece[2] - piece[1]
                    )
            replaced = [
                self.atom_range(self.rep[i])
                for i in range(start, end)
                if not kept[i - start] and self.rep[i] >= 0
            ]
            stands_for = (
                self.new_atom(min(r[0] for r in replaced), max(r[1] for r in replaced))
                if replaced
                else -1
            )
            for piece in pieces:
                if piece[0] == "keep":
                    out.append(self.cur[piece[1] : piece[2]])
                    org.extend(self.org[piece[1] : piece[2]])
                    rep.extend(self.rep[piece[1] : piece[2]])
                elif piece[0] == "new":
                    # a marker stands for what it replaced: the input range
                    # the piece names (a URL's redacted value), nothing
                    # (`-1`, a URL's added `=`), or the edit's replaced text
                    marker = stands_for
                    if len(piece) == 4:
                        marker = (
                            self.new_atom(piece[2], piece[3])
                            if piece[2] < piece[3]
                            else -1
                        )
                    out.append(piece[1])
                    org.extend([-1] * len(piece[1]))
                    rep.extend([marker] * len(piece[1]))
                else:
                    atom = self.new_atom(piece[2], piece[3])
                    out.append(piece[1])
                    org.extend([atom] * len(piece[1]))
                    rep.extend([atom] * len(piece[1]))
            position = end
        out.append(self.cur[position:])
        org.extend(self.org[position:])
        rep.extend(self.rep[position:])
        self.cur = "".join(out)
        self.org = org
        self.rep = rep


#: What a predicate may read before a match: back to the last whitespace,
#: quote or angle bracket (a resource name or a glued prefix never crosses
#: one), at most 256 characters.
_CONTEXT_STOPS = frozenset(" \t\n\r\f\v\"'<>")


def _context(text: str, start: int) -> str:
    """The text before ``start`` back to the last whitespace, quote or angle
    bracket, at most 256 characters: one backward walk (a regex anchored at
    the end restarted at every position of the window)."""
    k = start
    limit = max(0, start - 256)
    while k > limit and text[k - 1] not in _CONTEXT_STOPS and not text[k - 1].isspace():
        k -= 1
    work(start - k + 1)
    return text[k:start]


def _counted(matches: Iterator[re.Match[str]]) -> Iterator[re.Match[str]]:
    """Each match of a pass, counted as the work of reading it."""
    for match in matches:
        work(match.end() - match.start() + 1)
        yield match


def _spans_for(
    tracked: _Tracked, a: int, b: int, rule: str, *, key_at: int = -1, **info: Any
) -> list[FloorSpan]:
    """The floor spans of cur[a:b], one part of one match whose key starts at
    cur[key_at] (-1: no key)."""
    tracked.groups += 1
    raw_key_start = -1
    if 0 <= key_at < len(tracked.org) and tracked.org[key_at] >= 0:
        raw_key_start = tracked.atom_range(tracked.org[key_at])[0]
    return [
        FloorSpan(
            start,
            end,
            rule,
            group=tracked.groups,
            raw_key_start=raw_key_start,
            **info,  # type: ignore[arg-type]
        )
        for start, end in tracked.raw_ranges(a, b)
    ]


# ---- the URL step


def _requoted(component: str) -> str:
    return quote(unquote(component.replace("+", " ")))


def _url_pieces(
    raw: str, base: int
) -> tuple[list[_Piece], list[tuple[int, int, str]]] | None:
    """Main's `redact_auth_url(raw)` as pieces over the text (``base`` is the
    URL's offset), plus the labelled input ranges it drops. None when the
    alignment does not reproduce main's output exactly."""
    expected = main_redact_auth_url(raw)
    work(4 * len(raw))  # parse, rewrite, align, compare
    pieces: list[_Piece] = []
    dropped: list[tuple[int, int, str]] = []
    try:
        parsed = urlparse(raw)
        port = parsed.port
    except ValueError:
        head_end = min(len(raw.split("#", 1)[0]), 400)
        pieces.append(("keep", base, base + head_end))
        fragment = raw.find("#")
        if fragment < 0 or fragment > head_end:
            if head_end < len(raw):
                stop = fragment if fragment >= 0 else len(raw)
                dropped.append((base + head_end, base + stop, "url.overflow"))
                if fragment >= 0:
                    dropped.append((base + fragment, base + len(raw), "url.fragment"))
        else:
            dropped.append((base + fragment, base + len(raw), "url.fragment"))
        return (pieces, dropped) if raw[:head_end] == expected else None

    netloc_start = raw.index("://") + 3
    netloc_end = len(raw)
    for delimiter in "/?#":
        index = raw.find(delimiter, netloc_start)
        if index >= 0:
            netloc_end = min(netloc_end, index)
    pieces.append(("keep", base, base + netloc_start))
    at = raw.rfind("@", netloc_start, netloc_end)
    host_start = at + 1 if at >= 0 else netloc_start
    if at >= 0:
        dropped.append((base + netloc_start, base + host_start, "url.userinfo"))
    netloc = parsed.hostname or ""
    if ":" in netloc and not netloc.startswith("["):
        netloc = f"[{netloc}]"
    if port:
        netloc = f"{netloc}:{port}"
    raw_host = raw[host_start:netloc_end]
    if netloc == raw_host or (
        len(netloc) == len(raw_host)
        and all(o == r or o == r.lower() for o, r in zip(netloc, raw_host))
    ):
        pieces.append(("keep", base + host_start, base + netloc_end))
    elif netloc:
        pieces.append(("atom", netloc, base + host_start, base + netloc_end))
    elif raw_host:
        dropped.append((base + host_start, base + netloc_end, "url.normal"))

    fragment = raw.find("#", netloc_end)
    rest_end = fragment if fragment >= 0 else len(raw)
    query_mark = raw.find("?", netloc_end, rest_end)
    path_end = query_mark if query_mark >= 0 else rest_end
    path = parsed.path + (f";{parsed.params}" if parsed.params else "")
    raw_path = raw[netloc_end:path_end]
    if raw_path.startswith(path):
        pieces.append(("keep", base + netloc_end, base + netloc_end + len(path)))
        if len(path) < len(raw_path):
            dropped.append(
                (base + netloc_end + len(path), base + path_end, "url.normal")
            )
    else:
        return None

    if query_mark >= 0:
        pairs: list[list[_Piece]] = []
        position = query_mark + 1
        for pair in raw[query_mark + 1 : rest_end].split("&"):
            pair_start = position
            position += len(pair) + 1
            if not pair:
                continue  # an empty pair: its `&` is labelled below
            key, equals, value = pair.partition("=")
            key_start = pair_start
            value_start = key_start + len(key) + len(equals)
            pair_pieces: list[_Piece] = []
            if _requoted(key) == key:
                pair_pieces.append(
                    ("keep", base + key_start, base + key_start + len(key))
                )
            else:
                pair_pieces.append(
                    (
                        "atom",
                        _requoted(key),
                        base + key_start,
                        base + key_start + len(key),
                    )
                )
            if equals:
                pair_pieces.append(
                    ("keep", base + key_start + len(key), base + value_start)
                )
            else:
                pair_pieces.append(("new", "=", -1, -1))
            decoded_key = unquote(key.replace("+", " "))
            if decoded_key.lower() in MAIN_AUTH_SECRET_QUERY_KEYS:
                pair_pieces.append(
                    (
                        "new",
                        quote(REDACTED),
                        base + value_start,
                        base + value_start + len(value),
                    )
                )
                if value:
                    dropped.append(
                        (
                            base + value_start,
                            base + value_start + len(value),
                            "url.query",
                        )
                    )
            elif _requoted(value) == value:
                pair_pieces.append(
                    ("keep", base + value_start, base + value_start + len(value))
                )
            else:
                pair_pieces.append(
                    (
                        "atom",
                        _requoted(value),
                        base + value_start,
                        base + value_start + len(value),
                    )
                )
            if pairs:
                # the `&` before this pair, as written
                pair_pieces.insert(
                    0, ("keep", base + pair_start - 1, base + pair_start)
                )
            pairs.append(pair_pieces)
        if pairs:
            pieces.append(("keep", base + query_mark, base + query_mark + 1))
            for pair_pieces in pairs:
                pieces.extend(pair_pieces)
        else:
            dropped.append((base + query_mark, base + query_mark + 1, "url.normal"))
    if fragment >= 0:
        dropped.append((base + fragment, base + len(raw), "url.fragment"))

    rebuilt = "".join(
        piece[1]
        if piece[0] in ("new", "atom")
        else raw[piece[1] - base : piece[2] - base]
        for piece in pieces
    )
    if rebuilt != expected:
        return None
    _label_uncovered(raw, base, pieces, dropped)
    return pieces, dropped


def _label_uncovered(
    raw: str, base: int, pieces: list[_Piece], dropped: list[tuple[int, int, str]]
) -> None:
    """Every input position is either kept by a piece or dropped by a label:
    whatever no piece keeps and no label names (the `&` of an empty, doubled
    or trailing pair) is labelled `url.normal` here."""
    work(2 * len(raw) + len(pieces) + len(dropped))
    covered = bytearray(len(raw))
    for piece in pieces:
        if piece[0] == "keep":
            covered[piece[1] - base : piece[2] - base] = b"\x01" * (piece[2] - piece[1])
        elif piece[0] == "atom":
            covered[piece[2] - base : piece[3] - base] = b"\x01" * (piece[3] - piece[2])
    for start, stop, _ in dropped:
        covered[start - base : stop - base] = b"\x01" * (stop - start)
    position = 0
    while position < len(raw):
        if covered[position]:
            position += 1
            continue
        stop = position
        while stop < len(raw) and not covered[stop]:
            stop += 1
        dropped.append((base + position, base + stop, "url.normal"))
        position = stop


def main_redact_auth_url(url: str) -> str:
    """main's `redact_auth_url`, verbatim but for main's frozen key set."""
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError:
        return str(url).split("#", 1)[0][:400]
    netloc = parsed.hostname or ""
    if ":" in netloc and not netloc.startswith("["):
        netloc = f"[{netloc}]"
    if port:
        netloc = f"{netloc}:{port}"
    query_parts = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        if key.lower() in MAIN_AUTH_SECRET_QUERY_KEYS:
            query_parts.append((key, "[REDACTED]"))
        else:
            query_parts.append((key, value))
    query = "&".join(f"{quote(k)}={quote(v)}" for k, v in query_parts)
    return urlunparse((parsed.scheme, netloc, parsed.path, parsed.params, query, ""))


def _url_step(tracked: _Tracked, spans: list[FloorSpan]) -> None:
    # The URL step is the first: cur is the input and every origin is itself.
    text = tracked.cur
    work(len(text))
    edits: list[tuple[int, int, list[_Piece]]] = []
    for match in _counted(_MAIN_URL_RE.finditer(text)):
        # main's `redact_url_match`: trailing sentence punctuation is handed
        # back, stripped in one pass
        raw = match.group(0).rstrip(").,;")
        start = match.start()
        end = start + len(raw)
        result = _url_pieces(raw, start)
        if result is None:
            edits.append((start, end, [("new", main_redact_auth_url(raw))]))
            spans.append(FloorSpan(start, end, "url.fallback", value=raw))
            continue
        pieces, dropped = result
        edits.append((start, end, pieces))
        for a, b, label in dropped:
            if a < b:
                spans.append(
                    FloorSpan(
                        a,
                        b,
                        label,
                        value=text[a:b],
                        before=_context(text, a),
                        replacement=REDACTED if label == "url.query" else "",
                    )
                )
    tracked.rewrite(edits)


# ---- the regex steps


#: A value's wrapping as main's Bearer and Authorization values took it with
#: the value: opening quotes and brackets before it, closing ones (an escaped
#: quote included) after it. Split off so `wrapper_syntax` can keep them.
_WRAP_OPEN_RE = re.compile(r"[\"'(\[{<]+")
_WRAP_CLOSERS = frozenset("\"')]}>")


def _wrap_close_start(text: str, start: int, end: int) -> int:
    """Where the closing wrapping of text[start:end] begins -- one or more
    closing quotes or brackets, each optionally escaped, then sentence
    punctuation, running to ``end`` -- or -1. One right-to-left scan (a
    search anchored at the end restarts at every closer)."""
    k = end
    while k > start and text[k - 1] in ".:!?":
        k -= 1
    found = -1
    while k > start and text[k - 1] in _WRAP_CLOSERS:
        k -= 1
        if k > start and text[k - 1] == "\\":
            k -= 1
        found = k
    work(end - k + 1)
    return found


def _header_value_spans(
    tracked: _Tracked, a: int, end: int, rule: str, scheme_end: int, **info: Any
) -> list[FloorSpan]:
    """Floor spans of one Bearer/Authorization value cur[a:end]: the scheme
    word main took with it (`Authorization: Bearer x` lost `Bearer `), the
    wrapping, and the value itself."""
    cur = tracked.cur
    opening = _WRAP_OPEN_RE.match(cur, scheme_end, end)
    value_start = opening.end() if opening is not None else scheme_end
    closing = _wrap_close_start(cur, value_start, end)
    value_end = closing if closing >= 0 else end
    if value_end <= value_start:
        value_start, value_end = scheme_end, end  # nothing but wrapping: one value
    value = tracked.source(value_start, value_end)
    spans: list[FloorSpan] = []
    for start, stop, part in (
        (a, scheme_end, "scheme"),
        (scheme_end, value_start, "wrap_open"),
        (value_start, value_end, "value"),
        (value_end, end, "wrap_close"),
    ):
        if start < stop:
            spans.extend(
                _spans_for(tracked, start, stop, rule, part=part, value=value, **info)
            )
    return spans


def _authorization_step(tracked: _Tracked, spans: list[FloorSpan]) -> None:
    cur = tracked.cur
    work(len(cur))  # the pass's scan
    edits = []
    for m in _counted(_MAIN_AUTHORIZATION_RE.finditer(cur)):
        a = m.end(1)
        edits.append((m.start(), m.end(), [("keep", m.start(), a), ("new", REDACTED)]))
        scheme = m.group(2) or ""
        spans.extend(
            _header_value_spans(
                tracked,
                a,
                m.end(),
                "authorization",
                a + len(scheme),
                key=m.group(1),
                sep=scheme,
                before=_context(cur, m.start()),
                after=cur[m.end() : m.end() + 256],
            )
        )
    tracked.rewrite(edits)


def _bearer_step(tracked: _Tracked, spans: list[FloorSpan]) -> None:
    cur = tracked.cur
    work(len(cur))  # the pass's scan
    edits = []
    for m in _counted(_MAIN_BEARER_RE.finditer(cur)):
        a = m.end(1)
        edits.append((m.start(), m.end(), [("keep", m.start(), a), ("new", REDACTED)]))
        spans.extend(
            _header_value_spans(
                tracked,
                a,
                m.end(),
                "bearer",
                a,
                key=m.group(1).rstrip(),
                sep=m.group(1)[len(m.group(1).rstrip()) :],
                before=_context(cur, m.start()),
                after=cur[m.end() : m.end() + 256],
            )
        )
    tracked.rewrite(edits)


def _keyword_step(tracked: _Tracked, spans: list[FloorSpan]) -> None:
    cur = tracked.cur
    edits = []
    work(len(cur))  # the matcher's scan
    for start, key_end, value_start, end in main_keyword_matches(cur):
        work(end - start + 1)
        edits.append((start, end, [("keep", start, value_start), ("new", REDACTED)]))
        spans.extend(
            _spans_for(
                tracked,
                value_start,
                end,
                "keyword",
                key_at=start,
                key=cur[start:key_end],
                sep=cur[key_end:value_start],
                value=tracked.source(value_start, end),
                before=_context(cur, start),
                after=cur[end : end + 256],
            )
        )
    tracked.rewrite(edits)


def _jwt_step(tracked: _Tracked, spans: list[FloorSpan]) -> None:
    cur = tracked.cur
    work(len(cur))  # the pass's scan
    edits = []
    for m in _counted(_MAIN_JWT_RE.finditer(cur)):
        edits.append((m.start(), m.end(), [("new", REDACTED)]))
        spans.extend(
            _spans_for(
                tracked,
                m.start(),
                m.end(),
                "jwt",
                value=m.group(0),
                before=_context(cur, m.start()),
            )
        )
    tracked.rewrite(edits)


#: A separator's syntax as main's policy replacement takes it with the value:
#: the whitespace, operator characters and opening quote after the first
#: `:`/`=`.
_POLICY_SYNTAX_RE = re.compile(r"[\s:=\"']*")
_POLICY_KEYWORD_RE = re.compile(r"(?i)(?:bearer|token)\s+")


def _policy_step(
    tracked: _Tracked, spans: list[FloorSpan], regex: re.Pattern[str], index: int
) -> None:
    cur = tracked.cur
    work(len(cur))  # the pass's scan
    edits = []
    default = regex.pattern in MAIN_DEFAULT_REDACTION_PATTERNS
    rule = (
        f"policy:{MAIN_DEFAULT_REDACTION_PATTERNS.index(regex.pattern)}"
        if default
        else f"policy:custom{index}"
    )
    for m in _counted(regex.finditer(cur)):
        full = m.group(0)
        separator = next((i for i, char in enumerate(full) if char in ":="), -1)
        info: dict[str, Any] = {
            "before": _context(cur, m.start()),
            "after": cur[m.end() : m.end() + 256],
        }
        if separator >= 0:
            a = m.start() + separator + 1
            edits.append(
                (m.start(), m.end(), [("keep", m.start(), a), ("new", " " + REDACTED)])
            )
            syntax_end = _POLICY_SYNTAX_RE.match(cur, a, m.end()).end()  # type: ignore[union-attr]
            key, sep = (
                cur[m.start() : m.start() + separator],
                cur[m.start() + separator : syntax_end],
            )
            value = tracked.source(syntax_end, m.end())
            spans.extend(
                _spans_for(
                    tracked,
                    a,
                    syntax_end,
                    rule,
                    key_at=m.start(),
                    part="syntax",
                    key=key,
                    sep=sep,
                    value=value,
                    **info,
                )
            )
            spans.extend(
                _spans_for(
                    tracked,
                    syntax_end,
                    m.end(),
                    rule,
                    key_at=m.start(),
                    key=key,
                    sep=sep,
                    value=value,
                    **info,
                )
            )
        else:
            edits.append((m.start(), m.end(), [("new", REDACTED)]))
            word = (
                _POLICY_KEYWORD_RE.match(cur, m.start(), m.end())
                if rule == "policy:2"
                else None
            )
            split = word.end() if word is not None else m.start()
            key, sep = (
                (
                    cur[m.start() : split].rstrip(),
                    cur[m.start() : split][len(cur[m.start() : split].rstrip()) :],
                )
                if word
                else ("", "")
            )
            value = tracked.source(split, m.end())
            spans.extend(
                _spans_for(
                    tracked,
                    m.start(),
                    split,
                    rule,
                    part="keyword",
                    key=key,
                    sep=sep,
                    value=value,
                    **info,
                )
            )
            spans.extend(
                _spans_for(
                    tracked, split, m.end(), rule, key=key, sep=sep, value=value, **info
                )
            )
    tracked.rewrite(edits)


def replay(
    text: str, patterns: Sequence[re.Pattern[str]] | None = None
) -> tuple[str, list[FloorSpan]]:
    """Main's redaction of ``text``, replayed: main's output text, and what it
    removed as spans over ``text``. ``patterns`` None is main's
    `sanitize_auth_diagnostic(text, max_length=None)`; a list is main's
    `PolicyManager.redact_secrets` with those effective compiled patterns."""
    tracked = _Tracked(text)
    spans: list[FloorSpan] = []
    for step in (
        _url_step,
        _authorization_step,
        _bearer_step,
        _keyword_step,
        _jwt_step,
    ):
        tracked.begin_step()
        step(tracked, spans)
    for index, regex in enumerate(patterns or ()):
        tracked.begin_step()
        _policy_step(tracked, spans, regex, index)
    return tracked.cur, spans


# --------------------------------------------------------------------------
# the JSON adjustment
#
# Main's Bearer and Authorization values run to whitespace, `,` or `;`, so in
# a serialised document they ate the closing quote and bracket
# (`"Bearer x"}` -> `"Bearer [REDACTED]`), and a dict result came back as a
# string. When the text is a JSON document, a floor span is applied inside the
# string it starts in instead: the characters main removed outside string
# interiors are JSON syntax (a delimiting quote, `{}[],:`, whitespace), and
# are kept; a bare scalar main's span touched is replaced whole by the string
# `"[REDACTED]"`; and a span never starts or ends inside an escape: it is
# widened to the whole escape, which only ever removes more -- except that a
# span ending on an escape's backslash (main took `\\` and left the `"` it
# escaped) leaves the escape whole: the backslash was syntax, and main kept
# the character.

_JSON_STRING_RE = re.compile(r"\"(?:[^\"\\\n]|\\.)*\"")
_JSON_ESCAPE_TOKEN_RE = re.compile(r"\\u[0-9a-fA-F]{4}|\\.|[^\\]", re.DOTALL)
_JSON_SCALAR_TOKEN_RE = re.compile(
    r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?|true|false|null|NaN|-?Infinity"
)
#: What the adjustment may keep of a floor span: syntax, never content.
JSON_SYNTAX = frozenset(' \t\r\n"{}[],:')


class _JsonLayout:
    def __init__(self, text: str) -> None:
        self.text = text
        work(2 * len(text))
        #: (start, end) of every string token, quotes included
        self.strings = [m.span() for m in _JSON_STRING_RE.finditer(text)]
        self._starts = [start for start, _ in self.strings]
        #: interior positions where an escape token starts (or a plain char)
        self.token_start = bytearray(len(text) + 1)
        for start, end in self.strings:
            for token in _JSON_ESCAPE_TOKEN_RE.finditer(text, start + 1, end - 1):
                self.token_start[token.start()] = 1
            self.token_start[end - 1] = 1

    def string_at(self, index: int) -> tuple[int, int] | None:
        work(max(1, len(self._starts).bit_length()))
        import bisect

        i = bisect.bisect_right(self._starts, index) - 1
        if i >= 0 and self.strings[i][0] <= index < self.strings[i][1]:
            return self.strings[i]
        return None


def json_layout(text: str) -> _JsonLayout | None:
    """The layout of ``text`` when it is a JSON document, else None."""
    work(len(text))
    head = text.lstrip()[:1]
    if head not in ("{", "[", '"'):
        return None
    try:
        json.loads(text)
    except (ValueError, RecursionError):
        return None
    return _JsonLayout(text)


def adjust_for_json(layout: _JsonLayout, span: FloorSpan) -> list[tuple[int, int, str]]:
    """The applied form of one floor span over a JSON document (see above).
    Records what it kept in ``span.json_kept``."""
    text = layout.text
    applied: list[tuple[int, int, str]] = []
    position = span.start
    while position < span.end:
        work(1)
        string = layout.string_at(position)
        if string is not None and string[0] < position < string[1] - 1:
            stop = min(span.end, string[1] - 1)
            start, end = position, stop
            while not layout.token_start[start]:
                work(1)
                start -= 1
            if (
                end - 1 >= start
                and text[end - 1] == "\\"
                and layout.token_start[end - 1]
            ):
                # main took an escape's backslash and left what it escapes:
                # the backslash is syntax, and the escaped character main kept
                span.json_kept.append((end - 1, end))
                end -= 1
            while not layout.token_start[end]:
                work(1)
                end += 1
            applied.append((start, end, span.replacement))
            position = stop
            continue
        if string is not None:
            # a delimiting quote
            span.json_kept.append((position, position + 1))
            position += 1
            continue
        if text[position] not in JSON_SYNTAX:
            # a bare scalar (outside strings nothing else is not syntax)
            back = position
            while back > 0 and text[back - 1] not in JSON_SYNTAX:
                work(1)
                back -= 1
            scalar = _JSON_SCALAR_TOKEN_RE.match(text, back)
        else:
            scalar = None
        if scalar is not None:
            applied.append((scalar.start(), scalar.end(), f'"{REDACTED}"'))
            position = scalar.end()
            continue
        span.json_kept.append((position, position + 1))
        position += 1
    return applied


# --------------------------------------------------------------------------
# the suppression predicates

Predicate = Callable[[FloorSpan], bool]

#: name -> predicate. Filled below; the order is the order they are asked in,
#: and the first that fires is recorded on the span.
SUPPRESSIONS: dict[str, Predicate] = {}

#: Qualifiers under which `code` names a status or a description, not a
#: credential (`status_code=401`, `sqlstate_code=42P01`), matched against
#: the qualifier's last `_`/`-` segment.
DIAGNOSTIC_CODE_QUALIFIERS = frozenset(
    {
        "area", "byte", "char", "color", "colour", "country", "currency",
        "err", "errno", "error", "event", "exception", "exit", "fault", "http",
        "iso", "item", "lang", "language", "locale", "op", "opcode", "postal",
        "product", "rc", "reason", "region", "response", "result", "ret",
        "return", "sku", "source", "sqlstate", "state", "status", "zip",
    }
)  # fmt: skip
#: Qualifiers under which a bare `code` is OAuth's (`auth_code=`,
#: `device_code=`), where only a plain word or number is kept.
OAUTH_CODE_QUALIFIERS = frozenset(
    {"", "auth", "authorization", "oauth", "device", "user"}
)
#: Keys that are one declared name, not a key word plus a descriptive suffix.
DECLARED_KEYS = frozenset(
    {
        "access_token", "api_key", "apikey", "api-key", "assertion", "auth",
        "aws_access", "aws_secret", "client_secret", "code", "cookie",
        "credential", "credentials", "id_token", "jwt", "passwd", "password",
        "private_key", "privatekey", "private-key", "pwd", "refresh_token",
        "saml", "secret", "secret_access_key", "session", "set-cookie", "sid",
        "tenant-id", "tenant_id", "token",
    }
)  # fmt: skip

_PLAIN_WORD_RE = re.compile(r"[A-Za-z_]+")
#: A plain number: a short decimal (a status, an exit code, a JSON-RPC
#: error: at most 10 digits). A longer digit run or any `0x` hex is not
#: plain: it may be a numeric token or a hex key, and main's redaction of it
#: stands.
_PLAIN_NUMBER_RE = re.compile(r"[+-]?[0-9]{1,10}")


def is_plain(value: str) -> bool:
    """A word (`bucket`, `invalid_grant`) or a number (`401`, `-32601`,
    `0x80070005`), quotes and sentence punctuation after it allowed."""
    value = value.strip("\"'").rstrip(".:!?")
    return bool(_PLAIN_WORD_RE.fullmatch(value) or _PLAIN_NUMBER_RE.fullmatch(value))


def looks_like_credential(value: str) -> bool:
    """D2 of #234: not a plain word or number, and digit-bearing and 6+
    characters or punctuated and 8+ (`hunter2`, `abc123def456`,
    `correct-horse-battery`; not `v2`, `re-use`, `expired`)."""
    value = value.strip("\"'")
    if is_plain(value):
        return False
    return len(value) >= (6 if any(c.isdigit() for c in value) else 8)


@dataclass(frozen=True)
class KeyReading:
    """One way to read a match's key: `qualifier` (everything before the key
    word, glued prefix included), `glued` (its trailing alphanumeric run: no
    joiner before the key word), `name` (the key word), `suffix`."""

    qualifier: str
    glued: str
    name: str
    suffix: str


_KEY_WORD_RE = re.compile(
    rf"(?i)(?=({_main_keys_alternation(MAIN_DIAGNOSTIC_SECRET_KEYS | {'passwd', 'pwd', 'aws_secret', 'aws_access', 'bearer', 'authorization'})}))"
)
_ESCAPE_TAIL_RE = re.compile(r"(?:[nrtbf]|u[0-9a-fA-F]{4})")


#: A key longer than this is read no way at all, so every predicate that
#: reads the key declines and main's redaction stands: a run of 11 000
#: `token-` has 11 000 readings, and reading each is quadratic.
_MAX_READ_KEY = 256


_ALNUM = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")


def _glued_tail(text: str) -> str:
    """The run of ASCII letters and digits ``text`` ends with: one backward
    walk (a regex anchored at the end restarted at every position)."""
    k = len(text)
    while k > 0 and text[k - 1] in _ALNUM:
        k -= 1
    work(len(text) - k + 1)
    return text[k:]


def key_readings(span: FloorSpan) -> list[KeyReading]:
    """Every reading of the span's key: each key word in it, and -- when the
    key starts on the tail of a JSON escape (`\\npassword`) -- with and
    without that tail. A predicate that reads the key fires only if it fires
    on EVERY reading, so an ambiguous key is never read the lenient way."""
    if span.readings_cache is not None:
        return span.readings_cache
    key = span.key.rstrip()
    before = span.before
    if len(key) > _MAX_READ_KEY:
        return []  # no reading: no key-reading predicate fires (a cost bound)
    variants = [(_glued_tail(before), key)]
    if (len(before) - len(before.rstrip("\\"))) % 2 == 1:
        tail = _ESCAPE_TAIL_RE.match(key)
        if tail is not None:
            variants.append(("", key[tail.end() :]))
    readings = []
    for context_glue, text in variants:
        for word in _KEY_WORD_RE.finditer(text):
            name = word.group(1)
            qualifier = text[: word.start()]
            glued = _glued_tail(qualifier) if qualifier else context_glue
            readings.append(
                KeyReading(qualifier, glued, name, text[word.start() + len(name) :])
            )
    span.readings_cache = readings
    return readings


def _declared(name: str, suffix: str) -> bool:
    if (name + suffix).lower() in DECLARED_KEYS:
        return True
    return (
        re.fullmatch(r"[_-]?(?:id|key)|s", suffix.lower()) is not None
        and name.lower() in DECLARED_KEYS
    )


def _sep_and_value(span: FloorSpan) -> tuple[str, str]:
    """The separator and value as a reader sees them: an operator run main's
    value began with (`token==x` backtracks `=` into the value) is part of
    the separator."""
    value = _unescaped(span.value)
    lead = re.match(r"[:=]*", value).group(0)  # type: ignore[union-attr]
    if lead and value[len(lead) :]:
        return span.sep + lead, value[len(lead) :]
    return span.sep, value


def _unescaped(value: str) -> str:
    """A value read in a serialised leaf, with its escaped quotes read as the
    quotes they are (`\\"type` is `"type`)."""
    return value.replace('\\"', '"').replace("\\'", "'")


_KEYED_RULES = frozenset({"keyword", "policy:0", "policy:1", "policy:3"})


def _for_every_reading(span: FloorSpan, test: Callable[[KeyReading], bool]) -> bool:
    readings = key_readings(span)
    return bool(readings) and all(test(reading) for reading in readings)


def suppression(name: str) -> Callable[[Predicate], Predicate]:
    def register(predicate: Predicate) -> Predicate:
        SUPPRESSIONS[name] = predicate
        return predicate

    return register


def _decision_key(span: FloorSpan) -> int:
    """The predicates are asked once per part of a match (its group): one
    match can split into many spans around main's own markers, and asking
    each would rescan the shared value once per span (B-1c of rev 11's
    board)."""
    return span.group


#: The parts of a match whose removed text a predicate reads.
_SYNTAX_PARTS = frozenset({"syntax", "keyword", "scheme", "wrap_open", "wrap_close"})


def floor_spans(
    text: str, patterns: Sequence[re.Pattern[str]] | None = None
) -> tuple[list[FloorSpan], list[tuple[int, int, str]]]:
    """Every span main removed from ``text`` (``suppressed_by`` set on those
    a named predicate drops), and the spans the redactor must apply for the
    rest (adjusted for JSON when ``text`` is a JSON document)."""
    _, spans = replay(text, patterns)
    layout = json_layout(text) if spans else None
    applied: list[tuple[int, int, str]] = []
    work(len(text) + len(spans))
    # What a part of a match removed is read only by the syntax predicates,
    # and only for the syntax parts (a separator's whitespace and quote, a
    # scheme word, a wrapper): those are bounded by the match. A value part
    # can be one of many spans over the same many-character input (every
    # match inside one re-encoded URL component spans all of it), so its
    # text is never copied here (B-5 of rev 13's board).
    removed: dict[int, list[str]] = {}
    for span in spans:
        if span.part in _SYNTAX_PARTS:
            piece = text[span.start : span.end]
            work(len(piece))
            removed.setdefault(span.group, []).append(piece)
    joined = {group: "".join(parts) for group, parts in removed.items()}
    decided: dict[int, str | None] = {}
    applied_once: set[tuple[int, int, str]] = set()
    for span in spans:
        # what the whole part of the match removed, shared by its spans
        span.removed = joined.get(span.group, "")
        key = _decision_key(span)
        if key not in decided:
            # each predicate reads the match's own strings a bounded number of
            # times (key readings are bounded by `_MAX_READ_KEY`)
            work(
                len(SUPPRESSIONS)
                * (
                    len(span.key)
                    + len(span.sep)
                    + len(span.value)
                    + len(span.removed)
                    + len(span.before)
                    + len(span.after)
                    + 256
                )
            )
            if span.raw_key_start >= 0:
                span.raw_key_before = _context(text, span.raw_key_start)
            decided[key] = next(
                (name for name, predicate in SUPPRESSIONS.items() if predicate(span)),
                None,
            )
        span.suppressed_by = decided[key]
        if span.suppressed_by is not None:
            continue
        # one application per input range: many matches inside one
        # re-encoded component each span all of it
        if (span.start, span.end, span.replacement) in applied_once:
            continue
        applied_once.add((span.start, span.end, span.replacement))
        if layout is not None:
            applied.extend(adjust_for_json(layout, span))
        else:
            applied.append((span.start, span.end, span.replacement))
    return spans, applied


# The predicates. Each names the stated class it implements (the classes of
# `tests/_redaction_grammar.py`, approved by the maintainer) and the false
# positive it exists for; `tests/test_redaction_floor.py` holds, for each,
# the case it fires on and a credential-shaped case it does not fire on.


@suppression("separator_syntax")
def _separator_syntax(span: FloorSpan) -> bool:
    """A policy replacement (`key: [REDACTED]`) also took the whitespace, the
    operator characters and the opening quote between the separator and the
    value (`password: "x"` lost ` "`). Those are syntax, never content: the
    value itself is a separate floor span. Keeping them is what keeps
    `password: "[REDACTED]"` quoted."""
    return span.part == "syntax" and all(
        c.isspace() or c in ":=\"'" for c in span.removed
    )


@suppression("policy_keyword_word")
def _policy_keyword_word(span: FloorSpan) -> bool:
    """Main's `(bearer|token)\\s+value` default replaced the keyword too
    (`token abc` became `[REDACTED]`). The word itself is the pattern's own
    literal vocabulary, never content; the value is a separate floor span."""
    return span.part == "keyword" and span.removed.strip().lower() in (
        "token",
        "bearer",
    )


_SCHEME_WORD_RE = re.compile(r"(?i)bearer\s+")


@suppression("scheme_word")
def _scheme_word(span: FloorSpan) -> bool:
    """`Authorization: Bearer x` lost the scheme word with the value on main.
    The word is main's own pattern's literal (`bearer` and whitespace), never
    content; the value is a separate floor span."""
    return span.part == "scheme" and _SCHEME_WORD_RE.fullmatch(span.removed) is not None


_WRAPPER_CHARS = frozenset("\"'()[]{}<>\\")


@suppression("wrapper_syntax")
def _wrapper_syntax(span: FloorSpan) -> bool:
    """Main's Bearer and Authorization values ran to whitespace, `,` or `;`,
    so they took the quote or bracket wrapped around the token (`Bearer
    "x"`, `Authorization: [x]`) and the one closing the string or object the
    header sat in (`{'h': 'Bearer x'}`). Quotes, brackets and an escaping
    backslash at the value's edges are syntax, never content: the value
    between them is a separate floor span; so is sentence punctuation after a
    closing one (`"Bearer api"}.`). A quote INSIDE the value
    (`Bearer abc"SECRETPART`) is not at an edge, and goes with it."""
    return span.part in ("wrap_open", "wrap_close") and all(
        c in _WRAPPER_CHARS or (span.part == "wrap_close" and c in ".:!?")
        for c in span.removed
    )


_GLUED_SUFFIX_RE = re.compile(r"[A-Za-z0-9]*")


def _case(char: str) -> int:
    return 1 if char.isupper() else 2 if char.islower() else 0


def _same_case_run(text: str, *, from_end: bool, neighbour: str) -> int:
    """How long the run of ``text`` next to the key word is before a case
    change (`production` + `Password`, `PRODUCTION` + `password`, `Server` in
    `passwordForServer`): a case change joins words as `_` and `-` do (N-5
    of rev 12's board: camelCase and SHOUTING multi-word keys). Digits
    continue a run. ``neighbour`` is the key word's letter on that side."""
    chars = reversed(text) if from_end else iter(text)
    previous = _case(neighbour)
    length = 0
    for char in chars:
        case = _case(char)
        if case and previous and case != previous:
            break
        if case:
            previous = case
        length += 1
    return length


@suppression("N3")
def _n3(span: FloorSpan) -> bool:
    """A prefix or suffix of more than 24 alphanumerics GLUED to the key word
    -- no `_`/`-` and no case change between them and the key word
    (`<25 letters>password=`, `password<25 letters>=`) -- is an identifier,
    not a key (the additive rules' cost bound). Joined words are not glued:
    `DATABASE_PASSWORD_FOR_REPLICATION_USER_ACCOUNT=`,
    `passwordForTheProductionDatabaseServer=` and
    `PRODUCTIONDATABASEREPLICATIONpassword=` are keys."""

    def glued_too_long(r: KeyReading) -> bool:
        prefix = _same_case_run(r.glued, from_end=True, neighbour=r.name[:1])
        suffix_run = _GLUED_SUFFIX_RE.match(r.suffix).group(0)  # type: ignore[union-attr]
        suffix = _same_case_run(suffix_run, from_end=False, neighbour=r.name[-1:])
        return prefix > 24 or suffix > 24

    return span.rule in _KEYED_RULES and _for_every_reading(span, glued_too_long)


#: The text before a key that is itself a segment of an `arn:`/`urn:`
#: resource name: the name's start, then only name characters -- no
#: whitespace, quote, bracket or pair/list delimiter (`& , ; ( ) ? =`) -- and
#: the key right after a `:` or `/` segment boundary.
_NAME_DELIMITER_RE = re.compile(r"[\s\"'<>&,;()?=]")
_NAME_DELIMITERS = frozenset("\"'<>&,;()?=")
_RESOURCE_START_RE = re.compile(r"(?i)\b[au]rn:")


def _key_is_resource_segment(before: str) -> bool:
    """Does the text before a key end inside an `arn:`/`urn:` name, right
    after a `:` or `/` segment boundary? The name's tail is the run back to
    the last whitespace or name delimiter (`& , ; ( ) ? =`, a quote or angle
    bracket); it must hold the name's start. One backward walk and one
    forward search over the tail (a regex anchored at the end restarted at
    every position)."""
    if not before or before[-1] not in ":/":
        return False
    k = len(before)
    while (
        k > 0 and before[k - 1] not in _NAME_DELIMITERS and not before[k - 1].isspace()
    ):
        k -= 1
    work(len(before) - k + 1)
    return _RESOURCE_START_RE.search(before, k) is not None


@suppression("N10")
def _n10(span: FloorSpan) -> bool:
    """A key that is a segment of an `arn:`/`urn:` resource name, with its
    separator and value inside the name too, names a resource
    (`arn:aws:secretsmanager:...:secret:Name`), and only such a key: a key
    after the name in the same run (`...:token-exchange&client_secret=x`,
    `urn:db;password=x`) is a key, and so is a pair that leaves the name
    (`...:token-exchange=client_secret=x`). Read on the INPUT
    before the key: an earlier step of main's may already have rewritten the
    `arn` itself."""
    return (
        span.rule in _KEYED_RULES
        and span.part == "value"
        and span.raw_key_before is not None
        and _key_is_resource_segment(span.raw_key_before)
        # the separator and the value are in the name too: `:` only, and a
        # value with no name delimiter (`...:token-exchange=client_secret=x`
        # leaves the name at the `=`)
        and span.sep.strip(":") == ""
        and _NAME_DELIMITER_RE.search(span.value) is None
    )


def _last_segment(qualifier: str) -> str:
    return re.split(r"[_-]", qualifier.strip("_-").lower())[-1]


@suppression("C12")
def _c12(span: FloorSpan) -> bool:
    """`code` under a diagnostic or descriptive qualifier keeps its value
    (`status_code=401`, `error_code=E_TIMEOUT_42`, `sqlstate_code=42P01`)."""
    return span.rule in _KEYED_RULES and _for_every_reading(
        span,
        lambda r: r.name.lower() == "code"
        and _last_segment(r.qualifier) in DIAGNOSTIC_CODE_QUALIFIERS,
    )


def _whitespace_sep(span: FloorSpan) -> bool:
    sep, _ = _sep_and_value(span)
    return sep != "" and sep.isspace()


@suppression("C3a")
def _c3a(span: FloorSpan) -> bool:
    """`code` never counts on a whitespace-only separator (`exit code 137`,
    `status code 401`, `zip code 94105`)."""
    return (
        span.rule == "keyword"
        and _whitespace_sep(span)
        and _for_every_reading(span, lambda r: r.name.lower() == "code")
    )


@suppression("C3")
def _c3(span: FloorSpan) -> bool:
    """A whitespace-only separator with a value that is not credential-shaped
    (D2) is prose: `token bucket`, `secret ingredient`, `session expired`,
    `token v2`."""
    return (
        span.rule in ("keyword", "policy:2")
        and span.part == "value"
        and _whitespace_sep(span)
        and not looks_like_credential(_sep_and_value(span)[1])
    )


_PAIR_RE = re.compile(r"[A-Za-z_-]+=[^=]")


_PAIR_VALUE_RE = re.compile(r"[A-Za-z_-]+=([^\s\"',;]*)")


@suppression("N11")
def _n11(span: FloorSpan) -> bool:
    """After a keyword and whitespace, a `name=value` token whose value is
    not credential-shaped is its own pair, not the keyword's value (`token
    expires_in=3600`, `token code=404`). Never after `Bearer` or
    `Authorization`: whatever follows the scheme is the credential
    (`Bearer abcdef=SECRETPART`), as main read it."""
    if span.rule not in ("keyword", "policy:2") or span.part != "value":
        return False
    if span.key.strip().lower() in ("bearer", "authorization"):
        return False
    if not _whitespace_sep(span):
        return False
    pair = _PAIR_VALUE_RE.match(_sep_and_value(span)[1] + span.after)
    return pair is not None and not looks_like_credential(pair.group(1))


def _single_case(word: str) -> bool:
    word = word.lstrip("-_")
    return (
        word.isupper() or word.islower() or (word[:1].isupper() and word[1:].islower())
    )


_ACRONYM_TITLE_RE = re.compile(r"[A-Z0-9]{1,8}[A-Z][a-z]+")


@suppression("N4")
def _n4(span: FloorSpan) -> bool:
    """A glued mixed-case key before whitespace is an identifier, not a key
    (`Ed25519PrivateKey X509Cert`, `Base64UrlEncoder Sha256HashAlgorithm`)."""

    def mixed_identifier(r: KeyReading) -> bool:
        key = r.glued + r.name + r.suffix
        return (
            bool(r.glued)
            and not _single_case(key)
            and _ACRONYM_TITLE_RE.fullmatch(key.lstrip("-_")) is None
        )

    return (
        span.rule == "keyword"
        and _whitespace_sep(span)
        and _for_every_reading(span, mixed_identifier)
    )


@suppression("C4")
def _c4(span: FloorSpan) -> bool:
    """An operator run holding `==` or `::` before a value that is not
    credential-shaped is a comparison or a path (`if token == expected`,
    `token::Type`)."""
    sep, value = _sep_and_value(span)
    return (
        span.rule in _KEYED_RULES
        and span.part == "value"
        and ("==" in sep or "::" in sep)
        and not looks_like_credential(value)
    )


@suppression("C5")
def _c5(span: FloorSpan) -> bool:
    """A suffixed key with a value that is not credential-shaped names
    metadata (`token_type=Bearer`, `password_length=12`,
    `token_endpoint=https://...`)."""
    value = _sep_and_value(span)[1]
    return (
        span.rule == "keyword"
        and not looks_like_credential(value)
        and _for_every_reading(
            span, lambda r: bool(r.suffix) and not _declared(r.name, r.suffix)
        )
    )


@suppression("C6")
def _c6(span: FloorSpan) -> bool:
    """A glued key (no joiner before the key word) counts only with a
    credential-shaped value that is not a URL or ARN (`unicode input`,
    `encoded payload`, `mytoken=https://...`)."""
    value = _sep_and_value(span)[1]
    return (
        span.rule in _KEYED_RULES
        and span.part == "value"
        and (
            not looks_like_credential(value)
            or value.lower() in ("https", "http")
            and span.after.startswith("://")
            or value.lower().startswith("arn:")
        )
        and _for_every_reading(span, lambda r: bool(r.glued))
    )


@suppression("C7")
def _c7(span: FloorSpan) -> bool:
    """After an unquoted key, a value starting on `,` or `}` is JSON
    structure (`...token: ", "next": ...`)."""
    sep, value = _sep_and_value(span)
    return (
        span.rule in _KEYED_RULES
        and not sep.startswith('"')
        and value.startswith((",", "}"))
    )


def unindented_break(text: str) -> bool:
    """Does ``text`` hold a line break with no space, tab or no-break space
    after it (the last break decides: an indent after it is after every
    earlier one too)? One reverse search, not a regex anchored at the end."""
    work(len(text))
    last = max(text.rfind("\r"), text.rfind("\n"))
    return last >= 0 and not any(c in " \t\xa0" for c in text[last + 1 :])


@suppression("C8")
def _c8(span: FloorSpan) -> bool:
    """A separator whose last line break is unindented, before an unquoted
    value that is not credential-shaped, ends a sentence (`token:\\nthe
    bearer of`)."""
    sep, value = _sep_and_value(span)
    return (
        span.rule in (*_KEYED_RULES, "policy:2", "bearer")
        and span.part == "value"
        and unindented_break(span.key + sep)
        and not value.startswith(('"', "'"))
        and not looks_like_credential(value)
    )


@suppression("C10")
def _c10(span: FloorSpan) -> bool:
    """`code` (main's one weak key) keeps a plain word or number
    (`{"code": -32601}` is every JSON-RPC error), and under a non-OAuth
    qualifier any value that is not credential-shaped."""
    value = _sep_and_value(span)[1]

    def weak(r: KeyReading) -> bool:
        if r.name.lower() != "code" or (r.suffix and not _declared(r.name, r.suffix)):
            return False
        if is_plain(value):
            return True
        return r.qualifier.strip(
            "_-"
        ).lower() not in OAUTH_CODE_QUALIFIERS and not looks_like_credential(value)

    return span.rule in _KEYED_RULES and _for_every_reading(span, weak)


def _unwrap(value: str) -> str:
    if len(value) >= 2 and value[0] + value[-1] in ('""', "''", "()", "[]", "{}", "<>"):
        return value[1:-1]
    return value


@suppression("C11")
def _c11(span: FloorSpan) -> bool:
    """`Authorization`/`Bearer` followed by a plain word, bare or wrapped in
    quotes or brackets, is prose (`Missing bearer token`, `the bearer of`,
    `Authorization: required`)."""
    return (
        span.rule in ("bearer", "authorization", "policy:2")
        and span.part == "value"
        and (span.rule != "policy:2" or span.key.lower() == "bearer")
        and is_plain(_unwrap(_unescaped(span.value)))
    )
