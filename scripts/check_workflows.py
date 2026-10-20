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
EXPECTED_JOBS = {"build", "publish", "github-release"}
EXPECTED_NEEDS = {"publish": ["build"], "github-release": ["publish"]}
EXPECTED_ENVIRONMENT = {"publish": "release"}
# ``None`` means the job declares no permissions: block at all.
EXPECTED_JOB_PERMISSIONS: dict[str, dict[str, str] | None] = {
    "build": None,
    "publish": {"id-token": "write"},
    "github-release": {"contents": "write"},
}
# SHA form ONLY — no `# vX.Y.Z` here. `yaml.safe_load` strips the comment before
# `_collect_uses` runs, so a value carrying one could never match anything.
# The comment lives in release.yml and is enforced on raw text by
# `all_uses_are_sha_pinned`; the commit is enforced here.
EXPECTED_USES = {
    "build": [
        "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
        "astral-sh/setup-uv@c18668ad3cf93ea998bef934396af7bb5c839dc7",
        "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a",
    ],
    "publish": [
        "actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c",
        # v1.14.2 == the release/v1 head on the day of pinning. Any re-pin must
        # stay >= 1.13.0: GHSA-vxmw-7h4f-hqxh (injectable expression expansions)
        # affects every earlier release.
        "pypa/gh-action-pypi-publish@dc37677b2e1c63e2034f94d8a5b11f265b73ba33",
    ],
    "github-release": [
        "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
        "actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c",
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
# `docker.yml` is tag-triggered today — it builds and pushes the release image
# on `v*` — and carries `packages: write`. Adding a file here must be deliberate.
TAG_TRIGGERED_WORKFLOWS = {"release.yml", "docker.yml"}

# Timeouts: a floor stops a job being throttled to death, a ceiling stops the
# six-hour default being re-created by a large value. Both ends are load
# bearing; see .consiliency/evidence/bypass-proofs-187.md. The ceiling was 30
# until the test matrix job reached 29m42s; 45 is a stopgap while the suite is
# parallelised (see Consiliency/pmcp#383).
TIMEOUT_MIN = 10
TIMEOUT_MAX = 45

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
    for path in workflow_files():
        doc, err = _load(path)
        if err:
            failures.append(err)
            continue
        failures.extend(timeout_invariants(doc, path))
        failures.extend(tag_trigger_invariants(doc, path))
        failures.extend(stable_context_invariants(doc, path))
        if path.name == "test.yml":
            failures.extend(diagnostic_invariants(doc))
            failures.extend(full_bootstrap_invariants(doc))

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
