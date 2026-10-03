# Detailed plan: auth operator messages that survive pmcp's own sanitiser, the README `/mcp` rule behind a prefix-stripping proxy, and no empty `resource`

> Written on main `89559db` (dev0, a team host), worktree `pmcp-326`, branch
> `plan/326-auth-messages`. Every number below was measured on that tree, or on
> the spike of this plan applied to it (CPython 3.10.20). The spike was then
> removed; this PR carries only this file. The spike's patches and test module
> are reproduced verbatim at the end (*Verbatim bodies*).
>
> **Round 2** (after the round-1 panel on Consiliency/pmcp#333): the
> message walk was not sound. It read one positional argument of five call
> names, skipped any shape it did not know, and hid a shrinking list behind a
> `>= 28` floor. Claude's seat showed four mangled future messages staying
> green; codex showed a `description=` keyword staying green and the log line
> `Streamable-HTTP session manager started` rewritten. The walk is now
> fail-closed (Design decision 2), and the derived set is pinned exactly.
> The log question is settled by reading the sinks: no log record passes
> through the sanitiser (*Which sinks redact what*). Also taken: N1 (the
> startup text names the API parameter), N2 and N3 (the README separates CLI
> and embedding deployments; the `root_path` example matches the recommended
> form), N4 (the tripwire also asserts the literal path serves 200), and the
> loopback-normalisation nit. All numbers below were re-measured on the
> round-2 spike.

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

### Which sinks redact what

Measured by reading every sink, because round 1 disagreed on it:

| Sink | What is redacted | Where |
|---|---|---|
| `ResourceServerAuthError.description` / `str()` (and `ResourceServerJWKSUnavailable`) | the whole message, by `sanitize_auth_diagnostic` (base + additive) | `auth.py:366-372` |
| `describe_exception(exc)` (used by `ClientManager`, `cli.py`, tool handlers for `last_error`, warnings, CLI output) | the whole exception text | `manager.py:111` and callers |
| CLI and `doctor` output | the values interpolated through `sanitize_auth_diagnostic(...)` at each call site | `cli.py:1050-1520`, `cli_commands/doctor.py:66, 87` |
| `policy.py` output | `redact_additive` on its result | `policy.py:794-799` |
| **log records** | **nothing as a whole.** `setup_logging` (`cli.py:70-105`) installs a plain `logging.Formatter` or `_JsonFormatter` (which emits `record.getMessage()` verbatim) on a stderr handler and a `RotatingFileHandler`; no `Filter`, no record factory (`grep -rn 'addFilter\|logging.Filter\|setLogRecordFactory' src/pmcp` is empty); `pmcp logs` (`cli.py:1575`) prints the file raw. A log line is redacted only where its call site sanitises an interpolated value. | — |

So the claude seat was right that pmcp never sanitises a log record, and
codex's `Streamable-HTTP session [REDACTED] started` is what the text becomes
*if* put through the sanitiser, which no sink does. **Log templates are
therefore not reworded.** They are still walked and pinned (so the walk is
proven to see them), and `test_no_log_sink_applies_the_sanitiser` fails the
day a filter, a record factory or a redacting `_JsonFormatter` is added; at
that point the log templates join the sanitiser check and the two
`session manager` lines need rewording.

Raised `ValueError`s from `create_http_app` and `auth.py` reach an operator
through `describe_exception` (sanitised) or a traceback (raw); they are
checked as if sanitised, the conservative choice.

### The class, derived from the code

The walk (`_Walk` in the test module) reads `auth.py` and `transport/http.py`:
- **Message calls:** every call to a callee in `_MESSAGE_CALLS` --
  `ResourceServerAuthError` (arg 1 or `description=`),
  `ResourceServerJWKSUnavailable` (arg 0 or `description=`), `ValueError`,
  `RuntimeError`, `TypeError` (arg 0), urllib's `HTTPError` (arg 2 or
  `msg=`), `_reject` (arg 1 or `body=`) and `Response` (arg 0 or
  `content=`) -- reading the positional argument **and** the keyword.
- **Every `raise <Call>`:** a callee not in that table is reported as
  unclassified and fails, so a new exception type cannot slip past.
- **Logger calls:** `logger|log|logging.<level>(msg, ...)`, positional or
  `msg=`; collected as templates and pinned (see the sinks section).
- **Rendering:** string constants; f-strings; `+`; `%` with a constant
  template; `.format` with a constant template; and a name assigned exactly
  once in the enclosing function or module, resolved to its value. Holes
  filled from config (`{self.url}`, `" ".join(missing_scopes)`) take a
  sample from `_HOLE_SAMPLES`; any other hole makes the message unrenderable.
- **Fail-closed:** everything the walk cannot render is recorded as (module,
  function, source) and must equal a reviewed exemption list, as a multiset.
  On the spike it is five entries: `str(exc)` in
  `validate_resource_server_token` (pyjwt's text, checked by the pyjwt
  half); `body` in `_reject` (its callers' literals are collected);
  `_generate_latest()` and `'\n'.join(lines) + '\n'` in `handle_metrics`
  (Prometheus exposition, not a message); and the body-less `Response(
  status_code=202)` in `handle_mcp`.
- **Pinned:** the derived (module, text) multiset must equal
  `_PINNED_FIXED` exactly (39 texts on the spike), and the log templates
  `_PINNED_LOGS` (16). A floor such as `>= 28` let the round-1 list shrink.

pyjwt's own texts are generated from pyjwt itself for every class in
`_FIXED_TEXT_CLAIM_ERRORS` (`auth.py:585`), whose text pmcp keeps verbatim:
9 texts. Round 2 adds to the walk `auth.py`'s own `ValueError`s (5 URL texts),
`HTTPError`'s `Redirects are not allowed.`, and the round-2 startup text; all
pass. **On main, 4 texts are rewritten**, and 3 of them predate #325:

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
passes both layers unchanged. The walk finds no other mangled message in any
shape (the claude seat's independent walk of every string literal in both
modules agrees: the only other rewritten literals are the log lines above
and the in-memory `f"Bearer {auth_token}"` comparison value).

Not walked, and measured clean by the round-1 claude seat:
`normalize_auth_metadata`'s `"<field> ignored: …"` / `"… is relayed
unverified: …"` diagnostics for all five field names, and
`UNVERIFIED_URL_CAVEAT`.

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
  `https` URL with a netloc. Measured: `/.well-known/x`,
  `pmcp.example/.well-known/x`, `http://pmcp.example/...`, `https:///x`, and
  (round-1 nit) loopback `http://127.0.0.1:3344/...` and
  `http://localhost/...` all normalize to `None`.
- **The `pmcp` CLI never mounts the route at all.** `server.py:975-986` builds
  `create_http_app` without `protected_resource_metadata_url`, and the CLI
  has no such flag; the round-1 claude seat ran `pmcp.cli.main()` with a spy
  for the none, resource-server and shared-secret modes and saw only
  `/mcp /health /metrics`. The metadata route, and with it everything in
  this section and in the prefix-proxy table above, is reachable only from
  an application that embeds `create_http_app`.
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

### 2. The class test is a fail-closed walk with an exact pin

Round 1's walk failed open: it could not see a keyword argument, a variable,
a `%`/`.format` template or a non-`ValueError` startup refusal, and the
`>= 28` floor hid the count dropping from 80 to 78. The round-2 walk
(Research summary) fixes that as a class:
- **positional and keyword** message arguments, for every message call;
- **every `raise`** of a call is classified or fails
  (`test_every_raised_callee_is_classified`);
- **templates and single-assignment names are rendered**; anything else is
  recorded, and the recorded set must equal the reviewed exemptions as a
  multiset (`test_every_message_the_walk_cannot_render_is_reviewed`) -- a
  second unrenderable message in a function that already has an exempt one
  is caught;
- **exact pins** for the derived texts and the log templates
  (`test_the_derived_set_is_exactly_the_pinned_set`,
  `test_the_log_templates_are_exactly_the_pinned_set`), with the comparison
  itself tested (`test_the_pin_and_the_exemptions_are_exact_not_floors`);
- **every shape from round 1 is a regression test** on a synthetic module
  (`test_the_walk_renders_every_message_shape[keyword|variable|percent|format|runtime_error]`,
  each carrying a text the sanitiser really rewrites), plus
  `test_the_walk_fails_closed_on_what_it_cannot_render` (an f-string with an
  unknown hole, an opaque call, an unknown exception class, a log line);
- `test_every_kept_pyjwt_class_has_a_producer` fails when a class is added
  to `_FIXED_TEXT_CLAIM_ERRORS` without a generator.

Pinning texts means a PR that adds or rewords a message in these two files
updates `_PINNED_FIXED` (or `_PINNED_LOGS`). That is deliberate: it is the
one-line review step that makes the derivation unable to shrink silently.
Line numbers are never pinned.

Every text is checked against all three of `_sanitize_base`,
`redact_additive` and the composed `sanitize_auth_diagnostic`, and every
stored auth description is built through `ResourceServerAuthError` and
compared, end to end.

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

**README change (verbatim below), two paragraphs.**
- **CLI deployments** (the README's own nginx example is one): behind a
  proxy that strips a prefix, MCP's public URL is `https://<host>/pmcp/mcp`;
  set `--oauth-audience` to it. That is all the CLI needs: it publishes no
  metadata route (round-1 N2, measured above).
- **Applications embedding `create_http_app`:** set
  `resource_server_audience` to the public MCP URL; a prefixed metadata URL
  behind that proxy returns `404`; use the RFC 9728 path form
  `https://<host>/.well-known/oauth-protected-resource/pmcp/mcp`, forwarded
  unchanged; and an app that mounts PMCP under a `root_path` must route that
  path to the mounted app, which answers it at
  `/pmcp/.well-known/oauth-protected-resource/pmcp/mcp` (round-1 N3: the
  round-1 text gave the doubled prefix of the *prefixed* URL, not of the
  recommended form; the claude seat measured both).

Two tests pin the README's claims: one that the prefixed URL returns 404
behind the stripping proxy **and still returns 200 at its literal path**
(round-1 N4: without the 200, the tripwire would also pass if the route
stopped mounting), and one that the RFC 9728 form plus the audience serves
`resource = https://pub.example/pmcp/mcp`. **The 404 test is a
tripwire:** it pins today's behaviour so the README stays true. The
follow-up issue's fix must invert it, and that issue's text says so.

**Follow-up: filed as Consiliency/pmcp#334** by the maintainer from this
plan's draft. Round 2 changes its premise, so its text needs one correction
(not edited from here): the route exists only for applications embedding
`create_http_app`, never for the `pmcp` CLI, so the affected deployments are
embedders, and the working configuration uses `resource_server_audience`
(the API parameter), not `--oauth-audience`.

### 5. Empty `resource`: fail at startup, not assert, not omit, not leave

- **Not omit:** RFC 9728 §2 makes `resource` REQUIRED, so metadata without it
  is invalid for every client.
- **Not a request-time `assert`:** it would turn a public, unauthenticated
  endpoint into a 500 for every caller, and `python -O` strips it.
- **Not "leave":** the branch is dead today, but if it is ever reached it
  publishes invalid metadata silently (forced on main: `200 {"resource": ""}`).
- **Decision:** when the metadata route is about to be mounted
  (`http.py:740`), check `_canonical_resource()` once and raise `ValueError("
  Protected-resource metadata needs a canonical resource: set
  resource_server_audience (--oauth-audience) or an absolute
  protected_resource_metadata_url.")` from `create_http_app`. The text names
  the API parameters first (round-1 N1): the route is reachable only through
  the API, where the CLI flag does not exist. This fails closed at
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
- The metadata route, the 404 and the empty `resource` are reachable only
  from an application embedding `create_http_app`; the `pmcp` CLI never
  passes a metadata URL (round 2).

## Changes

The round-2 spike diff is 3 files, 47 insertions and 9 deletions: `auth.py`
13 lines, `transport/http.py` 14 lines and `README.md` 29 lines. It also
adds a new test module.

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
  of Design decision 5, with a comment; its text names
  `resource_server_audience` and `protected_resource_metadata_url`.

### `README.md` (modify)
- The `resource` paragraph (`README.md:177-184`): rewritten into two
  paragraphs (CLI, then embedding applications) as described in Design
  decision 4 (verbatim below).

### `tests/test_auth_operator_messages.py` (create)
98 tests on 3.10 (about 0.3 s), no network, no timed sleeps:

| Test | Count | Pins |
|---|---|---|
| `test_every_fixed_message_survives_the_sanitiser[<module>:<line>]` | 39 | the class: each derived text through base, additive and composed |
| `test_a_stored_description_is_the_text_written[...]` | 16 | the end-to-end `.description` for each distinct auth-error description (the `{self.url}` texts with the sample URL filled in) |
| `test_the_derived_set_is_exactly_the_pinned_set`, `test_the_log_templates_are_exactly_the_pinned_set`, `test_the_pin_and_the_exemptions_are_exact_not_floors` | 3 | exact pins, as multisets |
| `test_every_message_the_walk_cannot_render_is_reviewed`, `test_every_raised_callee_is_classified` | 2 | fail-closed walk |
| `test_the_walk_renders_every_message_shape[format, keyword, percent, runtime_error, variable]`, `test_the_walk_fails_closed_on_what_it_cannot_render` | 5 + 1 | the round-1 shapes, on synthetic modules |
| `test_no_log_sink_applies_the_sanitiser` | 1 | the sink finding: logs are not sanitised |
| `test_every_kept_pyjwt_class_has_a_producer`, `test_every_kept_pyjwt_text_survives_the_sanitiser[...]` | 1 + 9 | pyjwt's kept texts |
| `test_the_challenges_cover_401_403_503`, `test_challenge_parameters_survive_the_sanitiser[...]`, `test_pmcp_reads_its_own_challenge_back[...]` | 1 + 8 + 8 | `WWW-Authenticate` |
| `test_the_whole_header_is_redacted_by_the_base_bearer_rule_by_design` | 1 | the deliberate exception |
| `test_metadata_route_refuses_to_start_without_a_canonical_resource` | 1 | Design decision 5 |
| `test_readme_prefixed_metadata_url_404s_behind_a_stripping_proxy` (404 stripped, 200 literal), `test_readme_rfc9728_form_with_audience_serves_the_public_resource` | 2 | the README's claims |

The f-string texts are checked with the sample URL filled in. The URL rule
leaves a clean URL unchanged, so a mangled result can only come from the
fixed words around it.

## Documentation impact

- `README.md`: Design decision 4.
- `CHANGELOG.md`: one bullet under `## [Unreleased]` → `### Fixed`, phrased
  "see Consiliency/pmcp#326" (no closing keyword). Four auth and startup
  messages are reworded, because pmcp's own sanitiser rewrote them (`Token
  [REDACTED] not be verified…`). A test now derives every fixed auth and HTTP
  message from the code, fail-closed, and checks it comes through the
  sanitiser. The
  protected-resource metadata route now refuses to start rather than publish
  an empty `resource`, which no shipped configuration reaches. The README
  documents the prefix-stripping proxy case.
- `SECURITY.md`: no change. No ledger claim names these texts;
  `scripts/check_security_claims.py` reports `OK … 129 cited node id(s)` on
  the spike.
- No log-format change: the round-2 finding is that log records are not
  sanitised (Research summary), so no log template is reworded.

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
# 1. the new module (round-2 spike: 98 passed, ~0.3 s)
uv run pytest tests/test_auth_operator_messages.py --cov-fail-under=0 -p no:cacheprovider -q
# 2. the suites that touch auth, the HTTP transport and the redactor (round-2 spike: 644 passed, 55 deselected, 0 failed, 60 s)
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
#    (round-2 spike, run alone: 5027 passed, 3 skipped, 80 deselected, 0 failed, 552 s)
nohup uv run pytest -q -p no:cacheprovider > "$WORKTREE_ROOT/pmcp-326-full.log" 2>&1 &
```

**Red on main.** The module against `89559db`'s `auth.py` and `http.py`
(README is not imported): **9 failed, 88 passed** (the derivation finds no
metadata startup text on main, so one fewer case). The ids below are main's
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
| `test_the_derived_set_is_exactly_the_pinned_set` | `new: [('auth.py', 'Missing bearer token.'), …]; gone: […]` (main's four texts against the pin) |
| `test_metadata_route_refuses_to_start_without_a_canonical_resource` | `DID NOT RAISE ValueError` (main serves `200 {"resource": ""}`) |

The README tests, the challenge tests, the pyjwt tests, the walk's own
guards and the log tests **pass on main**. That is by design: they pin
behaviour this plan keeps, and the mutants below show each can fail.

## Acceptance criteria

- [ ] Every fixed message in `auth.py` and `transport/http.py`, derived from
  the code by a fail-closed walk (39 on the spike, pinned exactly), comes
  through `_sanitize_base`, `redact_additive`
  and `sanitize_auth_diagnostic` unchanged. Proven by
  `test_every_fixed_message_survives_the_sanitiser`.
- [ ] A `ResourceServerAuthError` stores each fixed description exactly as
  written. Proven by `test_a_stored_description_is_the_text_written`.
- [ ] The walk reads positional and keyword message arguments, renders
  names, f-strings, `%` and `.format`, classifies every `raise`, and fails on
  any message it cannot render outside the reviewed exemptions. Proven by
  the walk-guard tests and the five shape tests; mutants C1–C9 and K1–K9.
- [ ] No log sink applies the sanitiser, and the log templates are pinned.
  Proven by `test_no_log_sink_applies_the_sanitiser` and
  `test_the_log_templates_are_exactly_the_pinned_set`.
- [ ] Every pyjwt text pmcp keeps comes through unchanged, and every kept class
  has a producer. Proven by the two pyjwt tests.
- [ ] Every `WWW-Authenticate` parameter of the eight 401/403/503 challenges
  comes through unchanged, and pmcp parses each challenge back. Proven by the
  two challenge tests. The whole-header exception is pinned by
  `test_the_whole_header_is_redacted_by_the_base_bearer_rule_by_design`.
- [ ] The metadata route never publishes an empty `resource`; it refuses to
  start instead. Proven by
  `test_metadata_route_refuses_to_start_without_a_canonical_resource`.
- [ ] The README says a CLI deployment behind a prefix-stripping proxy needs
  only `--oauth-audience`; for embedding applications it names
  `resource_server_audience`, the 404, the RFC 9728 metadata path form and
  the `root_path` route. The claims are pinned by the two `test_readme_…`
  tests.
- [ ] Consiliency/pmcp#334's text is corrected by the maintainer for the
  embedding-only premise (Design decision 4).
- [ ] Verification steps 1–3 pass. The CHANGELOG entry is present, with no
  closing keyword.
- [ ] Every mutant below is red.

## Mutation table

Each mutant was measured on the round-2 spike (`mutants.py`: apply one
string replacement to a source file or to the test module, run
`tests/test_auth_operator_messages.py` with a 300 s cap, restore from the
saved copy in a `finally`). **24 of 25 are red; K7 survives by construction
(below).** After the run, all three files were byte-identical to the spike.
Ids in brackets are the spike's line numbers.

| # | Rule | Mutant | Red tests (measured) |
|---|---|---|---|
| B1 | the 401 text is clean | revert to `Token could not be verified…` | 3: the pin, `…survives_the_sanitiser[auth.py:631]`, `…stored_description…[Token could…]` |
| B2 | the empty-token text is clean | revert to `Missing bearer token.` | 3: the pin, `…[auth.py:649]`, `…stored_description…[Missing…]` |
| B3 | the algorithm text is clean | revert to `Unsupported token algorithm.` | 3: the pin, `…[auth.py:656]`, `…stored_description…[Unsupported…]` |
| B4 | the shared-secret text is clean | revert | 2: the pin, `…[transport/http.py:318]` |
| B5 | no empty `resource` | the startup check → `if False:` | `test_metadata_route_refuses_to_start_without_a_canonical_resource` |
| B6 | a new positional description is checked | `Invalid audience.` → `Token audience mismatch.` | 3: the pin, `…[auth.py:672]`, `…stored_description…` |
| B7 | challenge parameters are covered | `_auth_headers` adds `error_description="token expired"` | 6: `test_challenge_parameters_survive_the_sanitiser[{401-invalid,403-scope,503-jwks}-{audience,metadata}]` |
| C1 | keyword argument (codex's example) | `ResourceServerAuthError("invalid_token", description="Token audience mismatch.")` | 3: the pin, `…[auth.py:672]`, `…stored_description…` |
| C2 | text in a variable (claude) | `message = "Token could not be verified."` then `…(…, message)` | 3: the pin, `…[auth.py:661]`, `…stored_description…` |
| C3 | `%` template (claude) | `"JWKS URL is required for token %s." % "checks"` | 3: the pin, `…[auth.py:660]`, `…stored_description…` |
| C4 | non-`ValueError` refusal (claude) | `raise RuntimeError("Unsupported secret mode.")` | 2: the pin, `…[transport/http.py:314]` |
| C5 | unknown exception class | `raise ConfigError("Unsupported auth mode.")` | 2: the pin, `test_every_raised_callee_is_classified` |
| C6 | unrenderable message | `f"Invalid token {exc}."` | 3: the pin, `test_every_message_the_walk_cannot_render_is_reviewed`, `…exact_not_floors` |
| C7 | a message hidden from the walk | `_reject(*(403, "Forbidden"))` (a second `<no message>` in a function with an exempt one) | 2: the pin, the exemption check (multiset) |
| C8 | a new log line (codex) | add `logger.info("Streamable-HTTP session manager ready")` | `test_the_log_templates_are_exactly_the_pinned_set` |
| C9 | a message moves to an unknown callee | `Response("Too Many Requests", …)` → `PlainTextResponse(…)` | the pin **only** -- the case the pin exists for |
| K1 | walk reads keywords | ignore `call.keywords` | `test_the_walk_renders_every_message_shape[keyword]` |
| K2 | walk resolves names | skip `ast.Name` | `…[variable]` |
| K3 | walk renders `%` | match `Pow` instead of `Mod` | `…[percent]` |
| K4 | walk renders `.format` | match `.never` | `…[format]` |
| K5 | unknown `raise` reported | drop the report | `test_the_walk_fails_closed_on_what_it_cannot_render` |
| K6 | comparison counts multiplicity | compare `set(found)` | 2: the pin, `…exact_not_floors` |
| K7 | the pin is exact | replace the pin assertion with `len(...) >= 28` | **survives** on the spike tree: it deletes an assertion, and only a tree with a dropped message can show it. Measured double mutant K7 + C9: **97 passed** -- with the floor, a message moving to an unknown callee goes unseen, which is exactly the round-1 defect and why the pin is exact |
| K8 | log calls collected | drop `self.logs.append` | 2: the log pin, `…fails_closed…` |
| K9 | comparison is equality, not subset | report only missing items | `…exact_not_floors` |

The implementer re-runs all 25 on the final tree, restoring from a saved copy
(never `git checkout --`).

## Non-goals

- **The metadata 404 behind a prefix-stripping proxy.** This is a separate
  issue (Design decision 4).
- **Changing the sanitiser's base rules** so that `Bearer resource="…"` or
  `token <word>` survives. Those rules are frozen (#234 additive-only), and
  loosening them is the fail-open direction.
- **Sending `error_description` in challenges.** pmcp does not send it today,
  and adding a client-visible field belongs in its own change.
- **Rewording log templates.** No log sink sanitises a record (Research
  summary). `Streamable-HTTP session manager started/stopped` would be
  rewritten if one did; `test_no_log_sink_applies_the_sanitiser` is the
  tripwire for that.
- **Walking `normalize_auth_metadata`'s list-appended diagnostics and
  `UNVERIFIED_URL_CAVEAT`.** They are not message calls; the round-1 claude
  seat measured all of them clean. Adding `list.append` as a sink would need
  a sample for each hole (field names, sanitised URLs) for no current
  finding.

## Unverified

- **A real reverse proxy.** Setups B–E were measured with Starlette's
  `TestClient` sending the path a stripping proxy would forward, not with
  nginx.
- **Python 3.11 / 3.12.** The module was run on 3.10 only. It is pure string
  and AST work plus `TestClient`, with no version-specific asyncio.
- **The full suite** was run once on the round-2 spike, alone and detached
  (5027 passed, 3 skipped, 80 deselected, 0 failed, 552 s; the 80 deselected are the default marker exclusions).

## Execution Policy

- execute: effort=low.
- reason: four string changes, one startup check and two README paragraphs.
  The fail-closed walk is the substance, and its pins are what a later PR
  touching these two files will meet.
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
index 41af495..c5b4b5d 100644
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
@@ -769,6 +771,16 @@ def create_http_app(
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
+                    "set resource_server_audience (--oauth-audience) or an "
+                    "absolute protected_resource_metadata_url."
+                )
             routes.append(
                 Route(
                     metadata_path,
````

### Patch — `README.md`

````diff
diff --git a/README.md b/README.md
index 8af5061..4d22d09 100644
--- a/README.md
+++ b/README.md
@@ -178,11 +178,30 @@ window are checked against the cached keys. The `resource` published at
 `/.well-known/oauth-protected-resource` is the configured
 `resource_server_audience`, or, when that is unset, the origin of the configured
 protected-resource metadata URL plus `/mcp`, with a scheme-default port such as
-`:443` dropped. A path prefix in the metadata URL is not carried over, because
-PMCP serves MCP at `/mcp`. If MCP is reachable at a different public URL, set
-`--oauth-audience` to publish it. The `resource` is never taken from the
-request `Host`. A forged token, including one whose algorithm does not match
-the published key's type, gets `401`, never `500`.
+`:443` dropped. A path prefix in the metadata URL is not carried over: PMCP
+serves MCP at `/mcp` on the address it listens on, and that is the public URL
+only when nothing in front of PMCP rewrites the path. Behind a reverse proxy
+that strips a path prefix (for example nginx
+`location /pmcp/ { proxy_pass http://127.0.0.1:3344/; }`), MCP's public URL
+is `https://<host>/pmcp/mcp`; set `--oauth-audience` to that URL so the token
+audience names it. That is all a `pmcp` CLI deployment needs: the CLI does
+not publish protected-resource metadata, so it serves no metadata route.
+
+The metadata URL is a `create_http_app` parameter
+(`protected_resource_metadata_url`) for applications that embed PMCP. There,
+set `resource_server_audience` to the public MCP URL as well, so the published
+`resource` names it, and pick a metadata URL whose path reaches PMCP
+unchanged: PMCP serves the metadata at the URL's literal path, so
+`https://<host>/pmcp/.well-known/oauth-protected-resource` behind that proxy
+arrives as `/.well-known/oauth-protected-resource` and returns `404`. Use the
+RFC 9728 form for a resource with a path,
+`https://<host>/.well-known/oauth-protected-resource/pmcp/mcp`, and forward
+that path to PMCP unchanged. An application that mounts PMCP under a
+`root_path` must route that path to the mounted app as well: mounted at
+`/pmcp`, it answers at `/pmcp/.well-known/oauth-protected-resource/pmcp/mcp`,
+not at the URL's own path. The `resource` is never taken from the request
+`Host`. A forged token, including one whose algorithm does not match the
+published key's type, gets `401`, never `500`.
 In public auth metadata URLs it rejects hosts written as non-public **IP
 literals** — private, CGNAT, link-local, loopback, multicast, site-local, and
 unspecified — including IPv4 addresses embedded in IPv6 literals and legacy
````

### File — `tests/test_auth_operator_messages.py`

````python
"""Consiliency/pmcp#326: every fixed operator-facing auth message survives
pmcp's own diagnostic sanitiser unchanged.

`ResourceServerAuthError.__init__` runs its description through
`sanitize_auth_diagnostic`, so a fixed text the sanitiser reads as a
credential is stored mangled: "Token could not be verified with the
published key." became "Token [REDACTED] not be verified with the published
key.". The list below is DERIVED from the code: an AST walk of `auth.py` and
`transport/http.py` that reads every message-carrying call (every `raise`,
the auth error classes, the response constructors), positional and keyword
arguments, resolving single-assignment variables, f-strings, `%` and
`.format` templates. Whatever it cannot render must be in a reviewed
exemption list, a `raise` of an unknown callee fails, and the derived set is
pinned exactly -- so the walk fails closed rather than skipping a shape it
does not know. The pyjwt classes `_FIXED_TEXT_CLAIM_ERRORS` keeps the text of
are generated and checked separately.

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

# A message part filled from config at run time, keyed by `ast.unparse` of the
# expression. A new hole fails the derivation until it gets a sample here.
_HOLE_SAMPLES = {
    "self.url": _SAMPLE_URL,
    "' '.join(missing_scopes)": "mcp:read mcp:write",
}

# Every call that carries a message: callee -> (positional index, keyword
# names). Both are read. A `raise` of a callee missing here is reported as
# unclassified, so a new exception type cannot slip past the walk.
_MESSAGE_CALLS: dict[str, tuple[int, tuple[str, ...]]] = {
    "ResourceServerAuthError": (1, ("description",)),
    "ResourceServerJWKSUnavailable": (0, ("description",)),
    "ValueError": (0, ()),
    "RuntimeError": (0, ()),
    "TypeError": (0, ()),
    "HTTPError": (2, ("msg",)),  # urllib's (url, code, msg, hdrs, fp)
    "_reject": (1, ("body",)),  # http.py: the 401/403/503 response body
    "Response": (0, ("content",)),  # http.py: 413/429/504 bodies
}
# The descriptions an auth error stores (sanitised in `__init__`).
_STORED = {"ResourceServerAuthError", "ResourceServerJWKSUnavailable"}

_LOG_LEVELS = {"debug", "info", "warning", "warn", "error", "exception", "critical"}
_LOGGERS = {"logger", "log", "logging"}

# Messages the walk cannot render, each reviewed. Keyed by (module, enclosing
# function, `ast.unparse` of the argument) -- never by line number.
_EXEMPT_UNRENDERED = sorted(
    [
        # pyjwt's own text; `_FIXED_TEXT_CLAIM_ERRORS` keeps it, and the pyjwt half
        # below generates and checks every one.
        ("auth.py", "validate_resource_server_token", "str(exc)"),
        # `_reject`'s pass-through: every caller's literal body is collected.
        ("transport/http.py", "create_http_app._reject", "body"),
        # Prometheus exposition text, not a message.
        ("transport/http.py", "create_http_app.handle_metrics", "_generate_latest()"),
        (
            "transport/http.py",
            "create_http_app.handle_metrics",
            "'\\n'.join(lines) + '\\n'",
        ),
        # `202 Accepted` with no body.
        ("transport/http.py", "create_http_app.handle_mcp", "<no message>"),
    ]
)

_MODULES = {"auth.py": auth_mod, "transport/http.py": http_mod}


class _Walk:
    """One pass over a module: the messages it renders, the ones it cannot,
    the `raise`s of unclassified callees, and the log templates."""

    def __init__(self, rel: str, tree: ast.Module) -> None:
        self.rel = rel
        self.fixed: list[tuple[str, str, str]] = []  # (where, callee, text)
        self.unrendered: list[tuple[str, str, str]] = []
        self.unclassified: list[str] = []
        self.logs: list[str] = []
        self._visit(tree, [], [tree])

    def _visit(self, node: ast.AST, scope: list[str], frames: list[ast.AST]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self._visit(child, [*scope, child.name], [*frames, child])
                continue
            if isinstance(child, ast.ClassDef):
                self._visit(child, [*scope, child.name], frames)
                continue
            if isinstance(child, ast.Raise) and isinstance(child.exc, ast.Call):
                name = _callee(child.exc)
                if name not in _MESSAGE_CALLS:
                    self.unclassified.append(
                        f"{self.rel}:{child.lineno} raise {name}(...)"
                    )
            if isinstance(child, ast.Call):
                self._call(child, scope, frames)
            self._visit(child, scope, frames)

    def _call(self, call: ast.Call, scope: list[str], frames: list[ast.AST]) -> None:
        func = call.func
        if (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id in _LOGGERS
            and func.attr in _LOG_LEVELS
        ):
            arg = _message_arg(call, 0, ("msg",))
            self.logs.append(ast.unparse(arg) if arg is not None else "<no message>")
            return
        name = _callee(call)
        if name not in _MESSAGE_CALLS:
            return
        index, keywords = _MESSAGE_CALLS[name]
        arg = _message_arg(call, index, keywords)
        where = ".".join(scope)
        if arg is None:
            self.unrendered.append((self.rel, where, "<no message>"))
            return
        text = _render(arg, frames)
        if text is None:
            self.unrendered.append((self.rel, where, ast.unparse(arg)))
        else:
            self.fixed.append((f"{self.rel}:{call.lineno}", name, text))


def _callee(call: ast.Call) -> str:
    func = call.func
    return func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")


def _message_arg(
    call: ast.Call, index: int, keywords: tuple[str, ...]
) -> ast.expr | None:
    for keyword in call.keywords:
        if keyword.arg in keywords:
            return keyword.value
    if len(call.args) > index and not isinstance(call.args[index], ast.Starred):
        return call.args[index]
    return None


def _resolve(name: str, frames: list[ast.AST]) -> ast.expr | None:
    """The value of a local or module name assigned exactly once."""
    for frame in reversed(frames):
        values = [
            node.value
            for node in ast.walk(frame)
            if isinstance(node, (ast.Assign, ast.AnnAssign))
            and node.value is not None
            and any(
                isinstance(t, ast.Name) and t.id == name
                for t in (
                    node.targets if isinstance(node, ast.Assign) else [node.target]
                )
            )
        ]
        if values:
            return values[0] if len(values) == 1 else None
    return None


def _render(node: ast.expr, frames: list[ast.AST]) -> str | None:
    """The text a message expression produces, holes filled from
    `_HOLE_SAMPLES`. `None` means it cannot be rendered."""
    source = ast.unparse(node)
    if source in _HOLE_SAMPLES:
        return _HOLE_SAMPLES[source]
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        value = _resolve(node.id, frames)
        return None if value is None else _render(value, frames)
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant):
                parts.append(str(value.value))
                continue
            assert isinstance(value, ast.FormattedValue)
            part = _render(value.value, frames)
            if part is None:
                return None
            parts.append(part)
        return "".join(parts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _render(node.left, frames), _render(node.right, frames)
        return None if left is None or right is None else left + right
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
        template = _render(node.left, frames)
        items = node.right.elts if isinstance(node.right, ast.Tuple) else [node.right]
        values = [_render(item, frames) for item in items]
        if template is None or any(v is None for v in values):
            return None
        return template % tuple(values)
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "format"
        and not node.keywords
    ):
        template = _render(node.func.value, frames)
        values = [_render(a, frames) for a in node.args]
        if template is None or any(v is None for v in values):
            return None
        return template.format(*values)
    return None


def _walk_all() -> list[_Walk]:
    walks = []
    for rel, module in _MODULES.items():
        path = Path(module.__file__ or "")
        walks.append(_Walk(rel, ast.parse(path.read_text())))
    return walks


_WALKS = _walk_all()
_FIXED = [(where, text) for w in _WALKS for where, _name, text in w.fixed]

# The exact derived set, as (module, text) with multiplicity. A message that
# drops out of the walk -- or a new one -- changes this, so the derivation
# cannot shrink silently. Update it when a message is added or reworded.
_PINNED_FIXED = sorted(
    [
        ("auth.py", "Empty token."),
        ("auth.py", "Invalid JWKS JSON from " + _SAMPLE_URL + "."),
        ("auth.py", "Invalid JWKS object from " + _SAMPLE_URL + "."),
        ("auth.py", "Invalid URL-mode elicitation URL."),
        ("auth.py", "Invalid audience."),
        ("auth.py", "Invalid public auth URL."),
        ("auth.py", "Invalid token."),
        ("auth.py", "JWKS URL is required."),
        ("auth.py", "JWKS contains no usable signing keys."),
        (
            "auth.py",
            "JWKS endpoint returned a redirect for "
            + _SAMPLE_URL
            + "; refusing to follow.",
        ),
        ("auth.py", "JWKS fetch failed for " + _SAMPLE_URL + "."),
        ("auth.py", "JWKS fetch failed for " + _SAMPLE_URL + "."),
        (
            "auth.py",
            "JWKS refresh recently failed for " + _SAMPLE_URL + "; backing off.",
        ),
        ("auth.py", "JWKS response too large for " + _SAMPLE_URL + "."),
        ("auth.py", "Missing required scope(s): mcp:read mcp:write"),
        ("auth.py", "No matching JWK found."),
        (
            "auth.py",
            "Public auth URL host is a non-public IP literal or loopback name.",
        ),
        ("auth.py", "Public auth URL must be an absolute HTTP(S) URL."),
        ("auth.py", "Public auth URL only allows http:// URLs for loopback hosts."),
        ("auth.py", "Redirects are not allowed."),
        ("auth.py", "The published key cannot verify this token."),
        ("auth.py", "The token's algorithm is not supported."),
        ("transport/http.py", "Forbidden"),
        ("transport/http.py", "Forbidden"),
        ("transport/http.py", "Forbidden"),
        ("transport/http.py", "Gateway Timeout"),
        ("transport/http.py", "Gateway Timeout"),
        ("transport/http.py", "Payload Too Large"),
        ("transport/http.py", "Payload Too Large"),
        (
            "transport/http.py",
            "Protected-resource metadata needs a canonical resource: set "
            "resource_server_audience (--oauth-audience) or an absolute "
            "protected_resource_metadata_url.",
        ),
        ("transport/http.py", "Resource Server JWKS is not configured."),
        ("transport/http.py", "Service Unavailable"),
        ("transport/http.py", "Too Many Requests"),
        ("transport/http.py", "Unauthorized"),
        ("transport/http.py", "Unauthorized"),
        ("transport/http.py", "Unauthorized"),
        ("transport/http.py", "Unsupported auth mode."),
        (
            "transport/http.py",
            "auth_token is required when auth_mode is shared-secret.",
        ),
        (
            "transport/http.py",
            "resource-server auth mode requires issuer, JWKS URL, and audience.",
        ),
    ]
)

# Log templates, pinned the same way so the walk is proven to see them. They
# are NOT checked against the sanitiser: no log record passes through it (see
# `test_no_log_sink_applies_the_sanitiser`). Several would be rewritten if one
# did -- `Streamable-HTTP session manager started` becomes `Streamable-HTTP
# session [REDACTED] started` -- which is why that test exists.
_PINNED_LOGS = sorted(
    [
        "'Streamable-HTTP session manager started'",
        "'Streamable-HTTP session manager stopped'",
        "'handle_mcp [%s]: %s method=%s session=%s accept=%r'",
        "'handle_mcp [%s]: 401 invalid token'",
        "'handle_mcp [%s]: 401 missing bearer'",
        "'handle_mcp [%s]: 401 unauthorized'",
        "'handle_mcp [%s]: 403 insufficient scope'",
        "'handle_mcp [%s]: 403 invalid host'",
        "'handle_mcp [%s]: 403 invalid origin'",
        "'handle_mcp [%s]: 413 Content-Length over cap'",
        "'handle_mcp [%s]: 413 body exceeded cap during read'",
        "'handle_mcp [%s]: 429 rate limited ip=%s'",
        "'handle_mcp [%s]: 503 jwks unavailable'",
        "'handle_mcp [%s]: body read timed out after %ss'",
        "'handle_mcp [%s]: request timed out after %ss'",
        "'handle_mcp [%s]: rmcp-compat accepted notifications/initialized "
        "without session ID'",
    ]
)


def _multiset_diff(found: list[Any], expected: list[Any]) -> str:
    """'' when `found` equals `expected` as a multiset, else what differs.
    Multiplicity counts: a second `<no message>` in a function that already
    has a reviewed one is a new, unreviewed entry."""
    extra, missing = list(found), []
    for item in expected:
        if item in extra:
            extra.remove(item)
        else:
            missing.append(item)
    return "" if not extra and not missing else f"new: {extra}; gone: {missing}"


def _derived() -> list[tuple[str, str]]:
    return sorted((where.rsplit(":", 1)[0], text) for where, text in _FIXED)


def _unrendered() -> list[tuple[str, str, str]]:
    return sorted(u for w in _WALKS for u in w.unrendered)


def test_the_derived_set_is_exactly_the_pinned_set() -> None:
    assert _multiset_diff(_derived(), _PINNED_FIXED) == ""


def test_every_message_the_walk_cannot_render_is_reviewed() -> None:
    assert _multiset_diff(_unrendered(), _EXEMPT_UNRENDERED) == ""


def test_the_pin_and_the_exemptions_are_exact_not_floors() -> None:
    """The comparison itself: one more, one fewer, or one duplicated entry
    each fails (a floor such as `>= 28` let the round-1 list shrink)."""
    for found, expected in (
        (_derived(), _PINNED_FIXED),
        (_unrendered(), _EXEMPT_UNRENDERED),
    ):
        assert _multiset_diff(found + [found[0]], expected) != ""
        assert _multiset_diff(found[1:], expected) != ""
        assert (
            _multiset_diff([*found[1:], ("x", "y", "z")[: len(found[0])]], expected)
            != ""
        )


def test_every_raised_callee_is_classified() -> None:
    assert [u for w in _WALKS for u in w.unclassified] == []


def test_the_log_templates_are_exactly_the_pinned_set() -> None:
    assert sorted(t for w in _WALKS for t in w.logs) == _PINNED_LOGS


def test_no_log_sink_applies_the_sanitiser() -> None:
    """Which sinks redact log records: none. `setup_logging` (`cli.py`)
    installs a plain `logging.Formatter` or `_JsonFormatter` (which emits
    `record.getMessage()` as is) on a stderr and a rotating-file handler, no
    filter; `pmcp logs` prints the file raw. Values are sanitised where a call
    site interpolates them (`describe_exception`, `sanitize_auth_diagnostic`),
    never the whole record. If a filter, a record factory or a formatter that
    redacts is ever added, log templates join the sanitiser check above."""
    src = Path(auth_mod.__file__ or "").parent
    hits = [
        f"{p.relative_to(src)}: {needle}"
        for p in sorted(src.rglob("*.py"))
        for needle in ("addFilter(", "logging.Filter", "setLogRecordFactory(")
        if needle in p.read_text()
    ]
    assert hits == []
    from pmcp import cli

    tree = ast.parse(Path(cli.__file__ or "").read_text())
    formatter = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.ClassDef) and n.name == "_JsonFormatter"
    )
    assert "sanitize" not in ast.unparse(formatter)
    assert "redact" not in ast.unparse(formatter)


_SHAPES = {
    # Claude's four review shapes and codex's keyword example: each carries a
    # text the sanitiser really rewrites, so on the real tree each turns
    # `test_every_fixed_message_survives_the_sanitiser` red.
    "keyword": 'raise ResourceServerAuthError("invalid_token", '
    'description="Token audience mismatch.")',
    "variable": 'message = "Token could not be verified."\n'
    'raise ResourceServerAuthError("invalid_token", message)',
    "percent": 'raise ResourceServerAuthError("invalid_token", '
    '"JWKS URL is required for token %s." % "checks")',
    "format": 'raise ValueError("Token {} rejected.".format("checks"))',
    "runtime_error": 'raise RuntimeError("Unsupported secret mode.")',
}


@pytest.mark.parametrize("shape", sorted(_SHAPES))
def test_the_walk_renders_every_message_shape(shape: str) -> None:
    source = "def f():\n" + "".join(
        f"    {line}\n" for line in _SHAPES[shape].split("\n")
    )
    walk = _Walk("auth.py", ast.parse(source))
    assert walk.unclassified == [] and walk.unrendered == []
    [(_where, _name, text)] = walk.fixed
    assert _layers(text) != {k: text for k in ("base", "additive", "composed")}, (
        f"{shape}: the sample text {text!r} should be one the sanitiser rewrites"
    )


def test_the_walk_fails_closed_on_what_it_cannot_render() -> None:
    source = (
        "def f(x):\n"
        "    raise ResourceServerAuthError('invalid_token', f'bad {x}')\n"
        "    raise ResourceServerAuthError('invalid_token', compute())\n"
        "    raise BrandNewError('Token could not be verified.')\n"
        "    logger.info('Streamable-HTTP session manager started')\n"
    )
    walk = _Walk("auth.py", ast.parse(source))
    assert sorted(walk.unrendered) == sorted(
        [
            ("auth.py", "f", "f'bad {x}'"),
            ("auth.py", "f", "compute()"),
        ]
    )
    assert walk.unclassified == ["auth.py:4 raise BrandNewError(...)"]
    assert walk.logs == ["'Streamable-HTTP session manager started'"]


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
    sorted({text for w in _WALKS for _, name, text in w.fixed if name in _STORED}),
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
    # ...and the route is still mounted, at the URL's literal path only. This
    # is the behaviour the follow-up (Consiliency/pmcp#334) changes; its fix
    # must invert the 404 above.
    response = client.get("/pmcp/.well-known/oauth-protected-resource")
    assert response.status_code == 200
    assert response.json()["resource"] == "https://pub.example/pmcp/mcp"


def test_readme_rfc9728_form_with_audience_serves_the_public_resource() -> None:
    client = _metadata_client(
        "https://pub.example/.well-known/oauth-protected-resource/pmcp/mcp",
        "https://pub.example/pmcp/mcp",
    )
    response = client.get("/.well-known/oauth-protected-resource/pmcp/mcp")
    assert response.status_code == 200
    assert response.json()["resource"] == "https://pub.example/pmcp/mcp"
````
