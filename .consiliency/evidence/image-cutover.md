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
- **Publication spellings (R8f, R9).** `docker`, `podman` and `buildah` are refused when global options (each an option word, optionally followed by one value word) come before `push` or `image push`. As one backtracking regex this search was quadratic on a single hostile line (about 35 s for 160 KB of `docker -x `, below the work bound and then accepted; PR Consiliency/pmcp#475 F3), and so were the older `SKOPEO_DELETE` and `type=image ... push=true` patterns (on `main` too). All four are now decided in linear time (`_ToolPush`, a right-to-left pass over blank-separated runs; `_LinePair`, one pass per line) and checked against the old regexes on about 55,000 bounded generated texts each. The `build-smoke` load's two operands may contain only `[A-Za-z0-9_./:@+-]` and must each end at a blank or at the end of the text, so anything glued on (`;`, `&`, `|`, `$(`, a backtick or a quote) leaves `skopeo` in the remainder.
- **PowerShell web cmdlets.** `Invoke-WebRequest`, `Invoke-RestMethod`, `iwr`, `irm`, and `curl`/`wget` as Windows PowerShell 5.1 aliases, are refused (`contains a mutating PowerShell web request`) with any `-Method` but GET or HEAD, with `-CustomMethod`/`-CM`, or with a splatted argument set (`@params`). Parameter names are case-insensitive and may be abbreviated (`-Me`, `-Cu`), and an expression for the method counts as a write. The clause fails closed: it reads every step and `defaults.run.shell` text, whatever its shell. That covers `windows-latest`'s default `pwsh`, `shell: pwsh` and `shell: powershell` without resolving the effective shell. It flags nothing in the committed tree, so it is not recorded as an AC1 residual.
- **Bounded work (2026-11-03 dev0 incident).** All lexing, nested classification, rescans and lookaheads of the bash-grammar classifier for one text are charged to `_WORK_LIMIT` (250,000 characters). That budget does not cover the regex scans that run outside the classifier: the publication patterns are linear (above), and the 5d416e4 floor, which is quadratic in a token's run of letters after its `-` (`-[A-Za-z]*X[A-Za-z]+`, about 1.5 s for 32,000 letters), is not run when such a run is longer than `_FLOOR_LETTERS_LIMIT` (1,024): the text counts as a write instead. The floor itself stays byte-identical. The PowerShell scan has its own budget of the same size and stops scanning when it reaches it. Exceeding a budget, or Python's stack depth, counts as a write. Memoisation is per call. The largest committed step uses 10,677 characters of the budget (about 4%). Tests run 12 hostile, linear-size inputs (each quadratic or worse without the bound) under a 60 s timeout, with wall-clock and `tracemalloc` peak assertions.
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
