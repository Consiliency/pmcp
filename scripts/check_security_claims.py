#!/usr/bin/env python3
"""Bind every claim in `SECURITY.md`'s trust-model region to a test that proves it.

EC-SEAL-1 reads "SECURITY.md describes the implemented model with no claim the
tests do not prove; each claim cites the test that proves it". That is a contract,
not a writing task, and it is worthless unless a reviewer can run something that
goes red when a claim loses its proof. This script is that something. The format
it enforces and every way it fails are frozen in IF-0-SEAL-1 of
`plans/phase-plan-v13-SEAL.md`; the rule ids below are that gate's, and
`tests/test_security_claims_parser.py` carries one doctored fixture per rule.

    scripts/check_security_claims.py [PATH]             structure + resolvability
    scripts/check_security_claims.py --node-ids [PATH]  the cited union, one per line
    scripts/check_security_claims.py --run [PATH]       check, then execute that union

Exit codes: 0 clean, 1 one or more rules failed (or `--run`'s union did not pass),
2 the document could not be read. Every failure prints as

    FAIL <rule> <failure_name>: <detail>

so a reviewer reads which frozen rule rejected the document, not a stack trace.

WHAT THE TWO LIVENESS RULES DO, because they are easy to conflate. `--run`
executes the cited union once and proves the proofs pass *at the moment the phase
closes*; it says nothing about later commits. R9 is the standing guarantee: the
cited union must be a SUBSET of what CI's own collection discovers, so every proof
keeps running afterwards. Explicit collection is not discovery -- a
`tests/proof_helpers.py::test_boundary` collects perfectly well when named and is
invisible to `pytest tests/`, and a `live`-marked test is deselected by
`pyproject.toml`'s `addopts = "-m 'not live'"`. R22 is what keeps R9 honest: it
fails when CI's own invocation drifts away from the one this script mirrors.

WHAT THIS SCRIPT DOES NOT PROMISE. That a passing test still *means* what its
claim says is a human judgement no parser makes: a test can be weakened in place.
R8 and R9 bind the citation, the suite binds the result, and reviewing a diff that
edits both a claim and its test is where the remaining risk lives.
"""

from __future__ import annotations

import argparse
import ast
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

# --------------------------------------------------------------------------
# The document's two delimited regions.
# --------------------------------------------------------------------------

REGION_BEGIN = "<!-- TRUST-MODEL-CLAIMS: BEGIN -->"
REGION_END = "<!-- TRUST-MODEL-CLAIMS: END -->"
LEDGER_BEGIN = "<!-- CLAIM-LEDGER: BEGIN -->"
LEDGER_END = "<!-- CLAIM-LEDGER: END -->"

LEDGER_HEADER = ("Claim", "Kind", "Proof")
KINDS = ("guarantee", "limitation")
CHARACTERIZES = "characterizes:"
EM_DASH = "—"
LIMITATION_LABEL = "**Limitation.**"

# --------------------------------------------------------------------------
# R21: a heading inside the region is navigation, never assertion. The label set
# is frozen, so restructuring the section is a deliberate, reviewable edit to this
# file -- the same pattern as the census and the lexicon allowlist below.
# --------------------------------------------------------------------------

REGION_HEADINGS = (
    "### The v13 trust model",
    "#### Identity, not labels",
    "#### Consent for repository-supplied configuration",
    "#### Outbound actions",
    "#### Limitations",
)

# --------------------------------------------------------------------------
# R16: the v13 evidence census. Nineteen files, enumerated from the tree with
#   git log --diff-filter=A --name-only aa2de50..d455a1f -- tests/
# and attributed by each file's adding commit: TRUST 9ae65f0 (3), CONSENT 9979f23
# (4), PKGID 5c7e0a1 (7), EGRESS d455a1f (5). Three of the four phases' own
# amendment blocks undercounted, which is why this is a constant here rather than
# a hand-maintained list: a ledger citing three of the nineteen documents a
# sixth of the model and would otherwise pass every other rule.
#
# `tests/test_env_leak_229.py` is in that commit range and is NOT census: it
# predates v13 (87a01e4, Consiliency/pmcp#229). `tests/conftest.py` is a shared
# seam, not evidence.
# --------------------------------------------------------------------------

V13_EVIDENCE_CENSUS = (
    "tests/test_trust_store.py",
    "tests/test_trust_cli.py",
    "tests/test_package_identity.py",
    "tests/test_project_consent_gate.py",
    "tests/test_project_source_consent_manifest.py",
    "tests/test_project_source_consent_config.py",
    "tests/test_project_source_consent_policy.py",
    "tests/test_package_identity_gate.py",
    "tests/test_package_approvals.py",
    "tests/test_policy_package_identifiers.py",
    "tests/test_install_argv_logging.py",
    "tests/test_pkgid_panel_fixes.py",
    "tests/test_pkgid_spawn_logging.py",
    "tests/test_pkgid_manifest_npx_selectors.py",
    "tests/test_feedback_egress.py",
    "tests/test_feedback_egress_gate.py",
    "tests/test_feedback_provenance.py",
    "tests/test_feedback_submission_flag.py",
    "tests/test_egress_panel_fixes.py",
)

# --------------------------------------------------------------------------
# R17: the eleven lane-owned tests that no merged phase's acceptance criteria run,
# reported by
#   python3 scripts/check_plan_consistency.py plans/phase-plan-v13-*.md
# (ten in CONSENT, one in TRUST). All eleven exist and pass; what was missing was
# the requirement that they be proven, and neither merged plan's criteria can be
# edited now. Citing each from a `guarantee` row is what makes it required: delete
# it and R8 fails, break it and CI's suite fails. Among them are read_and_gate's
# one-read TOCTOU guard, both fail-closed cases and the redaction-widening rule's
# only falsifier.
# --------------------------------------------------------------------------

PREVIOUSLY_UNPROVEN_NODE_IDS = (
    "tests/test_project_consent_gate.py::test_read_and_gate_opens_the_path_exactly_once",
    "tests/test_project_consent_gate.py::test_an_unrecorded_path_is_refused",
    "tests/test_project_consent_gate.py::test_a_store_error_is_refused_not_raised",
    "tests/test_project_consent_gate.py::test_an_unreadable_source_is_refused_not_raised",
    "tests/test_project_consent_gate.py::test_read_and_gate_returns_none_bytes_when_refused",
    "tests/test_project_consent_gate.py"
    "::test_remediation_is_the_absolute_path_trust_approve_command",
    "tests/test_project_consent_gate.py"
    "::test_log_refusal_emits_one_warning_naming_the_remediation",
    "tests/test_project_source_consent_manifest.py::test_approved_overlay_is_applied",
    "tests/test_project_source_consent_config.py::test_approved_project_mcp_json_is_applied",
    "tests/test_project_source_consent_policy.py"
    "::test_project_redaction_patterns_extend_rather_than_replace_defaults",
    "tests/test_trust_store.py::test_a_denied_record_is_not_approved",
)

# --------------------------------------------------------------------------
# R18: the frozen v13 lexicon. Nothing stops an author stating a model property in
# the legacy prose above the region, where no marker is required; this closes that.
# Matched case-insensitively at identifier boundaries.
#
# KNOWN LIMIT, reported to the lead rather than silently widened: the freeze says
# "at word boundaries", so inflected forms (`approved`, `revoked`, `provisioned`)
# do not match. Widening each entry to a prefix match is a one-character change
# per term and a freeze amendment, not a lane's call.
# --------------------------------------------------------------------------

V13_LEXICON = (
    "trust store",
    "pmcp trust",
    "approval",
    "approve",
    "revoke",
    "consent",
    ".pmcp/manifest.yaml",
    ".mcp.json",
    ".mcp-gateway-policy.yaml",
    "package identity",
    "packages.denylist",
    "packages.allowlist",
    "npx",
    "provision",
    "discovered server",
    "register_discovered_server",
    "PMCP_FEEDBACK_TOKEN",
    "submit_feedback",
    "integrity",
    "provenance",
    "narrowing",
)

# ==========================================================================
# THE R18 ALLOWLIST -- THE ONE CONSTANT IN THIS FILE ANOTHER LANE MAY EDIT.
#
# SL-5 owns the region-external sweep and is authorised by the lead to add
# entries here, and only here, the way SL-0 was given one assertion in a file it
# did not own. Nothing else in this file is SL-5's to change.
#
# REQUIRED SHAPE -- a two-string tuple, in this order:
#   (exact stripped text of the exempt line, the reason it is exempt)
# The first element must equal `line.strip()` of the SECURITY.md line, verbatim
# and complete. It is not a substring, a pattern or a term: rewording the line
# retires its exemption and the sentence is re-reviewed, which is how the
# allowlist fails closed. The second element is prose a reviewer reads, and it
# must say why the line names a v13 term WITHOUT asserting a v13 property.
#
# A flat ban is unusable -- some region-external prose legitimately names a term
# about something else -- but an entry is a claim that no model property is being
# stated outside the region, so it is a deliberate, reviewable act. When the
# honest answer is "this does assert a v13 property", the remedy is to MOVE the
# sentence into the claim region, not to add a line here.
# ==========================================================================
REGION_EXTERNAL_ALLOWLIST = (
    (
        "accept an elicitation identifier and consent acknowledgement.",
        "URL-mode elicitation bullet: names consent about an OAuth handshake, "
        "not about the v13 project-source consent gate.",
    ),
)

# --------------------------------------------------------------------------
# R9 / R22: CI's invocation, and the one this script mirrors.
#
# CI_PYTEST_INVOCATION is `.github/workflows/test.yml:54` verbatim.
# DISCOVERY_INVOCATION shares CI's target (`tests/`) and inherits the same
# pyproject.toml configuration -- including `addopts = "-m 'not live'"`, which
# deselects the suite's `live`-marked tests -- so what it finds is what CI runs.
# The reporting flags differ and do not change which tests are discovered; R22 is
# what fails if anything else about CI's command does.
# --------------------------------------------------------------------------

CI_WORKFLOW_PATH = ".github/workflows/test.yml"
CI_PYTEST_INVOCATION = (
    "uv run pytest tests/ -v --tb=short --cov --cov-report=xml "
    "--cov-report=term-missing"
)
DISCOVERY_INVOCATION = ("uv", "run", "pytest", "tests/", "--collect-only", "-q")

# --------------------------------------------------------------------------
# R12 / R14 / R20: the sentence rule.
# --------------------------------------------------------------------------

ABBREVIATIONS = ("e.g.", "i.e.", "cf.", "etc.", "vs.")

MARKER_RE = re.compile(r"\[C-\d\d\]")
NODE_ID_RE = re.compile(r"^tests/[A-Za-z0-9_./-]+\.py::test_[A-Za-z0-9_]+$")
INLINE_CODE_RE = re.compile(r"`[^`]*`")
LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
EMPHASIS_RE = re.compile(r"\*\*|__|~~|\*|_")
HEADING_RE = re.compile(r"^#{1,6}\s")
CLAIM_ID_RE = re.compile(r"^C-\d\d$")
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")

CODE_SENTINEL = "\ue000"
MARKER_SENTINEL = "\ue001"


@dataclass(frozen=True)
class Failure:
    rule: str
    name: str
    detail: str

    def __str__(self) -> str:
        return f"FAIL {self.rule} {self.name}: {self.detail}"


@dataclass(frozen=True)
class Row:
    claim_id: str
    kind: str
    proof: str
    line_no: int


@dataclass(frozen=True)
class Block:
    text: str
    line_no: int
    markers: tuple[str, ...]


# --------------------------------------------------------------------------
# R1 / R2: the two regions
# --------------------------------------------------------------------------


def _span(lines: list[str], begin: str, end: str) -> tuple[int, int] | None:
    starts = [i for i, ln in enumerate(lines) if ln.strip() == begin]
    ends = [i for i, ln in enumerate(lines) if ln.strip() == end]
    if len(starts) != 1 or len(ends) != 1 or starts[0] >= ends[0]:
        return None
    return starts[0], ends[0]


def _region_span(lines: list[str]) -> tuple[tuple[int, int] | None, list[Failure]]:
    span = _span(lines, REGION_BEGIN, REGION_END)
    if span is None:
        return None, [
            Failure(
                "R1",
                "region_missing",
                f"expected exactly one {REGION_BEGIN!r} followed by one {REGION_END!r}",
            )
        ]
    return span, []


def _split_cells(line: str) -> list[str]:
    stripped = line.strip()
    if not stripped.startswith("|") or not stripped.endswith("|"):
        return []
    return [cell.strip() for cell in stripped[1:-1].split("|")]


def _parse_ledger(
    region: list[str], offset: int
) -> tuple[tuple[int, int] | None, list[Row], list[Failure]]:
    span = _span(region, LEDGER_BEGIN, LEDGER_END)
    if span is None:
        return (
            None,
            [],
            [
                Failure(
                    "R2",
                    "ledger_missing",
                    f"expected exactly one {LEDGER_BEGIN!r} followed by one "
                    f"{LEDGER_END!r} inside the claim region",
                )
            ],
        )
    begin, end = span
    body = [(i, region[i]) for i in range(begin + 1, end) if region[i].strip()]
    if len(body) < 2:
        return (
            span,
            [],
            [Failure("R2", "ledger_missing", "the ledger carries no table")],
        )

    header_no, header = body[0]
    if tuple(_split_cells(header)) != LEDGER_HEADER:
        return (
            span,
            [],
            [
                Failure(
                    "R2",
                    "ledger_missing",
                    f"line {offset + header_no + 1}: the ledger is not the "
                    f"three-column `| Claim | Kind | Proof |` form",
                )
            ],
        )
    sep_no, separator = body[1]
    if [c.strip("- ") for c in _split_cells(separator)] != ["", "", ""]:
        return (
            span,
            [],
            [
                Failure(
                    "R2",
                    "ledger_missing",
                    f"line {offset + sep_no + 1}: expected the three-column "
                    f"separator row",
                )
            ],
        )

    rows: list[Row] = []
    for index, line in body[2:]:
        cells = _split_cells(line)
        if len(cells) != 3:
            return (
                span,
                [],
                [
                    Failure(
                        "R2",
                        "ledger_missing",
                        f"line {offset + index + 1}: not a three-column ledger row",
                    )
                ],
            )
        rows.append(Row(cells[0], cells[1], cells[2], offset + index + 1))
    return span, rows, []


# --------------------------------------------------------------------------
# R4: the claim ids
# --------------------------------------------------------------------------


def _check_claim_ids(rows: list[Row]) -> list[Failure]:
    ids = [row.claim_id for row in rows]
    expected = [f"C-{n:02d}" for n in range(1, len(ids) + 1)]
    bad = [i for i in ids if not CLAIM_ID_RE.match(i)]
    if bad:
        return [
            Failure("R4", "bad_claim_ids", f"not of the form C-nn: {', '.join(bad)}")
        ]
    if len(set(ids)) != len(ids):
        duplicated = sorted({i for i in ids if ids.count(i) > 1})
        return [Failure("R4", "bad_claim_ids", f"duplicated: {', '.join(duplicated)}")]
    if ids != expected:
        return [
            Failure(
                "R4",
                "bad_claim_ids",
                f"not the contiguous sequence C-01..C-{len(ids):02d} in table "
                f"order: got {', '.join(ids)}",
            )
        ]
    return []


# --------------------------------------------------------------------------
# R3 / R5 / R6 / R7 / R19: kinds and proofs
# --------------------------------------------------------------------------


def _parse_proof_entries(cell: str) -> tuple[list[str], bool]:
    """Split a Proof cell into node ids. The flag says whether it parsed cleanly."""
    remainder = cell.strip()
    entries: list[str] = []
    while remainder:
        match = INLINE_CODE_RE.match(remainder)
        if match is None:
            return entries, False
        entries.append(match.group(0)[1:-1].strip())
        remainder = remainder[match.end() :].lstrip()
        if remainder.startswith(","):
            remainder = remainder[1:].lstrip()
        elif remainder:
            return entries, False
    return entries, bool(entries)


def _check_rows(rows: list[Row]) -> tuple[dict[str, list[str]], list[Failure]]:
    """Validate every row and return {claim id: cited node ids}."""
    failures: list[Failure] = []
    cited: dict[str, list[str]] = {}

    for row in rows:
        cited[row.claim_id] = []
        if row.kind not in KINDS:
            failures.append(
                Failure(
                    "R3",
                    "bad_kind",
                    f"line {row.line_no}: {row.claim_id} has Kind {row.kind!r}; "
                    f"the vocabulary is exactly {KINDS[0]!r} and {KINDS[1]!r}",
                )
            )
            continue

        proof = row.proof.strip()
        is_characterization = proof.startswith(CHARACTERIZES)
        body = proof[len(CHARACTERIZES) :].strip() if is_characterization else proof

        if row.kind == "guarantee":
            if is_characterization:
                failures.append(
                    Failure(
                        "R19",
                        "characterization_in_a_guarantee",
                        f"line {row.line_no}: {row.claim_id} is a guarantee and "
                        f"uses the `characterizes:` form, which would dodge R5",
                    )
                )
                continue
            if not proof or proof == EM_DASH:
                failures.append(
                    Failure(
                        "R5",
                        "guarantee_without_proof",
                        f"line {row.line_no}: {row.claim_id} is a guarantee and "
                        f"cites no node id",
                    )
                )
                continue
        else:
            if proof == EM_DASH:
                continue
            if not is_characterization:
                failures.append(
                    Failure(
                        "R6",
                        "limitation_with_bare_proof",
                        f"line {row.line_no}: {row.claim_id} is a limitation; its "
                        f"Proof must be exactly {EM_DASH!r} or a "
                        f"`{CHARACTERIZES} <node ids>` list",
                    )
                )
                continue

        entries, clean = _parse_proof_entries(body)
        malformed = [e for e in entries if not NODE_ID_RE.match(e)]
        if not clean or malformed:
            failures.append(
                Failure(
                    "R7",
                    "malformed_node_id",
                    f"line {row.line_no}: {row.claim_id}'s Proof is not a "
                    f"comma-separated list of inline-code "
                    f"`tests/<path>.py::test_<name>` entries: {row.proof!r}",
                )
            )
            continue
        cited[row.claim_id] = entries

    return cited, failures


# --------------------------------------------------------------------------
# R13 / R21: what a line inside the region may be, and the claim blocks
# --------------------------------------------------------------------------


def _parse_region(
    region: list[str], ledger_span: tuple[int, int] | None, offset: int
) -> tuple[list[Block], list[str], list[Failure]]:
    """Classify every region line; return the claim blocks and stray markers."""
    failures: list[Failure] = []
    blocks: list[Block] = []
    markers: list[str] = []

    ledger_lines = (
        set(range(ledger_span[0], ledger_span[1] + 1)) if ledger_span else set()
    )

    current: list[str] | None = None
    current_line = 0

    def close() -> None:
        nonlocal current
        if current is not None:
            text = " ".join(part.strip() for part in current)
            blocks.append(
                Block(
                    text,
                    current_line,
                    tuple(m[1:-1] for m in MARKER_RE.findall(text)),
                )
            )
            current = None

    for index, raw in enumerate(region):
        line_no = offset + index + 1
        if index in ledger_lines:
            close()
            continue
        if not raw.strip():
            close()
            continue
        if HEADING_RE.match(raw):
            close()
            heading = raw.strip()
            markers.extend(m[1:-1] for m in MARKER_RE.findall(heading))
            if heading not in REGION_HEADINGS:
                failures.append(
                    Failure(
                        "R21",
                        "unlisted_heading",
                        f"line {line_no}: {heading!r} is not in the frozen region "
                        f"label set; a heading here is navigation, never assertion",
                    )
                )
            continue
        if raw.startswith("- "):
            close()
            current = [raw[2:]]
            current_line = line_no
            continue
        if current is not None and raw.startswith("  "):
            current.append(raw)
            continue
        close()
        failures.append(
            Failure(
                "R13",
                "illegal_line",
                f"line {line_no}: {raw.strip()!r} is not a heading, a blank line, "
                f"a claim block or the ledger",
            )
        )
    close()

    for block in blocks:
        markers.extend(block.markers)
    return blocks, markers, failures


# --------------------------------------------------------------------------
# R12 / R14 / R15 / R20: the sentence rule
# --------------------------------------------------------------------------


def _mask(text: str) -> str:
    """Four ordered steps, frozen in IF-0-SEAL-1.

    Markers are masked BEFORE links so `[C-07]` is not read as a bracket, and
    emphasis is deleted LAST, after the first three steps have removed every
    construct whose interior could contain a delimiter character. Step 4 exists
    because the second plan panel measured the three-step version failing on
    `- **No install child inherits credentials.** Package identities are checked
    [C-01].`: the period is followed by `**` rather than whitespace, so the
    splitter saw one sentence and the bolded assertion rode uncited.
    """
    text = INLINE_CODE_RE.sub(CODE_SENTINEL, text)
    text = MARKER_RE.sub(MARKER_SENTINEL, text)
    text = LINK_RE.sub(lambda m: m.group(1), text)
    return EMPHASIS_RE.sub("", text)


def _check_block(block: Block, kinds: dict[str, str]) -> list[Failure]:
    failures: list[Failure] = []
    text = block.text

    limitation_markers = [m for m in block.markers if kinds.get(m) == "limitation"]
    guarantee_markers = [m for m in block.markers if kinds.get(m) == "guarantee"]
    labelled = text.startswith(LIMITATION_LABEL)
    if limitation_markers and not labelled:
        failures.append(
            Failure(
                "R15",
                "limitation_not_labelled",
                f"line {block.line_no}: the block citing "
                f"{', '.join(limitation_markers)} must begin "
                f"`- {LIMITATION_LABEL} `",
            )
        )
    if guarantee_markers and labelled:
        failures.append(
            Failure(
                "R15",
                "limitation_not_labelled",
                f"line {block.line_no}: the block citing "
                f"{', '.join(guarantee_markers)} is a guarantee and must not "
                f"begin `- {LIMITATION_LABEL} `",
            )
        )

    # The mandated label is a legibility marker, not an assertion, so it is not
    # offered to the sentence split -- which would otherwise read `Limitation.`
    # as an uncited sentence in every limitation block.
    body = text[len(LIMITATION_LABEL) :] if labelled else text
    masked = _mask(body).strip()

    lowered = masked.lower()
    found = [a for a in ABBREVIATIONS if a in lowered]
    if re.search(r"\d\.\d", masked):
        found.append("a digit-period-digit run")
    if found:
        failures.append(
            Failure(
                "R14",
                "ambiguous_abbreviation",
                f"line {block.line_no}: {', '.join(found)} outside an inline code "
                f"span; the sentence split is total, so write it plainly",
            )
        )

    trimmed = masked.rstrip()
    for match in re.finditer(r"[.!?]", trimmed):
        following = trimmed[match.end() : match.end() + 1]
        if following and not following.isspace():
            failures.append(
                Failure(
                    "R20",
                    "terminator_not_followed_by_whitespace",
                    f"line {block.line_no}: {match.group(0)!r} is followed by "
                    f"{following!r} rather than whitespace or the block end; the "
                    f"ambiguity is refused rather than parsed",
                )
            )
            break

    for sentence in SENTENCE_SPLIT_RE.split(masked):
        if sentence.strip() and MARKER_SENTINEL not in sentence:
            readable = sentence.replace(CODE_SENTINEL, "`...`").strip()
            failures.append(
                Failure(
                    "R12",
                    "unmarked_sentence",
                    f"line {block.line_no}: {readable!r} carries no claim marker",
                )
            )
    return failures


# --------------------------------------------------------------------------
# R8: resolvability
# --------------------------------------------------------------------------


def _module_level_tests(path: Path) -> set[str] | None:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return None
    return {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _check_resolvable(
    node_ids: list[str], repo_root: Path
) -> tuple[set[str], list[Failure]]:
    failures: list[Failure] = []
    resolved: set[str] = set()
    cache: dict[str, set[str] | None] = {}

    for node_id in sorted(set(node_ids)):
        file, _, name = node_id.partition("::")
        if file not in cache:
            candidate = repo_root / file
            cache[file] = (
                _module_level_tests(candidate) if candidate.is_file() else None
            )
        names = cache[file]
        if names is None:
            failures.append(
                Failure(
                    "R8",
                    "unresolvable_node_id",
                    f"{node_id}: {file} does not exist or does not parse",
                )
            )
            continue
        if name not in names:
            failures.append(
                Failure(
                    "R8",
                    "unresolvable_node_id",
                    f"{node_id}: {name} is not a module-level def in {file} "
                    f"(a method inside a class is a different node id)",
                )
            )
            continue
        resolved.add(node_id)
    return resolved, failures


# --------------------------------------------------------------------------
# R9 / R22: CI's discovery
# --------------------------------------------------------------------------


def discover_ci_node_ids(repo_root: Path) -> set[str]:
    """What CI's own collection finds. Stubbed by the parser's own tests."""
    proc = subprocess.run(
        list(DISCOVERY_INVOCATION),
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    found = {line.strip() for line in proc.stdout.splitlines() if "::" in line}
    if not found:
        raise RuntimeError(
            f"{' '.join(DISCOVERY_INVOCATION)} discovered nothing "
            f"(exit {proc.returncode})"
        )
    return found


def _is_discovered(node_id: str, discovered: set[str]) -> bool:
    """R9's subset test, aware that pytest decorates a parametrised id.

    1791 of this suite's 3921 collected ids carry a `[param]` suffix, so a plain
    set membership test would reject `tests/f.py::test_x` whenever pytest
    discovers it only as `test_x[a]` and `test_x[b]` -- a false RED on roughly
    half the suite, and the kind of false alarm that gets a rule loosened under
    deadline. It weakens nothing: a file outside the `test_*.py` discovery
    pattern and a `live`-marked test that `addopts = "-m 'not live'"` deselects
    appear in the collection under NO id, decorated or otherwise, which is
    exactly what R9 exists to catch.
    """
    if node_id in discovered:
        return True
    prefix = node_id + "["
    return any(found.startswith(prefix) for found in discovered)


def _check_ci_invocation(repo_root: Path) -> list[Failure]:
    workflow = repo_root / CI_WORKFLOW_PATH
    if not workflow.is_file():
        return [
            Failure("R22", "ci_invocation_drift", f"{CI_WORKFLOW_PATH} is not readable")
        ]
    lines = [
        ln for ln in workflow.read_text(encoding="utf-8").splitlines() if "pytest" in ln
    ]
    if len(lines) != 1:
        return [
            Failure(
                "R22",
                "ci_invocation_drift",
                f"expected exactly one pytest invocation in {CI_WORKFLOW_PATH}, "
                f"found {len(lines)}",
            )
        ]
    _, separator, command = lines[0].partition("run:")
    invocation = (command if separator else lines[0]).strip()
    if invocation != CI_PYTEST_INVOCATION:
        return [
            Failure(
                "R22",
                "ci_invocation_drift",
                f"{CI_WORKFLOW_PATH} runs {invocation!r}; this checker mirrors "
                f"{CI_PYTEST_INVOCATION!r}. R9 is only as strong as that match, so "
                f"re-read the change and update this constant deliberately",
            )
        ]
    return []


# --------------------------------------------------------------------------
# R18: the lexicon, outside the region
# --------------------------------------------------------------------------


def _lexicon_patterns() -> list[tuple[str, re.Pattern[str]]]:
    """A lexicon term, its inflections, and nothing mid-word.

    The word boundary is kept on the LEFT, so a term can never match inside a
    longer word; the right-hand boundary is relaxed to `\\w*` so an inflected
    form is caught. IF-0-SEAL-1 froze this as a plain word-boundary match, which
    missed `approved`, `revoked`, `provisioned` and `consented` -- the most
    natural way to write the very sentence R18 exists to catch. Widened on the
    lead's ruling; see the report's freeze-deviation list.

    Still evaded, stated rather than implied: a stem-changing derivation such as
    `revocation` is not a suffix of `revoke` and matches nothing here. The
    lexicon is a strong filter, not a proof, and SL-5's one-time reading is what
    covers the residual.
    """
    return [
        (
            term,
            re.compile(
                rf"(?<![A-Za-z0-9_]){re.escape(term)}\w*",
                re.IGNORECASE,
            ),
        )
        for term in V13_LEXICON
    ]


def _check_region_external(lines: list[str], begin: int, end: int) -> list[Failure]:
    allowed = {line for line, _ in REGION_EXTERNAL_ALLOWLIST}
    patterns = _lexicon_patterns()
    failures: list[Failure] = []
    outside = [(i, lines[i]) for i in range(len(lines)) if i < begin or i > end]
    for index, raw in outside:
        stripped = raw.strip()
        if not stripped or stripped in allowed:
            continue
        hits = [term for term, pattern in patterns if pattern.search(stripped)]
        if hits:
            failures.append(
                Failure(
                    "R18",
                    "v13_term_outside_the_region",
                    f"line {index + 1}: {', '.join(hits)} — a v13 model property "
                    f"stated where no marker is required. Move the sentence into "
                    f"the claim region, or allowlist the line with its reason",
                )
            )
    return failures


# --------------------------------------------------------------------------
# The check
# --------------------------------------------------------------------------


@dataclass
class Result:
    failures: list[Failure]
    guarantee_ids: list[str]
    cited_union: list[str]


def check_document(path: Path) -> Result:
    repo_root = path.parent
    lines = path.read_text(encoding="utf-8").splitlines()

    span, failures = _region_span(lines)
    if span is None:
        return Result(failures, [], [])
    begin, end = span
    region = lines[begin + 1 : end]

    failures += _check_region_external(lines, begin, end)
    failures += _check_ci_invocation(repo_root)

    ledger_span, rows, ledger_failures = _parse_ledger(region, begin + 1)
    failures += ledger_failures
    if ledger_failures:
        return Result(failures, [], [])

    id_failures = _check_claim_ids(rows)
    if id_failures:
        return Result(failures + id_failures, [], [])

    cited, row_failures = _check_rows(rows)
    failures += row_failures

    kinds = {row.claim_id: row.kind for row in rows}
    blocks, markers, region_failures = _parse_region(region, ledger_span, begin + 1)
    failures += region_failures
    for block in blocks:
        failures += _check_block(block, kinds)

    declared = {row.claim_id for row in rows}
    for marker in sorted(set(markers) - declared):
        failures.append(
            Failure(
                "R11",
                "undeclared_marker",
                f"[{marker}] appears in the region with no ledger row",
            )
        )
    for claim_id in sorted(declared - set(markers)):
        failures.append(
            Failure("R10", "orphan_claim", f"{claim_id} is cited by no claim block")
        )

    guarantee_ids = sorted(
        {n for row in rows if row.kind == "guarantee" for n in cited[row.claim_id]}
    )
    union = sorted({n for ids in cited.values() for n in ids})

    resolved, resolve_failures = _check_resolvable(union, repo_root)
    failures += resolve_failures

    if resolved:
        try:
            discovered = discover_ci_node_ids(repo_root)
        except (RuntimeError, OSError) as exc:
            failures.append(
                Failure(
                    "R9",
                    "not_discovered_by_ci",
                    f"CI's own collection could not be reproduced: {exc}",
                )
            )
        else:
            undiscovered = {n for n in resolved if not _is_discovered(n, discovered)}
            for node_id in sorted(undiscovered):
                failures.append(
                    Failure(
                        "R9",
                        "not_discovered_by_ci",
                        f"{node_id} resolves but is not in the set "
                        f"`{' '.join(DISCOVERY_INVOCATION)}` discovers; CI would "
                        f"never run this proof",
                    )
                )

    cited_files = {n.partition("::")[0] for n in guarantee_ids}
    for file in V13_EVIDENCE_CENSUS:
        if file not in cited_files:
            failures.append(
                Failure(
                    "R16",
                    "uncited_evidence_file",
                    f"{file} is v13 evidence that no guarantee cites",
                )
            )
    for node_id in PREVIOUSLY_UNPROVEN_NODE_IDS:
        if node_id not in guarantee_ids:
            failures.append(
                Failure(
                    "R17",
                    "uncited_unproven_test",
                    f"{node_id} is one of the eleven node ids no merged phase's "
                    f"acceptance criteria run; citing it from a guarantee is what "
                    f"makes it required",
                )
            )

    return Result(failures, guarantee_ids, union)


# --------------------------------------------------------------------------
# The modes
# --------------------------------------------------------------------------


def _node_ids(path: Path) -> tuple[int, list[str]]:
    """The cited union: guarantee ids AND characterization ids.

    IF-0-SEAL-1 is explicit that characterization ids are emitted alongside
    guarantee ids "so R8, R9 and --run cover them identically". SL-1's lane prose
    says the opposite and justifies it with "a limitation contributes nothing by
    construction (R6)", which R6 itself contradicts. The freeze governs; the
    disagreement is reported upward.
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    span, failures = _region_span(lines)
    if span is None:
        for failure in failures:
            print(failure, file=sys.stderr)
        return 1, []
    begin, end = span
    ledger_span, rows, ledger_failures = _parse_ledger(
        lines[begin + 1 : end], begin + 1
    )
    if ledger_failures:
        for failure in ledger_failures:
            print(failure, file=sys.stderr)
        return 1, []
    cited, _ = _check_rows(rows)
    return 0, sorted({n for ids in cited.values() for n in ids})


def _run_union(path: Path, node_ids: list[str]) -> int:
    if not node_ids:
        print("FAIL --run: the ledger cites no node id to execute", file=sys.stderr)
        return 1
    # `-p no:cacheprovider` so a nested run -- the parser's own `--run` fixture
    # runs this from a tmp_path -- cannot write into a repository's pytest cache.
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *node_ids],
        cwd=path.parent,
        check=False,
    )
    if proc.returncode != 0:
        print(
            f"FAIL --run: the cited union did not pass (pytest exit "
            f"{proc.returncode}) over {len(node_ids)} node id(s): "
            f"{' '.join(node_ids)}",
            file=sys.stderr,
        )
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "path",
        nargs="?",
        default=str(Path(__file__).resolve().parent.parent / "SECURITY.md"),
        help="the SECURITY.md to check (its directory is the repository root)",
    )
    parser.add_argument(
        "--node-ids",
        action="store_true",
        help="print the cited union, one node id per line, sorted and deduplicated",
    )
    parser.add_argument(
        "--run",
        action="store_true",
        help="check, then execute the cited union once under pytest",
    )
    args = parser.parse_args(argv)

    path = Path(args.path)
    if not path.is_file():
        print(f"FAIL: {path} is not readable", file=sys.stderr)
        return 2

    if args.node_ids:
        code, node_ids = _node_ids(path)
        for node_id in node_ids:
            print(node_id)
        return code

    result = check_document(path)
    for failure in result.failures:
        print(failure)
    if result.failures:
        print(f"{len(result.failures)} failure(s) in {path}")
        return 1

    print(f"OK {path}: {len(result.cited_union)} cited node id(s)")
    if args.run:
        return _run_union(path, result.cited_union)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
