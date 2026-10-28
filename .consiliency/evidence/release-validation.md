# Release validation rollout

Environment alignment retains the v* tag-only trigger, full history/tags, build
before OIDC publish before GitHub release, and the 45-minute build limit. It
selects Python 3.12, Node 22 and uv 0.13.1, runs the complete default suite with
loadfile xdist and combined coverage >=60%, and audits the locked dependency
export excluding the unpublished project. A tag audit failure blocks every later
job, including a future image call, regardless of PR advisory policy.

Release coverage and environment reports stay in RUNNER_TEMP; bounded environment
identity is also printed. After validation, the archive builder exports HEAD's
committed tree into a private directory. Tracked modifications fail; generated
and untracked files cannot enter either distribution. The manifest records this
tree, source SHA, run/attempt, version, lock and build-constraint hashes. Each
exact wheel and sdist is independently installed and behavior-tested before the
bundle is uploaded. Downstream jobs use its immutable artifact ID and verify
all bytes and current tag identity before publication. Existing GitHub releases
must have exactly the same complete asset set and hashes; no asset is clobbered.
No existing PR wheel substitutes for the tag's distribution. Timing improvement
remains unmeasured without a comparable serial baseline.

Only the owner may authorize a production v* tag push. The tag is publication,
not a CI test. Select a commit containing the current reviewed E/F/G workflows.
Verify its source/version/tag binding, exact-head review and current full CI.
Never tag a pre-G commit or replay a pre-G non-PR Docker run after cutover.
Read back tag identity before publishing; do not move/delete an occupied release
or registry version to recover from failure. Partial reruns deliberately fail
closed on an older attempt's artifact. Re-running all jobs can also fail if
PyPI already accepted that version; automatic partial-release recovery is not
provided. If PyPI succeeds and GitHub release creation fails, the owner must
prepare a separately reviewed recovery using the original immutable artifact ID,
source SHA/run/attempt and archive hashes. Read back both PyPI distribution hashes
and the current tag before authorizing any missing GitHub assets. Do not rebuild,
overwrite existing assets, move tags, or treat this note as recovery write
authorization. A new source head requires a new review binding.
