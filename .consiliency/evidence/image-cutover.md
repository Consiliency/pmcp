# Legacy image cutover evidence

G removes both legacy registry writers from `docker.yml` (workflow 219424499),
which becomes a read-only pull-request build with `contents: read`, no login,
literal `push: false` and no cache export, and adds a guarded replacement whose
registry writes are all disabled by default. `image.yml` listens only to
completed `Test` runs on `main`; its `gate` job opens only when the repository
variable `PMCP_IMAGE_PUBLISH` is exactly `enabled`, `verify-upstream` re-checks
the Test run identity, attempt and `ci-required` result through the API before
any candidate checkout, `build-smoke` builds one OCI archive and smokes it
locally, and only `promote-main` holds `packages: write`, writing the immutable
`sha-<40 lowercase hex>` tag and then moving `main`. Tag mode is implemented but
has no caller. `image-watch.yml` keeps a weekly read-only observation of both
the legacy run inventory and the registry. Nothing here authorizes activation:
the variable stays unset until the owner procedure below is complete.

Run `python scripts/image_cutover_inventory.py --output <fresh-evidence.json>`
using the existing authenticated gh reader. The correct old Docker workflow ID
is 219424499, path .github/workflows/docker.yml, repository ID 1123923171. The tool
checks those identities and enumerates every unfiltered page twice back to back.
It checks counts and unique IDs, records actual status/attempt/SHA/conclusion, and selects every
non-pull_request status other than completed. It deliberately has no event,
status, date or search filter: filtered Actions queries can cap at 1000 results.
Both passes must have identical run IDs, attempts, SHAs, events, actual statuses
and conclusions. Missing or malformed terminal conclusions are incomplete.
Changed totals, duplicate/missing entries, API failures or incomplete evidence
fail closed. An unknown nonterminal status is a blocker. No cancellation or
registry API write is implemented. Cancellation requested is never terminal
quiescence. The report always says activation_allowed=false, even if drained.
Use a fresh output path: an existing path is refused, and a new incomplete placeholder is written
before querying, so a crash cannot leave a prior successful report in that path.
Two matching passes are bounded point observations, not an atomic snapshot or
cryptographic replay prevention: an operator replay can still land after each
run's position is read in the second pass. If the passes differ, repeat the whole
inventory until stable; PR runs are never excluded from the comparison. The final
pre-activation rescan and prohibited-replay detection remain mandatory.

`complete=true` means complete enumeration only. A drain observation needs a
stable complete inventory, exit 0 and an empty `non_pr_not_completed`, and none
of these authorizes activation.

After G merges, record its exact merge SHA and cutover timestamp, run the complete
inventory, handle outstanding runs through the owner procedure, then repeat until
every non-PR run is actually completed. Capture a fresh complete inventory again
immediately before activation. Earlier preliminary inventories cannot substitute
for this post-cutover sequence. Retain all IDs/attempts/SHA/status/conclusions.

Separately read and verify registry manifest digests or verified absence for all
tags each legacy run could write. The effective metadata-action inventory at pin
dc802804100637a589fabce1cb79ff13a1411302 includes main, the unprefixed seven-hex SHA,
exact version, major.minor and latest. Stable releases generate latest implicitly;
prereleases retain their version and short SHA, without automatic major.minor or
latest. No major-only alias was configured. Enumerate the full registry tag list
and validate every manifest digest, not only known example tags. Record metadata
source pin, source workflow and complete tag inventory alongside every baseline.

Historical occupancy is untrusted legacy evidence. In particular, version 3.0.0,
3.0 and latest currently share a legacy digest; this is not approval to replace,
remove or move any of them. Differing immutable writes must fail closed. The new protocol never writes or
moves `latest` or `major.minor` (owner decision D1): tag mode writes exact
version tags only, and the only new SHA target is `sha-<40 lowercase hex>` (D2).
Any replacement, deletion or tag movement requires specific owner authorization
with concrete evidence.

Only a later reviewed activation may enable writes after complete drain, registry
readback, smoke/digest/race rehearsals and exact source/review/CI binding. Re-scan
for prohibited pre-G reruns and pre-G release tags; a replay or unexpected digest
requires disabling promotion and the existing incident procedure. This is owner
procedure plus detection, not cryptographic prevention of operator replay.

The [GitHub workflow-run API reference](https://docs.github.com/en/rest/actions/workflow-runs#list-workflow-runs-for-a-workflow) documents the filtered-search limit and pagination fields.
