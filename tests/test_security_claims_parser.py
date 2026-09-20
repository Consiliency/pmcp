"""Falsify `scripts/check_security_claims.py` itself, one fixture per frozen rule.

EC-SEAL-1 is "SECURITY.md describes the implemented model with no claim the tests
do not prove". That criterion is only as good as the parser that enforces it, and
EGRESS amendment 5's lesson is that a structural check is code: a check nobody has
run is a check nobody has tested. So every rule R1-R22 frozen in IF-0-SEAL-1 gets a
doctored synthetic `SECURITY.md` here, and each asserts the checker names THAT rule
and exits non-zero.

Three deliberate properties of this file:

* **Never the shipped `SECURITY.md`.** Every fixture is built in `tmp_path`, with a
  complete miniature repository around it — a `tests/` tree the citations resolve
  against and a `.github/workflows/test.yml` R22 reads. The real file is SL-5's
  subject; a parser test that read it would go red for SL-5's reasons and green for
  the parser's.
* **The real suite is never run inside the suite.** R9 shells out to
  `uv run pytest tests/ --collect-only -q`; here `discover_ci_node_ids` is
  monkeypatched on the imported module, so no fixture spawns pytest over this
  repository. The single exception is `test_the_run_subcommand_fails_when_a_cited_test_fails`,
  which is the `--run` mode's whole point: it runs a nested pytest over the two
  one-line test files in its own `tmp_path`, with `-p no:cacheprovider` so the child
  cannot touch this repository's cache.
* **The census and the eleven previously-unproven node ids are written out here as
  literals**, not imported from the checker, and asserted equal to the checker's own
  constants. Importing them would make R16 and R17 tautological: a checker with a
  three-file census would pass its own test. Two registries that must agree is the
  defect class this phase exists to prevent, so this file is the second registry.
"""

from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_security_claims.py"


def _load_checker():
    spec = importlib.util.spec_from_file_location("check_security_claims", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_security_claims"] = module
    spec.loader.exec_module(module)
    return module


checker = _load_checker()


# --------------------------------------------------------------------------
# The frozen constants, restated from plans/phase-plan-v13-SEAL.md rather than
# imported from the checker. See the module docstring.
# --------------------------------------------------------------------------

# The nineteen v13 evidence files, from the plan's Context census (built with
# `git log --diff-filter=A --name-only aa2de50..d455a1f -- tests/`).
CENSUS = (
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

# The eleven lane-owned tests no merged phase's acceptance criteria run, from
# `python3 scripts/check_plan_consistency.py plans/phase-plan-v13-{CONSENT,TRUST}.md`.
UNPROVEN = (
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

# `.github/workflows/test.yml:54`, the invocation R9 mirrors and R22 guards.
CI_INVOCATION = (
    "uv run pytest tests/ -v --tb=short --cov --cov-report=xml "
    "--cov-report=term-missing"
)

WORKFLOW = """\
name: Test
on: [push]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - name: Run tests with coverage
        run: {invocation}
"""

# Region-external prose. The last line is the one entry on the checker's frozen
# allowlist -- the URL-mode elicitation bullet, which names `consent` about an
# OAuth flow rather than about the v13 consent gate -- so the well-formed fixture
# proves the allowlist is live as well as proving R18 does not fire spuriously.
PREAMBLE = """\
# Security Policy

PMCP does not terminate TLS and ships no default credentials.

- **URL-mode elicitation is out of band**: never paste OAuth codes, third-party
  passwords, or provider refresh tokens into gateway tools. `gateway.auth_connect`
  accepts API-key credentials only for local env-store flows; URL-mode flows only
  accept an elicitation identifier and consent acknowledgement.
"""

EPILOGUE = """\
Report a vulnerability through the advisory form on the packaged repository.
"""


def _code(node_id: str) -> str:
    return f"`{node_id}`"


def _proofs(node_ids) -> str:
    return ", ".join(_code(n) for n in node_ids)


# Every census file is cited through this one name, so R16 is satisfied by
# construction and a test that drops one citation falsifies exactly R16.
CENSUS_IDS = tuple(f"{path}::test_evidence" for path in CENSUS)

# Cited ONLY by the C-05 limitation row, so `--node-ids` including it proves the
# union carries characterization ids rather than merely repeating a guarantee's.
CHARACTERIZATION_ID = "tests/test_egress_panel_fixes.py::test_characterization"


def _default_rows() -> list[str]:
    return [
        f"| C-01 | guarantee | {_proofs(CENSUS_IDS + UNPROVEN)} |",
        "| C-02 | guarantee | "
        f"{_code('tests/test_project_consent_gate.py::test_evidence')} |",
        "| C-03 | guarantee | "
        f"{_code('tests/test_feedback_egress.py::test_evidence')} |",
        "| C-04 | limitation | — |",
        f"| C-05 | limitation | characterizes: {_code(CHARACTERIZATION_ID)} |",
    ]


DEFAULT_BLOCKS = """\
### The v13 trust model

#### Identity, not labels

- **A registered package is bound to its identity, not its label [C-01].** The
  gate refuses an arbitrary package published under an allowlisted name, and the
  refusal names the package and the command that would grant it [C-01].

#### Consent for repository-supplied configuration

- Repository-supplied configuration is applied only after the operator approves
  the exact bytes that are parsed [C-02].

#### Outbound actions

- Nothing leaves this process without an explicit confirmation, and the
  [provenance registry](https://example.invalid/registry) records what was sent
  [C-03].

#### Limitations

- **Limitation.** An operator approval pins `name@version` and does not bind the
  bytes the registry serves behind it [C-04].

- **Limitation.** A credential store file this process never read, written and
  removed out of band, is invisible to every provenance source [C-05].
"""


def _document(
    *,
    blocks: str = DEFAULT_BLOCKS,
    rows: list[str] | None = None,
    preamble: str = PREAMBLE,
    epilogue: str = EPILOGUE,
    region_begin: str = "<!-- TRUST-MODEL-CLAIMS: BEGIN -->",
    region_end: str = "<!-- TRUST-MODEL-CLAIMS: END -->",
    ledger_begin: str = "<!-- CLAIM-LEDGER: BEGIN -->",
    ledger_end: str = "<!-- CLAIM-LEDGER: END -->",
    header: str = "| Claim | Kind | Proof |",
    separator: str = "|---|---|---|",
) -> str:
    """Assemble a synthetic SECURITY.md. Every knob is a rule's falsifier."""
    rows = _default_rows() if rows is None else rows
    ledger = "\n".join([ledger_begin, header, separator, *rows, ledger_end])
    return "\n".join(
        [
            preamble,
            region_begin,
            blocks,
            ledger,
            region_end,
            "",
            epilogue,
        ]
    )


class Repo:
    """A miniature repository: a SECURITY.md, a tests/ tree, a CI workflow."""

    def __init__(self, path: Path, discovered: set[str]) -> None:
        self.path = path
        self.security_md = path / "SECURITY.md"
        self.discovered = discovered


def _discoverable(root: Path) -> set[str]:
    """Mirror pytest's discovery: module-level `test_*` in `tests/test_*.py`.

    Deliberately NOT a call to pytest -- this is the fixture's own model of what
    CI would find, and it excludes `tests/proof_helpers.py` (wrong filename) and
    class methods (a different node id) for the same reasons the real thing does.
    """
    found: set[str] = set()
    for file in sorted((root / "tests").glob("test_*.py")):
        tree = ast.parse(file.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name.startswith("test_"):
                    found.add(f"tests/{file.name}::{node.name}")
    return found


def _make_repo(
    tmp_path: Path, document: str | None = None, *, workflow: str = ""
) -> Repo:
    (tmp_path / "tests").mkdir(parents=True, exist_ok=True)
    for rel in CENSUS:
        body = [
            "def test_evidence():\n    pass\n",
            "def test_characterization():\n    pass\n",
        ]
        for node_id in UNPROVEN:
            file, _, name = node_id.partition("::")
            if file == rel:
                body.append(f"def {name}():\n    pass\n")
        if rel == "tests/test_trust_store.py":
            # R8's method case: resolves for a regex, must not for ast.parse.
            body.append(
                "class TestResidency:\n    def test_inside_a_class(self):\n        pass\n"
            )
        (tmp_path / rel).write_text("\n".join(body), encoding="utf-8")

    # R9's case: collects when named explicitly, invisible to `pytest tests/`.
    (tmp_path / "tests" / "proof_helpers.py").write_text(
        "def test_boundary():\n    pass\n", encoding="utf-8"
    )
    # `--run`'s case.
    (tmp_path / "tests" / "test_failing_evidence.py").write_text(
        "def test_it_fails():\n    assert False\n", encoding="utf-8"
    )

    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True, exist_ok=True)
    (workflows / "test.yml").write_text(
        workflow or WORKFLOW.format(invocation=CI_INVOCATION), encoding="utf-8"
    )

    (tmp_path / "SECURITY.md").write_text(
        document if document is not None else _document(), encoding="utf-8"
    )
    return Repo(tmp_path, _discoverable(tmp_path))


def _check(
    monkeypatch,
    capsys,
    repo: Repo,
    *argv: str,
    discovered: set[str] | None = None,
) -> tuple[int, str]:
    """Run the checker in-process with CI discovery stubbed."""
    found = set(repo.discovered if discovered is None else discovered)
    monkeypatch.setattr(checker, "discover_ci_node_ids", lambda root: set(found))
    code = checker.main([*argv, str(repo.security_md)])
    captured = capsys.readouterr()
    return code, captured.out + captured.err


def _assert_reports(output: str, rule: str, name: str) -> None:
    assert rule in output, f"expected rule {rule} in:\n{output}"
    assert name in output, f"expected failure name {name!r} in:\n{output}"


# --------------------------------------------------------------------------
# The positive case
# --------------------------------------------------------------------------


def test_a_wellformed_ledger_passes_every_rule(tmp_path, monkeypatch, capsys):
    repo = _make_repo(tmp_path)
    code, out = _check(monkeypatch, capsys, repo)
    assert code == 0, out
    assert "FAIL" not in out


# --------------------------------------------------------------------------
# R1-R2: the two regions
# --------------------------------------------------------------------------


def test_a_missing_claim_region_is_reported(tmp_path, monkeypatch, capsys):
    absent = _make_repo(tmp_path / "absent", _document(region_begin=""))
    code, out = _check(monkeypatch, capsys, absent)
    assert code != 0
    _assert_reports(out, "R1", "region_missing")

    doubled_text = _document()
    doubled_text = doubled_text.replace(
        "<!-- TRUST-MODEL-CLAIMS: BEGIN -->",
        "<!-- TRUST-MODEL-CLAIMS: BEGIN -->\n<!-- TRUST-MODEL-CLAIMS: BEGIN -->",
    )
    doubled = _make_repo(tmp_path / "doubled", doubled_text)
    code, out = _check(monkeypatch, capsys, doubled)
    assert code != 0
    _assert_reports(out, "R1", "region_missing")

    swapped = _make_repo(
        tmp_path / "swapped",
        _document(
            region_begin="<!-- TRUST-MODEL-CLAIMS: END -->",
            region_end="<!-- TRUST-MODEL-CLAIMS: BEGIN -->",
        ),
    )
    code, out = _check(monkeypatch, capsys, swapped)
    assert code != 0
    _assert_reports(out, "R1", "region_missing")


def test_a_missing_or_duplicated_ledger_is_reported(tmp_path, monkeypatch, capsys):
    absent = _make_repo(tmp_path / "absent", _document(ledger_begin=""))
    code, out = _check(monkeypatch, capsys, absent)
    assert code != 0
    _assert_reports(out, "R2", "ledger_missing")

    doubled_text = _document().replace(
        "<!-- CLAIM-LEDGER: END -->",
        "<!-- CLAIM-LEDGER: END -->\n<!-- CLAIM-LEDGER: END -->",
    )
    doubled = _make_repo(tmp_path / "doubled", doubled_text)
    code, out = _check(monkeypatch, capsys, doubled)
    assert code != 0
    _assert_reports(out, "R2", "ledger_missing")

    two_column = _make_repo(
        tmp_path / "shape",
        _document(header="| Claim | Proof |", separator="|---|---|"),
    )
    code, out = _check(monkeypatch, capsys, two_column)
    assert code != 0
    _assert_reports(out, "R2", "ledger_missing")


# --------------------------------------------------------------------------
# R3-R7, R19: the ledger rows
# --------------------------------------------------------------------------


def test_a_kind_outside_the_two_value_vocabulary_is_reported(
    tmp_path, monkeypatch, capsys
):
    rows = _default_rows()
    rows[2] = rows[2].replace("| guarantee |", "| context |")
    repo = _make_repo(tmp_path, _document(rows=rows))
    code, out = _check(monkeypatch, capsys, repo)
    assert code != 0
    _assert_reports(out, "R3", "bad_kind")


def test_duplicate_and_non_sequential_claim_ids_are_reported(
    tmp_path, monkeypatch, capsys
):
    duplicated = _default_rows()
    duplicated[2] = duplicated[2].replace("| C-03 |", "| C-02 |")
    repo = _make_repo(tmp_path / "duplicate", _document(rows=duplicated))
    code, out = _check(monkeypatch, capsys, repo)
    assert code != 0
    _assert_reports(out, "R4", "bad_claim_ids")

    gapped = _default_rows()
    gapped[4] = gapped[4].replace("| C-05 |", "| C-06 |")
    blocks = DEFAULT_BLOCKS.replace("[C-05]", "[C-06]")
    repo = _make_repo(tmp_path / "gap", _document(rows=gapped, blocks=blocks))
    code, out = _check(monkeypatch, capsys, repo)
    assert code != 0
    _assert_reports(out, "R4", "bad_claim_ids")


def test_a_guarantee_without_a_proof_is_reported(tmp_path, monkeypatch, capsys):
    rows = _default_rows()
    rows[2] = "| C-03 | guarantee | — |"
    repo = _make_repo(tmp_path / "dash", _document(rows=rows))
    code, out = _check(monkeypatch, capsys, repo)
    assert code != 0
    _assert_reports(out, "R5", "guarantee_without_proof")

    rows = _default_rows()
    rows[2] = "| C-03 | guarantee |  |"
    repo = _make_repo(tmp_path / "empty", _document(rows=rows))
    code, out = _check(monkeypatch, capsys, repo)
    assert code != 0
    _assert_reports(out, "R5", "guarantee_without_proof")


def test_a_proof_entry_that_is_not_a_node_id_is_reported(tmp_path, monkeypatch, capsys):
    rows = _default_rows()
    rows[2] = "| C-03 | guarantee | the whole egress suite |"
    repo = _make_repo(tmp_path / "prose", _document(rows=rows))
    code, out = _check(monkeypatch, capsys, repo)
    assert code != 0
    _assert_reports(out, "R7", "malformed_node_id")

    rows = _default_rows()
    rows[2] = "| C-03 | guarantee | `scripts/check_workflows.py::test_evidence` |"
    repo = _make_repo(tmp_path / "outside", _document(rows=rows))
    code, out = _check(monkeypatch, capsys, repo)
    assert code != 0
    _assert_reports(out, "R7", "malformed_node_id")


def test_a_limitation_carrying_an_uncharacterized_proof_is_reported(
    tmp_path, monkeypatch, capsys
):
    rows = _default_rows()
    rows[3] = (
        "| C-04 | limitation | "
        f"{_code('tests/test_package_approvals.py::test_evidence')} |"
    )
    repo = _make_repo(tmp_path, _document(rows=rows))
    code, out = _check(monkeypatch, capsys, repo)
    assert code != 0
    _assert_reports(out, "R6", "limitation_with_bare_proof")


def test_a_limitation_may_cite_a_characterization_and_nothing_else(
    tmp_path, monkeypatch, capsys
):
    """The form the freeze deliberately ALLOWS: an uncited limitation rots."""
    rows = _default_rows()
    rows[3] = (
        "| C-04 | limitation | characterizes: "
        + _proofs(
            (
                "tests/test_package_approvals.py::test_evidence",
                "tests/test_egress_panel_fixes.py::test_evidence",
            )
        )
        + " |"
    )
    repo = _make_repo(tmp_path, _document(rows=rows))
    code, out = _check(monkeypatch, capsys, repo)
    assert code == 0, out
    assert "limitation_with_bare_proof" not in out


def test_a_guarantee_may_not_cite_a_characterization(tmp_path, monkeypatch, capsys):
    rows = _default_rows()
    rows[2] = (
        "| C-03 | guarantee | characterizes: "
        f"{_code('tests/test_feedback_egress.py::test_evidence')} |"
    )
    repo = _make_repo(tmp_path, _document(rows=rows))
    code, out = _check(monkeypatch, capsys, repo)
    assert code != 0
    _assert_reports(out, "R19", "characterization_in_a_guarantee")


# --------------------------------------------------------------------------
# R8-R9: resolvability, and the discovery subset
# --------------------------------------------------------------------------


def test_a_node_id_naming_a_missing_file_is_reported(tmp_path, monkeypatch, capsys):
    rows = _default_rows()
    rows[2] = "| C-03 | guarantee | `tests/test_not_written_yet.py::test_evidence` |"
    repo = _make_repo(tmp_path, _document(rows=rows))
    code, out = _check(monkeypatch, capsys, repo)
    assert code != 0
    _assert_reports(out, "R8", "unresolvable_node_id")


def test_a_node_id_naming_a_missing_function_is_reported(tmp_path, monkeypatch, capsys):
    rows = _default_rows()
    rows[2] = "| C-03 | guarantee | `tests/test_feedback_egress.py::test_absent` |"
    repo = _make_repo(tmp_path, _document(rows=rows))
    code, out = _check(monkeypatch, capsys, repo)
    assert code != 0
    _assert_reports(out, "R8", "unresolvable_node_id")


def test_a_node_id_naming_a_method_inside_a_class_is_reported(
    tmp_path, monkeypatch, capsys
):
    rows = _default_rows()
    rows[2] = "| C-03 | guarantee | `tests/test_trust_store.py::test_inside_a_class` |"
    repo = _make_repo(tmp_path, _document(rows=rows))
    code, out = _check(monkeypatch, capsys, repo)
    assert code != 0
    _assert_reports(out, "R8", "unresolvable_node_id")


def test_a_cited_test_that_ci_would_not_discover_is_reported(
    tmp_path, monkeypatch, capsys
):
    """The second panel's B2: resolves for R7 and R8, and CI never runs it."""
    rows = _default_rows()
    rows[2] = "| C-03 | guarantee | `tests/proof_helpers.py::test_boundary` |"
    repo = _make_repo(tmp_path, _document(rows=rows))
    assert "tests/proof_helpers.py::test_boundary" not in repo.discovered
    code, out = _check(monkeypatch, capsys, repo)
    assert code != 0
    _assert_reports(out, "R9", "not_discovered_by_ci")
    assert "unresolvable_node_id" not in out, (
        "the citation resolves; R9 must be what rejects it"
    )


def test_the_checkers_discovery_invocation_matches_the_ci_workflow(
    tmp_path, monkeypatch, capsys
):
    """R22, both halves: the live drift guard, and the rule's own failure name."""
    real_workflow = (
        Path(__file__).resolve().parents[1] / ".github" / "workflows" / "test.yml"
    )
    lines = [ln for ln in real_workflow.read_text().splitlines() if "pytest" in ln]
    assert len(lines) == 1, f"expected one pytest invocation, got {lines}"
    assert lines[0].split("run:", 1)[1].strip() == CI_INVOCATION
    assert checker.CI_PYTEST_INVOCATION == CI_INVOCATION

    narrowed = WORKFLOW.format(invocation=CI_INVOCATION + " -k not_slow")
    repo = _make_repo(tmp_path, workflow=narrowed)
    code, out = _check(monkeypatch, capsys, repo)
    assert code != 0
    _assert_reports(out, "R22", "ci_invocation_drift")


def test_a_parametrised_citation_is_discovered_but_a_missing_one_is_not(
    tmp_path, monkeypatch, capsys
):
    """R9 must accept the bare id of a parametrised test, and nothing more.

    NOT one of SL-1's thirty frozen node ids -- added because 1791 of this
    suite's 3921 collected ids carry a `[param]` suffix, so an exact-set subset
    test would have rejected a citation of any of them. `--node-ids` emits the
    bare form (R7 freezes it), so without this R9 was a false RED on roughly half
    the suite. This test is the guard on the widening: the decorated form counts,
    a merely similar prefix does not.
    """
    rows = _default_rows()
    rows[2] = "| C-03 | guarantee | `tests/test_feedback_egress.py::test_evidence` |"
    repo = _make_repo(tmp_path, _document(rows=rows))

    parametrised = {
        n
        for n in repo.discovered
        if n != "tests/test_feedback_egress.py::test_evidence"
    } | {
        "tests/test_feedback_egress.py::test_evidence[first]",
        "tests/test_feedback_egress.py::test_evidence[second]",
    }
    code, out = _check(monkeypatch, capsys, repo, discovered=parametrised)
    assert code == 0, out

    # The widening is a `[` suffix, not a prefix match: a longer name is still
    # a different test and must still fail.
    renamed = {
        n
        for n in repo.discovered
        if n != "tests/test_feedback_egress.py::test_evidence"
    } | {"tests/test_feedback_egress.py::test_evidence_extended"}
    code, out = _check(monkeypatch, capsys, repo, discovered=renamed)
    assert code != 0
    _assert_reports(out, "R9", "not_discovered_by_ci")


# --------------------------------------------------------------------------
# R10-R11: markers and rows must correspond
# --------------------------------------------------------------------------


def test_a_claim_marker_with_no_ledger_row_is_reported(tmp_path, monkeypatch, capsys):
    blocks = DEFAULT_BLOCKS.replace(
        "the exact bytes that are parsed [C-02].",
        "the exact bytes that are parsed [C-02] [C-09].",
    )
    repo = _make_repo(tmp_path, _document(blocks=blocks))
    code, out = _check(monkeypatch, capsys, repo)
    assert code != 0
    _assert_reports(out, "R11", "undeclared_marker")


def test_a_ledger_row_that_no_block_cites_is_reported(tmp_path, monkeypatch, capsys):
    blocks = DEFAULT_BLOCKS.replace(
        "- Nothing leaves this process without an explicit confirmation, and the\n"
        "  [provenance registry](https://example.invalid/registry) records what was sent\n"
        "  [C-03].\n",
        "",
    )
    assert "[C-03]" not in blocks
    repo = _make_repo(tmp_path, _document(blocks=blocks))
    code, out = _check(monkeypatch, capsys, repo)
    assert code != 0
    _assert_reports(out, "R10", "orphan_claim")


# --------------------------------------------------------------------------
# R12, R14, R20: the sentence rule
# --------------------------------------------------------------------------


def test_a_sentence_without_a_claim_marker_is_reported(tmp_path, monkeypatch, capsys):
    blocks = DEFAULT_BLOCKS + "\n- The store is consulted on every load.\n"
    repo = _make_repo(tmp_path, _document(blocks=blocks))
    code, out = _check(monkeypatch, capsys, repo)
    assert code != 0
    _assert_reports(out, "R12", "unmarked_sentence")


def test_a_second_sentence_in_a_block_must_carry_its_own_marker(
    tmp_path, monkeypatch, capsys
):
    blocks = DEFAULT_BLOCKS.replace(
        "the exact bytes that are parsed [C-02].",
        "the exact bytes that are parsed [C-02]. The refusal is logged once.",
    )
    repo = _make_repo(tmp_path, _document(blocks=blocks))
    code, out = _check(monkeypatch, capsys, repo)
    assert code != 0
    _assert_reports(out, "R12", "unmarked_sentence")


def test_a_bolded_sentence_before_another_is_split_and_must_be_cited(
    tmp_path, monkeypatch, capsys
):
    """The second panel's measured hole, verbatim.

    `(?<=[.!?])\\s+` does not split after `credentials.` because the period is
    followed by `**`, so the bolded assertion -- the strongest in the block --
    rode uncited on the second sentence's marker. Step 4 of the masking is what
    makes this fire; the italic spelling failed identically and is asserted too.
    """
    bolded = (
        "- **No install child inherits credentials.** Package identities are "
        "checked [C-03].\n"
    )
    blocks = DEFAULT_BLOCKS + "\n" + bolded
    repo = _make_repo(tmp_path / "bold", _document(blocks=blocks))
    code, out = _check(monkeypatch, capsys, repo)
    assert code != 0
    _assert_reports(out, "R12", "unmarked_sentence")

    italic = "- *Nothing is submitted by default.* The gate refuses [C-03].\n"
    repo = _make_repo(
        tmp_path / "italic", _document(blocks=DEFAULT_BLOCKS + "\n" + italic)
    )
    code, out = _check(monkeypatch, capsys, repo)
    assert code != 0
    _assert_reports(out, "R12", "unmarked_sentence")


def test_a_terminator_followed_by_unexpected_markup_is_reported(
    tmp_path, monkeypatch, capsys
):
    """R20: refuse the ambiguity rather than enumerate the markup."""
    blocks = DEFAULT_BLOCKS + (
        "\n- The gate refuses an unapproved source [C-03].<br>It is logged once "
        "[C-03].\n"
    )
    repo = _make_repo(tmp_path, _document(blocks=blocks))
    code, out = _check(monkeypatch, capsys, repo)
    assert code != 0
    _assert_reports(out, "R20", "terminator_not_followed_by_whitespace")


def test_an_abbreviation_that_breaks_the_sentence_split_is_reported(
    tmp_path, monkeypatch, capsys
):
    blocks = DEFAULT_BLOCKS + (
        "\n- Every project source is gated, e.g. the overlay the checkout ships "
        "[C-03].\n"
    )
    repo = _make_repo(tmp_path / "abbrev", _document(blocks=blocks))
    code, out = _check(monkeypatch, capsys, repo)
    assert code != 0
    _assert_reports(out, "R14", "ambiguous_abbreviation")

    numeric = DEFAULT_BLOCKS + (
        "\n- A refusal is emitted within 0.5 seconds of the read [C-03].\n"
    )
    repo = _make_repo(tmp_path / "numeric", _document(blocks=numeric))
    code, out = _check(monkeypatch, capsys, repo)
    assert code != 0
    _assert_reports(out, "R14", "ambiguous_abbreviation")


# --------------------------------------------------------------------------
# R13, R21: what a line in the region may be
# --------------------------------------------------------------------------


def test_a_line_that_is_neither_heading_nor_claim_block_is_reported(
    tmp_path, monkeypatch, capsys
):
    blocks = DEFAULT_BLOCKS + (
        "\nThe paragraphs below explain the reasoning behind the model.\n"
    )
    repo = _make_repo(tmp_path, _document(blocks=blocks))
    code, out = _check(monkeypatch, capsys, repo)
    assert code != 0
    _assert_reports(out, "R13", "illegal_line")


def test_a_heading_outside_the_reviewed_label_set_is_reported(
    tmp_path, monkeypatch, capsys
):
    """A heading is navigation, never assertion -- and R18 does not reach inside."""
    blocks = DEFAULT_BLOCKS.replace(
        "#### Outbound actions",
        "### The install spawn strips every credential PMCP stores",
    )
    repo = _make_repo(tmp_path, _document(blocks=blocks))
    code, out = _check(monkeypatch, capsys, repo)
    assert code != 0
    _assert_reports(out, "R21", "unlisted_heading")


# --------------------------------------------------------------------------
# R15-R18: labels, the census, the unproven eleven, the lexicon
# --------------------------------------------------------------------------


def test_a_limitation_block_must_be_labelled_as_one(tmp_path, monkeypatch, capsys):
    unlabelled = DEFAULT_BLOCKS.replace(
        "- **Limitation.** An operator approval pins", "- An operator approval pins"
    )
    repo = _make_repo(tmp_path / "unlabelled", _document(blocks=unlabelled))
    code, out = _check(monkeypatch, capsys, repo)
    assert code != 0
    _assert_reports(out, "R15", "limitation_not_labelled")

    mislabelled = DEFAULT_BLOCKS.replace(
        "- Repository-supplied configuration is applied",
        "- **Limitation.** Repository-supplied configuration is applied",
    )
    repo = _make_repo(tmp_path / "mislabelled", _document(blocks=mislabelled))
    code, out = _check(monkeypatch, capsys, repo)
    assert code != 0
    _assert_reports(out, "R15", "limitation_not_labelled")


def test_every_v13_evidence_file_must_be_cited(tmp_path, monkeypatch, capsys):
    assert tuple(checker.V13_EVIDENCE_CENSUS) == CENSUS, (
        "the checker's census and this file's must agree -- two registries, "
        "reconciled by the test rather than by reading"
    )
    dropped = "tests/test_pkgid_spawn_logging.py::test_evidence"
    kept = tuple(n for n in CENSUS_IDS if n != dropped)
    rows = _default_rows()
    rows[0] = f"| C-01 | guarantee | {_proofs(kept + UNPROVEN)} |"
    repo = _make_repo(tmp_path, _document(rows=rows))
    code, out = _check(monkeypatch, capsys, repo)
    assert code != 0
    _assert_reports(out, "R16", "uncited_evidence_file")
    assert "tests/test_pkgid_spawn_logging.py" in out


def test_a_previously_unproven_test_must_be_cited(tmp_path, monkeypatch, capsys):
    assert tuple(checker.PREVIOUSLY_UNPROVEN_NODE_IDS) == UNPROVEN, (
        "the eleven node ids no merged phase's criteria run are a frozen constant"
    )
    dropped = "tests/test_trust_store.py::test_a_denied_record_is_not_approved"
    kept = tuple(n for n in UNPROVEN if n != dropped)
    rows = _default_rows()
    rows[0] = f"| C-01 | guarantee | {_proofs(CENSUS_IDS + kept)} |"
    repo = _make_repo(tmp_path, _document(rows=rows))
    code, out = _check(monkeypatch, capsys, repo)
    assert code != 0
    _assert_reports(out, "R17", "uncited_unproven_test")
    assert dropped in out
    assert "uncited_evidence_file" not in out, (
        "the file is still cited; only the node id was dropped"
    )


def test_a_v13_model_term_outside_the_region_is_reported(tmp_path, monkeypatch, capsys):
    smuggled = EPILOGUE + "\nThe trust store lives outside any repository.\n"
    repo = _make_repo(tmp_path, _document(epilogue=smuggled))
    code, out = _check(monkeypatch, capsys, repo)
    assert code != 0
    _assert_reports(out, "R18", "v13_term_outside_the_region")
    assert "trust store" in out
    assert "elicitation identifier" not in out, (
        "the allowlisted URL-mode bullet must stay exempt"
    )


def test_an_inflected_v13_term_outside_the_region_is_reported(
    tmp_path, monkeypatch, capsys
):
    """R18 must catch the inflected form, which is how the sentence gets written.

    NOT one of SL-1's thirty frozen node ids. IF-0-SEAL-1 froze R18 as a plain
    word-boundary match, so `approved`, `revoked`, `provisioned` and `consented`
    matched nothing -- the most natural spelling of the very sentence the rule
    exists to catch walked straight past it. Widened on the lead's ruling: the
    boundary is kept on the LEFT so a term still cannot match mid-word, which is
    what the second half of this test pins.
    """
    smuggled = EPILOGUE + "\nAn approved source is applied on the next startup.\n"
    repo = _make_repo(tmp_path / "inflected", _document(epilogue=smuggled))
    code, out = _check(monkeypatch, capsys, repo)
    assert code != 0
    _assert_reports(out, "R18", "v13_term_outside_the_region")

    # The left boundary still holds: a term inside a longer word is not a hit.
    innocent = EPILOGUE + "\nDisapproval of the design was noted in review.\n"
    repo = _make_repo(tmp_path / "midword", _document(epilogue=innocent))
    code, out = _check(monkeypatch, capsys, repo)
    assert code == 0, out


# --------------------------------------------------------------------------
# The two modes
# --------------------------------------------------------------------------


def test_the_node_ids_subcommand_prints_the_cited_union(tmp_path, monkeypatch, capsys):
    """The union is guarantee ids AND characterization ids (IF-0-SEAL-1).

    The freeze is explicit that `--node-ids` emits characterization ids alongside
    guarantee ids "so R8, R9 and --run cover them identically"; SL-1's lane prose
    says the opposite. The freeze governs, and this assertion is what pins it.
    """
    repo = _make_repo(tmp_path)
    code, out = _check(monkeypatch, capsys, repo, "--node-ids")
    assert code == 0, out
    printed = [ln for ln in out.splitlines() if ln.strip()]
    assert printed == sorted(set(printed)), "sorted and deduplicated"
    assert "tests/test_feedback_egress.py::test_evidence" in printed
    assert CHARACTERIZATION_ID in printed, (
        "a characterization id cited by no guarantee must still be emitted"
    )
    assert CHARACTERIZATION_ID not in _proofs(CENSUS_IDS + UNPROVEN)
    for node_id in CENSUS_IDS + UNPROVEN:
        assert node_id in printed
    assert all(p.startswith("tests/") and "::" in p for p in printed)
    assert "tests/test_failing_evidence.py::test_it_fails" not in printed


def test_the_run_subcommand_fails_when_a_cited_test_fails(
    tmp_path, monkeypatch, capsys
):
    """`--run` is a gate at phase close: it executes the union once.

    The nested pytest here runs the two one-line modules in this fixture's own
    tmp_path, never this repository's suite, and `-p no:cacheprovider` keeps it
    off this repository's cache.
    """
    rows = _default_rows()
    rows[2] = "| C-03 | guarantee | `tests/test_failing_evidence.py::test_it_fails` |"
    repo = _make_repo(tmp_path, _document(rows=rows))

    passing, _ = _check(monkeypatch, capsys, repo)
    assert passing == 0, "the document itself must be structurally clean"

    code, out = _check(monkeypatch, capsys, repo, "--run")
    assert code != 0
    assert "--run" in out
    assert "tests/test_failing_evidence.py::test_it_fails" in out


if __name__ == "__main__":  # pragma: no cover - convenience only
    raise SystemExit(pytest.main([__file__, "-q"]))
