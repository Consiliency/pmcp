#!/usr/bin/env python3
"""Structural guards for ``.github/workflows/``.

``release.yml`` triggers only on tag push, so it never appears in PR checks:
the tag push *is* the publish. A mistake in it is invisible until a release
breaks, and the worst mutants are silent — changing the tag filter ``v*`` to
``V*`` leaves a valid workflow that simply never runs, so there is no red X
anywhere and a version sits tagged-but-unshipped.

Four independent checks run here:

``all_uses_are_sha_pinned``
    Every remote ``uses:`` in every workflow *and* in every local composite
    action under ``.github/actions/`` must name a 40-hex commit SHA with a
    trailing ``# <release>`` comment (Consiliency/pmcp#217). Checked on the
    **raw text**, because ``yaml.safe_load`` drops comments.
``release_invariants``
    Asserts the *state* of ``release.yml`` against the committed shape.
``timeout_invariants``
    Asserts every job in every workflow carries a sane ``timeout-minutes``
    (Consiliency/pmcp#187: the GitHub default is six hours).
``job_set_drift``
    Compares the job set of each changed workflow against its base revision
    and fails on any job that disappeared. This is the only guard against a
    job being silently deleted or renamed away.
``image_publication_invariants``
    The guarded container image cutover: ``docker.yml`` stays a read-only PR
    build, every registry write lives in ``image.yml:promote-main`` behind the
    exact-``enabled`` gate, and ``image-watch.yml`` stays read-only. Every
    reason it reports is tagged ``[image]``. These are static YAML and string
    checks: commands assembled at runtime, code inside third-party actions and
    repository settings are outside their reach.

**The ``uses:`` and ``permissions:`` allowlists below are exact.** Any
legitimate edit to ``release.yml`` — bumping an action, adding a step that
uses one, granting a scope — must update the constants in this file in the
same PR. That is intended: on the one workflow with no PR-time feedback, a
change should be deliberate enough to be spelled twice.

The ``uses:`` allowlist holds the **SHA form without the comment**, because
the parser never sees the comment. An exact SHA in ``EXPECTED_USES`` is what
rejects a *valid-looking* pin to the wrong commit — a moved digit, or a real
older release with an honest comment (``pypa-rolled-back`` in
``.consiliency/evidence/mutation-217.md``). The SHA-pin invariant rejects the
*form* (a mutable tag, a missing comment); it cannot know which commit is right.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

WORKFLOW_DIR = Path(".github/workflows")
ACTIONS_DIR = Path(".github/actions")
RELEASE_PATH = WORKFLOW_DIR / "release.yml"

# --- committed shape of release.yml -----------------------------------------

EXPECTED_TAGS = ["v*"]
EXPECTED_RELEASE_COMMANDS = {
    "Verify Node and npm environment": 'test "$(node -p "process.versions.node.split(\'.\')[0]")" = 22\nnpm_version=$(npm --version)\ncase "$npm_version" in\n  10.*|11.*) ;;\n  *) echo \'Unsupported npm major for identity validation\'; exit 1 ;;\nesac\nnode --version\nnpm --version\n',
    "Set up Python": "uv python install 3.12",
    "Install dependencies": "uv sync --locked --all-extras --python 3.12",
    "Record environment identity": 'uv run --locked python scripts/ci_environment.py "$RUNNER_TEMP/release-environment.json"\ncat "$RUNNER_TEMP/release-environment.json"\n',
    "Build and verify exact archives": "uv run --locked python scripts/release_artifacts.py --bundle .release-bundle",
    "Audit locked dependencies before publication": 'uv export --locked --all-extras --dev --no-emit-project --format requirements-txt -o "$RUNNER_TEMP/requirements.txt"\nuv run --locked --with pip-audit==2.9.0 pip-audit --progress-spinner off --strict --disable-pip -r "$RUNNER_TEMP/requirements.txt"\n',
    "Run tests with coverage": 'uv run --locked python -m pytest tests/ -v --tb=short -n auto --dist loadfile --cov --cov-report=xml:"$RUNNER_TEMP/release-coverage.xml" --cov-report=term-missing --cov-fail-under=60',
}
EXPECTED_RELEASE_NOTES_COMMAND = 'tag="${GITHUB_REF_NAME}"\nversion="${tag#v}"\nawk -v version="$version" \'\n  $0 ~ "^## \\\\[" version "\\\\]([ -]|$)" {\n    in_section = 1\n    next\n  }\n  in_section && /^## \\[/ {\n    exit\n  }\n  in_section {\n    print\n  }\n\' CHANGELOG.md > release-notes.md\n\nif [ ! -s release-notes.md ]; then\n  printf \'Release %s\\n\' "$tag" > release-notes.md\nfi\n'
EXPECTED_GITHUB_RELEASE_COMMAND = 'tag="${GITHUB_REF_NAME}"\nif gh release view "$tag" >/dev/null 2>&1; then\n  mkdir -p "$RUNNER_TEMP/published-dist"\n  gh release download "$tag" --dir "$RUNNER_TEMP/published-dist"\n  python3 -I -B scripts/verify_release_bundle.py .release-bundle --released-dir "$RUNNER_TEMP/published-dist"\n  gh release edit "$tag" \\\n    --title "$tag" \\\n    --notes-file release-notes.md\nelse\n  gh release create "$tag" .release-bundle/dist/* \\\n    --title "$tag" \\\n    --notes-file release-notes.md \\\n    --verify-tag\nfi\n'
EXPECTED_JOBS = {"build", "publish", "github-release"}
EXPECTED_NEEDS = {"publish": ["build"], "github-release": ["publish", "build"]}
EXPECTED_ENVIRONMENT = {"publish": "release"}
# ``None`` means the job declares no permissions: block at all.
EXPECTED_JOB_PERMISSIONS: dict[str, dict[str, str] | None] = {
    "build": {"contents": "read"},
    "publish": {"id-token": "write", "contents": "read"},
    "github-release": {"contents": "write"},
}
# SHA form ONLY — no `# vX.Y.Z` here. `yaml.safe_load` strips the comment before
# `_collect_uses` runs, so a value carrying one could never match anything.
# The comment lives in release.yml and is enforced on raw text by
# `all_uses_are_sha_pinned`; the commit is enforced here.
EXPECTED_USES = {
    "build": [
        "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
        "actions/setup-node@949feb2413d6458794dcd2491c4babbbce0c15c1",
        "astral-sh/setup-uv@68559e99cc807c1eb7e9238fd43a15bba460eec2",
        "actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9",
    ],
    "publish": [
        "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
        "actions/download-artifact@9000827ccba6bdab643e8b6fd33ac0654aef8333",
        # v1.14.2 == the release/v1 head on the day of pinning. Any re-pin must
        # stay >= 1.13.0: GHSA-vxmw-7h4f-hqxh (injectable expression expansions)
        # affects every earlier release.
        "pypa/gh-action-pypi-publish@dc37677b2e1c63e2034f94d8a5b11f265b73ba33",
    ],
    "github-release": [
        "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
        "actions/download-artifact@9000827ccba6bdab643e8b6fd33ac0654aef8333",
    ],
}
# Jobs whose execution must not be made conditional or best-effort, at job
# level *or* step level. `build` is in the set: an `if:` that skips it skips
# `publish` through `needs`, and the tag push concludes **green** — the same
# silence class `tag-case` headlines — while `continue-on-error` on it lets a
# failed build's artifact publish anyway.
PROTECTED_JOBS = ("build", "publish", "github-release")

# Workflow files permitted to be tag-triggered, i.e. to declare `on.push.tags`.
# An allowlist, like the others, and for the same reason: a *new* tag-triggered
# workflow file is silent across every other layer here. It is not `release.yml`,
# so the release invariants never look at it; it is a new file with no base
# version, so drift passes it; and `release-diff-ack` greps for `release.yml`
# specifically, so the label is never demanded. It would run arbitrary code at
# tag push with whatever permissions it grants itself.
# `docker.yml` left this set with the guarded image cutover: it is a read-only
# pull-request build now, and a tag caller for image publication needs its own
# reviewed PR. Adding a file here must be deliberate.
TAG_TRIGGERED_WORKFLOWS = {"release.yml"}

# Timeouts: a floor stops a job being throttled to death, a ceiling stops the
# six-hour default being re-created by a large value. Both ends are load
# bearing; see .consiliency/evidence/bypass-proofs-187.md. The ceiling was 30
# until the test matrix job reached 29m42s; 45 is a stopgap while the suite is
# parallelised (see Consiliency/pmcp#383).
TIMEOUT_MIN = 10
TIMEOUT_MAX = 45

EXPECTED_VALIDATION_CONCURRENCY = {
    "group": "${{ github.workflow }}-${{ github.event_name == 'pull_request' && format('pr-{0}', github.event.pull_request.number) || format('{0}-{1}-{2}', github.event_name, github.sha, github.run_id) }}",
    "cancel-in-progress": "${{ github.event_name == 'pull_request' }}",
}

# FULL-only bootstrap/recovery bytes; normal pinned policy is a later rollout.
BOOTSTRAP_STEPS_SHA256 = {
    "ci-scope": "06f67bdc7b7c3e836c3b1849caa62bc484c8d47da370a5ed372006c987096b50",
    "ci-required": "c57d62f18b78fadcc688c48e7a40e4c0d56db8296bf96ae79fa202ce8ebf332d",
}

AUDIT_VERIFY_SHA256 = "d8be7aa4ed92d83495fb7a003c7c082090e8cc9740863a782f5a3cba5737cb4e"


def get_on(doc: Any) -> Any:
    """Return the workflow trigger mapping, or ``None``.

    YAML 1.1 reads a bare ``on:`` as the boolean ``True``, so ``yaml.safe_load``
    on a real workflow yields keys ``['name', True, 'jobs']`` and ``"on" in doc``
    is **False**. Quoted (``"on":``) and bare forms must both be accepted — and
    a guard written as ``doc["on"]`` inside a ``try`` passes vacuously on the
    real file, which is worse than not having it.
    """
    if not isinstance(doc, dict):
        return None
    for key in ("on", True):
        if key in doc:
            return doc[key]
    return None


def _as_list(value: Any) -> list[Any]:
    """Normalise a scalar-or-list field. ``needs: build`` == ``needs: [build]``."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _jobs(doc: Any) -> dict[str, Any]:
    if not isinstance(doc, dict):
        return {}
    jobs = doc.get("jobs")
    if not isinstance(jobs, dict):
        return {}
    return jobs


def _collect_uses(job: Any) -> list[str]:
    uses: list[str] = []
    if not isinstance(job, dict):
        return uses
    if isinstance(job.get("uses"), str):
        uses.append(job["uses"])
    for step in job.get("steps") or []:
        if isinstance(step, dict) and isinstance(step.get("uses"), str):
            uses.append(step["uses"])
    return uses


def release_invariants(doc: Any) -> list[str]:
    """Check the committed shape of ``release.yml``.

    ``doc`` is the parsed workflow, or ``None`` when the file is absent —
    outright deletion of the release path is itself a failure.
    """
    reasons: list[str] = []
    tag = "[release]"

    if doc is None:
        return [f"{tag} {RELEASE_PATH} is missing — the release path was deleted"]
    if not isinstance(doc, dict):
        return [f"{tag} {RELEASE_PATH} did not parse as a mapping"]

    # The trigger set must be EXACTLY {push: {tags: ["v*"]}}. Checking only that
    # `push.tags` contains "v*" is an allowlist of one key, not a constraint on
    # the trigger set: it permits ADDITIONAL events and filters alongside it.
    # That is the worst possible miss on this file. `publish` carries
    # `id-token: write` and the `release` environment has no protection rules,
    # so adding `pull_request:` or `push.branches` makes the trusted-publishing
    # path reachable from any same-repository PR and from every ordinary push to
    # main — GitHub evaluates the workflow at the triggering ref, and combined
    # branch/tag filters fire for either ref type. PyPI's own trusted-publisher
    # model calls this out: unintended triggers in a trusted workflow require
    # environment approval protections, which this repo does not have.
    # `workflow_dispatch` belongs in the same set; it was struck as dangerous
    # here during #187.
    on = get_on(doc)
    if not isinstance(on, dict):
        reasons.append(
            f"{tag} the trigger block is {on!r}, expected exactly "
            f"{{'push': {{'tags': {EXPECTED_TAGS!r}}}}}"
        )
    else:
        extra_events = sorted(str(event) for event in on if event != "push")
        if extra_events:
            reasons.append(
                f"{tag} extra trigger event(s): {', '.join(extra_events)} — the "
                "release workflow must trigger on tag push and nothing else. "
                "`publish` holds id-token: write against an environment with no "
                "protection rules, so any other trigger makes trusted publishing "
                "reachable from that event"
            )
        push = on.get("push")
        if not isinstance(push, dict):
            reasons.append(
                f"{tag} on.push is {push!r}, expected a mapping with only `tags`"
            )
        else:
            extra_filters = sorted(str(key) for key in push if key != "tags")
            if extra_filters:
                reasons.append(
                    f"{tag} on.push carries {', '.join(extra_filters)} alongside "
                    "tags — a branch or path filter fires on ordinary pushes to "
                    "that branch, not only on tag pushes, and reaches the same "
                    "trusted-publishing path"
                )
            if _as_list(push.get("tags")) != EXPECTED_TAGS:
                reasons.append(
                    f"{tag} on.push.tags is {push.get('tags')!r}, expected "
                    f"{EXPECTED_TAGS!r} — a tag filter that does not match the "
                    "release tags means the publish never runs and never reports "
                    "a failure"
                )

    if "permissions" in doc:
        reasons.append(
            f"{tag} workflow-level permissions: {doc['permissions']!r} — a "
            "workflow-level block applies to every job that does not override "
            "it, so it widens `build` while leaving each job map untouched"
        )

    jobs = _jobs(doc)
    if not jobs:
        return reasons + [f"{tag} {RELEASE_PATH} declares no jobs"]

    if set(jobs) != EXPECTED_JOBS:
        added = sorted(set(jobs) - EXPECTED_JOBS)
        removed = sorted(EXPECTED_JOBS - set(jobs))
        detail = []
        if added:
            detail.append(f"unexpected: {', '.join(added)}")
        if removed:
            detail.append(f"missing: {', '.join(removed)}")
        reasons.append(
            f"{tag} job set is {sorted(jobs)}, expected "
            f"{sorted(EXPECTED_JOBS)} ({'; '.join(detail)})"
        )

    build = jobs.get("build", {})
    if build.get("env") != {"UV_PYTHON": "3.12"}:
        reasons.append("[release] build must select Python 3.12 explicitly")
    for label, command in EXPECTED_RELEASE_COMMANDS.items():
        matches = [step for step in build.get("steps", []) if step.get("name") == label]
        if len(matches) != 1 or matches[0] != {"name": label, "run": command}:
            reasons.append("[release] missing or changed validation step: " + label)
    steps = build.get("steps", [])
    labels = [step.get("name", step.get("uses", "").split("@")[0]) for step in steps]
    expected_labels = [
        "actions/checkout",
        "actions/setup-node",
        "Verify Node and npm environment",
        "Install uv",
        "Set up Python",
        "Install dependencies",
        "Record environment identity",
        "Audit locked dependencies before publication",
        "Run tests with coverage",
        "Build and verify exact archives",
        "Upload artifacts",
    ]
    if labels != expected_labels:
        reasons.append(
            "[release] validation, build and upload steps must retain their exact order"
        )
    if not steps or steps[0].get("with") != {"fetch-depth": 0}:
        reasons.append("[release] build checkout must retain full history and tags")
    if any(field in doc for field in ("env", "defaults")) or "defaults" in build:
        reasons.append(
            "[release] validation must not inherit environment or shell overrides"
        )
    uv_steps = [
        step
        for step in build.get("steps", [])
        if str(step.get("uses", "")).startswith("astral-sh/setup-uv@")
    ]
    if len(uv_steps) != 1 or uv_steps[0].get("with", {}).get("version") != "0.13.1":
        reasons.append("[release] uv tool version must remain pinned")

    if build.get("outputs") != {"bundle_id": "${{ steps.upload.outputs.artifact-id }}"}:
        reasons.append("[release] build must expose the same-run immutable artifact ID")
    verification = [
        s
        for s in build.get("steps", [])
        if s.get("name") == "Build and verify exact archives"
    ]
    if (
        len(verification) != 1
        or verification[0].get("run")
        != "uv run --locked python scripts/release_artifacts.py --bundle .release-bundle"
    ):
        reasons.append("[release] exact wheel and sdist verification is mandatory")
    uploads = [s for s in build.get("steps", []) if s.get("name") == "Upload artifacts"]
    if (
        len(uploads) != 1
        or uploads[0].get("id") != "upload"
        or uploads[0].get("with")
        != {
            "name": "release-${{ github.run_id }}-${{ github.run_attempt }}",
            "path": ".release-bundle/",
            "include-hidden-files": True,
            "if-no-files-found": "error",
        }
    ):
        reasons.append("[release] upload only the verified bundle with immutable ID")
    for target in ("publish", "github-release"):
        steps = jobs.get(target, {}).get("steps", [])
        checks = [s for s in steps if s.get("name") == "Verify artifact identity"]
        downloads = [
            s
            for s in steps
            if str(s.get("uses", "")).startswith("actions/download-artifact@")
        ]
        if (
            len(checks) != 1
            or checks[0].get("run")
            != "python3 -I -B scripts/verify_release_bundle.py .release-bundle"
        ):
            reasons.append(
                "[release] missing downstream archive identity verification: " + target
            )
        if len(downloads) != 1 or downloads[0].get("with") != {
            "artifact-ids": "${{ needs.build.outputs.bundle_id }}",
            "path": ".release-bundle/",
            "merge-multiple": True,
        }:
            reasons.append(
                "[release] downstream must use the exact build artifact ID: " + target
            )
        expected_steps = [
            {
                "uses": EXPECTED_USES[target][0],
                "with": {"ref": "${{ github.sha }}", "persist-credentials": False},
            },
            {
                "name": "Download artifacts",
                "uses": EXPECTED_USES[target][1],
                "with": {
                    "artifact-ids": "${{ needs.build.outputs.bundle_id }}",
                    "path": ".release-bundle/",
                    "merge-multiple": True,
                },
            },
            {
                "name": "Verify artifact identity",
                "run": "python3 -I -B scripts/verify_release_bundle.py .release-bundle",
            },
        ]
        if target == "publish":
            expected_steps.append(
                {
                    "name": "Publish to PyPI",
                    "uses": EXPECTED_USES[target][2],
                    "with": {"packages-dir": ".release-bundle/dist/"},
                }
            )
        else:
            expected_steps.extend(
                [
                    {
                        "name": "Extract release notes",
                        "run": EXPECTED_RELEASE_NOTES_COMMAND,
                    },
                    {
                        "name": "Create GitHub Release",
                        "env": {"GH_TOKEN": "${{ github.token }}"},
                        "run": EXPECTED_GITHUB_RELEASE_COMMAND,
                    },
                ]
            )
        expected_job_keys = {
            "needs",
            "runs-on",
            "timeout-minutes",
            "permissions",
            "steps",
        } | ({"environment"} if target == "publish" else set())
        if steps != expected_steps or set(jobs[target]) != expected_job_keys:
            reasons.append(
                "[release] complete downstream execution mapping must remain exact: "
                + target
            )
        expected_order = [
            "actions/checkout",
            "Download artifacts",
            "Verify artifact identity",
        ] + (
            ["Publish to PyPI"]
            if target == "publish"
            else ["Extract release notes", "Create GitHub Release"]
        )
        if [
            s.get("name", s.get("uses", "").split("@")[0]) for s in steps
        ] != expected_order:
            reasons.append(
                "[release] downstream download and verification must precede publication: "
                + target
            )
        if not steps or steps[0].get("with") != {
            "ref": "${{ github.sha }}",
            "persist-credentials": False,
        }:
            reasons.append(
                "[release] downstream checkout must bind the triggering SHA: " + target
            )
    pypi = [
        s
        for s in jobs.get("publish", {}).get("steps", [])
        if str(s.get("uses", "")).startswith("pypa/")
    ]
    if len(pypi) != 1 or pypi[0].get("with") != {
        "packages-dir": ".release-bundle/dist/"
    }:
        reasons.append("[release] publish only verified distribution files")
    release_steps = jobs.get("github-release", {}).get("steps", [])
    if any("--clobber" in str(s.get("run", "")) for s in release_steps):
        reasons.append("[release] published archive bytes may never be clobbered")

    create = [s for s in release_steps if s.get("name") == "Create GitHub Release"]
    if len(create) != 1 or create[0].get("run") != EXPECTED_GITHUB_RELEASE_COMMAND:
        reasons.append(
            "[release] existing release must retain exact whole-asset verification before notes edit"
        )

    for name, expected_needs in EXPECTED_NEEDS.items():
        job = jobs.get(name)
        if not isinstance(job, dict):
            continue
        if _as_list(job.get("needs")) != expected_needs:
            reasons.append(
                f"{tag} job {name!r} has needs: {job.get('needs')!r}, expected "
                f"{expected_needs[0]!r} — the release ordering is what keeps an "
                "untested or unpublished artifact from being released"
            )

    for name, expected_env in EXPECTED_ENVIRONMENT.items():
        job = jobs.get(name)
        if not isinstance(job, dict):
            continue
        env = job.get("environment")
        env_name = env.get("name") if isinstance(env, dict) else env
        if env_name != expected_env:
            reasons.append(
                f"{tag} job {name!r} has environment: {env_name!r}, expected "
                f"{expected_env!r} — GitHub auto-creates unprotected "
                "environments, so a renamed one passes a presence check and ships"
            )

    for name in PROTECTED_JOBS:
        job = jobs.get(name)
        if not isinstance(job, dict):
            continue
        for field in ("continue-on-error", "if"):
            if field in job:
                reasons.append(
                    f"{tag} job {name!r} declares {field}: {job[field]!r} — a tag "
                    "push then reports green whether or not the release happened"
                )
        for index, step in enumerate(job.get("steps") or []):
            if not isinstance(step, dict):
                continue
            label = step.get("name") or step.get("uses") or f"step {index}"
            for field in ("continue-on-error", "if"):
                if field in step:
                    reasons.append(
                        f"{tag} job {name!r} step {label!r} declares {field}: "
                        f"{step[field]!r} — the job-level rule is bypassed one "
                        "level down; a failed upload still reports green"
                    )

    for name, expected_perms in EXPECTED_JOB_PERMISSIONS.items():
        job = jobs.get(name)
        if not isinstance(job, dict):
            continue
        actual = job.get("permissions")
        if actual != expected_perms:
            reasons.append(
                f"{tag} job {name!r} has permissions: {actual!r}, expected "
                f"{expected_perms!r}"
            )

    for name, expected_uses in EXPECTED_USES.items():
        job = jobs.get(name)
        if not isinstance(job, dict):
            continue
        actual_uses = _collect_uses(job)
        if actual_uses != expected_uses:
            reasons.append(
                f"{tag} job {name!r} uses: {actual_uses!r}, expected "
                f"{expected_uses!r} — an action repointed at a fork runs "
                "attacker code with the release environment in scope"
            )

    return reasons


def tag_trigger_invariants(doc: Any, path: str | Path) -> list[str]:
    """Only an allowlisted workflow file may be tag-triggered.

    The release path is not one file, it is *every file that runs at tag push*.
    A brand-new `release-notes.yml` with `on: push: tags: ["v*"]` and
    `permissions: contents: write` passes the release invariants (it is not
    `release.yml`), passes drift (a new file has no base version), and never
    trips `release-diff-ack` (which greps for `release.yml`). It would run
    arbitrary code on every release, unreviewed by any of them.
    """
    name = Path(path).name
    if name in TAG_TRIGGERED_WORKFLOWS:
        return []
    on = get_on(doc)
    if not isinstance(on, dict):
        return []
    push = on.get("push")
    if not isinstance(push, dict) or not _as_list(push.get("tags")):
        return []
    return [
        f"[release] {path} declares on.push.tags: {push['tags']!r} — it would run "
        f"at tag push, on the release path, but it is not one of "
        f"{sorted(TAG_TRIGGERED_WORKFLOWS)}. The release invariants only inspect "
        "release.yml, drift passes any new file, and release-diff-ack greps for "
        "release.yml specifically, so nothing else here would see it. Add it to "
        "TAG_TRIGGERED_WORKFLOWS deliberately if it belongs on that path."
    ]


def timeout_invariants(doc: Any, path: str | Path) -> list[str]:
    """Every job in every workflow must carry a sane ``timeout-minutes``.

    Applies to all workflow files, not just ``release.yml``. The predicate is
    #187's hardened one: ``timeout-minutes: 360`` re-creates the six-hour
    default that issue exists to fix, ``true`` is an ``int`` in Python, and a
    value of ``1`` kills a four-minute job after one minute.
    """
    reasons: list[str] = []
    tag = "[timeout]"
    jobs = _jobs(doc)
    if not jobs:
        return [f"{tag} {path} declares no jobs"]

    for name, job in jobs.items():
        if not isinstance(job, dict):
            reasons.append(f"{tag} {path}:{name} did not parse as a mapping")
            continue
        # A reusable-workflow call cannot carry timeout-minutes; requiring one
        # there would force invalid YAML.
        if "uses" in job and "steps" not in job:
            continue
        if "timeout-minutes" not in job:
            reasons.append(
                f"{tag} {path}:{name} has no timeout-minutes — GitHub's default "
                "is 360 minutes"
            )
            continue
        value = job["timeout-minutes"]
        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or not (TIMEOUT_MIN <= value <= TIMEOUT_MAX)
        ):
            reasons.append(
                f"{tag} {path}:{name} has timeout-minutes: {value!r}, expected an "
                f"integer in {TIMEOUT_MIN}..{TIMEOUT_MAX}"
            )
    return reasons


# --- SHA pinning ----------------------------------------------------------------

# A `uses:` line, at step level (`- uses:`) or job level (reusable workflow).
_USES_LINE = re.compile(r"^\s*-?\s*uses:\s*(?P<value>\S.*?)\s*$")
# The only accepted form for a remote reference: owner/repo[/path]@<40 hex>,
# then a trailing `# <something>` naming the release. Subdirectory actions and
# remote reusable workflows (`owner/repo/.github/workflows/x.yml@<sha>`) are
# admitted by the path segment; they are remote code and must be pinned too.
SHA_PINNED_USES = re.compile(
    r"^\s*-?\s*uses:\s*[\w.-]+/[\w./-]+@[0-9a-f]{40}\s+#\s*\S+"
)


def action_files() -> list[Path]:
    """Every local composite/JS action definition under ``.github/actions/``.

    Both extensions: an ``action.yaml`` runs exactly like an ``action.yml``,
    so renaming one would otherwise remove it from the scan while keeping it
    executable.
    """
    if not ACTIONS_DIR.is_dir():
        return []
    found = set(ACTIONS_DIR.rglob("action.yml")) | set(ACTIONS_DIR.rglob("action.yaml"))
    return sorted(found)


def pin_scan_files() -> list[Path]:
    return workflow_files() + action_files()


def remote_uses(paths: list[Path]) -> list[tuple[Path, int, str]]:
    """Every ``uses:`` value that refers to code outside this repository.

    ``./path`` is same-repository code, reviewed like any other file, and is
    the ONLY thing skipped. ``docker://`` is remote and is *not* skipped: it is
    not pinnable by this scheme, so it is reported until the decision to admit
    it (by digest) is made deliberately here rather than by default.
    """
    found: list[tuple[Path, int, str]] = []
    for path in paths:
        for lineno, line in enumerate(path.read_text().splitlines(), start=1):
            match = _USES_LINE.match(line)
            if not match:
                continue
            value = match.group("value")
            if value.startswith("./"):
                continue
            found.append((path, lineno, value))
    return found


def _parsed_uses(node: Any) -> list[str]:
    """Every string ``uses:`` value the YAML parser sees, anywhere in ``node``.

    Deliberately over-collects (a ``uses`` key under ``with:`` would count):
    over-collection can only make the cross-check below fail, never pass.
    """
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "uses" and isinstance(value, str):
                found.append(value)
            else:
                found.extend(_parsed_uses(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(_parsed_uses(item))
    return found


_TRAILING_COMMENT = re.compile(r"\s+#.*$")


def _unseen_by_raw_scan(path: Path) -> list[str]:
    """Cross-check the raw-text inventory against the parsed one.

    The raw scan is line-based, so it can see the comment the parser drops —
    but it recognises only the canonical ``uses: value`` line. ``"uses":``,
    ``uses :``, a flow mapping ``{uses: x}`` and a block scalar ``uses: >-``
    are all valid YAML that GitHub runs and that the line scan does not see.
    Requiring the two inventories to be EQUAL makes every such form fail
    closed instead of running unpinned.
    """
    doc, err = _load(path)
    if err:
        return [err]
    parsed = sorted(v for v in _parsed_uses(doc) if not v.startswith("./"))
    raw = sorted(
        _TRAILING_COMMENT.sub("", value) for _, _, value in remote_uses([path])
    )
    if parsed == raw:
        return []
    only_parsed = sorted(set(parsed) - set(raw))
    only_raw = sorted(set(raw) - set(parsed))
    detail = []
    if only_parsed:
        detail.append(f"seen by the parser but not the scan: {only_parsed}")
    if only_raw:
        detail.append(f"seen by the scan but not the parser: {only_raw}")
    if not detail:
        detail.append(f"parser counts {len(parsed)}, scan counts {len(raw)}")
    return [
        f"[pin] {path}: uses: inventory mismatch — {'; '.join(detail)}. Every "
        "uses: must be a plain single-line `uses: owner/action@<sha> # <release>` "
        "(unquoted key, no space before the colon, no flow mapping, no block "
        "scalar), because only that form lets the scan see the release comment"
    ]


def all_uses_are_sha_pinned(paths: list[Path]) -> list[str]:
    """Every remote ``uses:`` must be ``owner/action@<40-hex-sha> # <release>``.

    Raw text, not parsed YAML: the parser drops the comment, and the comment is
    half of the convention — it is what Dependabot reads to propose the next
    release and what a reviewer reads to know which one this is. The parsed
    document is then used as a second inventory that the raw one must equal,
    so a ``uses:`` spelled in a form the line scan cannot see fails closed.
    """
    reasons: list[str] = []
    tag = "[pin]"
    for path in paths:
        reasons.extend(_unseen_by_raw_scan(path))
    for path, lineno, value in remote_uses(paths):
        line = path.read_text().splitlines()[lineno - 1]
        if SHA_PINNED_USES.match(line):
            continue
        if value.startswith("docker://"):
            reasons.append(
                f"{tag} {path}:{lineno} uses {value!r} — a docker:// reference is "
                "remote code this guard cannot pin by commit SHA; admit it by "
                "digest deliberately, in this file, not by default"
            )
            continue
        reasons.append(
            f"{tag} {path}:{lineno} uses {value!r} — not pinned to a 40-hex commit "
            "SHA with a trailing `# <release>` comment. A tag or branch is "
            "mutable: whoever controls the action's repository controls what "
            "runs here, with this job's permissions"
        )
    return reasons


# --- drift -------------------------------------------------------------------


class GitError(RuntimeError):
    """A git command failed.

    Never swallowed into an empty result. "The command failed" and "the answer
    is empty" are different facts, and conflating them is how a guard exits 0
    with its protection silently disabled.
    """


def _git(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=False)


def _git_checked(args: list[str]) -> str:
    result = _git(args)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        first = detail[0] if detail else "no output"
        raise GitError(
            f"`git {' '.join(args)}` failed (exit {result.returncode}): {first}"
        )
    return result.stdout


def _base_resolves(base_ref: str) -> bool:
    return (
        _git(["rev-parse", "--verify", "--quiet", f"{base_ref}^{{commit}}"]).returncode
        == 0
    )


def _job_names(text: str, label: str) -> tuple[set[str], str | None]:
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        first = str(exc).splitlines()[0] if str(exc) else exc.__class__.__name__
        return set(), f"[drift] {label} is not valid YAML: {first}"
    return set(_jobs(doc)), None


def changed_workflow_paths(base_ref: str) -> list[str]:
    """Paths under ``.github/workflows/`` that changed since ``base_ref``.

    ``--diff-filter=ACMRD`` **includes deletions**: a glob of the worktree
    cannot see a file that is gone, and a deleted workflow is precisely the
    case drift exists to catch. ``--no-renames`` makes a rename show up as a
    delete plus an add, so the removed jobs are reported.

    Raises ``GitError`` rather than returning ``[]`` on failure. A base that
    resolves but shares no history with HEAD makes ``git diff A...B`` exit 128
    with "no merge base"; reading that as "nothing changed" passes every
    subsequent check vacuously, on exactly the histories where drift matters —
    divergent or rewritten history, shallow clones, force-pushed bases. This is
    a *different* case from an unresolvable ref, and it defeats the same
    fail-closed property.
    """
    stdout = _git_checked(
        [
            "diff",
            "--name-only",
            "--no-renames",
            "--diff-filter=ACMRD",
            f"{base_ref}...HEAD",
            "--",
            str(WORKFLOW_DIR),
        ]
    )
    return [line for line in stdout.splitlines() if line.strip()]


def _base_workflow_paths(base_ref: str) -> set[str]:
    """Workflow paths that exist in the base tree.

    A positive membership test, deliberately. Inferring "the path is absent at
    the base" from a non-zero ``git cat-file -e`` is the same defect as above
    wearing a different hat: any other git failure would then read as "this file
    is new", and drift would pass on a file whose jobs had in fact been deleted.
    """
    stdout = _git_checked(
        ["ls-tree", "-r", "--name-only", base_ref, "--", str(WORKFLOW_DIR)]
    )
    return {line for line in stdout.splitlines() if line.strip()}


def job_set_drift(paths: list[str], base_ref: str) -> list[str]:
    """Fail on any job present in the base revision and absent in the head.

    Fails **closed** when the base itself cannot be resolved. Treating an
    unresolvable base (shallow clone, bad ref, failed fetch) the same as "the
    file is new" would silently disable every drift check while still exiting 0.
    """
    if not _base_resolves(base_ref):
        return [
            f"[drift] base ref {base_ref!r} does not resolve to a commit — "
            "refusing to pass with drift detection disabled (shallow clone? "
            "missing fetch-depth: 0?)"
        ]

    try:
        base_paths = _base_workflow_paths(base_ref)
    except GitError as exc:
        return [f"[drift] {exc} — refusing to pass with drift detection disabled"]

    reasons: list[str] = []
    for path in paths:
        blob = f"{base_ref}:{path}"
        if path not in base_paths:
            # The base resolves, the base tree was read, and this path is not in
            # it: a genuinely new workflow file. Nothing can have been removed.
            continue
        try:
            shown_stdout = _git_checked(["show", blob])
        except GitError as exc:
            reasons.append(f"[drift] {exc}")
            continue
        base_jobs, err = _job_names(shown_stdout, blob)
        if err:
            reasons.append(err)
            continue

        head = Path(path)
        if head.exists():
            head_jobs, err = _job_names(head.read_text(), path)
            if err:
                reasons.append(err)
                continue
        else:
            # A DELETED file has an empty job set. Reading it would raise, and
            # a traceback does not name the jobs that were removed.
            head_jobs = set()

        removed = sorted(base_jobs - head_jobs)
        if removed:
            state = "deleted" if not head.exists() else "modified"
            reasons.append(
                f"[drift] {path} ({state}): job(s) removed since {base_ref}: "
                f"{', '.join(removed)} — a rename counts as a removal; restore "
                "the job or state the removal in the PR"
            )
    return reasons


# --- entry point --------------------------------------------------------------


def _load(path: Path) -> tuple[Any, str | None]:
    try:
        return yaml.safe_load(path.read_text()), None
    except yaml.YAMLError as exc:
        first = str(exc).splitlines()[0] if str(exc) else exc.__class__.__name__
        return None, f"[yaml] {path} is not valid YAML: {first}"


def workflow_files() -> list[Path]:
    """Every workflow file. Globbing ``*.yml`` alone leaves a ``.yaml``
    workflow unchecked, and GitHub runs both extensions."""
    if not WORKFLOW_DIR.is_dir():
        return []
    found = set(WORKFLOW_DIR.glob("*.yml")) | set(WORKFLOW_DIR.glob("*.yaml"))
    return sorted(found)


def stable_context_invariants(doc: Any, path: Path) -> list[str]:
    """Only the two reviewed Test jobs may emit the new stable contexts."""
    for name, job in _jobs(doc).items():
        if not isinstance(job, dict):
            return ["[ci-gate] malformed job definition"]
        display = job.get("name", name)
        if isinstance(display, str) and "${{" in display:
            return ["[ci-gate] dynamic job names require deliberate context review"]
        for context in ("ci-scope", "ci-required"):
            if name == context or display == context:
                if path.name != "test.yml" or name != context or "name" in job:
                    return [
                        "[ci-gate] stable context collision or display-name override"
                    ]
    return []


EXPECTED_FULL_SUITE_COMMAND = "uv run --locked python -m pytest tests/ -v --tb=short -n auto --dist loadfile --cov --cov-report=xml --cov-report=term-missing -p tests.ci_diagnostics --junitxml=.ci-reports/raw/junit.xml --durations=50"


def full_bootstrap_invariants(doc: Any) -> list[str]:
    """Bind the complete embedded jobs and same-run inputs, not only code text."""
    try:
        jobs = doc["jobs"]
        if "defaults" in doc or "env" in doc:
            return ["[ci-gate] workflow defaults/env can alter the embedded contract"]
        if doc.get("name") != "Test" or get_on(doc) != {
            "push": {"branches": ["main"]},
            "pull_request": {"branches": ["main"]},
            "schedule": [{"cron": "0 8 * * 1"}],
            "workflow_dispatch": None,
        }:
            return ["[ci-gate] Test triggers must remain complete and unfiltered"]
        if "needs" in jobs["test"] or "if" in jobs["test"]:
            return ["[ci-gate] pre-S suite must remain independent and unconditional"]
        if jobs["test"].get("env") != {
            "UV_PYTHON": "3.12",
            "PMCP_CI_REPORT_DIR": "${{ github.workspace }}/.ci-reports/raw",
        } or jobs["test"].get("strategy") != {"matrix": {"python-version": ["3.12"]}}:
            return [
                "[ci-gate] preserve the full-suite environment and legacy matrix context"
            ]
        expected = [
            "ci-scope",
            "test",
            "install-smoke",
            "min-version-smoke",
            "lint",
            "typecheck",
            "workflows",
            "audit",
            "changelog",
            "release-diff-ack",
        ]
        for name, digest in BOOTSTRAP_STEPS_SHA256.items():
            value = jobs[name]
            step = value["steps"][0]
            code = step["run"]
            if hashlib.sha256(code.encode()).hexdigest() != digest:
                return [
                    "[ci-gate] embedded FULL-only contract changed; deliberate review required"
                ]
            common = {
                "runs-on": "ubuntu-latest",
                "timeout-minutes": 10,
                "permissions": {"contents": "read"},
            }
            env = {"FORCE_FULL": "${{ vars.PMCP_CI_FORCE_FULL }}"}
            if name == "ci-scope":
                required = common | {
                    "outputs": {
                        key: "${{ steps.scope.outputs." + key + " }}"
                        for key in ("mode", "proof_valid", "decision")
                    },
                    "steps": [
                        {
                            "name": "Emit FULL-only bootstrap decision",
                            "id": "scope",
                            "env": env,
                            "run": code,
                        }
                    ],
                }
            else:
                env.update(
                    NEEDS_JSON="${{ toJSON(needs) }}",
                    SCOPE_DECISION="${{ needs.ci-scope.outputs.decision }}",
                )
                required = common | {
                    "if": "always()",
                    "needs": expected,
                    "steps": [
                        {
                            "name": "Evaluate FULL-only bootstrap and recovery contract",
                            "env": env,
                            "run": code,
                        }
                    ],
                }
            if value != required:
                return [
                    "[ci-gate] complete bootstrap job/wiring must match the reviewed contract"
                ]
        diagnostics = {
            "Record environment identity": "always()",
            "Sanitize reports": "always()",
            "Upload safe reports": "${{ always() && steps.sanitize.outcome == 'success' && vars.PMCP_CI_DISABLE_REPORTS == '' }}",
            "Report diagnostic availability": "always()",
        }
        for name in expected[1:]:
            job = jobs[name]
            if "name" in job or "continue-on-error" in job or "defaults" in job:
                return [
                    "[ci-gate] selected jobs cannot rename, ignore failures or override shells"
                ]
            condition = (
                "github.event_name == 'pull_request'"
                if name in {"changelog", "release-diff-ack"}
                else None
            )
            if ("if" in job) != (condition is not None) or job.get("if") != condition:
                return [
                    "[ci-gate] selected jobs must execute except enumerated PR-only checks"
                ]
            labels = [step["name"] for step in job["steps"] if "name" in step]
            if any(not isinstance(label, str) for label in labels) or len(
                labels
            ) != len(set(labels)):
                return ["[ci-gate] selected step names must be unique and literal"]
            for selected in job["steps"]:
                if "shell" in selected:
                    return ["[ci-gate] selected verification cannot override its shell"]
                if "continue-on-error" in selected:
                    return ["[ci-gate] selected verification cannot ignore failures"]
                if "if" in selected and (
                    name != "test"
                    or selected.get("name") not in diagnostics
                    or selected["if"] != diagnostics[selected["name"]]
                ):
                    return [
                        "[ci-gate] selected verification cannot be conditionally skipped"
                    ]
        audit = jobs["audit"]["steps"][-1]
        if (
            hashlib.sha256(json.dumps(audit, sort_keys=True).encode()).hexdigest()
            != AUDIT_VERIFY_SHA256
        ):
            return [
                "[ci-gate] locked P3-A audit execution must remain unconditional and exact"
            ]
        suites = [
            s
            for s in jobs["test"]["steps"]
            if s.get("run") == EXPECTED_FULL_SUITE_COMMAND
        ]
        if len(suites) != 1 or suites[0] != {
            "name": "Run tests with coverage",
            "run": EXPECTED_FULL_SUITE_COMMAND,
        }:
            return [
                "[ci-gate] actual full suite command must bind its unconditional step, name and invocation"
            ]
        failures = stable_context_invariants(doc, Path("test.yml"))
        if failures:
            return failures
    except (KeyError, TypeError, AttributeError, IndexError, StopIteration):
        return ["[ci-gate] missing bootstrap contract"]
    return []


def validation_concurrency_invariants(doc: Any, path: Path) -> list[str]:
    if path.name in {"test.yml", "windows.yml"} and (
        doc.get("concurrency") != EXPECTED_VALIDATION_CONCURRENCY
        or any("concurrency" in job for job in _jobs(doc).values())
    ):
        return [
            "[concurrency] isolate PR cancellation and retain each integration run: "
            + str(path)
        ]
    return []


def diagnostic_invariants(doc: Any) -> list[str]:
    """Uploads must be gated on reconstruction, never raw test output."""
    try:
        steps = doc["jobs"]["test"]["steps"]
        sanitizers = [s for s in steps if s.get("id") == "sanitize"]
        uploads = [
            s
            for s in steps
            if str(s.get("uses", "")).startswith("actions/upload-artifact@")
        ]
        if len(sanitizers) != 1 or len(uploads) != 1:
            return ["[diagnostics] exactly one sanitizer and safe upload required"]
        identity_steps = [
            s for s in steps if s.get("name") == "Record environment identity"
        ]
        if len(identity_steps) != 1:
            return ["[diagnostics] exactly one environment recorder required"]
        recorder = identity_steps[0]
        tests = [
            i for i, s in enumerate(steps) if s.get("name") == "Run tests with coverage"
        ]
        if (
            len(tests) != 1
            or recorder.get("if") != "always()"
            or recorder.get("continue-on-error")
            or steps.index(recorder) <= tests[0]
            or steps.index(recorder) >= steps.index(sanitizers[0])
            or recorder.get("run")
            != "mkdir -p .ci-reports/raw\nuv run --locked python scripts/ci_environment.py .ci-reports/raw/environment.json\n"
        ):
            return ["[diagnostics] environment evidence follows tests even on failure"]
        sanitizer, upload = sanitizers[0], uploads[0]
        if (
            sanitizer.get("if") != "always()"
            or sanitizer.get("continue-on-error")
            or sanitizer.get("run")
            != "python3 -I -B scripts/ci_reports.py .ci-reports/raw .ci-reports/safe"
        ):
            return ["[diagnostics] sanitizer must run after failures and fail closed"]
        if (
            upload.get("continue-on-error")
            or upload.get("if")
            != "${{ always() && steps.sanitize.outcome == 'success' && vars.PMCP_CI_DISABLE_REPORTS == '' }}"
        ):
            return [
                "[diagnostics] upload must require successful sanitization and honor disable"
            ]
        if upload.get("with") != {
            "name": "ci-reports-${{ github.run_id }}-${{ github.run_attempt }}-linux",
            "path": ".ci-reports/safe/",
            "retention-days": 14,
            "if-no-files-found": "error",
        }:
            return [
                "[diagnostics] upload only bounded safe reports with run/attempt identity"
            ]
    except (KeyError, TypeError, AttributeError):
        return ["[diagnostics] missing diagnostic contract"]
    return []


# --- guarded image publication ----------------------------------------------

IMAGE_TAG = "[image]"
DOCKER_WORKFLOW = "docker.yml"
IMAGE_WORKFLOW = "image.yml"
IMAGE_WATCH_WORKFLOW = "image-watch.yml"
PROMOTE_JOB = "promote-main"
IMAGE_TOP_LEVEL_KEYS = {"name", "on", True, "permissions", "jobs"}
DOCKER_ON = {"pull_request": {"branches": ["main"]}}
IMAGE_ON = {
    "workflow_run": {
        "workflows": ["Test"],
        "types": ["completed"],
        "branches": ["main"],
    }
}
IMAGE_WATCH_CRON = "0 9 * * 1"
IMAGE_JOB_PERMISSIONS: dict[str, dict[str, str]] = {
    "gate": {},
    "verify-upstream": {"actions": "read", "contents": "read"},
    "build-smoke": {"contents": "read"},
    PROMOTE_JOB: {"contents": "read", "packages": "write"},
}
IMAGE_WATCH_PERMISSIONS = {"contents": "read", "actions": "read", "packages": "read"}
GATE_ENV = {"PMCP_IMAGE_PUBLISH": "${{ vars.PMCP_IMAGE_PUBLISH }}"}
GATE_OUTPUTS = {"open": "${{ steps.decide.outputs.open }}"}
# sha256 of the gate step's whole `run:` heredoc. Any edit, including a
# case-folding `.lower()`, needs a deliberate update here.
GATE_RUN_SHA256 = "19effd6ec3af5fb790c95c80f59d4a237c66724638f74077b665587b24b94115"
PROMOTE_IF = "${{ needs.gate.outputs.open == 'yes' }}"
PROMOTE_NEEDS = {"gate", "verify-upstream", "build-smoke"}
PROMOTE_ENV = {
    "PMCP_IMAGE_PUBLISH": "${{ vars.PMCP_IMAGE_PUBLISH }}",
    "GH_TOKEN": "${{ github.token }}",
    "MANIFEST_DIGEST": "${{ needs.build-smoke.outputs.manifest_digest }}",
    "HEAD_SHA": "${{ needs.verify-upstream.outputs.head_sha }}",
}
PROMOTE_CONCURRENCY = {"group": "pmcp-image-main", "cancel-in-progress": False}
TRUSTED_CHECKOUT = {
    "ref": "${{ github.workflow_sha }}",
    "sparse-checkout": "scripts/image_publish.py",
    "sparse-checkout-cone-mode": False,
    "persist-credentials": False,
}
CANDIDATE_CHECKOUT = {
    "ref": "${{ needs.verify-upstream.outputs.head_sha }}",
    "persist-credentials": False,
}
IMAGE_WATCH_CHECKOUT = {
    "ref": "${{ github.sha }}",
    "sparse-checkout": "scripts/image_cutover_inventory.py\n"
    "scripts/image_registry_readback.py\n"
    ".consiliency/evidence/image-baselines/*\n",
    "sparse-checkout-cone-mode": False,
    "persist-credentials": False,
}
# The single skopeo form allowed outside promote-main: a local load of the
# verified archive into the runner's daemon, in build-smoke only.
SKOPEO_SMOKE_LOAD = re.compile(
    r"quay\.io/skopeo/stable@sha256:[0-9a-f]{64} (?:--insecure-policy )?"
    r"copy oci-archive:[^\s;&|]+ docker-daemon:[^\s;&|]+"
)
PUBLICATION_PATTERNS = {
    "a buildx --push": re.compile(r"(?<![\w-])--push\b"),
    "a type=registry output": re.compile(r"type=registry"),
    "a pushing type=image output": re.compile(r"type=image[^\n]*push=true"),
    "docker push": re.compile(r"\bdocker\s+(?:image\s+)?push\b"),
    "podman push": re.compile(r"\bpodman\s+(?:image\s+)?push\b"),
    "buildah push": re.compile(r"\bbuildah\s+push\b"),
    "crane": re.compile(r"\bcrane\b"),
    "oras": re.compile(r"\boras\b"),
    "regctl": re.compile(r"\bregctl\b"),
    "docker login": re.compile(r"\bdocker\s+login\b"),
    "an image_publish.py write": re.compile(r"image_publish\.py\s+(?:promote|tag)\b"),
}
SKOPEO_DELETE = re.compile(r"\bskopeo\b[^\n]*\bdelete\b", re.IGNORECASE)
MUTATING_HTTP_LABEL = "a mutating curl/wget"
_SAFE_METHODS = {"GET", "HEAD"}
_CURL_IMPLIED_LONG = ("--data", "--form", "--json", "--upload-file")
_WGET_IMPLIED_LONG = ("--post-data", "--post-file", "--body-data", "--body-file")
# curl short options whose value may be attached: the rest of the word after
# one of these is a value, never further option letters.
_CURL_VALUE_SHORT = set("AbcCdDeEFHKmoPQrtTuUwxXyYz")
_SHELL_SEPARATORS = set(";&|()`\n")
_METHOD_WORD = re.compile(r"\$.*|[A-Za-z][A-Za-z0-9_-]*")
_MAX_NESTING = 4


class _Word:
    """One shell word after quote removal, with the contents of its quoted parts."""

    def __init__(self) -> None:
        self.text = ""
        self.quoted: list[str] = []
        self.started = False


def _shell_commands(text: str) -> list[list[_Word]] | None:
    """Split text the way bash does: words at unquoted blanks, commands at
    unquoted ``;``, ``&``, ``|``, ``(``, ``)``, backtick and newline, quotes
    removed. ``None`` when the quotes do not balance."""
    commands: list[list[_Word]] = [[]]
    word = _Word()

    def finish() -> None:
        nonlocal word
        if word.started:
            commands[-1].append(word)
        word = _Word()

    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch in ("'", '"'):
            close = i + 1
            if ch == "'":
                close = text.find("'", i + 1)
                if close < 0:
                    return None
                inner = text[i + 1 : close]
            else:
                parts = []
                while True:
                    if close >= n:
                        return None
                    if text[close] == "\\" and close + 1 < n:
                        parts.append(text[close + 1])
                        close += 2
                        continue
                    if text[close] == '"':
                        break
                    parts.append(text[close])
                    close += 1
                inner = "".join(parts)
            word.text += inner
            word.quoted.append(inner)
            word.started = True
            i = close + 1
            continue
        if ch == "\\" and i + 1 < n:
            word.text += text[i + 1]
            word.started = True
            i += 2
            continue
        if ch in _SHELL_SEPARATORS:
            finish()
            commands.append([])
            i += 1
            continue
        if ch.isspace():
            finish()
            i += 1
            continue
        word.text += ch
        word.started = True
        i += 1
    finish()
    return [command for command in commands if command]


def _method_writes(method: str) -> bool:
    """A method other than GET or HEAD; a ``$`` expansion counts as a write."""
    return _METHOD_WORD.fullmatch(method) is not None and (
        method.startswith("$") or method.upper() not in _SAFE_METHODS
    )


def _curl_writes(args: list[str]) -> bool:
    for index, word in enumerate(args):
        following = args[index + 1] if index + 1 < len(args) else ""
        if word.startswith("--"):
            option, _, attached = word.partition("=")
            if option == "--request" and _method_writes(attached or following):
                return True
            if option.startswith(_CURL_IMPLIED_LONG):
                return True
        elif word.startswith("-") and len(word) > 1:
            for position, letter in enumerate(word[1:], start=1):
                if not letter.isalnum():
                    break
                if letter in "dFT":
                    return True
                if letter == "X":
                    rest = word[position + 1 :]
                    if _method_writes(rest or following):
                        return True
                    break
                if letter in _CURL_VALUE_SHORT:
                    break
    return False


def _wget_writes(args: list[str]) -> bool:
    for index, word in enumerate(args):
        option, _, attached = word.partition("=")
        following = args[index + 1] if index + 1 < len(args) else ""
        if option == "--method" and _method_writes(attached or following):
            return True
        if option.startswith(_WGET_IMPLIED_LONG):
            return True
    return False


def _http_writes(text: str, depth: int) -> bool:
    if depth > _MAX_NESTING:
        return False
    commands = _shell_commands(text)
    if commands is None:
        # Unbalanced quotes: classify conservatively, untruncated, with every
        # quote character removed.
        flat = [w for w in re.sub("['\"]", "", text).split() if w]
        commands_words = [flat]
        quoted: list[str] = []
    else:
        commands_words = [[w.text for w in command] for command in commands]
        quoted = [q for command in commands for w in command for q in w.quoted]
    for words in commands_words:
        for index, word in enumerate(words):
            tool = word.rsplit("/", 1)[-1]
            if tool == "curl" and _curl_writes(words[index + 1 :]):
                return True
            if tool == "wget" and _wget_writes(words[index + 1 :]):
                return True
    # A quoted word is classified as a command too (bash -c "...", ssh h "...").
    return any(_http_writes(inner, depth + 1) for inner in quoted)


def mutating_http(text: str) -> bool:
    """A curl or wget that sends a method other than GET or HEAD.

    The text is read as bash reads it (words split at unquoted blanks,
    commands at unquoted ``; & | ( )``, backtick or newline, quotes removed);
    the command word is recognised by its basename anywhere in a command
    (``/usr/bin/curl``, ``timeout 5 curl``, ``env A=1 curl``), and quoted
    words are classified as commands too (``bash -c "..."``). A method is sent
    explicitly (``-X``/``--request``/``--method``, attached or not) or
    implied: curl ``-d``/``--data*``, ``-F``/``--form*``, ``--json``,
    ``-T``/``--upload-file`` and any short group whose option letters include
    ``d``, ``F`` or ``T``, whatever value is attached; wget
    ``--post-data``/``--post-file``/``--body-data``/``--body-file``. Static
    text only: a command assembled at runtime is out of reach.
    """
    return _http_writes(_joined(text), 0)


# The one pinned exemption from the curl/wget clause (plan T7(d), president
# re-check 1): the maintenance-worker event POST that predates the cutover.
# The pin is the sha256 of canonical JSON of the WHOLE job mapping as
# yaml.safe_load returns it (json.dumps with sort_keys=True,
# separators=(",", ":"), ensure_ascii=False, allow_nan=False), so any edit to
# that job (run block, env, an added step, shell, container, defaults) re-arms
# the clause. Recompute with exactly that recipe. This file is covered by
# release-diff-ack, so changing the pin needs the owner's acknowledgement.
MUTATING_HTTP_EXEMPT = {
    ("maintenance.yml", "notify-worker"): (
        "3391171c6bdbd2ba03e5ae24bd0d636d9160141e2495cff6dfb737632de1881b"
    ),
}


def job_pin(job: Any) -> str | None:
    """Canonical-JSON sha256 of a job mapping; ``None`` if it does not serialize."""
    try:
        canonical = json.dumps(
            job,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError):
        return None
    return hashlib.sha256(canonical.encode()).hexdigest()


def _defaults_shell(node: Any) -> str | None:
    try:
        shell = node["defaults"]["run"]["shell"]
    except (KeyError, TypeError):
        return None
    return shell if isinstance(shell, str) else None


IMAGE_RUN_FORBIDDEN = {
    "git clone/fetch/pull/checkout": re.compile(
        r"\bgit\s+(?:clone|fetch|pull|checkout)\b"
    ),
    "gh repo clone": re.compile(r"\bgh\s+repo\s+clone\b"),
    "curl": re.compile(r"\bcurl\b"),
    "wget": re.compile(r"\bwget\b"),
    "raw.githubusercontent": re.compile(r"raw\.githubusercontent"),
    "a contents API path": re.compile(r"/contents/"),
    "workflow_run.head_sha": re.compile(r"workflow_run\.head_sha"),
}
ACK_REQUIRED_PATHS = (
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
)


def _strings(node: Any) -> list[str]:
    """Every string key and value in a parsed YAML subtree."""
    if isinstance(node, str):
        return [node]
    if isinstance(node, dict):
        found: list[str] = []
        for key, value in node.items():
            if isinstance(key, str):
                found.append(key)
            found.extend(_strings(value))
        return found
    if isinstance(node, list):
        return [s for item in node for s in _strings(item)]
    return []


def _steps(job: Any) -> list[dict[str, Any]]:
    steps = job.get("steps") if isinstance(job, dict) else None
    return [s for s in steps if isinstance(s, dict)] if isinstance(steps, list) else []


def _joined(text: str) -> str:
    """Shell line continuations joined, so a split command is one line."""
    return re.sub(r"\\\n\s*", " ", text)


def _step_text(step: dict[str, Any]) -> str:
    parts = (
        [step.get("run"), step.get("shell")]
        + _strings(step.get("with"))
        + _strings(step.get("env"))
    )
    return _joined("\n".join(p for p in parts if isinstance(p, str)))


def _with_form(step: dict[str, Any], form: dict[str, Any]) -> bool:
    given = step.get("with")
    if not isinstance(given, dict):
        return False
    extra = {k: v for k, v in given.items() if k not in form}
    if set(extra) - {"path"}:
        return False
    path = extra.get("path")
    if path is not None and (not isinstance(path, str) or "${{" in path or not path):
        return False
    return {k: given.get(k) for k in form} == form


def _ack_regex(workflows: dict[str, Any]) -> str | None:
    try:
        job = workflows["test.yml"]["jobs"]["release-diff-ack"]
        for step in _steps(job):
            if "Require" in str(step.get("name", "")):
                match = re.search(r"grep -Eq '([^']+)'", step["run"])
                return match[1] if match else None
    except (KeyError, TypeError):
        return None
    return None


def image_publication_invariants(
    workflows: dict[str, Any], tag_triggered: set[str] | None = None
) -> list[str]:
    """The guarded image cutover, checked across every workflow file."""
    tag = IMAGE_TAG
    reasons: list[str] = []
    allowlist = TAG_TRIGGERED_WORKFLOWS if tag_triggered is None else tag_triggered

    # (b) Only the release path may be tag-triggered.
    if allowlist != {"release.yml"}:
        reasons.append(f"{tag} TAG_TRIGGERED_WORKFLOWS must be exactly {{release.yml}}")

    # (a) docker.yml: a read-only pull-request build.
    docker = workflows.get(DOCKER_WORKFLOW)
    if not isinstance(docker, dict):
        reasons.append(f"{tag} {DOCKER_WORKFLOW} is missing or not a mapping")
    else:
        if get_on(docker) != DOCKER_ON:
            reasons.append(
                f"{tag} {DOCKER_WORKFLOW} on: must be exactly pull_request to main"
            )
        jobs = _jobs(docker)
        if set(jobs) != {"build"}:
            reasons.append(f"{tag} {DOCKER_WORKFLOW} jobs must be exactly {{build}}")
        if docker.get("permissions", {"contents": "read"}) != {"contents": "read"}:
            reasons.append(
                f"{tag} {DOCKER_WORKFLOW} workflow permissions must be contents: read"
            )
        build = jobs.get("build")
        if isinstance(build, dict) and build.get("permissions") != {"contents": "read"}:
            reasons.append(
                f"{tag} {DOCKER_WORKFLOW}:build permissions must be exactly contents: read"
            )
        if any("secrets." in s for s in _strings(docker)):
            reasons.append(f"{tag} {DOCKER_WORKFLOW} must not reference secrets.")
        for job in jobs.values():
            for step in _steps(job):
                uses = str(step.get("uses", ""))
                if uses.startswith("docker/login-action"):
                    reasons.append(
                        f"{tag} {DOCKER_WORKFLOW} must not log in to a registry"
                    )
                given = step.get("with") if isinstance(step.get("with"), dict) else {}
                if "cache-to" in given:
                    reasons.append(
                        f"{tag} {DOCKER_WORKFLOW} must not export a build cache"
                    )
                if (
                    uses.startswith("docker/build-push-action")
                    and given.get("push") is not False
                ):
                    reasons.append(
                        f"{tag} {DOCKER_WORKFLOW} build push: must be literally false"
                    )

    image = workflows.get(IMAGE_WORKFLOW)
    watch = workflows.get(IMAGE_WATCH_WORKFLOW)
    for name, doc in ((IMAGE_WORKFLOW, image), (IMAGE_WATCH_WORKFLOW, watch)):
        if not isinstance(doc, dict):
            reasons.append(f"{tag} {name} is missing or not a mapping")

    # (c) Privilege: packages: write / write-all only on image.yml:promote-main.
    for name, doc in sorted(workflows.items()):
        if not isinstance(doc, dict):
            continue
        scopes = [("<workflow>", doc.get("permissions"))] + [
            (job_name, job.get("permissions"))
            for job_name, job in _jobs(doc).items()
            if isinstance(job, dict)
        ]
        for where, perms in scopes:
            writes = perms == "write-all" or (
                isinstance(perms, dict)
                and (perms.get("packages") == "write" or "write-all" in perms.values())
            )
            if not writes:
                continue
            if (
                name != IMAGE_WORKFLOW
                or where != PROMOTE_JOB
                or perms != IMAGE_JOB_PERMISSIONS[PROMOTE_JOB]
            ):
                reasons.append(
                    f"{tag} {name}:{where} grants packages: write or write-all; only "
                    f"{IMAGE_WORKFLOW}:{PROMOTE_JOB} may, as exactly contents: read, packages: write"
                )
        if name in {DOCKER_WORKFLOW, IMAGE_WORKFLOW, IMAGE_WATCH_WORKFLOW}:
            for job_name, job in _jobs(doc).items():
                if not isinstance(job, dict) or "permissions" not in job:
                    reasons.append(f"{tag} {name}:{job_name} must declare permissions")
    if isinstance(image, dict):
        if image.get("permissions") != {}:
            reasons.append(f"{tag} {IMAGE_WORKFLOW} workflow permissions must be {{}}")
        for job_name, expected in IMAGE_JOB_PERMISSIONS.items():
            job = _jobs(image).get(job_name)
            if not isinstance(job, dict):
                reasons.append(f"{tag} {IMAGE_WORKFLOW}:{job_name} is missing")
            elif job.get("permissions") != expected:
                reasons.append(
                    f"{tag} {IMAGE_WORKFLOW}:{job_name} permissions must be exactly {expected}"
                )
    if isinstance(watch, dict):
        if watch.get("permissions") != IMAGE_WATCH_PERMISSIONS:
            reasons.append(f"{tag} {IMAGE_WATCH_WORKFLOW} workflow permissions changed")
        for job_name, job in _jobs(watch).items():
            if (
                isinstance(job, dict)
                and job.get("permissions") != IMAGE_WATCH_PERMISSIONS
            ):
                reasons.append(
                    f"{tag} {IMAGE_WATCH_WORKFLOW}:{job_name} permissions changed"
                )

    # (d) Publication commands, in workflow steps outside promote-main.
    for name, doc in sorted(workflows.items()):
        if not isinstance(doc, dict):
            continue
        # defaults.run.shell wraps every run step: scanned with every (d)
        # pattern at both levels, and never exempt.
        shells = [("<workflow>", _defaults_shell(doc))] + [
            (job_name, _defaults_shell(job)) for job_name, job in _jobs(doc).items()
        ]
        for where_name, shell in shells:
            if shell is None:
                continue
            where = f"{name}:{where_name} defaults.run.shell"
            hits = [
                label
                for label, pattern in PUBLICATION_PATTERNS.items()
                if pattern.search(shell)
            ]
            if mutating_http(shell):
                hits.append(MUTATING_HTTP_LABEL)
            if re.search(r"skopeo", shell, re.IGNORECASE):
                hits.append("skopeo")
            reasons.extend(f"{tag} {where} contains {label}" for label in hits)
        for job_name, job in _jobs(doc).items():
            promote = name == IMAGE_WORKFLOW and job_name == PROMOTE_JOB
            smoke_job = name == IMAGE_WORKFLOW and job_name == "build-smoke"
            pinned = MUTATING_HTTP_EXEMPT.get((name, job_name))
            http_exempt = pinned is not None and job_pin(job) == pinned
            for step in _steps(job):
                text = _step_text(step)
                where = f"{name}:{job_name}"
                if SKOPEO_DELETE.search(text):
                    reasons.append(f"{tag} {where} runs skopeo delete")
                if promote:
                    continue
                uses = str(step.get("uses", ""))
                given = step.get("with") if isinstance(step.get("with"), dict) else {}
                if (
                    uses.startswith("docker/build-push-action")
                    and given.get("push") is not False
                ):
                    reasons.append(
                        f"{tag} {where} build-push-action push: is not literally false"
                    )
                for label, pattern in PUBLICATION_PATTERNS.items():
                    if pattern.search(text):
                        reasons.append(f"{tag} {where} contains {label}")
                if not http_exempt and mutating_http(text):
                    reasons.append(f"{tag} {where} contains {MUTATING_HTTP_LABEL}")
                remaining = SKOPEO_SMOKE_LOAD.sub("", text) if smoke_job else text
                if re.search(r"skopeo", remaining, re.IGNORECASE):
                    reasons.append(
                        f"{tag} {where} uses skopeo outside the pinned local load in build-smoke"
                    )
    for name, doc in sorted(workflows.items()):
        if not isinstance(doc, dict) or name == IMAGE_WATCH_WORKFLOW:
            continue
        for key, value in doc.items():
            if name == DOCKER_WORKFLOW and key == "env":
                continue
            if key == "jobs" and isinstance(value, dict):
                for job_name, job in value.items():
                    if name == IMAGE_WORKFLOW and job_name == PROMOTE_JOB:
                        continue
                    if any("ghcr.io" in s for s in _strings(job)):
                        reasons.append(f"{tag} {name}:{job_name} names ghcr.io")
            elif any("ghcr.io" in s for s in _strings(value)):
                reasons.append(f"{tag} {name} names ghcr.io outside an allowed place")

    # (e) The gate, the promote condition and its phase-1 environment.
    for name, doc in sorted(workflows.items()):
        for job_name, job in _jobs(doc).items():
            if not isinstance(job, dict):
                continue
            conditions = [job.get("if")] + [s.get("if") for s in _steps(job)]
            if any(
                "vars.PMCP_IMAGE_PUBLISH" in str(c) for c in conditions if c is not None
            ):
                reasons.append(
                    f"{tag} {name}:{job_name} decides on vars.PMCP_IMAGE_PUBLISH in an if:"
                )
    if isinstance(image, dict):
        # A workflow-level env (BASH_ENV) or defaults.run.shell reaches the
        # gate step before its heredoc runs; only these top-level keys may
        # exist. PyYAML reads a bare `on:` as True.
        extra = sorted(str(k) for k in image if k not in IMAGE_TOP_LEVEL_KEYS)
        if extra:
            reasons.append(
                f"{tag} {IMAGE_WORKFLOW} top-level keys must be a subset of "
                f"name, on, permissions and jobs; found {', '.join(extra)}"
            )
        jobs = _jobs(image)
        gate = jobs.get("gate")
        steps = _steps(gate)
        if (
            not isinstance(gate, dict)
            or set(gate)
            - {"runs-on", "timeout-minutes", "permissions", "outputs", "steps"}
            or gate.get("permissions") != {}
            or gate.get("outputs") != GATE_OUTPUTS
            or len(steps) != 1
            or len(gate.get("steps") or []) != 1
            or set(steps[0]) != {"id", "env", "run"}
            or steps[0].get("id") != "decide"
            or steps[0].get("env") != GATE_ENV
            or not isinstance(steps[0].get("run"), str)
            or hashlib.sha256(steps[0]["run"].encode()).hexdigest() != GATE_RUN_SHA256
        ):
            reasons.append(f"{tag} {IMAGE_WORKFLOW}:gate differs from its pinned form")
        promote_job = jobs.get(PROMOTE_JOB)
        if isinstance(promote_job, dict):
            needs = _as_list(promote_job.get("needs"))
            if promote_job.get("if") != PROMOTE_IF:
                reasons.append(
                    f"{tag} {IMAGE_WORKFLOW}:{PROMOTE_JOB} if: differs from the pinned gate test"
                )
            if set(needs) != PROMOTE_NEEDS or len(needs) != len(PROMOTE_NEEDS):
                reasons.append(
                    f"{tag} {IMAGE_WORKFLOW}:{PROMOTE_JOB} needs must be {sorted(PROMOTE_NEEDS)}"
                )
            invokers = [
                s
                for s in _steps(promote_job)
                if PUBLICATION_PATTERNS["an image_publish.py write"].search(
                    str(s.get("run", ""))
                )
            ]
            if len(invokers) != 1 or invokers[0].get("env") != PROMOTE_ENV:
                reasons.append(
                    f"{tag} {IMAGE_WORKFLOW}:{PROMOTE_JOB} phase-1 environment differs"
                )
            if any(
                re.search(r"\bset\s+-[a-wyz]*x", str(s.get("run", "")))
                for s in _steps(promote_job)
            ):
                reasons.append(
                    f"{tag} {IMAGE_WORKFLOW}:{PROMOTE_JOB} must not trace commands"
                )
            # (g) One serialised namespace, never cancelled.
            if promote_job.get("concurrency") != PROMOTE_CONCURRENCY:
                reasons.append(
                    f"{tag} {IMAGE_WORKFLOW}:{PROMOTE_JOB} concurrency must be pmcp-image-main without cancellation"
                )
        if any("secrets." in s for s in _strings(image)):
            reasons.append(f"{tag} {IMAGE_WORKFLOW} must not reference secrets.")

        # (f) Trusted code source and candidate checkout.
        for job_name, job in jobs.items():
            for step in _steps(job):
                uses = str(step.get("uses", ""))
                where = f"{IMAGE_WORKFLOW}:{job_name}"
                if uses.startswith("actions/checkout@"):
                    trusted = _with_form(step, TRUSTED_CHECKOUT)
                    candidate = job_name == "build-smoke" and _with_form(
                        step, CANDIDATE_CHECKOUT
                    )
                    if job_name == "gate" or not (trusted or candidate):
                        reasons.append(
                            f"{tag} {where} checkout is neither the trusted nor the candidate form"
                        )
                if uses.startswith("actions/download-artifact@"):
                    given = (
                        step.get("with") if isinstance(step.get("with"), dict) else {}
                    )
                    if "run-id" in given or "github-token" in given:
                        reasons.append(
                            f"{tag} {where} downloads another run's artifact"
                        )
                if "run" in step:
                    text = _joined(
                        "\n".join(
                            s
                            for s in [step.get("run"), step.get("shell")]
                            + _strings(step.get("env"))
                            if isinstance(s, str)
                        )
                    )
                    for label, pattern in IMAGE_RUN_FORBIDDEN.items():
                        if pattern.search(text):
                            reasons.append(f"{tag} {where} run step contains {label}")

        # (h) Trigger: only the completed Test run on main; nothing calls it.
        if get_on(image) != IMAGE_ON:
            reasons.append(
                f"{tag} {IMAGE_WORKFLOW} on: must be exactly the workflow_run block"
            )
    if isinstance(watch, dict):
        watch_on = get_on(watch)
        if (
            not isinstance(watch_on, dict)
            or set(watch_on) != {"schedule", "workflow_dispatch"}
            or watch_on.get("schedule") != [{"cron": IMAGE_WATCH_CRON}]
            or watch_on.get("workflow_dispatch") not in (None, {})
        ):
            reasons.append(
                f"{tag} {IMAGE_WATCH_WORKFLOW} on: must be exactly schedule plus workflow_dispatch"
            )
        checkouts = [
            s
            for job in _jobs(watch).values()
            for s in _steps(job)
            if str(s.get("uses", "")).startswith("actions/checkout@")
        ]
        if len(checkouts) != 1 or checkouts[0].get("with") != IMAGE_WATCH_CHECKOUT:
            reasons.append(
                f"{tag} {IMAGE_WATCH_WORKFLOW} checkout differs from its pinned form"
            )
    for name, doc in sorted(workflows.items()):
        for job_name, job in _jobs(doc).items():
            uses = job.get("uses") if isinstance(job, dict) else None
            if isinstance(uses, str) and re.search(r"(^|/)image\.yml(@|$)", uses):
                reasons.append(f"{tag} {name}:{job_name} calls {IMAGE_WORKFLOW}")

    # (i) The release acknowledgement covers every image publication input.
    pattern = _ack_regex(workflows)
    try:
        compiled = re.compile(pattern) if pattern else None
    except re.error:
        compiled = None
    if compiled is None:
        reasons.append(f"{tag} release-diff-ack path regex not found in test.yml")
    else:
        for path in ACK_REQUIRED_PATHS:
            if not compiled.search(path):
                reasons.append(f"{tag} release-diff-ack does not cover {path}")
    return reasons


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--base-ref",
        default=None,
        help=(
            "Base commit for job-set drift. pull_request: "
            "github.event.pull_request.base.sha; push: github.event.before; "
            "schedule/workflow_dispatch: pass empty, drift is skipped."
        ),
    )
    args = parser.parse_args(argv)
    # An empty --base-ref is 'no base', never a consumed next argument.
    base_ref = args.base_ref or None

    failures: list[str] = []

    # 1. release.yml invariants — always run, including when the file is gone.
    if RELEASE_PATH.exists():
        release_doc, err = _load(RELEASE_PATH)
        if err:
            failures.append(err)
        else:
            failures.extend(release_invariants(release_doc))
    else:
        failures.extend(release_invariants(None))

    # 2. timeout invariants — every workflow file.
    docs: dict[str, Any] = {}
    for path in workflow_files():
        doc, err = _load(path)
        if err:
            failures.append(err)
            continue
        docs[path.name] = doc
        failures.extend(timeout_invariants(doc, path))
        failures.extend(tag_trigger_invariants(doc, path))
        failures.extend(stable_context_invariants(doc, path))
        failures.extend(validation_concurrency_invariants(doc, path))
        if path.name == "test.yml":
            failures.extend(diagnostic_invariants(doc))
            failures.extend(full_bootstrap_invariants(doc))

    # 2a. guarded image publication — across every workflow at once.
    failures.extend(image_publication_invariants(docs))

    # 2b. SHA pinning — raw text, every workflow AND every local action. Wired
    # here, not only into pytest: the evidence loop runs this CLI.
    failures.extend(all_uses_are_sha_pinned(pin_scan_files()))

    # 3. job-set drift — only against a base.
    if base_ref is None:
        print(
            "drift: SKIPPED — no --base-ref given (schedule/workflow_dispatch "
            "have no base to diff against); invariants ran"
        )
    elif not _base_resolves(base_ref):
        # Reported before the diff is attempted, so this stays distinguishable
        # from a diff that fails for some other reason. They are different
        # faults and must not be conflated behind one message.
        print(f"drift: base {base_ref} does NOT resolve")
        failures.extend(job_set_drift([], base_ref))
    else:
        try:
            paths = changed_workflow_paths(base_ref)
        except GitError as exc:
            # Never "no paths changed". A failed diff is a failed diff.
            print(f"drift: base {base_ref}, FAILED to list changed paths")
            failures.append(
                f"[drift] {exc} — refusing to pass with drift detection disabled"
            )
        else:
            print(f"drift: base {base_ref}, changed workflow paths: {paths or 'none'}")
            failures.extend(job_set_drift(paths, base_ref))

    if failures:
        for reason in failures:
            print(f"::error::{reason}" if _in_actions() else f"FAIL {reason}")
        print(f"\n{len(failures)} workflow guard failure(s).")
        return 1

    print("workflow guards: OK")
    return 0


def _in_actions() -> bool:
    import os

    return os.environ.get("GITHUB_ACTIONS") == "true"


if __name__ == "__main__":
    sys.exit(main())
