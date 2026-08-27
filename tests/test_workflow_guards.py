"""Guards for the guards: proof that ``scripts/check_workflows.py`` kills the
mutants it claims to, and that ``test.yml`` wires it up in a way that can fail.

Consiliency/pmcp#189. ``release.yml`` triggers only on tag push, so nothing in
it is ever exercised by a PR check — the tag push *is* the publish. Each test
below corresponds to a named mutant in ``scripts/_mutate_workflow.sh``, which
is the single source of the mutant set;
``TestMutationHelperContract`` asserts the two lists agree rather than trusting
that they do.
"""

from __future__ import annotations

import copy
import importlib.util
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = REPO_ROOT / ".github" / "workflows"
RELEASE_YML = WORKFLOW_DIR / "release.yml"
TEST_YML = WORKFLOW_DIR / "test.yml"
MUTATE_SH = REPO_ROOT / "scripts" / "_mutate_workflow.sh"


def _load_checker() -> Any:
    spec = importlib.util.spec_from_file_location(
        "check_workflows", REPO_ROOT / "scripts" / "check_workflows.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cw = _load_checker()

_REAL_RELEASE = yaml.safe_load(RELEASE_YML.read_text())


def release_doc() -> dict[str, Any]:
    return copy.deepcopy(_REAL_RELEASE)


def _publish(doc: dict[str, Any]) -> dict[str, Any]:
    return doc["jobs"]["publish"]


def _pypi_step(doc: dict[str, Any]) -> dict[str, Any]:
    for step in _publish(doc)["steps"]:
        if str(step.get("uses", "")).startswith("pypa/"):
            return step
    raise AssertionError("release.yml no longer has a pypa/ publish step")


def _on(doc: dict[str, Any]) -> dict[str, Any]:
    return cw.get_on(doc)


# The exact expression scripts/_mutate_workflow.sh injects for the `if:`
# mutants. Asserted against the shell source in TestMutationHelperContract, so
# the in-memory mutation and the committed one cannot drift apart.
_IF_EXPR = "${{ github.actor != 'nobody' }}"

# Each key is a mutant name in scripts/_mutate_workflow.sh; the callable is the
# same edit applied to the parsed document.
RELEASE_MUTANTS: dict[str, Any] = {
    "tag-case": lambda d: _on(d)["push"].update({"tags": ["V*"]}),
    "tags-deleted": lambda d: _on(d).update({"push": {"branches": ["main"]}}),
    # The trigger set is a SET, not one key with an allowlist. `publish` holds
    # id-token: write against an environment with no protection rules, so an
    # extra trigger makes trusted publishing reachable from that event.
    "trigger-pull-request-added": lambda d: _on(d).update({"pull_request": {}}),
    "trigger-workflow-dispatch-added": lambda d: _on(d).update(
        {"workflow_dispatch": None}
    ),
    "trigger-push-branches-added": lambda d: _on(d)["push"].update(
        {"branches": ["main"]}
    ),
    "trigger-paths-filter-added": lambda d: _on(d)["push"].update({"paths": ["**"]}),
    "env-dropped": lambda d: _publish(d).pop("environment"),
    "env-renamed": lambda d: _publish(d).update({"environment": "dev"}),
    "needs-build-dropped": lambda d: _publish(d).pop("needs"),
    "needs-publish-to-build": lambda d: d["jobs"]["github-release"].update(
        {"needs": "build"}
    ),
    "continue-on-error-job": lambda d: _publish(d).update({"continue-on-error": True}),
    "continue-on-error-step": lambda d: _pypi_step(d).update(
        {"continue-on-error": True}
    ),
    # The expression must be the SAME STRING the shell helper injects; these two
    # had diverged (`${{ success() }}` here vs github.actor there), which is
    # exactly the drift the single-source rule exists to prevent.
    "if-on-publish-job": lambda d: _publish(d).update({"if": _IF_EXPR}),
    "if-on-publish-step": lambda d: _pypi_step(d).update({"if": _IF_EXPR}),
    "if-on-build-job": lambda d: d["jobs"]["build"].update({"if": _IF_EXPR}),
    "continue-on-error-build-job": lambda d: d["jobs"]["build"].update(
        {"continue-on-error": True}
    ),
    "forked-action": lambda d: _pypi_step(d).update(
        {"uses": "attacker-fork/gh-action-pypi-publish@release/v1"}
    ),
    "permissions-job-widened": lambda d: _publish(d)["permissions"].update(
        {"contents": "write"}
    ),
    "permissions-workflow-level": lambda d: d.update({"permissions": "write-all"}),
    "job-added": lambda d: d["jobs"].update(
        {
            "exfil": {
                "runs-on": "ubuntu-latest",
                "timeout-minutes": 10,
                "environment": "release",
                "steps": [{"name": "Collect", "run": "env"}],
            }
        }
    ),
    "job-deleted": lambda d: d["jobs"].pop("publish"),
}

TIMEOUT_MUTANTS = {
    "timeout-360": 360,
    "timeout-1": 1,
    "timeout-string": "not a number",
    "timeout-bool": True,
}

DRIFT_MUTANTS = (
    "maintenance-deleted",
    "changelog-job-deleted",
    "workflows-job-deleted",
)

# Every mutant these tests exercise. Asserted EQUAL to the helper's expected-1
# rows — in BOTH directions — by TestMutationHelperContract, because the
# evidence matrix is generated from that TSV: a phantom row there would
# otherwise appear in the matrix with nothing exercising it.
TESTED_MUTANTS = (
    set(RELEASE_MUTANTS)
    | set(TIMEOUT_MUTANTS)
    | set(DRIFT_MUTANTS)
    | {"file-deleted", "timeout-deleted", "new-tag-triggered-workflow"}
)


class TestReleaseInvariants:
    def test_the_committed_release_workflow_passes(self) -> None:
        assert cw.release_invariants(release_doc()) == []

    @pytest.mark.parametrize("mutant", sorted(RELEASE_MUTANTS))
    def test_mutant_is_rejected(self, mutant: str) -> None:
        doc = release_doc()
        RELEASE_MUTANTS[mutant](doc)
        reasons = cw.release_invariants(doc)
        assert reasons, f"{mutant} passed release_invariants unnoticed"
        assert all(r.startswith("[release]") for r in reasons)

    def test_a_deleted_release_file_is_a_failure_not_a_pass(self) -> None:
        # mutant: file-deleted. Nothing else in the checker sees a file that is
        # gone, so the absent case has to be a failure here.
        reasons = cw.release_invariants(None)
        assert reasons and "missing" in reasons[0]

    def test_the_list_form_of_needs_is_accepted(self) -> None:
        # mutant: needs-as-list (expected exit 0). The real file uses the scalar
        # form; rejecting `needs: [build]` would be a false positive on a
        # legitimate edit.
        doc = release_doc()
        _publish(doc)["needs"] = ["build"]
        assert cw.release_invariants(doc) == []

    def test_the_environment_is_checked_by_name_not_by_presence(self) -> None:
        doc = release_doc()
        _publish(doc)["environment"] = "dev"
        reasons = cw.release_invariants(doc)
        assert any("environment" in r and "dev" in r for r in reasons)

    def test_the_step_level_continue_on_error_is_caught_not_just_the_job(self) -> None:
        # The nastiest live mutant: the tag push reports GREEN while the PyPI
        # upload failed. A job-level-only rule is bypassed one level down.
        doc = release_doc()
        _pypi_step(doc)["continue-on-error"] = True
        reasons = cw.release_invariants(doc)
        assert any("step" in r and "continue-on-error" in r for r in reasons)

    def test_an_extra_trigger_event_is_caught_even_with_the_tag_filter_intact(
        self,
    ) -> None:
        # The trigger block below keeps `push.tags: ["v*"]` untouched, so a
        # check that only looks for "v*" in push.tags passes it. It also makes
        # `publish` — id-token: write, against an environment whose
        # protection_rules are [] — reachable from any same-repository PR and
        # from every ordinary push to main.
        doc = release_doc()
        _on(doc).update({"pull_request": {}})
        _on(doc)["push"].update({"branches": ["main"]})
        assert cw._as_list(_on(doc)["push"]["tags"]) == ["v*"], (
            "this test is only meaningful while the tag filter is still correct"
        )
        reasons = cw.release_invariants(doc)
        assert any("extra trigger event" in r for r in reasons)
        assert any("branches" in r for r in reasons)
        assert all(r.startswith("[release]") for r in reasons)

    @pytest.mark.parametrize(
        "trigger",
        [
            {"push": {"tags": ["v*"]}, "pull_request": {}},
            {"push": {"tags": ["v*"]}, "workflow_dispatch": None},
            {"push": {"tags": ["v*"], "branches": ["main"]}},
            {"push": {"tags": ["v*"], "paths": ["**"]}},
            {"push": {"tags": ["v*", "release-*"]}},
        ],
    )
    def test_the_trigger_set_must_be_exactly_a_tag_push(self, trigger: Any) -> None:
        doc = release_doc()
        doc.pop(True)
        doc["on"] = trigger
        assert cw.release_invariants(doc), f"{trigger!r} passed unnoticed"

    def test_the_committed_trigger_set_is_the_only_accepted_one(self) -> None:
        doc = release_doc()
        doc.pop(True)
        doc["on"] = {"push": {"tags": ["v*"]}}
        assert cw.release_invariants(doc) == []

    def test_a_workflow_level_permissions_block_is_caught(self) -> None:
        # It leaves every job's permissions map untouched while widening the
        # build job, so a job-level-only check passes it.
        doc = release_doc()
        doc["permissions"] = "write-all"
        assert any(
            "workflow-level permissions" in r for r in cw.release_invariants(doc)
        )


class TestTagTriggerInvariants:
    """The release path is not one file — it is every file that runs at tag push."""

    @pytest.mark.parametrize("path", sorted(WORKFLOW_DIR.glob("*.y*ml")))
    def test_every_committed_workflow_passes(self, path: Path) -> None:
        doc = yaml.safe_load(path.read_text())
        assert cw.tag_trigger_invariants(doc, path) == []

    def test_a_new_tag_triggered_workflow_is_rejected(self) -> None:
        # mutant: new-tag-triggered-workflow. It passes the release invariants
        # (it is not release.yml), passes drift (a new file has no base
        # version) and never trips release-diff-ack (which greps for
        # release.yml) — so this is the only layer that can see it.
        doc = yaml.safe_load(
            'name: Release notes\non:\n  push:\n    tags:\n      - "v*"\n'
            "jobs:\n  notes:\n    timeout-minutes: 10\n"
        )
        reasons = cw.tag_trigger_invariants(doc, ".github/workflows/release-notes.yml")
        assert reasons and reasons[0].startswith("[release]")
        assert "on.push.tags" in reasons[0]

    def test_the_allowlisted_files_are_permitted(self) -> None:
        # docker.yml really is tag-triggered today: it pushes the release image.
        doc = yaml.safe_load('on:\n  push:\n    tags:\n      - "v*"\njobs: {}\n')
        assert cw.tag_trigger_invariants(doc, ".github/workflows/docker.yml") == []
        assert cw.tag_trigger_invariants(doc, ".github/workflows/release.yml") == []

    def test_a_new_workflow_without_a_tag_trigger_is_fine(self) -> None:
        doc = yaml.safe_load("on:\n  pull_request:\njobs: {}\n")
        assert cw.tag_trigger_invariants(doc, ".github/workflows/anything.yml") == []

    def test_the_allowlist_matches_the_committed_tree(self) -> None:
        tag_triggered = set()
        for path in cw.workflow_files():
            on = cw.get_on(yaml.safe_load(path.read_text()))
            push = on.get("push") if isinstance(on, dict) else None
            if isinstance(push, dict) and cw._as_list(push.get("tags")):
                tag_triggered.add(path.name)
        assert tag_triggered == cw.TAG_TRIGGERED_WORKFLOWS


class TestTimeoutInvariants:
    @pytest.mark.parametrize("path", sorted(WORKFLOW_DIR.glob("*.y*ml")))
    def test_every_committed_workflow_passes(self, path: Path) -> None:
        doc = yaml.safe_load(path.read_text())
        assert cw.timeout_invariants(doc, path) == []

    @pytest.mark.parametrize("mutant", sorted(TIMEOUT_MUTANTS))
    def test_mutant_is_rejected(self, mutant: str) -> None:
        doc = {"jobs": {"build": {"timeout-minutes": TIMEOUT_MUTANTS[mutant]}}}
        reasons = cw.timeout_invariants(doc, "release.yml")
        assert reasons, f"{mutant} passed timeout_invariants unnoticed"
        assert reasons[0].startswith("[timeout]")

    def test_a_missing_timeout_is_rejected(self) -> None:
        # mutant: timeout-deleted. GitHub's default is 360 minutes.
        doc = {"jobs": {"build": {"runs-on": "ubuntu-latest"}}}
        assert cw.timeout_invariants(doc, "release.yml")

    @pytest.mark.parametrize("value", [10, 12, 20, 25, 30])
    def test_legitimate_values_pass(self, value: int) -> None:
        doc = {"jobs": {"build": {"timeout-minutes": value}}}
        assert cw.timeout_invariants(doc, "release.yml") == []

    def test_true_is_rejected_even_though_python_calls_it_an_int(self) -> None:
        assert cw.timeout_invariants({"jobs": {"b": {"timeout-minutes": True}}}, "x")

    def test_a_reusable_workflow_call_is_skipped(self) -> None:
        # A `uses:` job cannot carry timeout-minutes; requiring one would force
        # invalid YAML.
        doc = {"jobs": {"call": {"uses": "./.github/workflows/other.yml"}}}
        assert cw.timeout_invariants(doc, "x.yml") == []


def _run(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, check=False)


def _orphan_commit(cwd: Path) -> str:
    """A commit that RESOLVES but shares no history with HEAD.

    `git commit-tree` takes its committer from the environment, and a CI runner
    has no global git identity — `actions/checkout` does not set one — so the
    identity is supplied explicitly rather than inherited. Without this the test
    passed on a developer machine and failed on the runner, which is the same
    class of "green locally, dead in CI" this whole change exists to prevent.
    """
    identity = {
        "GIT_AUTHOR_NAME": "workflow guard test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "workflow guard test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
    }
    env = {**os.environ, **identity}
    tree = subprocess.run(
        ["git", "write-tree"],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
        env=env,
    ).stdout.strip()
    # Unreachable from any ref, so git gc collects it; nothing is written to the
    # working tree or the index.
    return subprocess.run(
        ["git", "commit-tree", tree, "-m", "no merge base"],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
        env=env,
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A throwaway git repo with two workflow files committed."""
    _run(["git", "init", "-q", "-b", "main"], tmp_path)
    _run(["git", "config", "user.email", "t@example.com"], tmp_path)
    _run(["git", "config", "user.name", "test"], tmp_path)
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "test.yml").write_text(
        "name: Test\non:\n  push:\njobs:\n"
        "  alpha:\n    timeout-minutes: 10\n"
        "  beta:\n    timeout-minutes: 10\n"
    )
    (workflows / "maintenance.yml").write_text(
        "name: Maintenance\non:\n  push:\njobs:\n  notify-worker:\n    timeout-minutes: 10\n"
    )
    _run(["git", "add", "-A"], tmp_path)
    _run(["git", "commit", "-qm", "base"], tmp_path)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _commit(repo: Path, message: str) -> None:
    _run(["git", "add", "-A"], repo)
    _run(["git", "commit", "-qm", message], repo)


class TestJobSetDrift:
    def test_an_unchanged_tree_reports_nothing(self, repo: Path) -> None:
        paths = cw.changed_workflow_paths("HEAD")
        assert cw.job_set_drift(paths, "HEAD") == []

    def test_a_removed_job_fails_and_names_it(self, repo: Path) -> None:
        # mutant: changelog-job-deleted, in miniature.
        path = repo / ".github/workflows/test.yml"
        path.write_text(
            "name: Test\non:\n  push:\njobs:\n  alpha:\n    timeout-minutes: 10\n"
        )
        _commit(repo, "drop beta")
        reasons = cw.job_set_drift([".github/workflows/test.yml"], "HEAD~1")
        assert len(reasons) == 1
        assert reasons[0].startswith("[drift]")
        assert "beta" in reasons[0]

    def test_an_added_job_passes(self, repo: Path) -> None:
        path = repo / ".github/workflows/test.yml"
        path.write_text(path.read_text() + "  gamma:\n    timeout-minutes: 10\n")
        _commit(repo, "add gamma")
        assert cw.job_set_drift([".github/workflows/test.yml"], "HEAD~1") == []

    def test_a_renamed_job_fails(self, repo: Path) -> None:
        # A rename is a removal plus an addition, and the removal is what
        # breaks a required status check that names the old job.
        path = repo / ".github/workflows/test.yml"
        path.write_text(path.read_text().replace("  beta:", "  bravo:"))
        _commit(repo, "rename beta")
        reasons = cw.job_set_drift([".github/workflows/test.yml"], "HEAD~1")
        assert reasons and "beta" in reasons[0]

    def test_a_deleted_file_reports_instead_of_crashing(self, repo: Path) -> None:
        # mutant: maintenance-deleted. Rev 4 read the head blob unconditionally,
        # which raises on a file that is gone — and a traceback does not name
        # the jobs that were removed, so the one guard against silent job
        # deletion would have shipped dead.
        (repo / ".github/workflows/maintenance.yml").unlink()
        _commit(repo, "delete maintenance")
        paths = cw.changed_workflow_paths("HEAD~1")
        assert ".github/workflows/maintenance.yml" in paths, (
            "git diff must include deletions; a worktree glob cannot see a file "
            "that is gone"
        )
        reasons = cw.job_set_drift(paths, "HEAD~1")
        assert len(reasons) == 1
        assert "notify-worker" in reasons[0]
        assert "deleted" in reasons[0]

    def test_a_renamed_file_fails(self, repo: Path) -> None:
        workflows = repo / ".github/workflows"
        (workflows / "maintenance.yml").rename(workflows / "maint.yml")
        _commit(repo, "rename the file")
        paths = cw.changed_workflow_paths("HEAD~1")
        reasons = cw.job_set_drift(paths, "HEAD~1")
        assert reasons and "notify-worker" in reasons[0]

    def test_a_brand_new_workflow_file_passes(self, repo: Path) -> None:
        (repo / ".github/workflows/new.yml").write_text(
            "name: New\non:\n  push:\njobs:\n  fresh:\n    timeout-minutes: 10\n"
        )
        _commit(repo, "add a workflow")
        paths = cw.changed_workflow_paths("HEAD~1")
        assert paths == [".github/workflows/new.yml"]
        assert cw.job_set_drift(paths, "HEAD~1") == []

    def test_an_unresolvable_base_fails_closed(self, repo: Path) -> None:
        # The whole point: a shallow clone, a bad ref or a failed fetch must not
        # look like "every file is new". That would exit 0 with drift silently
        # disabled.
        reasons = cw.job_set_drift(
            [".github/workflows/test.yml"], "0000000000000000000000000000000000000000"
        )
        assert reasons and "does not resolve" in reasons[0]

    def test_a_failing_git_diff_is_never_read_as_no_changes(self, repo: Path) -> None:
        # A base that RESOLVES but shares no history with HEAD makes
        # `git diff A...B` exit 128 with "no merge base". Returning [] there
        # passes every subsequent check vacuously — a different fault from an
        # unresolvable ref, and it defeats the same fail-closed property on
        # exactly the histories where drift matters: divergent or rewritten
        # history, shallow clones, force-pushed bases.
        orphan = _orphan_commit(repo)
        assert cw._base_resolves(orphan), "the orphan must be a resolvable commit"
        with pytest.raises(cw.GitError) as excinfo:
            cw.changed_workflow_paths(orphan)
        assert "no merge base" in str(excinfo.value)

    def test_the_two_drift_faults_are_reported_distinctly(
        self, repo: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # They must not be conflated behind one message: one means "fetch more
        # history", the other means "this base shares no history with HEAD".
        orphan = _orphan_commit(repo)

        assert cw.main(["--base-ref", "definitely-not-a-ref"]) == 1
        unresolvable = capsys.readouterr().out
        assert "does not resolve to a commit" in unresolvable
        assert "no merge base" not in unresolvable

        assert cw.main(["--base-ref", orphan]) == 1
        no_base = capsys.readouterr().out
        assert "no merge base" in no_base
        assert "does not resolve to a commit" not in no_base

    def test_a_failing_base_tree_read_is_not_read_as_a_new_file(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The same defect wearing a different hat: inferring "this path is new"
        # from a non-zero git invocation would make drift pass on a file whose
        # jobs had in fact been deleted.
        def boom(args: list[str]) -> str:
            raise cw.GitError("`git ls-tree` failed (exit 128): simulated")

        monkeypatch.setattr(cw, "_git_checked", boom)
        reasons = cw.job_set_drift([".github/workflows/test.yml"], "HEAD")
        assert reasons and reasons[0].startswith("[drift]")
        assert "simulated" in reasons[0]

    def test_a_missing_base_and_a_new_file_are_distinguished(self, repo: Path) -> None:
        (repo / ".github/workflows/new.yml").write_text(
            "name: New\non:\n  push:\njobs:\n  fresh:\n    timeout-minutes: 10\n"
        )
        _commit(repo, "add a workflow")
        assert cw.job_set_drift([".github/workflows/new.yml"], "HEAD~1") == []
        assert cw.job_set_drift([".github/workflows/new.yml"], "nope-not-a-ref") != []

    def test_invalid_yaml_is_reported_not_raised(self, repo: Path) -> None:
        (repo / ".github/workflows/test.yml").write_text("jobs: [unclosed\n")
        _commit(repo, "break the yaml")
        reasons = cw.job_set_drift([".github/workflows/test.yml"], "HEAD~1")
        assert reasons and "not valid YAML" in reasons[0]


class TestOnKeyParsing:
    """YAML 1.1 reads a bare ``on:`` as the boolean ``True``.

    ``yaml.safe_load`` on the real release.yml yields keys ``['name', True,
    'jobs']``, so ``"on" in doc`` is False and a guard written as ``doc["on"]``
    inside a ``try`` passes vacuously — the guard is dead and looks green.
    Both spellings have to work: drop either branch and one of these goes red.
    """

    def test_the_bare_form_parses_to_the_boolean_true(self) -> None:
        doc = yaml.safe_load(RELEASE_YML.read_text())
        assert "on" not in doc
        assert True in doc

    def test_get_on_reads_the_boolean_key(self) -> None:
        assert cw.get_on({True: {"push": {}}, "jobs": {}}) == {"push": {}}

    def test_get_on_reads_the_string_key(self) -> None:
        assert cw.get_on({"on": {"push": {}}, "jobs": {}}) == {"push": {}}

    def test_the_real_document_passes_with_the_boolean_key(self) -> None:
        # Goes red if only the "on" string branch survives.
        assert cw.release_invariants(release_doc()) == []

    def test_a_quoted_on_key_passes_too(self) -> None:
        # Goes red if only the True branch survives.
        doc = release_doc()
        doc["on"] = doc.pop(True)
        assert cw.release_invariants(doc) == []

    def test_a_document_with_no_trigger_is_reported_not_raised(self) -> None:
        doc = release_doc()
        doc.pop(True)
        reasons = cw.release_invariants(doc)
        assert any("tags" in r for r in reasons)


class TestCheckerEntryPoint:
    def test_the_unmutated_tree_exits_zero(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(REPO_ROOT)
        assert cw.main(["--base-ref", "HEAD"]) == 0

    def test_an_empty_base_ref_skips_drift_and_still_runs_invariants(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.chdir(REPO_ROOT)
        assert cw.main(["--base-ref", ""]) == 0
        out = capsys.readouterr().out
        assert "drift: SKIPPED" in out

    def test_an_unresolvable_base_ref_exits_non_zero(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(REPO_ROOT)
        assert cw.main(["--base-ref", "definitely-not-a-ref"]) == 1

    def test_a_base_with_no_merge_base_exits_non_zero(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(REPO_ROOT)
        orphan = _orphan_commit(REPO_ROOT)
        assert cw.main(["--base-ref", orphan]) == 1

    def test_both_workflow_extensions_are_globbed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # bypass-proofs-187.md §A: globbing *.yml alone left a .yaml workflow
        # unchecked, and GitHub runs both.
        monkeypatch.chdir(REPO_ROOT)
        patterns = {p.suffix for p in cw.workflow_files()}
        assert patterns <= {".yml", ".yaml"}
        assert any(str(p).endswith("release.yml") for p in cw.workflow_files())


# --- the CI wiring itself -----------------------------------------------------

_TEST_WF = yaml.safe_load(TEST_YML.read_text())
_TEST_WF_TEXT = TEST_YML.read_text()


def _job(name: str) -> dict[str, Any]:
    return _TEST_WF["jobs"][name]


def _step_named(job: dict[str, Any], fragment: str) -> dict[str, Any]:
    for step in job["steps"]:
        if fragment in str(step.get("name", "")):
            return step
    raise AssertionError(f"no step named like {fragment!r}")


class TestCiWiring:
    """A correct script wired up wrongly guards nothing, and a script-level test
    cannot see that. These assert the workflow expressions themselves."""

    def test_both_guard_jobs_exist(self) -> None:
        assert "workflows" in _TEST_WF["jobs"]
        assert "release-diff-ack" in _TEST_WF["jobs"]

    @pytest.mark.parametrize("name", ["workflows", "release-diff-ack"])
    def test_the_guard_jobs_obey_the_timeout_invariant(self, name: str) -> None:
        assert cw.TIMEOUT_MIN <= _job(name)["timeout-minutes"] <= cw.TIMEOUT_MAX

    @pytest.mark.parametrize("name", ["workflows", "release-diff-ack"])
    def test_the_guard_jobs_repeat_contents_read(self, name: str) -> None:
        # A job-level permissions: block REPLACES the repo default for every
        # scope, so omitting contents: read costs actions/checkout its access.
        assert _job(name)["permissions"]["contents"] == "read"

    @pytest.mark.parametrize("name", ["workflows", "release-diff-ack"])
    def test_the_guard_jobs_check_out_full_history(self, name: str) -> None:
        # Both do a three-dot diff against the base; a depth-1 clone has no base.
        checkout = [s for s in _job(name)["steps"] if "checkout" in str(s.get("uses"))]
        assert checkout and checkout[0]["with"]["fetch-depth"] == 0

    def test_the_checker_step_cannot_fail_open(self) -> None:
        step = _step_named(_job("workflows"), "Check workflow invariants")
        assert "|| true" not in step["run"]
        assert "continue-on-error" not in step
        assert "if" not in step
        assert "scripts/check_workflows.py" in step["run"]

    def test_no_step_of_the_guard_jobs_continues_on_error(self) -> None:
        for name in ("workflows", "release-diff-ack"):
            assert "continue-on-error" not in _job(name)
            for step in _job(name)["steps"]:
                assert "continue-on-error" not in step

    def test_the_guard_job_is_not_restricted_to_pull_request(self) -> None:
        # The invariants are worth running on push and schedule too; only drift
        # needs a base, and the script says so when it has none.
        assert "if" not in _job("workflows")

    def test_the_drift_base_is_the_pr_base_sha_on_pull_request(self) -> None:
        # Rev 2 of the plan wired only github.event.before, which is a PushEvent
        # field and is UNSET on pull_request: every PR would have run with an
        # empty base and skipped drift entirely, on the path that gates merges.
        step = _step_named(_job("workflows"), "Resolve the drift base")
        assert step["env"]["PR_BASE_SHA"] == "${{ github.event.pull_request.base.sha }}"
        assert step["env"]["PUSH_BEFORE"] == "${{ github.event.before }}"
        run = step["run"]
        assert 'pull_request) BASE="$PR_BASE_SHA"' in run
        assert 'push)         BASE="$PUSH_BEFORE"' in run
        # schedule / workflow_dispatch fall through to no base.
        assert '*)            BASE=""' in run

    def test_the_resolved_base_is_the_one_passed_to_the_checker(self) -> None:
        resolve = _step_named(_job("workflows"), "Resolve the drift base")
        check = _step_named(_job("workflows"), "Check workflow invariants")
        assert resolve["id"] == "drift-base"
        assert "steps.drift-base.outputs.base" in check["run"]

    def test_actionlint_is_pinned_and_digest_checked_before_extraction(self) -> None:
        step = _step_named(_job("workflows"), "Install actionlint")
        assert step["env"]["ACTIONLINT_VERSION"] == "1.7.12"
        assert (
            step["env"]["ACTIONLINT_SHA256"]
            == "8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8"
        )
        run = step["run"]
        assert run.index("sha256sum -c -") < run.index("tar -xzf"), (
            "the digest must be asserted BEFORE extraction"
        )
        # The installed executable is a different byte stream from the archive,
        # so it gets its own assertion rather than the same digest.
        assert "actionlint --version | head -n1" in run

    def test_actionlint_runs_bare_with_no_glob(self) -> None:
        # `actionlint *.yml` leaves a .yaml workflow unchecked.
        step = _step_named(_job("workflows"), "Run actionlint")
        assert step["run"].strip() == "actionlint"

    def test_release_diff_ack_reads_live_labels_not_the_frozen_payload(self) -> None:
        step = _step_named(_job("release-diff-ack"), "Require an acknowledgement")
        run = step["run"]
        # The event payload is frozen at trigger time and a re-run replays it,
        # so a label applied to an already-failed PR would never be seen.
        assert "gh api" in run and "/pulls/" in run
        code = "\n".join(
            line for line in run.splitlines() if not line.lstrip().startswith("#")
        )
        assert "github.event.pull_request.labels" not in code
        # It is allowed as the fallback, passed by env so label text can never
        # be parsed as shell.
        assert "labels.*.name" in step["env"]["PAYLOAD_LABELS_JSON"]
        assert "release-change-approved" in run

    def test_release_diff_ack_passes_when_release_yml_is_untouched(self) -> None:
        step = _step_named(_job("release-diff-ack"), "Require an acknowledgement")
        run = step["run"]
        assert "grep -qx '\\.github/workflows/release\\.yml'" in run
        assert "no acknowledgement required" in run

    def test_release_diff_ack_only_runs_on_pull_request(self) -> None:
        assert _job("release-diff-ack")["if"] == "github.event_name == 'pull_request'"


class TestMutationHelperContract:
    """The mutant list and the tests must be the same list.

    Four consecutive plan revisions shipped a mutation contract that disagreed
    with the loop meant to execute it, so the loop certified a checker that had
    never seen half the mutants.
    """

    @staticmethod
    def _rows() -> list[tuple[str, str, str, str]]:
        out = subprocess.run(
            [str(MUTATE_SH), "--list"], capture_output=True, text=True, check=True
        ).stdout
        rows = []
        for line in out.splitlines():
            if not line.strip():
                continue
            parts = line.split("|")
            assert len(parts) == 4, f"malformed mutant row: {line}"
            rows.append((parts[0], parts[1], parts[2], parts[3]))
        return rows

    def test_the_helper_is_executable(self) -> None:
        assert MUTATE_SH.exists()

    def test_names_are_unique(self) -> None:
        names = [row[0] for row in self._rows()]
        assert len(names) == len(set(names))

    @pytest.mark.parametrize("mutant", sorted(TESTED_MUTANTS))
    def test_every_mutant_these_tests_exercise_is_in_the_helper(
        self, mutant: str
    ) -> None:
        rows = {row[0]: row for row in self._rows()}
        assert mutant in rows, f"{mutant} is tested here but the helper cannot apply it"
        assert rows[mutant][1] == "1", f"{mutant} must be expected to fail the checker"

    def test_the_inclusion_is_bidirectional(self) -> None:
        # One-directional (suite ⊆ helper) is not enough: a phantom row appended
        # to the TSV would still pass while nothing here ever exercised it, and
        # the evidence matrix is generated from that TSV.
        expected_to_fail = {row[0] for row in self._rows() if row[1] == "1"}
        assert expected_to_fail == TESTED_MUTANTS, (
            "helper rows and tested mutants have diverged: "
            f"only in helper={sorted(expected_to_fail - TESTED_MUTANTS)}, "
            f"only in tests={sorted(TESTED_MUTANTS - expected_to_fail)}"
        )

    def test_the_if_expression_matches_the_shell_helper(self) -> None:
        # The tests re-implement each mutant in Python, so "single source"
        # covers names, exit codes and tags — not the edit itself. This pins the
        # one value where the two had already drifted apart.
        source = MUTATE_SH.read_text()
        assert "github.actor != " in source
        assert "nobody" in _IF_EXPR and "github.actor != " in _IF_EXPR

    @pytest.mark.parametrize("mutant", DRIFT_MUTANTS)
    def test_the_drift_only_mutants_declare_drift_as_their_catcher(
        self, mutant: str
    ) -> None:
        # AC2: drift has to be proven by its own reported reason. The release
        # and timeout invariants only see surviving files, so if drift were
        # replaced by `return []` these would go green with nothing else to
        # catch them.
        rows = {row[0]: row for row in self._rows()}
        assert rows[mutant][2] == "[drift]"

    @pytest.mark.parametrize(
        "mutant",
        [
            "needs-as-list",
            "timeout-below-p100",
            "concurrency-added",
            "guard-self-disabled",
            "guard-self-disabled-nonconstant",
            "guard-step-gutted",
        ],
    )
    def test_the_uncovered_cases_are_listed_as_expected_green(
        self, mutant: str
    ) -> None:
        # Named individually rather than omitted, so the evidence cannot imply
        # coverage that does not exist.
        rows = {row[0]: row for row in self._rows()}
        assert rows[mutant][1] == "0"

    def test_an_unknown_mutant_name_is_refused(self) -> None:
        result = subprocess.run(
            [str(MUTATE_SH), "no-such-mutant"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode != 0
