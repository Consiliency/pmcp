"""Verify EVERY flag-table entry against the tool's own --help output.

The source comment and CHANGELOG claim these tables were "verified entry by
entry". This is the check that makes the claim true, run per entry rather than
per table. A boolean entry that actually takes a value is the fail-OPEN
direction -- its value becomes the package name -- so the boolean tables matter
most.

Deliberately a scratchpad script, not a pytest: help text varies by tool
version and CI has none of these CLIs installed. Behaviour is pinned by the
pure unit tests in tests/test_version_checker.py.
"""

import pathlib
import re
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))
from pmcp.manifest import version_checker as vc  # noqa: E402


def help_text(cmd: list[str]) -> str:
    p = subprocess.run(cmd, capture_output=True, text=True)
    return p.stdout + p.stderr


def definitions(text: str) -> dict[str, bool]:
    """{flag: takes_a_value} for every option DEFINITION line in *text*.

    A definition line starts with whitespace then `-`, and its head (the part
    before the description's 2+ space gutter) is a comma-separated list where
    EVERY part begins with `-`. That last condition is what rejects wrapped
    prose like "--implementation, --platform, and --python-version", which
    otherwise parses as three definitions and silently overwrites the real
    ones with the wrong arity.
    """
    out: dict[str, bool] = {}
    for line in text.splitlines():
        if not re.match(r"^\s+-", line):
            continue
        head = re.split(r"\s{2,}", line.strip(), maxsplit=1)[0].rstrip(",")
        parts = [p.strip() for p in head.split(",")]
        if not parts or not all(p.startswith("-") for p in parts):
            continue
        # Reject WRAPPED PROSE that happens to begin with a flag. pip's
        # `--extra-index-url` description wraps onto a line reading just
        # "--index-url.", which parses as a definition with no metavar and
        # then overwrote the real `-i, --index-url <url>` entry with
        # takes_value=False -- a false MISCLASSIFIED report that cost a real
        # investigation. Trailing sentence punctuation is the tell.
        # `...` is the REPEATABLE-counter marker (`-q, --quiet...`), not
        # sentence punctuation, so strip it before judging.
        last = parts[-1].rstrip()
        while last.endswith("..."):
            last = last[:-3]
        if last.endswith((".", ",", ":", ";")):
            continue
        words = parts[-1].split()
        # A trailing `...` marks a REPEATABLE counter (uv prints `-q,
        # --quiet...`), not a value. Reading those as value flags would eat
        # the package in `uvx --quiet my-package`.
        takes = len(words) > 1 and not words[0].endswith("...")
        for part in parts:
            flag = part.split()[0].rstrip(".")
            out.setdefault(flag, takes)
            out[flag] = takes
    return out


UVX = definitions(help_text(["uv", "tool", "run", "--help"]))
DOCKER = definitions(help_text(["docker", "run", "--help"]))
PIP = definitions(help_text(["pip", "install", "--help"]))
CARGO = definitions(help_text(["cargo", "run", "--help"]))
CARGO.update(
    {k: v for k, v in definitions(help_text(["cargo", "install", "--help"])).items()}
)
NPM = definitions(help_text(["npm", "exec", "--help"]))

# `docker run --help` never documents combined short flags; they are one token
# formed from two documented booleans. Justified by hand, and the ONLY entries
# permitted to be absent from help.
JUSTIFIED_ABSENT = {
    ("docker", "-it"): "combined -i -t; docker documents them only separately",
    ("docker", "-ti"): "combined -t -i; docker documents them only separately",
}

# npm's option LIST prints a bare `--package` with the description on the next
# line, so the metavar never appears where this parser looks. Its USAGE block
# is unambiguous -- `npm exec --package=<pkg>[@<version>] -- <cmd>` and
# `[--package <package-spec> ...]` -- so the arity is verified from there.
JUSTIFIED_ARITY = {
    ("npm", "--package"): "metavar is in the usage block, not the option list",
}

TABLES = [
    ("uvx", UVX, "value", vc._UVX_VALUE_FLAGS),
    ("uvx", UVX, "boolean", vc._UVX_BOOLEAN_FLAGS),
    ("uvx", UVX, "positive", vc._UVX_POSITIVE_FLAGS),
    ("pip", PIP, "value", vc._PIP_VALUE_FLAGS),
    ("pip", PIP, "boolean", vc._PIP_BOOLEAN_FLAGS),
    ("cargo", CARGO, "value", vc._CARGO_VALUE_FLAGS),
    ("cargo", CARGO, "boolean", vc._CARGO_BOOLEAN_FLAGS),
    ("cargo", CARGO, "positive", vc._CARGO_POSITIVE_FLAGS),
    ("docker", DOCKER, "value", vc._DOCKER_VALUE_FLAGS),
    ("docker", DOCKER, "boolean", vc._DOCKER_BOOLEAN_FLAGS),
    ("npm", NPM, "positive", vc._NPM_POSITIVE_FLAGS),
]


def main() -> int:
    mismatches: list[str] = []
    unverified: list[str] = []
    checked = 0

    for tool, help_table, kind, flags in TABLES:
        for flag in sorted(flags):
            checked += 1
            if flag not in help_table:
                why = JUSTIFIED_ABSENT.get((tool, flag))
                if why:
                    print(f"  ABSENT-OK  {tool:6s} {kind:8s} {flag:24s} {why}")
                else:
                    unverified.append(f"{tool} {kind} {flag}")
                continue
            takes = help_table[flag]
            # value and positive flags must take a value; boolean must not.
            expected = kind in ("value", "positive")
            if takes != expected and (tool, flag) in JUSTIFIED_ARITY:
                print(
                    f"  ARITY-OK   {tool:6s} {kind:8s} {flag:24s} "
                    f"{JUSTIFIED_ARITY[(tool, flag)]}"
                )
            elif takes != expected:
                mismatches.append(
                    f"{tool} {kind:8s} {flag:24s} "
                    f"help says takes_value={takes}, table says {expected}"
                )

    print(f"\nchecked {checked} entries against live --help output")

    if mismatches:
        print(f"\n!! {len(mismatches)} MISCLASSIFIED ENTRIES:")
        for m in mismatches:
            print(f"   {m}")
    if unverified:
        print(f"\n!! {len(unverified)} ENTRIES NOT FOUND IN HELP (unverified):")
        for u in unverified:
            print(f"   {u}")
    if not mismatches and not unverified:
        print("\nAll entries verified against the tool's own --help.")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
