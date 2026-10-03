# Detailed plan: auth operator messages that survive pmcp's own sanitiser, the README `/mcp` rule behind a prefix-stripping proxy, and no empty `resource`

> Written on main `89559db` (dev0, a team host), worktree `pmcp-326`, branch
> `plan/326-auth-messages`. Every number below was measured on that tree, or on
> the spike of this plan applied to it (CPython 3.10.20). The spike was then
> removed; this PR carries only this file. The spike's patches and test module
> are reproduced verbatim at the end (*Verbatim bodies*).

## Task

Consiliency/pmcp#326 collects three non-blocking items from the review panel
on Consiliency/pmcp#325 (see Consiliency/pmcp#231, Consiliency/pmcp#320):

1. **The sanitiser rewords a fixed 401 description.** pmcp's diagnostic
   sanitiser turns `"Token could not be verified with the published key."`
   (`src/pmcp/auth.py:629`) into `"Token [REDACTED] not be verified…"`. Reword
   it. **Fix the class:** test that every fixed operator-facing message in
   `auth.py` and `transport/http.py` comes through the sanitiser and the
   additive redaction (Consiliency/pmcp#234, PR #310) unchanged. That covers
   the 401/403/503 descriptions and the `WWW-Authenticate` texts, with the list
   derived from the code.
2. **README `/mcp` rule.** "PMCP serves MCP at `/mcp`" is not the public URL
   behind a reverse proxy that strips a path prefix, or under an ASGI
   `root_path`. Name that case and point to `--oauth-audience`. Decide whether
   the metadata 404 under such a proxy is a bug to fix here or a separate
   issue, and measure it.
3. **Empty `resource`.** `_canonical_resource()` returns `""` when there is no
   audience and no metadata URL. Decide whether to assert, to omit `resource`,
   or to leave it, and justify the choice.

## Research summary

### Where a description goes (main `89559db`)

- `ResourceServerAuthError.__init__` (`auth.py:366-372`) stores
  `sanitize_auth_diagnostic(description)` as `.description` and as the
  exception's `str()`. So the stored text is already mangled. Measured:
  `ResourceServerAuthError('invalid_token', 'Token could not be verified with
  the published key.').description == 'Token [REDACTED] not be verified with
  the published key.'`.
- **No description reaches the HTTP wire.** `handle_mcp`
  (`http.py:588-610`) answers with a fixed body (`Unauthorized` / `Forbidden`
  / `Service Unavailable`) and a `WWW-Authenticate` header that carries only
  `resource` or `resource_metadata`, `error=<code>` and `scope`. It never
  sends `error_description`. Its log lines (`"handle_mcp [%s]: 401 invalid
  token"`) do not include the exception either. These were measured through
  the app for all eight 401/403/503 cases (table below).
- **Who sees the mangled text, then:** anything that prints or logs the
  exception: a traceback, a test failure, or a future log line. The issue's
  "operator message" is the exception text, not the response. That is still
  worth fixing, because it is the text an operator debugging a 401 reads. It
  also makes the change pure wording: no response a client sees changes.
- Startup refusals in `create_http_app` (`http.py:314, 316, 324`) are
  `ValueError`s. They are mangled only where a caller runs them through the
  sanitiser, which `describe_exception` does (`manager.py:111`).

### The sanitiser has two layers

`sanitize_auth_diagnostic` (`auth.py:724`) is `redact_additive(_sanitize_base(text))`,
then cut to 400 characters:
- the redactor's own rules (`_sanitize_base`, `auth.py:735`): URLs,
  `Authorization`, `Bearer <x>`, the keyword rule (`token`, `secret`, … followed
  by a value) and JWT shapes;
- the additive shape rules (`pmcp.redaction_additive`, #234), which run over
  that output and can only redact more.

Each layer was measured separately, because a text can pass one and fail the
other.

### The class, derived from the code

An AST walk of `auth.py` and `transport/http.py` collects every message
argument:
- `ResourceServerAuthError(_, msg)` and `ResourceServerJWKSUnavailable(msg)`;
- `_reject(_, body)` and `Response(body)` in `http.py`;
- `ValueError(msg)` in `http.py`.

In those messages, an f-string hole `{self.url}` is filled with a sample JWKS
URL, and `"Missing required scope(s): " + …` gets sample scopes. pyjwt's own
texts are generated from pyjwt itself for every class in
`_FIXED_TEXT_CLAIM_ERRORS` (`auth.py:585`), whose text pmcp keeps verbatim.
**33 fixed texts plus 9 pyjwt texts.** On main, **4 are rewritten**, and 3 of
them predate #325:

| Site (main) | Text | Rewritten by | Comes out as |
|---|---|---|---|
| `auth.py:629` (#325) | `Token could not be verified with the published key.` | base keyword rule (`Token <word>`) | `Token [REDACTED] not be verified with the published key.` |
| `auth.py:644` | `Missing bearer token.` | base `Bearer` rule | `Missing bearer [REDACTED]` |
| `auth.py:649` | `Unsupported token algorithm.` | base keyword rule | `Unsupported token [REDACTED]` |
| `http.py:316` | `shared-secret auth mode requires auth_token.` | base keyword rule (`secret <word>`) | `shared-secret [REDACTED] mode requires auth_token.` |

The additive layer rewrites none of them. Its own comments exclude `Missing
bearer token` and a challenge's parameters (`redaction_additive.py:646-660`),
but the base rules run first. Every other text, including all 9 pyjwt texts
(`Token is missing the "iss" claim`, `The token is not yet valid (nbf)`, …),
passes both layers unchanged.

### `WWW-Authenticate`, measured through the app

There are eight challenges: 401 for a missing token, 401 for an invalid
token, 403 for insufficient scope and 503 for an unavailable JWKS, each with
and without a metadata URL. For example:

- `Bearer resource="https://pmcp.example/mcp", error="invalid_token"`
- `Bearer resource_metadata="https://pmcp.example/.well-known/oauth-protected-resource", error="insufficient_scope", scope="admin"`

**A whole header never comes through the sanitiser.** The base `Bearer` rule
redacts the first parameter: `Bearer [REDACTED], error="invalid_token"`. It
cannot tell the challenge `Bearer resource="…"` from the credential `Bearer
<token>`. The base rules are frozen: #234 shipped as additive-only rules on top of
them (`src/pmcp/redaction_additive.py`'s module docstring, and the #310
entry in `CHANGELOG.md`). `sanitize_auth_diagnostic`'s docstring says the
base rules "run first, unchanged". So this is not something to
reword. It is also not a leak: pmcp builds its challenge (`_auth_headers`,
`http.py:437-454`) without the sanitiser and never sanitises it afterwards.
Every **parameter** (the `resource` and `resource_metadata` URLs, the three
`error` codes, `scope`) comes through both layers unchanged, and pmcp's own
challenge parser (`parse_www_authenticate`) reads every challenge back with
`error`, `scope` and `resource_metadata` intact.

### The README `/mcp` rule: measured

pmcp has **no `--root-path` flag** (`pmcp --help`), and `uvicorn.Config` is
built without `root_path` (`server.py:991`). An ASGI `root_path` therefore
applies only when someone mounts `create_http_app` inside another ASGI app.
The common case is a reverse proxy that strips a prefix. Measured with
Starlette's `TestClient`; the metadata route is registered at the metadata
URL's literal path (`http.py:743-750`):

| Setup | Request PMCP receives | Result |
|---|---|---|
| A: metadata URL `https://pub.example/.well-known/oauth-protected-resource` | `/.well-known/oauth-protected-resource` | 200, `resource = https://pub.example/mcp` |
| B: metadata URL `https://pub.example/pmcp/.well-known/oauth-protected-resource`, proxy strips `/pmcp` | `/.well-known/oauth-protected-resource` | **404** |
| B, unstripped | `/pmcp/.well-known/oauth-protected-resource` | 200, but `resource = https://pub.example/mcp` (wrong) |
| C: RFC 9728 path form `https://pub.example/.well-known/oauth-protected-resource/pmcp/mcp`, forwarded unchanged | that path | 200 |
| D: B plus `--oauth-audience https://pub.example/pmcp/mcp` | `/.well-known/oauth-protected-resource` | **still 404**; at the unstripped path, `resource = https://pub.example/pmcp/mcp` |
| E: app under `Mount("/pmcp")` (ASGI `root_path`) | `/pmcp/.well-known/oauth-protected-resource` | **404**; it answers only at `/pmcp/pmcp/.well-known/...`. `POST /pmcp/mcp` → 202 |

So `--oauth-audience` fixes the published `resource` but **not** the 404.
The README's current advice ("If MCP is reachable at a different public URL,
set `--oauth-audience`") is right about `resource` but silent about the
route. In setup B, every 401 points the client (`resource_metadata=…`) at a
URL the proxy turns into a 404, so OAuth discovery fails.

### `_canonical_resource()` returning `""`: measured

- The metadata route is mounted only when
  `auth_metadata.protected_resource_metadata_url` is set and has a path
  (`http.py:740-750`).
- `normalize_auth_metadata` drops any metadata URL that
  `sanitize_public_auth_url` refuses, so a mounted route always has an absolute
  `https` (or loopback `http`) URL with a netloc. Measured: `/.well-known/x`,
  `pmcp.example/.well-known/x`, `http://pmcp.example/...` and `https:///x` all
  normalize to `None`.
- So `""` is unreachable. When it is forced (a test patches the normalized URL
  to a relative path), main serves **`200 {"resource": ""}`**.
- RFC 9728 §2 makes `resource` REQUIRED in protected-resource metadata, so
  that response is invalid metadata, not merely an empty field.

## Design decisions (made explicitly)

### 1. Reword the four texts; do not touch the sanitiser

| Site | New text | Why this wording |
|---|---|---|
| `auth.py:629` | `The published key cannot verify this token.` | The issue's suggestion; no `token <word>` and no `key <word>` pair |
| `auth.py:644` | `Empty token.` | Any word after `bearer` is redacted (`No bearer credential.` → `No bearer [REDACTED]`, measured), and `No token was presented.` → `No token [REDACTED] presented.`. The case is `token == ""`, so say exactly that |
| `auth.py:649` | `The token's algorithm is not supported.` | `Token algorithm not supported.` is still mangled (measured); the possessive is not |
| `http.py:316` | `auth_token is required when auth_mode is shared-secret.` | `Shared-secret auth needs auth_token.` and `... for shared-secret auth.` are both mangled (measured); `secret` must end the sentence |

Each change carries a one-line comment naming the rule that bit it. **Not
changing the sanitiser** is deliberate. Making the base keyword or `Bearer`
rule spare these words would loosen a credential rule (the fail-open
direction), and the base rules are frozen (#234 additive-only). The texts
are pmcp's own, so changing them is the safe side.

No test, doc or CHANGELOG line asserts any of the four old texts (`grep -rn`
over `tests src *.md`). The only hit, `tests/test_redaction_additive.py:82`,
is corpus prose that contains the words "Missing bearer token". None of the
four reaches the wire, so no client sees a change.

### 2. The class test is derived from the code, and its derivation is guarded

`tests/test_auth_operator_messages.py` builds the list with the AST walk
described above. That way, a message added later is tested the day it is
written:
- mutant B6 rewrites `Invalid audience.` to `Token audience mismatch.`, and
  the derivation finds it and goes red;
- an f-string hole without a sample value fails the test instead of being
  skipped;
- `test_the_derived_list_is_not_empty` stops the derivation from silently
  finding nothing (33 on the spike);
- `test_every_kept_pyjwt_class_has_a_producer` fails when a class is added to
  `_FIXED_TEXT_CLAIM_ERRORS` without a generator.

Every text is checked against all three of `_sanitize_base`, `redact_additive`
and the composed `sanitize_auth_diagnostic`. The test also builds a
`ResourceServerAuthError` for each auth description and checks that the
stored `.description` is the text as written, end to end.

### 3. `WWW-Authenticate`: parameters, not the whole header

The scope is the one stated in the Research summary:
- every challenge **parameter**, for all eight measured 401/403/503
  challenges, comes through all three layers;
- `parse_www_authenticate` reads every challenge back;
- one test pins that the **whole** header becomes `Bearer [REDACTED], …`, by
  design, so a change to that is a decision and not an accident. Mutant B7
  adds a parameter the sanitiser rewrites (`error_description="token
  expired"`); 6 challenge cases go red.

### 4. The prefix-proxy 404 is a separate issue; this plan fixes the README

**Decision: a separate issue.** It is a behaviour bug, not wording: the route
path is wrong for a deployment pmcp cannot see. Any fix needs a design
choice this issue does not cover:
- a new option for the public path prefix;
- or serving the metadata at both the literal path and the path with an
  operator-declared prefix removed;
- or deriving the route from `--oauth-audience`'s path following RFC 9728.

It needs its own tests against a real proxy rewrite, and #326 is
explicitly "wording, not behaviour". A working configuration exists today
(setup C plus `--oauth-audience`, measured at 200 with the right
`resource`), so the README can document it. The 404 predates #325, and nothing
here makes it worse.

**README change (verbatim below).** Name the case (a proxy that strips a
prefix, or an ASGI `root_path`), give the nginx shape, and say three things:
- set `--oauth-audience` to `https://<host>/pmcp/mcp`;
- a prefixed metadata URL behind that proxy returns `404`;
- use the RFC 9728 path form
  `https://<host>/.well-known/oauth-protected-resource/pmcp/mcp`, forwarded
  unchanged;
- an app that mounts PMCP under a `root_path` must route that path itself,
  because the mounted app answers only at the doubled prefix (setup E).

Two tests pin the README's claims: one that the prefixed URL returns 404
behind the stripping proxy, and one that the RFC 9728 form plus the audience
serves `resource = https://pub.example/pmcp/mcp`. **The 404 test is a
tripwire:** it pins today's behaviour so the README stays true. The
follow-up issue's fix must invert it, and that issue's text says so.

**Follow-up issue text, for the maintainer to file.** It is not filed from
here, because opening an issue is an outward action. Suggested title:
"metadata route 404s behind a prefix-stripping proxy or an ASGI `root_path`".
The body is setups B, D and E of the table above, the consequence (every
401's `resource_metadata` points at a 404, so OAuth discovery fails), the
three design options, and a note that
`tests/test_auth_operator_messages.py::test_readme_prefixed_metadata_url_404s_behind_a_stripping_proxy`
and the README paragraph change with the fix.

### 5. Empty `resource`: fail at startup, not assert, not omit, not leave

- **Not omit:** RFC 9728 §2 makes `resource` REQUIRED, so metadata without it
  is invalid for every client.
- **Not a request-time `assert`:** it would turn a public, unauthenticated
  endpoint into a 500 for every caller, and `python -O` strips it.
- **Not "leave":** the branch is dead today, but if it is ever reached it
  publishes invalid metadata silently (forced on main: `200 {"resource": ""}`).
- **Decision:** when the metadata route is about to be mounted
  (`http.py:740`), check `_canonical_resource()` once and raise `ValueError("
  Protected-resource metadata needs a canonical resource: set --oauth-audience
  or an absolute metadata URL.")` from `create_http_app`. This fails closed at
  startup, as resource-server mode already does for a missing
  issuer/JWKS/audience (`http.py:318-326`). It costs nothing at request time.
  The message is itself checked by the class test.
- `_canonical_resource()` keeps its `return ""` as the type-complete fallback.
  The startup check makes it unpublishable.
- Mutant B5 removes the check: red.

## Corrections to the issue description

- The issue names one mangled text. There are **four**:
  - `Missing bearer token.` and `Unsupported token algorithm.` predate #325;
  - `shared-secret auth mode requires auth_token.` is a startup refusal in
    `http.py`.
- The mangled text is not "operator-facing" in the HTTP sense. No description
  reaches a response or a log line today. It is the exception's stored text
  (`ResourceServerAuthError` sanitises in `__init__`), which is what a
  traceback or a future log shows.
- pmcp has no `--root-path` option. The `root_path` case is an embedding of
  `create_http_app` in another ASGI app. The usual case is a prefix-stripping
  reverse proxy.
- `--oauth-audience` fixes the published `resource` under a prefix, but not
  the metadata 404 (setup D).

## Changes

The spike diff is 3 files, 39 insertions and 8 deletions: `auth.py` 13 lines,
`transport/http.py` 13 lines and `README.md` 21 lines. It also adds a new test
module.

### `src/pmcp/auth.py` (modify)
- `_decode_with_key` (`auth.py:628-630`): the new 401 text, with a comment.
- `validate_resource_server_token` (`auth.py:644`): `"Empty token."`, with a
  comment.
- the same function (`auth.py:649-651`): `"The token's algorithm is not
  supported."`, with a comment.

### `src/pmcp/transport/http.py` (modify)
- `create_http_app` (`http.py:316`): the shared-secret refusal reworded, with
  a comment.
- `create_http_app` metadata-route block (`http.py:740-750`): the startup check
  of Design decision 5, with a comment.

### `README.md` (modify)
- The `resource` paragraph (`README.md:177-184`): rewritten as described in
  Design decision 4 (verbatim below).

### `tests/test_auth_operator_messages.py` (create)
80 tests on 3.10 (0.2–0.9 s), no network, no timed sleeps:

| Test | Count | Pins |
|---|---|---|
| `test_every_fixed_message_survives_the_sanitiser[<file>:<line>]` | 33 | the class: each derived text through base, additive and composed |
| `test_a_stored_description_is_the_text_written[...]` | 15 | the end-to-end `.description` for each distinct auth description (the 7 `{self.url}` texts with the sample URL filled in) |
| `test_the_derived_list_is_not_empty` | 1 | the derivation |
| `test_every_kept_pyjwt_class_has_a_producer`, `test_every_kept_pyjwt_text_survives_the_sanitiser[...]` | 1 + 9 | pyjwt's kept texts |
| `test_the_challenges_cover_401_403_503`, `test_challenge_parameters_survive_the_sanitiser[...]`, `test_pmcp_reads_its_own_challenge_back[...]` | 1 + 8 + 8 | `WWW-Authenticate` |
| `test_the_whole_header_is_redacted_by_the_base_bearer_rule_by_design` | 1 | the deliberate exception |
| `test_metadata_route_refuses_to_start_without_a_canonical_resource` | 1 | Design decision 5 |
| `test_readme_prefixed_metadata_url_404s_behind_a_stripping_proxy`, `test_readme_rfc9728_form_with_audience_serves_the_public_resource` | 2 | the README's two claims |

The f-string texts are checked with the sample URL filled in. The URL rule
leaves a clean URL unchanged, so a mangled result can only come from the
fixed words around it.

## Documentation impact

- `README.md`: Design decision 4.
- `CHANGELOG.md`: one bullet under `## [Unreleased]` → `### Fixed`, phrased
  "see Consiliency/pmcp#326" (no closing keyword). Four auth and startup
  messages are reworded, because pmcp's own sanitiser rewrote them (`Token
  [REDACTED] not be verified…`). A test now derives every fixed auth message
  from the code and checks it comes through the sanitiser. The
  protected-resource metadata route now refuses to start rather than publish
  an empty `resource`, which no shipped configuration reaches. The README
  documents the prefix-stripping proxy case.
- `SECURITY.md`: no change. No ledger claim names these texts;
  `scripts/check_security_claims.py` reports `OK … 129 cited node id(s)` on
  the spike.

## Dependencies & order

1. The four rewordings and the startup check, in any order.
2. The test module.
3. The README and CHANGELOG last.

**Touch points with open work.** Plan Consiliency/pmcp#297 (PR
Consiliency/pmcp#314) changes how validation errors echo argument values. It
is not in `auth.py`/`transport/http.py`'s message set. If a later change adds
a `ResourceServerAuthError` with an interpolated value, this module fails
until a sample is given; that is the intended behaviour. Plan PR
Consiliency/pmcp#332 (#324) touches only `client/manager.py`, so there is no
overlap.

## Verification

Run from a fresh worktree of `origin/main` on dev0 (a team host):

```bash
git -C ~/code/pmcp worktree add -b fix/326-auth-messages "$WORKTREE_ROOT/pmcp-326-fix" origin/main
cd "$WORKTREE_ROOT/pmcp-326-fix"
uv sync --all-extras -p 3.10      # without --all-extras, `uv run` silently uses the system pytest
unset npm_config_cache npm_config_store_dir pnpm_config_store_dir
```

Apply *Verbatim bodies*: `git apply` both patches, write the test module, and
add the CHANGELOG bullet by hand. Then:

```bash
# 1. the new module (spike: 80 passed, 0.2-0.9 s)
uv run pytest tests/test_auth_operator_messages.py --cov-fail-under=0 -p no:cacheprovider -q
# 2. the suites that touch auth, the HTTP transport and the redactor (spike: 626 passed, 55 deselected, 0 failed, 71 s)
uv run pytest tests/test_auth.py tests/test_transport_http.py tests/test_auth_origin_wiring.py \
  tests/test_redaction_additive.py tests/test_auth_operator_messages.py tests/test_cli.py tests/test_server.py \
  --cov-fail-under=0 -p no:cacheprovider -q
# 3. CI gates (spike: all clean)
uv run ruff check src tests && uv run ruff format --check src tests
uv run mypy src/pmcp/auth.py src/pmcp/transport/http.py
python3 scripts/check_security_claims.py          # expect OK, 129 cited node ids
python3 scripts/check_plan_consistency.py .consiliency/plans/detailed-326-auth-messages-20261003-0226.md
#   measured on this file: "consistent ... blocking inconsistencies: 0", exit 0 (a detailed plan has no roadmap pin)
# 4. the full suite: once, detached, with a notifying waiter (memory on dev0 is shared)
nohup uv run pytest -q -p no:cacheprovider > "$WORKTREE_ROOT/pmcp-326-full.log" 2>&1 &
```

**Red on main.** The module against `89559db`'s `auth.py` and `http.py`
(README is not imported): **9 failed, 70 passed** (the derivation adds no
`metadata` startup text on main, so one fewer case). The ids below are main's
line numbers (`auth.py:628`). The mutation table uses the spike's
(`auth.py:631`), because the comments the patch adds move the lines. Both are
correct for their tree.

| Test | First assertion on main |
|---|---|
| `test_every_fixed_message_survives_the_sanitiser[auth.py:628]` | `'Token could not be verified with the published key.' is rewritten: {'base': 'Token [REDACTED] not be verified…', 'composed': …}` |
| `…[auth.py:644]` | `'Missing bearer token.' is rewritten: {'base': 'Missing bearer [REDACTED]', …}` |
| `…[auth.py:649]` | `'Unsupported token algorithm.' is rewritten: {'base': 'Unsupported token [REDACTED]', …}` |
| `…[http.py:316]` | `'shared-secret auth mode requires auth_token.' is rewritten: {'base': 'shared-secret [REDACTED] mode requires auth_token.', …}` |
| `test_a_stored_description_is_the_text_written` ×3 | e.g. `assert 'Token [REDAC...ublished key.' == 'Token could ...ublished key.'` |
| `test_the_derived_list_is_not_empty` | the new 401 text is not in the list |
| `test_metadata_route_refuses_to_start_without_a_canonical_resource` | `DID NOT RAISE ValueError` (main serves `200 {"resource": ""}`) |

The two README tests, the challenge tests and the pyjwt tests **pass on
main**. That is by design: they pin behaviour this plan keeps, and mutants B5
and B7 are what show they can fail.

## Acceptance criteria

- [ ] Every fixed message in `auth.py` and `transport/http.py`, derived from
  the code (33 on the spike), comes through `_sanitize_base`, `redact_additive`
  and `sanitize_auth_diagnostic` unchanged. Proven by
  `test_every_fixed_message_survives_the_sanitiser`.
- [ ] A `ResourceServerAuthError` stores each fixed description exactly as
  written. Proven by `test_a_stored_description_is_the_text_written`.
- [ ] Every pyjwt text pmcp keeps comes through unchanged, and every kept class
  has a producer. Proven by the two pyjwt tests.
- [ ] Every `WWW-Authenticate` parameter of the eight 401/403/503 challenges
  comes through unchanged, and pmcp parses each challenge back. Proven by the
  two challenge tests. The whole-header exception is pinned by
  `test_the_whole_header_is_redacted_by_the_base_bearer_rule_by_design`.
- [ ] The metadata route never publishes an empty `resource`; it refuses to
  start instead. Proven by
  `test_metadata_route_refuses_to_start_without_a_canonical_resource`.
- [ ] The README names the prefix-stripping proxy and `root_path` case, points
  to `--oauth-audience` and to the RFC 9728 metadata path form, and states the
  404. Both claims are pinned by the two `test_readme_…` tests.
- [ ] The follow-up issue for the 404 is filed by the maintainer, with the
  text in Design decision 4.
- [ ] Verification steps 1–3 pass. The CHANGELOG entry is present, with no
  closing keyword.
- [ ] Every mutant below is red.

## Mutation table

Each mutant was measured on the spiked tree (`mutants.py`: apply one string
replacement, run `tests/test_auth_operator_messages.py`, restore from the saved
copy in a `finally`). All 7 are red. After the run, both files were
byte-identical to the spike.

| # | Rule | Mutant | Red tests (measured) |
|---|---|---|---|
| B1 | the 401 text is clean | revert to `Token could not be verified…` | 3: `…survives_the_sanitiser[auth.py:631]`, `…stored_description…[Token could…]`, `test_the_derived_list_is_not_empty` |
| B2 | the empty-token text is clean | revert to `Missing bearer token.` | 2: `…[auth.py:649]`, `…stored_description…[Missing…]` |
| B3 | the algorithm text is clean | revert to `Unsupported token algorithm.` | 2: `…[auth.py:656]`, `…stored_description…[Unsupported…]` |
| B4 | the shared-secret text is clean | revert | `…[http.py:318]` |
| B5 | no empty `resource` | the startup check → `if False:` | `test_metadata_route_refuses_to_start_without_a_canonical_resource` |
| B6 | the derivation finds new texts | `Invalid audience.` → `Token audience mismatch.` | 2: `…[auth.py:672]`, `…stored_description…[Token audience…]` |
| B7 | challenge parameters are covered | `_auth_headers` adds `error_description="token expired"` to every `error=` challenge | 6: `test_challenge_parameters_survive_the_sanitiser[{401-invalid,403-scope,503-jwks}-{audience,metadata}]` |

The implementer re-runs all 7 on the final tree, restoring from a saved copy
(never `git checkout --`).

## Non-goals

- **The metadata 404 behind a prefix-stripping proxy.** This is a separate
  issue (Design decision 4).
- **Changing the sanitiser's base rules** so that `Bearer resource="…"` or
  `token <word>` survives. Those rules are frozen (#234 additive-only), and
  loosening them is the fail-open direction.
- **Sending `error_description` in challenges.** pmcp does not send it today,
  and adding a client-visible field belongs in its own change.
- **Rewording `auth.py`'s URL-validation `ValueError`s.** They are re-raised
  as fixed diagnostics through `normalize_auth_metadata`, which sanitises them
  itself. The derivation skips `auth.py`'s `ValueError`s on purpose, and they
  come through anyway (measured with the same layers: `Invalid public auth
  URL.` and its siblings are unchanged).

## Unverified

- **A real reverse proxy.** Setups B–E were measured with Starlette's
  `TestClient` sending the path a stripping proxy would forward, not with
  nginx.
- **Python 3.11 / 3.12.** The module was run on 3.10 only. It is pure string
  and AST work plus `TestClient`, with no version-specific asyncio.
- **The full suite.** It was not run, because memory on dev0 is shared.
  Verification step 4 runs it once.

## Execution Policy

- execute: effort=low.
- reason: four string changes, one startup check and a README paragraph. The
  derived test is the substance.
- Re-run the mutation table, ruff and mypy before requesting review.
- Get a cross-vendor panel CR before merge, as for every PR to main.

## Verbatim bodies

### How to apply

1. Save the source patch below to `326-src.patch` and the README patch to
   `326-readme.patch`, then run `git apply 326-src.patch 326-readme.patch` on
   `89559db`.
2. Write the test module below to `tests/test_auth_operator_messages.py`.
3. Add the `CHANGELOG.md` bullet by hand.

### Patch — `src/pmcp/auth.py`, `src/pmcp/transport/http.py`

````diff
diff --git a/src/pmcp/auth.py b/src/pmcp/auth.py
index e929470..198c4c7 100644
--- a/src/pmcp/auth.py
+++ b/src/pmcp/auth.py
@@ -625,8 +625,11 @@ def _decode_with_key(
     except jwt.InvalidTokenError:
         raise
     except (jwt.PyJWTError, TypeError, ValueError) as exc:
+        # Worded so `sanitize_auth_diagnostic` passes it through unchanged: the
+        # keyword rule reads `Token <word>` as a credential and redacted this
+        # as "Token [REDACTED] not be verified..." (Consiliency/pmcp#326).
         raise ResourceServerAuthError(
-            "invalid_token", "Token could not be verified with the published key."
+            "invalid_token", "The published key cannot verify this token."
         ) from exc
 
 
@@ -641,13 +644,17 @@ def validate_resource_server_token(
 ) -> ResourceServerTokenClaims:
     """Validate an AS-issued JWT for PMCP Resource Server mode."""
     if not token:
-        raise ResourceServerAuthError("invalid_token", "Missing bearer token.")
+        # Not "Missing bearer token.": the Bearer rule redacts the word after
+        # `bearer` (Consiliency/pmcp#326).
+        raise ResourceServerAuthError("invalid_token", "Empty token.")
     try:
         header = jwt.get_unverified_header(token)
         algorithm = header.get("alg")
         if not isinstance(algorithm, str) or algorithm.lower() == "none":
+            # Not "Unsupported token algorithm.": the keyword rule redacted
+            # `algorithm` (Consiliency/pmcp#326).
             raise ResourceServerAuthError(
-                "invalid_token", "Unsupported token algorithm."
+                "invalid_token", "The token's algorithm is not supported."
             )
         if jwks is None:
             raise ResourceServerAuthError("invalid_token", "JWKS URL is required.")
diff --git a/src/pmcp/transport/http.py b/src/pmcp/transport/http.py
index 41af495..5747e33 100644
--- a/src/pmcp/transport/http.py
+++ b/src/pmcp/transport/http.py
@@ -313,7 +313,9 @@ def create_http_app(
     if effective_auth_mode not in {"none", "shared-secret", "resource-server"}:
         raise ValueError("Unsupported auth mode.")
     if effective_auth_mode == "shared-secret" and auth_token is None:
-        raise ValueError("shared-secret auth mode requires auth_token.")
+        # Not "shared-secret auth mode requires ...": the keyword rule reads
+        # `secret <word>` as a credential (Consiliency/pmcp#326).
+        raise ValueError("auth_token is required when auth_mode is shared-secret.")
     resource_jwks: AsyncJWKS | None = None
     if effective_auth_mode == "resource-server":
         if (
@@ -769,6 +771,15 @@ def create_http_app(
     if auth_metadata.protected_resource_metadata_url:
         metadata_path = urlparse(auth_metadata.protected_resource_metadata_url).path
         if metadata_path:
+            # RFC 9728 makes `resource` REQUIRED, so never serve the route with
+            # an empty one. Unreachable today -- a normalized metadata URL is
+            # always absolute -- so this fails closed at startup, not per
+            # request, if a later change makes it reachable (Consiliency/pmcp#326).
+            if not _canonical_resource():
+                raise ValueError(
+                    "Protected-resource metadata needs a canonical resource: "
+                    "set --oauth-audience or an absolute metadata URL."
+                )
             routes.append(
                 Route(
                     metadata_path,
````

### Patch — `README.md`

````diff
diff --git a/README.md b/README.md
index 8af5061..d028a26 100644
--- a/README.md
+++ b/README.md
@@ -178,10 +178,23 @@ window are checked against the cached keys. The `resource` published at
 `/.well-known/oauth-protected-resource` is the configured
 `resource_server_audience`, or, when that is unset, the origin of the configured
 protected-resource metadata URL plus `/mcp`, with a scheme-default port such as
-`:443` dropped. A path prefix in the metadata URL is not carried over, because
-PMCP serves MCP at `/mcp`. If MCP is reachable at a different public URL, set
-`--oauth-audience` to publish it. The `resource` is never taken from the
-request `Host`. A forged token, including one whose algorithm does not match
+`:443` dropped. A path prefix in the metadata URL is not carried over: PMCP
+serves MCP at `/mcp` on the address it listens on, and that is the public URL
+only when nothing in front of PMCP rewrites the path. Behind a reverse proxy
+that strips a path prefix (for example nginx
+`location /pmcp/ { proxy_pass http://127.0.0.1:3344/; }`), or with the app
+mounted under an ASGI `root_path`, MCP's public URL is
+`https://<host>/pmcp/mcp`; set `--oauth-audience` to that URL so the published
+`resource` and the token audience both name it. Pick a metadata URL whose path
+reaches PMCP unchanged: PMCP serves the metadata at the URL's literal path, so
+`https://<host>/pmcp/.well-known/oauth-protected-resource` behind that proxy
+arrives as `/.well-known/oauth-protected-resource` and returns `404`. Use the
+RFC 9728 form for a resource with a path,
+`https://<host>/.well-known/oauth-protected-resource/pmcp/mcp`, and have the
+proxy forward that path to PMCP unchanged. An app that mounts PMCP under a
+`root_path` must route that path to PMCP itself: the mounted app answers the
+metadata only at the prefix doubled (`/pmcp/pmcp/.well-known/...`). The
+`resource` is never taken from the request `Host`. A forged token, including one whose algorithm does not match
 the published key's type, gets `401`, never `500`.
 In public auth metadata URLs it rejects hosts written as non-public **IP
 literals** — private, CGNAT, link-local, loopback, multicast, site-local, and
````

### File — `tests/test_auth_operator_messages.py`

````python
"""Consiliency/pmcp#326: every fixed operator-facing auth message survives
pmcp's own diagnostic sanitiser unchanged.

`ResourceServerAuthError.__init__` runs its description through
`sanitize_auth_diagnostic`, so a fixed text the sanitiser reads as a
credential is stored mangled: "Token could not be verified with the
published key." became "Token [REDACTED] not be verified with the published
key.". The list below is DERIVED from the code (an AST walk of `auth.py` and
`transport/http.py`, plus the pyjwt classes `_FIXED_TEXT_CLAIM_ERRORS` keeps
the text of), so a new message is covered the day it is written.

Each text must survive both layers separately and together: the redactor's
own rules (`_sanitize_base`), the additive rules (`redact_additive`,
Consiliency/pmcp#234), and the composed `sanitize_auth_diagnostic`.

A whole `WWW-Authenticate` header is the one deliberate exception, pinned
below: the base `Bearer` rule redacts the challenge's first parameter (it
cannot tell `Bearer resource="..."` from `Bearer <token>`), the base rules
are frozen (additive-only, #234), and pmcp never runs its own challenge
through the sanitiser. Its parameters must survive one by one, and pmcp's own
challenge parser must read them back intact.

The challenges are collected once, at import, through the real app: a wiring
change there is a collection error for this module, not one red test.
"""

from __future__ import annotations

import ast
from pathlib import Path
import time
from typing import Any, Callable
from unittest.mock import AsyncMock, MagicMock, patch

import jwt
import pytest
from starlette.testclient import TestClient

from pmcp import auth as auth_mod
from pmcp.auth import (
    ResourceServerAuthError,
    ResourceServerJWKSUnavailable,
    _sanitize_base,
    parse_www_authenticate,
    sanitize_auth_diagnostic,
)
from pmcp.redaction_additive import redact_additive
from pmcp.transport import http as http_mod
from pmcp.transport.http import create_http_app

_SAMPLE_URL = "https://issuer.example/.well-known/jwks.json"
_SAMPLE_SCOPES = "mcp:read mcp:write"

# Call name -> index of the positional argument that carries the message.
_MESSAGE_ARG = {
    "ResourceServerAuthError": 1,
    "ResourceServerJWKSUnavailable": 0,
    "_reject": 1,  # http.py: the 401/403/503 response body
    "Response": 0,  # http.py: 413/429/504 bodies
    "ValueError": 0,  # http.py: startup refusals
}


def _render(node: ast.expr) -> str | None:
    """The text a message expression produces, with config-derived holes
    filled by representative values. `None` = not a fixed text (e.g. `str(exc)`,
    covered by the pyjwt half below)."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant):
                parts.append(str(value.value))
                continue
            assert isinstance(value, ast.FormattedValue)
            source = ast.unparse(value.value)
            if source != "self.url":  # a new hole needs a sample here
                pytest.fail(f"no sample value for f-string hole {{{source}}}")
            parts.append(_SAMPLE_URL)
        return "".join(parts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _render(node.left)
        if left is not None and _render(node.right) is None:
            return left + _SAMPLE_SCOPES  # "Missing required scope(s): " + ...
    return None


def _fixed_messages() -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for module in (auth_mod, http_mod):
        path = Path(module.__file__ or "")
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = (
                func.attr
                if isinstance(func, ast.Attribute)
                else getattr(func, "id", "")
            )
            index = _MESSAGE_ARG.get(name)
            if index is None or len(node.args) <= index:
                continue
            if name == "ValueError" and module is not http_mod:
                continue  # auth.py's URL errors are reworded via `sanitize_*`
            text = _render(node.args[index])
            if text is not None:
                found.append((f"{path.name}:{node.lineno}", text))
    return found


_FIXED = _fixed_messages()


def test_the_derived_list_is_not_empty() -> None:
    """Guards the derivation itself: on the spike it finds 33 texts, 16 in
    auth.py and 17 in transport/http.py."""
    assert len(_FIXED) >= 28, _FIXED
    texts = {text for _, text in _FIXED}
    assert "The published key cannot verify this token." in texts


def _layers(text: str) -> dict[str, str]:
    return {
        "base": _sanitize_base(text),
        "additive": redact_additive(text),
        "composed": sanitize_auth_diagnostic(text, max_length=None),
    }


@pytest.mark.parametrize(("where", "text"), _FIXED, ids=[w for w, _ in _FIXED])
def test_every_fixed_message_survives_the_sanitiser(where: str, text: str) -> None:
    mangled = {k: v for k, v in _layers(text).items() if v != text}
    assert mangled == {}, f"{where}: {text!r} is rewritten: {mangled}"


@pytest.mark.parametrize(
    "description",
    sorted({text for where, text in _FIXED if where.startswith("auth.py")}),
)
def test_a_stored_description_is_the_text_written(description: str) -> None:
    """End to end through the class that stores it."""
    assert ResourceServerAuthError("invalid_token", description).description == (
        description
    )


# --- pyjwt's fixed texts, kept verbatim by `_FIXED_TEXT_CLAIM_ERRORS` ----------

_KEY = "k" * 32


def _pyjwt_error(payload: dict[str, Any], **decode: Any) -> jwt.InvalidTokenError:
    token = jwt.encode(payload, _KEY, algorithm="HS256")
    try:
        jwt.decode(
            token, _KEY, algorithms=decode.pop("algorithms", ["HS256"]), **decode
        )
    except jwt.InvalidTokenError as exc:
        return exc
    raise AssertionError("pyjwt accepted the token")


def _now() -> int:
    """Read at test time, not import time: a full-suite run can execute this
    module long after collecting it."""
    return int(time.time())


def _missing(claim: str) -> jwt.InvalidTokenError:
    """A token valid except that it lacks `claim`, decoded with pmcp's
    `require` list (`_decode_with_key`)."""
    payload = {"iss": "i", "aud": "a", "exp": _now() + 300, "nbf": _now() - 10}
    del payload[claim]
    return _pyjwt_error(
        payload,
        audience="a",
        issuer="i",
        options={"require": ["iss", "exp", "nbf", "aud"]},
    )


# One producer per class in `_FIXED_TEXT_CLAIM_ERRORS`; a class added there
# without a producer fails `test_every_kept_pyjwt_class_has_a_producer`.
_PYJWT_PRODUCERS: dict[type[jwt.InvalidTokenError], list[Callable[[], Any]]] = {
    jwt.ExpiredSignatureError: [lambda: _pyjwt_error({"exp": _now() - 100})],
    jwt.ImmatureSignatureError: [lambda: _pyjwt_error({"nbf": _now() + 1000})],
    jwt.InvalidIssuerError: [lambda: _pyjwt_error({"iss": "a"}, issuer="b")],
    jwt.InvalidIssuedAtError: [lambda: _pyjwt_error({"iat": "x"})],
    jwt.MissingRequiredClaimError: [
        (lambda c=claim: _missing(c)) for claim in ("iss", "exp", "nbf", "aud")
    ],
    jwt.InvalidAlgorithmError: [lambda: _pyjwt_error({}, algorithms=["RS256"])],
}


def test_every_kept_pyjwt_class_has_a_producer() -> None:
    assert set(auth_mod._FIXED_TEXT_CLAIM_ERRORS) == set(_PYJWT_PRODUCERS)


@pytest.mark.parametrize(
    "produce",
    [p for ps in _PYJWT_PRODUCERS.values() for p in ps],
)
def test_every_kept_pyjwt_text_survives_the_sanitiser(produce: Any) -> None:
    exc = produce()
    assert isinstance(exc, auth_mod._FIXED_TEXT_CLAIM_ERRORS)
    text = str(exc)
    mangled = {k: v for k, v in _layers(text).items() if v != text}
    assert mangled == {}, f"{type(exc).__name__}: {text!r} is rewritten: {mangled}"


# --- the challenge: parameters survive, the header is read back intact --------

_ISSUER = "https://issuer.example"
_AUDIENCE = "https://pmcp.example/mcp"
_META = "https://pmcp.example/.well-known/oauth-protected-resource"


def _client(**overrides: Any) -> TestClient:
    with patch("pmcp.transport.http.StreamableHTTPSessionManager", autospec=True) as m:
        instance = m.return_value
        instance.run.return_value.__aenter__ = AsyncMock(return_value=None)
        instance.run.return_value.__aexit__ = AsyncMock(return_value=False)
        instance.handle_request = AsyncMock(return_value=None)
        kwargs: dict[str, Any] = {
            "auth_mode": "resource-server",
            "resource_server_issuer": _ISSUER,
            "resource_server_jwks_url": _SAMPLE_URL,
            "resource_server_audience": _AUDIENCE,
        }
        kwargs.update(overrides)
        app = create_http_app(MagicMock(), **kwargs)
    return TestClient(app, base_url="http://127.0.0.1", raise_server_exceptions=False)


def _challenges() -> list[tuple[str, int, str]]:
    body = {"jsonrpc": "2.0", "method": "notifications/initialized"}
    hs_token = jwt.encode({"iss": _ISSUER}, _KEY, algorithm="HS256")
    out: list[tuple[str, int, str]] = []
    for meta in (None, _META):
        extra = {"protected_resource_metadata_url": meta} if meta else {}
        label = "metadata" if meta else "audience"
        plain = _client(**extra)
        scoped = _client(required_scopes=["admin"], **extra)
        unavailable = AsyncMock(side_effect=ResourceServerJWKSUnavailable("down"))
        missing_scope = AsyncMock(
            side_effect=ResourceServerAuthError(
                "insufficient_scope", "Missing required scope(s): admin"
            )
        )
        cases: list[tuple[str, TestClient, dict[str, str], Any, Any]] = [
            ("401-missing", plain, {}, None, None),
            ("401-invalid", plain, {"Authorization": "Bearer x.y.z"}, None, None),
            (
                "403-scope",
                scoped,
                {"Authorization": f"Bearer {hs_token}"},
                None,
                missing_scope,
            ),
            (
                "503-jwks",
                plain,
                {"Authorization": f"Bearer {hs_token}"},
                unavailable,
                None,
            ),
        ]
        for name, client, headers, jwks_mock, validate_mock in cases:
            jwks = jwks_mock or AsyncMock(return_value={"keys": []})
            with patch("pmcp.transport.http.AsyncJWKS.get_for_token", new=jwks):
                if validate_mock is not None:
                    with patch(
                        "pmcp.transport.http.validate_resource_server_token",
                        new=MagicMock(side_effect=validate_mock.side_effect),
                    ):
                        r = client.post("/mcp", json=body, headers=headers)
                else:
                    r = client.post("/mcp", json=body, headers=headers)
            out.append(
                (f"{name}-{label}", r.status_code, r.headers["www-authenticate"])
            )
    return out


_CHALLENGES = _challenges()


def test_the_challenges_cover_401_403_503() -> None:
    assert sorted({status for _, status, _ in _CHALLENGES}) == [401, 403, 503]


@pytest.mark.parametrize(
    ("case", "status", "header"), _CHALLENGES, ids=[c for c, _, _ in _CHALLENGES]
)
def test_challenge_parameters_survive_the_sanitiser(
    case: str, status: int, header: str
) -> None:
    scheme, _, params = header.partition(" ")
    assert scheme == "Bearer"
    for param in (p.strip() for p in params.split(",")):
        _key, _, value = param.partition("=")
        for text in (param, value.strip('"')):
            mangled = {k: v for k, v in _layers(text).items() if v != text}
            assert mangled == {}, f"{case}: {text!r} is rewritten: {mangled}"


@pytest.mark.parametrize(
    ("case", "status", "header"), _CHALLENGES, ids=[c for c, _, _ in _CHALLENGES]
)
def test_pmcp_reads_its_own_challenge_back(case: str, status: int, header: str) -> None:
    parsed = parse_www_authenticate(header)
    assert parsed is not None
    if "metadata" in case:
        assert parsed.resource_metadata_url == _META
    expected = {
        401: "invalid_token",
        403: "insufficient_scope",
        503: "temporarily_unavailable",
    }
    if case != "401-missing-audience" and case != "401-missing-metadata":
        assert parsed.error == expected[status]
    if status == 403:
        assert parsed.scope == "admin"


def test_the_whole_header_is_redacted_by_the_base_bearer_rule_by_design() -> None:
    """Pinned so a change to it is a decision, not an accident: the base
    `Bearer` rule cannot tell a challenge from a credential, and it is frozen
    (additive-only, #234). pmcp never sanitises its own challenge."""
    header = f'Bearer resource_metadata="{_META}", error="invalid_token"'
    assert (
        sanitize_auth_diagnostic(header) == 'Bearer [REDACTED], error="invalid_token"'
    )


# --- `_canonical_resource()` can never publish "" ------------------------------


def test_metadata_route_refuses_to_start_without_a_canonical_resource() -> None:
    """Unreachable through `normalize_auth_metadata` (it drops any metadata
    URL that is not absolute), so force it: a route with an empty `resource`
    fails at startup instead of serving invalid RFC 9728 metadata."""
    real = http_mod.normalize_auth_metadata

    def relative(*a: Any, **k: Any) -> Any:
        info = real(*a, **k)
        return info.model_copy(
            update={
                "protected_resource_metadata_url": "/.well-known/oauth-protected-resource"
            }
        )

    with patch("pmcp.transport.http.normalize_auth_metadata", relative):
        with pytest.raises(ValueError, match="canonical resource"):
            _client(
                auth_mode="none",
                resource_server_audience=None,
                protected_resource_metadata_url="https://pmcp.example/.well-known/oauth-protected-resource",
            )


# --- the README's prefix-stripping proxy case, measured ------------------------


def _metadata_client(meta: str, audience: str | None = None) -> TestClient:
    return _client(
        auth_mode="none",
        resource_server_audience=audience,
        protected_resource_metadata_url=meta,
        authorization_server_metadata_url=(
            "https://auth.example/.well-known/oauth-authorization-server"
        ),
    )


def test_readme_prefixed_metadata_url_404s_behind_a_stripping_proxy() -> None:
    client = _metadata_client(
        "https://pub.example/pmcp/.well-known/oauth-protected-resource",
        "https://pub.example/pmcp/mcp",
    )
    # What a proxy that strips `/pmcp` forwards:
    assert client.get("/.well-known/oauth-protected-resource").status_code == 404


def test_readme_rfc9728_form_with_audience_serves_the_public_resource() -> None:
    client = _metadata_client(
        "https://pub.example/.well-known/oauth-protected-resource/pmcp/mcp",
        "https://pub.example/pmcp/mcp",
    )
    response = client.get("/.well-known/oauth-protected-resource/pmcp/mcp")
    assert response.status_code == 200
    assert response.json()["resource"] == "https://pub.example/pmcp/mcp"
````
