"""Adversarial input shapes derived from the redactor's own regular
expressions (Consiliency/pmcp#234).

A super-linear path in a regex or a scan needs an input the path can consume
over and over and then fail on. So each pattern is parsed (`re._parser`) and
every piece a quantifier can repeat -- a literal run, a representative of
each character class, each key word of an alternation -- becomes a unit;
each unit is repeated to the target length, with each of a few failing tails
and after each of the redactor's trigger words. The timing sweep in
`tests/test_redaction_floor.py` runs every shape through every public entry
point at growing sizes and asserts the growth is linear.

Stdlib only.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable

try:  # Python 3.11+
    import re._parser as sre_parse  # type: ignore[import-not-found]
    from re import _constants as sre_constants  # type: ignore[attr-defined]
except ImportError:  # Python 3.10
    import sre_constants  # type: ignore[no-redef]
    import sre_parse  # type: ignore[no-redef]

#: Characters after a repeated unit that make a match fail late: a letter, a
#: space, a line break, an operator, each quote and closing bracket.
TAILS = ("", "a", " ", "\n", "=", '"', "'", ")", "\\", " abc", "=abc123")
#: What precedes the run: nothing, or a word that arms one of the rules.
LEADS = (
    "",
    "x ",
    "token",
    "password=",
    "Bearer x",
    "x Bearer",
    "Authorization: x",
    "arn:",
    "https://h/?",
    "code ",
)

_CATEGORY_CHARS = {
    sre_constants.CATEGORY_SPACE: " \n",
    sre_constants.CATEGORY_NOT_SPACE: "a",
    sre_constants.CATEGORY_DIGIT: "1",
    sre_constants.CATEGORY_NOT_DIGIT: "a",
    sre_constants.CATEGORY_WORD: "a_",
    sre_constants.CATEGORY_NOT_WORD: " -",
}
_FALLBACK = "a1-_ :=\n\"')]}.,;/\\&"


def _class_chars(items: list[tuple[object, object]]) -> str:
    chars = ""
    negate = False
    members: set[str] = set()
    for op, arg in items:
        if op is sre_constants.NEGATE:
            negate = True
        elif op is sre_constants.LITERAL:
            members.add(chr(arg))  # type: ignore[arg-type]
        elif op is sre_constants.RANGE:
            lo, hi = arg  # type: ignore[misc]
            members.update({chr(lo), chr(hi)})
        elif op is sre_constants.CATEGORY:
            members.update(_CATEGORY_CHARS.get(arg, ""))  # type: ignore[arg-type]
    if negate:
        excluded = members
        chars = "".join(c for c in _FALLBACK if c not in excluded)[:3]
    else:
        chars = "".join(sorted(members))[:4]
    return chars


def units(pattern: str, flags: int = 0) -> set[str]:
    """The repeatable pieces of ``pattern``: literal runs, one to four
    representatives of each class, each alternative of an alternation."""
    found: set[str] = set()

    def walk(tree: Iterable[tuple[object, object]]) -> str:
        """Returns the literal text the subtree always starts with."""
        literal = ""
        for op, arg in tree:
            if op is sre_constants.LITERAL:
                literal += chr(arg)  # type: ignore[arg-type]
                continue
            if literal:
                found.add(literal)
                literal = ""
            if op is sre_constants.IN:
                found.update(_class_chars(arg))  # type: ignore[arg-type]
            elif op is sre_constants.CATEGORY:
                found.update(_CATEGORY_CHARS.get(arg, ""))  # type: ignore[arg-type]
            elif op in (sre_constants.MAX_REPEAT, sre_constants.MIN_REPEAT):
                walk(arg[2])  # type: ignore[index]
            elif op is sre_constants.SUBPATTERN:
                walk(arg[-1])  # type: ignore[index]
            elif op is sre_constants.BRANCH:
                for branch in arg[1]:  # type: ignore[index]
                    walk(branch)
            elif op in (sre_constants.ASSERT, sre_constants.ASSERT_NOT):
                walk(arg[1])  # type: ignore[index]
        if literal:
            found.add(literal)
        return literal

    walk(sre_parse.parse(pattern, flags))
    return {u for u in found if u}


def shapes(patterns: Iterable[re.Pattern[str]]) -> dict[str, Callable[[int], str]]:
    """Every shape, keyed by a readable name: ``lead + unit * k + tail``,
    sized to ``n`` characters."""
    all_units: set[str] = set()
    for pattern in patterns:
        all_units |= units(pattern.pattern, pattern.flags)
    out: dict[str, Callable[[int], str]] = {}
    for unit in sorted(all_units):
        for lead in LEADS:
            for tail in TAILS:
                name = f"{lead!r}+{unit!r}*k+{tail!r}"

                def make(
                    n: int, lead: str = lead, unit: str = unit, tail: str = tail
                ) -> str:
                    return (
                        lead
                        + unit * max(1, (n - len(lead) - len(tail)) // len(unit))
                        + tail
                    )

                out[name] = make
    # a key word glued to a separator or joiner (`secret:secret:...`,
    # `token-token-...`): a key and a separator at every step
    words = sorted(u for u in all_units if len(u) >= 3 and u.isalpha())
    for word in words:
        for glue in (":", "-", "=", "_", " ", "/"):
            for lead in ("", "arn:", "x "):
                for tail in ("", "=abc123", " abc"):
                    name = f"{lead!r}+{word + glue!r}*k+{tail!r}"

                    def make3(
                        n: int,
                        lead: str = lead,
                        unit: str = word + glue,
                        tail: str = tail,
                    ) -> str:
                        return (
                            lead
                            + unit * max(1, (n - len(lead) - len(tail)) // len(unit))
                            + tail
                        )

                    out[name] = make3
    # two-unit alternations (`a-`, `=:`, `\"`): a word boundary or a
    # backtracking point at every step
    singles = sorted(u for u in all_units if len(u) == 1)
    for a in singles:
        for b in singles:
            if a == b:
                continue
            for lead in ("", "x ", "token", "Bearer x"):
                for tail in ("", "a", " "):
                    name = f"{lead!r}+{a + b!r}*k+{tail!r}"

                    def make2(
                        n: int, lead: str = lead, unit: str = a + b, tail: str = tail
                    ) -> str:
                        return (
                            lead
                            + unit * max(1, (n - len(lead) - len(tail)) // 2)
                            + tail
                        )

                    out[name] = make2
    return out


def redactor_patterns() -> list[re.Pattern[str]]:
    """Every compiled pattern the redactor's modules hold."""
    import pmcp.auth
    import pmcp.keyword_matcher
    import pmcp.policy.policy
    import pmcp.redaction_floor

    found: list[re.Pattern[str]] = []
    for module in (
        pmcp.redaction_floor,
        pmcp.keyword_matcher,
        pmcp.auth,
        pmcp.policy.policy,
    ):
        for value in vars(module).values():
            candidates = (
                value
                if isinstance(value, (tuple, list))
                else value.values()
                if isinstance(value, dict)
                else [value]
            )
            found.extend(c for c in candidates if isinstance(c, re.Pattern))
    found.extend(
        re.compile(p, re.IGNORECASE)
        for p in pmcp.policy.policy.DEFAULT_REDACTION_PATTERNS
    )
    unique = {(p.pattern, p.flags): p for p in found if isinstance(p.pattern, str)}
    return list(unique.values())


# ------------------------------------------------------------ compositions
#
# The shapes above come from the regular expressions alone, one lead at a
# time. The replay and the additive rules also have Python loops whose cost
# depends on how passes COMPOSE: a match of one pass that spans many
# markers left by an earlier one (a Bearer value over a URL's rewritten
# pairs), a lookup per match over structures built by another pass (every
# keyword match asking every resource name), a scan per span over text
# other spans share (the backslash run before every escape). So the second
# family is built from the loop structure: two leads that arm different
# passes, a repeated unit that makes one pass leave many markers or ranges
# for the other, units interleaved, and each composition nested inside a
# JSON string and after a header.

CONTEXTS = (
    "",
    "Authorization: ",
    "Bearer ",
    "password=",
    "token ",
    "https://h/?",
    "https://h/?password=x",
    "Authorization: https://h/?q",
    "Bearer https://h/?password=x",
    "see https://h/?password=x",
    "arn:x ",
    "x=urn:a:b&",
    '{"t": "',
    "code ",
    "\\",
)
#: Units that make one pass leave many markers, ranges or spans behind.
LOOP_UNITS = (
    "&a+b",
    "password=x&",
    "&a",
    "a=%41&",
    "&token=%2541",
    "token=x ",
    "arn:x ",
    "arn:x:secret=abc123 ",
    "password=abc123 ",
    'tokens: ["a1b2c3d4"] ',
    "[REDACTED]",
    "\\u00e9",
    '\\"',
    "\\\\",
    "Bearer x ",
    "Authorization: x ",
    "secret:",
    "a-",
    "=:",
    ")",
    "\n",
    "%25",
    "ghp_abcdefghij1234 ",
    "https://u:p@h/?a=1 ",
)
COMPOSITION_TAILS = ("", " end", '"')


def compositions() -> dict[str, Callable[[int], str]]:
    """Two leads + a repeated unit (or two interleaved) + a tail, and each
    nested in a JSON string."""
    import json

    out: dict[str, Callable[[int], str]] = {}

    def add(name: str, lead: str, unit: str, tail: str, nest: bool) -> None:
        def make(n: int, lead: str = lead, unit: str = unit, tail: str = tail) -> str:
            text = lead + unit * max(1, (n - len(lead) - len(tail)) // len(unit)) + tail
            return json.dumps({"t": text}) if nest else text

        out[name] = make

    for first in CONTEXTS:
        for second in CONTEXTS:
            for unit in LOOP_UNITS:
                for tail in COMPOSITION_TAILS:
                    lead = first + second
                    add(f"{lead!r}+{unit!r}*k+{tail!r}", lead, unit, tail, False)
    for lead in CONTEXTS:
        for a in LOOP_UNITS:
            for b in LOOP_UNITS:
                if a != b:
                    add(f"{lead!r}+{a + b!r}*k", lead, a + b, "", False)
    for lead in CONTEXTS:
        for unit in LOOP_UNITS:
            add(f"json({lead!r}+{unit!r}*k)", lead, unit, "", True)
            add(
                f"hdr+json({lead!r}+{unit!r}*k)",
                "Authorization: " + lead,
                unit,
                "",
                True,
            )
    return out
