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

## Guarded cutover evidence (G)

Spec: `plan-accepted-v3.md` (sha256 `3f4a3a828f0ccd05f64261d01a9736f8c1a0db60f89e0db6dc5a5c84b8af1b51`): the accepted plan plus president re-check 1 amendments PA-1a..PA-1d, PA-2a and PA-3a, and the Consiliency/pmcp#463 round-1 amendment PA-4. Nothing in this section authorizes activation, a settings change, a tag, a release or a registry write.

### Design as built

- `docker.yml` (workflow 219424499) is a pull-request-only build with `contents: read`, no registry login, literal `push: false` and no cache export. Both legacy writers (push to `main`, `v*` tags) are gone, and `TAG_TRIGGERED_WORKFLOWS` is `{release.yml}`. A non-PR run of this workflow ID after the cutover can only replay a pre-cutover definition and is a detection event.
- `image.yml` listens only to completed `Test` runs on `main`. `gate` (no permissions, one pinned heredoc step) opens only when `PMCP_IMAGE_PUBLISH` is byte-for-byte `enabled`. Guard (e) pins the whole gate job and, since PR round 1 (PA-4), limits `image.yml`'s top-level keys to `name`, `on`, `permissions` and `jobs`: a workflow-level `env` (bash runs `BASH_ENV`, command substitution included, before the heredoc) or `defaults.run.shell` would otherwise reach the gate step. `verify-upstream` re-reads the Test run attempt and its jobs through the API from a sparse `github.workflow_sha` checkout of `scripts/image_publish.py`. `build-smoke` checks out the verified head without credentials, builds one OCI archive with buildx, verifies its single-manifest layout, loads it with the pinned skopeo and drives a real downstream tool call through it as UID 1000. `promote-main`, the only `packages: write` job, re-checks the gate, re-verifies the archive, refuses a stale remote `main`, writes or no-ops the immutable `sha-<40 hex>` tag (refusing a different digest), re-reads `main`, and only then moves the `main` alias, reading back each write. Tag mode exists in the script with no caller.
- `image-watch.yml` runs weekly and on dispatch with read-only permissions. It writes exactly one marker, `IMAGE_WATCH_OUTCOME=` `COMPLETE`, `DISCREPANCY`, `INCOMPLETE` or `NO_BASELINE`, to its job summary.
- The Dockerfile pins both base images by digest, installs `uv==0.13.1`, installs runtime dependencies from a hashed `uv export --locked` and the project with `--no-deps`, and runs as UID 1000. Follow-up F-A covers the remaining unhashed build inputs.

### The one exemption from guard (d)'s curl/wget clause

`maintenance.yml:notify-worker` (the maintenance-worker event POST that predates G, `curl -sf --max-time 30 -X POST "$WORKER_URL/events/github"`) is exempt from the curl/wget clause only, and only while the sha256 of canonical JSON of its whole job mapping (`json.dumps(job, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)` over the `yaml.safe_load` mapping) equals the pin `3391171c6bdbd2ba03e5ae24bd0d636d9160141e2495cff6dfb737632de1881b` in `check_workflows.py`. Any edit to that job (its run block, env, an added step, or a key such as `shell`, `container` or `defaults`) re-arms the clause; `release-diff-ack` covers the file that holds the pin. Every other (d) rule still applies to the job. Why it is safe while the pin holds: the job's only mutating request is one POST of an event JSON body to the URL in `vars.CONSILIENCY_WORKER_URL`, authenticated with the `CONSILIENCY_WORKER_API_KEY` secret. An OCI tag is created, moved or deleted only by PUT or DELETE, so this POST cannot change a tag through the registry API, and the job's `GITHUB_TOKEN` stays read-only under guard (c) and the repository default. Repointing that variable or secret is a settings change, which no static guard can see.

### How guard (d) reads shell text (Consiliency/pmcp#472)

The curl/wget clause is the union of two detectors: the 5d416e4 detector, kept byte-identical as `_mutating_http_5d416e4` (with `_HTTP_COMMAND`, `_SAFE_METHODS`, `_CURL_IMPLIED_LONG`, `_WGET_IMPLIED_LONG` and `_joined`), and a classifier that follows bash's grammar (bash(1): SHELL GRAMMAR, QUOTING, EXPANSION, REDIRECTION, Here Documents). The classifier was derived from that grammar, not from the case lists (president re-check R7-R9 of PR Consiliency/pmcp#463).

- **Comments.** An unquoted `#` that starts a word runs to the end of the line, so quote characters in it are literal. The comment text is also classified as commands, which can only add refusals.
- **Here-documents.** After the newline that ends a line holding `<<WORD` or `<<-WORD` (any quoting of `WORD`), the body is read line by line up to its delimiter (`<<-` strips leading tabs), so a quote in it never opens a region past it. Each body is classified as commands (it may feed a shell, `bash <<'EOF'`), and when `WORD` is unquoted its `$(...)`, `${...}` and backticks are classified too. `<<<` is a here-string, not a here-document.
- **Expansions.** `$(...)`, `$((...))`, backticks, `<(...)`, `>(...)`, `${...}` and `$name` end neither the word nor the command. The commands inside substitutions are classified, and so are the word part of `${name:-word}` and arithmetic contents. `$(...)` is parsed in place, with nested quotes, comments, here-documents and `case` patterns, so its closing `)` is the one bash uses.
- **Operators.** A command ends at a newline, `;`, `&`, `&&`, `|`, `||`, `|&`, `;;`, `;&` and `;;&`, and at a `(` or `)` that opens or closes a subshell or group (arithmetic `((...))` excepted). The redirections `<`, `>`, `>>`, `<<`, `<<-`, `<<<`, `<&`, `>&`, `<>`, `>|`, `&>` and `&>>`, with an optional fd number or `{var}`, are not arguments, and their `&` never ends a command (`2>&1`, `&>/dev/null`).
- **Quoting.** `\x`, `'...'`, `"..."` (with its own backslash rules and nested expansions), `$'...'` (ANSI-C escapes decoded) and `$"..."` are removed. The content of every quoted part, and every word holding a backslash-escaped blank (`bash -c curl\ -X\ PUT\ u`), is classified as commands of its own.
- **No fail-open path.** There is no depth cap; each recursion is on strictly shorter text. Text bash could not finish reading (an unterminated quote or substitution) keeps the commands read before it, and also has every `curl`/`wget` word, glued to anything, classified with the rest of the text, quotes removed, as its arguments. Work is bounded (below), and reaching the bound counts as a write.
- **The command word.** Its basename is `curl`, `curl.exe`, `wget` or `wget.exe` (any letter case, `/` or `\` paths), anywhere in a command (`timeout 5 curl`, `if curl ...`), or an expansion in the word names one (`$(command -v curl)`, `${CURL:-curl}`; a `-` before the name is glue, so `${X:-curl}` and `${X-curl}` count). PR Consiliency/pmcp#475 F1/F2 added the word's *shape*: the word as bash may build it, with each expansion standing for an unknown value that may be empty or split the word. For `${name<op>word}` with a value operator (`-`, `:-`, `=`, `:=`, `+`, `:+`, and the substitutions `/`, `//`, `/#`, `/%`), `word` itself is part of the shape, its blanks as split points (`${X:-cu}rl`, `${X:-/usr/bin/curl}`, `${X:-${Y:-curl}}`). A tool name counts in the shape where it starts at the word's start, after `/` or `\` or an expansion, and ends at the word's end or an expansion (`${X}curl`, `$X""curl`, `cu${X}rl`, `curl${X}`). What follows it inside the word, split at each expansion, is read as its first arguments, so `curl$IFS-d@report.json$IFS"$URL"` is a write. Text that cannot be lexed is also re-read with its words split at simple expansions. Pattern-removal, error, case and substring operators (`#`, `%`, `:?`, `^`, `,`, `:offset`) do not put their word in the shape. Accepted false positives (fail closed, listed in the tests as `EXPANSION_WORD_ACCEPTED_FALSE_POSITIVES` and live-confirmed to send nothing): a quoted expansion still counts as a split point (`"curl${X}-d@f"`), a tool word anywhere in a command counts (`echo "${X:-curl}" -d@f`), and the older raw-text rule reads curl/wget inside any expansion (`${X#curl} -d@f`, `${X/curl/a} -d@f`).
- **curl options.** In a short group, `#`, `:` and digits are option letters, so the scan continues past them; `d`, `F` or `T` imply a method, and `X` takes the rest of the word or the next word. A value-taking letter (`AbcCdDeEFHKmoPQrtTuUwxXyYz`) ends the group, so `-#fLo` stays a read. Long options: `--request`, and names starting with `--data`, `--form`, `--json` or `--upload-file`, each also as `--expand-<name>` (curl 8.3.0+) and as any abbreviation of those names.
- **wget options.** `--method`, `--post-data`, `--post-file`, `--body-data`, `--body-file` and any abbreviation (GNU getopt_long). A wgetrc command run by `-e`/`--execute` (`post_data=`, `method=`, ...; wgetrc ignores case, `-` and `_`) is also read.
- **The method word.** Any value but GET or HEAD, in any letter case after quote removal, is a write; an expansion anywhere in the word counts as a write; an empty or missing word does not (`curl -X ""` exits 2).
- **Publication spellings (R8f, R9).** `docker`, `podman` and `buildah` are refused when global options (each an option word, optionally followed by one value word) come before `push` or `image push`. As one backtracking regex this search was quadratic on a single hostile line (about 35 s for 160 KB of `docker -x `, below the work bound and then accepted; PR Consiliency/pmcp#475 F3), and so were the older `SKOPEO_DELETE` and `type=image ... push=true` patterns (on `main` too). The replacements avoid quadratic backtracking (`_ToolPush`, a right-to-left pass with binary searches, O(n log n); `_LinePair`, a linear pass per line) and checked against the old regexes on about 55,000 bounded generated texts each. The `build-smoke` load's two operands may contain only `[A-Za-z0-9_./:@+-]` and must each end at a blank or at the end of the text, so anything glued on (`;`, `&`, `|`, `$(`, a backtick or a quote) leaves `skopeo` in the remainder.
- **PowerShell web cmdlets.** `Invoke-WebRequest`, `Invoke-RestMethod`, `iwr`, `irm`, and `curl`/`wget` as Windows PowerShell 5.1 aliases, are refused (`contains a mutating PowerShell web request`) with any `-Method` but GET or HEAD, with `-CustomMethod`/`-CM`, or with a splatted argument set (`@params`). Parameter names are case-insensitive and may be abbreviated (`-Me`, `-Cu`), and an expression for the method counts as a write. The clause scans statically named commands in every step and `defaults.run.shell` text, whatever its shell. It recognizes ASCII, en, em and horizontal-bar parameter dashes, quoted/module-qualified command names, and the tail after command-object expressions such as `& (gcm iwr)`. Only the runtime-dependent name classes accepted in the scope ruling below remain outside static analysis; literal format/join is refused. The closing-parenthesis handling conservatively associates a following method even without an invocation operator. That covers `windows-latest`'s default `pwsh`, `shell: pwsh` and `shell: powershell` without resolving the effective shell. The reported static forms are covered; this is not a complete PowerShell evaluator. Unknown runtime state and command construction remain within the AC1 limits below.
- **Bounded work (2026-11-03 dev0 incident).** All lexing, nested classification, rescans and lookaheads of the bash-grammar classifier for one text are charged to `_WORK_LIMIT` (250,000 characters). That budget does not cover the regex scans that run outside the classifier: the publication scans have the bounds described above, and the 5d416e4 floor, which is quadratic in a token's run of letters after its `-` (`-[A-Za-z]*X[A-Za-z]+`, about 1.5 s for 32,000 letters), is not run when such a run is longer than `_FLOOR_LETTERS_LIMIT` (1,024): the text counts as a write instead. The floor itself stays byte-identical. Each PowerShell reading has a scan budget of the same size and stops when it reaches it; at most four static readings are considered, with an input-length check before normalization. Exceeding a budget, or Python's stack depth, counts as a write. Memoisation is per call. The committed-tree regression requires every step to use less than 10% of the budget. Tests run 12 hostile, linear-size inputs (each quadratic or worse without the bound) under a 60 s timeout, with CPU-time and `tracemalloc` peak assertions. CPU time excludes time descheduled on a loaded host; wall-time timeouts are hang guards.
- **Out of reach (AC1 and follow-ups).** Commands assembled at runtime (`eval "$X"`, an unquoted `$OPTS` that word-splits into options, aliases and functions), `-K`/`--config`, `.curlrc`/`wgetrc` files and `gh api` writes (F-F, Consiliency/pmcp#468).

The committed tree's classification is unchanged. All 117 step and `defaults.run.shell` texts lex completely, only `maintenance.yml:notify-worker` is flagged (and exempt by its unchanged pin), and the PowerShell clause flags none of them.

**Never weaker than 5d416e4 (PR re-check R6).** The president's re-check of round 1 found 23 writes (A1-A23) that the 5d416e4 detector refused and the shell-word classifier accepted (command substitution and backticks ending a command, comments with apostrophes, a fail-open nesting cap, `curl.exe`, runtime command words and methods). The clause is therefore the union of both: the 5d416e4 detector is kept verbatim as `_mutating_http_5d416e4`, with its `_HTTP_COMMAND` regex, and a command is mutating if either flags it. The 23 cases (A14/A15 at nesting depths 5, 6 and 8, in double- and single-quoted forms) are must-flag tests, and negative control NC-R6 (drop the old detector) kills them. Owner scope decision, 2026-11-01: land G with this floor; the bash-grammar lexer, the per-tool option grammars and the smoke-operand tightening (R7-R9, with their mutants and controls) and PowerShell web cmdlets move to Consiliency/pmcp#472, which must be done before publishing is enabled. Consiliency/pmcp#472 (the section above) keeps the floor and its wiring: with the classifier stubbed to return False, every A case is still flagged, which NC-R6 kills. R10 made `mypy scripts/check_workflows.py` clean with no behaviour change.

### The skopeo pin

Both copies (the `build-smoke` load in `image.yml` and `SKOPEO_IMAGE` in `image_publish.py`, asserted identical by a test) pin `quay.io/skopeo/stable@sha256:c0ee1f4edca5c01cb8d5611124f92f3cc47196ecab68aee5d0f90834e00574d5`, the OCI index of the never-overwritten `v1.22.3-immutable` tag (linux/amd64 manifest `sha256:ab4c269c9e2bd11affe2666b860fb651a15afec121c15986b052b02e09d86239`). On 2026-11-01 a registry GET of `manifests/v1.22.3-immutable` returned that `Docker-Content-Digest` (body hash equal), and the tag history held one entry with no end. The r4 pin `cb18e3dd…` returned 404 because the plain version tags are rebuilt every weekday. There is no fallback; an unpullable pin fails `build-smoke` closed. A local no-publish rehearsal with this pin (buildx OCI build, `oci-inspect`, skopeo 1.22.3 load, smoke) returned `{"real_downstream_response": true, "uid": 1000}`.

### Negative controls

Each control ran in a scratch copy of `scripts/`, `tests/`, `.github/` and `.consiliency/`; a control is killed when its targeted tests fail. `TestCheckerEntryPoint::test_the_unmutated_tree_exits_zero` and `::test_a_base_with_no_merge_base_exits_non_zero` fail in any scratch copy (it is not a git repository) and are excluded from the counts. `D1-*` rows ran against the re-check 1 detector; `D1-*-r1` rows re-ran them against the PR round-1 detector, and `NC-*` rows are the round-1 controls. Edit definitions are kept beside the results as `negctl-edits/<control>.json`.

| control | file | result | failing tests | examples |
|---|---|---|---|---|
| `T2-1-predicate` | `scripts/image_cutover_inventory.py` | KILLED | 2 | `test_cli_complete_inventory_with_a_non_pr_blocker_exits_one[future_platform_status]`, `test_cli_complete_inventory_with_a_non_pr_blocker_exits_one[in_progress]` |
| `T2-2-except-narrowed` | `scripts/image_cutover_inventory.py` | KILLED | 3 | `test_reader_failures_are_incomplete_with_the_fixed_text[called-process-error]`, `test_reader_failures_are_incomplete_with_the_fixed_text[timeout]`, `test_response_failures_cannot_become_complete[reader-failure]` |
| `T2-3-non-pr-only` | `scripts/image_cutover_inventory.py` | KILLED | 2 | `test_a_pr_row_changing_between_passes_is_unstable`, `test_three_unstable_attempts_are_incomplete_and_never_a_stop` |
| `T3-1-drop-missing-ids` | `scripts/image_cutover_inventory.py` | KILLED | 8 | `test_a_missing_pr_row_also_stops`, `test_a_row_exactly_at_the_horizon_is_expired_and_exits_zero`, `test_a_row_one_second_younger_than_the_horizon_stops` (+5 more) |
| `T3-2-identity-without-sha-event` | `scripts/image_cutover_inventory.py` | KILLED | 4 | `test_an_identity_change_stops[completed-event]`, `test_an_identity_change_stops[completed-head_sha]`, `test_an_identity_change_stops[queued-event]` (+1 more) |
| `T3-3-attempt-change-as-progress` | `scripts/image_cutover_inventory.py` | KILLED | 3 | `test_an_identity_change_stops[completed-run_attempt]`, `test_an_identity_change_stops[queued-run_attempt]`, `test_issue_443_pass_two_rerun_repeats_then_stops` |
| `T3-4-every-missing-expired` | `scripts/image_cutover_inventory.py` | KILLED | 7 | `test_a_missing_pr_row_also_stops`, `test_a_row_one_second_younger_than_the_horizon_stops`, `test_a_young_missing_row_stops_while_older_rows_expire` (+4 more) |
| `T3-5-age-against-days` | `scripts/image_cutover_inventory.py` | KILLED | 1 | `test_a_row_exactly_at_the_horizon_is_expired_and_exits_zero` |
| `T4-1-swap-on-main-statuses` | `scripts/image_registry_readback.py` | KILLED | 7 | `test_watch_first_promotion_is_accepted`, `test_watch_main_must_match_an_on_main_sha_tag`, `test_watch_on_main_rule[ahead-True]` (+4 more) |
| `T4-2-RE4b-watch-accepts-unknown-new-tags` | `scripts/image_registry_readback.py` | KILLED | 4 | `test_watch_new_tag_outside_the_allowed_classes_stops[3.0.1]`, `test_watch_new_tag_outside_the_allowed_classes_stops[aaaaaaa]`, `test_watch_new_tag_outside_the_allowed_classes_stops[foo]` (+1 more) |
| `T4-3-RE3c-validity-ignores-timestamps` | `scripts/image_registry_readback.py` | KILLED | 4 | `test_drain_readback_missing_a_timestamp_is_incomplete[baseline-finished_at]`, `test_drain_readback_missing_a_timestamp_is_incomplete[baseline-started_at]`, `test_drain_readback_missing_a_timestamp_is_incomplete[current-finished_at]` (+1 more) |
| `T4-4-RE3c-cli-omits-finished-at` | `scripts/image_registry_readback.py` | KILLED | 5 | `test_cli_output_has_timestamps_and_must_be_fresh`, `test_readback_cli_with_a_contract_exits_one_on_a_stop`, `test_watch_discrepancy_alongside_incomplete_read_is_discrepancy` (+2 more) |
| `T4-5-RE2d-incomplete-takes-precedence` | `scripts/image_registry_readback.py` | KILLED | 1 | `test_watch_discrepancy_alongside_incomplete_read_is_discrepancy` |
| `T4-6-RE2d-second-marker-possible` | `scripts/image_registry_readback.py` | KILLED | 4 | `test_each_outcome_writes_exactly_one_marker[COMPLETE]`, `test_each_outcome_writes_exactly_one_marker[DISCREPANCY]`, `test_each_outcome_writes_exactly_one_marker[INCOMPLETE]` (+1 more) |
| `T5-1-gate-lower` | `scripts/image_publish.py` | KILLED | 4 | `test_phase_one_gate_is_byte_exact[ENABLED-False]`, `test_phase_one_gate_is_byte_exact[Enabled-False]`, `test_promote_rechecks_the_gate_before_anything[Enabled]` (+1 more) |
| `T5-2-ci-required-dropped` | `scripts/image_publish.py` | KILLED | 4 | `test_ci_required_must_be_exactly_one_success[jobs0]`, `test_ci_required_must_be_exactly_one_success[jobs1]`, `test_ci_required_must_be_exactly_one_success[jobs2]` (+1 more) |
| `T5-3-different-leads-to-write` | `scripts/image_publish.py` | KILLED | 1 | `test_a_different_sha_tag_refuses_with_zero_alias_writes` |
| `T5-4-alias-before-phase-4` | `scripts/image_publish.py` | KILLED | 4 | `test_a_different_sha_tag_refuses_with_zero_alias_writes`, `test_absent_sha_tag_is_written_read_back_then_main_moves`, `test_identical_sha_tag_moves_only_the_alias` (+1 more) |
| `T5-5-stale-check-reads-local-head` | `scripts/image_publish.py` | KILLED | 6 | `test_a_different_sha_tag_refuses_with_zero_alias_writes`, `test_a_failed_readback_refuses`, `test_absent_sha_tag_is_written_read_back_then_main_moves` (+3 more) |
| `T5-6-tag-different-leads-to-write` | `scripts/image_publish.py` | KILLED | 1 | `test_tag_mode_occupied_version_with_another_digest_refuses` |
| `D1-1-exempt-without-hash` | `scripts/check_workflows.py` | KILLED | 4 | `TestMutatingHttpClause::test_mutant_is_rejected_by_the_curl_wget_clause[image-http-notify-curlrc]`, `TestMutatingHttpClause::test_mutant_is_rejected_by_the_curl_wget_clause[image-http-notify-put]`, `TestMutatingHttpClause::test_mutant_is_rejected_by_the_curl_wget_clause[image-http-notify-url-repointed]` (+1 more) |
| `D1-2-hash-over-run-block-only` | `scripts/check_workflows.py` | KILLED | 3 | `TestMutatingHttpClause::test_mutant_is_rejected_by_the_curl_wget_clause[image-http-notify-curlrc]`, `TestMutatingHttpClause::test_mutant_is_rejected_by_the_curl_wget_clause[image-http-notify-url-repointed]`, `TestMutatingHttpClause::test_shell_and_defaults_are_scanned_and_never_exempt[step]` |
| `D1-3-implied-methods-removed` | `scripts/check_workflows.py` | KILLED | 5 | `TestMutatingHttpClause::test_mutant_is_rejected_by_the_curl_wget_clause[image-http-data-d]`, `TestMutatingHttpClause::test_mutant_is_rejected_by_the_curl_wget_clause[image-http-upload-T]`, `TestMutatingHttpClause::test_mutant_is_rejected_by_the_curl_wget_clause[image-http-wget-post-file]` (+2 more) |
| `D1-4-shell-and-defaults-unscanned` | `scripts/check_workflows.py` | KILLED | 3 | `TestMutatingHttpClause::test_mutant_is_rejected_by_the_curl_wget_clause[image-http-defaults-shell]`, `TestMutatingHttpClause::test_mutant_is_rejected_by_the_curl_wget_clause[image-http-step-shell]`, `TestMutatingHttpClause::test_shell_and_defaults_are_scanned_and_never_exempt[workflow]` |
| `D2-1-one-skopeo-copy-changed` | `.github/workflows/image.yml` | KILLED | 1 | `test_build_smoke_and_promote_use_the_identical_skopeo_pin` |
| `NC-R1-top-level-check-deleted` | `scripts/check_workflows.py` | KILLED | 7 | `TestImagePublicationInvariants::test_mutant_is_rejected_with_the_image_tag[image-gate-workflow-defaults]`, `TestImagePublicationInvariants::test_mutant_is_rejected_with_the_image_tag[image-gate-workflow-env]`, `TestRoundOneGuards::test_extra_top_level_keys_in_image_yml_are_refused[concurrency-value2]` (+4 more) |
| `NC-R2a-split-at-any-separator` | `scripts/check_workflows.py` | KILLED | 6 | `TestMutatingHttpClause::test_mutant_is_rejected_by_the_curl_wget_clause[image-http-quoted-separator]`, `TestMutatingHttpClause::test_the_detector_flags_explicit_and_implied_methods[curl "$URL?x=1&y=2" -X PUT]`, `TestMutatingHttpClause::test_the_detector_flags_explicit_and_implied_methods[curl "https://h/x;y" -T f]` (+3 more) |
| `NC-R2b-alphanumeric-only-groups` | `scripts/check_workflows.py` | KILLED | 8 | `TestMutatingHttpClause::test_mutant_is_rejected_by_the_curl_wget_clause[image-http-attached-value]`, `TestMutatingHttpClause::test_the_detector_flags_explicit_and_implied_methods[curl -F"x=y" "$URL"]`, `TestMutatingHttpClause::test_the_detector_flags_explicit_and_implied_methods[curl -F'file=@image.tar' u]` (+5 more) |
| `NC-R2c-quotes-stripped-at-ends-only` | `scripts/check_workflows.py` | KILLED | 7 | `TestMutatingHttpClause::test_mutant_is_rejected_by_the_curl_wget_clause[image-http-quoted-method]`, `TestMutatingHttpClause::test_the_detector_flags_explicit_and_implied_methods[c""url -X PUT u]`, `TestMutatingHttpClause::test_the_detector_flags_explicit_and_implied_methods[curl -""X PUT u]` (+4 more) |
| `NC-R3-docker-image-push-reverted` | `scripts/check_workflows.py` | KILLED | 3 | `TestImagePublicationInvariants::test_mutant_is_rejected_with_the_image_tag[image-docker-image-push]`, `TestImagePublicationInvariants::test_publication_commands_outside_promote_main_fail[docker image push x]`, `TestRoundOneGuards::test_mutant_reports_its_specific_reason[image-docker-image-push]` |
| `NC-R4-smoke-regex-greedy` | `scripts/check_workflows.py` | KILLED | 2 | `TestImagePublicationInvariants::test_mutant_is_rejected_with_the_image_tag[image-skopeo-smoke-glued]`, `TestRoundOneGuards::test_mutant_reports_its_specific_reason[image-skopeo-smoke-glued]` |
| `D1-1-exempt-without-hash-r1` | `scripts/check_workflows.py` | KILLED | 4 | `TestMutatingHttpClause::test_mutant_is_rejected_by_the_curl_wget_clause[image-http-notify-curlrc]`, `TestMutatingHttpClause::test_mutant_is_rejected_by_the_curl_wget_clause[image-http-notify-put]`, `TestMutatingHttpClause::test_mutant_is_rejected_by_the_curl_wget_clause[image-http-notify-url-repointed]` (+1 more) |
| `D1-2-hash-over-run-block-only-r1` | `scripts/check_workflows.py` | KILLED | 3 | `TestMutatingHttpClause::test_mutant_is_rejected_by_the_curl_wget_clause[image-http-notify-curlrc]`, `TestMutatingHttpClause::test_mutant_is_rejected_by_the_curl_wget_clause[image-http-notify-url-repointed]`, `TestMutatingHttpClause::test_shell_and_defaults_are_scanned_and_never_exempt[step]` |
| `D1-3-implied-methods-removed-r1` | `scripts/check_workflows.py` | KILLED | 36 | `TestMutatingHttpClause::test_mutant_is_rejected_by_the_curl_wget_clause[image-http-attached-value]`, `TestMutatingHttpClause::test_mutant_is_rejected_by_the_curl_wget_clause[image-http-data-d]`, `TestMutatingHttpClause::test_mutant_is_rejected_by_the_curl_wget_clause[image-http-upload-T]` (+33 more) |
| `D1-4-shell-and-defaults-unscanned-r1` | `scripts/check_workflows.py` | KILLED | 3 | `TestMutatingHttpClause::test_mutant_is_rejected_by_the_curl_wget_clause[image-http-defaults-shell]`, `TestMutatingHttpClause::test_mutant_is_rejected_by_the_curl_wget_clause[image-http-step-shell]`, `TestMutatingHttpClause::test_shell_and_defaults_are_scanned_and_never_exempt[workflow]` |
| `NC-R6-old-detector-dropped` | `scripts/check_workflows.py` | KILLED | 24 | `TestNeverWeakerThan5d416e4::test_each_5d416e4_refusal_stays_flagged[A10]`, `TestNeverWeakerThan5d416e4::test_each_5d416e4_refusal_stays_flagged[A11]`, `TestNeverWeakerThan5d416e4::test_each_5d416e4_refusal_stays_flagged[A12]` (+21 more) |

**Consiliency/pmcp#472 controls.** These ran on `ai` at `f65cb6b`, inside one `systemd-run --user --scope -p MemoryMax=4G -p MemorySwapMax=0` scope, each on a scratch copy of `scripts/`, `tests/`, `.github/`, `.consiliency/` and `pyproject.toml`, with all of `tests/test_workflow_guards.py` run through the copy's `scripts/pytest_temp.py` (`-n 4`). The baseline (no edit) had only the two git-repository failures excluded above. The `-r2` rows and `NC-R1` to `NC-R6` re-apply the earlier controls to the new code; `NC-R2a` is re-expressed as "split at any `;`, `&` or `|` regardless of quotes", and `NC-R6` is killed by the stubbed-classifier floor test. `NC-R7c2-case-untracked` first ran (at `aef3aa1`) with an edit that did not compile (`1 error`), which is not a kill; the corrected edit is the one below. Edit definitions and the full failing node IDs are in `negctl-edits/` and `negctl-final.jsonl` beside the run's evidence.

| control | file | result | failing tests | examples |
|---|---|---|---|---|
| `D1-1-exempt-without-hash-r2` | `scripts/check_workflows.py` | KILLED | 4 | `TestMutatingHttpClause::test_mutant_is_rejected_by_the_curl_wget_clause[image-http-notify-curlrc]`, `TestMutatingHttpClause::test_mutant_is_rejected_by_the_curl_wget_clause[image-http-notify-put]`, `TestMutatingHttpClause::test_mutant_is_rejected_by_the_curl_wget_clause[image-http-notify-url-repointed]` (+1 more) |
| `D1-2-hash-over-run-block-only-r2` | `scripts/check_workflows.py` | KILLED | 3 | `TestMutatingHttpClause::test_mutant_is_rejected_by_the_curl_wget_clause[image-http-notify-curlrc]`, `TestMutatingHttpClause::test_mutant_is_rejected_by_the_curl_wget_clause[image-http-notify-url-repointed]`, `TestMutatingHttpClause::test_shell_and_defaults_are_scanned_and_never_exempt[step]` |
| `D1-3-implied-methods-removed-r2` | `scripts/check_workflows.py` | KILLED | 67 | `TestBashGrammar::test_deep_nesting_never_fails_open[12]`, `TestBashGrammar::test_deep_nesting_never_fails_open[60]`, `TestBashGrammar::test_each_write_is_flagged[H2]` (+64 more) |
| `D1-4-shell-and-defaults-unscanned-r2` | `scripts/check_workflows.py` | KILLED | 3 | `TestMutatingHttpClause::test_mutant_is_rejected_by_the_curl_wget_clause[image-http-defaults-shell]`, `TestMutatingHttpClause::test_mutant_is_rejected_by_the_curl_wget_clause[image-http-step-shell]`, `TestMutatingHttpClause::test_shell_and_defaults_are_scanned_and_never_exempt[workflow]` |
| `NC-BOUND-work-limit-fails-open` | `scripts/check_workflows.py` | KILLED | 6 | `TestBashGrammar::test_deep_nesting_never_fails_open[400]`, `TestBashGrammar::test_reaching_the_work_bound_counts_as_a_write`, `TestBashGrammar::test_the_work_is_bounded_and_fails_closed[arithmetic-lookahead]` (+3 more) |
| `NC-PS-powershell-clause-dropped` | `scripts/check_workflows.py` | KILLED | 7 | `TestImagePublicationInvariants::test_mutant_is_rejected_with_the_image_tag[image-pwsh-invoke-method]`, `TestImagePublicationInvariants::test_publication_commands_outside_promote_main_fail[Invoke-RestMethod -Method Put -Uri $env:URL -InFile f]`, `TestPowerShellWebCmdlets::test_every_powershell_step_is_read[job-defaults]` (+4 more) |
| `NC-R1-top-level-check-deleted` | `scripts/check_workflows.py` | KILLED | 7 | `TestImagePublicationInvariants::test_mutant_is_rejected_with_the_image_tag[image-gate-workflow-defaults]`, `TestImagePublicationInvariants::test_mutant_is_rejected_with_the_image_tag[image-gate-workflow-env]`, `TestRoundOneGuards::test_extra_top_level_keys_in_image_yml_are_refused[concurrency-value2]` (+4 more) |
| `NC-R2a-split-at-any-separator` | `scripts/check_workflows.py` | KILLED | 41 | `TestBashGrammar::test_deep_nesting_never_fails_open[12]`, `TestBashGrammar::test_deep_nesting_never_fails_open[60]`, `TestBashGrammar::test_each_write_is_flagged[H1]` (+38 more) |
| `NC-R2b-alphanumeric-only-groups` | `scripts/check_workflows.py` | KILLED | 51 | `TestBashGrammar::test_deep_nesting_never_fails_open[12]`, `TestBashGrammar::test_deep_nesting_never_fails_open[60]`, `TestBashGrammar::test_each_write_is_flagged[H2]` (+48 more) |
| `NC-R2c-quotes-stripped-at-ends-only` | `scripts/check_workflows.py` | KILLED | 4 | `TestBashGrammar::test_the_committed_tree_lexes_completely_and_only_notify_is_flagged`, `TestMutatingHttpClause::test_the_detector_flags_explicit_and_implied_methods[c""url -X PUT u]`, `TestMutatingHttpClause::test_the_detector_flags_explicit_and_implied_methods[curl --""data @f u]` (+1 more) |
| `NC-R3-docker-image-push-reverted` | `scripts/check_workflows.py` | KILLED | 5 | `TestBashGrammar::test_global_options_before_push_are_publication[docker -H unix:///x.sock image push x-docker push]`, `TestImagePublicationInvariants::test_mutant_is_rejected_with_the_image_tag[image-docker-image-push]`, `TestImagePublicationInvariants::test_publication_commands_outside_promote_main_fail[docker -H unix:///x.sock image push x]` (+2 more) |
| `NC-R4-smoke-regex-greedy` | `scripts/check_workflows.py` | KILLED | 8 | `TestBashGrammar::test_anything_glued_to_the_smoke_load_leaves_skopeo_refused["x";skopeo copy docker://example.invalid/a:t docker://example.invalid/b:t]`, `TestBashGrammar::test_anything_glued_to_the_smoke_load_leaves_skopeo_refused[$(skopeo copy docker://example.invalid/a:t docker://example.invalid/b:t)]`, `TestBashGrammar::test_anything_glued_to_the_smoke_load_leaves_skopeo_refused[`skopeo copy docker://example.invalid/a:t docker://example.invalid/b:t`]` (+5 more) |
| `NC-R6-old-detector-dropped` | `scripts/check_workflows.py` | KILLED | 1 | `TestNeverWeakerThan5d416e4::test_the_floor_is_still_wired_in_when_the_classifier_says_no` |
| `NC-R7a-comments-unhandled` | `scripts/check_workflows.py` | KILLED | 4 | `TestBashGrammar::test_each_write_is_flagged[H2]`, `TestBashGrammar::test_the_classifier_alone_flags_each_5d416e4_case[A6]`, `TestBashGrammar::test_the_classifier_alone_flags_each_5d416e4_case[A7]` (+1 more) |
| `NC-R7b2-heredoc-bodies-unread` | `scripts/check_workflows.py` | KILLED | 2 | `TestBashGrammar::test_each_write_is_flagged[H3]`, `TestBashGrammar::test_the_work_is_bounded_and_fails_closed[many-heredocs]` |
| `NC-R7b-substitutions-terminate` | `scripts/check_workflows.py` | KILLED | 17 | `TestBashGrammar::test_each_write_is_flagged[H1]`, `TestBashGrammar::test_each_write_is_flagged[H5]`, `TestBashGrammar::test_each_write_is_flagged[X12-arith-shift]` (+14 more) |
| `NC-R7c2-case-untracked` | `scripts/check_workflows.py` | KILLED | 1 | `TestBashGrammar::test_each_write_is_flagged[X2-case-paren-hides-args]` |
| `NC-R7c-redirection-ampersand-terminates` | `scripts/check_workflows.py` | KILLED | 4 | `TestBashGrammar::test_each_write_is_flagged[S1]`, `TestBashGrammar::test_each_write_is_flagged[S2]`, `TestBashGrammar::test_each_write_is_flagged[X10-fd-var-redirect]` (+1 more) |
| `NC-R7d-fail-open-depth-cap` | `scripts/check_workflows.py` | KILLED | 8 | `TestBashGrammar::test_deep_nesting_never_fails_open[12]`, `TestBashGrammar::test_deep_nesting_never_fails_open[400]`, `TestBashGrammar::test_deep_nesting_never_fails_open[60]` (+5 more) |
| `NC-R7e-quoted-parts-unclassified` | `scripts/check_workflows.py` | KILLED | 13 | `TestBashGrammar::test_each_write_is_flagged[H4-depth5-double]`, `TestBashGrammar::test_each_write_is_flagged[H4-depth5-single]`, `TestBashGrammar::test_each_write_is_flagged[H4-depth8-double]` (+10 more) |
| `NC-R7f-unlexable-fallback-dropped` | `scripts/check_workflows.py` | KILLED | 5 | `TestBashGrammar::test_text_bash_cannot_finish_reading_is_read_conservatively[X=$(curl -s "$URL?a&b" -d@report.json]`, `TestBashGrammar::test_text_bash_cannot_finish_reading_is_read_conservatively[X=`curl -s "$URL?a&b" -d@report.json]`, `TestBashGrammar::test_text_bash_cannot_finish_reading_is_read_conservatively[curl -s "$URL?a&b" ${X -d@report.json]` (+2 more) |
| `NC-R8a-short-group-stops-at-non-alnum` | `scripts/check_workflows.py` | KILLED | 2 | `TestBashGrammar::test_each_write_is_flagged[S3]`, `TestBashGrammar::test_each_write_is_flagged[S4]` |
| `NC-R8b-expand-rule-dropped` | `scripts/check_workflows.py` | KILLED | 6 | `TestBashGrammar::test_each_write_is_flagged[S10]`, `TestBashGrammar::test_each_write_is_flagged[S11]`, `TestBashGrammar::test_each_write_is_flagged[S7]` (+3 more) |
| `NC-R8d-wget-prefix-rule-dropped` | `scripts/check_workflows.py` | KILLED | 5 | `TestBashGrammar::test_each_write_is_flagged[S12]`, `TestBashGrammar::test_each_write_is_flagged[S13]`, `TestBashGrammar::test_each_write_is_flagged[S14]` (+2 more) |
| `NC-R8f-docker-pattern-reverted` | `scripts/check_workflows.py` | KILLED | 9 | `TestBashGrammar::test_global_options_before_push_are_publication[docker --config "$RUNNER_TEMP/cfg" push "$IMAGE_REF"-docker push]`, `TestBashGrammar::test_global_options_before_push_are_publication[docker --config=/x --debug image push x-docker push]`, `TestBashGrammar::test_global_options_before_push_are_publication[docker --tls push x-docker push]` (+6 more) |
| `NC-R9-smoke-operands-restored` | `scripts/check_workflows.py` | KILLED | 4 | `TestBashGrammar::test_anything_glued_to_the_smoke_load_leaves_skopeo_refused[$(skopeo copy docker://example.invalid/a:t docker://example.invalid/b:t)]`, `TestBashGrammar::test_anything_glued_to_the_smoke_load_leaves_skopeo_refused[`skopeo copy docker://example.invalid/a:t docker://example.invalid/b:t`]`, `TestImagePublicationInvariants::test_mutant_is_rejected_with_the_image_tag[image-skopeo-smoke-cmdsubst]` (+1 more) |

**PR Consiliency/pmcp#475 review fixes (F1-F3).** These ran on `ai` at `0d02cd3`, the same way (one 4 GiB scope, isolated `HOME`, scratch copies, all of `tests/test_workflow_guards.py`, 1,026 tests). The baseline had only the two git-repository failures. All 41 controls were killed: the 26 `#472` controls above (those whose anchor this change moved are re-expressed as `-r475` rows, same intent) and the 15 new ones below. At `732851b`, `NC-F2c-expansion-start-not-a-boundary` survived (no case had literal text before the expansion), so `F2-split-after-literal` (`env${IFS}curl ...`) was added; that run is superseded. The `NC-F3a`-`c` and `NC-FLOOR` rows restore each old quadratic regex, or drop the floor bound, and only the timing tests fail: the old and new answers are the same, and only the time differs. The live oracle at `0d02cd3` ran 141 texts (the 98 above, plus 32 F1/F2 writes, 7 reads and 4 accepted false positives) with no disagreement; every F1/F2 write sent POST or PUT, and the 5d416e4 floor misses each one.

| control | file | result | failing tests | examples |
|---|---|---|---|---|
| `NC-F1a-default-word-not-in-shape` | `scripts/check_workflows.py` | KILLED | 14 | `TestExpansionBuiltCommandWords::test_each_write_is_flagged[F1-default-glued]`, `TestExpansionBuiltCommandWords::test_each_write_is_flagged[F1-long-name-default]`, `TestExpansionBuiltCommandWords::test_the_classifier_alone_flags_each_write[F1-default-glued]` (+11 more) |
| `NC-F1b-hyphen-back-in-tool-word` | `scripts/check_workflows.py` | KILLED | 2 | `TestExpansionBuiltCommandWords::test_the_unlexable_fallback_reads_the_same_class[${X-curl} -s -d@report.json "$URL" "oops]`, `TestExpansionBuiltCommandWords::test_the_unlexable_fallback_reads_the_same_class[${X:-curl} -s -d@report.json "$URL" "oops]` |
| `NC-F1c-value-operators-empty` | `scripts/check_workflows.py` | KILLED | 14 | `TestExpansionBuiltCommandWords::test_each_write_is_flagged[F1-default-glued]`, `TestExpansionBuiltCommandWords::test_each_write_is_flagged[F1-long-name-default]`, `TestExpansionBuiltCommandWords::test_the_classifier_alone_flags_each_write[F1-default-glued]` (+11 more) |
| `NC-F2a-shape-rule-dropped` | `scripts/check_workflows.py` | KILLED | 29 | `TestExpansionBuiltCommandWords::test_each_accepted_false_positive_is_flagged[FX-quoted-glue-no-split]`, `TestExpansionBuiltCommandWords::test_each_write_is_flagged[F1-default-glued]`, `TestExpansionBuiltCommandWords::test_each_write_is_flagged[F1-long-name-default]` (+26 more) |
| `NC-F2b-word-arguments-dropped` | `scripts/check_workflows.py` | KILLED | 9 | `TestExpansionBuiltCommandWords::test_each_accepted_false_positive_is_flagged[FX-quoted-glue-no-split]`, `TestExpansionBuiltCommandWords::test_each_write_is_flagged[F2-ifs-braced]`, `TestExpansionBuiltCommandWords::test_each_write_is_flagged[F2-ifs-method]` (+6 more) |
| `NC-F2c-expansion-start-not-a-boundary` | `scripts/check_workflows.py` | KILLED | 2 | `TestExpansionBuiltCommandWords::test_each_write_is_flagged[F2-split-after-literal]`, `TestExpansionBuiltCommandWords::test_the_classifier_alone_flags_each_write[F2-split-after-literal]` |
| `NC-F2d-fallback-split-dropped` | `scripts/check_workflows.py` | KILLED | 1 | `TestExpansionBuiltCommandWords::test_the_unlexable_fallback_reads_the_same_class[curl$IFS-d@report.json$IFS"$URL" "oops]` |
| `NC-F3a-docker-regex-restored` | `scripts/check_workflows.py` | KILLED | 2 | `TestLinearPublicationScans::test_a_hostile_line_is_scanned_in_linear_time[docker push]`, `TestLinearPublicationScans::test_the_checker_scans_a_hostile_step_in_bounded_time[docker push]` |
| `NC-F3b-type-image-regex-restored` | `scripts/check_workflows.py` | KILLED | 1 | `TestLinearPublicationScans::test_a_hostile_line_is_scanned_in_linear_time[a pushing type=image output]` |
| `NC-F3c-skopeo-delete-regex-restored` | `scripts/check_workflows.py` | KILLED | 1 | `TestLinearPublicationScans::test_a_hostile_skopeo_line_is_scanned_in_linear_time` |
| `NC-F3d-quoted-values-dropped` | `scripts/check_workflows.py` | KILLED | 8 | `TestBashGrammar::test_global_options_before_push_are_publication[docker --config "$RUNNER_TEMP/cfg" push "$IMAGE_REF"-docker push]`, `TestImagePublicationInvariants::test_mutant_is_rejected_with_the_image_tag[image-docker-global-option-push]`, `TestImagePublicationInvariants::test_publication_commands_outside_promote_main_fail[docker --config "$RUNNER_TEMP/cfg" push "$IMAGE_REF"]` (+5 more) |
| `NC-F3e-line-pair-first-line-only` | `scripts/check_workflows.py` | KILLED | 2 | `TestLinearPublicationScans::test_each_scanner_answers_as_the_old_regex[a pushing type=image output]`, `TestLinearPublicationScans::test_skopeo_delete_answers_as_the_old_regex` |
| `NC-F3f-image-push-dropped` | `scripts/check_workflows.py` | KILLED | 9 | `TestBashGrammar::test_global_options_before_push_are_publication[docker -H unix:///x.sock image push x-docker push]`, `TestImagePublicationInvariants::test_mutant_is_rejected_with_the_image_tag[image-docker-image-push]`, `TestImagePublicationInvariants::test_publication_commands_outside_promote_main_fail[docker -H unix:///x.sock image push x]` (+6 more) |
| `NC-F3g-value-needs-no-blank` | `scripts/check_workflows.py` | KILLED | 2 | `TestLinearPublicationScans::test_each_scanner_answers_as_the_old_regex[docker push]`, `TestLinearPublicationScans::test_the_scanners_refuse_what_the_regex_refused[docker push-docker -x "a"b push]` |
| `NC-FLOOR-input-bound-dropped` | `scripts/check_workflows.py` | KILLED | 2 | `TestFloorInputBound::test_a_token_over_the_limit_flags_without_running_the_floor`, `TestFloorInputBound::test_the_floor_input_is_bounded[one-long-token]` |
| `NC-R2a-split-at-any-separator-r475` | `scripts/check_workflows.py` | KILLED | 41 | `TestBashGrammar::test_deep_nesting_never_fails_open[12]`, `TestBashGrammar::test_deep_nesting_never_fails_open[60]`, `TestBashGrammar::test_each_write_is_flagged[H1]` (+38 more) |
| `NC-R2c-quotes-stripped-at-ends-only-r475` | `scripts/check_workflows.py` | KILLED | 4 | `TestBashGrammar::test_the_committed_tree_lexes_completely_and_only_notify_is_flagged`, `TestMutatingHttpClause::test_the_detector_flags_explicit_and_implied_methods[c""url -X PUT u]`, `TestMutatingHttpClause::test_the_detector_flags_explicit_and_implied_methods[curl --""data @f u]` (+1 more) |
| `NC-R3-docker-image-push-reverted-r475` | `scripts/check_workflows.py` | KILLED | 7 | `TestBashGrammar::test_global_options_before_push_are_publication[docker -H unix:///x.sock image push x-docker push]`, `TestImagePublicationInvariants::test_mutant_is_rejected_with_the_image_tag[image-docker-image-push]`, `TestImagePublicationInvariants::test_publication_commands_outside_promote_main_fail[docker -H unix:///x.sock image push x]` (+4 more) |
| `NC-R6-old-detector-dropped-r475` | `scripts/check_workflows.py` | KILLED | 1 | `TestNeverWeakerThan5d416e4::test_the_floor_is_still_wired_in_when_the_classifier_says_no` |
| `NC-R7e-quoted-parts-unclassified-r475` | `scripts/check_workflows.py` | KILLED | 13 | `TestBashGrammar::test_each_write_is_flagged[H4-depth5-double]`, `TestBashGrammar::test_each_write_is_flagged[H4-depth5-single]`, `TestBashGrammar::test_each_write_is_flagged[H4-depth8-double]` (+10 more) |
| `NC-R8f-docker-pattern-reverted-r475` | `scripts/check_workflows.py` | KILLED | 25 | `TestBashGrammar::test_global_options_before_push_are_publication[buildah --root /x push x-buildah push]`, `TestBashGrammar::test_global_options_before_push_are_publication[docker --config "$RUNNER_TEMP/cfg" push "$IMAGE_REF"-docker push]`, `TestBashGrammar::test_global_options_before_push_are_publication[docker --config=/x --debug image push x-docker push]` (+22 more) |

### Parameter quoting and brace expansion (PR Consiliency/pmcp#475, F4/F5)

Code head `aeb02d5593e64b811f746fd7e7f6d3db2b504d8a` adds two readings while
preserving the original classifier and the byte-identical 5d416e4 floor:

- Parameter operands decode ANSI-C and locale quotes under bash's default
  `extquote`, including inside an outer double quote. Nested parameters inherit
  the quoting context; pattern-substitution operands allow their own quotes.
- Unquoted brace lists and integer/alphabetic sequences are expanded in order,
  including nesting, adjacent products, signed endpoints/steps and zero padding.
  The generated command is classified in addition to the original. Quotes and
  parameter expansions are skipped as units during brace expansion.
- Source copies, rescans, Cartesian products and range output spend the shared
  work budget **before** allocating. Exhaustion and stack-depth limits refuse.
  Alphabetic ranges producing a backslash or backtick are explicitly refused:
  bash processes those generated bytes differently from reparsed source. This
  is an accepted false positive even in a read-only command such as `echo {Z..b}`.
  Classifying quoted/nested text as possible commands retains the existing
  conservative behavior; this is not a general-purpose shell evaluator.

Validation ran detached on `ai`, in a systemd scope with `MemoryMax=4G`,
`MemorySwapMax=0`, `nice -n 10`, an isolated process HOME before imports, and
prepared Python 3.10.22 through `scripts/pytest_temp.py`. No guard/parser tests
or probes ran on dev0. At this code head:

- Guard tests: **1061 passed** (exit 0); coverage mode: **1061 passed**, but the
  command **exited 1** because measured coverage was 0%, below the 60% threshold.
  The single-file coverage run is not an overall passing check.
- Five focused files: **1362 passed** (exit 0).
- Live loopback oracle: **171 texts, no disagreements**, including the original
  F4/F5 examples, nested parameter quotes, brace-built arguments, reads and the
  previously declared false positives.
- Brace-word oracle: **204 texts**; **187 comparisons equal bash**, and **17
  explicit fail-closed refusals** for generated quoting syntax. The comparison
  disables brace expansion when evaluating generated words, so an unexpanded
  brace cannot pass accidentally by being expanded again by bash.
- Ruff check, CI-form formatting, mypy and workflow checks with/without a base
  pass. Repository-wide format still flags only the two unchanged
  `diagnostics/issue-79-1b` files.
- Mutation loop: **170/170 rows as expected**. Negative controls: **47/47 killed**;
  baseline has only the two expected Git-context failures in scratch copies.
- The old floor and its referenced constants are identical; the committed tree
  still flags only `notify-worker`; all eight issue-specific mutants still pass
  main and are refused here.

The six new negative controls are all killed by non-baseline assertions:

| control removed | non-baseline failing tests |
|---|---:|
| parameter operand extquote | 5 |
| nested parameter quote context | 1 |
| substitution operand quote removal | 1 |
| brace-expanded command classification | 9 |
| refusal of generated quoting syntax | 3 |
| sequence expansion | 11 |

The complete battery receipt, commands, JUnit, live tables and control edits are
retained under `/home/viperjuice/workspace/tmp/pmcp-475-f4-f5-root/` and on `ai`
under `/tmp/pmcp-ai-viperjuice/475-f45-aeb02d5/`.
Three superseded batteries (`1b3e2a2`, `432e719`, `88297c7`) were stopped by the
coordinator while revising code or oracle inputs: **interrupted, no result**.
Their logs and printed scratch paths remain at the corresponding ai run paths,
with `INVALID-SUPERSEDED.txt`; none is final validation evidence.

The cross-vendor panel has **not** approved this change. The approved 0.7.27
attempt at `31b31a9` refused composition before any seat launched because the
vendor wrapper dependencies were not mounted. Its receipt is at
`/home/viperjuice/workspace/boards/pmcp/475/board1-jenner-31b31a9/RECEIPT.md`;
agent-harness issue 1774 tracks the dependency-admission problem. F4/F5 still
need independent review at the new head. No publication activation is authorized.

### Mutation loop

The extended loop (`mutation-189.md:18-26`; a failed apply or commit is a failure, each exit must equal `--list`'s expected column, every expected tag must appear, non-zero after cleanup on any mismatch) ran on `ai` in a scratch worktree at `f65cb6bad6453458096194052e29ee792fae3170` and again, in a scratch clone, at `0d02cd30ec3785bba74e9bf53ef93b6a6ea9a98e` (PR Consiliency/pmcp#475 fixes; same 170/170), inside the same 4 GiB scope, with `PYTHONDONTWRITEBYTECODE=1` and the prepared `.venv` interpreter: **170/170 rows as expected**. That is 169 `--list` mutants plus the unmutated tree (exit 0). It includes all 86 `[image]` mutants (exit 1 with their tags; `image-docker-tag-push` also reports `[release]`), the reordered-`needs` equivalent form (exit 0) and the six re-anchored timeout mutants. Consiliency/pmcp#472 added 8 mutants: the 7 the issue names (168 mutants, 169 rows) and `image-pwsh-invoke-method`, which only the PowerShell clause kills. Each of the 8 passes the `main` checker at `2a13d47` and is refused by this one with its specific reason.

| mutant | expected | exit | tags | result |
|---|---|---|---|---|
| `(none: unmutated tree)` | 0 | 0 | - | PASS |
| `tag-case` | 1 | 1 | [release] | PASS |
| `tags-deleted` | 1 | 1 | [release] | PASS |
| `trigger-pull-request-added` | 1 | 1 | [release] | PASS |
| `trigger-push-branches-added` | 1 | 1 | [release] | PASS |
| `trigger-workflow-dispatch-added` | 1 | 1 | [release] | PASS |
| `trigger-paths-filter-added` | 1 | 1 | [release] | PASS |
| `env-dropped` | 1 | 1 | [release] | PASS |
| `env-renamed` | 1 | 1 | [release] | PASS |
| `needs-build-dropped` | 1 | 1 | [release] | PASS |
| `needs-publish-to-build` | 1 | 1 | [release] | PASS |
| `continue-on-error-job` | 1 | 1 | [release] | PASS |
| `continue-on-error-step` | 1 | 1 | [release] | PASS |
| `if-on-build-job` | 1 | 1 | [release] | PASS |
| `continue-on-error-build-job` | 1 | 1 | [release] | PASS |
| `new-tag-triggered-workflow` | 1 | 1 | [release] | PASS |
| `if-on-publish-job` | 1 | 1 | [release] | PASS |
| `if-on-publish-step` | 1 | 1 | [release] | PASS |
| `forked-action` | 1 | 1 | [release] | PASS |
| `permissions-job-widened` | 1 | 1 | [release] | PASS |
| `permissions-workflow-level` | 1 | 1 | [release] | PASS |
| `job-added` | 1 | 1 | [release] | PASS |
| `job-deleted` | 1 | 1 | [release] [drift] | PASS |
| `file-deleted` | 1 | 1 | [release] [drift] | PASS |
| `timeout-360` | 1 | 1 | [timeout] | PASS |
| `timeout-1` | 1 | 1 | [timeout] | PASS |
| `timeout-string` | 1 | 1 | [timeout] | PASS |
| `timeout-bool` | 1 | 1 | [timeout] | PASS |
| `timeout-deleted` | 1 | 1 | [timeout] | PASS |
| `maintenance-deleted` | 1 | 1 | [drift] | PASS |
| `changelog-job-deleted` | 1 | 1 | [drift] | PASS |
| `workflows-job-deleted` | 1 | 1 | [drift] | PASS |
| `tag-pinned-action` | 1 | 1 | [pin] | PASS |
| `sha-comment-dropped` | 1 | 1 | [pin] | PASS |
| `sha-moved` | 1 | 1 | [release] | PASS |
| `pypa-rolled-back` | 1 | 1 | [release] | PASS |
| `composite-tag-pinned` | 1 | 1 | [pin] | PASS |
| `uses-quoted-key` | 1 | 1 | [pin] | PASS |
| `needs-as-list` | 0 | 0 | - | PASS |
| `timeout-below-p100` | 0 | 0 | - | PASS |
| `concurrency-added` | 0 | 0 | - | PASS |
| `guard-self-disabled` | 1 | 1 | [ci-gate] | PASS |
| `guard-self-disabled-nonconstant` | 1 | 1 | [ci-gate] | PASS |
| `guard-step-gutted` | 0 | 0 | - | PASS |
| `bootstrap-suite-decoy` | 1 | 1 | [ci-gate] | PASS |
| `bootstrap-selected-shell` | 1 | 1 | [ci-gate] | PASS |
| `release-suite-removed` | 1 | 1 | [release] | PASS |
| `release-coverage-drift` | 1 | 1 | [release] | PASS |
| `release-audit-removed` | 1 | 1 | [release] | PASS |
| `release-node-removed` | 1 | 1 | [release] | PASS |
| `release-xml-root` | 1 | 1 | [release] | PASS |
| `release-history-shallow` | 1 | 1 | [release] | PASS |
| `release-suite-env-filter` | 1 | 1 | [release] | PASS |
| `release-bundle-hidden` | 1 | 1 | [release] | PASS |
| `release-verifier-env` | 1 | 1 | [release] | PASS |
| `release-notes-mutated` | 1 | 1 | [release] | PASS |
| `release-artifact-unverified` | 1 | 1 | [release] | PASS |
| `release-artifact-path-widened` | 1 | 1 | [release] | PASS |
| `diagnostics-upload-raw` | 1 | 1 | [diagnostics] | PASS |
| `diagnostics-upload-ungated` | 1 | 1 | [diagnostics] | PASS |
| `diagnostics-sanitize-not-always` | 1 | 1 | [diagnostics] | PASS |
| `diagnostics-retention` | 1 | 1 | [diagnostics] | PASS |
| `scope-manufactures-asset` | 1 | 1 | [ci-gate] | PASS |
| `gate-no-always` | 1 | 1 | [ci-gate] | PASS |
| `gate-missing-audit` | 1 | 1 | [ci-gate] | PASS |
| `test-needs-before-protection` | 1 | 1 | [ci-gate] | PASS |
| `gate-static-needs` | 1 | 1 | [ci-gate] | PASS |
| `gate-step-false` | 1 | 1 | [ci-gate] | PASS |
| `gate-shell-override` | 1 | 1 | [ci-gate] | PASS |
| `scope-output-rebound` | 1 | 1 | [ci-gate] | PASS |
| `scope-step-id` | 1 | 1 | [ci-gate] | PASS |
| `scope-force-full-unbound` | 1 | 1 | [ci-gate] | PASS |
| `gate-job-name` | 1 | 1 | [ci-gate] | PASS |
| `gate-matrix` | 1 | 1 | [ci-gate] | PASS |
| `workflow-shell-default` | 1 | 1 | [ci-gate] | PASS |
| `audit-step-skipped` | 1 | 1 | [ci-gate] | PASS |
| `audit-step-best-effort` | 1 | 1 | [ci-gate] | PASS |
| `audit-command-omitted` | 1 | 1 | [ci-gate] | PASS |
| `test-path-filtered` | 1 | 1 | [ci-gate] | PASS |
| `gate-context-collision` | 1 | 1 | [ci-gate] | PASS |
| `gate-context-cross-workflow` | 1 | 1 | [ci-gate] | PASS |
| `integration-cancelled` | 1 | 1 | [concurrency] | PASS |
| `integration-shared-group` | 1 | 1 | [concurrency] | PASS |
| `image-docker-main-push` | 1 | 1 | [image] | PASS |
| `image-docker-tag-push` | 1 | 1 | [image] [release] | PASS |
| `image-docker-pull-request-target` | 1 | 1 | [image] | PASS |
| `image-docker-push-expression` | 1 | 1 | [image] | PASS |
| `image-docker-packages-write` | 1 | 1 | [image] | PASS |
| `image-docker-login` | 1 | 1 | [image] | PASS |
| `image-docker-cache-to` | 1 | 1 | [image] | PASS |
| `image-docker-secrets` | 1 | 1 | [image] | PASS |
| `image-docker-extra-job` | 1 | 1 | [image] | PASS |
| `image-tag-allowlist-docker` | 1 | 1 | [image] | PASS |
| `image-workflow-packages-write` | 1 | 1 | [image] | PASS |
| `image-job-write-all` | 1 | 1 | [image] | PASS |
| `image-promote-contents-dropped` | 1 | 1 | [image] | PASS |
| `image-watch-packages-write` | 1 | 1 | [image] | PASS |
| `image-job-no-permissions-image` | 1 | 1 | [image] | PASS |
| `image-job-no-permissions-watch` | 1 | 1 | [image] | PASS |
| `image-job-no-permissions-docker` | 1 | 1 | [image] | PASS |
| `image-docker-push-step` | 1 | 1 | [image] | PASS |
| `image-buildx-push` | 1 | 1 | [image] | PASS |
| `image-type-registry` | 1 | 1 | [image] | PASS |
| `image-curl-put` | 1 | 1 | [image] | PASS |
| `image-skopeo-delete` | 1 | 1 | [image] | PASS |
| `image-crane` | 1 | 1 | [image] | PASS |
| `image-oras` | 1 | 1 | [image] | PASS |
| `image-regctl` | 1 | 1 | [image] | PASS |
| `image-podman-push` | 1 | 1 | [image] | PASS |
| `image-buildah-push` | 1 | 1 | [image] | PASS |
| `image-promote-outside` | 1 | 1 | [image] | PASS |
| `image-stray-ghcr` | 1 | 1 | [image] | PASS |
| `image-promote-if-vars` | 1 | 1 | [image] | PASS |
| `image-needs-gate-dropped` | 1 | 1 | [image] | PASS |
| `image-needs-verify-dropped` | 1 | 1 | [image] | PASS |
| `image-gate-lower` | 1 | 1 | [image] | PASS |
| `image-gate-literal-output` | 1 | 1 | [image] | PASS |
| `image-gate-extra-step` | 1 | 1 | [image] | PASS |
| `image-gate-literal-env` | 1 | 1 | [image] | PASS |
| `image-gate-if` | 1 | 1 | [image] | PASS |
| `image-candidate-ref-head-sha` | 1 | 1 | [image] | PASS |
| `image-persist-credentials` | 1 | 1 | [image] | PASS |
| `image-trusted-ref-main` | 1 | 1 | [image] | PASS |
| `image-cone-mode` | 1 | 1 | [image] | PASS |
| `image-watch-baselines-dropped` | 1 | 1 | [image] | PASS |
| `image-gate-checkout` | 1 | 1 | [image] | PASS |
| `image-git-clone` | 1 | 1 | [image] | PASS |
| `image-curl-contents` | 1 | 1 | [image] | PASS |
| `image-run-head-sha` | 1 | 1 | [image] | PASS |
| `image-download-run-id` | 1 | 1 | [image] | PASS |
| `image-cancel-in-progress` | 1 | 1 | [image] | PASS |
| `image-trigger-push-tags` | 1 | 1 | [image] | PASS |
| `image-trigger-workflow-call` | 1 | 1 | [image] | PASS |
| `image-trigger-schedule` | 1 | 1 | [image] | PASS |
| `image-trigger-repository-dispatch` | 1 | 1 | [image] | PASS |
| `image-trigger-workflow-dispatch` | 1 | 1 | [image] | PASS |
| `image-types-requested` | 1 | 1 | [image] | PASS |
| `image-branches-removed` | 1 | 1 | [image] | PASS |
| `image-tag-caller` | 1 | 1 | [image] | PASS |
| `image-watch-push` | 1 | 1 | [image] | PASS |
| `image-watch-pull-request` | 1 | 1 | [image] | PASS |
| `image-ack-docker` | 1 | 1 | [image] | PASS |
| `image-ack-image` | 1 | 1 | [image] | PASS |
| `image-ack-dockerfile` | 1 | 1 | [image] | PASS |
| `image-ack-baselines` | 1 | 1 | [image] | PASS |
| `image-http-notify-put` | 1 | 1 | [image] | PASS |
| `image-http-notify-url-repointed` | 1 | 1 | [image] | PASS |
| `image-http-notify-curlrc` | 1 | 1 | [image] | PASS |
| `image-http-notify-copied` | 1 | 1 | [image] | PASS |
| `image-http-upload-T` | 1 | 1 | [image] | PASS |
| `image-http-data-d` | 1 | 1 | [image] | PASS |
| `image-http-wget-post-file` | 1 | 1 | [image] | PASS |
| `image-http-step-shell` | 1 | 1 | [image] | PASS |
| `image-http-defaults-shell` | 1 | 1 | [image] | PASS |
| `image-gate-workflow-env` | 1 | 1 | [image] | PASS |
| `image-gate-workflow-defaults` | 1 | 1 | [image] | PASS |
| `image-http-quoted-separator` | 1 | 1 | [image] | PASS |
| `image-http-attached-value` | 1 | 1 | [image] | PASS |
| `image-http-quoted-method` | 1 | 1 | [image] | PASS |
| `image-docker-image-push` | 1 | 1 | [image] | PASS |
| `image-skopeo-smoke-glued` | 1 | 1 | [image] | PASS |
| `image-http-comment-cmdsubst` | 1 | 1 | [image] | PASS |
| `image-http-arith-mid` | 1 | 1 | [image] | PASS |
| `image-http-prefix-hidden` | 1 | 1 | [image] | PASS |
| `image-http-redirect-before-method` | 1 | 1 | [image] | PASS |
| `image-http-expand-request` | 1 | 1 | [image] | PASS |
| `image-docker-global-option-push` | 1 | 1 | [image] | PASS |
| `image-skopeo-smoke-cmdsubst` | 1 | 1 | [image] | PASS |
| `image-pwsh-invoke-method` | 1 | 1 | [image] | PASS |
| `image-needs-reordered` | 0 | 0 | - | PASS |

## 7. Owner procedure, from before merge through activation (OUT OF SCOPE for this PR)

Nothing here is authorized by this plan. Every stable discrepancy halts and invokes D3. A `complete: false` (including
bounded instability or a readback order violation) halts the step, which is retaken; by itself it is not a D3 event.
1. **Pre-merge readbacks** (GET only): repo and org `PMCP_IMAGE_PUBLISH` are unset (an org variable applies when the
   repo one is unset), and `actions/permissions/workflow` reports `default_workflow_permissions == read`. Record `GET repos/Consiliency/pmcp/actions/permissions/artifact-and-log-retention` (admin read) in the T3 retention-file format; steps 7.5 and 7.6 use this record, and 7.9 takes a fresh one.
2. **Pre-merge baselines**, taken as late as possible with main frozen until G merges: first a stable, complete
   inventory `B0`, then, only after B0 is stable, a complete registry readback `R0`. Both are produced by the G head's
   scripts from a read-only detached checkout of the pushed PR head, so the new fields exist. Before applying the label, confirm that `R0.started_at` is after `B0.finished_at` and retake R0 if it is not; R0 cannot be retaken after the merge. The owner applies
   `release-change-approved` after the final push (the label is not head-scoped), then merges.
3. **Record** the G merge SHA and merge timestamp `T`.
4. **Standing prohibitions**, permanent (they do not expire at activation): no deletion of Docker runs; no rerun of any
   pre-G run of 219424499; no `v*` tag on a commit whose `docker.yml` still has `on.push`; release tags only on commits
   containing E, F and G. An unexplained missing run ID is a detection event.
5. **First post-merge diff:** `--mode pre-merge --baseline B0 --merge-time T --retention-file <7.1 record>` must show no stop; its `expired_ids` are kept with the evidence, and step 6's diff against B0 passes the same flag.
6. **Drain:** repeat the whole inventory until it is stable, complete, exit 0 and has an empty `non_pr_not_completed`.
   Outstanding runs go through the owner; tooling cancels nothing. Keep the result as `B1`, and take R1 only after it.
   B1 must pass `drained` acceptance and diff against B0 as in step 5.
7. **Post-drain readback** `R1 --mode drain --baseline R0` (with B0 and B1): T4 contract 1. The 2026-10-19 226-tag
   observation is history only.
8. **Disposable test-repo rehearsal** (local prototypes are not certification): an old-definition run across the
   cutover; the buildx OCI round trip, smoke, digest and race; "Re-run failed jobs" after a mid-promote failure, reusing
   the artifact within its 1-day retention and ending identical; a late legacy write before activation; and a late
   legacy write after the first successful promotion and readback, which `image-watch.yml` must detect.
9. **Before enabling:** repeat step 1, including a fresh retention record. Refresh the drain baseline: take a stable, complete inventory that passes `drained` acceptance and compare it with `--mode drained --baseline B1 --retention-file <record>`; any stop invokes D3; from here on this inventory is B1. Immediately afterwards, take the final stable inventory `--mode drained --baseline B1` without `--retention-file` (#443 item 3): every missing ID stops and invokes D3, even one older than the horizon, and after the D3 issue records such a stop the owner may repeat this step from the refresh. Then read back `R2 --mode pre-enable --baseline R1` (contract 2); commit B1, R2 and the record as `inventory-B1.json`, `registry-R2.json` and `retention.json` under `.consiliency/evidence/image-baselines/` by an owner-merged PR; bind the exact source, review and CI.
10. **Enable:** the owner sets `PMCP_IMAGE_PUBLISH=enabled` (a settings change). That act is the owner's authorization
    to move `main` and create `sha-<40hex>` tags, and nothing else: no `3.0.0`, `3.0`, `latest`, version or 7-hex
    writes. The owner records who receives `image-watch.yml` failure notifications.
11. **Post-enable:** read back the first promotion. From then on, D3 follows the 5.3 outcome rule (every `DISCREPANCY` run and the second consecutive scheduled `INCOMPLETE`) and every unexpected digest; a single `INCOMPLETE` is retaken by a `workflow_dispatch` run on `main`, without D3. At every release cut and owner review, confirm that the latest `schedule` run of `image-watch.yml` on `main` is at most 8 days old (5.3 liveness; dispatch runs do not count), and repeat the step-1 retention read; if `days` changed, refresh `retention.json` by an owner PR.
12. **Tag mode:** a tag caller needs its own reviewed PR, which inherits D1 and adds no alias movement.

### Four-vendor review corrections (PR Consiliency/pmcp#475)

At `9a9cfb6`, Claude, Codex and Grok returned DISAGREE. Gemini could not
start because the approved model had no server capacity; this is an incomplete
panel, not approval. The owner authorized T3-managed review orchestration only.

Code head `748fad0` addresses the consolidated findings:

- G1/C2/F-OPT: expanded argument words now get bounded join/split shape readings,
  including whole default operands, nested quotes, substitution replacements,
  and mixed joins and splits. Literal/original readings remain in the union.
  An unknown runtime value alone still remains AC1. Read options retain their
  negative controls. Candidate products are charged before allocation.
- C1: ANSI-C zero escapes truncate their quoted segment; text outside the quote
  still concatenates. Octal, hexadecimal, Unicode and control-zero spellings
  have direct and parameter-context tests. A NUL no longer aliases the internal
  expansion marker.
- G2/C3: static PowerShell command-object invocations and Unicode parameter
  dashes are recognized as described above. Unit tests cover GET, PUT and
  CustomMethod. No PowerShell runtime is installed on the validation hosts;
  these semantics remain specification-supported, not live-validated.
- Nested expansion slices and quoted joins charge before copying. Brace source
  spans are retained without copying until a command contains an unquoted brace
  candidate; the existing 10% committed-tree budget assertion stays unchanged.
  Process substitutions are skipped as units by the brace scan.

Accepted conservative refusals: curl 8.5 rejects `--request=PUT`, but an expanded
option with that spelling is flagged. Bash 5.2 consumes the remaining parameter
text after an ANSI-C NUL inside a double-quoted parameter; the guard retains the
outside-quote concatenation reading and flags it. The loopback oracle records
these nine cases explicitly as false positives, rather than claiming they sent
writes. Existing false positives and runtime residuals remain documented above.

The initial `7020580` and `5c5411c` runs completed with 1131 passed and one failed
committed-tree work-margin assertion. `5c5411c` also exposed the nine oracle
expectation differences just described. Neither run is cited as passing. At
`748fad0`, the initial guard run has 1132 passed and the loopback oracle has 241
texts with no disagreements after explicitly classifying those refusals.
Full-battery results follow separately; panel approval remains outstanding.

### Completed c9f7634 panel and grammar corrections

All four requested vendors completed the T3-managed panel at `c9f7634` and
returned **DISAGREE**. Claude N1, Codex C4, Grok F-REMAIN and Gemini GM-1 are the
same root cause: expansion boundaries in the tool word's remainder were lost.
Gemini additionally confirmed raw-line continuation, compound `case` and nested
arithmetic accept gaps (GM-2 through GM-4). PowerShell quote classes, escaped
static names and block comments were specification-supported findings; no live
PowerShell runtime was available. Full reports and probes are retained under
`/home/viperjuice/workspace/boards/pmcp/475/t3-panel-c9f7634/`.

The completed `748fad0` battery killed **53/54** negative controls. The survivor
was `NC-G1-substitution-boundary`, whose original pattern began with a dash and
allowed another conservative option reading to mask removal. The new regression
uses `X=old; curl "${X/#old/-d}" x "$URL"`; the control itself is retained.
That older battery is not reported as wholly passing.

Code head `c50af77` addresses the consolidated findings:

- The tool-word remainder retains its marker boundaries in an additional
  budgeted reading, alongside the historical all-split reading. The malformed
  text fallback likewise retains simple-expansion shapes.
- Raw Bash text reaches both `mutating_http` and the workflow entry point.
  `_joined` and the old floor stay byte-identical. Publication scanners also
  get an additional empty-removal reading of backslash-newline continuations,
  for steps and both levels of `defaults.run.shell`.
- `case` state survives the newline before `in` and recognizes compound-command
  prefixes, including `if`, `while` and `until` conditions. Arithmetic scanning
  skips quotes, escapes and nested substitutions before counting parentheses.
- PowerShell retains its original reading and adds normalized smart quotes,
  static backtick spellings and adjacent literal-string concatenations. Block
  comments end at the first `#>` and line comments do not expose quoted content
  as syntax. Explicit `$PSDefaultParameterValues`, `WebClient` and `HttpClient`
  references are conservatively refused, including read-only configuration or
  construction. These are documented false positives, not claims that every
  reference sends a request. Arbitrary runtime values/configuration still are
  not evaluated. The next review found further static gaps, addressed below;
  the runtime boundary requires the explicit adjudication recorded below.

PowerShell semantics were checked against Microsoft's [quoting rules](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_quoting_rules),
[comment rules](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_comments),
[default-parameter rules](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_parameters_default_values)
and [tokenizer source](https://github.com/PowerShell/PowerShell/blob/master/src/System.Management.Automation/engine/parser/tokenizer.cs).
They remain source-supported rather than live PowerShell evidence.

The early guard-only runs completed: `cc0ce69` had 1214 passed; `0e776ef` had
1231 passed. They are intermediate results, not final-head certification.
The driver initially launched for `0e776ef-full` retained an earlier evidence
path; it was stopped, its affected logs preserved, and the prior `748fad0`
evidence restored from its completed local archive. That run, and the later
`0e776ef-battery` and `09d023e` batteries superseded during final corrections,
are **interrupted, no result**. Their ai directories contain `INVALID.txt` and
logs/scratch receipts. Nothing from them is counted as a passing battery.

The completed `c50af77` battery recorded 1235 guard assertions, 1536 focused
assertions, 274 live texts with no disagreements, 216 brace words (199 matching
comparisons and 17 explicit refusals), and 170/170 mutation rows. Negative
controls killed 67/68; `NC-F2b-word-arguments-dropped` survived. The coverage
command exited 1 at 0% below 60 despite passing assertions. Whole-repository
formatting flagged two unchanged diagnostics scripts. This was not a wholly
passing battery. The completed archive is under
`/home/viperjuice/workspace/tmp/pmcp-475-grammar-fixes/validation/475-grammar-c50af77/`.

### 775d55f panel findings and prefix/PowerShell corrections

Claude, Codex and Grok returned **DISAGREE** at `775d55f`; Gemini failed before
review because its requested model had no capacity. This was an incomplete
panel. Reports are under `/home/viperjuice/workspace/boards/pmcp/475/t3-panel-775d55f/`.

Code `c9a8908` addresses the consolidated findings:

- K1/C7/G-CASE: compound prefixes have a linear parser for `time` options,
  `coproc` names and `function` names, instead of testing each preceding word
  against a bare-keyword list. Tests include the original live writes and GET
  controls, named coprocesses, one-line functions and hyphenated function names.
- P1/C8: PowerShell comments start only at token boundaries. A URL fragment or
  `a#b` inside a group no longer hides a later method. Real comments still do
  not expose their contents as syntax.
- P2/C9/P-SUBEXPR: a recognized HTTP command with a here-string or a nested
  subexpression inside an expandable string is conservatively refused. GET
  uses of these unsupported forms are explicit false positives; this is not
  a claim to evaluate their contents.
- P3/C10: normalized PowerShell readings also run through the native curl/wget
  classifier, so smart-quoted write options are recognized.
- P4/C11/P-SENTINEL/P-CONCAT: each normalized reading checks scoped/braced
  default-parameter variables and static .NET references. Literal name chains
  are assembled from whole quoted fragments and serialized with consistent
  quotes, including mixed-quote chains. `WebRequest`/`HttpWebRequest`,
  `Start-BitsTransfer`, and `Invoke-Expression`/`iex` are conservatively refused
  as well. These references can be read-only and remain documented false
  positives. The `Invoke-WebRequest` GET cmdlet is distinguished from the
  `WebRequest` type.

The [president ruling](475-powershell-scope-ruling.md), made against
`b324cee56843bbca2e5f81ef78c5ec4423bcb1cb`, accepts only the narrowed residual
under [Consiliency/pmcp#476](https://github.com/Consiliency/pmcp/issues/476)
and Consiliency/pmcp#472: unknown variables and data flow across statements
(including a literal assigned in an earlier statement), state established
outside the step, reflection on runtime-dependent targets, and indirect objects
of unknown/external origin. Examples are `& $cmd`, an imported function or
profile-established alias, and `$client.SendAsync(...)` where the object's type
comes from external code. Existing refusals always remain in the union.

Literal request APIs/constructors, in-step aliases/defaults, literal scriptblock
bodies, fixed evaluation entry points and literal format/join expressions are
excluded. Literal `-f`/`-join` and `[string]::Format/Join/Concat` must refuse in
invocation/type/member positions; they are not residual. The accepted boundary
carries to subsequent heads only without widening, and lapses if guard (c),
token defaults or secrets allow registry/package write credentials outside
`image.yml:promote-main`. Publication remains disabled until Consiliency/pmcp#472
is complete. The ruling is not implementation certification or panel approval.
This guard is **not a complete PowerShell evaluator**; no live PowerShell
validation is available, so its semantics remain source-supported/unit-tested.

The initial `fd27262` guard run had 1275 passed and two failed read controls;
`c9a8908` corrected the overbroad `WebRequest` name match and passed all 1277.
The first expanded live oracle used case names containing spaces in URL paths,
causing four invalid URLs. The corrected harness uses opaque URL keys and
completed 288 texts with no disagreements. The failed first log is retained;
only the corrected run is cited. Evidence is at
`/tmp/pmcp-ai-viperjuice/475-prefix-c9a8908/` (targeted and live-v2 logs).

The full battery at code `c9a8908` is running in
`/tmp/pmcp-ai-viperjuice/475-prefix-c9a8908-full/`, detached on ai under 4 GiB,
with isolated HOME and the prepared interpreter. Its 75 negative controls are
pending, not passing evidence. The previous surviving F2b control now removes
both the old split and new remainder argument paths, preserving its intent to
disable tool-word argument detection. The independent N1 control still removes
only the new remainder path. New controls remove comment-boundary handling,
here-string/subexpression refusal, native quote normalization, and each compound
prefix class. Scripts and archives are under
`/home/viperjuice/workspace/tmp/pmcp-475-prefix-fixes/`.

A new complete four-vendor review, explicit residual adjudication, completed
validation and exact-head CI remain required. No panel approval is asserted.

The `c9a8908` full battery subsequently completed: 170/170 mutation rows,
75/75 negative controls killed (plus the unchanged baseline), no surviving
control. The archived final log and JSONL are in the local validation directory
above. This completion does not certify later code or substitute for review.

At `7af323e`, Claude returned an incomplete review without a new reproducible
finding; it cannot count as a usable seat. Codex reported C12: a literal
`HttpMessageInvoker.SendAsync` request is accepted. The classifier result was
measured on ai; request semantics are supported by Microsoft's
[HttpMessageInvoker documentation](https://learn.microsoft.com/en-us/dotnet/api/system.net.http.httpmessageinvoker),
not by live PowerShell execution. Other seats remain outstanding.

Code `1c69b1c` conservatively refuses references to the `System.Net.Http`
namespace (including the `Net.Http` shorthand) and unqualified HTTP message,
invoker and handler types. It applies to every normalized reading. References
to GET methods, response types and construction without a send are deliberate
false positives. Tests cover C12 at the workflow entry and qualified,
unqualified, escaped and concatenated references. A new negative control
removes this API-family check. Validation is pending in
`/tmp/pmcp-ai-viperjuice/475-http-api-1c69b1c/` under the same isolated/capped
conditions. Consiliency/pmcp#476 now explicitly excludes omitted fixed literal
request primitives from automatic residual treatment; each needs a fix or an
individual ruling. The later president ruling below narrows and disposes of the evaluation boundary.

### Consolidated 7af323e objections and scope implementation

All four vendors delivered at `7af323e`, but Claude was explicitly incomplete;
Codex, Grok and Gemini returned DISAGREE. No complete usable panel exists for
that head. The president's narrow scope ruling is preserved verbatim above.

Code `5bdfdf4` consolidates C12, Grok P-BOUNDARY/P-GROUP and Gemini GM-NEW-1–3:

- Compound arithmetic uses the same budgeted prefix parser as `case`, retaining
  arithmetic `for` support. Parameter heads scan balanced subscripts, skipping
  nested expansions, quotes and escapes before locating the outer operator.
  Tests include every value operator and nested subscript class, plus GET reads.
- PowerShell recognizes boundaries after quotes/groups, continuations, commas
  and parameter colons. Braced variables are lexical units inside and outside
  expandable strings. Qualified command basenames consume their closing quote.
  Workflow checks retain both raw and historical joined PowerShell readings.
- Native curl/wget grouping and arrays get a budgeted additional argument
  reading, preserving quoted contents. `.exe` is a native executable, not a
  cmdlet alias. Original Bash readings and the exact maintenance pin remain.
- Per the ruling, format/join at invocation/type/member sites and static
  String.Format/Join/Concat calls refuse without evaluating expressions.
  Fixed scriptblock/evaluation APIs, in-step alias declarations and child
  PowerShell command/encoded-command forms also refuse conservatively.

Conservative false positives include GET/HEAD format/join invocations, string
formatting without a request, read-only aliases/evaluation, HTTP API namespace
references (including response-only types), recognized calls with here-strings
or expandable subexpressions, continued comments, and URL paths containing a
refused API name such as `WebRequest`. These are refusals, not live write claims.
The separate controls for format and join preserve the ruling's required
independence. Additional controls cover each new lexical/entry correction.

Completed intermediate checks: `1c69b1c` had 1289 guard passes, its C12 control
killed, mypy/workflows clean. `b324cee` had 1310 guard passes and all four new
boundary/group controls killed, mypy/workflows clean. The intervening `4509f26`
run failed 35 tests because the new native reading also classified the pinned
maintenance call and treated `.exe` as a cmdlet alias; those regressions were
corrected without altering the pin or existing read tests. That run's control
driver had malformed paths and ran no controls; it is not control evidence.
`1a3cb00` had 1406 guard passes and clean mypy/workflows. Its live launch lacked
`cases.py`, exited 1 without an oracle result, and is not passing evidence.

The complete code `5bdfdf4` battery is running at
`/tmp/pmcp-ai-viperjuice/475-final-5bdfdf4/` under the same detached 4 GiB cap,
isolated HOME and prepared interpreter. It includes the complete live harness
and 93 negative controls; completion is pending. Prior completed counts do not
certify this new head. The standalone guard coverage command and whole-tree
format command retain their disclosed failures; no waiver is asserted.

### 7e42288 review and CPU-budget corrections

Codex reported C13/C14 (operator token boundaries and invocation operands).
Grok reported P-FOR-PREFIX, P-TOKEN-QUOTE, P-RULING-FORM and P-NEWITEM.
Claude again returned an explicitly incomplete review, with no new reproducible
finding; it is not a usable seat. Gemini's attempt failed with server capacity
unavailable and delivered no review. These reports do not constitute panel approval.

Implementation `ea5252a` includes the following corrections:

- Format/join recognition accepts quote-adjacent operands and PowerShell's
  dash classes. Invocation-site recognition includes subexpression and quoted
  operands; bracket type expressions are included too.
- Arithmetic `for` headers retain their recognition after compound prefixes.
  Quoted parameter values begin a separate token. Command-adjacent quotes keep
  both opening-argument and closing-command readings under one work budget.
- Child PowerShell options receive the grouped/array native-argument reading.
  Static String member access is conservatively refused, including quoted or
  concatenated member selectors. Alias-provider item operations are refused.
  Read-only String static calls and alias-provider item operations are additional
  deliberate false positives. The narrowed scope ruling is unchanged.

Hosted Python 3.12 testing at `7e42288` failed two CPU-budget assertions:
`many-curl-words` took 37.9 seconds and `pwsh-many-words` 41.1 seconds, against
the unchanged 30-second ceiling. The run reported 2 failed, 14701 passed,
60 skipped and 1 xfailed. This is a failed resource check, not a wall-time
flake. The failed log is retained at
`/home/viperjuice/workspace/tmp/pmcp-475-prefix-fixes/ci-7e42288-failed.log`.

The corrected scanners charge 16 work units before each completed lexical
token allocation/dispatch. PowerShell returns and accumulates that work across
candidate commands and alternate quote readings. Excess work refuses. Empty
Bash word boundaries avoid allocating a new word. The 30-second CPU,
512-MiB memory and 60-second timeout assertions are unchanged; separate
negative controls remove each scanner's token charge.

An isolated Python 3.12 measurement at intermediate code `4508d8b` used
`time.process_time()` and `tracemalloc`, but not the full CI coverage setup.
Both hostile inputs refused: curl took 0.311 seconds with 2,353,686 traced
peak bytes, and PowerShell took 0.203 seconds with 1,288,790 bytes. These
measurements do not certify hosted CI or the later accounting adjustment.
Artifacts are in `/tmp/pmcp-ai-viperjuice/475-perf-4508d8b/` on ai.

The complete `ea5252a` run is at
`/tmp/pmcp-ai-viperjuice/475-final-ea5252a/` on ai, detached under 4 GiB with
isolated HOME and the prepared Python 3.10 interpreter. Completed stages:
1543 guard tests, 1844 focused tests, 300 live-oracle texts with no
disagreements, and 216 brace-oracle words (199 comparisons and 17 explicit
refusals). Ruff, CI-form formatting, single-file mypy, workflow checks and the
old detector identity check pass. The standalone guard coverage invocation
still exits 1 at 0% against 60%, despite 1543 passing assertions; whole-tree
formatting retains its two disclosed diagnostics-file failures.

The mutation loop and 103 negative controls are still running, not passing
evidence. Durable driver/control sources are at
`/home/viperjuice/workspace/tmp/pmcp-475-review-7e-fixes/`.
Completed validation, reconciliation of the outstanding review, a complete
usable panel and exact-head hosted CI remain required before merge.

### Consolidated b42f1f9 review and validation

Claude delivered a complete native Opus 5.5 review at `b42f1f9`. Claude,
Codex and Grok all disagreed; Gemini failed for server capacity and supplied
no review. None of these results is panel approval.

Code `bd4babb` incorporates C15/C16, CL-1–7 and Grok P-ADJACENT,
P-CALL-CONT, P-STRING-ASM and P-ITEM-ALIAS:

- Bash finishes a word at `(` before recognizing a compound arithmetic
  command/header. This covers adjacent keywords, negation and time options,
  including prefixed `for`, with spaced and read controls retained.
- PowerShell operator boundaries consume letters rather than Python word
  characters, retaining numeric operands and the dash classes. Invocation
  sites admit comments and continuations and exclude `&&` as a call operator.
  Fixed expression operands with write-shaped tails refuse without evaluating
  their names; parenthesized GET method controls remain reads. Expandable
  invocation strings containing subexpressions refuse conservatively.
- Parameter terminators split before grouped arguments. Child command switches
  recognize slash/doubled-dash forms, `ec`, `ea`, `cwa` and the existing names.
- Provider item spellings include their built-in aliases; content operations
  and alias-provider variables refuse too. Static Format/Join/Concat member
  spellings refuse through type objects and assembly-qualified types.
  PowerShell type references and AddCommand/AddScript/AddParameter APIs refuse.

The additional conservative refusals include read-only provider variables and
content operations, static Format/Join/Concat on non-String types, references to
the PowerShell API without execution, builder API references, and expandable
invocation-name subexpressions even with GET tails. These are refusals, not
live-write claims. The narrow scope ruling and its credential condition are
unchanged. There is still no live PowerShell validation.

Claude disputed the earlier P-TOKEN-QUOTE interpretation: the upstream tokenizer
may treat `-Method"Put"` as a positional argument and a command glued to a quoted
suffix as a different name. Retained extra readings only add refusals; those
texts are now characterized as conservative tests, not confirmed live writes.
The previously undisclosed `[ -f x ] && ...` false positive is corrected and
has explicit read controls.

Hosted `b42f1f9` testing passed the former many-curl/many-iwr CPU cases, but
failed `split-tool-words` at 30.6 seconds against the unchanged 30-second ceiling:
1 failed, 14815 passed, 60 skipped, 1 xfailed. The aggregate failed its test
dependency. The release acknowledgement label is also absent. Logs are retained
under `/home/viperjuice/workspace/tmp/pmcp-475-review-7e-fixes/`.

The long-word case also traverses the PowerShell reader, even after Bash refuses.
In addition to charging each Bash expansion's allocation, the PowerShell scanner
now charges four work units per character (including skipped spans), preserving
that charge in returned/accumulated costs. This bounds a single long argument,
not only lists of small tokens. Limits still refuse, and CPU/memory/timeout
assertions remain unchanged. Each charge has a separate negative control.

Intermediate validation (ai, isolated HOME, 4 GiB cap): `5fa8369` passed 1629
guard tests and three C15/C16 controls; `b047ba0` passed 1703; `d2ee9d7` passed
1718; `14983f4` passed 1719, also with coverage instrumentation, although the
standalone coverage command still exits 1 at 0% against 60%. Mypy and workflow
checks passed. The first integration `8ca941d` failed 35 tests: the expression
scan was too broad and one imported reviewer case was misclassified as a read
because its name contained GetType. Both were corrected; that run remains failed.

The `d2ee9d7` controls included one invalid collection-error result
(NC-CL5-call-separators), not a valid kill. Its mutation quoting is repaired,
all new mutation sources are parsed before execution, and the control driver now
marks collection errors INVALID_ERROR. The copied-tree harness has two documented
baseline entry-point failures because its scratch copy has no Git repository;
only additional failing test nodes count as kills. The historical `5bdfdf4`
run finished 170/170 mutation rows and 93 controls with additional failures,
with exactly those two baseline failures. It does not certify later heads.

The full `bd4babb` battery is running at
`/tmp/pmcp-ai-viperjuice/475-final-bd4babb/`, with durable driver/control sources
at `/home/viperjuice/workspace/tmp/pmcp-475-final-bd4/`. It retains the original
selectors and oracles, adds the Claude adjacent-keyword live texts, and includes
115 negative controls. The targeted `bd4babb` guard run passed 1733 tests;
its controls and the full battery are pending, not passing evidence. The older
`ea5252a` run remains separate. Whole-tree formatting retains its two unchanged
diagnostics failures. Final-head hosted CI and a complete usable four-vendor
review remain required; no merge approval or new residual waiver is asserted.

### Review round 552663e: subsequent corrections

All four native vendor seats returned DISAGREE at `552663e`; no panel approval
is claimed. Reports are retained under
`/home/viperjuice/workspace/boards/pmcp/475/t3-panel-552663e/`.
Code `a78bf3e` incorporates these dispositions (validation below is separate):

- Claude CL-8 / Codex child quoting: Bash-lexed command and option shapes now
  recognize child PowerShell evaluation, including quoted and escaped words.
- CL-9: child argument strings are split conservatively for execution switches.
  Whitespace-padded switches and explicit stdin `-` / `-File -` are refused
  (also Gemini GM-552-4). Even read-only child evaluation is refused; this does
  not add a residual or claim that every such child performs a write.
- CL-10: expression-name invocation includes quoted member/index expressions
  and array expressions. CL-11: literal type construction through expressions
  is refused without evaluating the type, including read-only constructions.
- CL-12 / C15: independent member-name numeric format/join tests expose removal
  of each operator rule without the expression invocation-tail rule masking it.
- Grok G-SEP: line comments and CRLF continuations before invocation operands
  are skipped, with their scanned spans charged before further processing.
- Gemini GM-552-1: implicit-positional `for name do` and `select name do`
  headers participate in compound-command recognition.
- GM-552-2: backtick-space/tab at token start separates tokens, and is recognized
  before an invocation operand. Escaped whitespace inside a token is retained.
- GM-552-3: top-level closing groups finish an argument unless member access
  or indexing continues it; expression invocation tails now refuse splats.
  Array expression operands were included in the CL-10 fix.
- GM-552-5: an independent conservative command-object-tail test exercises
  retaining arguments after an unmatched closing parenthesis without the
  generic invocation rule masking it. This test asserts the classifier's
  conservative syntax policy, not live PowerShell execution.

The unterminated invocation block-comment scan now charges the entire scanned
suffix; its negative control restores the uncharged scan. The committed-tree
margin assertion measures the Bash work counter only, not total PowerShell
work. PowerShell has its own accumulated fail-closed budget; the assertion is
not evidence of a 10% margin for that separate reader.

Additional conservative false positives remain: expression-site plus format
operator detection may refuse Bash `docker build -f Dockerfile .`,
`[ -f ./x ] && ...`, or `[0-9a-f]` patterns. The earlier correction for
`[ -f x ] && ...` covers that specific form, not all filesystem tests or regex
ranges. These refusals do not widen the accepted residual.

Targeted validation on ai: `d1949fd` passed 1768 guard tests and eight new
negative controls (including both C15 controls). `b45f3ac` passed 1840 guard
tests and all three G-SEP controls. `a78bf3e` passed 1878 guard tests, mypy and
workflow invariants; all six focused controls were killed independently.
All used the prepared interpreter, isolated HOME, pytest_temp runner, detached
4 GiB scopes and durable logs. No guard execution occurred on dev0.

The full exact-code `a78bf3e` battery completed: 1878 guard tests, 2179 focused
tests, 338 corrected live-oracle texts with no disagreement, 216 brace-oracle
words, and 170/170 mutation rows. Of 133 real negative controls, 131 were
killed; `NC-N3-literal-concatenation` and `NC-RULING-string` survived. The first
was masked by the newer expression-invocation rule. Code `c187800` adds an
independent conservative literal-chain reading test, which kills that control.
The second targeted a redundant broad refusal of every `[string]::` member;
that redundant decision and stale control were removed. The specific
`Format`/`Join`/`Concat` member rule required by the ruling remains, and its
independent `NC-CL7-static-member` control is killed. At `c187800`, 1879 guard
tests, mypy, workflow invariants and both focused controls pass; a 133-control
full exact-head battery is running at
`/tmp/pmcp-ai-viperjuice/475-final-c187800/`.

Coverage-only runs still exit 1 at 0% against the global 60% threshold, and
whole-tree formatting still fails on two unchanged diagnostics files. These
are disclosed failures, not waivers. Hosted tests succeeded at `f03f8b3`; the
current head still requires hosted CI and a fresh complete panel. The release
acknowledgement gate remains unmet.
