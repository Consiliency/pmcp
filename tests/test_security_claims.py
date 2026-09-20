"""Bind the shipped `SECURITY.md` to the claim checker, on the real file.

EC-SEAL-1 is a contract: "SECURITY.md describes the implemented model with no
claim the tests do not prove; each claim cites the test that proves it." SL-1's
`tests/test_security_claims_parser.py` proves the *checker* is correct against
synthetic fixtures in `tmp_path`; this file proves the *shipped document* passes
it, and pins the four legacy-sweep properties (census coverage, region-external
lexicon, packaged repository, subprocess caution) directly on the real file so a
future edit that overclaims goes red here rather than in a reviewer's head.

`scripts/check_security_claims.py` shells out to a full
`uv run pytest tests/ --collect-only -q` on every `check_document`, so the
result is computed once behind `functools.lru_cache` and the eight tests assert
different properties of that one `Result`. The keystone runs the real exit-code
path (`main`) once, which is the invocation a reviewer runs by hand.
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
import sys
from functools import lru_cache
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SECURITY_MD = _REPO_ROOT / "SECURITY.md"
_SCRIPT = _REPO_ROOT / "scripts" / "check_security_claims.py"


def _load_checker():
    """Import the standalone checker; `scripts/` is not a package."""
    spec = importlib.util.spec_from_file_location("check_security_claims", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_security_claims"] = module
    spec.loader.exec_module(module)
    return module


checker = _load_checker()


@lru_cache(maxsize=1)
def _result():
    """The one `check_document` call the whole suite shares."""
    return checker.check_document(_SECURITY_MD)


@lru_cache(maxsize=1)
def _ledger_rows():
    """The parsed ledger rows of the shipped file, via the checker's own parser."""
    lines = _SECURITY_MD.read_text(encoding="utf-8").splitlines()
    span, failures = checker._region_span(lines)
    assert span is not None, f"no claim region in {_SECURITY_MD}: {failures}"
    begin, end = span
    region = lines[begin + 1 : end]
    _, rows, ledger_failures = checker._parse_ledger(region, begin + 1)
    assert not ledger_failures, f"ledger did not parse: {ledger_failures}"
    return tuple(rows)


def _failures(*rules: str):
    return [f for f in _result().failures if f.rule in rules]


def test_the_shipped_security_md_passes_the_claim_checker() -> None:
    """The keystone: the checker exits 0 on the real file, structure through
    resolvability and the CI-discovery subset."""
    exit_code = checker.main([str(_SECURITY_MD)])
    assert exit_code == 0, "scripts/check_security_claims.py rejected SECURITY.md"


def test_every_cited_node_id_exists_and_collects() -> None:
    """R8/R9 on the shipped file: every cited node id resolves to a module-level
    test and is in the set CI's own collection discovers."""
    result = _result()
    assert result.cited_union, "the ledger cites no node id"
    assert not _failures("R8", "R9"), _failures("R8", "R9")


def test_every_v13_evidence_file_is_cited_by_a_guarantee() -> None:
    """R16 on the shipped file: each of the nineteen v13 evidence files backs at
    least one guarantee, so the document cannot cover a fraction of the model."""
    cited_files = {n.partition("::")[0] for n in _result().guarantee_ids}
    missing = [f for f in checker.V13_EVIDENCE_CENSUS if f not in cited_files]
    assert not missing, f"v13 evidence files cited by no guarantee: {missing}"
    assert not _failures("R16"), _failures("R16")


def test_every_limitation_is_labelled_and_cites_only_characterizations() -> None:
    """R6/R15/R19 on the shipped file: a limitation's Proof is either the em-dash
    or a `characterizes:` list, never a bare guarantee-style citation, and its
    block carries the `**Limitation.**` label."""
    limitations = [r for r in _ledger_rows() if r.kind == "limitation"]
    assert limitations, "the model states no limitation"
    for row in limitations:
        proof = row.proof.strip()
        assert proof == checker.EM_DASH or proof.startswith(checker.CHARACTERIZES), (
            f"{row.claim_id} limitation Proof is neither {checker.EM_DASH!r} nor a "
            f"characterization: {row.proof!r}"
        )
    assert not _failures("R6", "R15", "R19"), _failures("R6", "R15", "R19")


def test_no_v13_model_term_appears_outside_the_claim_region() -> None:
    """R18 on the shipped file: no line outside the region carries a v13 lexicon
    term except one on the checker's frozen allowlist."""
    assert not _failures("R18"), _failures("R18")


def test_no_guarantee_cites_a_test_outside_the_repository_suite() -> None:
    """The liveness precondition: every guarantee's proof lives under `tests/`,
    so CI's `pytest tests/` can run it. Necessary, not sufficient — R9's subset
    rule is what actually closes it, and is asserted above."""
    guarantee_ids = _result().guarantee_ids
    assert guarantee_ids, "no guarantee cites any node id"
    outside = [n for n in guarantee_ids if not n.startswith("tests/")]
    assert not outside, f"guarantee node ids outside tests/: {outside}"


def test_the_security_policy_names_the_packaged_repository() -> None:
    """No `ViperJuice` reference survives, and the advisory destination is the
    repository the distribution metadata names — the drift oracle, not a literal
    (following EC-EGRESS-3)."""
    text = _SECURITY_MD.read_text(encoding="utf-8")
    assert "ViperJuice" not in text, "a ViperJuice reference survives in SECURITY.md"

    urls = importlib.metadata.metadata("pmcp").get_all("Project-URL") or []
    repository_urls = [
        value.split(",", 1)[1].strip()
        for value in urls
        if value.split(",", 1)[0].strip().lower() == "repository"
    ]
    assert repository_urls, f"no Project-URL: Repository in metadata: {urls}"
    owner_name = "/".join(
        repository_urls[0].rstrip("/").removesuffix(".git").split("/")[-2:]
    )
    expected = f"https://github.com/{owner_name}/security/advisories/new"
    assert expected in text, f"the advisory URL does not name {owner_name}"


def test_the_subprocess_spawning_limitation_names_the_v13_gates() -> None:
    """R18 cannot catch a paraphrase, so the one model sentence naming no lexicon
    term is pinned by name. The subprocess-spawning caution now lives inside the
    region as a limitation and names the gates that changed the posture — a
    project entry gated by consent, a discovered package bound to an approved
    identity — rather than only 'configure servers you trust'."""
    lines = _SECURITY_MD.read_text(encoding="utf-8").splitlines()
    span, _ = checker._region_span(lines)
    assert span is not None
    begin, end = span
    region = "\n".join(lines[begin + 1 : end])

    block = next(
        (
            ln.strip()
            for ln in lines[begin + 1 : end]
            if "spawns a child process" in ln.lower() and ln.lstrip().startswith("-")
        ),
        None,
    )
    assert block is not None, "no subprocess-spawning block inside the claim region"
    assert block.startswith(f"- {checker.LIMITATION_LABEL}"), (
        f"the subprocess-spawning block must be a labelled limitation: {block!r}"
    )
    lowered = block.lower()
    assert "consent" in lowered, "it must name the consent gate"
    assert "approved identity" in lowered, "it must name the approved-identity bind"
    assert "trust" in lowered, "it must keep the residual caution"
    # The caution has not leaked back into the legacy prose outside the region.
    legacy = "\n".join(lines[:begin] + lines[end + 1 :])
    assert "Only configure\n  servers you trust" not in legacy
    assert region  # region parsed
