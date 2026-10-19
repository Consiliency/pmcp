# Release validation rollout

Environment alignment retains the v* tag-only trigger, full history/tags, build
before OIDC publish before GitHub release, and the 45-minute build limit. It
selects Python 3.12, Node 22 and uv 0.13.1, runs the complete default suite with
loadfile xdist and combined coverage >=60%, and audits the locked dependency
export excluding the unpublished project. A tag audit failure blocks every later
job, including a future image call, regardless of PR advisory policy.

This does not yet prove the final wheel or sdist. Exact archive installation,
behavior verification and same-byte publication are the following F slice; no
existing PR wheel substitutes for the tag's distribution. Serial-to-parallel
benefit is unmeasured until a release-like full-history rehearsal on ai.

Only the owner may authorize a production v* tag push. The tag is publication,
not a CI test. Select a commit containing the current reviewed E/F/G workflows.
Verify its source/version/tag binding, exact-head review and current full CI.
Never tag a pre-G commit or replay a pre-G non-PR Docker run after cutover.
Read back tag identity before publishing; do not move/delete an occupied release
or registry version to recover from failure. A rerun reuses the original source
and must preserve artifact identity; a new head needs a new review binding.
