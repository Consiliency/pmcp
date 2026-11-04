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

# The committed pypa pin, SHA form (what the parser yields). Read from the
# checker so the tests cannot drift from the allowlist they exercise.
_PYPA_PIN = next(u for u in cw.EXPECTED_USES["publish"] if u.startswith("pypa/"))

# Each key is a mutant name in scripts/_mutate_workflow.sh; the callable is the
# same edit applied to the parsed document.
RELEASE_MUTANTS: dict[str, Any] = {
    "release-artifact-unverified": lambda d: d["jobs"]["publish"].update(
        {
            "steps": [
                s
                for s in d["jobs"]["publish"]["steps"]
                if s.get("name") != "Verify artifact identity"
            ]
        }
    ),
    "release-artifact-path-widened": lambda d: next(
        s
        for s in d["jobs"]["publish"]["steps"]
        if str(s.get("uses", "")).startswith("pypa/")
    ).update({"with": {"packages-dir": ".release-bundle/"}}),
    "release-suite-removed": lambda d: d["jobs"]["build"].update(
        {
            "steps": [
                s
                for s in d["jobs"]["build"]["steps"]
                if s.get("name") != "Run tests with coverage"
            ]
        }
    ),
    "release-coverage-drift": lambda d: next(
        s
        for s in d["jobs"]["build"]["steps"]
        if s.get("name") == "Run tests with coverage"
    ).update({"run": "uv run --locked python -m pytest tests/"}),
    "release-audit-removed": lambda d: d["jobs"]["build"].update(
        {
            "steps": [
                s
                for s in d["jobs"]["build"]["steps"]
                if s.get("name") != "Audit locked dependencies before publication"
            ]
        }
    ),
    "release-node-removed": lambda d: d["jobs"]["build"].update(
        {
            "steps": [
                s
                for s in d["jobs"]["build"]["steps"]
                if not str(s.get("uses", "")).startswith("actions/setup-node@")
            ]
        }
    ),
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
    # SHA form kept, so the pin invariant passes and only the exact allowlist
    # sees the owner change.
    "forked-action": lambda d: _pypi_step(d).update(
        {"uses": "attacker-fork/" + _PYPA_PIN.split("/", 1)[1]}
    ),
    # Form-valid, honest-looking, wrong commit. The pin invariant cannot know
    # which commit is right; EXPECTED_USES can.
    "sha-moved": lambda d: _pypi_step(d).update(
        {"uses": _PYPA_PIN[:-1] + ("4" if _PYPA_PIN[-1] != "4" else "5")}
    ),
    # A REAL older release with its real commit: 144 commits behind v1.14.2 and
    # below the GHSA-vxmw-7h4f-hqxh floor (< 1.13.0). Nothing about its form is
    # wrong; only the exact allowlist rejects it.
    "pypa-rolled-back": lambda d: _pypi_step(d).update(
        {"uses": "pypa/gh-action-pypi-publish@ec4db0b4ddc65acdf4bff5fa45ac92d78b56bdf0"}
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

# Mutants of the FORM of a pin, caught by `all_uses_are_sha_pinned` on raw
# text. Each is the same edit the shell helper makes, applied to a copy:
# (file, regex locating the CURRENT pinned line, replacement). Regex, not a
# literal: the SHA and comment change on every Dependabot bump, and a literal
# anchor made the first such bump (#219) fail this suite.
_PIN_RE = r"@[0-9a-f]{40} # \S+"
PIN_MUTANTS: dict[str, tuple[Path, str, str]] = {
    "tag-pinned-action": (
        TEST_YML,
        "- uses: actions/setup-node" + _PIN_RE,
        "- uses: actions/setup-node@v7",
    ),
    "sha-comment-dropped": (
        RELEASE_YML,
        r"(uses: actions/upload-artifact@[0-9a-f]{40}) # \S+",
        r"\1",
    ),
    "composite-tag-pinned": (
        REPO_ROOT / ".github" / "actions" / "pipeline-bootstrap-setup" / "action.yml",
        "uses: actions/setup-node" + _PIN_RE,
        "uses: actions/setup-node@v4",
    ),
    # Valid YAML GitHub runs; invisible to the line scan. Only the
    # parsed-inventory cross-check sees it.
    "uses-quoted-key": (
        REPO_ROOT / ".github" / "actions" / "pipeline-bootstrap-setup" / "action.yml",
        "- uses: actions/setup-node" + _PIN_RE,
        '- "uses": actions/setup-node@v4',
    ),
}

# The guarded image cutover's mutants (G T7). Each is applied by the shell
# helper itself to a scratch copy of the tree (see TestImagePublicationInvariants),
# so the edit is single-source, not re-implemented here.
IMAGE_MUTANTS = (
    "image-docker-main-push",
    "image-docker-tag-push",
    "image-docker-pull-request-target",
    "image-docker-push-expression",
    "image-docker-packages-write",
    "image-docker-login",
    "image-docker-cache-to",
    "image-docker-secrets",
    "image-docker-extra-job",
    "image-tag-allowlist-docker",
    "image-workflow-packages-write",
    "image-job-write-all",
    "image-promote-contents-dropped",
    "image-watch-packages-write",
    "image-job-no-permissions-image",
    "image-job-no-permissions-watch",
    "image-job-no-permissions-docker",
    "image-docker-push-step",
    "image-buildx-push",
    "image-type-registry",
    "image-curl-put",
    "image-skopeo-delete",
    "image-crane",
    "image-oras",
    "image-regctl",
    "image-podman-push",
    "image-buildah-push",
    "image-promote-outside",
    "image-stray-ghcr",
    "image-promote-if-vars",
    "image-needs-gate-dropped",
    "image-needs-verify-dropped",
    "image-gate-lower",
    "image-gate-literal-output",
    "image-gate-extra-step",
    "image-gate-literal-env",
    "image-gate-if",
    "image-candidate-ref-head-sha",
    "image-persist-credentials",
    "image-trusted-ref-main",
    "image-cone-mode",
    "image-watch-baselines-dropped",
    "image-gate-checkout",
    "image-git-clone",
    "image-curl-contents",
    "image-run-head-sha",
    "image-download-run-id",
    "image-cancel-in-progress",
    "image-trigger-push-tags",
    "image-trigger-workflow-call",
    "image-trigger-schedule",
    "image-trigger-repository-dispatch",
    "image-trigger-workflow-dispatch",
    "image-types-requested",
    "image-branches-removed",
    "image-tag-caller",
    "image-watch-push",
    "image-watch-pull-request",
    "image-ack-docker",
    "image-ack-image",
    "image-ack-dockerfile",
    "image-ack-baselines",
    "image-gate-workflow-env",
    "image-gate-workflow-defaults",
    "image-docker-image-push",
    "image-skopeo-smoke-glued",
    "image-docker-global-option-push",
    "image-skopeo-smoke-cmdsubst",
    "image-pwsh-invoke-method",
)

# Round-1 mutants (PR Consiliency/pmcp#463, president R1, R3, R4) and the
# specific reason each must produce, not just any [image] reason.
SPECIFIC_REASONS = {
    "image-gate-workflow-env": "top-level keys must be a subset of",
    "image-gate-workflow-defaults": "top-level keys must be a subset of",
    "image-docker-image-push": "contains docker push",
    "image-skopeo-smoke-glued": "uses skopeo outside the pinned local load in build-smoke",
    # Consiliency/pmcp#472: R8f, R9 and the PowerShell web cmdlets.
    "image-docker-global-option-push": "contains docker push",
    "image-skopeo-smoke-cmdsubst": "uses skopeo outside the pinned local load in build-smoke",
    "image-pwsh-invoke-method": "contains a mutating PowerShell web request",
}

# Mutants that only the curl/wget clause of guard (d), or its whole-job
# exemption pin, can kill (president re-check 1, PA-1c).
HTTP_MUTANTS = (
    "image-http-notify-put",
    "image-http-notify-url-repointed",
    "image-http-notify-curlrc",
    "image-http-notify-copied",
    "image-http-upload-T",
    "image-http-data-d",
    "image-http-wget-post-file",
    "image-http-step-shell",
    "image-http-defaults-shell",
    "image-http-quoted-separator",
    "image-http-attached-value",
    "image-http-quoted-method",
    # Consiliency/pmcp#472 (R7, R8): each passes the 5d416e4 floor.
    "image-http-comment-cmdsubst",
    "image-http-arith-mid",
    "image-http-prefix-hidden",
    "image-http-redirect-before-method",
    "image-http-expand-request",
)

# Every mutant these tests exercise. Asserted EQUAL to the helper's expected-1
# rows — in BOTH directions — by TestMutationHelperContract, because the
# evidence matrix is generated from that TSV: a phantom row there would
# otherwise appear in the matrix with nothing exercising it.
TESTED_MUTANTS = (
    {"bootstrap-suite-decoy", "bootstrap-selected-shell"}
    | {
        "release-xml-root",
        "release-bundle-hidden",
        "release-verifier-env",
        "release-notes-mutated",
        "release-history-shallow",
        "release-suite-env-filter",
        "release-bundle-hidden",
        "release-verifier-env",
        "release-notes-mutated",
    }
    | set(RELEASE_MUTANTS)
    | set(TIMEOUT_MUTANTS)
    | set(DRIFT_MUTANTS)
    | set(PIN_MUTANTS)
    | {"file-deleted", "timeout-deleted", "new-tag-triggered-workflow"}
    | {
        "diagnostics-upload-raw",
        "diagnostics-upload-ungated",
        "diagnostics-sanitize-not-always",
        "diagnostics-retention",
    }
    | {
        "scope-manufactures-asset",
        "gate-no-always",
        "gate-missing-audit",
        "test-needs-before-protection",
        "gate-static-needs",
        "gate-step-false",
        "gate-shell-override",
        "scope-output-rebound",
        "scope-step-id",
        "scope-force-full-unbound",
        "gate-job-name",
        "gate-matrix",
        "workflow-shell-default",
        "audit-step-skipped",
        "audit-step-best-effort",
        "audit-command-omitted",
        "test-path-filtered",
        "gate-context-collision",
        "gate-context-cross-workflow",
        "guard-self-disabled",
        "guard-self-disabled-nonconstant",
    }
    | {"integration-cancelled", "integration-shared-group"}
    | set(IMAGE_MUTANTS)
    | set(HTTP_MUTANTS)
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

    def test_only_the_release_workflow_is_allowlisted(self) -> None:
        # docker.yml left the allowlist with the guarded image cutover: it is a
        # read-only pull-request build, so a tag trigger on it is a regression.
        doc = yaml.safe_load('on:\n  push:\n    tags:\n      - "v*"\njobs: {}\n')
        assert cw.TAG_TRIGGERED_WORKFLOWS == {"release.yml"}
        assert cw.tag_trigger_invariants(doc, ".github/workflows/release.yml") == []
        reasons = cw.tag_trigger_invariants(doc, ".github/workflows/docker.yml")
        assert reasons and reasons[0].startswith("[release]")

    def test_a_new_workflow_without_a_tag_trigger_is_fine(self) -> None:
        doc = yaml.safe_load("on:\n  pull_request:\njobs: {}\n")
        assert cw.tag_trigger_invariants(doc, ".github/workflows/anything.yml") == []

    @pytest.mark.real_cwd  # subject IS the committed tree at the invocation root;
    # scripts/check_workflows.py resolves .github/workflows relative to cwd by design.
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


_SHA = "0123456789abcdef0123456789abcdef01234567"
_COMPOSITE_YML = (
    REPO_ROOT / ".github" / "actions" / "pipeline-bootstrap-setup" / "action.yml"
)


def _write(tmp_path: Path, rel: str, body: str) -> Path:
    path = tmp_path / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    return path


def _wf(uses: str) -> str:
    return f"name: x\non:\n  push:\njobs:\n  j:\n    timeout-minutes: 10\n    steps:\n      - uses: {uses}\n"


class TestShaPinning:
    """Consiliency/pmcp#217: every remote `uses:` is `owner/action@<sha> # <release>`."""

    def test_every_committed_file_passes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(REPO_ROOT)
        assert cw.all_uses_are_sha_pinned(cw.pin_scan_files()) == []

    def test_the_scan_covers_the_composite_action(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # It runs inside a job holding id-token: write and contents: write, and
        # Dependabot's root entry had never scanned it.
        monkeypatch.chdir(REPO_ROOT)
        assert _COMPOSITE_YML.resolve() in {p.resolve() for p in cw.pin_scan_files()}

    def test_the_committed_tree_has_the_expected_remote_ref_count(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Spelled twice on purpose, like EXPECTED_USES: a new remote action is a
        # deliberate event, and this guard is what makes it one. 34 in the
        # workflows + 1 in the composite.
        #
        # Was 30 until the `audit` job (pip-audit, see #224) added a checkout
        # and a setup-uv; 32 until the `windows-smoke` job
        # (Consiliency/pmcp#388 round 8) added the same two, already-pinned
        # actions. Raising this number is the deliberate step; the guard
        # firing on an unreviewed addition is the point.
        monkeypatch.chdir(REPO_ROOT)
        # Milestone A adds the safe diagnostic artifact uploader.
        # Release alignment adds its explicit Node setup too.
        # Artifact verification checks out its same trusted tag in publish.
        # Separate published-wheel observation uses four read-only actions.
        # The guarded image cutover drops docker.yml's login action and adds
        # eight already-pinned uses: seven in image.yml, one in image-watch.yml.
        assert len(cw.remote_uses(cw.pin_scan_files())) == 48

    def test_expected_uses_is_sha_form_without_comments(self) -> None:
        # yaml.safe_load strips comments before _collect_uses runs, so a value
        # carrying `# vX` could never match; a mutable tag defeats the point.
        for job, values in cw.EXPECTED_USES.items():
            for value in values:
                assert cw.re.fullmatch(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}", value), (
                    job,
                    value,
                )

    def test_the_pypa_pin_is_at_or_above_the_advisory_floor(self) -> None:
        # GHSA-vxmw-7h4f-hqxh: < 1.13.0 is vulnerable. Compared NUMERICALLY —
        # a lexical comparison is how v1.9.0 once looked newer than v1.14.2.
        line = next(
            ln
            for ln in RELEASE_YML.read_text().splitlines()
            if "pypa/gh-action-pypi-publish@" in ln
        )
        comment = line.split("#", 1)[1].strip()
        assert comment.startswith("v")
        version = tuple(int(part) for part in comment[1:].split("."))
        assert version >= (1, 13, 0), comment

    @pytest.mark.parametrize("mutant", sorted(PIN_MUTANTS))
    def test_mutant_is_rejected(self, mutant: str, tmp_path: Path) -> None:
        source, pattern, repl = PIN_MUTANTS[mutant]
        text = source.read_text()
        mutated, count = cw.re.subn(pattern, repl, text, count=1)
        # tag-pinned-action's line is byte-identical in two jobs; the helper
        # scopes it under `install-smoke:`. Here any one occurrence will do,
        # but the pattern must still FIND the pin.
        assert count == 1, f"{mutant}: pin pattern not found in {source.name}"
        copy_path = _write(tmp_path, source.name, mutated)
        reasons = cw.all_uses_are_sha_pinned([copy_path])
        assert reasons and all(r.startswith("[pin]") for r in reasons), reasons

    @pytest.mark.parametrize(
        ("uses", "ok"),
        [
            (f"actions/checkout@{_SHA} # v7.0.1", True),
            (f"actions/checkout@{_SHA} #v7.0.1", True),
            (f"owner/repo/sub/dir@{_SHA} # v1", True),  # subdirectory action
            ("actions/checkout@v7", False),  # mutable tag
            ("actions/checkout@main", False),  # branch
            (f"actions/checkout@{_SHA}", False),  # no comment
            (f"actions/checkout@{_SHA[:39]} # v7", False),  # 39 hex
            (f"actions/checkout@{_SHA.upper()} # v7", False),  # not lowercase hex
            ("docker://alpine:3.20", False),  # remote, not pinnable here
        ],
    )
    def test_step_level_forms(self, tmp_path: Path, uses: str, ok: bool) -> None:
        path = _write(tmp_path, ".github/workflows/x.yml", _wf(uses))
        assert (cw.all_uses_are_sha_pinned([path]) == []) is ok, uses

    @pytest.mark.parametrize(
        "step",
        [
            '- "uses": actions/checkout@v7',  # quoted key
            "- uses : actions/checkout@v7",  # space before the colon
            "- {uses: actions/checkout@v7}",  # flow mapping
            "- uses: >-\n          actions/checkout@v7",  # block scalar
            "- uses: 'actions/checkout@v7'",  # quoted value
        ],
    )
    def test_a_uses_the_line_scan_cannot_see_fails_closed(
        self, tmp_path: Path, step: str
    ) -> None:
        # All valid YAML that GitHub executes as a mutable-ref step. The raw
        # scan sees none of them; the parsed inventory does, and the two must
        # be equal. A guard that only reads lines would let these run unpinned.
        body = (
            "name: x\non:\n  push:\njobs:\n  j:\n    timeout-minutes: 10\n"
            f"    steps:\n      {step}\n"
        )
        path = _write(tmp_path, ".github/workflows/x.yml", body)
        assert [s.get("uses") for s in yaml.safe_load(body)["jobs"]["j"]["steps"]] == [
            "actions/checkout@v7"
        ]
        reasons = cw.all_uses_are_sha_pinned([path])
        assert any("inventory mismatch" in r for r in reasons), (step, reasons)

    def test_a_quoted_value_with_a_valid_pin_still_fails_closed(
        self, tmp_path: Path
    ) -> None:
        # Even a correct pin in a form the scan cannot read is refused: the
        # convention is one spelling, so the comment is always visible.
        path = _write(
            tmp_path,
            ".github/workflows/x.yml",
            _wf(f"'actions/checkout@{_SHA}' # v7.0.1"),
        )
        assert cw.all_uses_are_sha_pinned([path]) != []

    def test_the_inventories_agree_on_the_committed_tree(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(REPO_ROOT)
        for path in cw.pin_scan_files():
            assert cw._unseen_by_raw_scan(path) == [], path

    def test_a_same_repository_path_is_skipped(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path, ".github/workflows/x.yml", _wf("./.github/actions/local")
        )
        assert cw.all_uses_are_sha_pinned([path]) == []
        assert cw.remote_uses([path]) == []

    @pytest.mark.parametrize(
        ("uses", "ok"),
        [
            (f"owner/repo/.github/workflows/build.yml@{_SHA} # v1.2.3", True),
            ("owner/repo/.github/workflows/build.yml@main", False),
            ("./.github/workflows/build.yml", True),
        ],
    )
    def test_job_level_reusable_workflows(
        self, tmp_path: Path, uses: str, ok: bool
    ) -> None:
        # A remote reusable workflow is remote code and must be pinned; a
        # same-repository one is reviewed like any file. NOT a blanket skip.
        path = _write(
            tmp_path,
            ".github/workflows/x.yml",
            f"name: x\non:\n  push:\njobs:\n  call:\n    uses: {uses}\n",
        )
        assert (cw.all_uses_are_sha_pinned([path]) == []) is ok, uses

    def test_both_extensions_are_scanned_for_actions_and_workflows(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Renaming to .yaml keeps the file executable; it must not leave the scan.
        monkeypatch.chdir(tmp_path)
        _write(tmp_path, ".github/workflows/a.yaml", _wf("actions/checkout@v7"))
        _write(
            tmp_path,
            ".github/actions/thing/action.yaml",
            "name: t\nruns:\n  using: composite\n  steps:\n    - uses: actions/setup-node@v4\n",
        )
        reasons = cw.all_uses_are_sha_pinned(cw.pin_scan_files())
        assert len(reasons) == 2, reasons
        assert any("a.yaml" in r for r in reasons)
        assert any("action.yaml" in r for r in reasons)

    def test_a_composite_runs_block_is_scanned_as_steps(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path,
            ".github/actions/thing/action.yml",
            f"name: t\nruns:\n  using: composite\n  steps:\n    - uses: actions/setup-node@{_SHA} # v4.4.0\n",
        )
        assert cw.remote_uses([path]) == [
            (path, 5, f"actions/setup-node@{_SHA} # v4.4.0")
        ]
        assert cw.all_uses_are_sha_pinned([path]) == []

    def test_the_reason_names_file_line_and_ref(self, tmp_path: Path) -> None:
        path = _write(tmp_path, ".github/workflows/x.yml", _wf("actions/checkout@v7"))
        (reason,) = cw.all_uses_are_sha_pinned([path])
        assert reason.startswith("[pin]")
        assert f"{path}:8" in reason
        assert "actions/checkout@v7" in reason


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

    def test_full_bootstrap_contract_is_wired(self) -> None:
        assert cw.full_bootstrap_invariants(_TEST_WF) == []

    @pytest.mark.parametrize(
        "mutant",
        [
            "scope-manufactures-asset",
            "gate-no-always",
            "gate-missing-audit",
            "test-needs-before-protection",
        ],
    )
    def test_bootstrap_mutants_fail(self, mutant: str) -> None:
        doc = copy.deepcopy(_TEST_WF)
        jobs = doc["jobs"]
        if mutant == "scope-manufactures-asset":
            step = jobs["ci-scope"]["steps"][0]
            step["run"] = step["run"].replace("'mode': 'FULL'", "'mode': 'ASSET_V1'")
        elif mutant == "gate-no-always":
            jobs["ci-required"].pop("if")
        elif mutant == "gate-missing-audit":
            jobs["ci-required"]["needs"].remove("audit")
        else:
            jobs["test"]["needs"] = ["ci-scope"]
        assert cw.full_bootstrap_invariants(doc)

    @pytest.mark.parametrize(
        "mutant",
        [
            "gate-static-needs",
            "gate-step-false",
            "gate-shell-override",
            "scope-output-rebound",
            "scope-step-id",
            "scope-force-full-unbound",
            "gate-job-name",
            "gate-matrix",
            "workflow-shell-default",
            "audit-step-skipped",
            "audit-step-best-effort",
            "audit-command-omitted",
            "test-path-filtered",
            "gate-context-collision",
            "gate-context-cross-workflow",
        ],
    )
    def test_complete_bootstrap_wiring_mutants_fail(self, mutant: str) -> None:
        doc = copy.deepcopy(_TEST_WF)
        jobs = doc["jobs"]
        gate = jobs["ci-required"]
        scope = jobs["ci-scope"]
        audit = jobs["audit"]["steps"][-1]
        if mutant == "gate-static-needs":
            gate["steps"][0]["env"]["NEEDS_JSON"] = '{"test":{"result":"success"}}'
        elif mutant == "gate-step-false":
            gate["steps"][0]["if"] = False
        elif mutant == "gate-shell-override":
            gate["steps"][0]["shell"] = 'bash -c "exit 0" -- {0}'
        elif mutant == "scope-output-rebound":
            scope["outputs"]["decision"] = "${{ steps.scope.outputs.mode }}"
        elif mutant == "scope-step-id":
            scope["steps"][0]["id"] = "wrong"
        elif mutant == "scope-force-full-unbound":
            scope["steps"][0]["env"]["FORCE_FULL"] = ""
        elif mutant == "gate-job-name":
            gate["name"] = "harmless"
        elif mutant == "gate-matrix":
            gate["strategy"] = {"matrix": {"slot": ["one", "two"]}}
        elif mutant == "workflow-shell-default":
            doc["defaults"] = {"run": {"shell": 'bash -c "exit 0" -- {0}'}}
        elif mutant == "audit-step-skipped":
            audit["if"] = False
        elif mutant == "audit-step-best-effort":
            audit["continue-on-error"] = True
        elif mutant == "audit-command-omitted":
            audit["run"] = "echo audit disabled"
        elif mutant == "test-path-filtered":
            cw.get_on(doc)["pull_request"]["paths"] = ["src/**"]
        elif mutant == "gate-context-collision":
            jobs["lint"]["name"] = "ci-required"
        else:
            other = {"jobs": {"build": {"name": "ci-required", "steps": []}}}
            assert cw.stable_context_invariants(other, Path("maintenance.yml"))
            return
        assert cw.full_bootstrap_invariants(doc)

    @pytest.mark.parametrize("job", ["ci-scope", "ci-required"])
    @pytest.mark.parametrize("condition", [False, None, 0, "", "${{ false }}"])
    def test_falsy_bootstrap_step_conditions_fail(
        self, job: str, condition: Any
    ) -> None:
        doc = copy.deepcopy(_TEST_WF)
        doc["jobs"][job]["steps"][0]["if"] = condition
        assert cw.full_bootstrap_invariants(doc)

    @pytest.mark.parametrize(
        "mutation",
        [
            "global-env",
            "gate-defaults",
            "scope-environment",
            "scope-container",
            "suite-env",
            "suite-job-env",
        ],
    )
    def test_execution_overrides_fail(self, mutation: str) -> None:
        doc = copy.deepcopy(_TEST_WF)
        if mutation == "global-env":
            doc["env"] = {"PATH": "/wrong"}
        elif mutation == "gate-defaults":
            doc["jobs"]["ci-required"]["defaults"] = {"run": {"shell": "python {0}"}}
        elif mutation == "scope-environment":
            doc["jobs"]["ci-scope"]["environment"] = "wrong"
        elif mutation == "scope-container":
            doc["jobs"]["ci-scope"]["container"] = "untrusted"
        elif mutation == "suite-job-env":
            doc["jobs"]["test"]["env"]["PYTEST_ADDOPTS"] = "-k one_test"
        else:
            suite = next(
                s
                for s in doc["jobs"]["test"]["steps"]
                if s.get("name") == "Run tests with coverage"
            )
            suite["env"] = {"PYTEST_ADDOPTS": "-k one_test"}
        assert cw.full_bootstrap_invariants(doc)

    @pytest.mark.parametrize(
        "condition", [False, "${{ github.event_name == 'never' }}"]
    )
    def test_selected_workflow_verification_cannot_skip(self, condition: Any) -> None:
        doc = copy.deepcopy(_TEST_WF)
        doc["jobs"]["workflows"]["if"] = condition
        assert cw.full_bootstrap_invariants(doc)

    @pytest.mark.parametrize(
        "mutant", ["integration-cancelled", "integration-shared-group"]
    )
    def test_integration_concurrency_mutants_fail(self, mutant: str) -> None:
        doc = copy.deepcopy(_TEST_WF)
        if mutant == "integration-cancelled":
            doc["concurrency"]["cancel-in-progress"] = True
        else:
            doc["concurrency"]["group"] = "${{ github.workflow }}-${{ github.ref }}"
        assert cw.validation_concurrency_invariants(doc, TEST_YML)

    def test_diagnostic_contract_is_wired(self) -> None:
        assert cw.diagnostic_invariants(_TEST_WF) == []

    @pytest.mark.parametrize(
        "mutant",
        [
            "diagnostics-upload-raw",
            "diagnostics-upload-ungated",
            "diagnostics-sanitize-not-always",
            "diagnostics-retention",
        ],
    )
    def test_diagnostic_mutants_fail(self, mutant: str) -> None:
        doc = copy.deepcopy(_TEST_WF)
        steps = doc["jobs"]["test"]["steps"]
        upload = next(
            s
            for s in steps
            if str(s.get("uses", "")).startswith("actions/upload-artifact@")
        )
        sanitizer = next(s for s in steps if s.get("id") == "sanitize")
        if mutant == "diagnostics-upload-raw":
            upload["with"]["path"] = ".ci-reports/raw/"
        elif mutant == "diagnostics-upload-ungated":
            upload["if"] = "always()"
        elif mutant == "diagnostics-sanitize-not-always":
            sanitizer.pop("if")
        else:
            upload["with"]["retention-days"] = 90
        assert cw.diagnostic_invariants(doc)

    @pytest.mark.parametrize("mutation", ["missing", "before-tests", "not-always"])
    def test_diagnostics_environment_cannot_suppress_tests(self, mutation: str) -> None:
        doc = copy.deepcopy(_TEST_WF)
        steps = doc["jobs"]["test"]["steps"]
        recorder = next(
            s for s in steps if s.get("name") == "Record environment identity"
        )
        if mutation == "missing":
            steps.remove(recorder)
        elif mutation == "before-tests":
            steps.remove(recorder)
            steps.insert(0, recorder)
        else:
            recorder.pop("if")
        assert cw.diagnostic_invariants(doc)

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
        assert "release|test" in run and "release_artifacts\\.py" in run
        assert "verify_release_bundle\\.py" in run and "check_workflows\\.py" in run
        assert "release_installed\\.py" in run and "p1_probe_server\\.py" in run
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

    @pytest.mark.parametrize("mutant", sorted(PIN_MUTANTS))
    def test_the_pin_mutants_declare_pin_as_their_catcher(self, mutant: str) -> None:
        rows = {row[0]: row for row in self._rows()}
        assert rows[mutant][2] == "[pin]"

    @pytest.mark.parametrize("mutant", ["sha-moved", "pypa-rolled-back"])
    def test_the_wrong_commit_mutants_are_caught_by_the_allowlist_not_the_form(
        self, mutant: str
    ) -> None:
        # Both are form-valid pins; if the tag said [pin] the evidence would be
        # claiming a catch the pin invariant cannot make.
        rows = {row[0]: row for row in self._rows()}
        assert rows[mutant][2] == "[release]"

    @pytest.mark.parametrize(
        "mutant",
        [
            "needs-as-list",
            "timeout-below-p100",
            "concurrency-added",
            "guard-step-gutted",
            "image-needs-reordered",
        ],
    )
    def test_the_uncovered_cases_are_listed_as_expected_green(
        self, mutant: str
    ) -> None:
        # Named individually rather than omitted, so the evidence cannot imply
        # coverage that does not exist.
        rows = {row[0]: row for row in self._rows()}
        assert rows[mutant][1] == "0"

    @pytest.mark.parametrize(
        ("source", "allowed"),
        [
            # The one deliberate literal: the v1.9.0 rollback TARGET.
            (MUTATE_SH, {"ec4db0b4ddc65acdf4bff5fa45ac92d78b56bdf0"}),
            # Same target, plus the synthetic fixture SHA and the all-zeros
            # placeholder the CI-wiring tests use.
            (
                Path(__file__),
                {"ec4db0b4ddc65acdf4bff5fa45ac92d78b56bdf0", _SHA, "0" * 40},
            ),
        ],
    )
    def test_no_pin_anchor_hardcodes_a_real_sha(
        self, source: Path, allowed: set[str]
    ) -> None:
        # #219: the first Dependabot bump of the composite's pin changed the
        # line two mutants and two tests anchored on by literal, and CI went
        # red on a correct one-line update. Anchors match the FORM of a pin
        # (`@<40 hex> # <release>`); only a mutation's *target* may be literal.
        found = set(cw.re.findall(r"\b[0-9a-f]{40}\b", source.read_text()))
        assert found <= allowed, sorted(found - allowed)

    def test_an_unknown_mutant_name_is_refused(self) -> None:
        result = subprocess.run(
            [str(MUTATE_SH), "no-such-mutant"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode != 0


@pytest.mark.parametrize(
    "mutant", ["bootstrap-suite-decoy", "bootstrap-selected-shell"]
)
def test_bootstrap_selected_execution_binds_actual_command(mutant: str) -> None:
    doc = copy.deepcopy(_TEST_WF)
    steps = doc["jobs"]["test"]["steps"]
    if mutant == "bootstrap-suite-decoy":
        real = next(s for s in steps if s.get("name") == "Run tests with coverage")
        real["name"] = "Upload safe reports"
        real["if"] = (
            "${{ always() && steps.sanitize.outcome == 'success' && vars.PMCP_CI_DISABLE_REPORTS == '' }}"
        )
        steps.append({"name": "Run tests with coverage", "run": "echo decoy"})
    else:
        next(s for s in steps if s.get("name") == "Check coverage threshold")[
            "shell"
        ] = "true {0}"
    assert cw.full_bootstrap_invariants(doc)


@pytest.mark.parametrize(
    "mutation",
    [
        "release-xml-root",
        "identity-removed",
        "release-history-shallow",
        "build-before-tests",
        "release-suite-env-filter",
    ],
)
def test_release_validation_identity_and_order(mutation: str) -> None:
    doc = release_doc()
    steps = doc["jobs"]["build"]["steps"]
    if mutation == "release-xml-root":
        step = next(x for x in steps if x.get("name") == "Run tests with coverage")
        step["run"] = step["run"].replace(
            'xml:"$RUNNER_TEMP/release-coverage.xml"', "xml"
        )
    elif mutation == "identity-removed":
        steps[:] = [x for x in steps if x.get("name") != "Record environment identity"]
    elif mutation == "release-history-shallow":
        steps[0]["with"] = {"fetch-depth": 1}
    elif mutation == "build-before-tests":
        i = next(
            i
            for i, x in enumerate(steps)
            if x.get("name") == "Build and verify exact archives"
        )
        steps.insert(1, steps.pop(i))
    else:
        next(x for x in steps if x.get("name") == "Run tests with coverage")["env"] = {
            "PYTEST_ADDOPTS": "-k nothing"
        }
    assert any("[release]" in x for x in cw.release_invariants(doc))


@pytest.mark.parametrize("target", ["publish", "github-release"])
def test_release_verification_must_precede_publication(target: str) -> None:
    doc = release_doc()
    steps = doc["jobs"][target]["steps"]
    verify = next(s for s in steps if s.get("name") == "Verify artifact identity")
    steps.remove(verify)
    steps.append(verify)
    assert cw.release_invariants(doc)


@pytest.mark.parametrize(
    "mutation",
    [
        "upload-path",
        "checkout-ref",
        "existing-assets-filter",
        "existing-verify-removed",
    ],
)
def test_release_exact_publication_boundary(mutation: str) -> None:
    doc = release_doc()
    if mutation == "upload-path":
        next(
            s
            for s in doc["jobs"]["build"]["steps"]
            if s.get("name") == "Upload artifacts"
        )["with"]["path"] = "."
    elif mutation == "checkout-ref":
        doc["jobs"]["github-release"]["steps"][0]["with"] = {}
    else:
        step = next(
            s
            for s in doc["jobs"]["github-release"]["steps"]
            if s.get("name") == "Create GitHub Release"
        )
        if mutation == "existing-assets-filter":
            step["run"] = step["run"].replace(
                'gh release download "$tag"',
                'gh release download "$tag" --pattern "*.whl"',
            )
        else:
            step["run"] = "\n".join(
                line
                for line in step["run"].splitlines()
                if "--released-dir" not in line
            )
    assert cw.release_invariants(doc)


@pytest.mark.parametrize(
    "mutation",
    ["hidden-disabled", "verify-env", "notes-bytes", "create-repo", "job-env"],
)
def test_release_upload_and_downstream_mapping(mutation: str) -> None:
    doc = release_doc()
    if mutation == "hidden-disabled":
        next(
            x
            for x in doc["jobs"]["build"]["steps"]
            if x.get("name") == "Upload artifacts"
        )["with"].pop("include-hidden-files")
    elif mutation == "job-env":
        doc["jobs"]["publish"]["env"] = {"GIT_CONFIG_COUNT": "0"}
    else:
        label = {
            "verify-env": "Verify artifact identity",
            "notes-bytes": "Extract release notes",
            "create-repo": "Create GitHub Release",
        }[mutation]
        target = "publish" if mutation == "verify-env" else "github-release"
        step = next(x for x in doc["jobs"][target]["steps"] if x.get("name") == label)
        if mutation == "notes-bytes":
            step["run"] += (
                "\ncp /tmp/other.whl .release-bundle/dist/pmcp-3.0.0-py3-none-any.whl\n"
            )
        else:
            step.setdefault("env", {})["GH_REPO"] = "different/repository"
    assert cw.release_invariants(doc)


@pytest.mark.parametrize(
    "filename",
    [
        ".github/workflows/release.yml",
        ".github/workflows/test.yml",
        "scripts/release_artifacts.py",
        "scripts/verify_release_bundle.py",
        "scripts/ci_environment.py",
        "scripts/check_workflows.py",
        "scripts/release-build-constraints.txt",
        ".github/probe/release_installed.py",
        ".github/probe/p1_probe_server.py",
        ".github/workflows/docker.yml",
        ".github/workflows/image.yml",
        ".github/workflows/image-watch.yml",
        "Dockerfile",
        ".consiliency/evidence/image-baselines/inventory-B1.json",
        ".consiliency/evidence/image-baselines/registry-R2.json",
        ".consiliency/evidence/image-baselines/retention.json",
        "scripts/image_publish.py",
        "scripts/image_registry_readback.py",
        "scripts/image_cutover_inventory.py",
    ],
)
def test_release_acknowledgement_covers_invoked_graph(filename: str) -> None:
    import re

    run = _step_named(_TEST_WF["jobs"]["release-diff-ack"], "Require")["run"]
    match = re.search(r"grep -Eq '([^']+)'", run)
    assert match
    assert (
        subprocess.run(
            ["grep", "-Eq", match[1]],
            input=filename + "\n",
            text=True,
            capture_output=True,
        ).returncode
        == 0
    )


# --- guarded image publication (G T7) -----------------------------------------


def _image_tree(tmp_path: Path) -> Path:
    """A scratch copy of everything the checker and the mutation helper touch."""
    import shutil

    root = tmp_path / "tree"
    shutil.copytree(REPO_ROOT / ".github", root / ".github")
    (root / "scripts").mkdir()
    for name in ("check_workflows.py", "_mutate_workflow.sh"):
        shutil.copy2(REPO_ROOT / "scripts" / name, root / "scripts" / name)
    return root


def _image_reasons(root: Path) -> tuple[list[str], list[str]]:
    """(image reasons, release tag-trigger reasons) for the tree at ``root``."""
    spec = importlib.util.spec_from_file_location(
        "check_workflows_scratch", root / "scripts" / "check_workflows.py"
    )
    assert spec and spec.loader
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    docs = {}
    release: list[str] = []
    for path in sorted((root / ".github" / "workflows").glob("*.y*ml")):
        doc = yaml.safe_load(path.read_text())
        docs[path.name] = doc
        release.extend(checker.tag_trigger_invariants(doc, path.name))
    return checker.image_publication_invariants(docs), release


def _mutate(root: Path, mutant: str) -> None:
    subprocess.run(
        [str(root / "scripts" / "_mutate_workflow.sh"), mutant],
        check=True,
        capture_output=True,
        text=True,
    )


class TestImagePublicationInvariants:
    """Consiliency/pmcp G: every registry write is behind the exact gate."""

    def test_the_committed_tree_passes(self, tmp_path: Path) -> None:
        image, release = _image_reasons(_image_tree(tmp_path))
        assert image == [] and release == []

    @pytest.mark.parametrize("mutant", IMAGE_MUTANTS)
    def test_mutant_is_rejected_with_the_image_tag(
        self, tmp_path: Path, mutant: str
    ) -> None:
        root = _image_tree(tmp_path)
        before, _ = _image_reasons(root)
        _mutate(root, mutant)
        after, release = _image_reasons(root)
        new = [r for r in after if r not in before]
        assert new, f"{mutant} was not rejected"
        assert all(r.startswith("[image] ") for r in after)
        if mutant == "image-docker-tag-push":
            assert release and release[0].startswith("[release]")

    def test_reordered_needs_are_an_equivalent_form(self, tmp_path: Path) -> None:
        root = _image_tree(tmp_path)
        before, _ = _image_reasons(root)
        _mutate(root, "image-needs-reordered")
        after, _ = _image_reasons(root)
        assert after == before

    def test_the_tag_allowlist_parameter_is_checked(self) -> None:
        docs = {
            p.name: yaml.safe_load(p.read_text()) for p in WORKFLOW_DIR.glob("*.y*ml")
        }
        reasons = cw.image_publication_invariants(docs, {"release.yml", "docker.yml"})
        assert any("TAG_TRIGGERED_WORKFLOWS" in r for r in reasons)

    @pytest.mark.parametrize(
        "command",
        [
            "docker push x",
            "docker image push x",
            # R8f: global options before push (Consiliency/pmcp#472, D1).
            'docker --config "$RUNNER_TEMP/cfg" push "$IMAGE_REF"',
            "docker -H unix:///x.sock image push x",
            "docker --tls push x",
            "podman --remote push x",
            "buildah --root /x push x",
            "podman push x",
            "podman image push x",
            "buildah push x",
            "crane copy a b",
            "oras push a b",
            "regctl image copy a b",
            "docker login ghcr.io",
            "docker buildx build --push .",
            "docker buildx build --output type=registry .",
            "docker buildx build --output type=image,push=true .",
            "curl -X PUT https://example.invalid/v2/x",
            "curl --request DELETE https://example.invalid/v2/x",
            "wget --method=PATCH https://example.invalid/v2/x",
            "curl -X POST \\\n  https://example.invalid/v2/x",
            "python3 scripts/image_publish.py promote",
            "python3 scripts/image_publish.py tag --version 3.0.1",
            "skopeo copy oci-archive:a docker://example.invalid/x",
            "skopeo delete docker://example.invalid/x",
            "Invoke-RestMethod -Method Put -Uri $env:URL -InFile f",
        ],
    )
    def test_publication_commands_outside_promote_main_fail(self, command: str) -> None:
        docs = {
            p.name: yaml.safe_load(p.read_text()) for p in WORKFLOW_DIR.glob("*.y*ml")
        }
        baseline = cw.image_publication_invariants(docs)
        assert baseline == [] and "maintenance.yml" in docs
        docs["extra.yml"] = {
            "on": {"pull_request": None},
            "jobs": {
                "x": {
                    "runs-on": "ubuntu-latest",
                    "timeout-minutes": 10,
                    "steps": [{"run": command}],
                }
            },
        }
        new = [r for r in cw.image_publication_invariants(docs) if r not in baseline]
        assert new and all(r.startswith("[image] ") for r in new)

    def test_the_build_smoke_skopeo_load_is_the_only_allowed_form(self) -> None:
        image = yaml.safe_load((WORKFLOW_DIR / "image.yml").read_text())
        loads = [
            s
            for s in image["jobs"]["build-smoke"]["steps"]
            if "skopeo" in str(s.get("run", ""))
        ]
        assert len(loads) == 1
        assert cw.SKOPEO_SMOKE_LOAD.fullmatch(loads[0]["run"].split("sock ", 1)[1])


class TestMutatingHttpClause:
    """Guard (d)'s curl/wget clause and its single pinned exemption."""

    def test_the_committed_notify_job_hashes_to_the_pin(self) -> None:
        doc = yaml.safe_load((WORKFLOW_DIR / "maintenance.yml").read_text())
        pin = cw.MUTATING_HTTP_EXEMPT[("maintenance.yml", "notify-worker")]
        actual = cw.job_pin(doc["jobs"]["notify-worker"])
        assert actual == pin, (
            f"maintenance.yml:notify-worker hashes to {actual}, but "
            f"MUTATING_HTTP_EXEMPT pins {pin}; re-review the job before re-pinning"
        )
        assert list(cw.MUTATING_HTTP_EXEMPT) == [("maintenance.yml", "notify-worker")]

    def test_a_job_that_does_not_serialize_is_not_exempt(self) -> None:
        assert cw.job_pin({"steps": [{"run": float("nan")}]}) is None
        assert cw.job_pin({"steps": [{"run": object()}]}) is None

    @pytest.mark.parametrize("mutant", HTTP_MUTANTS)
    def test_mutant_is_rejected_by_the_curl_wget_clause(
        self, tmp_path: Path, mutant: str
    ) -> None:
        root = _image_tree(tmp_path)
        before, _ = _image_reasons(root)
        _mutate(root, mutant)
        after, _ = _image_reasons(root)
        new = [r for r in after if r not in before]
        assert new and all(r.startswith("[image] ") for r in new)
        assert any(r.endswith(cw.MUTATING_HTTP_LABEL) for r in new), new

    @pytest.mark.parametrize(
        "command",
        [
            "curl -X PUT u",
            "curl -XPUT u",
            "curl --request PATCH u",
            "wget --method=DELETE u",
            "curl -T f u",
            "curl --upload-file f u",
            "curl -d x u",
            "curl -sd x u",
            "curl -sfT f u",
            "curl --data-binary @f u",
            "curl --data-raw x u",
            "curl --data-urlencode x u",
            "curl --json x u",
            "curl -F a=b u",
            "curl --form a=b u",
            "wget --post-data=x u",
            "wget --post-file=f u",
            "wget --body-data=x u",
            "wget --body-file=f u",
            # Round 1 (president R2): read as bash reads it.
            'curl "$URL?x=1&y=2" -X PUT',
            'curl "https://h/x;y" -T f',
            'curl "https://h/x|y" -d x',
            'curl -T/tmp/payload "$URL"',
            "curl -T./image.tar u",
            'curl -d@payload "$URL"',
            'curl -d\'{"a":1}\' "$URL"',
            "curl -sfd@f u",
            'curl -F"x=y" "$URL"',
            "curl -F'file=@image.tar' u",
            'curl -X"PUT" u',
            "curl -X'DELETE' u",
            'curl --""data @f u',
            'curl -""X PUT u',
            'c""url -X PUT u',
            'wget "http://e.com?a=1&b=2" --post-data=abc',
            'wget "http://e.com?a=1&b=2" --method=DELETE',
            'wget --post-""data=x u',
            # Regression cases, each flagged at 5d416e4.
            "/usr/bin/curl -T f u",
            "timeout 5 curl -X PUT u",
            "X=$(curl -sf -X PUT u)",
            "X=`curl -T f u`",
            "env A=1 curl -d x u",
            'bash -c "curl -X PUT u"',
            "sh -c 'curl -d x u'",
            'ssh host "curl -T f u"',
        ],
    )
    def test_the_detector_flags_explicit_and_implied_methods(
        self, command: str
    ) -> None:
        assert cw.mutating_http(command)

    @pytest.mark.parametrize(
        "command",
        [
            "curl -sf --max-time 2 http://127.0.0.1:3399/health",
            "curl -fsSL -o a.tgz https://example.invalid/a.tgz",
            "curl -X GET u",
            "curl -I u",
            'curl -s "https://h/x?a=1&b=2"',
            'curl -s "$URL" | grep -F needle',
            'curl -fsSL -o a.tgz "$URL" && tar -xzf a.tgz -T files.txt',
            'wget -qO- "https://h/x?a=1&b=2"',
        ],
    )
    def test_the_detector_leaves_reads_alone(self, command: str) -> None:
        assert not cw.mutating_http(command)

    def test_the_existing_gets_in_test_yml_are_not_flagged(self) -> None:
        texts = [
            cw._step_text(step)
            for job in _TEST_WF["jobs"].values()
            for step in cw._steps(job)
            if "curl" in str(step.get("run", ""))
        ]
        assert len(texts) == 2
        assert not any(cw.mutating_http(text) for text in texts)

    @pytest.mark.parametrize("where", ["step", "job", "workflow"])
    def test_shell_and_defaults_are_scanned_and_never_exempt(self, where: str) -> None:
        docs = {
            p.name: yaml.safe_load(p.read_text()) for p in WORKFLOW_DIR.glob("*.y*ml")
        }
        baseline = cw.image_publication_invariants(docs)
        shell = 'curl -sf -X PUT --data-binary @{0} "$URL"'
        notify = docs["maintenance.yml"]
        if where == "step":
            notify["jobs"]["notify-worker"]["steps"][0]["shell"] = shell
        elif where == "job":
            notify["jobs"]["notify-worker"]["defaults"] = {"run": {"shell": shell}}
        else:
            notify["defaults"] = {"run": {"shell": shell}}
        new = [r for r in cw.image_publication_invariants(docs) if r not in baseline]
        assert any(r.endswith(cw.MUTATING_HTTP_LABEL) for r in new), new


class TestRoundOneGuards:
    """PR Consiliency/pmcp#463 round 1: R1 (top-level keys), R3, R4."""

    @pytest.mark.parametrize("mutant", sorted(SPECIFIC_REASONS))
    def test_mutant_reports_its_specific_reason(
        self, tmp_path: Path, mutant: str
    ) -> None:
        root = _image_tree(tmp_path)
        before, _ = _image_reasons(root)
        _mutate(root, mutant)
        after, _ = _image_reasons(root)
        new = [r for r in after if r not in before]
        assert any(SPECIFIC_REASONS[mutant] in r for r in new), new

    @pytest.mark.parametrize(
        ("key", "value"),
        [
            ("env", {"BASH_ENV": "$(echo open=yes >>$GITHUB_OUTPUT)"}),
            ("defaults", {"run": {"shell": "bash {0}"}}),
            ("concurrency", {"group": "x"}),
        ],
    )
    def test_extra_top_level_keys_in_image_yml_are_refused(
        self, key: str, value: Any
    ) -> None:
        docs = {
            p.name: yaml.safe_load(p.read_text()) for p in WORKFLOW_DIR.glob("*.y*ml")
        }
        reason = "image.yml top-level keys must be a subset of"
        assert not any(reason in r for r in cw.image_publication_invariants(docs))
        docs["image.yml"][key] = value
        new = [r for r in cw.image_publication_invariants(docs) if reason in r]
        assert new and key in new[0]


def _nested_bash_c(
    depth: int, *, double: bool, inner: str = 'curl -s -X PUT "$URL"'
) -> str:
    """``inner`` inside ``depth`` nested ``bash -c`` layers."""
    import shlex

    text = inner
    for _ in range(depth):
        if double:
            escaped = text.replace("\\", "\\\\").replace('"', '\\"')
            escaped = escaped.replace("$", "\\$").replace("`", "\\`")
            text = f'bash -c "{escaped}"'
        else:
            text = "bash -c " + shlex.quote(text)
    return text


# Writes that 5d416e4 refused and the round-1 shell-word classifier accepted
# (president re-check of PR Consiliency/pmcp#463, A1-A23). R6 keeps the
# 5d416e4 detector as a second check, so each must stay flagged.
REGRESSION_CASES_5D416E4 = {
    "A1": 'curl -s $(echo -s) -X PUT "$URL"',
    "A2": 'curl -s --max-time $((30)) -X PUT "$URL"',
    "A3": 'curl -s `echo -s` -X PUT "$URL"',
    "A4": 'curl -s -o >(cat >/dev/null) -X PUT "$URL"',
    "A5": 'curl -s -u $(echo a:b) -T f "$URL"',
    "A6": "# don't retry\ncurl -sf '<url>' -X PUT\n# that's all",
    "A7": "# don't retry\ncurl -s -X 'PUT' \"$URL\"\n# that's all",
    "A8": '# don\'t log the token\nX=$(curl -s -X PUT "$URL")',
    "A9": '# it\'s fine\ntrue;curl -s -X PUT "$URL"',
    "A10": '# it\'s fine\n(curl -s -X PUT "$URL")',
    "A11": '# it\'s fine\nX=`curl -s -T f "$URL"`',
    "A12": '# it\'s fine\ntrue&&curl -s -d x "$URL"',
    "A13": 'cat <<EOF >/dev/null\nit\'s\nEOF\nX=$(curl -s -X PUT "$URL")',
    "A14-depth5-double": _nested_bash_c(5, double=True),
    "A14-depth5-single": _nested_bash_c(5, double=False),
    "A14-depth8-double": _nested_bash_c(8, double=True),
    "A14-depth8-single": _nested_bash_c(8, double=False),
    "A15-depth6-double": _nested_bash_c(6, double=True),
    "A15-depth6-single": _nested_bash_c(6, double=False),
    "A16": 'curl.exe -s -X PUT "$URL"',
    "A17": 'curl -s -X "PUT " "$URL"',
    "A18": '# don\'t log it\nX=$(wget -q -O- --post-data=x "$URL")',
    "A19": 'wget -q -O /dev/null $(echo -q) --method=PUT "$URL"',
    "A20": '$(which curl) -s -X PUT "$URL"',
    "A21": '"$(command -v curl)" -s -X PUT "$URL"',
    "A22": '${CURL:-curl} -s -X PUT "$URL"',
    "A23": 'curl -s -X P${EMPTY}UT "$URL"',
}


class TestNeverWeakerThan5d416e4:
    """R6: the guard's curl/wget clause is the union of both detectors."""

    @pytest.mark.parametrize(
        "case", sorted(REGRESSION_CASES_5D416E4), ids=sorted(REGRESSION_CASES_5D416E4)
    )
    def test_each_5d416e4_refusal_stays_flagged(self, case: str) -> None:
        assert cw.mutating_http(REGRESSION_CASES_5D416E4[case])

    @pytest.mark.parametrize(
        "case", sorted(REGRESSION_CASES_5D416E4), ids=sorted(REGRESSION_CASES_5D416E4)
    )
    def test_the_kept_5d416e4_detector_flags_each_case(self, case: str) -> None:
        assert cw._mutating_http_5d416e4(REGRESSION_CASES_5D416E4[case])

    def test_the_union_never_unflags_what_the_old_detector_flags(self) -> None:
        texts = [
            cw._step_text(step)
            for path in WORKFLOW_DIR.glob("*.y*ml")
            for job in cw._jobs(yaml.safe_load(path.read_text())).values()
            for step in cw._steps(job)
        ]
        assert texts
        for text in texts:
            if cw._mutating_http_5d416e4(text):
                assert cw.mutating_http(text)

    def test_the_floor_is_still_wired_in_when_the_classifier_says_no(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Consiliency/pmcp#472 item 1: the classifier now flags A1-A23 by
        # itself, so only a stubbed classifier shows that the OR is still there.
        monkeypatch.setattr(cw, "_http_writes", lambda *a: False)
        missed = [
            case
            for case, text in sorted(REGRESSION_CASES_5D416E4.items())
            if not cw.mutating_http(text)
        ]
        assert missed == []


# Consiliency/pmcp#472 (president re-check R7 and R8 of PR Consiliency/pmcp#463).
# Each sent PUT or POST when run as GitHub runs a bash step (bash --noprofile
# --norc -eo pipefail, bash 5.3.9, curl 8.18.0, GNU Wget 1.25.0, loopback
# server), and the 5d416e4 floor misses each one.
BASH_GRAMMAR_CASES = {
    "H1": 'curl -s $(true) "$URL?a=1&b=2" -X PUT',
    "H2": "# don't retry\ncurl -s '<url>' -d@report.json\n# that's all",
    "H3": "cat <<EOF >a\nit's\nEOF\ncurl -s '<url>' -d@report.json\n"
    "cat <<EOF >b\nthat's\nEOF",
    "H4-depth5-double": _nested_bash_c(
        5, double=True, inner='curl -s -d@report.json "$URL"'
    ),
    "H4-depth5-single": _nested_bash_c(
        5, double=False, inner='curl -s -d@report.json "$URL"'
    ),
    "H4-depth8-double": _nested_bash_c(
        8, double=True, inner='curl -s -d@report.json "$URL"'
    ),
    "H4-depth8-single": _nested_bash_c(
        8, double=False, inner='curl -s -d@report.json "$URL"'
    ),
    "H5": 'curl -s $(echo -s) -d@report.json "$URL"',
    "S1": 'curl -s "$URL" 2>&1 -X PUT',
    "S2": 'curl -s &>/dev/null -X PUT "$URL"',
    "S3": 'curl -s -#T f "$URL"',
    "S4": 'curl -s -#d x "$URL"',
    "S5": 'bash -c curl\\ -s\\ -X\\ PUT\\ \\"\\$URL\\"',
    "S6": "curl -s $'-XPUT' \"$URL\"",
    "S7": 'curl -s --expand-request PUT "$URL"',
    "S8": 'curl -s --expand-data x "$URL"',
    "S9": 'curl -s --expand-upload-file f "$URL"',
    "S10": 'curl -s --expand-json "{}" "$URL"',
    "S11": 'curl -s --expand-form a=b "$URL"',
    "S12": 'wget -q -O /dev/null --meth=PUT "$URL"',
    "S13": 'wget -q -O /dev/null --post-d=x "$URL"',
    "S14": 'wget -q -O /dev/null --post-f=f "$URL"',
    "S15": 'wget -q -O /dev/null --meth=PUT --body-d=x "$URL"',
    # Beyond the issue's list, each also a live PUT or POST missed by the
    # floor: bash grammar the cases above do not exercise.
    "X1-case-in-cmdsubst": 'X=$(case a in a) curl -s -d@report.json "$URL";; esac)',
    "X2-case-paren-hides-args": "curl -s $(case a in a) echo -s;; esac) "
    '"$URL?a&b" -d@report.json',
    "X3-bash-heredoc-unquoted": 'bash <<EOF\ncurl -s "$URL?a&b" -d@report.json\nEOF',
    "X4-bash-heredoc-quoted": "bash <<'EOF'\ncurl -s \"$URL?a&b\" -d@report.json\nEOF",
    "X5-heredoc-dash-tabs": "cat <<-EOF >/dev/null\n\tit's\n\tEOF\n"
    'curl -s "$URL?a&b" -d@report.json',
    "X6-heredoc-cmdsubst-body": "cat <<EOF >/dev/null\n"
    'it\'s $(curl -s "$URL?a&b" -d@report.json)\nEOF',
    "X7-herestring": 'bash <<< "curl -s \\"\\$URL?a&b\\" -d@report.json"',
    "X8-ansi-c-hex": "curl -s $'\\x2dXPUT' \"$URL\"",
    "X9-noclobber-redirect": 'curl -s >|/dev/null "$URL?a&b" -d@report.json',
    "X10-fd-var-redirect": 'curl -s {fd}>&2 "$URL?a&b" -d@report.json',
    "X11-procsub-in": 'curl -s -H @<(echo X-A: 1) "$URL?a&b" -d@report.json',
    "X12-arith-shift": 'curl -s --max-time $(( 1 << 4 )) "$URL?a&b" -d@report.json',
    "X13-param-default-cmdsubst": 'X=${Y:-$(curl -s "$URL?a&b" -d@report.json)}',
    "X14-wget-execute": 'wget -q -O /dev/null -e post_data=x "$URL"',
    "X16-brace-method": 'curl -s -X{PUT,PUT} "$URL"',
    "X17-quoted-cmd-in-dquote-cmdsubst": 'echo "$(curl -s "$URL?a&b" -d@report.json)"',
    "X18-dollar-dquote": 'curl -s $"-dx" "$URL"',
    "X19-escaped-newline-continued": 'curl -s "$URL?a&b" \\\n  -d@report.json',
    "X20-group-braces": '{ curl -s "$URL?a&b" -d@report.json; }',
    "X21-if-condition": 'if curl -s "$URL?a&b" -d@report.json; then :; fi',
    "X22-time-prefix": 'time curl -s "$URL?a&b" -d@report.json',
    "X23-wget-exe-abbrev": 'wget.exe -q -O /dev/null --post-d=x "$URL"',
    "X26-unterminated-later-quote": 'curl -s "$URL?a&b" -d@report.json\necho "oops',
    "X27-comment-after-cmd": 'curl -s "$URL?a&b" -d@report.json # it\'s done',
    "X28-subshell-inside-cmdsubst": 'X=$( (curl -s "$URL?a&b" -d@report.json) )',
    "X29-backtick-in-dquote": 'echo "`curl -s "$URL?a&b" -d@report.json`"',
    "X30-env-S": 'env -S "curl -s \\"$URL?a&b\\" -d@report.json"',
    "X31-ansi-escaped-quote": "bash -c $'curl -s \\'<url>?a&b\\' -d@report.json'",
    "X32-semicolon-ampersand-in-case": "case a in a) true;& b) "
    'curl -s "$URL?a&b" -d@report.json;; esac',
}

# Each sent only GET live, and must stay unflagged (N1-N5 and my additions).
BASH_GRAMMAR_READS = {
    "N1": "# don't retry\ncurl -s '<url>' -o /dev/null\n# that's all",
    "N2": 'curl -s $(echo -s) "$URL" -o /dev/null',
    "N3": 'curl -s "$URL" 2>&1 | grep -c x || true',
    "N4": 'curl -s -#fLo /dev/null "$URL"',
    "N5": 'cat <<EOF >notes\nit\'s\nEOF\ncurl -s "$URL" -o /dev/null',
    "XR1-case-get": 'X=$(case a in a) curl -s "$URL" -o /dev/null;; esac)',
    "XR3-arith": 'curl -s --max-time $((10 << 1)) "$URL" -o /dev/null',
    "XR4-get-explicit": 'curl -s -X get "$URL" -o /dev/null',
    "XR5-wget-execute-robots": 'wget -q -O /dev/null -e robots=off "$URL"',
    "XR6-curl-H-file": 'curl -s -H @<(echo X-A: 1) "$URL" -o /dev/null',
}


class TestBashGrammar:
    """Consiliency/pmcp#472: guard (d) reads the step text as bash does."""

    @pytest.mark.parametrize(
        "case", sorted(BASH_GRAMMAR_CASES), ids=sorted(BASH_GRAMMAR_CASES)
    )
    def test_each_write_is_flagged(self, case: str) -> None:
        assert cw.mutating_http(BASH_GRAMMAR_CASES[case])

    @pytest.mark.parametrize(
        "case", sorted(BASH_GRAMMAR_CASES), ids=sorted(BASH_GRAMMAR_CASES)
    )
    def test_the_5d416e4_floor_alone_misses_each_write(self, case: str) -> None:
        # So each case shows a rule of the new classifier, not the floor.
        assert not cw._mutating_http_5d416e4(BASH_GRAMMAR_CASES[case])

    @pytest.mark.parametrize(
        "case", sorted(BASH_GRAMMAR_READS), ids=sorted(BASH_GRAMMAR_READS)
    )
    def test_each_read_is_left_alone(self, case: str) -> None:
        assert not cw.mutating_http(BASH_GRAMMAR_READS[case])

    @pytest.mark.parametrize(
        "case", sorted(REGRESSION_CASES_5D416E4), ids=sorted(REGRESSION_CASES_5D416E4)
    )
    def test_the_classifier_alone_flags_each_5d416e4_case(self, case: str) -> None:
        assert cw._http_writes(cw._joined(REGRESSION_CASES_5D416E4[case]))

    @pytest.mark.parametrize("depth", [12, 60, 400])
    def test_deep_nesting_never_fails_open(self, depth: int) -> None:
        # `bash <<E1` / `bash <<E2` / ... grows linearly with depth (nested
        # quoting grows exponentially); 400 levels overflow Python's stack,
        # which must count as a write too.
        heads = "".join(f"bash <<E{level}\n" for level in range(depth))
        tails = "".join(f"E{level}\n" for level in reversed(range(depth)))
        text = heads + 'curl -s "$URL?a&b" -d@report.json\n' + tails
        assert not cw._mutating_http_5d416e4(text)
        assert cw.mutating_http(text)

    # The 2026-11-03 dev0 incident: guard (d)'s own work must stay bounded on
    # hostile text, and reaching the bound must flag (R7f). Each input is
    # linear in size; each would be quadratic or worse without the bound.
    BOUNDED_INPUTS = {
        "arithmetic-lookahead": "$((" * 20_000,
        "command-arithmetic-lookahead": "((" * 20_000,
        "many-curl-words": "curl " * 40_000 + "-s",
        "long-word": "curl -s " + "a" * 300_000,
        "many-quoted-parts": "curl -s " + "'a'" * 50_000,
        "many-dquoted-parts": 'curl -s "$URL" ' + '"a"' * 50_000,
        "nested-cmdsubst": "$(" * 5_000 + "curl -s" + ")" * 5_000,
        "nested-backticks": "echo " + "`" * 20_001,
        "unterminated-quotes": "curl " + "'a " * 40_000,
        "many-heredocs": "cat <<E\n" * 20_000,
        "many-comments": "curl -s u # x\n" * 25_000,
        "pwsh-many-words": "iwr " * 50_000,
        # PR Consiliency/pmcp#475: word shapes and parameter heads.
        "glued-tool-words": "${X}curl" * 25_000,
        "split-tool-words": "curl" + "$IFS-x" * 40_000,
        "long-default-word": "${X:-" + "a" * 240_000 + "}",
        "open-subscripts": "${a[" * 50_000,
        "closed-subscripts": "${a[}" * 40_000,
    }
    OVER_BOUND = {
        "arithmetic-lookahead",
        "command-arithmetic-lookahead",
        "many-curl-words",
        "long-word",
        "nested-cmdsubst",
        "many-heredocs",
        "pwsh-many-words",
    }

    @pytest.mark.timeout(60)
    @pytest.mark.parametrize("name", sorted(BOUNDED_INPUTS))
    def test_the_work_is_bounded_and_fails_closed(self, name: str) -> None:
        import time
        import tracemalloc

        text = self.BOUNDED_INPUTS[name]
        tracemalloc.start()
        started = time.process_time()
        try:
            bash = cw.mutating_http(text)
            pwsh = cw.mutating_powershell_http(text)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        elapsed = time.process_time() - started
        assert elapsed < 30, f"{name} took {elapsed:.1f}s"
        assert peak < 512 * 2**20, f"{name} peaked at {peak / 2**20:.0f} MiB"
        if name in self.OVER_BOUND:
            # Quadratic or deeper without the bound: reaching it must flag.
            assert bash or pwsh

    def test_many_short_words_charge_allocation_work(self) -> None:
        # Each small word allocates parser state; input byte count alone is
        # insufficient. Both classifiers must refuse without reaching a write.
        assert cw._http_writes("echo " + "a " * 20_000)
        assert cw.mutating_powershell_http("iwr " + "a " * 20_000)

    def test_reaching_the_work_bound_counts_as_a_write(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        text = 'curl -s "$URL" -o /dev/null'
        assert not cw.mutating_http(text)
        monkeypatch.setattr(cw, "_WORK_LIMIT", 10)
        assert cw.mutating_http(text)
        assert cw._http_writes(text)

    def test_the_committed_tree_stays_far_below_the_work_bound(self) -> None:
        for path in sorted(WORKFLOW_DIR.glob("*.y*ml")):
            for job in cw._jobs(yaml.safe_load(path.read_text())).values():
                for step in cw._steps(job):
                    work = cw._Work()
                    text = cw._joined(cw._step_text(step))
                    cw._classify(text, work)
                    assert work.spent < cw._WORK_LIMIT // 10, (path.name, work.spent)

    def test_a_stack_overflow_counts_as_a_write(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def overflow(text: str) -> bool:
            raise RecursionError

        monkeypatch.setattr(cw, "_http_writes", overflow)
        assert cw.mutating_http('curl -s "$URL"')

    @pytest.mark.parametrize(
        "text",
        [
            'X=$(curl -s "$URL?a&b" -d@report.json',
            'curl -s "$URL?a&b" -d@report.json `',
            'X=`curl -s "$URL?a&b" -d@report.json',
            'curl -s "$URL?a&b" ${X -d@report.json',
            'true;curl -s "$URL?a&b" -d\'@report.json',
        ],
    )
    def test_text_bash_cannot_finish_reading_is_read_conservatively(
        self, text: str
    ) -> None:
        assert not cw._ShellLexer(cw._joined(text)).run()
        assert cw.mutating_http(text)

    def test_the_committed_tree_lexes_completely_and_only_notify_is_flagged(
        self,
    ) -> None:
        texts = {}
        for path in sorted(WORKFLOW_DIR.glob("*.y*ml")):
            doc = yaml.safe_load(path.read_text())
            shells = [("<workflow>", cw._defaults_shell(doc))] + [
                (name, cw._defaults_shell(job)) for name, job in cw._jobs(doc).items()
            ]
            for where, shell in shells:
                if shell is not None:
                    texts[f"{path.name}:{where} defaults.run.shell"] = shell
            for name, job in cw._jobs(doc).items():
                for index, step in enumerate(cw._steps(job)):
                    texts[f"{path.name}:{name}#{index}"] = cw._step_text(step)
        assert len(texts) == 117
        assert [w for w, t in texts.items() if not cw._ShellLexer(t).run()] == []
        assert [w for w, t in texts.items() if cw.mutating_http(t)] == [
            "maintenance.yml:notify-worker#0"
        ]
        assert [w for w, t in texts.items() if cw.mutating_powershell_http(t)] == []

    @pytest.mark.parametrize(
        ("command", "pattern"),
        [
            ('docker --config "$RUNNER_TEMP/cfg" push "$IMAGE_REF"', "docker push"),
            ("docker -H unix:///x.sock image push x", "docker push"),
            ("docker --tls push x", "docker push"),
            ("docker --config=/x --debug image push x", "docker push"),
            ("podman --remote push x", "podman push"),
            ("buildah --root /x push x", "buildah push"),
        ],
    )
    def test_global_options_before_push_are_publication(
        self, command: str, pattern: str
    ) -> None:
        assert cw.PUBLICATION_PATTERNS[pattern].search(command)

    @pytest.mark.parametrize(
        "command",
        [
            "docker tag a push",
            "docker run --rm x push",
            "docker --config x run push",
            "docker buildx build --load .",
        ],
    )
    def test_a_push_word_after_a_subcommand_is_not_publication(
        self, command: str
    ) -> None:
        labels = [
            label
            for label in ("docker push", "podman push", "buildah push")
            if cw.PUBLICATION_PATTERNS[label].search(command)
        ]
        assert labels == []

    @pytest.mark.parametrize(
        "glued",
        [
            "$(skopeo copy docker://example.invalid/a:t docker://example.invalid/b:t)",
            "`skopeo copy docker://example.invalid/a:t docker://example.invalid/b:t`",
            '"x";skopeo copy docker://example.invalid/a:t docker://example.invalid/b:t',
            "|skopeo copy docker://example.invalid/a:t docker://example.invalid/b:t",
        ],
    )
    def test_anything_glued_to_the_smoke_load_leaves_skopeo_refused(
        self, glued: str
    ) -> None:
        docs = {
            p.name: yaml.safe_load(p.read_text()) for p in WORKFLOW_DIR.glob("*.y*ml")
        }
        assert cw.image_publication_invariants(docs) == []
        steps = docs["image.yml"]["jobs"]["build-smoke"]["steps"]
        load = next(s for s in steps if "skopeo" in str(s.get("run", "")))
        assert load["run"].endswith("docker-daemon:pmcp-image-smoke:candidate")
        load["run"] += glued
        reason = "uses skopeo outside the pinned local load in build-smoke"
        assert any(reason in r for r in cw.image_publication_invariants(docs))


# PR Consiliency/pmcp#475 review F1 and F2: the command word built with an
# expansion. F1 is the ${name<op>word} operator class whose word can become
# the value (-, :-, =, :=, +, :+ and the pattern substitutions); F2 is a tool
# name glued to an expansion that may be empty or split the word. Each sent
# POST or PUT live on ai (bash --noprofile --norc -eo pipefail, bash 5.2.21,
# curl 8.5.0, GNU Wget 1.21.4, loopback server; evidence in the PR), and the
# 5d416e4 floor misses each one.
EXPANSION_WORD_CASES = {
    "F1-colon-dash": '${X:-curl} -s -d@report.json "$URL"',
    "F1-quoted-idiom": '"${HTTP_CLIENT:-curl}" -sf -d@report.json "$URL"',
    "F1-dash": '${X-curl} -s -d@report.json "$URL"',
    "F1-wget-execute": '${X:-wget} -q -O /dev/null -epost_data=x "$URL"',
    "F1-wget-abbrev": '${X-wget} -q -O /dev/null --post-d=x "$URL"',
    "F1-assign-default": '${X:=curl} -s -d@report.json "$URL"',
    "F1-assign-dash": '${X=curl} -s -d@report.json "$URL"',
    "F1-alternate": 'X=1; ${X:+curl} -s -d@report.json "$URL"',
    "F1-alternate-plus": 'X=1; ${X+curl} -s -d@report.json "$URL"',
    "F1-substitution": 'X=a; ${X/a/curl} -s -d@report.json "$URL"',
    "F1-substitution-all": 'X=a; ${X//a/curl} -s -d@report.json "$URL"',
    "F1-default-glued": '${X:-cu}rl -s -d@report.json "$URL"',
    "F1-default-path": '${X:-/usr/bin/curl} -s -d@report.json "$URL"',
    "F1-default-exe": '${X:-curl.exe} -s -d@report.json "$URL"',
    "F1-nested-default": '${X:-${Y:-curl}} -s -d@report.json "$URL"',
    "F1-quoted-word": "${X:-'curl'} -s -d@report.json \"$URL\"",
    "F1-after-env": 'env ${X:-curl} -s -d@report.json "$URL"',
    "F1-default-with-blank": '${X:-env curl} -s -d@report.json "$URL"',
    "F1-long-name-default": "${" + "A" * 2000 + ':-cu}rl -s -d@report.json "$URL"',
    "F2-braced-glue": '${X}curl -s -d@report.json "$URL"',
    "F2-empty-quotes": '$X""curl -s -d@report.json "$URL"',
    "F2-quoted-glue": '"$X"curl -s -d@report.json "$URL"',
    "F2-inside-name": 'cu${X}rl -s -d@report.json "$URL"',
    "F2-trailing": 'curl${X} -s -d@report.json "$URL"',
    "F2-cmdsubst-glue": '$(true)curl -s -d@report.json "$URL"',
    "F2-backtick-glue": '`true`curl -s -d@report.json "$URL"',
    "F2-path-glue": '${X}/usr/bin/curl -s -d@report.json "$URL"',
    "F2-split-after-literal": 'env${IFS}curl -s -d@report.json "$URL"',
    "F2-ifs": 'curl$IFS-s$IFS-d@report.json$IFS"$URL"',
    "F2-ifs-braced": 'curl${IFS}-d@report.json${IFS}"$URL"',
    "F2-ifs-wget": 'wget$IFS-q$IFS-O/dev/null$IFS--post-d=x$IFS"$URL"',
    "F2-ifs-method": 'curl$IFS-X$IFS"PUT"$IFS"$URL"',
}

# Each sent only GET, or nothing, live, and must stay unflagged.
EXPANSION_WORD_READS = {
    "F1R-default-get": '${X:-curl} -s -o /dev/null "$URL"',
    "F1R-quoted-default-get": '"${HTTP_CLIENT:-curl}" -sf -o /dev/null "$URL"',
    "F1R-default-path-arg": 'wget -q -O "${TMP:-/tmp}/curl.tar" "$URL"',
    "F2R-ifs-get": 'curl$IFS-s$IFS-o$IFS/dev/null$IFS"$URL"',
    "F2R-glue-get": '${X}curl -s -o /dev/null "$URL"',
    "F2R-longer-name": "curl-config${X} --version || true",
    "F2R-path-prefix": 'curl -s -o "$HOME/curl-out" "$URL"',
}

# Accepted false positives (fail closed): each sent nothing live, and each is
# flagged. Every expansion counts as a possible split point, quoted or not,
# and the older raw-text rule reads curl/wget anywhere inside an expansion.
EXPANSION_WORD_ACCEPTED_FALSE_POSITIVES = {
    "FX-quoted-glue-no-split": '"curl${X}-d@report.json" "$URL" || true',
    "FX-strip-operator": '${X#curl} -s -d@report.json "$URL" || true',
    "FX-echo-default": 'echo "${X:-curl}" -d@report.json',
    "FX-substitution-pattern": 'X=a; ${X/curl/a} -s -d@report.json "$URL" || true',
}


PANEL_OPTION_WRITES = {
    "long-default": 'curl -s --${X:-data} @report.json "$URL"',
    "short-empty": 'curl -s -${Y}d@report.json "$URL"',
    "short-default": 'curl -s -${Y:-d}@report.json "$URL"',
    "long-empty": 'curl -s --d${E}ata @report.json "$URL"',
    "request-default": 'curl -s --${X:-request}=PUT "$URL"',
    "wget-long": 'wget -q --${X:-post-file}=report.json "$URL"',
    "quoted-empty": 'curl -s -"$A"d@report.json "$URL"',
    "whole-option": 'curl "${X:--d@report.json}" "$URL"',
    "whole-extquote": 'curl "${X:-$\'-XPUT\'}" "$URL"',
    "whole-split": 'curl ${X:- -X PUT} "$URL"',
    "wget-whole": 'wget ${OPT:---post-data=x} "$URL"',
    "nested-extquote": r'''curl "${X:-${OPT:-$'\x2dd@report.json'}}" "$URL"''',
    "replacement": 'X=-s; curl "${X/#-s/-d@report.json}" "$URL"',
    "alternate": 'X=v; curl ${X:+--data} x "$URL"',
    "assigned-default": 'curl ${X:=--data} x "$URL"',
    "split-and-join": 'curl --${X:-request}${IFS}PUT "$URL"',
    "wget-execute": 'wget -q ${RC:--e post_data=x} "$URL"',
}
PANEL_OPTION_READS = {
    "long-output": 'curl --${X:-output} /dev/null "$URL"',
    "output-path": 'curl -o "${F:-/dev/null}" "$URL"',
    "whole-output": 'curl "${X:--o/dev/null}" "$URL"',
    "split-output": 'curl ${X:- --output /dev/null} "$URL"',
    "wget-output": 'wget --${X:-output-document}=/dev/null "$URL"',
}


PANEL_GRAMMAR_WRITES = {
    "grok7e-new1-bang-for": '! for (( i=0; i<1; i++ << true )); do :; done\ncurl -s $(\ntrue\n) "$URL" -d x',
    "grok7e-new1-time-for": 'time -p for (( i=0; i<1; i++ << true )); do :; done\ncurl -s $(\ntrue\n) "$URL" -d x',
    "grok7e-new1-brace-for": '{ for (( i=0; i<1; i++ << true )); do :; done; }\ncurl -s $(\ntrue\n) "$URL" -d x',
    "NEW1-if-arith-shift-heredoc": 'if (( 1 << true )); then :; fi\ncurl -s $(\ntrue\n) "$URL?a=1&b=2" -d@report.json',
    "NEW1-while-arith-shift-heredoc": 'while (( 0 << true )); do :; done\ncurl -s $(\ntrue\n) "$URL?a=1&b=2" -d@report.json',
    "NEW1-bang-arith-shift-heredoc": '! (( 0 << true ))\ncurl -s $(\ntrue\n) "$URL?a=1&b=2" -X PUT',
    "NEW1-time-p-arith-shift-heredoc": 'time -p (( 1 << true ))\ncurl -s $(\ntrue\n) "$URL?a=1&b=2" -d@report.json',
    "NEW2-subscript-nested-brace-opt": 'curl -s ${a[${i:-0}]:--d} x "$URL"',
    "NEW2-subscript-nested-bracket-opt": 'curl -s ${a[b[0]]:--d} x "$URL"',
    "NEW2-subscript-nested-brace-method": 'curl -s ${a[${i:-0}]:--X} PUT "$URL"',
    "NEW2-subscript-nested-brace-cmdword": '${a[${i:-0}]:-cu}rl -s -d@report.json "$URL"',
    "NEW2-subscript-nested-brace-wget": 'wget -q -O /dev/null ${a[${i:-0}]:---post-data=x} "$URL"',
    "prefix-time -p": 'curl -s $(time -p case a in a) echo -s;; esac; wait) "$URL?a=1&b=2" -d x',
    "prefix-time --": 'curl -s $(time -- case a in a) echo -s;; esac; wait) "$URL?a=1&b=2" -d x',
    "prefix-time -p --": 'curl -s $(time -p -- case a in a) echo -s;; esac; wait) "$URL?a=1&b=2" -d x',
    "prefix-coproc": 'curl -s $(coproc case a in a) echo -s;; esac; wait) "$URL?a=1&b=2" -d x',
    "prefix-coproc WORKER": 'curl -s $(coproc WORKER case a in a) echo -s;; esac; wait) "$URL?a=1&b=2" -d x',
    "function-one-line": 'curl -s $(function f { case a in a) echo -s;; esac; }; f) "$URL" -d x',
    "function-hyphen": 'curl -s $(function f-f { case a in a) echo -s;; esac; }; f-f) "$URL" -d x',
    "GF3-if-case": 'curl $(if case a in a) true;; esac; then echo -s; fi) "$URL?a=1&b=2" -d x',
    "GF3-while-case": 'curl $(while case a in a) false;; esac; do :; done; echo -s) "$URL?a=1&b=2" -d x',
    "GF3-until-case": 'curl $(until case a in a) true;; esac; do :; done; echo -s) "$URL?a=1&b=2" -d x',
    "GF1-ifs-long-default": 'curl$IFS-s$IFS--${X:-data} @report.json "$URL"',
    "GF1-ifs-short-empty": 'curl$IFS-s$IFS-${Y}d@report.json "$URL"',
    "GF1-ifs-short-default": 'curl$IFS-s$IFS-${Y:-d}@report.json "$URL"',
    "GF1-ifs-quoted-empty": 'curl$IFS-s$IFS-"$A"d@report.json "$URL"',
    "GF1-ifs-wget-long": 'wget$IFS-q$IFS-O/dev/null$IFS--${X:-post-file}=report.json "$URL"',
    "GF1-outer-default-short-X": '${Z:-curl -s -${Y:-X}} PUT "$URL"',
    "GF1-outer-default-long-request": '${Z:-curl -s --${X:-request}} PUT "$URL"',
    "GF2-continued-curl-name": 'cu\\\nrl -s -d@report.json "$URL"',
    "GF2-continued-short-dash": 'curl -s -\\\nd@report.json "$URL"',
    "GF2-continued-long-dashes": 'curl -s --\\\ndata @report.json "$URL"',
    "GF2-continued-wget-long": 'wget -q -O /dev/null --\\\npost-data=x "$URL"',
    "GF3-case-newline-in": 'curl -s $(case a\nin a) echo -s;; esac) "$URL?a=1&b=2" -d@report.json',
    "GF3-if-then-case": 'curl -s $(if true; then case a in a) echo -s;; esac; fi) "$URL?a=1&b=2" -d@report.json',
    "GF3-brace-group-case": 'curl -s $({ case a in a) echo -s;; esac; }) "$URL?a=1&b=2" -d@report.json',
    "GF4-arith-cmdsubst-quotes": "curl -s --max-time $(( $(printf ')));' \\' >/dev/null; echo 5) )) \"$URL?a=1&b=2\" -d@report.json",
    "N1-dash": '${X:-curl -}d x "$URL"',
    "N1-long": '${X:-curl --}data x "$URL"',
    "N1-empty": 'curl$IFS-${E}dx$IFS"$URL"',
    "N1-method": '${X:-curl -}X${E}PUT "$URL"',
    "N1-wget": '${X:-wget --}post-data=x "$URL"',
    "replacement-no-dash-pattern": 'X=old; curl "${X/#old/-d}" x "$URL"',
}


class TestPanelGrammarCorrections:
    @pytest.mark.parametrize("case", sorted(PANEL_GRAMMAR_WRITES))
    def test_raw_and_workflow_write(self, case: str) -> None:
        text = PANEL_GRAMMAR_WRITES[case]
        assert cw._http_writes(text)
        assert cw.mutating_http(text)
        # Round-trip a literal YAML block through the production entry point.
        document = yaml.safe_load(
            "jobs:\n  probe:\n    steps:\n      - run: |\n"
            + "".join("          " + line + "\n" for line in text.splitlines())
        )
        reasons = cw.image_publication_invariants({"probe.yml": document})
        assert any(cw.MUTATING_HTTP_LABEL in reason for reason in reasons)

    @pytest.mark.parametrize(
        "text",
        [
            '${X:-curl -}o /dev/null "$URL"',
            'curl$IFS-${E}o$IFS/dev/null$IFS"$URL"',
            'wget$IFS--${X:-output-document}=/dev/null "$URL"',
            'cu\\\nrl -o /dev/null "$URL"',
            'curl -\\\no /dev/null "$URL"',
            'curl $(case a\nin a) echo -s;; esac) -o /dev/null "$URL"',
            'curl $(if true; then case a in a) echo -s;; esac; fi) -o /dev/null "$URL"',
            'curl $({ case a in a) echo -s;; esac; }) -o /dev/null "$URL"',
            "curl --max-time $(( $(printf ')));' \\' >/dev/null; echo 5) )) -o /dev/null \"$URL\"",
        ],
    )
    def test_reads_stay_reads(self, text: str) -> None:
        assert not cw.mutating_http(text)

    def test_unlexable_remainder_preserves_glue(self) -> None:
        assert cw._http_writes('curl$IFS-${E}dx "$URL')

    @pytest.mark.parametrize(
        "tool,tail",
        [
            ("docker", "push x"),
            ("podman", "push x"),
            ("buildah", "push x"),
            ("skopeo", "delete x"),
        ],
    )
    @pytest.mark.parametrize("location", ["run", "job-shell", "workflow-shell"])
    def test_publication_continuations(
        self, tool: str, tail: str, location: str
    ) -> None:
        text = tool[:2] + "\\\n" + tool[2:] + " " + tail
        doc: dict[str, Any] = {"jobs": {"probe": {"steps": [{"run": "true"}]}}}
        if location == "run":
            doc["jobs"]["probe"]["steps"][0]["run"] = text
        else:
            node = doc if location == "workflow-shell" else doc["jobs"]["probe"]
            node["defaults"] = {"run": {"shell": text}}
        reasons = cw.image_publication_invariants({"probe.yml": doc})
        assert any(tool in reason for reason in reasons)


class TestPanelPowerShellQuoting:
    @pytest.mark.parametrize("opening", list("'‘’‚‛"))
    @pytest.mark.parametrize("closing", list("'‘’‚‛"))
    def test_single_quote_class(self, opening: str, closing: str) -> None:
        text = f"Invoke-RestMethod -Body {opening}a;b{closing} -Method "
        assert cw.mutating_powershell_http(text + "Put")
        assert not cw.mutating_powershell_http(text + "Get")

    @pytest.mark.parametrize("opening", list('"“”„'))
    @pytest.mark.parametrize("closing", list('"“”„'))
    def test_double_quote_class(self, opening: str, closing: str) -> None:
        text = f"Invoke-RestMethod -Body {opening}a|b{closing} -Method "
        assert cw.mutating_powershell_http(text + "Put")
        assert not cw.mutating_powershell_http(text + "Get")

    @pytest.mark.parametrize(
        "command",
        [
            "i`rm",
            "Invoke-RestMet`hod",
            "Invoke-`RestMethod",
            "& ‘Invoke-RestMethod’",
            "& “Invoke-RestMethod”",
            r'& "Microsoft.PowerShell.Utility\Invoke-RestMet`hod"',
        ],
    )
    def test_static_names(self, command: str) -> None:
        assert cw.mutating_powershell_http(command + " -Method Put")
        assert not cw.mutating_powershell_http(command + " -Method Get")

    @pytest.mark.parametrize(
        "comment", ["<# don't retry #>", '<# " #>', "<# outer <# ' nested #>"]
    )
    def test_block_comments(self, comment: str) -> None:
        assert cw.mutating_powershell_http(
            "Invoke-RestMethod " + comment + " -Method Put"
        )
        assert not cw.mutating_powershell_http(
            "Invoke-RestMethod " + comment + " -Method Get"
        )

    def test_block_comment_ends_at_first_terminator(self) -> None:
        assert cw.mutating_powershell_http(
            "Invoke-RestMethod <# nested-looking <# #> -Method Put # tail"
        )
        assert not cw.mutating_powershell_http(
            "Invoke-RestMethod <# nested-looking <# #> -Method Get # tail"
        )

    @pytest.mark.parametrize("method", ["Put", "Get"])
    def test_defaults_are_conservatively_refused(self, method: str) -> None:
        # Even GET defaults are refused: scope and configuration are not evaluated.
        assert cw.mutating_powershell_http(
            "$PSDefaultParameterValues['Invoke-RestMethod:Method']='"
            + method
            + "'; Invoke-RestMethod -Uri $u"
        )

    @pytest.mark.parametrize(
        "command",
        [
            "& ('Invoke-Rest'+'Method')",
            "& ('Inv' + 'oke-Rest' + 'Method')",
        ],
    )
    def test_literal_concatenated_names(self, command: str) -> None:
        assert cw.mutating_powershell_http(command + " -Method Put")
        assert not cw.mutating_powershell_http(command + " -Method Get")

    @pytest.mark.parametrize(
        "type_name", ["System.Net.WebClient", "System.Net.Http.HttpClient"]
    )
    def test_dotnet_clients_are_conservatively_refused(self, type_name: str) -> None:
        assert cw.mutating_powershell_http("$client = New-Object " + type_name)


class TestPanelOptionAndNulFindings:
    @pytest.mark.parametrize("case", sorted(PANEL_OPTION_WRITES))
    def test_option_shapes_write(self, case: str) -> None:
        assert cw._http_writes(PANEL_OPTION_WRITES[case])
        assert cw.mutating_http(PANEL_OPTION_WRITES[case])

    @pytest.mark.parametrize("case", sorted(PANEL_OPTION_READS))
    def test_option_shapes_read(self, case: str) -> None:
        assert not cw.mutating_http(PANEL_OPTION_READS[case])

    @pytest.mark.parametrize(
        "escape",
        [r"\0", r"\000", r"\400", r"\x00", r"\u0000", r"\U00000000", r"\c@", r"\c`"],
    )
    @pytest.mark.parametrize("context", ["direct", "parameter", "quoted-parameter"])
    def test_nul_truncates_only_the_quoted_segment(
        self, escape: str, context: str
    ) -> None:
        assert cw._ansi_c("cu" + escape + "ignored") == "cu"
        tool = "$'cu" + escape + "ignored'rl"
        if context != "direct":
            tool = "${X:-" + tool + "}"
        if context == "quoted-parameter":
            tool = '"' + tool + '"'
        assert cw._http_writes(tool + ' --data x "$URL"')
        assert not cw.mutating_http(tool + ' -o /dev/null "$URL"')

    def test_argument_shape_products_are_bounded(self) -> None:
        text = "curl -" + "${X}a" * 40 + ' "$URL"'
        assert len(text) < 1000
        assert cw._http_writes(text)


class TestPanelPowerShellForms:
    @pytest.mark.parametrize("dash", ["-", "\u2013", "\u2014", "\u2015"])
    @pytest.mark.parametrize(
        "command",
        [
            "Invoke-RestMethod",
            "& (Get-Command Invoke-RestMethod)",
            "& (gcm iwr)",
            ". ((gcm irm))",
            "& 'Invoke-RestMethod'",
            '& "Microsoft.PowerShell.Utility\\Invoke-RestMethod"',
        ],
    )
    def test_methods_across_command_expressions(self, dash: str, command: str) -> None:
        assert cw.mutating_powershell_http(f"{command} {dash}Method Put -Uri $u")
        assert cw.mutating_powershell_http(
            f"{command} {dash}CustomMethod PATCH -Uri $u"
        )
        assert not cw.mutating_powershell_http(f"{command} {dash}Method Get -Uri $u")


class TestParameterQuotesAndBraces:
    @pytest.mark.parametrize(
        "tool",
        [
            "${X:-$'cu'rl}",
            "\"${X:-$'cu'rl}\"",
            r"${X:-$'\x63url'}",
            '${X:-$"cu"rl}',
            "${X:-${Y:-'cu'rl}}",
            "\"${X/a/'cu'rl}\"",
            "{cu,}rl",
            "{cu,{x,}}rl",
            "c{u..u}rl",
            "c{u..z..+2}rl",
            "{wge,}t",
        ],
    )
    def test_write_and_read(self, tool: str) -> None:
        write_option = "--post-data=x" if "wge" in tool else "-d@report.json"
        assert cw._http_writes(f'{tool} {write_option} "$URL"')
        assert not cw.mutating_http(f'{tool} "$URL"')

    @pytest.mark.parametrize(
        "text",
        [
            '{curl,-sd@report.json} "$URL"',
            'curl -s {-d@report.json,"$URL"}',
            '{curl,-X,POST} "$URL"',
            '{wget,--post-data=x} "$URL"',
            'curl -{d,o}@report.json "$URL"',
        ],
    )
    def test_braces_supply_arguments(self, text: str) -> None:
        assert cw._http_writes(text)

    @pytest.mark.parametrize(
        "text",
        [
            '{curl,-s} -o /dev/null "$URL"',
            'curl -s {-o/dev/null,"$URL"}',
            '{curl,-X,GET} "$URL"',
        ],
    )
    def test_brace_reads(self, text: str) -> None:
        assert not cw.mutating_http(text)

    @pytest.mark.parametrize(
        ("word", "expected"),
        [
            ("a{b,c}{d,e}", ["abd", "abe", "acd", "ace"]),
            ("{a,{b,c}}", ["a", "b", "c"]),
            ("x{03..01}", ["x03", "x02", "x01"]),
            ("{d..a..2}", ["d", "b"]),
            ("{+01..+03}", ["1", "2", "3"]),
            ("{-1..+2}", ["-1", "0", "1", "2"]),
            (r"a\{b,c\}", [r"a\{b,c\}"]),
            ('a"{b,c}"', ['a"{b,c}"']),
            ("${X:-{a,b}}", ["${X:-{a,b}}"]),
            ("{a}", ["{a}"]),
        ],
    )
    def test_order_and_quote_protection(self, word: str, expected: list[str]) -> None:
        assert cw._brace_expand(word, cw._Work()) == expected

    @pytest.mark.parametrize(
        "word", ["{a,b}" * 30, "{1..999999999}", "{1.." + "9" * 50 + "}"]
    )
    def test_expansion_budget_fails_closed(self, word: str) -> None:
        assert cw._http_writes("echo " + word)

    @pytest.mark.parametrize("word", ["{Z..b}", "{Z..b}y", "x{Z..b}{q,r}"])
    def test_generated_quoting_syntax_is_refused(self, word: str) -> None:
        assert cw._http_writes("echo " + word)


class TestExpansionBuiltCommandWords:
    """PR Consiliency/pmcp#475 F1/F2: the tool word as bash may build it."""

    @pytest.mark.parametrize(
        "case", sorted(EXPANSION_WORD_CASES), ids=sorted(EXPANSION_WORD_CASES)
    )
    def test_each_write_is_flagged(self, case: str) -> None:
        assert cw.mutating_http(EXPANSION_WORD_CASES[case])

    @pytest.mark.parametrize(
        "case", sorted(EXPANSION_WORD_CASES), ids=sorted(EXPANSION_WORD_CASES)
    )
    def test_the_classifier_alone_flags_each_write(self, case: str) -> None:
        text = cw._joined(EXPANSION_WORD_CASES[case])
        assert cw._ShellLexer(text).run()
        assert cw._http_writes(text)

    @pytest.mark.parametrize(
        "case", sorted(EXPANSION_WORD_CASES), ids=sorted(EXPANSION_WORD_CASES)
    )
    def test_the_5d416e4_floor_alone_misses_each_write(self, case: str) -> None:
        assert not cw._mutating_http_5d416e4(EXPANSION_WORD_CASES[case])

    @pytest.mark.parametrize(
        "case", sorted(EXPANSION_WORD_READS), ids=sorted(EXPANSION_WORD_READS)
    )
    def test_each_read_is_left_alone(self, case: str) -> None:
        assert not cw.mutating_http(EXPANSION_WORD_READS[case])

    @pytest.mark.parametrize(
        "case",
        sorted(EXPANSION_WORD_ACCEPTED_FALSE_POSITIVES),
        ids=sorted(EXPANSION_WORD_ACCEPTED_FALSE_POSITIVES),
    )
    def test_each_accepted_false_positive_is_flagged(self, case: str) -> None:
        assert cw.mutating_http(EXPANSION_WORD_ACCEPTED_FALSE_POSITIVES[case])

    @pytest.mark.parametrize(
        "text",
        [
            '${X:-curl} -s -d@report.json "$URL" "oops',
            '${X-curl} -s -d@report.json "$URL" "oops',
            'curl$IFS-d@report.json$IFS"$URL" "oops',
            '${X}curl -s -d@report.json "$URL" "oops',
        ],
    )
    def test_the_unlexable_fallback_reads_the_same_class(self, text: str) -> None:
        assert not cw._ShellLexer(cw._joined(text)).run()
        assert not cw._mutating_http_5d416e4(text)
        assert cw.mutating_http(text)

    @pytest.mark.parametrize(
        ("expansion", "writes"),
        [
            ("${X:-curl}", True),
            ("${X-curl}", True),
            ("${X:=curl}", True),
            ("${X=curl}", True),
            ("${X:+curl}", True),
            ("${X+curl}", True),
            ("${X/a/curl}", True),
            ("${X//a/curl}", True),
            ("${X/#a/curl}", True),
            ("${X/%a/curl}", True),
            ("${X#curl}", False),
            ("${X##curl}", False),
            ("${X%curl}", False),
            ("${X%%curl}", False),
            ("${X:?curl}", False),
            ("${X^curl}", False),
            ("${X,,curl}", False),
            ("${X:1:curl}", False),
        ],
    )
    def test_the_operator_class_decides_whether_the_word_is_a_value(
        self, expansion: str, writes: bool
    ) -> None:
        # The word of a value operator is part of the word's shape; the word
        # of a pattern-removal, error, case or substring operator is not.
        # (Any curl/wget inside an expansion still counts by the raw-text
        # rule, which is older and broader, so `${X#curl} -d x` is flagged: a
        # false positive.)
        word = cw._Word()
        cw._ShellLexer(expansion).dollar(0, word, quoted=False)
        tools = [tool for tool, _ in cw._shape_tools(word.shape, cw._Work())]
        assert ("curl" in tools) is writes


# PR Consiliency/pmcp#475 review F3: the publication patterns as they stood,
# each a backtracking regex that is quadratic on one hostile line. The linear
# scanners replacing them must answer exactly as these do.
_OLD_GLOBAL_OPTIONS = r"""(?:\s+-\S*(?:\s+(?:"[^"\n]*"|'[^'\n]*'|[^\s"'-]\S*))?)*"""
OLD_PUBLICATION_REGEXES = {
    "docker push": rf"\bdocker{_OLD_GLOBAL_OPTIONS}\s+(?:image\s+)?push\b",
    "podman push": rf"\bpodman{_OLD_GLOBAL_OPTIONS}\s+(?:image\s+)?push\b",
    "buildah push": rf"\bbuildah{_OLD_GLOBAL_OPTIONS}\s+push\b",
    "a pushing type=image output": r"type=image[^\n]*push=true",
}
OLD_SKOPEO_DELETE = r"\bskopeo\b[^\n]*\bdelete\b"

_PUBLICATION_TOKENS = [
    "docker",
    "podman",
    "buildah",
    "Docker",
    "x-docker",
    "/usr/bin/docker",
    "dockerd",
    "push",
    "push;",
    "pushx",
    "image",
    "images",
    "-x",
    "--tls",
    "-",
    "a",
    "'a",
    "a'",
    '"a',
    'a"',
    '"',
    "'",
    '"a b"',
    "''",
    "type=image",
    "push=true",
    "skopeo",
    "SKOPEO",
    "delete",
    "deleted",
    ";",
]
_PUBLICATION_SEPARATORS = [" ", "  ", "\t", "\n", "", " "]


def _publication_texts(seed: int, count: int, most: int) -> list[str]:
    """``count`` texts of at most ``most`` tokens each (bounded, < 200 chars)."""
    import random

    rng = random.Random(seed)
    texts = []
    for _ in range(count):
        parts = []
        for _ in range(rng.randint(1, most)):
            parts.append(rng.choice(_PUBLICATION_TOKENS))
            parts.append(rng.choice(_PUBLICATION_SEPARATORS))
        texts.append("".join(parts))
    return texts


def _small_publication_texts() -> list[str]:
    """Every text of one or two tokens, each followed by any separator."""
    pieces = [t + sep for t in _PUBLICATION_TOKENS for sep in _PUBLICATION_SEPARATORS]
    return pieces + [a + b for a in pieces for b in pieces]


class TestLinearPublicationScans:
    """PR Consiliency/pmcp#475 F3: (d)'s publication scans are linear."""

    @pytest.mark.parametrize("label", sorted(OLD_PUBLICATION_REGEXES))
    def test_each_scanner_answers_as_the_old_regex(self, label: str) -> None:
        import re

        old = re.compile(OLD_PUBLICATION_REGEXES[label])
        new = cw.PUBLICATION_PATTERNS[label]
        texts = _small_publication_texts() + _publication_texts(475, 20_000, 12)
        differ = [t for t in texts if bool(old.search(t)) != bool(new.search(t))]
        assert differ == []
        # The generator reaches both answers, so the comparison is not vacuous.
        assert any(old.search(t) for t in texts)
        assert not all(old.search(t) for t in texts)

    def test_skopeo_delete_answers_as_the_old_regex(self) -> None:
        import re

        old = re.compile(OLD_SKOPEO_DELETE, re.IGNORECASE)
        texts = _small_publication_texts() + _publication_texts(4750, 20_000, 12)
        differ = [
            t for t in texts if bool(old.search(t)) != bool(cw.SKOPEO_DELETE.search(t))
        ]
        assert differ == []
        assert any(old.search(t) for t in texts)

    @pytest.mark.parametrize(
        ("label", "text"),
        [
            ("docker push", 'docker -x "a b" push x'),
            ("docker push", "docker -x 'a b' image push x"),
            ("docker push", "docker -x\n-y\tpush"),
            ("docker push", "/usr/bin/docker --config=/x push"),
            ("podman push", "podman -x a -y image push"),
            ("buildah push", "buildah --root /x push x"),
        ],
    )
    def test_the_scanners_still_find_option_chains(self, label: str, text: str) -> None:
        assert cw.PUBLICATION_PATTERNS[label].search(text)

    @pytest.mark.parametrize(
        ("label", "text"),
        [
            ("docker push", 'docker -x "a\nb" push x'),
            ("docker push", 'docker -x "a"b push'),
            ("docker push", "docker -x a b push"),
            ("buildah push", "buildah image push x"),
            ("docker push", "docker -x"),
        ],
    )
    def test_the_scanners_refuse_what_the_regex_refused(
        self, label: str, text: str
    ) -> None:
        import re

        assert not re.search(OLD_PUBLICATION_REGEXES[label], text)
        assert not cw.PUBLICATION_PATTERNS[label].search(text)

    # CPU budgets exclude time descheduled on a loaded CI host. These are
    # resource-regression checks, not elapsed-wall-time performance assertions.
    # Quadratic for the old regexes. Measured on ai: 18.5 s for 120 KB of
    # `docker -x `, 2.7 s for 264 KB of `type=image ` (550 KB here, about 4x
    # by the quadratic) and 7.4 s for 140 KB of `skopeo ` (210 KB here); the
    # scanners take < 0.03 s. Restoring each old regex fails these tests.
    HOSTILE = {
        "docker push": "docker -x " * 12_000,
        "podman push": "podman -x " * 12_000,
        "buildah push": "buildah -x " * 12_000,
        "a pushing type=image output": "type=image " * 50_000,
    }

    @pytest.mark.timeout(60)
    @pytest.mark.parametrize("label", sorted(HOSTILE))
    def test_a_hostile_line_is_scanned_in_linear_time(self, label: str) -> None:
        import time

        text = self.HOSTILE[label]
        started = time.process_time()
        assert not cw.PUBLICATION_PATTERNS[label].search(text)
        assert cw.PUBLICATION_PATTERNS[label].search(text + " push=true push")
        elapsed = time.process_time() - started
        assert elapsed < 3, f"{label} took {elapsed:.1f}s"

    @pytest.mark.timeout(60)
    def test_a_hostile_skopeo_line_is_scanned_in_linear_time(self) -> None:
        import time

        text = "skopeo " * 30_000
        started = time.process_time()
        assert not cw.SKOPEO_DELETE.search(text)
        assert cw.SKOPEO_DELETE.search(text + "delete")
        elapsed = time.process_time() - started
        assert elapsed < 3, f"skopeo took {elapsed:.1f}s"

    @pytest.mark.timeout(120)
    @pytest.mark.parametrize("label", sorted(HOSTILE))
    def test_the_checker_scans_a_hostile_step_in_bounded_time(self, label: str) -> None:
        import time

        docs = {
            p.name: yaml.safe_load(p.read_text()) for p in WORKFLOW_DIR.glob("*.y*ml")
        }
        text = self.HOSTILE[label]
        docs["test.yml"]["jobs"]["hostile"] = {
            "runs-on": "ubuntu-latest",
            "defaults": {"run": {"shell": text}},
            "steps": [{"run": text}],
        }
        started = time.process_time()
        reasons = cw.image_publication_invariants(docs)
        elapsed = time.process_time() - started
        assert elapsed < 30, f"{label} took {elapsed:.1f}s"
        assert not any(f"contains {label}" in r for r in reasons)


class TestFloorInputBound:
    """PR Consiliency/pmcp#475: the 5d416e4 floor runs outside the classifier's
    budget, and its `-[A-Za-z]*X[A-Za-z]+` fullmatch is quadratic in a token's
    run of letters, so a longer run than the limit flags without the floor."""

    def test_a_token_over_the_limit_flags_without_running_the_floor(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def floor(text: str) -> bool:
            raise AssertionError("the floor ran")

        text = "curl -o" + "X" * cw._FLOOR_LETTERS_LIMIT + '1 "$URL"'
        monkeypatch.setattr(cw, "_mutating_http_5d416e4", floor)
        assert cw.mutating_http(text)

    def test_a_token_at_the_limit_still_runs_the_floor(self) -> None:
        text = "curl -o" + "X" * (cw._FLOOR_LETTERS_LIMIT - 1) + '1 "$URL"'
        assert cw._floor_input_is_bounded(text)
        assert not cw.mutating_http(text)

    # About 15 s for the floor alone on ai without the bound (quadratic).
    @pytest.mark.timeout(60)
    @pytest.mark.parametrize(
        "text",
        [
            "curl -o" + "X" * 100_000 + '1 "$URL"',
            ("curl -o" + "X" * 1_023 + '1 "$URL"\n') * 200,
        ],
        ids=["one-long-token", "many-tokens-at-the-limit"],
    )
    def test_the_floor_input_is_bounded(self, text: str) -> None:
        import time

        started = time.process_time()
        cw.mutating_http(text)
        elapsed = time.process_time() - started
        assert elapsed < 3, f"took {elapsed:.1f}s"


class TestPowerShellWebCmdlets:
    """Consiliency/pmcp#472: Invoke-WebRequest/Invoke-RestMethod writes."""

    @pytest.mark.parametrize(
        "text",
        [
            "Invoke-WebRequest -Uri $u -Method Put -InFile f",
            "iwr $u -Method:Post",
            "irm -Method 'Delete' $u",
            "Invoke-RestMethod -CustomMethod PROPFIND $u",
            "Invoke-RestMethod -Uri $u -CM PATCH",
            "curl -Method Put u",
            "wget -Me Patch u",
            "IWR -METHOD PUT u",
            "Microsoft.PowerShell.Utility\\Invoke-WebRequest -Method Put u",
            "iwr @params",
            "Invoke-WebRequest -Method $m u",
            "Invoke-RestMethod -Uri $u `\n  -Method Post",
            "iwr -Method ([Microsoft.PowerShell.Commands.WebRequestMethod]::Put) u",
        ],
    )
    def test_writes_are_flagged(self, text: str) -> None:
        assert cw.mutating_powershell_http(text)

    @pytest.mark.parametrize(
        "text",
        [
            "Invoke-WebRequest -Uri $u -OutFile f",
            "iwr $u -Method Get",
            "irm $u -Method HEAD",
            "Invoke-WebRequest -Uri $u -Method 'GET'",
            "iwr u; Set-Foo -Method Post",
            "curl -sf -X GET u",
            "curl.exe -Method Put u",
        ],
    )
    def test_reads_are_left_alone(self, text: str) -> None:
        assert not cw.mutating_powershell_http(text)

    @pytest.mark.parametrize(
        "where", ["windows-default", "step-pwsh", "step-powershell", "job-defaults"]
    )
    def test_every_powershell_step_is_read(self, where: str) -> None:
        docs = {
            p.name: yaml.safe_load(p.read_text()) for p in WORKFLOW_DIR.glob("*.y*ml")
        }
        baseline = cw.image_publication_invariants(docs)
        job = docs["windows.yml"]["jobs"]["windows-smoke"]
        assert job["runs-on"] == "windows-latest"
        step: dict[str, Any] = {"run": "Invoke-RestMethod -Method Put -Uri $env:U"}
        if where == "step-pwsh":
            step["shell"] = "pwsh"
        elif where == "step-powershell":
            step = {"run": "curl -Method Put $env:U", "shell": "powershell"}
        elif where == "job-defaults":
            job["defaults"] = {"run": {"shell": "pwsh"}}
        job["steps"].append(step)
        new = [r for r in cw.image_publication_invariants(docs) if r not in baseline]
        # (The 5d416e4 floor also reads `curl -Method` as a short group with d.)
        assert (
            f"[image] windows.yml:windows-smoke contains {cw.MUTATING_PWSH_LABEL}"
            in new
        )


PANEL_FINAL_PS_WRITES = {
    "grok7e-hole-method-glued-quote": 'Invoke-RestMethod -Uri https://example.invalid -Method"Put" -InFile f',
    "grok7e-hole-custom-glued": 'Invoke-RestMethod -Uri https://example.invalid -CustomMethod"PROPFIND"',
    "grok7e-hole-cmd-glued-dquote": 'Invoke-RestMethod"https://example.invalid/p" -Method Put -InFile f',
    "grok7e-hole-cmd-glued-squote": "Invoke-RestMethod'https://example.invalid/p' -Method Put -InFile f",
    "grok7e-hole-format-tight": "& ('Invoke-{0}' -f'RestMethod') -Method Put -Uri http://example.invalid",
    "grok7e-hole-format-var-tight": "& ('Invoke-{0}' -f$cmd) -Method Put -Uri http://example.invalid",
    "grok7e-hole-join-tight": "& (('Invoke-','RestMethod')-join'') -Method Put -Uri http://example.invalid",
    "grok7e-hole-type-join": "[(-join ('System.Net.Web','Client'))]::new().UploadString('http://example.invalid','x')",
    "grok7e-hole-type-format": "[('{0}{1}' -f 'System.Net.Web','Client')]::new().UploadString('http://example.invalid','x')",
    "grok7e-hole-string-member-quote": "& ([string]::('Format')('Invoke-{0}','RestMethod')) -Method Put -Uri http://example.invalid",
    "grok7e-hole-string-member-concat": "& ([string]::('For'+'mat')('Invoke-{0}','RestMethod')) -Method Put -Uri http://example.invalid",
    "grok7e-hole-string-join-quote": "& ([System.String]::('Join')('', @('Invoke-','RestMethod'))) -Method Put",
    "grok7e-hole-child-array": "pwsh -NoProfile @('-Command','Invoke-RestMethod -Method Put -Uri http://example.invalid')",
    "grok7e-hole-child-paren": "pwsh -NoProfile ('-Command') 'Invoke-RestMethod -Method Put -Uri http://example.invalid'",
    "grok7e-hole-child-enc-array": "powershell.exe @('-EncodedCommand','QQ==')",
    "grok7e-hole-newitem-alias": "New-Item -Path Alias:x -Value Invoke-RestMethod; x -Method Put -Uri http://example.invalid",
    "grok7e-enc-direct": "powershell.exe -NoProfile -EncodedCommand QQ==",
    "grok7e-enc-array": "powershell.exe -NoProfile @('-EncodedCommand','QQ==')",
    "grok7e-enc-paren": "powershell.exe -NoProfile ('-EncodedCommand') 'QQ=='",
    "grok7e-command-array-plain": "pwsh -NoProfile @('-Command','Write-Output hi')",
    "grok7e-command-paren-plain": "pwsh -NoProfile ('-Command') 'Write-Output hi'",
    "grok7e-command-direct-plain": "pwsh -NoProfile -Command 'Write-Output hi'",
    "ruling-format-name": "& ('Invoke-{0}' -f 'RestMethod') -Method Put -Uri $u",
    "ruling-join-name": "& (-join ('Invoke-', 'RestMethod')) -Method Put -Uri $u",
    "ruling-format-type": "New-Object ('System.Net.{0}' -f 'WebClient')",
    "ruling-join-type": "[type](-join ('System.Net.', 'WebClient'))",
    "ruling-string-format": "& ([string]::Format('Invoke-{0}', 'RestMethod')) -Method Put",
    "ruling-string-join": "& ([System.String]::Join('', @('Invoke-', 'RestMethod'))) -Method Put",
    "ruling-string-concat": "& ([string]::Concat('Invoke-', 'RestMethod')) -Method Put",
    "ruling-scriptblock": "& ([scriptblock]::Create($text))",
    "ruling-newscriptblock": "$ExecutionContext.InvokeCommand.NewScriptBlock($text)",
    "ruling-invokescript": "$ExecutionContext.InvokeCommand.InvokeScript($text)",
    "ruling-addtype": "Add-Type -TypeDefinition $source",
    "ruling-setalias": "Set-Alias x Invoke-RestMethod; x -Method Put $u",
    "ruling-newalias": "New-Alias x Invoke-RestMethod; x -Method Put $u",
    "ruling-sal": "sal x irm; x -Method Put $u",
    "ruling-nal": "nal x irm; x -Method Put $u",
    "ruling-childcommand": "pwsh -NoProfile -Command $text",
    "ruling-childencoded": "powershell.exe -EncodedCommand $text",
    "NEW3a-colon-herestring-squote": "Invoke-RestMethod -Uri $env:URL -Body:@'\nit's\n'@ -Method Put",
    "NEW3a-colon-herestring-dquote": 'Invoke-RestMethod -Uri $env:URL -Body:@"\nsay "hi\n"@ -Method Put',
    "NEW3a-comma-herestring": "Invoke-RestMethod -Uri $env:URL -Body a,@'\nit's\n'@ -Method Put",
    "NEW3a-colon-block-comment": "Invoke-RestMethod -Uri $env:URL -Body:<# don't retry #>x -Method Put",
    "NEW3a-rparen-block-comment": "& (Get-Command Invoke-RestMethod)<# don't retry #> -Method Put -Uri $env:URL",
    "NEW3b-braced-var-squote": "${don't} = 'x'; Invoke-RestMethod -Uri $env:URL -Body ${don't} -Method Put",
    "NEW3b-dquote-braced-var-quote-semi": 'Invoke-RestMethod -Uri $env:URL -Body "${a";}" -Method Put',
    "NEW3c-quoted-winpath-iwr": "& 'C:\\Tools\\iwr' -Uri 'https://example.invalid' -Method Put -InFile f",
    "NEW3c-quoted-alias-provider-irm": "& 'Alias:irm' -Uri 'https://example.invalid' -Method Put -InFile f",
    "NEW3d-joined-backslash-comment-in-parens": "Invoke-RestMethod -Uri $env:URL -Body (\n  $payload # C:\\temp\\\n) -Method Put",
    "continued-comment": "Invoke-RestMethod -Uri $env:URL `\n# it's a note\n-Method Put -InFile f",
    "continued-here": "Invoke-RestMethod -Uri $env:URL -Body `\n@'\nit's\n'@ -Method Put",
    "quote-block": "Invoke-RestMethod -Uri 'http://example.invalid'<# it's #> -Method Put -InFile f",
    "group-block": "Invoke-RestMethod -Uri ('http://example.invalid')<# it's #> -Method Put -InFile f",
    "native-group-short": 'curl ("-d") x $env:URL',
    "native-group-long": "curl ('--data') x $env:URL",
    "native-smart-group": "curl (“-d”) x $env:URL",
    "native-array": "curl @('-d','x',$env:URL)",
    "native-nested-group": "curl.exe ((('-d'))) x $env:URL",
    "native-array-method": "curl.exe @('-X','PUT',$env:URL)",
    "native-wget-group": "wget.exe ('--post-data=x') $env:URL",
    "native-wget-array": "wget @('--method','PUT',$env:URL)",
    "C12-literal-invoker": "([System.Net.Http.HttpMessageInvoker]::new([System.Net.Http.SocketsHttpHandler]::new())).SendAsync([System.Net.Http.HttpRequestMessage]::new([System.Net.Http.HttpMethod]::Post, 'http://127.0.0.1:9/review'), [System.Threading.CancellationToken]::None).GetAwaiter().GetResult()",
    "H1-hash-in-url": "Invoke-RestMethod -Uri https://example.invalid/p#frag -Method Put -InFile f",
    "H1b-hash-iwr": "Invoke-WebRequest -Uri http://h.invalid/#/x -Method Post -Body x",
    "H2-herestring-apostrophe": "Invoke-RestMethod -Uri $env:URL -Body @'\nit's\n'@ -Method Put",
    "H2b-herestring-dquote": 'Invoke-RestMethod -Uri $env:URL -Body @"\nsay "hi\n"@ -Method Put',
    "H3-subexpr-nested-dquote": 'Invoke-RestMethod -Uri $env:URL -Body "$("a;b")" -Method Put',
    "H4-global-psdefault": "$global:PSDefaultParameterValues['Invoke-RestMethod:Method']='Put'; Invoke-RestMethod -Uri $env:URL -InFile f",
    "H5-braced-psdefault": "${PSDefaultParameterValues}['Invoke-RestMethod:Method']='Put'; Invoke-RestMethod -Uri $env:URL -InFile f",
    "H6-backtick-webclient": "(New-Object Net.Web`Client).UploadString($env:URL, 'x')",
    "H7-concat-webclient": "(New-Object ('Net.Web'+'Client')).UploadString($env:URL, 'x')",
    "H8-webrequest-create": "$r=[System.Net.WebRequest]::Create($env:URL); $r.Method='PUT'; $s=$r.GetRequestStream(); $s.Close(); $r.GetResponse()",
    "H9-hash-in-parens": "Invoke-RestMethod -Body (Write-Output a#b) -Method Put -Uri $env:URL",
    "N1-native-smart-quoted-opt": "curl “-d” x $env:URL",
    "N4-native-exe-smart-long": "curl.exe ‘--data’ x $env:URL",
    "mixed-concat": "& ('Invoke-Rest' + \"Method\") -Method Put",
    "mixed-reverse": "& (\"Invoke-Rest\" + 'Method') -Method Put",
    "escaped-default": "$PSDefault`ParameterValues['Invoke-RestMethod:Method']='Put'; Invoke-RestMethod -Uri $u",
    "groksubexpr": 'Invoke-RestMethod -Body "$( \'";\' )" -Method Put',
}


class TestPanelFinalRegressions:
    @pytest.mark.parametrize("dash", ["-", "–", "—", "―"])
    @pytest.mark.parametrize("operator", ["f", "join"])
    @pytest.mark.parametrize("operand", ["0", "0x0", "1,2,3", "-1", "+1", ".5", "$n"])
    def test_expression_operator_operand_boundaries(self, dash, operator, operand):
        # Policy refuses these name expressions even if they produce no command.
        for gap in ("", " "):
            text = (
                "& ('i{0:wr}'"
                + dash
                + operator
                + gap
                + operand
                + ") -Method Put -Uri $u"
            )
            assert cw.mutating_powershell_http(text)
            docs = {
                "probe.yml": {
                    "jobs": {"probe": {"steps": [{"shell": "pwsh", "run": text}]}}
                }
            }
            assert any(
                cw.MUTATING_PWSH_LABEL in r
                for r in cw.image_publication_invariants(docs)
            )

    @pytest.mark.parametrize(
        "command",
        [
            "New-Item",
            "Set-Item",
            "Copy-Item",
            "Move-Item",
            "Rename-Item",
            "ni",
            "si",
            "cpi",
            "cp",
            "copy",
            "mi",
            "mv",
            "move",
            "rni",
            "ren",
        ],
    )
    @pytest.mark.parametrize("method", ["Put", "Get"])
    def test_alias_provider_operation_spellings(self, command, method):
        text = (
            command
            + " -Path Alias:review475 -Value Invoke-RestMethod; review475 -Method "
            + method
            + " -Uri $u"
        )
        # Alias-provider manipulation is refused even without a later write.
        assert cw.mutating_powershell_http(text)
        docs = {
            "probe.yml": {
                "jobs": {"probe": {"steps": [{"shell": "pwsh", "run": text}]}}
            }
        }
        assert any(
            cw.MUTATING_PWSH_LABEL in r for r in cw.image_publication_invariants(docs)
        )
        assert not cw.mutating_powershell_http(
            command + " -Path ./example -Value plain"
        )

    @pytest.mark.parametrize(
        "text",
        [
            "& 'C:\\Tools\\iwr' -Uri 'https://example.invalid' -Method Get",
            "Invoke-WebRequest -Uri $u -Method Get",
            'Invoke-RestMethod -Uri https://example.invalid -Method"Get"',
            'Invoke-RestMethod"https://example.invalid/p" -Method Get',
        ],
    )
    def test_glued_quote_reads(self, text: str) -> None:
        assert not cw.mutating_powershell_http(text)

    @pytest.mark.parametrize(
        "text",
        [
            '! for (( i=0; i<1; i++ << true )); do :; done\ncurl -s $(\ntrue\n) "$URL" -o /dev/null',
            'time -p for (( i=0; i<1; i++ << true )); do :; done\ncurl -s $(\ntrue\n) "$URL" -o /dev/null',
            '{ for (( i=0; i<1; i++ << true )); do :; done; }\ncurl -s $(\ntrue\n) "$URL" -o /dev/null',
        ],
    )
    def test_prefixed_arithmetic_for_reads(self, text: str) -> None:
        assert not cw.mutating_http(text)

    @pytest.mark.parametrize("dash", ["-", "–", "—", "―"])
    @pytest.mark.parametrize("operator", ["f", "join"])
    @pytest.mark.parametrize(
        "site", ["& ({})", "& $({})", '& "$({})"', ". ({})", ". $({})"]
    )
    @pytest.mark.parametrize("method", ["Put", "Get"])
    def test_literal_operator_tokens_and_invocation_sites(
        self, dash: str, operator: str, site: str, method: str
    ) -> None:
        expression = (
            "'Invoke-{0}'" + dash + "f'RestMethod'"
            if operator == "f"
            else "('Invoke-','RestMethod')" + dash + "join''"
        )
        text = site.format(expression) + " -Method " + method + " -Uri $u"
        # GET is a documented conservative refusal of expression-built names.
        assert cw.mutating_powershell_http(text)
        docs = {
            "probe.yml": {
                "jobs": {"probe": {"steps": [{"shell": "pwsh", "run": text}]}}
            }
        }
        assert any(
            cw.MUTATING_PWSH_LABEL in r for r in cw.image_publication_invariants(docs)
        )

    @pytest.mark.parametrize(
        "text",
        [
            "& ('Invoke-{0}' -f 'RestMethod') -Method Get -Uri $u",
            "& (-join ('Invoke-', 'RestMethod')) -Method Head -Uri $u",
            "[string]::Format('plain {0}', 'text')",
            "Set-Alias x Get-Date",
        ],
    )
    def test_scope_ruling_conservative_refusals(self, text: str) -> None:
        assert cw.mutating_powershell_http(text)

    @pytest.mark.parametrize(
        "text", ["rm -f file", "curl -f $URL", "pwsh -NoProfile -File external.ps1"]
    )
    def test_scope_ruling_ordinary_text(self, text: str) -> None:
        assert not cw.mutating_powershell_http(text)

    @pytest.mark.parametrize(
        "text",
        [
            'if (( 1 << true )); then :; fi\ncurl -s $(\ntrue\n) "$URL?a=1&b=2" -o /dev/null',
            'curl -s ${a[${i:-0}]:--o} /dev/null "$URL"',
        ],
    )
    def test_compound_arithmetic_and_array_reads(self, text: str) -> None:
        assert not cw.mutating_http(text)

    @pytest.mark.parametrize(
        "text",
        [
            "${don't} = 'x'; Invoke-RestMethod -Uri $env:URL -Body ${don't} -Method Get",
            'Invoke-RestMethod -Uri $env:URL -Body "${a";}" -Method Get',
            "& 'C:\\Tools\\iwr' -Uri 'https://example.invalid' -Method Get -InFile f",
            "& 'Alias:irm' -Uri 'https://example.invalid' -Method Get -InFile f",
            "Invoke-RestMethod -Uri $env:URL -Body (\n  $payload # C:\\temp\\\n) -Method Get",
        ],
    )
    def test_variable_qualified_and_raw_reads(self, text: str) -> None:
        assert not cw.mutating_powershell_http(text)

    @pytest.mark.parametrize(
        "subscript", ["${i:-0}", "b[0]", "b[c[0]]", "$(echo 0)", '"0"', "'0'", r"0\]0"]
    )
    @pytest.mark.parametrize(
        "operator", [":-", "-", ":=", "=", ":+", "+", "/", "//", "/#", "/%"]
    )
    def test_array_operand_shape(self, subscript: str, operator: str) -> None:
        # Conservative operand readings do not require the default to be taken.
        text = "curl ${a[" + subscript + "]" + operator + "-d} x $URL"
        assert cw.mutating_http(text)

    @pytest.mark.parametrize(
        "text",
        [
            "Invoke-RestMethod -Uri 'http://example.invalid'<# it's #> -Method Get",
            "Invoke-RestMethod -Uri ('http://example.invalid')<# it's #> -Method Get",
            "Invoke-RestMethod -Uri 'http://example.invalid'# -Method Put",
            'curl ("-o") /dev/null $env:URL',
            "curl.exe @('--output','/dev/null',$env:URL)",
            "curl.exe @('-X','GET',$env:URL)",
            "wget @('--method','GET',$env:URL)",
            "curl ('-o') ('a,b') $env:URL",
        ],
    )
    def test_native_group_and_boundary_reads(self, text: str) -> None:
        assert not cw.mutating_powershell_http(text)

    def test_continued_comment_read_is_conservative_refusal(self) -> None:
        assert cw.mutating_powershell_http(
            "Invoke-RestMethod -Uri $env:URL `\n# it's a note\n-Method Get -InFile f"
        )

    @pytest.mark.parametrize(
        "reference",
        [
            "[System.Net.Http.HttpMessageInvoker]",
            "[Net.Http.HttpMessageInvoker]",
            "[HttpMessageInvoker]",
            "[HttpMessageHandler]",
            "[HttpRequestMessage]",
            "[SocketsHttpHandler]",
            "[HttpClientHandler]",
            "[System.Net.Http.HttpMethod]::Get",
            "[System.Net.Http.HttpResponseMessage]",
            "[System.Net.Http.HttpMessage`Invoker]",
            "New-Object ('System.Net.Http.' + 'HttpMessageInvoker')",
        ],
    )
    def test_http_api_references_conservatively_refused(self, reference: str) -> None:
        # References/construction alone need not send anything. These are
        # deliberate refusals, including method GET and response-only objects.
        assert cw.mutating_powershell_http(reference)

    @pytest.mark.parametrize("case", sorted(PANEL_FINAL_PS_WRITES))
    def test_powershell_write_at_workflow_entry(self, case: str) -> None:
        text = PANEL_FINAL_PS_WRITES[case]
        assert cw.mutating_powershell_http(text)
        docs = {
            "probe.yml": {
                "jobs": {"probe": {"steps": [{"shell": "pwsh", "run": text}]}}
            }
        }
        assert any(
            cw.MUTATING_PWSH_LABEL in r for r in cw.image_publication_invariants(docs)
        )

    @pytest.mark.parametrize(
        "text",
        [
            "Invoke-RestMethod -Uri https://example.invalid/p#frag -Method Get",
            "Invoke-RestMethod -Body (Write-Output a#b) -Method Get",
            "Invoke-RestMethod -Uri $u # -Method Put",
            "Invoke-RestMethod -Body (Write-Output a # comment\n) -Method Get",
            "curl “-o” /dev/null $env:URL",
            "curl.exe ‘--output’ /dev/null $env:URL",
            "& ('Invoke-Rest' + \"Method\") -Method Get",
            "& (\"Invoke-Rest\" + 'Method') -Method Get",
        ],
    )
    def test_powershell_reads(self, text: str) -> None:
        assert not cw.mutating_powershell_http(text)

    @pytest.mark.parametrize("method", ["Get", "Put"])
    @pytest.mark.parametrize("body", ["@'\nplain\n'@", '"$(1)"'])
    def test_complex_strings_conservatively_refused(
        self, method: str, body: str
    ) -> None:
        assert cw.mutating_powershell_http(
            f"Invoke-RestMethod -Body {body} -Method {method}"
        )

    @pytest.mark.parametrize(
        "name",
        [
            "prefix-time -p",
            "prefix-time --",
            "prefix-coproc",
            "prefix-coproc WORKER",
            "function-one-line",
            "function-hyphen",
        ],
    )
    def test_compound_prefix_reads(self, name: str) -> None:
        text = PANEL_GRAMMAR_WRITES[name].replace("-d x", "-o /dev/null")
        assert not cw.mutating_http(text)


# Exact fixed reviewer texts; no source probes are imported or executed.
CLAUDE_B42_BASH = {
    "results.json:B1-for-nospace": "for((i=0;i<1;i++ << true)); do :; done\n"
    "curl -s $(\n"
    "true\n"
    ') "$URL" -d x',
    "results.json:B2-while-nospace": "i=0; while((i++ << true)); do :; done\n"
    "curl -s $(\n"
    "true\n"
    ') "$URL" -d x',
    "results.json:B3-if-nospace": 'if((0 << true)); then :; fi\ncurl -s $(\ntrue\n) "$URL" -d x',
    "results.json:B4-until-nospace": 'until((1 << true)); do :; done\ncurl -s $(\ntrue\n) "$URL" -d x',
    "results.json:B5-bang-for-nospace": "! for((i=0;i<1;i++ << true)); do :; done\n"
    "curl -s $(\n"
    "true\n"
    ') "$URL" -d x',
    "results.json:B6-brace-for-nospace": "{ for((i=0;i<1;i++ << true)); do :; done; }\n"
    "curl -s $(\n"
    "true\n"
    ') "$URL" -d x',
    "results.json:B7-for-spaced-control": "for (( i=0; i<1; i++ << true )); do :; done\n"
    "curl -s $(\n"
    "true\n"
    ') "$URL" -d x',
    "results.json:B8-elif-nospace": "if false; then :; elif((0 << true)); then :; fi\n"
    "curl -s $(\n"
    "true\n"
    ') "$URL" -d x',
    "results2.json:C1-for-nospace": "for((i=0;i<1;i++ << true)); do :; done\n"
    'curl -s "$URL" --da""ta x\n'
    "true\n",
    "results2.json:C2-while-nospace": "i=0; while((i++ << true)); do :; done\n"
    'curl -s "$URL" --da""ta x\n'
    "true\n",
    "results2.json:C3-if-nospace": 'if((0 << true)); then :; fi\ncurl -s "$URL" --da""ta x\ntrue\n',
    "results2.json:C4-until-nospace": "until((1 << true)); do :; done\n"
    'curl -s "$URL" --da""ta x\n'
    "true\n",
    "results2.json:C5-bang-for-nospace": "! for((i=0;i<1;i++ << true)); do :; done\n"
    'curl -s "$URL" --da""ta x\n'
    "true\n",
    "results2.json:C6-elif-nospace": "if false; then :; elif((0 << true)); then :; fi\n"
    'curl -s "$URL" --da""ta x\n'
    "true\n",
    "results2.json:C7-for-spaced-control": "for (( i=0; i<1; i++ << true )); do :; done\n"
    'curl -s "$URL" --da""ta x\n'
    "true\n",
    "results2.json:C8-if-spaced-control": "if (( 0 << true )); then :; fi\n"
    'curl -s "$URL" --da""ta x\n'
    "true\n",
    "results2.json:C9-plain-curl-control": 'curl -s "$URL" --da""ta x\ntrue\n',
    "results3.json:C1-for-nospace": "for((i=0;i<1;i++ << true)); do :; done\n"
    'curl -s "$URL"${N+\n'
    "true\n"
    "} -d x\n",
    "results3.json:C10-prior-grok-bang-for-spaced": "! for (( i=0; i<1; i++ << true )); do :; done\n"
    'curl -s "$URL"${N+\n'
    "true\n"
    "} -d x\n",
    "results3.json:C11-time-for-nospace": "time -p for((i=0;i<1;i++ << true)); do :; done\n"
    'curl -s "$URL"${N+\n'
    "true\n"
    "} -d x\n",
    "results3.json:C12-brace-for-nospace": "{ for((i=0;i<1;i++ << true)); do :; done; }\n"
    'curl -s "$URL"${N+\n'
    "true\n"
    "} -d x\n",
    "results3.json:C2-while-nospace": "i=0; while((i++ << true)); do :; done\n"
    'curl -s "$URL"${N+\n'
    "true\n"
    "} -d x\n",
    "results3.json:C3-if-nospace": 'if((0 << true)); then :; fi\ncurl -s "$URL"${N+\ntrue\n} -d x\n',
    "results3.json:C4-until-nospace": "until((1 << true)); do :; done\n"
    'curl -s "$URL"${N+\n'
    "true\n"
    "} -d x\n",
    "results3.json:C5-bang-for-nospace": "! for((i=0;i<1;i++ << true)); do :; done\n"
    'curl -s "$URL"${N+\n'
    "true\n"
    "} -d x\n",
    "results3.json:C6-elif-nospace": "if false; then :; elif((0 << true)); then :; fi\n"
    'curl -s "$URL"${N+\n'
    "true\n"
    "} -d x\n",
    "results3.json:C7-for-spaced-control": "for (( i=0; i<1; i++ << true )); do :; done\n"
    'curl -s "$URL"${N+\n'
    "true\n"
    "} -d x\n",
    "results3.json:C8-if-spaced-control": "if (( 0 << true )); then :; fi\n"
    'curl -s "$URL"${N+\n'
    "true\n"
    "} -d x\n",
    "results3.json:C9-plain-curl-control": 'curl -s "$URL"${N+\ntrue\n} -d x\n',
}
CLAUDE_B42_PS = {
    "results.json:P1-method-paren": "Invoke-RestMethod -Uri $u -Method('Put')",
    "results.json:P10-alias-ni": "ni Alias:x -Value Invoke-RestMethod; x -Method Put -Uri $u",
    "results.json:P11-alias-si": "si Alias:x Invoke-RestMethod; x -Method Put -Uri $u",
    "results.json:P12-alias-newitem-control": "New-Item -Path Alias:x -Value Invoke-RestMethod; x "
    "-Method Put -Uri $u",
    "results.json:P13-replace-op": "& ('Invoke-RestXethod' -replace 'X','M') -Method Put -Uri $u",
    "results.json:P14-replace-method": "& ('Invoke-RestXethod'.Replace('X','M')) -Method Put -Uri $u",
    "results.json:P15-char-cast": "& ([char]73 + 'nvoke-RestMethod') -Method Put -Uri $u",
    "results.json:P16-expandable-subexpr": "& \"Invoke-Rest$('Method')\" -Method Put -Uri $u",
    "results.json:P17-substring": "& ('Invoke-RestMethodX'.Substring(0,17)) -Method Put -Uri $u",
    "results.json:P18-type-string-format": "& "
    "(([type]'System.String')::Format('Invoke-{0}','RestMethod')) "
    "-Method Put -Uri $u",
    "results.json:P2-custom-paren": "Invoke-RestMethod -Uri $u -CustomMethod('PROPFIND')",
    "results.json:P20-powershell-addcommand": "[powershell]::Create().AddCommand('Invoke-RestMethod').AddParameter('Method','Put').AddParameter('Uri',$u).Invoke()",
    "results.json:P21-chain-control": "& ('Invoke-' + 'Rest' + 'Method') -Method Put -Uri $u",
    "results.json:P22-format-control": "& ('Invoke-{0}'-f'RestMethod') -Method Put -Uri $u",
    "results.json:P3-iwr-me-paren": 'iwr $u -Me("Post") -Body x',
    "results.json:P4-endash-paren": "Invoke-WebRequest -Uri $u –Method('Put') -InFile f",
    "results.json:P6-colon-paren-control": "Invoke-RestMethod -Uri $u -Method:('Put')",
    "results.json:P7-alias-var": "$alias:x = 'Invoke-RestMethod'; x -Method Put -Uri $u",
    "results.json:P8-alias-bracevar": "${alias:x} = 'Invoke-RestMethod'; x -Method Put -Uri $u",
    "results.json:P9-alias-setcontent": "Set-Content Alias:x Invoke-RestMethod; x -Method Put -Uri $u",
    "results4.json:S1-amp-backtick-continuation-format": "&`\n"
    "('Invoke-{0}' -f 'RestMethod') -Method Put "
    "-Uri $u",
    "results4.json:S2-amp-blockcomment-format": "&<#c#>('Invoke-{0}' -f 'RestMethod') -Method Put "
    "-Uri $u",
    "results4.json:S3-amp-blockcomment-join": "&<#c#>(-join('Invoke-','RestMethod')) -Method Put -Uri "
    "$u",
    "results4.json:S4-dot-backtick-continuation-join": ".`\n"
    "(-join('Invoke-','RestMethod')) -Method Put "
    "-Uri $u",
    "results4.json:S5-amp-space-format-control": "& ('Invoke-{0}' -f 'RestMethod') -Method Put -Uri "
    "$u",
    "results4.json:S6-amp-tight-format-control": "&('Invoke-{0}' -f 'RestMethod') -Method Put -Uri $u",
    "results5.json:K1-ec": "pwsh -NoProfile -ec SQBuAHYAbwBrAGUALQBSAGUAcwB0AE0AZQB0AGgAbwBkAA==",
    "results5.json:K10-endash-ec": "pwsh -NoProfile –ec "
    "SQBuAHYAbwBrAGUALQBSAGUAcwB0AE0AZQB0AGgAbwBkAA==",
    "results5.json:K2-slash-encodedcommand": "pwsh -NoProfile /EncodedCommand "
    "SQBuAHYAbwBrAGUALQBSAGUAcwB0AE0AZQB0AGgAbwBkAA==",
    "results5.json:K3-doubledash-encodedcommand": "pwsh -NoProfile --encodedcommand "
    "SQBuAHYAbwBrAGUALQBSAGUAcwB0AE0AZQB0AGgAbwBkAA==",
    "results5.json:K4-slash-e": "powershell.exe -NoProfile /e "
    "SQBuAHYAbwBrAGUALQBSAGUAcwB0AE0AZQB0AGgAbwBkAA==",
    "results5.json:K5-slash-c-readonly": "pwsh -NoProfile /c 'Write-Output hi'",
    "results5.json:K6-doubledash-command-readonly": "pwsh -NoProfile --command 'Write-Output hi'",
    "results5.json:K7-cwa-readonly": "pwsh -NoProfile -cwa 'Write-Output $args' x",
    "results5.json:K8-encodedcommand-control": "pwsh -NoProfile -EncodedCommand "
    "SQBuAHYAbwBrAGUALQBSAGUAcwB0AE0AZQB0AGgAbwBkAA==",
    "results5.json:K9-e-control": "pwsh -NoProfile -e "
    "SQBuAHYAbwBrAGUALQBSAGUAcwB0AE0AZQB0AGgAbwBkAA==",
}
CLAUDE_B42_READS = {
    "results.json:B1r-for-nospace-read": (
        "for((i=0;i<1;i++ << true)); do :; done\n"
        "curl -s $(\n"
        "true\n"
        ') "$URL" -o /dev/null',
        True,
    ),
    "results.json:FP-test-f-and": ("[ -f x ] && echo ok", False),
    "results.json:FP-test-f-and2": ("test -f go.sum && go mod download", False),
    "results.json:P19-gettype-concat": (
        "& ((''.GetType())::Concat('Invoke-','RestMethod')) -Method Put -Uri $u",
        False,
    ),
    "results.json:P23-get-control": ("Invoke-RestMethod -Uri $u -Method Get", False),
    "results.json:P24-paren-method-get-replace": (
        "& ('Invoke-RestXethod' -replace 'X','M') -Method Get -Uri $u",
        False,
    ),
    "results.json:P5-paren-get-control": (
        "Invoke-RestMethod -Uri $u -Method('Get')",
        False,
    ),
    "results2.json:C1r-for-nospace-read": (
        'for((i=0;i<1;i++ << true)); do :; done\ncurl -s "$URL" -o /dev/n""ull\ntrue\n',
        True,
    ),
    "results3.json:C1r-for-nospace-read": (
        "for((i=0;i<1;i++ << true)); do :; done\n"
        'curl -s "$URL"${N+\n'
        "true\n"
        "} -o /dev/null\n",
        True,
    ),
}


CLAUDE_B42_PS["results.json:P19-gettype-concat"] = CLAUDE_B42_READS.pop(
    "results.json:P19-gettype-concat"
)[0]


class TestClaudeB42Regressions:
    @pytest.mark.parametrize(
        "text",
        [
            "([type]'System.String')::Format('text {0}', 'value')",
            "(''.GetType())::Concat('text', 'value')",
            "[powershell]::Create()",
            "[System.Management.Automation.PowerShell]::Create()",
            "$builder.AddCommand('Get-Date')",
            "$builder.AddScript('Get-Date')",
            "$builder.AddParameter('Method', 'Get')",
            "$alias:x = 'Get-Date'",
            "${alias:x} = 'Get-Date'",
            "Set-Content Alias:x Get-Date",
            "sc Alias:x Get-Date",
            "Add-Content Alias:x Get-Date",
            "ac Alias:x Get-Date",
            "&<#note#>('name{0}' -f 0) -Method Get",
            ".`\n(-join('name','part')) -Method Get",
        ],
    )
    def test_explicit_conservative_refusals(self, text):
        assert cw.mutating_powershell_http(text)

    @pytest.mark.parametrize("case", sorted(CLAUDE_B42_BASH))
    def test_bash_metacharacter_boundary(self, case):
        text = CLAUDE_B42_BASH[case]
        assert cw.mutating_http(text)
        assert cw.image_publication_invariants(
            {
                "probe.yml": {
                    "jobs": {"probe": {"steps": [{"shell": "bash", "run": text}]}}
                }
            }
        )

    @pytest.mark.parametrize("case", sorted(CLAUDE_B42_PS))
    def test_static_powershell_write(self, case):
        text = CLAUDE_B42_PS[case]
        assert cw.mutating_powershell_http(text)
        assert cw.image_publication_invariants(
            {
                "probe.yml": {
                    "jobs": {"probe": {"steps": [{"shell": "pwsh", "run": text}]}}
                }
            }
        )

    @pytest.mark.parametrize("case", sorted(CLAUDE_B42_READS))
    def test_read_controls(self, case):
        text, bash = CLAUDE_B42_READS[case]
        assert not (
            cw.mutating_http(text) if bash else cw.mutating_powershell_http(text)
        )
