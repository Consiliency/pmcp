"""The auth redactor's keyword rule, matched in one linear pass.

`sanitize_auth_diagnostic` redacts `<key><sep><value>` as

    (?i)\\b([A-Za-z0-9_-]*(?:KEYS)[A-Za-z0-9_-]*)([\\s:=]+)([A-Za-z0-9._~+/=-]{3,})

`keyword_matches` yields exactly the matches `finditer` of that pattern
yields, left to right, but scans each identifier run once instead of letting
the regex engine re-scan it from every word boundary, so the cost stays
linear in the length of the text. `redact_keyword_values` is the drop-in for
`re.sub(<pattern>, r"\\1\\2[REDACTED]", text)`.

Why this is the same match set:

* Group 1 is made of identifier characters W = [A-Za-z0-9_-] (under (?i),
  which also admits the case-fold partners of those letters), and group 2
  starts with a character of [\\s:=], which W never contains. So group 1 always
  ends exactly where the maximal W-run it starts in ends (r), and a match
  needs text[r] in [\\s:=].
* Group 1 must hold a key, and a key is made of W characters, so the key lies
  inside the run. The match starts at the leftmost position s of the run
  where `\\b` holds and a key starts at or after s.
* Group 2 is greedy: it takes the maximal [\\s:=] run [r, q) and backtracks
  one character at a time. The value V = [A-Za-z0-9._~+/=-] shares only `=`
  with it, so a value starting at p < q is the `=` run from p, continuing
  past q only if every character in between is `=`. The first p, from q down
  to r + 1, whose V-run is 3+ long is the regex's.
* A value ends at a character outside V, which is outside W too (W is a
  subset of V), so the next match's run starts after it.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator

_W_RUN_RE = re.compile(r"(?i)[A-Za-z0-9_-]+")
_SEP_RUN_RE = re.compile(r"[\s:=]+")
_VALUE_RUN_RE = re.compile(r"(?i)[A-Za-z0-9._~+/=-]+")
_WORD_CHAR_RE = re.compile(r"\w")


def keys_alternation(keys: Iterable[str]) -> str:
    """The key alternation: the escaped keys (sorted; order cannot change a
    match, since group 1 always runs to the end of its identifier run) and
    `api[_-]?key`."""
    return "|".join([*sorted(re.escape(key) for key in keys), r"api[_-]?key"])


def key_start_pattern(keys: Iterable[str]) -> re.Pattern[str]:
    """Where a key starts (a lookahead: every start, overlapping ones too)."""
    return re.compile(rf"(?i)(?=(?:{keys_alternation(keys)}))")


def keyword_matches(
    text: str, key_start: re.Pattern[str]
) -> Iterator[tuple[int, int, int, int]]:
    """Main's keyword rule's matches, left to right: for each, (match start,
    group 1 end, group 2 end, match end) -- the tuples `finditer` of main's
    regex yields for the key set ``key_start`` was built from."""

    def is_word(index: int) -> bool:
        return 0 <= index < len(text) and _WORD_CHAR_RE.match(text, index) is not None

    consumed = 0
    for run in _W_RUN_RE.finditer(text):
        a, r = run.span()
        if a < consumed:
            continue  # inside the previous match's value
        sep = _SEP_RUN_RE.match(text, r)
        if sep is None:
            continue
        last_key = -1
        for key in key_start.finditer(text, a, r):
            last_key = key.start()
        if last_key < 0:
            continue
        start = -1
        for s in range(a, last_key + 1):
            if is_word(s - 1) != is_word(s):
                start = s
                break
        if start < 0:
            continue
        q = sep.end()
        value = _VALUE_RUN_RE.match(text, q)
        value_end = value.end() if value is not None else q
        # p from q down: the V-run from p, as the backtracking regex sees it
        start_of_value = -1
        run_end = value_end  # the V-run's end from p, as p decreases
        p = q
        while p > r:
            if p < q:
                if text[p] != "=":
                    run_end = p  # empty here; a lower `=` restarts a run
                elif p + 1 < q and text[p + 1] != "=":
                    run_end = p + 1
                # else: the `=` run from p+1 (or the value at q) continues
            if run_end - p >= 3:
                start_of_value = p
                break
            p -= 1
        if start_of_value < 0:
            continue
        yield (start, r, start_of_value, run_end)
        consumed = run_end


def redact_keyword_values(
    text: str, key_start: re.Pattern[str], marker: str = "[REDACTED]"
) -> str:
    """Main's `re.sub(<keyword rule>, r"\\1\\2[REDACTED]", text)`, linear."""
    pieces: list[str] = []
    position = 0
    for _, _, value_start, end in keyword_matches(text, key_start):
        pieces.append(text[position:value_start])
        pieces.append(marker)
        position = end
    pieces.append(text[position:])
    return "".join(pieces)
