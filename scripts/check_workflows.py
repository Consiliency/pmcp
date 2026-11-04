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
import bisect
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
# verified archive into the runner's daemon, in build-smoke only. Each operand
# is reference characters only and ends at a blank or the end of the text, so
# anything glued on (; & | $( ` or a quote) leaves skopeo in the remainder.
SKOPEO_SMOKE_LOAD = re.compile(
    r"quay\.io/skopeo/stable@sha256:[0-9a-f]{64} (?:--insecure-policy )?"
    r"copy oci-archive:[A-Za-z0-9_./:@+-]+(?!\S) docker-daemon:[A-Za-z0-9_./:@+-]+(?!\S)"
)


class _ToolPush:
    """``\\b<tool>(?:\\s+-\\S*(?:\\s+VALUE)?)*\\s+(?:image\\s+)?push\\b``, where
    VALUE is ``"[^"\\n]*"``, ``'[^'\\n]*'`` or ``[^\\s"'-]\\S*``, decided in
    O(n log n) time.

    Global options before a container tool's subcommand: each an option word,
    optionally followed by one value word (docker --config DIR, -H URL,
    --tls). As one backtracking regex the search is quadratic: every tool
    word rescans the option chain after it, about 35 s for 160 KB of
    ``docker -x `` (PR Consiliency/pmcp#475 F3). This answers the same
    question with one right-to-left pass over the blank-separated runs:
    ``chain[r]`` says whether the options from run ``r`` on reach ``push``.
    The tests check it against the regex on generated texts.
    """

    _VALUE_QUOTES = "\"'"

    def __init__(self, tool: str, image: bool) -> None:
        self.tool = re.compile(rf"\b{tool}(?=\s)")
        self.image = image
        self.push = re.compile(r"push\b")

    def search(self, text: str) -> bool:
        tools = [match.end() for match in self.tool.finditer(text)]
        if not tools:
            return False
        runs = [(match.start(), match.end()) for match in re.finditer(r"\S+", text)]
        begins = [begin for begin, _ in runs]
        quotes = {
            char: [m.start() for m in re.finditer(re.escape(char), text)]
            for char in self._VALUE_QUOTES
        }
        newlines = [m.start() for m in re.finditer("\n", text)]
        count = len(runs)
        chain = [False] * (count + 1)
        for index in range(count - 1, -1, -1):
            begin, end = runs[index]
            if self._ends_in_push(text, runs, index):
                chain[index] = True
                continue
            if text[begin] != "-" or index + 1 >= count:
                continue
            after = self._after_value(text, runs, begins, quotes, newlines, index + 1)
            chain[index] = chain[index + 1] or (after is not None and chain[after])
        for end in tools:
            # The tool word ends at a blank, so the next run follows it.
            index = bisect.bisect_left(begins, end)
            if index < count and chain[index]:
                return True
        return False

    def _ends_in_push(self, text: str, runs: list[tuple[int, int]], index: int) -> bool:
        """``(?:image\\s+)?push\\b`` at run ``index``."""
        begin, end = runs[index]
        if self.push.match(text, begin):
            return True
        return (
            self.image
            and text[begin:end] == "image"
            and index + 1 < len(runs)
            and self.push.match(text, runs[index + 1][0]) is not None
        )

    def _after_value(
        self,
        text: str,
        runs: list[tuple[int, int]],
        begins: list[int],
        quotes: dict[str, list[int]],
        newlines: list[int],
        index: int,
    ) -> int | None:
        """The run after a VALUE starting at run ``index``, or None if none
        starts there. A VALUE must be followed by a blank."""
        begin, end = runs[index]
        char = text[begin]
        if char == "-":
            return None
        if char not in self._VALUE_QUOTES:
            return index + 1
        positions = quotes[char]
        spot = bisect.bisect_right(positions, begin)
        if spot >= len(positions):
            return None
        close = positions[spot]
        newline = bisect.bisect_right(newlines, begin)
        if newline < len(newlines) and newlines[newline] < close:
            return None
        last = bisect.bisect_right(begins, close) - 1
        if runs[last][1] != close + 1 or close + 1 >= len(text):
            return None
        return last + 1


class _LinePair:
    """``FIRST[^\\n]*SECOND``, decided in linear time: on each line, SECOND
    after the first FIRST (one backtracking regex rescans the line from every
    FIRST, quadratic on one long line). FIRST must match a fixed length."""

    def __init__(self, first: str, second: str, flags: int = 0) -> None:
        self.first = re.compile(first, flags)
        self.second = re.compile(second, flags)

    def search(self, text: str) -> bool:
        start = 0
        while start <= len(text):
            stop = text.find("\n", start)
            stop = len(text) if stop < 0 else stop
            first = self.first.search(text, start, stop)
            if first is not None and self.second.search(text, first.end(), stop):
                return True
            start = stop + 1
        return False


PUBLICATION_PATTERNS: dict[str, Any] = {
    "a buildx --push": re.compile(r"(?<![\w-])--push\b"),
    "a type=registry output": re.compile(r"type=registry"),
    "a pushing type=image output": _LinePair(r"type=image", r"push=true"),
    "docker push": _ToolPush("docker", image=True),
    "podman push": _ToolPush("podman", image=True),
    "buildah push": _ToolPush("buildah", image=False),
    "crane": re.compile(r"\bcrane\b"),
    "oras": re.compile(r"\boras\b"),
    "regctl": re.compile(r"\bregctl\b"),
    "docker login": re.compile(r"\bdocker\s+login\b"),
    "an image_publish.py write": re.compile(r"image_publish\.py\s+(?:promote|tag)\b"),
}
SKOPEO_DELETE = _LinePair(r"\bskopeo\b", r"\bdelete\b", re.IGNORECASE)
MUTATING_HTTP_LABEL = "a mutating curl/wget"
# The 5d416e4 detector's command regex, kept for _mutating_http_5d416e4.
_HTTP_COMMAND = re.compile(r"\b(curl|wget)\b([^\n;|&]*)")
_SAFE_METHODS = {"GET", "HEAD"}
_CURL_IMPLIED_LONG = ("--data", "--form", "--json", "--upload-file")
_WGET_IMPLIED_LONG = ("--post-data", "--post-file", "--body-data", "--body-file")
# --- guard (d), curl/wget: the text read as bash reads it -------------------
#
# Consiliency/pmcp#472 (president re-check R7 and R8 of PR
# Consiliency/pmcp#463). The lexer follows bash's own grammar (bash(1):
# SHELL GRAMMAR, QUOTING, EXPANSION, REDIRECTION, Here Documents), not a list
# of known spellings, and it has no path that answers "no write" for text it
# could not read:
# - a `#` that starts a word begins a comment to the end of the line;
# - here-document bodies are read line by line up to their delimiter, so a
#   quote inside one never opens a region past it;
# - $(...), $((...)), `...`, <(...), >(...) and ${...} end neither the word nor
#   the command; the commands inside substitutions are classified too;
# - a command ends at a newline, ; & && | || |& ;; ;& ;;& and at a ( or ) that
#   opens or closes a subshell or group, but the & of >& <& &> &>> belongs to
#   the redirection, and redirections are not arguments;
# - '...', "...", $'...', $"..." and \x are removed; the content of every
#   quoted part, every comment, every here-document body, every substitution
#   and every word holding a backslash-escaped blank is classified as commands
#   of its own (bash -c "...", ssh host "...", bash <<EOF);
# - there is no depth cap: every recursion is on strictly shorter text, and a
#   stack overflow counts as a write; text bash could not finish reading (an
#   unterminated quote or substitution) is also classified with every
#   curl/wget word, glued to anything, taking the rest of the text, quotes
#   removed, as its arguments.

# curl short options that take a value (curl(1)): the rest of the word after
# one of these is that value. Every other character of a short group,
# including `#`, `:` and digits, is an option letter of its own.
_CURL_VALUE_SHORT = set("AbcCdDeEFHKmoPQrtTuUwxXyYz")
# curl long options that send a method: --request names one, the others imply
# one whatever follows (a longer name, such as --data-binary, too).
_CURL_LONG_WRITES = (
    ("data", "implied"),
    ("form", "implied"),
    ("json", "implied"),
    ("upload-file", "implied"),
    ("request", "method"),
)
# curl 8.3.0+ reads --expand-<option> as <option> with variables expanded.
_CURL_EXPAND = "expand-"
_CURL_LONG_NAMES = _CURL_LONG_WRITES + tuple(
    (_CURL_EXPAND + name, kind) for name, kind in _CURL_LONG_WRITES
)
# wget long options that send a method; -e/--execute runs a wgetrc command.
_WGET_LONG_WRITES = (
    ("post-data", "implied"),
    ("post-file", "implied"),
    ("body-data", "implied"),
    ("body-file", "implied"),
    ("method", "method"),
    ("execute", "execute"),
)
# wget short options that take a value (wget(1)).
_WGET_VALUE_SHORT = set("aABDeiIlnoOPQRtTUwX")
# wgetrc commands that send a method; wgetrc ignores case, `-` and `_`.
_WGETRC_WRITES = {"postdata", "postfile", "bodydata", "bodyfile"}
# The command word, by basename (R8a).
_HTTP_TOOLS = {"curl": "curl", "curl.exe": "curl", "wget": "wget", "wget.exe": "wget"}
# curl or wget as a word glued to anything but a name character: inside an
# expansion that builds a command word, and in text that cannot be lexed. A
# `-` before it is glue, not part of the name: it ends the parameter
# expansion operators `-` and `:-` (`${X:-curl}`, PR Consiliency/pmcp#475 F1).
_TOOL_WORD = re.compile(
    r"(?<![A-Za-z0-9_.])(curl|wget)(?:\.exe)?(?![A-Za-z0-9_.-])", re.IGNORECASE
)
# A tool name in a word's shape (see _MARK), matched at every position.
_TOOL_NAME = re.compile(r"(?=(curl|wget))", re.IGNORECASE)
# Stands for an expansion in a word's shape: a value the guard cannot know,
# possibly empty, possibly split into several words (PR
# Consiliency/pmcp#475 F2). YAML text cannot hold a NUL, and reading one as an
# expansion only flags more.
_MARK = "\0"
# Simple expansions the R7f fallback splits a word at, as $IFS may.
_FALLBACK_EXPANSION = re.compile(r"\$\{?(?:[A-Za-z_][A-Za-z0-9_]*|[0-9@*#?$!-])\}?")
# bash's redirection operators, longest first.
_REDIRECTIONS = (
    "&>>",
    "&>",
    "<<<",
    "<<-",
    "<<",
    "<&",
    "<>",
    ">>",
    ">&",
    ">|",
    "<",
    ">",
)
# bash's control operators, longest first.
_CONTROL_OPERATORS = (";;&", ";;", ";&", "||", "|&", "&&", ";", "&", "|")
# Characters with no special meaning to the lexer once a word has started.
_ORDINARY_RUN = re.compile(r"[^ \t\n'\"\\$`<>&;|()]+")
_REDIRECTED_FD = re.compile(r"[0-9]+|\{[A-Za-z_][A-Za-z0-9_]*\}")
_SIMPLE_PARAMETER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|[0-9@*#?$!-]")
# The parameter name and operator that open ${...}; the rest is a word.
_PARAMETER_NAME = re.compile(r"[#!]?(?:[A-Za-z_][A-Za-z0-9_]*|[0-9]+|[@*#?$!-])")
_PARAMETER_OPERATOR = re.compile(r":?[-=+?]|##?|%%?|/[#%]|//?|\^\^?|,,?|@")
_VALUE_OPERATORS = {":-", "-", ":=", "=", ":+", "+", "/", "//", "/#", "/%"}
_ANSI_C_ESCAPES = {
    "a": "\a",
    "b": "\b",
    "e": "\x1b",
    "E": "\x1b",
    "f": "\f",
    "n": "\n",
    "r": "\r",
    "t": "\t",
    "v": "\v",
    "\\": "\\",
    "'": "'",
    '"': '"',
    "?": "?",
}
_ANSI_C_NUMERIC = re.compile(
    r"[0-7]{1,3}|x[0-9A-Fa-f]{1,2}|u[0-9A-Fa-f]{1,4}|U[0-9A-Fa-f]{1,8}|c."
)


def _ansi_c(body: str) -> str:
    """The value of ``$'body'`` (bash(1), QUOTING)."""
    out: list[str] = []
    index = 0
    while index < len(body):
        char = body[index]
        escape = body[index + 1 : index + 2]
        if char != "\\" or not escape:
            out.append(char)
            index += 1
            continue
        if escape in _ANSI_C_ESCAPES:
            out.append(_ANSI_C_ESCAPES[escape])
            index += 2
            continue
        number = _ANSI_C_NUMERIC.match(body, index + 1)
        if number is None:
            out.append(char)
            index += 1
            continue
        token = number[0]
        if token[0] == "c":
            decoded = chr(ord(token[1]) & 0x1F)
        elif token[0] in "xuU":
            decoded = chr(min(int(token[1:], 16), 0x10FFFF))
        else:
            decoded = chr(int(token, 8) & 0xFF)
        # Bash terminates this ANSI-C quoted segment at a decoded NUL.
        # Text after its closing quote still concatenates in the lexer.
        if decoded == "\0":
            break
        out.append(decoded)
        index += 1 + len(token)
    return "".join(out)


class _Unlexable(Exception):
    """Text bash could not finish reading: an unterminated quote or expansion."""


# The most characters the classifier may lex or scan for one text, counting
# every nested text, rescan and lookahead (2026-11-03 dev0 incident: the guard's
# own work must be bounded). Reaching it counts as a write: no bound may fail
# open (R7f). Committed steps must stay below 10% in the regression test.
_WORK_LIMIT = 250_000
_TOKEN_WORK = 16  # Fixed allocation/dispatch work per completed lexical word.


class _OverBudget(Exception):
    """The classifier reached ``_WORK_LIMIT``."""


class _UnmodelledExpansion(Exception):
    """An expansion generates shell quoting syntax; refuse conservatively."""


class _Work:
    """The work spent on one classified text, and the texts already classified."""

    def __init__(self) -> None:
        self.spent = 0
        self.seen: set[str] = set()

    def spend(self, amount: int) -> None:
        self.spent += amount
        if self.spent > _WORK_LIMIT:
            raise _OverBudget


class _Word:
    """One shell word: its text after quote removal, expansions kept as written."""

    def __init__(self, text: str = "", expanded: bool = False) -> None:
        # Pieces joined on demand: appending to one string would copy it
        # every time, quadratic in the length of a long word.
        self._pieces = [text] if text else []
        self._joined: str | None = text
        self.started = bool(text)
        self.quoted = False
        self.expanded = expanded
        self.escaped_blank = False
        self.brace_candidate = False
        self.expansions: list[str] = []
        # The word as bash may build it: literal text, with each expansion
        # replaced by _MARK, or for ${X:-word} by _MARK, the shape of `word`
        # (its blanks as _MARK, since an unquoted value is split) and _MARK.
        self._shape_pieces = [text] if text else []
        self._shape: str | None = text

    @property
    def text(self) -> str:
        if self._joined is None:
            self._joined = "".join(self._pieces)
            self._pieces = [self._joined]
        return self._joined

    @property
    def shape(self) -> str:
        if self._shape is None:
            self._shape = "".join(self._shape_pieces)
            self._shape_pieces = [self._shape]
        return self._shape

    def add(self, piece: str, shape: str | None = None) -> None:
        self._pieces.append(piece)
        self._joined = None
        self._shape_pieces.append(piece if shape is None else shape)
        self._shape = None
        self.started = True


def _compound_prefix(words: list[_Word]) -> bool:
    """Recognize command-prefix productions before a compound command.

    Time options, optional coprocess names and function names are operands of
    their prefix, rather than arbitrary words allowed everywhere.
    """
    index = 0
    while index < len(words):
        word = words[index]
        if word.quoted:
            return False
        name = word.text
        index += 1
        if name in {"if", "then", "while", "until", "do", "else", "elif", "!", "{"}:
            continue
        if name == "time":
            while index < len(words) and words[index].text in {"-p", "--"}:
                index += 1
            continue
        if name == "function":
            if index >= len(words):
                return False
            index += 1  # Bash function names are words, not just identifiers.
            continue
        if name == "coproc":
            if index < len(words) and re.fullmatch(
                r"[A-Za-z_][A-Za-z0-9_]*", words[index].text
            ):
                index += 1
            continue
        return False
    return True


class _ShellLexer:
    """Simple commands of a bash text, plus the texts bash may run as commands."""

    def __init__(self, text: str, work: _Work | None = None) -> None:
        self.s = text
        self.n = len(text)
        self.has_braces = "{" in text
        self.work = _Work() if work is None else work
        self.commands: list[list[_Word]] = []
        # Text that may itself run as commands: quoted parts, comments,
        # here-document bodies, substitutions, words with an escaped blank.
        self.nested: list[str] = []
        # Here-document bodies whose expansions could not be lexed.
        self.unlexed: list[str] = []
        # Brace expansion may make a command longer. These readings share
        # the work budget but are not restricted to shorter nested texts.
        self.braced: list[str] = []
        # Here-documents waiting for the next newline: delimiter, <<-, expands.
        self.heredocs: list[tuple[str, bool, bool]] = []

    def run(self) -> bool:
        """Lex the whole text; False if bash could not finish reading it."""
        try:
            self.parse(0, closer=False)
        except _Unlexable:
            return False
        return True

    def parse(self, pos: int, closer: bool) -> int:
        """A command list from ``pos``; with ``closer``, up to its matching ``)``."""
        s, n = self.s, self.n
        command: list[_Word] = []
        word = _Word()
        redirect: str | None = None
        depth = 0
        # One [state, depth] per open case: "head", "pattern" or "body".
        cases: list[list[Any]] = []
        raw_words: list[tuple[int, int, bool]] = []
        raw_start = pos

        def finish_word() -> None:
            nonlocal word, command, redirect
            if not word.started:
                return
            self.work.spend(_TOKEN_WORK)
            current, word = word, _Word()
            if current.escaped_blank:
                self.nested.append(current.text)
            if redirect is not None:
                if redirect in ("<<", "<<-"):
                    self.heredocs.append(
                        (current.text, redirect == "<<-", not current.quoted)
                    )
                redirect = None
                return
            reserved = None if current.quoted else current.text
            if reserved == "case":
                self.work.spend(len(command))
                if _compound_prefix(command):
                    cases.append(["head", depth])
            elif reserved == "esac" and not command and cases:
                cases.pop()
            command.append(current)
            if self.has_braces:
                # Nested command substitutions can copy overlapping source
                # spans. Charge before slicing, not after building all words.
                raw_words.append((raw_start, pos, current.brace_candidate))
            if reserved == "in" and cases and cases[-1] == ["head", depth]:
                cases[-1][0] = "pattern"
                self.commands.append(command)
                command = []
                raw_words.clear()

        def finish_command() -> None:
            nonlocal command, redirect
            finish_word()
            redirect = None
            if command:
                self.commands.append(command)
                # Most braces are quoted data or parameter expansions. Keep
                # source spans until an unquoted brace can expand; only then
                # copy the command, charging all source copies first.
                if any(candidate for _, _, candidate in raw_words):
                    self.work.spend(sum(end - start for start, end, _ in raw_words))
                    sources = [s[start:end] for start, end, _ in raw_words]
                    expanded = [
                        _brace_expand(raw, self.work) if candidate else [raw]
                        for raw, (_, _, candidate) in zip(sources, raw_words)
                    ]
                else:
                    sources, expanded = [], []
                if any(parts != [raw] for raw, parts in zip(sources, expanded)):
                    self.work.spend(
                        sum(len(part) + 1 for parts in expanded for part in parts)
                    )
                    self.braced.append(
                        " ".join(part for parts in expanded for part in parts)
                    )
            command = []
            raw_words.clear()

        while pos < n:
            if not word.started:
                raw_start = pos
            char = s[pos]
            if char == "\n":
                finish_command()
                pos = self.read_heredocs(pos + 1)
                continue
            if char in " \t":
                finish_word()
                pos += 1
                continue
            if char == "#" and not word.started:
                end = s.find("\n", pos)
                end = n if end < 0 else end
                self.nested.append(s[pos + 1 : end])
                pos = end
                continue
            if char == "\\":
                if pos + 1 >= n:
                    word.add(char)
                    pos += 1
                    continue
                escaped = s[pos + 1]
                pos += 2
                if escaped == "\n":
                    continue
                word.add(escaped)
                word.quoted = True
                if escaped in " \t":
                    word.escaped_blank = True
                continue
            if char == "'":
                close = s.find("'", pos + 1)
                if close < 0:
                    raise _Unlexable
                self.quoted_part(word, s[pos + 1 : close])
                pos = close + 1
                continue
            if char == '"':
                pos = self.double_quoted(pos + 1, word)
                continue
            if char == "$":
                pos = self.dollar(pos, word, quoted=False)
                continue
            if char == "`":
                pos = self.backtick(pos, word)
                continue
            if char in "<>" and s.startswith("(", pos + 1):
                end = self.parse(pos + 2, closer=True)
                pos = self.expansion(word, pos, end)
                continue
            op = (
                next((o for o in _REDIRECTIONS if s.startswith(o, pos)), None)
                if char in "<>&"
                else None
            )
            if op is not None:
                if (
                    word.started
                    and not word.quoted
                    and not word.expanded
                    and _REDIRECTED_FD.fullmatch(word.text)
                ):
                    word = _Word()
                else:
                    finish_word()
                redirect = op
                pos += len(op)
                continue
            op = (
                next((o for o in _CONTROL_OPERATORS if s.startswith(o, pos)), None)
                if char in ";&|"
                else None
            )
            if op is not None:
                finish_command()
                if op in (";;", ";&", ";;&") and cases and cases[-1][1] == depth:
                    cases[-1][0] = "pattern"
                pos += len(op)
                continue
            if char == "(":
                self.work.spend(len(command))
                at_start = not word.started and (
                    _compound_prefix(command)
                    or (
                        bool(command)
                        and command[-1].text == "for"
                        and not command[-1].quoted
                        and _compound_prefix(command[:-1])
                    )
                )
                finish_command()
                if at_start and s.startswith("((", pos):
                    arithmetic = self.arithmetic_end(pos + 2)
                    if arithmetic is not None:
                        self.nested.append(s[pos + 2 : arithmetic - 2])
                        pos = arithmetic
                        continue
                if at_start and cases and cases[-1] == ["pattern", depth]:
                    pos += 1
                    continue
                depth += 1
                pos += 1
                continue
            if char == ")":
                finish_command()
                pos += 1
                if cases and cases[-1] == ["pattern", depth]:
                    cases[-1][0] = "body"
                elif depth:
                    depth -= 1
                elif closer:
                    return pos
                continue
            # A run of ordinary characters, added at once.
            run = _ORDINARY_RUN.match(s, pos)
            end = run.end() if run else pos + 1
            ordinary = s[pos:end]
            word.brace_candidate |= "{" in ordinary
            word.add(ordinary)
            pos = end
        if closer:
            raise _Unlexable
        finish_command()
        return pos

    def quoted_part(self, word: _Word, inner: str, shape: str | None = None) -> None:
        word.add(inner, shape)
        word.quoted = True
        self.nested.append(inner)

    def expansion(self, word: _Word, start: int, end: int, shape: str = _MARK) -> int:
        self.work.spend(end - start)
        raw = self.s[start:end]
        word.add(raw, shape)
        word.expanded = True
        word.expansions.append(raw)
        return end

    def double_quoted(self, pos: int, word: _Word) -> int:
        """``"..."`` from just after its opening quote."""
        s, inner = self.s, _Word()
        start = pos
        while True:
            if pos >= self.n:
                raise _Unlexable
            char = s[pos]
            if char == '"':
                break
            if char == "\\" and s[pos + 1 : pos + 2] in ("$", "`", '"', "\\", "\n"):
                if s[pos + 1] != "\n":
                    inner.add(s[pos + 1])
                pos += 2
                continue
            if char == "$":
                pos = self.dollar(pos, inner, quoted=True)
                continue
            if char == "`":
                pos = self.backtick(pos, inner)
                continue
            inner.add(char)
            pos += 1
        word.expanded = word.expanded or inner.expanded
        word.expansions += inner.expansions
        self.work.spend(pos - start)
        self.quoted_part(word, inner.text, inner.shape)
        return pos + 1

    def dollar(self, pos: int, word: _Word, quoted: bool) -> int:
        """A ``$`` at ``pos``: an expansion, ``$'...'``, ``$"..."`` or itself."""
        s = self.s
        following = s[pos + 1 : pos + 2]
        if following == "(":
            if s.startswith("((", pos + 1):
                end = self.arithmetic_end(pos + 3)
                if end is not None:
                    self.nested.append(s[pos + 3 : end - 2])
                    return self.expansion(word, pos, end)
            end = self.parse(pos + 2, closer=True)
            return self.expansion(word, pos, end)
        if following == "{":
            # The word of ${X:-word} and the like may become the value.
            head_end, operator = self.parameter_head(pos + 2)
            operand = head_end if operator in _VALUE_OPERATORS else None
            value = _Word()
            substitution = operator.startswith("/")
            operand_quoted = quoted and not substitution
            end = self.parameter(pos + 2, operand_quoted, value, operand, substitution)
            self.work.spend(2 * (end - pos))
            self.nested.append(s[head_end : end - 1])
            shape = _MARK
            if operand is not None and operand < end:
                self.work.spend(len(value.shape))
                shape += re.sub(r"[ \t\n]+", _MARK, value.shape) + _MARK
            return self.expansion(word, pos, end, shape)
        if following == "'" and not quoted:
            close = pos + 2
            while close < self.n and s[close] != "'":
                close += 2 if s[close] == "\\" else 1
            if close >= self.n:
                raise _Unlexable
            self.quoted_part(word, _ansi_c(s[pos + 2 : close]))
            return close + 1
        if following == '"' and not quoted:
            return self.double_quoted(pos + 2, word)
        name = _SIMPLE_PARAMETER.match(s, pos + 1)
        if name is not None:
            return self.expansion(word, pos, name.end())
        word.add("$")
        return pos + 1

    def parameter_head(self, pos: int) -> tuple[int, str]:
        """Read a name, balanced array subscript, then its outer operator."""
        start = pos
        name = _PARAMETER_NAME.match(self.s, pos)
        if name is None:
            return pos, ""
        pos = name.end()
        if self.s[pos : pos + 1] == "[":
            depth, scratch = 1, _Word()
            pos += 1
            while pos < self.n and depth:
                char = self.s[pos]
                if char == "\\":
                    pos += 2
                elif char == "$":
                    pos = self.dollar(pos, scratch, quoted=False)
                elif char == "`":
                    pos = self.backtick(pos, scratch)
                elif char == '"':
                    pos = self.double_quoted(pos + 1, scratch)
                elif char == "'":
                    close = self.s.find("'", pos + 1)
                    if close < 0:
                        raise _UnmodelledExpansion
                    pos = close + 1
                else:
                    depth += (char == "[") - (char == "]")
                    pos += 1
            if depth:
                raise _UnmodelledExpansion
        self.work.spend(pos - start)
        operator = _PARAMETER_OPERATOR.match(self.s, pos)
        return (operator.end(), operator[0]) if operator else (pos, "")

    def parameter(
        self,
        pos: int,
        quoted: bool,
        value: _Word,
        operand: int | None,
        substitution: bool = False,
    ) -> int:
        """The end of ``${...}``, from just after its ``{``; from ``operand``
        on, the text is also built into ``value``."""
        s, scratch = self.s, _Word()
        replacement = False
        while pos < self.n:
            char = s[pos]
            if char == "}":
                return pos + 1
            into = value if operand is not None and pos >= operand else scratch
            if char == "/" and substitution and into is value and not replacement:
                # Keep the pattern reading conservatively, but give the
                # replacement its own boundary: it can be an entire option.
                into.add(char, _MARK)
                replacement = True
                pos += 1
            elif char == "\\":
                into.add(s[pos + 1 : pos + 2])
                pos += 2
            elif char == "'" and not quoted:
                close = s.find("'", pos + 1)
                if close < 0:
                    raise _Unlexable
                self.nested.append(s[pos + 1 : close])
                into.add(s[pos + 1 : close])
                pos = close + 1
            elif char == '"':
                pos = self.double_quoted(pos + 1, into)
            elif char == "$":
                # bash's default extquote decodes ANSI-C and locale quotes
                # in parameter operands even inside an outer double quote.
                # Nested parameters still inherit the actual quoting context.
                extended_quote = s[pos + 1 : pos + 2] in ("'", '"')
                pos = self.dollar(pos, into, quoted=quoted and not extended_quote)
            elif char == "`":
                pos = self.backtick(pos, into)
            else:
                into.add(char)
                pos += 1
        raise _Unlexable

    def backtick(self, pos: int, word: _Word) -> int:
        """```...``` at ``pos``: its content runs as commands."""
        s, inner, index = self.s, [], pos + 1
        while True:
            if index >= self.n:
                raise _Unlexable
            char = s[index]
            if char == "`":
                break
            if char == "\\" and index + 1 < self.n:
                escaped = s[index + 1]
                inner.append(escaped if escaped in "$`\\" else char + escaped)
                index += 2
                continue
            inner.append(char)
            index += 1
        self.nested.append("".join(inner))
        return self.expansion(word, pos, index + 1)

    def arithmetic_end(self, pos: int) -> int | None:
        """The end of ``((...))`` from just after ``((``, or None if it is not one."""
        end = self._arithmetic_end(pos)
        # A lookahead that finds no `))` is repeated for every `$((`.
        self.work.spend((self.n if end is None else end) - pos)
        return end

    def _arithmetic_end(self, pos: int) -> int | None:
        s, depth, scratch = self.s, 0, _Word()
        while pos < self.n:
            char = s[pos]
            if char == "\\":
                pos += 2
                continue
            if char == "$":
                pos = self.dollar(pos, scratch, quoted=False)
                continue
            if char == "`":
                pos = self.backtick(pos, scratch)
                continue
            if char == '"':
                pos = self.double_quoted(pos + 1, scratch)
                continue
            if char == "'":
                close = s.find("'", pos + 1)
                if close < 0:
                    raise _Unlexable
                pos = close + 1
                continue
            if char == "(":
                depth += 1
            elif char == ")":
                if depth == 0:
                    return pos + 2 if s.startswith("))", pos) else None
                depth -= 1
            pos += 1
        return None

    def read_heredocs(self, pos: int) -> int:
        """The bodies of the pending here-documents, from the line at ``pos``."""
        s = self.s
        pending, self.heredocs = self.heredocs, []
        for delimiter, strip_tabs, expands in pending:
            body: list[str] = []
            while pos < self.n:
                end = s.find("\n", pos)
                end = self.n if end < 0 else end
                line = s[pos:end]
                pos = min(end + 1, self.n)
                if (line.lstrip("\t") if strip_tabs else line) == delimiter:
                    break
                body.append(line + "\n")
            text = "".join(body)
            # The body may be a script (bash <<EOF): classify it as commands.
            self.nested.append(text)
            if expands:
                self.heredoc_expansions(text)
        return pos

    def heredoc_expansions(self, body: str) -> None:
        """The substitutions bash runs in a body whose delimiter is unquoted."""
        sub = _ShellLexer(body, self.work)
        scratch = _Word()
        pos = 0
        try:
            while pos < sub.n:
                char = body[pos]
                if char == "\\":
                    pos += 2
                elif char == "$":
                    pos = sub.dollar(pos, scratch, quoted=True)
                elif char == "`":
                    pos = sub.backtick(pos, scratch)
                else:
                    pos += 1
        except _Unlexable:
            self.unlexed.append(body)
        self.commands += sub.commands
        self.nested += sub.nested
        self.unlexed += sub.unlexed


def _method_writes(method: str, expanded: bool) -> bool:
    """A method other than GET or HEAD in any letter case (R8e).

    An expansion anywhere in the word counts as a write; an empty or missing
    word does not (``curl -X ""`` exits 2 without sending a request).
    """
    if not method:
        return False
    return expanded or method.upper() not in _SAFE_METHODS


def _option_value(
    word: _Word, eq: str, attached: str, following: _Word | None
) -> tuple[str, bool]:
    """A long option's value: attached after ``=``, or the next word."""
    if eq:
        return attached, word.expanded
    if following is None:
        return "", False
    return following.text, following.expanded


def _long_option(
    name: str, table: tuple[tuple[str, str], ...], abbreviated: bool
) -> str | None:
    """The kind of the long option ``--name`` in ``table``, or None.

    A longer spelling of an implied-write name counts (``--data-binary``), and
    with ``abbreviated`` so does any prefix of a listed name (GNU getopt_long
    accepts an unambiguous one; an ambiguous one is flagged too).
    """
    if not name:
        return None
    for full, kind in table:
        if name == full or (kind == "implied" and name.startswith(full)):
            return kind
    if abbreviated:
        for full, kind in table:
            if full.startswith(name):
                return kind
    return None


def _curl_writes(args: list[_Word]) -> bool:
    for index, word in enumerate(args):
        text = word.text
        following = args[index + 1] if index + 1 < len(args) else None
        if text.startswith("--"):
            name, eq, attached = text[2:].partition("=")
            kind = _long_option(name, _CURL_LONG_NAMES, abbreviated=True)
            if kind == "implied":
                return True
            if kind == "method" and _method_writes(
                *_option_value(word, eq, attached, following)
            ):
                return True
        elif text.startswith("-") and len(text) > 1:
            for position, letter in enumerate(text[1:], start=1):
                if letter in "dFT":
                    return True
                if letter == "X":
                    rest = text[position + 1 :]
                    method = (
                        (rest, word.expanded)
                        if rest
                        else _option_value(word, "", "", following)
                    )
                    if _method_writes(*method):
                        return True
                    break
                if letter in _CURL_VALUE_SHORT:
                    break
    return False


def _wgetrc_writes(command: str, expanded: bool) -> bool:
    """A wgetrc command (``wget -e``) that sends a method."""
    name, _, value = command.partition("=")
    key = re.sub(r"[-_\s]", "", name).lower()
    if key in _WGETRC_WRITES or (expanded and re.search(r"[$`]", name)):
        return True
    return key == "method" and _method_writes(value.strip(), expanded)


def _wget_writes(args: list[_Word]) -> bool:
    for index, word in enumerate(args):
        text = word.text
        following = args[index + 1] if index + 1 < len(args) else None
        if text.startswith("--"):
            name, eq, attached = text[2:].partition("=")
            kind = _long_option(name, _WGET_LONG_WRITES, abbreviated=True)
            value = _option_value(word, eq, attached, following)
            if kind == "implied":
                return True
            if kind == "method" and _method_writes(*value):
                return True
            if kind == "execute" and _wgetrc_writes(*value):
                return True
        elif text.startswith("-") and len(text) > 1:
            for spot, letter in enumerate(text[1:], start=1):
                if letter == "e":
                    rest = text[spot + 1 :]
                    command = (
                        (rest, word.expanded)
                        if rest
                        else _option_value(word, "", "", following)
                    )
                    if _wgetrc_writes(*command):
                        return True
                    break
                if letter in _WGET_VALUE_SHORT:
                    break
    return False


def _shape_tools(shape: str, work: _Work) -> list[tuple[str, list[_Word]]]:
    """Each curl/wget a word with expansions may become, with the words that
    follow it inside the same word (PR Consiliency/pmcp#475 F1, F2).

    Every expansion in ``shape`` (``_MARK``) may be empty and may split the
    word, so a tool name counts where it starts at the word's start, after a
    ``/`` or ``\\`` or an expansion, and ends at the word's end or an
    expansion (``${X}curl``, ``$X""curl``, ``${X:-cu}rl``,
    ``${X:-/usr/bin/curl}``). What follows it up to the end of the word, split
    at each expansion, is read as its first arguments (``curl$IFS-d@f``),
    each counted as holding an expansion.
    """
    segments = shape.split(_MARK)
    stripped = "".join(segments)
    starts: list[int] = []
    offset = 0
    for segment in segments:
        starts.append(offset)
        offset += len(segment)
    cuts = set(starts[1:])
    found: list[tuple[str, list[_Word]]] = []
    for match in _TOOL_NAME.finditer(stripped):
        begin, tool = match.start(), match[1].lower()
        if not (begin == 0 or begin in cuts or stripped[begin - 1] in "/\\"):
            continue
        end = begin + len(tool)
        if stripped[end : end + 4].lower() == ".exe" and (
            end + 4 == len(stripped) or end + 4 in cuts
        ):
            end += 4
        elif not (end == len(stripped) or end in cuts):
            continue
        work.spend(len(stripped) - end + 1)
        # The segment holding `end`, cut there, and every later segment.
        index = bisect.bisect_right(starts, end) - 1
        rest = [segments[index][end - starts[index] :], *segments[index + 1 :]]
        found.append((tool, [_Word(part, expanded=True) for part in rest if part]))
        # Preserve boundaries for the option reader as well as the historical
        # all-split reading: an empty expansion can join '-' and 'd'.
        work.spend(sum(len(part) + 1 for part in rest))
        remainder = _Word(_MARK.join(rest), expanded=True)
        found.append((tool, [remainder]))
    return found


def _http_tools(word: _Word, work: _Work) -> list[tuple[str, list[_Word]]]:
    """``curl``/``wget`` if the word names one: by basename (``/usr/bin/curl``,
    ``curl.exe``), inside an expansion (``$(command -v curl)``,
    ``${X:-curl}``), or glued to an expansion (``${X}curl``); each with the
    arguments it takes from the rest of the word."""
    text = word.text
    work.spend(len(text) + sum(len(raw) for raw in word.expansions))
    base = re.split(r"[/\\]", text)[-1].lower()
    tools = {_HTTP_TOOLS[base]} if base in _HTTP_TOOLS else set()
    for raw in word.expansions:
        tools.update(match[1].lower() for match in _TOOL_WORD.finditer(raw))
    found: list[tuple[str, list[_Word]]] = [(tool, []) for tool in sorted(tools)]
    if word.expanded:
        work.spend(len(word.shape))
        found += _shape_tools(word.shape, work)
    return found


def _argument_shapes(word: _Word, work: _Work) -> list[list[_Word]]:
    """Read an expanded option with each unknown piece empty or splitting.

    Retain all join/split combinations within a word, not just the two
    extremes: an option name can join across one expansion while its method
    splits at another. Charge each candidate before copying/allocating it.
    """
    if not word.expanded:
        return []
    work.spend(len(word.shape))
    if "-" not in word.shape:
        return []  # No statically present option prefix in this word.
    parts = [part for part in word.shape.split(_MARK) if part]
    # An option must start at a segment boundary with a literal dash. Prefix
    # segments cannot change that: joining them only hides an option, and
    # splitting them leaves exactly the same suffix for the option reader.
    first = next((i for i, part in enumerate(parts) if part.startswith("-")), None)
    if first is None:
        return []  # A completely unknown runtime value remains AC1.
    parts = parts[first:]
    readings: set[tuple[str, ...]] = {(parts[0],)}
    for part in parts[1:]:
        following: set[tuple[str, ...]] = set()
        for reading in readings:
            work.spend(2 * (sum(len(piece) + 1 for piece in reading) + len(part)))
            following.add((*reading[:-1], reading[-1] + part))
            following.add((*reading, part))
        readings = following
    return [[_Word(part, expanded=True) for part in reading] for reading in readings]


def _command_writes(words: list[_Word], work: _Work) -> bool:
    """A simple command with curl or wget anywhere in it (``timeout 5 curl``,
    ``env A=1 curl``) whose arguments send a method."""
    for index, word in enumerate(words):
        for tool, inside in _http_tools(word, work):
            args = inside + words[index + 1 :]
            # The option scans read every following word, letter by letter.
            work.spend(sum(len(w.text) + 1 for w in args))
            if tool == "curl" and _curl_writes(args):
                return True
            if tool == "wget" and _wget_writes(args):
                return True
            for arg_index, arg in enumerate(args):
                for reading in _argument_shapes(arg, work):
                    # Readers inspect each option independently and at most
                    # its following word; avoid a cross-product across argv.
                    following = args[arg_index + 1 : arg_index + 2]
                    work.spend(sum(len(w.text) + 1 for w in reading + following))
                    if tool == "curl" and _curl_writes(reading + following):
                        return True
                    if tool == "wget" and _wget_writes(reading + following):
                        return True
    return False


def _unlexed_writes(text: str, work: _Work) -> bool:
    """R7f: every curl/wget word glued to anything, with the rest of the text,
    quotes and backslashes removed, as its arguments."""
    for match in _TOOL_WORD.finditer(text):
        work.spend(len(text) - match.end())
        rest = re.sub(r"[\\'\"]", "", text[match.end() :]).split()
        args = [
            _Word(token, expanded=bool(re.search(r"[$`]", token))) for token in rest
        ]
        if _command_writes([_Word(match[1].lower()), *args], work):
            return True
        # Also split at simple expansions, as an unquoted $IFS may split
        # (curl$IFS-d@f, PR Consiliency/pmcp#475 F2); the unsplit reading
        # above is kept, so this only adds writes.
        if not any(word.expanded for word in args):
            continue
        work.spend(len(text) - match.end())
        shapes = [
            _Word(_FALLBACK_EXPANSION.sub(_MARK, token), expanded=True)
            for token in rest
        ]
        if _command_writes([_Word(match[1].lower()), *shapes], work):
            return True
        split = [
            _Word(part, expanded=True)
            for token in rest
            for part in _FALLBACK_EXPANSION.split(token)
            if part
        ]
        if _command_writes([_Word(match[1].lower()), *split], work):
            return True
    return False


_BRACE_SEQUENCE = re.compile(
    r"([+-]?[0-9]+|[a-zA-Z])\.\.([+-]?[0-9]+|[a-zA-Z])(?:\.\.([+-]?[0-9]+))?"
)


def _brace_expand(raw: str, work: _Work) -> list[str]:
    """Expand a shell word's unquoted brace lists/sequences before lexing it.

    Quoting and parameter operands are skipped as units: braces produced by
    parameter expansion do not undergo brace expansion. Every scan and every
    output allocation is charged, including Cartesian products and ranges.
    The original command is classified separately, so this only adds refusals.
    """
    if "{" not in raw:
        return [raw]
    work.spend(len(raw))
    lexer = _ShellLexer(raw, work)
    stack: list[tuple[int, list[int]]] = []
    sites: list[tuple[int, int, list[int]]] = []
    pos = 0
    while pos < len(raw):
        char = raw[pos]
        if char == "\\":
            pos += 2
            continue
        if char in "<>" and raw.startswith("(", pos + 1):
            try:
                pos = lexer.parse(pos + 2, closer=True)
            except _Unlexable:
                return [raw]
            continue
        if char == "'":
            close = raw.find("'", pos + 1)
            if close < 0:
                return [raw]
            pos = close + 1
            continue
        if char in ('"', "$", "`"):
            scratch = _Word()
            try:
                if char == '"':
                    pos = lexer.double_quoted(pos + 1, scratch)
                elif char == "$":
                    pos = lexer.dollar(pos, scratch, quoted=False)
                else:
                    pos = lexer.backtick(pos, scratch)
            except _Unlexable:
                return [raw]
            continue
        if char == "{":
            stack.append((pos, []))
        elif char == "," and stack:
            stack[-1][1].append(pos)
        elif char == "}" and stack:
            start, commas = stack.pop()
            sites.append((start, pos, commas))
        pos += 1
    # Outer lists must expand before their children, preserving bash's order.
    for start, end, commas in sorted(sites):
        work.spend(end - start)
        body = raw[start + 1 : end]
        choices: list[str]
        if commas:
            boundaries = [start, *commas, end]
            choices = [raw[a + 1 : b] for a, b in zip(boundaries, boundaries[1:])]
        else:
            sequence = _BRACE_SEQUENCE.fullmatch(body)
            if sequence is None:
                continue
            first, last, increment = sequence.groups()
            numeric = first.lstrip("+-").isdigit() and last.lstrip("+-").isdigit()
            if not numeric and not (first.isalpha() and last.isalpha()):
                continue
            # Refuse oversized integers before converting or allocating a range.
            if any(len(value) > 10 for value in (first, last, increment or "")):
                raise _OverBudget
            left, right = (
                (int(first), int(last)) if numeric else (ord(first), ord(last))
            )
            stride = abs(int(increment or "1")) or 1
            count = abs(right - left) // stride + 1
            work.spend(count * (len(raw) + 1))
            step = stride if left <= right else -stride
            values = range(left, right + (1 if step > 0 else -1), step)
            padded = numeric and any(
                value.lstrip("-").startswith("0") for value in (first, last)
            )
            width = max(len(first), len(last)) if padded else 0
            # bash treats generated backslashes/backticks differently from
            # reparsed source (including quote removal and substitution).
            # Do not approximate that boundary: refuse these rare cross-case
            # alphabetic ranges, even in an otherwise read-only command.
            if not numeric and any(value in values for value in (92, 96)):
                raise _UnmodelledExpansion
            choices = [
                str(value).zfill(width) if numeric else chr(value) for value in values
            ]
        result: list[str] = []
        for choice in choices:
            work.spend(start + len(choice) + len(raw) - end)
            replacement = raw[:start] + choice + raw[end + 1 :]
            result.extend(_brace_expand(replacement, work))
        return result
    return [raw]


def _classify(text: str, work: _Work) -> bool:
    if text in work.seen:
        # Classified earlier in this call, and found no write (a write ends it).
        return False
    work.seen.add(text)
    work.spend(len(text))
    lexer = _ShellLexer(text, work)
    complete = lexer.run()
    if any(_command_writes(words, work) for words in lexer.commands):
        return True
    unlexed = lexer.unlexed if complete else [*lexer.unlexed, text]
    if any(_unlexed_writes(chunk, work) for chunk in unlexed):
        return True
    if any(_classify(expanded, work) for expanded in lexer.braced):
        return True
    # Every nested text is strictly shorter, so the recursion ends.
    return any(
        _classify(inner, work) for inner in lexer.nested if len(inner) < len(text)
    )


def _http_writes(text: str) -> bool:
    """The bash-grammar classifier behind :func:`mutating_http` (R7, R8).

    Its work is bounded: past ``_WORK_LIMIT`` characters lexed or scanned, or
    past Python's stack depth, the text counts as a write (R7f).
    """
    try:
        return _classify(text, _Work())
    except (_OverBudget, _UnmodelledExpansion, RecursionError):
        return True


def _bash_writes(text: str) -> bool:
    try:
        # The floor's historical join inserts spaces. Bash removes only the
        # backslash-newline, including in the middle of a word. Keep both.
        return _http_writes(text) or ("\\\n" in text and _http_writes(_joined(text)))
    except RecursionError:
        # Nesting deeper than Python's stack: never fail open (R7f).
        return True


def mutating_http(text: str) -> bool:
    """A curl or wget that sends a method other than GET or HEAD.

    The union (president re-check R6) of the 5d416e4 detector, kept verbatim,
    and the bash-grammar classifier (Consiliency/pmcp#472, R7 and R8): the
    text is lexed as bash lexes it (see the comment above ``_CURL_VALUE_SHORT``),
    the command word is recognised by its basename (``curl``, ``curl.exe``,
    ``wget``, ``wget.exe``) anywhere in a command or inside an expansion that
    builds one, and every text bash may run as commands of its own is
    classified too. A method is sent explicitly (curl ``-X``/``--request``,
    wget ``--method``, attached or not, any value but GET or HEAD, an
    expansion counting as a write) or implied: curl ``-d``, ``-F``, ``-T`` in
    any short group (``#``, ``:`` and digits are option letters), ``--data*``,
    ``--form*``, ``--json*``, ``--upload-file*``, each also as ``--expand-*``
    and as any abbreviation; wget ``--post-data``, ``--post-file``,
    ``--body-data``, ``--body-file`` and any abbreviation, and a wgetrc
    command run by ``-e``. The command word may also be built with
    expansions: the word of ``${X:-curl}`` and the other value operators,
    and a tool name glued to an expansion (``${X}curl``, ``curl$IFS-d@f``).
    A floor token with more than ``_FLOOR_LETTERS_LIMIT`` letters after its
    ``-`` counts as a write without running the floor (its regex is
    quadratic there). Static text only: a command assembled at runtime is out
    of reach (AC1).
    """
    return (
        not _floor_input_is_bounded(text)
        or _mutating_http_5d416e4(text)
        or _bash_writes(text)
    )


# The 5d416e4 floor runs before, and outside, the classifier's work budget.
# Its `-[A-Za-z]*X[A-Za-z]+` fullmatch is quadratic in a token's leading run
# of letters (about 1.5 s for 32,000, PR Consiliency/pmcp#475), so a token
# with a longer run than this counts as a write without running the floor.
_FLOOR_LETTERS_LIMIT = 1024
_FLOOR_LONG_LETTERS = re.compile(rf"-[A-Za-z]{{{_FLOOR_LETTERS_LIMIT + 1}}}")


def _floor_input_is_bounded(text: str) -> bool:
    """No token the floor would test has more than _FLOOR_LETTERS_LIMIT
    letters after its `-` (the floor's own tokens, read in linear time)."""
    for match in _HTTP_COMMAND.finditer(_joined(text)):
        for token in match[2].split():
            if _FLOOR_LONG_LETTERS.match(token.strip("'\"")):
                return False
    return True


# --- guard (d), PowerShell web cmdlets ---------------------------------------
#
# A run step on windows-latest defaults to pwsh, and `shell: pwsh` or
# `shell: powershell` select it anywhere. Invoke-WebRequest and
# Invoke-RestMethod (aliases iwr and irm; curl and wget too under Windows
# PowerShell 5.1) write with any -Method but GET or HEAD, or with
# -CustomMethod. Fail closed: the clause reads every step and shell text,
# whatever its shell, PowerShell's parameter names are case-insensitive and
# may be abbreviated, and a splatted argument set (@params) counts as a write.
MUTATING_PWSH_LABEL = "a mutating PowerShell web request"
_PWSH_WEB = re.compile(
    r"(?<![\w.$-])(?:[\w.-]+\\)?"
    r"(?:invoke-webrequest|invoke-restmethod|iwr|irm|(?:curl|wget)(?:\.exe)?)(?![\w.-])",
    re.IGNORECASE,
)
_PWSH_PARAMETER = re.compile(r"[-\u2013\u2014\u2015]([A-Za-z]+)(:.*)?", re.DOTALL)


def _pwsh_variable_end(text: str, pos: int, limit: int) -> int:
    """Skip a braced variable name; quotes and semicolons are name bytes."""
    pos += 2
    while pos < limit:
        if text[pos] == "`":
            pos += 2
        elif text[pos] == "}":
            return pos + 1
        else:
            pos += 1
    raise _UnmodelledExpansion


def _pwsh_arguments(text: str, pos: int, limit: int) -> tuple[list[str], int]:
    """The argument tokens of a PowerShell command from ``pos`` to the end of
    its statement; an unterminated quote or group runs to the end of the text.
    The scan stops after ``limit`` characters."""
    tokens: list[str] = []
    token: list[str] = []
    depth = 0
    at_token_start = True
    start = pos
    token_work = 0
    n = min(len(text), pos + limit + 1)

    def finish_token() -> None:
        nonlocal token, token_work
        if token:
            token_work += _TOKEN_WORK
            if pos - start + token_work > limit:
                raise _UnmodelledExpansion
            tokens.append("".join(token))
            token = []

    while pos < n:
        if pos - start + token_work > limit:
            raise _UnmodelledExpansion
        char = text[pos]
        if at_token_start and text.startswith("<#", pos):
            # PowerShell ends a block comment at the first #>; block
            # comments do not nest (tokenizer.cs: ScanBlockComment).
            end = text.find("#>", pos + 2, n)
            pos = n if end < 0 else end + 2
            continue
        if at_token_start and char == "#":
            if not depth:
                break
            end = text.find("\n", pos, n)
            pos = n if end < 0 else end
            continue
        if at_token_start and text[pos : pos + 2] in ("@'", '@"'):
            # Unsupported multiline string syntax on a recognized HTTP call:
            # refuse rather than expose its body as command syntax.
            raise _UnmodelledExpansion
        if char == "`":
            at_token_start = text[pos + 1 : pos + 2] == "\n"
            if at_token_start and re.compile(r"[ \t]*#").match(text, pos + 2, n):
                # Continued comments have ambiguous statement association in
                # this abstraction. Refuse, including read-only calls.
                raise _UnmodelledExpansion
            if text[pos + 1 : pos + 2] not in ("", "\n"):
                token.append(text[pos + 1])
            pos += 2
            continue
        if text.startswith("${", pos):
            end = _pwsh_variable_end(text, pos, n)
            token.append(text[pos:end])
            pos = end
            at_token_start = False
            continue
        if char in "'\"":
            if (
                token
                and token[0] in "-–—―"
                and all(piece.isalpha() or piece == ":" for piece in token[1:])
            ):
                finish_token()  # A quote terminates a parameter token.
            at_token_start = False
            end = pos + 1
            while end < n:
                if char == '"' and text.startswith("${", end):
                    end = _pwsh_variable_end(text, end, n)
                    continue
                if char == '"' and text.startswith("$(", end):
                    raise _UnmodelledExpansion
                if text[end] == char:
                    if text.startswith(char * 2, end):
                        end += 2
                        continue
                    break
                end += 2 if char == '"' and text[end] == "`" else 1
            token.append(text[pos : end + 1])
            at_token_start = True
            pos = end + 1
            continue
        if char in "({[":
            depth += 1
        elif char in ")}]":
            if not depth:
                if char == ")":
                    # A statically named command can sit in a command-object
                    # expression, e.g. & (Get-Command iwr) -Method Put.
                    # Retain the tail conservatively even without & or . .
                    finish_token()
                    pos += 1
                    continue
                break
            depth -= 1
        elif not depth and (char in ";|\n" or text.startswith("&&", pos)):
            break
        elif not depth and char.isspace():
            finish_token()
            at_token_start = True
            pos += 1
            continue
        parameter_colon = (
            char == ":"
            and len(token) > 1
            and token[0] in "-–—―"
            and all(token[index].isalpha() for index in range(1, len(token)))
        )
        at_token_start = char.isspace() or char in "(){}[];|," or parameter_colon
        token.append(char)
        pos += 1
    finish_token()
    return tokens, pos


def _pwsh_native_arguments(args: list[str]) -> list[_Word]:
    """Additional native-tool reading: group/array delimiters separate values.

    This is a conservative lexical reading, not expression evaluation. Quotes
    preserve their contents and adjacent fragments concatenate. Original Bash
    and cmdlet readings remain in the union. The caller charges input first.
    """
    words: list[_Word] = []
    for arg in args:
        value: list[str] = []
        pos = 0
        while pos < len(arg):
            char = arg[pos]
            if char in "'\"":
                quote = char
                pos += 1
                while pos < len(arg):
                    char = arg[pos]
                    if char == quote:
                        if arg[pos : pos + 2] == quote * 2:
                            value.append(quote)
                            pos += 2
                            continue
                        pos += 1
                        break
                    if quote == '"' and char == "`" and pos + 1 < len(arg):
                        pos += 1
                        char = arg[pos]
                    value.append(char)
                    pos += 1
                continue
            if char.isspace() or char in "()," or arg.startswith("@(", pos):
                if value:
                    words.append(_Word("".join(value)))
                    value = []
                pos += 2 if arg.startswith("@(", pos) else 1
                continue
            value.append(char)
            pos += 1
        if value:
            words.append(_Word("".join(value)))
    return words


def _pwsh_method_writes(value: str) -> bool:
    """A -Method value other than GET or HEAD; any expression counts."""
    bare = value.strip("()'\" \t")
    if not bare:
        return False
    return bool(re.search(r"[$(\[`@]", bare)) or bare.upper() not in _SAFE_METHODS


_PWSH_QUOTES = str.maketrans(
    {
        **dict.fromkeys("‘’‚‛", "'"),
        **dict.fromkeys("“”„", '"'),
    }
)


_PWSH_LITERAL_NAME = re.compile(r"(?:'[A-Za-z0-9_.:/\\-]*'|\"[A-Za-z0-9_.:/\\-]*\")")
_PWSH_LITERAL_CHAIN = re.compile(
    _PWSH_LITERAL_NAME.pattern + r"(?:\s*\+\s*" + _PWSH_LITERAL_NAME.pattern + r")+"
)


def mutating_powershell_http(text: str) -> bool:
    """Union of literal and normalized static PowerShell readings.

    Smart quotes belong to the ASCII quote classes. Removing backticks before
    letters adds a conservative static-name reading; retain the original so
    escaped argument syntax cannot hide an existing refusal.
    """
    if len(text) > _WORK_LIMIT:
        return True
    if _powershell_http_reading(text):
        return True
    normalized = text.translate(_PWSH_QUOTES)
    if normalized != text and (
        _powershell_http_reading(normalized) or mutating_http(normalized)
    ):
        return True
    unescaped = re.sub(r"`(?=[A-Za-z-])", "", normalized)
    if unescaped != normalized and (
        _powershell_http_reading(unescaped) or mutating_http(unescaped)
    ):
        return True
    # Static string concatenation in a call expression: retain the original
    # readings, then join adjacent literal fragments conservatively.
    joined = _PWSH_LITERAL_CHAIN.sub(
        lambda match: "'"
        + "".join(
            fragment[0][1:-1] for fragment in _PWSH_LITERAL_NAME.finditer(match[0])
        )
        + "'",
        unescaped,
    )
    return joined != unescaped and (
        _powershell_http_reading(joined) or mutating_http(joined)
    )


def _powershell_http_reading(text: str) -> bool:
    """A PowerShell web cmdlet (or its curl/wget alias) that writes.

    Each command word rescans the rest of its statement; past
    ``_WORK_LIMIT`` characters scanned the text counts as a write.
    """
    # A literal format/join at an invocation, type or member site is not an
    # unknown runtime value (president scope ruling, Consiliency/pmcp#476).
    # Refuse without evaluating the expression; GET forms are conservative hits.
    expression_site = re.search(
        r"(?:(?<![\w.])[&.](?=\s|[$(\"'])|(?<![\w-])(?:New-Object|TypeName|GetType|GetMethod|"
        r"GetMember|InvokeMember)\b|\[\s*(?:\(|(?:System\.)?(?:type|string)\s*\])|-as\b)",
        text,
        re.IGNORECASE,
    )
    if expression_site and re.search(
        r"(?<![\w-])[-–—―]f(?![\w-])", text, re.IGNORECASE
    ):
        return True
    if expression_site and re.search(
        r"(?<![\w-])[-–—―]join(?![\w-])", text, re.IGNORECASE
    ):
        return True
    if re.search(
        r"\[\s*(?:System\.)?String\s*\]\s*::",
        text,
        re.IGNORECASE,
    ):
        return True
    if re.search(
        r"(?<![\w-])(?:scriptblock|NewScriptBlock|InvokeScript|Add-Type|"
        r"Set-Alias|New-Alias|sal|nal)\b",
        text,
        re.IGNORECASE,
    ):
        return True
    # Alias provider writes can be spelled through item cmdlets as well as
    # Set-Alias. Refuse provider references conservatively, including reads.
    if re.search(r"(?<![\w])Alias\s*:", text, re.IGNORECASE):
        # Qualified invocation names alone are handled by the cmdlet scanner;
        # provider manipulation is the additional refusal here.
        if re.search(
            r"(?<![\w-])(?:New|Set|Copy|Move|Rename)-Item\b", text, re.IGNORECASE
        ):
            return True
    child_spent = 0
    for child in re.finditer(
        r"(?<![\w.-])(?:pwsh|powershell)(?:\.exe)?(?![\w.-])", text, re.IGNORECASE
    ):
        try:
            child_args, child_end = _pwsh_arguments(
                text, child.end(), _WORK_LIMIT - child_spent
            )
        except _UnmodelledExpansion:
            return True
        child_spent += child_end - child.end() + 1
        if child_spent > _WORK_LIMIT:
            return True
        child_spent += sum(len(argument) for argument in child_args)
        if child_spent > _WORK_LIMIT:
            return True
        for argument in _pwsh_native_arguments(child_args):
            option = _PWSH_PARAMETER.fullmatch(argument.text)
            if option and (
                "command".startswith(option[1].lower())
                or "encodedcommand".startswith(option[1].lower())
                or option[1].lower() in {"commandwithargs", "encodedarguments"}
            ):
                return True
    # Defaults can select a write method without an explicit invocation flag.
    # Do not try to evaluate configuration assignments or their later scope.
    if re.search(
        r"\$\{?(?:[A-Za-z0-9_]+:)?PSDefaultParameterValues\b", text, re.IGNORECASE
    ):
        return True
    if re.search(
        r"(?<![\w-])(?:WebClient|HttpClient|WebRequest|HttpWebRequest|Start-BitsTransfer|Invoke-Expression|iex)\b",
        text,
        re.IGNORECASE,
    ):
        return True
    # Refuse the HTTP message/handler API family, including namespace-qualified
    # references. This deliberately also refuses construction and read-only use;
    # it does not attempt to evaluate object lifetimes or request methods.
    if re.search(
        r"(?<![\w-])(?:(?:System\.)?Net\.Http\b|"
        r"(?:HttpMessageInvoker|HttpMessageHandler|HttpRequestMessage|"
        r"SocketsHttpHandler|HttpClientHandler)\b)",
        text,
        re.IGNORECASE,
    ):
        return True
    spent = 0
    for match in _PWSH_WEB.finditer(text):
        starts = [match.end()]
        if text[match.end() : match.end() + 1] in {"'", '"'}:
            # Could close a qualified command token or open its first argument.
            # Retain both readings; neither may suppress the other.
            starts.append(match.end() + 1)
        for start in starts:
            try:
                args, end = _pwsh_arguments(text, start, _WORK_LIMIT - spent)
            except _UnmodelledExpansion:
                return True
            spent += end - match.end() + 1
            if spent > _WORK_LIMIT:
                return True
            native = match[0].lower().rsplit("\\", 1)[-1].removesuffix(".exe")
            if native in {"curl", "wget"} and any("(" in arg for arg in args):
                spent += sum(len(arg) for arg in args)
                if spent > _WORK_LIMIT:
                    return True
                native_args = _pwsh_native_arguments(args)
                if (_curl_writes if native == "curl" else _wget_writes)(native_args):
                    return True
            if match[0].lower().endswith(".exe"):
                continue  # Explicit native executables have no cmdlet -Method alias.
            for index, token in enumerate(args):
                if re.fullmatch(r"@[A-Za-z_]\w*", token):
                    return True
                parameter = _PWSH_PARAMETER.fullmatch(token)
                if parameter is None:
                    continue
                name = parameter[1].lower()
                if name == "cm" or (len(name) >= 2 and "custommethod".startswith(name)):
                    return True
                if len(name) >= 2 and "method".startswith(name):
                    value = (parameter[2] or ":")[1:]
                    if not value and index + 1 < len(args):
                        value = args[index + 1]
                    if _pwsh_method_writes(value):
                        return True
    return False


# The guard (d) curl/wget detector exactly as it stood at 5d416e4 (PR
# Consiliency/pmcp#463 round 1), kept verbatim as a second check so that the
# shell-word classifier can only ever add refusals.
def _mutating_http_5d416e4(text: str) -> bool:
    """A curl or wget that sends a method other than GET or HEAD.

    Explicitly (``-X``/``--request``/``--method``, including ``-XPUT`` and a
    short group ending in ``X``) or through a flag that implies one: curl
    ``-d``/``--data*``, ``-F``/``--form*``, ``--json``, ``-T``/``--upload-file``
    and any short group containing ``d``, ``F`` or ``T`` (``-sd``, ``-sfT``);
    wget ``--post-data``/``--post-file``/``--body-data``/``--body-file``.
    Static text only: a command assembled at runtime is out of reach.
    """
    for match in _HTTP_COMMAND.finditer(_joined(text)):
        tool, tokens = match[1], match[2].split()
        for index, token in enumerate(tokens):
            bare = token.strip("'\"")
            option, _, attached = bare.partition("=")
            method = None
            if tool == "curl":
                if option == "--request" or re.fullmatch(r"-[A-Za-z]*X", bare):
                    method = attached or (
                        tokens[index + 1] if index + 1 < len(tokens) else ""
                    )
                elif re.fullmatch(r"-[A-Za-z]*X[A-Za-z]+", bare):
                    method = bare.split("X", 1)[1]
                elif option.startswith(_CURL_IMPLIED_LONG):
                    return True
                elif re.fullmatch(r"-[A-Za-z0-9]+", bare) and set(bare[1:]) & set(
                    "dFT"
                ):
                    return True
            else:
                if option == "--method":
                    method = attached or (
                        tokens[index + 1] if index + 1 < len(tokens) else ""
                    )
                elif option.startswith(_WGET_IMPLIED_LONG):
                    return True
            if method is not None and method.strip("'\"").upper() not in _SAFE_METHODS:
                return True
    return False


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


def _with(step: dict[str, Any]) -> dict[str, Any]:
    """The step's ``with:`` mapping, or an empty one."""
    given = step.get("with")
    return given if isinstance(given, dict) else {}


def _raw_step_text(step: dict[str, Any]) -> str:
    parts = (
        [step.get("run"), step.get("shell")]
        + _strings(step.get("with"))
        + _strings(step.get("env"))
    )
    return "\n".join(p for p in parts if isinstance(p, str))


def _step_text(step: dict[str, Any]) -> str:
    return _joined(_raw_step_text(step))


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
                given = _with(step)
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
            shell_texts = [shell, shell.replace("\\\n", "")]
            where = f"{name}:{where_name} defaults.run.shell"
            hits = [
                label
                for label, pattern in PUBLICATION_PATTERNS.items()
                if any(pattern.search(reading) for reading in shell_texts)
            ]
            if mutating_http(shell):
                hits.append(MUTATING_HTTP_LABEL)
            if mutating_powershell_http(shell):
                hits.append(MUTATING_PWSH_LABEL)
            if any(
                re.search(r"skopeo", reading, re.IGNORECASE) for reading in shell_texts
            ):
                hits.append("skopeo")
            reasons.extend(f"{tag} {where} contains {label}" for label in hits)
        for job_name, job in _jobs(doc).items():
            promote = name == IMAGE_WORKFLOW and job_name == PROMOTE_JOB
            smoke_job = name == IMAGE_WORKFLOW and job_name == "build-smoke"
            pinned = MUTATING_HTTP_EXEMPT.get((name, job_name))
            http_exempt = pinned is not None and job_pin(job) == pinned
            for step in _steps(job):
                text = _step_text(step)
                # Preserve legacy scans and add Bash's empty continuation
                # removal for publication tool names and subcommands too.
                continued = _raw_step_text(step).replace("\\\n", "")
                texts = [text] if continued == text else [text, continued]
                where = f"{name}:{job_name}"
                if any(SKOPEO_DELETE.search(reading) for reading in texts):
                    reasons.append(f"{tag} {where} runs skopeo delete")
                if promote:
                    continue
                uses = str(step.get("uses", ""))
                given = _with(step)
                if (
                    uses.startswith("docker/build-push-action")
                    and given.get("push") is not False
                ):
                    reasons.append(
                        f"{tag} {where} build-push-action push: is not literally false"
                    )
                for label, pattern in PUBLICATION_PATTERNS.items():
                    if any(pattern.search(reading) for reading in texts):
                        reasons.append(f"{tag} {where} contains {label}")
                if not http_exempt and (
                    mutating_http(text) or mutating_http(_raw_step_text(step))
                ):
                    reasons.append(f"{tag} {where} contains {MUTATING_HTTP_LABEL}")
                if mutating_powershell_http(text) or mutating_powershell_http(
                    _raw_step_text(step)
                ):
                    reasons.append(f"{tag} {where} contains {MUTATING_PWSH_LABEL}")
                remaining = "\n".join(
                    SKOPEO_SMOKE_LOAD.sub("", reading) if smoke_job else reading
                    for reading in texts
                )
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
                    given = _with(step)
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
            called = job.get("uses") if isinstance(job, dict) else None
            if isinstance(called, str) and re.search(r"(^|/)image\.yml(@|$)", called):
                reasons.append(f"{tag} {name}:{job_name} calls {IMAGE_WORKFLOW}")

    # (i) The release acknowledgement covers every image publication input.
    ack_pattern = _ack_regex(workflows)
    try:
        compiled = re.compile(ack_pattern) if ack_pattern else None
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
