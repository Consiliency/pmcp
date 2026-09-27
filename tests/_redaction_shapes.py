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
