# Legacy image cutover evidence

This preparation adds read-only inventory tooling. It does not remove or replace
any publisher and never authorizes activation. G must atomically introduce the
guarded replacement and remove both legacy main and tag paths in its own PR.
All new registry writes, including SHA/version writes, remain disabled until the
post-G evidence and activation prerequisites are complete. The positive enable
control is still absent and must default to disabled when introduced.

Run `python scripts/image_cutover_inventory.py --output <fresh-evidence.json>`
using the existing authenticated gh reader. The correct old Docker workflow ID
is 219424499, path .github/workflows/docker.yml, repository ID 1123923171. The tool
checks those identities, enumerates every unfiltered page, checks counts and
unique IDs, records actual status/attempt/SHA/conclusion, and selects every
non-pull_request status other than completed. It deliberately has no event,
status, date or search filter: filtered Actions queries can cap at 1000 results.
Changed totals, duplicate/missing entries, API failures or incomplete evidence
fail closed. An unknown nonterminal status is a blocker. No cancellation or
registry API write is implemented. Cancellation requested is never terminal
quiescence. The report always says activation_allowed=false, even if drained.

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
remove or move any of them. Differing immutable writes must fail closed. Preserve
consumer-visible aliases in the reviewed new protocol. Any replacement, deletion
or tag movement requires specific owner authorization with concrete evidence.

Only a later reviewed activation may enable writes after complete drain, registry
readback, smoke/digest/race rehearsals and exact source/review/CI binding. Re-scan
for prohibited pre-G reruns and pre-G release tags; a replay or unexpected digest
requires disabling promotion and the existing incident procedure. This is owner
procedure plus detection, not cryptographic prevention of operator replay.

The [GitHub workflow-run API reference](https://docs.github.com/en/rest/actions/workflow-runs#list-workflow-runs-for-a-workflow) documents the filtered-search limit and pagination fields.
