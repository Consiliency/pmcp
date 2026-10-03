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
>
> **Round 3** (after the round-2 panel; codex marked these blocking against
> the plan's own fail-closed criterion, and the coordinator agreed): the walk
> still failed open on four more shapes -- `message += ...` (rendered the
> first assignment's text), an exception built and then raised by name, an
> alias raised by name, and a subclass whose message sits in
> `super().__init__` -- and the log tripwire missed a redacting *text*
> formatter selected in `setup_logging`. The walk now discovers exception
> classes at test time, collects `super().__init__` messages, resolves
> `raise <name>`, and refuses any name with a binding other than one plain
> assignment; the log test is behavioural. All numbers re-measured on the
> round-3 spike.
>
> **Round 4** (after the round-3 panel; claude DISAGREE, codex 3 blocking):
> four more shapes beat the static collector -- a subclass whose message is a
> class constant or a `self.` attribute, a `+=` on a constructor parameter, a
> comprehension walrus that rebinds the enclosing name, and an alias resolved
> in the wrong scope. This was the fourth round in which a collector was
> beaten, so round 4 **changes the design so the bad state cannot exist**:
> every fixed operator-facing text lives in one registry, `pmcp.auth.
> AuthMessage`, and the code cannot pass anything else -- the auth error
> classes and the 401/403/503 response helper raise `TypeError` for a
> non-member, and a syntax-only check (no resolver) requires every raise and
> `_reject` site to name `AuthMessage.<NAME>`. The resolver, scope walker,
> exemption multiset and pins are gone (Design decision 2). Logs are checked
> end to end (claude N3). All numbers below are re-measured on the round-4
> spike, which now changes source in `auth.py` and `transport/http.py` beyond
> the four rewordings.
>
> **Round 5** (after the round-4 panel; gemini clean, claude one blocker,
> codex two that overlap it): the registry design holds; five holes in its
> enforcement are closed. Membership is checked by identity, not type, and
> `PyJwtText` can only be minted by `pyjwt_text`; placeholder fields must be
> exactly the template's, and a programming error inside the JWKS fetch is no
> longer re-wrapped as a 503; the static check accepts `raise <name>` only
> inside the handler that binds it, counts every raise, requires field values
> to be runtime names, and accepts `pyjwt_text(<name>)` only inside `except
> _FIXED_TEXT_CLAIM_ERRORS as <name>`. Numbers below are re-measured on the
> round-5 spike.
>
> **Round 6** (after the round-5 panel; gemini clean, claude nothing
> blocking, codex two blockers): `_auth_response` now goes through the one
> identity-checked guard, and a structural test forbids any other
> `isinstance(…, AuthText)` check; every field *value* must have its
> placeholder's shape at run time (an absolute http(s) URL, an RFC 6749 scope
> list), so prose bound to a variable or constant fails at construction;
> the static check compares each site's field names with its member's
> placeholders, and a test drives the JWKS redirect path; the registry test
> pins named-only fields, and the renderer always formats. Numbers below are
> re-measured on the round-6 spike.

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
| **log records** | **nothing as a whole.** Measured end to end (round 4, claude N3): the real `setup_logging` in `text` and `json` mode, every one of the 16 log templates logged through the real `pmcp.transport.http` logger, and the text that reached stderr and the rotating log file read back -- each template appears verbatim. That covers a filter, a record factory, a formatter and a handler's own `emit()`. `pmcp logs` (`cli.py:1575`) prints the file raw. A log line is redacted only where its call site sanitises an interpolated value. | — |

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

### The class: every fixed message, and why a collector could not hold it

On main the operator-facing texts in `auth.py` and `transport/http.py` are
string literals and f-strings at 29 sites: 15 `ResourceServerAuthError` /
`ResourceServerJWKSUnavailable` descriptions (7 of them with a `{self.url}`
and one with `+ " ".join(missing_scopes)`), 8 startup or URL-validation
`ValueError`s, urllib's `HTTPError("Redirects are not allowed.")`, and the
`_reject` bodies `Unauthorized` / `Forbidden` / `Service Unavailable`. pyjwt's
own text is kept verbatim for the classes in `_FIXED_TEXT_CLAIM_ERRORS`
(9 texts, generated from pyjwt). **On main, 4 texts are rewritten**, 3 of them
predating #325:

| Site (main) | Text | Rewritten by | Comes out as |
|---|---|---|---|
| `auth.py:629` (#325) | `Token could not be verified with the published key.` | base keyword rule (`Token <word>`) | `Token [REDACTED] not be verified with the published key.` |
| `auth.py:644` | `Missing bearer token.` | base `Bearer` rule | `Missing bearer [REDACTED]` |
| `auth.py:649` | `Unsupported token algorithm.` | base keyword rule | `Unsupported token [REDACTED]` |
| `http.py:316` | `shared-secret auth mode requires auth_token.` | base keyword rule (`secret <word>`) | `shared-secret [REDACTED] mode requires auth_token.` |

The additive layer rewrites none of them; every other text, and all 9 pyjwt
texts, pass both layers unchanged.

Rounds 1–3 kept the texts inline and tried to *derive* them from the code by
static analysis. Each round, a reviewer wrote a shape the analysis read
wrongly: a keyword argument, a variable, a `%` template, a non-`ValueError`
refusal (round 1); `+=`, a raise by name, an alias, a subclass's
`super().__init__` (round 2); a subclass class constant or `self.` attribute,
a `+=` on a parameter, a comprehension walrus, an alias resolved in the
wrong scope (round 3). An analyser that must model Python's binding rules
fails open on the next shape. Round 4 removes the need to model them.

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

Round 4: the four texts are registry members, with one comment in the
registry naming the rule that bit each one. **Not
changing the sanitiser** is deliberate. Making the base keyword or `Bearer`
rule spare these words would loosen a credential rule (the fail-open
direction), and the base rules are frozen (#234 additive-only). The texts
are pmcp's own, so changing them is the safe side.

No test, doc or CHANGELOG line asserts any of the four old texts (`grep -rn`
over `tests src *.md`). The only hit, `tests/test_redaction_additive.py:82`,
is corpus prose that contains the words "Missing bearer token". None of the
four reaches the wire, so no client sees a change.

### 2. One registry; the code cannot pass anything else (round 4)

**Decision:** every fixed operator-facing text in `auth.py` and
`transport/http.py` becomes a member of one registry, `pmcp.auth.AuthMessage`
(29 members), and the paths that carry them accept only members.

- **`AuthText(str)`** is the member type; members exist only as `AuthMessage`
  attributes. Being a `str` subclass, a member works unchanged wherever a
  string did (`ValueError(AuthMessage.X)` prints the text).
- **Runtime-valued messages stay in the registry as templates.** Seven JWKS
  texts carry the configured JWKS URL and one carries the missing scope
  names, which are runtime values. They are members with `{url}` /
  `{scopes}` placeholders, filled by the constructor
  (`ResourceServerJWKSUnavailable(AuthMessage.JWKS_FETCH_FAILED,
  url=self.url)`), so the fixed words are still checked; only configuration
  is interpolated. No message needed to leave the registry for this.
- **One narrow pass-through:** `pyjwt_text(exc)` returns a `PyJwtText` only
  for an instance of `_FIXED_TEXT_CLAIM_ERRORS`, and `TypeError` otherwise
  (`auth.py:676`'s `str(exc)` becomes `pyjwt_text(exc)`). The pyjwt half of
  the tests keeps generating and checking those 9 texts.
- **The constructors refuse anything else, always.**
  `ResourceServerAuthError.__init__` renders through
  `render_auth_message(description, **fields)`, which raises `TypeError` for
  anything that is not an `AuthText` or `PyJwtText`; `ResourceServerJWKSUnavailable`
  passes through it; `_reject` now calls a module-level `_auth_response`,
  which raises `TypeError` for a non-member body. A subclass cannot avoid
  it: whatever it passes to `super().__init__` -- a class constant, a
  `self.` attribute, a `+=`-built parameter -- arrives at the same check.
  **Always on, not test-only:** these are internal APIs with a handful of
  call sites, the check is one `isinstance` per error, and a test-only check
  would let a production-only code path (an error raised under load, say)
  carry a mangled text unseen.
- **A syntax-only static check covers sites no test reaches.** In `auth.py`
  and `transport/http.py`:
  - every `raise` is bare, a re-raise of an `except ... as` name, or a call
    of `ResourceServerAuthError`, `ResourceServerJWKSUnavailable`,
    `ValueError` or `HTTPError` (plus `TypeError` inside the three guard
    functions only);
  - the message argument of those calls and of every `_reject` /
    `_auth_response` call is **literally** `AuthMessage.<NAME>` for an
    existing member (or `pyjwt_text(<name>)` for `ResourceServerAuthError`);
  - the error code of `ResourceServerAuthError` is one of the three RFC 6750
    codes;
  - no class in the two modules subclasses an exception other than the two
    auth error classes;
  - `AuthText(...)` / `PyJwtText(...)` are built only inside the registry and
    `pyjwt_text`.

  The check reads syntax only -- no name resolution, no scopes -- so a
  walrus, an alias, an augmented assignment or a subclass constant is simply
  "not `AuthMessage.<NAME>`" and fails. `test_the_site_check_sees_every_site`
  pins that it visits all 31 raises and 7 `_reject` calls.
- **What went away:** the resolver, the scope walker, the exemption multiset,
  the derived-text and log-template pins, and the K-family of collector
  mutants. There is nothing to derive: the registry *is* the list.

**Round 5: what the registry still let through, and how each is closed.**

| Hole (round-4 panel) | Repro | Closed by |
|---|---|---|
| Membership was type, not identity (codex 1, claude N2) | `ResourceServerAuthError("invalid_token", AuthText("Token expired."))` stored `Token [REDACTED]`; `str.__new__(AuthText, ...)` too | `render_auth_message` accepts an `AuthText` only if `id(message)` is in `_MEMBER_IDS`, built once at import from the registry |
| `PyJwtText` could be built anywhere | `str.__new__(PyJwtText, ...)` | `PyJwtText.__new__` needs a private token only `pyjwt_text` holds, and the guard refuses an instance that does not carry it |
| Fields unchecked (claude B1, codex's `scopes=`) | a literal field value reached the sanitiser; a missing field published `{url}`; a misnamed one raised `KeyError`, which `AsyncJWKS` turned into a wrong 503 and a backoff | the guard requires `set(fields)` to equal the member's placeholders (`string.Formatter().parse`, precomputed); the static check requires every field value to be a name or attribute (the one call site that built a value inline, `" ".join(missing_scopes)`, now binds `scope_names` first) |
| A programming error posed as a fetch failure | a `TypeError` in `_fetch` came out as `JWKS_FETCH_FAILED` and opened the backoff | `AsyncJWKS.get` and `_fetch` re-raise `TypeError`/`KeyError` ahead of their broad `except Exception` |
| Named raises (codex 2) | `exc = RuntimeError("Token expired."); raise exc` passed because the check collected handler names module-wide; named raises were left out of the pin | `raise <name>` is accepted only lexically inside the `except ... as <name>` that binds it (within the same function), with no rebinding in the handler; every raise is counted (34) |
| `pyjwt_text` placement (claude N1) | moving it to the `InvalidTokenError` site turned every malformed-token 401 into a 500, caught only by accident (an import-time collection error) | `pyjwt_text(<name>)` is accepted only inside `except _FIXED_TEXT_CLAIM_ERRORS as <name>`; the challenge tests now build their requests lazily, so the same mutant fails tests instead of erroring the module |

**Round 6: the two remaining holes, and claude's notes.**

| Hole (round-5 panel) | Repro | Closed by |
|---|---|---|
| `_auth_response` checked type (codex 1) | `_auth_response(401, AuthText("Token expired."))` and `str.__new__(AuthText, …)` reached the `Response` | `_auth_response` returns `Response(render_auth_message(body), …)`: the one identity-checked guard. `test_isinstance_auth_text_is_only_used_to_enumerate_the_registry` fails on any other `isinstance(…, AuthText)` in `src/pmcp` (the only one left is `auth_messages()`, which enumerates the registry) |
| A field value bound to prose (codex 2 = claude N2) | `reason = "Token expired."; ResourceServerJWKSUnavailable(AuthMessage.JWKS_FETCH_FAILED, url=reason)` stored `… Token [REDACTED]`; `url=_PRETEND_URL` likewise | the renderer checks each field *value's shape*: `{url}` must be an absolute http(s) URL with no whitespace -- `_url_field`, which reuses `_is_absolute_http`, the rule `sanitize_public_auth_url` already applies to every configured auth URL (factored out, behaviour unchanged); `{scopes}` must be an RFC 6749 §3.3 scope list (`scope-token *( SP scope-token )`, `scope-token = 1*NQCHAR`). Anything else is a `TypeError` at construction, wherever the value came from. Every registry placeholder has a validator; an unknown one fails at import (`_check_registry_fields`) |
| A misnamed field on an untested path (claude N1) | `JWKS_REDIRECT_REFUSED, uri=self.url` passed every test; in production a 500 on every request while the JWKS redirects | the static check compares each site's keyword names with its literal member's placeholders (`misnamed_field`, `missing_field` shapes); `test_the_jwks_redirect_path_raises_its_registry_message` drives a real 302 through `_fetch` |
| `importlib.reload` (claude N3) | `_MEMBER_IDS` holds one import's identities, so a reloaded `pmcp.auth` refuses the members `http.py` still holds | documented next to `_MEMBER_IDS`: reload both modules together. No test reloads |
| Registry hygiene (claude N4) | an escaped `{{url}}` or bare `{}` member would have been published verbatim by the no-fields shortcut | the renderer always calls `.format(**fields)`, and `test_every_registry_placeholder_has_a_validator_and_no_odd_braces` allows only named identifier fields with no spec or conversion |

**Residual, stated (the field validators):** a shape check cannot tell a
scope list from prose made only of scope-token characters -- `Token
expired.` with no quotes is two well-formed RFC 6749 scope-tokens. The
`{scopes}` field has one producer, `validate_resource_server_token`, which
computes it from `required_scopes`; the validator stops prose with spaces in
the wrong places, quotes or other non-NQCHAR characters, and an empty or
non-string value. Tracking where a string came from statically is the arms
race rounds 1–3 lost, so the plan stops at the shape. A configured URL or
scope that the sanitiser itself redacts (a scope named `secret:read`) stays
a runtime value, as the scope-name non-goal says.

**The other broad catches, checked (claude's list):** `auth.py:1184`
(`fetch_json_metadata`) turns any failure of a metadata fetch into a
diagnostic string, and `http.py:686` ignores an unparseable request body;
neither wraps an auth error constructor or a registry render, so a refused
message cannot be laundered through them. They are left as they are --
re-raising `TypeError` there would change unrelated behaviour.

**Why not keep the collector and add rules for the four new shapes:** each
rule would close one shape and leave Python's binding rules to the next
reviewer. Construction-time refusal holds for any shape that reaches a
constructor, and the static check is a pattern match that does not need to
understand what a shape means.

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

The round-6 spike diff is 6 files, 352 insertions and 66 deletions:
most of it in `auth.py` (the registry, its guard and the field validators),
then `transport/http.py`, `README.md` (unchanged since round 2) and three
existing test files. It also adds a new test module.

### `src/pmcp/auth.py` (modify)
- After the imports: `AuthText(str)`, `PyJwtText(str)`, the registry
  `AuthMessage` (29 members, with a comment on the four rewordings),
  `auth_messages()`, `pyjwt_text(exc)` and `render_auth_message(message,
  **fields)` (Design decision 2).
- `ResourceServerAuthError.__init__(error, description, **fields)` renders
  through `render_auth_message` (a `TypeError` for a non-member), then
  sanitises as before; `ResourceServerJWKSUnavailable(description, **fields)`.
- Every raise site: the literal or f-string becomes `AuthMessage.<NAME>`
  (with `url=self.url` / `scopes=" ".join(missing_scopes)` where it had
  them); `str(exc)` becomes `pyjwt_text(exc)`; `HTTPError`'s message and the
  five URL-validation `ValueError`s use members. The four reworded texts are
  members `KEY_CANNOT_VERIFY_TOKEN`, `EMPTY_TOKEN`,
  `TOKEN_ALGORITHM_UNSUPPORTED` and (in http.py's use)
  `SHARED_SECRET_NEEDS_TOKEN`.

### `src/pmcp/transport/http.py` (modify)
- Import `AuthMessage`, `AuthText`. New module-level `_auth_response(status,
  body, headers)` refuses a non-member body; the `_reject` closure keeps its
  metric and calls it.
- The four startup `ValueError`s, the `RS_JWKS_NOT_CONFIGURED` description
  and the seven `_reject` bodies use members.
- The metadata-route startup check of Design decision 5 (unchanged since
  round 2, now `ValueError(AuthMessage.METADATA_NEEDS_RESOURCE)`).

### `README.md` (modify)
- The `resource` paragraph (`README.md:177-184`): rewritten into two
  paragraphs (CLI, then embedding applications) as described in Design
  decision 4 (verbatim below).

### `tests/test_auth.py`, `tests/test_http_transport.py`, `tests/test_scoped_advisor_audit.py` (modify)
Measured by the full suite, not foreseen: these construct the auth errors
with plain strings, which the always-on check now refuses (28 failures in
the first full run: 2 in `test_http_transport.py`, 26 parametrized cases in
`test_scoped_advisor_audit.py`). Each site now passes a member:
- `test_auth.py`: four fake `_fetch`es raise
  `ResourceServerJWKSUnavailable(AuthMessage.JWKS_FETCH_FAILED, url=...)`.
  They passed even with plain strings, because `AsyncJWKS.get` re-wraps any
  other exception as `JWKS_FETCH_FAILED` -- so a `TypeError` there was
  silently absorbed; with members they test what they say again.
- `test_http_transport.py`: the 403 and 503 contract tests use
  `AuthMessage.MISSING_SCOPES, scopes="write"` and `JWKS_FETCH_FAILED`.
- `test_scoped_advisor_audit.py`: `_EXCEPTION_ARGS` gives both auth error
  classes member arguments (it instantiates every exception class `src/pmcp`
  raises).

### `tests/test_auth_operator_messages.py` (create)
157 tests on 3.10 (about 0.7 s), no network, no timed sleeps (one test runs a local TCP server):

| Test | Count | Pins |
|---|---|---|
| `test_the_registry_is_complete_and_typed` | 1 | 29 distinct `AuthText` members |
| `test_every_registry_message_survives_the_sanitiser[NAME]` | 29 | each member, rendered with sample `url`/`scopes`, through base, additive and composed |
| `test_a_stored_description_is_the_text_written[NAME]` | 29 | end to end through `ResourceServerAuthError` |
| `test_a_message_outside_the_registry_is_refused_at_construction[shape]` | 12 | `TypeError` for: literal, keyword, `%`, `.format`, f-string, `JWKSUnavailable(str)`, a plain-`str` copy of a member, and round 3's class constant, `self.` attribute, `+=` parameter, comprehension walrus and shadowed alias |
| `test_an_auth_response_body_outside_the_registry_is_refused`, `test_the_pyjwt_pass_through_is_narrow` | 2 | the other two guards |
| **round 5:** `test_a_value_of_the_right_type_that_is_not_a_member_is_refused[...]`, `test_pyjwt_text_cannot_be_constructed_directly` | 4 + 1 | identity, not type; minted pyjwt text only |
| **round 5:** `test_fields_must_be_exactly_the_placeholders[missing, misnamed, extra, field_on_pyjwt]` | 4 | the placeholder contract |
| **round 5:** `test_a_programming_error_in_the_fetch_is_not_a_503`, `test_a_programming_error_inside_fetch_itself_is_not_rewrapped` | 2 | `TypeError` is not a fetch failure; no backoff opens |
| **round 6:** `test_a_field_value_without_its_placeholder_shape_is_refused[...]`, `test_real_field_values_are_accepted` | 9 + 1 | codex's variable and claude's constant bound to prose, the real redirect member with prose, a relative URL, a URL with a space, a non-string, quoted prose, an empty and a double-spaced scope list; real URLs (IPv6, query) and scope lists still pass |
| **round 6:** `test_an_auth_response_body_of_the_right_type_that_is_not_a_member_is_refused`, `test_isinstance_auth_text_is_only_used_to_enumerate_the_registry` | 2 | the one guard |
| **round 6:** `test_every_registry_placeholder_has_a_validator_and_no_odd_braces`, `test_the_jwks_redirect_path_raises_its_registry_message` | 2 | claude N4 and N1 |
| `test_every_message_site_names_a_registry_member`, `test_the_site_check_sees_every_site`, `test_auth_text_is_built_only_in_the_registry` | 3 | the static check on the real modules |
| `test_the_site_check_refuses_every_shape[shape]` | 21 | the static check on the rounds 1–4 shapes as source (round 5 adds a literal and an f-string field, three named raises -- outside its handler, in another function, rebound in the handler -- and `pyjwt_text` at the `InvalidTokenError` site and outside any handler) |
| `test_the_log_templates_are_collected`, `test_no_log_sink_applies_the_sanitiser[text, json]`, `test_no_redacting_log_hook_in_the_source` | 1 + 2 + 1 | logs, end to end |
| `test_every_kept_pyjwt_class_has_a_producer`, `test_every_kept_pyjwt_text_survives_the_sanitiser[...]` | 1 + 9 | pyjwt's kept texts |
| `test_the_challenges_cover_401_403_503`, `test_challenge_parameters_survive_the_sanitiser[...]`, `test_pmcp_reads_its_own_challenge_back[...]` | 1 + 8 + 8 | `WWW-Authenticate` |
| `test_the_whole_header_is_redacted_by_the_base_bearer_rule_by_design` | 1 | the deliberate exception |
| `test_metadata_route_refuses_to_start_without_a_canonical_resource` | 1 | Design decision 5 |
| `test_readme_prefixed_metadata_url_404s_behind_a_stripping_proxy`, `test_readme_rfc9728_form_with_audience_serves_the_public_resource` | 2 | the README's claims |

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
# 1. the new module (round-6 spike: 157 passed, ~0.7 s)
uv run pytest tests/test_auth_operator_messages.py --cov-fail-under=0 -p no:cacheprovider -q
# 2. the suites that touch auth, the HTTP transport and the redactor (round-6 spike: 911 passed, 55 deselected, 0 failed, 87 s)
uv run pytest tests/test_auth.py tests/test_transport_http.py tests/test_auth_origin_wiring.py \
  tests/test_redaction_additive.py tests/test_auth_operator_messages.py tests/test_cli.py tests/test_server.py \
  tests/test_http_transport.py tests/test_scoped_advisor_audit.py \
  --cov-fail-under=0 -p no:cacheprovider -q
# 3. CI gates (spike: all clean)
uv run ruff check src tests && uv run ruff format --check src tests
uv run mypy src/pmcp/auth.py src/pmcp/transport/http.py
python3 scripts/check_security_claims.py          # expect OK, 129 cited node ids
python3 scripts/check_plan_consistency.py .consiliency/plans/detailed-326-auth-messages-20261003-0226.md
#   measured on this file: "consistent ... blocking inconsistencies: 0", exit 0 (a detailed plan has no roadmap pin)
# 4. the full suite: once, detached, with a notifying waiter (memory on dev0 is shared)
#    (round-6 spike, run alone: 5086 passed, 3 skipped, 80 deselected, 0 failed, 581 s;
#     round 4's first run, before the three existing test files were moved
#     onto members, had 28 failures -- see Changes)
nohup uv run pytest -q -p no:cacheprovider > "$WORKTREE_ROOT/pmcp-326-full.log" 2>&1 &
```

**Red on main.** The round-4 module imports `AuthMessage` from `pmcp.auth`,
so on `89559db` it fails at collection (`ImportError: cannot import name
'AuthMessage'`): every test is red. The texts themselves were measured on
main by the round-3 module, which needs no registry: **9 failed, 107 passed**
-- the four mangled texts (`Token [REDACTED] not be verified…`, `Missing
bearer [REDACTED]`, `Unsupported token [REDACTED]`, `shared-secret
[REDACTED] mode…`), their three stored descriptions, the pin, and the
metadata guard (`DID NOT RAISE ValueError`; main serves `200 {"resource":
""}`).

## Acceptance criteria

- [ ] Every fixed operator-facing message in `auth.py` and
  `transport/http.py` is an `AuthMessage` member (29), and each, rendered
  with sample configuration fields, comes through `_sanitize_base`,
  `redact_additive` and `sanitize_auth_diagnostic` unchanged and is stored as
  written. Proven by `test_every_registry_message_survives_the_sanitiser`
  and `test_a_stored_description_is_the_text_written`.
- [ ] The auth error classes and `_auth_response` raise `TypeError` for any
  message that is not a member (or the narrow `pyjwt_text` pass-through),
  including every shape the panel found in rounds 1–3. Proven by the
  refusal tests; mutants G1, G2, G5.
- [ ] Every raise and `_reject` site in the two modules names
  `AuthMessage.<NAME>` literally, no other exception class is defined, and
  `AuthText` is built only in the registry -- a syntax check with no
  resolver. Proven by the site-check tests; mutants G3, G4, S1–S5.
- [ ] No log sink applies the sanitiser, measured end to end through the
  real logger, stderr and the log file in text and JSON mode. Proven by
  `test_no_log_sink_applies_the_sanitiser[text|json]`; mutants L1–L3.
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
- [ ] Membership is identity (a minted `AuthText` or `PyJwtText` is
  refused); fields must be exactly a member's placeholders; a `TypeError`
  inside the JWKS fetch is not a 503. Proven by the round-5 runtime tests;
  mutants V1–V6.
- [ ] The static check accepts a named raise only inside its own handler,
  counts every raise, refuses literal field values, and confines
  `pyjwt_text` to `except _FIXED_TEXT_CLAIM_ERRORS as <name>`. Proven by the
  round-5 static shapes; mutants V7–V12.
- [ ] `_auth_response` uses the identity-checked guard and no other
  `isinstance(…, AuthText)` exists; every field value has its placeholder's
  shape; each site's field names equal its member's placeholders; the JWKS
  redirect path is driven; the registry has named-only fields. Proven by the
  round-6 tests; mutants X1–X8.
- [ ] Every mutant below is red.

## Mutation table

Each mutant was measured on the round-6 spike (`mutants6.py`: one or more
string edits to `auth.py`, `transport/http.py`, `cli.py` or the test module,
run `tests/test_auth_operator_messages.py` under `-o timeout=60` and a 300 s
cap, restore every touched file from its saved copy in a `finally`). **All
38 are red.** After the run, every file was byte-identical to the spike. X is
round 6, V round 5; G, W, S, L and B carry over (round 4's G2 is replaced by
X1, since `_auth_response` no longer has its own check).

| # | Mutant | Red tests (measured) |
|---|---|---|
| G1 | constructor accepts any non-member (membership check dropped) | 17 red -- a_message_outside_the_registry_is_refused_at_construction[, a_message_outside_the_registry_is_refused_at_construction[, a_message_outside_the_registry_is_refused_at_construction[ (+14) |
| X1 | _auth_response accepts any AuthText (type, not identity; codex r5 1) | 2 red -- an_auth_response_body_of_the_right_type_that_is_not_a_memb, isinstance_auth_text_is_only_used_to_enumerate_the_registr |
| X2 | url field: any value accepted | 1 red -- a_field_value_without_its_placeholder_shape_is_refused[url |
| X3 | scopes field: any str accepted | 3 red -- a_field_value_without_its_placeholder_shape_is_refused[sco, a_field_value_without_its_placeholder_shape_is_refused[sco, a_field_value_without_its_placeholder_shape_is_refused[sco |
| X4 | renderer skips field-shape validation (codex r5 2) | 9 red -- a_field_value_without_its_placeholder_shape_is_refused[red, a_field_value_without_its_placeholder_shape_is_refused[sco, a_field_value_without_its_placeholder_shape_is_refused[sco (+6) |
| X5 | url field: whitespace allowed | 1 red -- a_field_value_without_its_placeholder_shape_is_refused[url |
| X6 | static check ignores field names (claude r5 N1) | 2 red -- the_site_check_refuses_every_shape[misnamed_field], the_site_check_refuses_every_shape[missing_field] |
| X7 | misnamed field at the real redirect site (claude r5 N1) | 2 red -- the_jwks_redirect_path_raises_its_registry_message, every_message_site_names_a_registry_member |
| X8 | registry gains an escaped-brace member (claude r5 N4) | 2 red -- the_registry_is_complete_and_typed, every_registry_placeholder_has_a_validator_and_no_odd_brac |
| V1 | membership checked by type, not identity (codex r4 1 / claude N2) | 5 red -- a_value_of_the_right_type_that_is_not_a_member_is_refused[, a_value_of_the_right_type_that_is_not_a_member_is_refused[, a_value_of_the_right_type_that_is_not_a_member_is_refused[ (+2) |
| V2 | unminted PyJwtText accepted | 1 red -- a_value_of_the_right_type_that_is_not_a_member_is_refused[ |
| V3 | PyJwtText constructible directly | 1 red -- pyjwt_text_cannot_be_constructed_directly |
| V4 | fields not checked against placeholders (claude r4 B1) | 3 red -- fields_must_be_exactly_the_placeholders[extra], fields_must_be_exactly_the_placeholders[misnamed], fields_must_be_exactly_the_placeholders[missing] |
| V5 | get() turns a TypeError into a 503 | 1 red -- a_programming_error_in_the_fetch_is_not_a_503 |
| V6 | _fetch() turns a TypeError into a 503 | 1 red -- a_programming_error_inside_fetch_itself_is_not_rewrapped |
| V7 | static check accepts literal field values | 2 red -- the_site_check_refuses_every_shape[field_f_string], the_site_check_refuses_every_shape[field_literal] |
| V8 | static check accepts any handler-bound name module-wide (codex r4 2) | 3 red -- the_site_check_refuses_every_shape[named_raise_other_funct, the_site_check_refuses_every_shape[named_raise_outside_its, the_site_check_refuses_every_shape[named_raise_rebound_in_ |
| V9 | static check accepts pyjwt_text anywhere (claude N1) | 2 red -- the_site_check_refuses_every_shape[pyjwt_text_at_invalid_t, the_site_check_refuses_every_shape[pyjwt_text_outside_a_ha |
| V10 | pyjwt_text moved to the InvalidTokenError site (claude N1) | 6 red -- every_message_site_names_a_registry_member, the_challenges_cover_401_403_503, challenge_parameters_survive_the_sanitiser[401-invalid-aud (+3) |
| V11 | a literal field value at a real site | 1 red -- every_message_site_names_a_registry_member |
| V12 | a named raise of a new exception at a real site (codex r4 2) | 2 red -- every_message_site_names_a_registry_member, the_site_check_sees_every_site |
| G3 | static check accepts any argument | 11 red -- every_message_site_names_a_registry_member, the_site_check_refuses_every_shape[attribute_not_member], the_site_check_refuses_every_shape[keyword] (+8) |
| G4 | static check accepts unknown raise callees | 2 red -- the_site_check_refuses_every_shape[alias], the_site_check_refuses_every_shape[runtime_error] |
| G5 | pyjwt pass-through accepts any exception | 1 red -- the_pyjwt_pass_through_is_narrow |
| W1 | registry: 401 key-mismatch text reverted | 3 red -- the_registry_is_complete_and_typed, every_registry_message_survives_the_sanitiser[KEY_CANNOT_V, a_stored_description_is_the_text_written[KEY_CANNOT_VERIFY |
| W2 | registry: empty-token text reverted | 2 red -- every_registry_message_survives_the_sanitiser[EMPTY_TOKEN], a_stored_description_is_the_text_written[EMPTY_TOKEN] |
| W3 | registry: algorithm text reverted | 2 red -- every_registry_message_survives_the_sanitiser[TOKEN_ALGORI, a_stored_description_is_the_text_written[TOKEN_ALGORITHM_U |
| W4 | registry: shared-secret text reverted | 2 red -- every_registry_message_survives_the_sanitiser[SHARED_SECRE, a_stored_description_is_the_text_written[SHARED_SECRET_NEE |
| S1 | a site passes a literal | 1 red -- every_message_site_names_a_registry_member |
| S2 | a site mints AuthText inline | 2 red -- every_message_site_names_a_registry_member, auth_text_is_built_only_in_the_registry |
| S3 | a new exception subclass with a constant (claude r3 B1) | 1 red -- every_message_site_names_a_registry_member |
| S4 | an alias raised (codex r3 3) | 2 red -- every_message_site_names_a_registry_member, the_site_check_sees_every_site |
| S5 | a _reject body passed as a literal | 1 red -- every_message_site_names_a_registry_member |
| L1 | redacting text formatter (codex r2) | 1 red -- no_log_sink_applies_the_sanitiser[text] |
| L2 | redacting emit() on the stderr handler (claude r3 N3) | 2 red -- no_log_sink_applies_the_sanitiser[text], no_log_sink_applies_the_sanitiser[json] |
| L3 | a non-constant log template | 1 red -- the_log_templates_are_collected |
| B5 | metadata startup guard removed | 1 red -- metadata_route_refuses_to_start_without_a_canonical_resour |
| B7 | challenge gains a param the sanitiser rewrites | 6 red -- challenge_parameters_survive_the_sanitiser[401-invalid-aud, challenge_parameters_survive_the_sanitiser[403-scope-audie, challenge_parameters_survive_the_sanitiser[503-jwks-audien (+3) |

The implementer re-runs all 38 on the final tree, restoring from a saved copy
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
- **Scope names in `MISSING_SCOPES`** (claude round-4 N3). They are runtime
  values from configuration and token claims, interpolated into the 403
  description, so a scope such as `secret:read` is stored as
  `Missing required scope(s): secret:[REDACTED]` -- as on main. This plan
  makes the fixed words safe, not configuration values; redacting a
  credential-shaped config value is the sanitiser working. A follow-up could
  decide whether scope names should be exempt.
- **Moving `normalize_auth_metadata`'s list-appended diagnostics and
  `UNVERIFIED_URL_CAVEAT` into the registry.** They are diagnostics built
  from sanitised parts and appended to a list, not raised or sent; the
  round-1 claude seat measured all of them clean. They can join the registry
  later; nothing here depends on it.
- **Plain `Response(...)` bodies** (`Too Many Requests`, `Payload Too Large`,
  `Gateway Timeout`). They go on the wire as written and never pass through
  the sanitiser, so they are not operator diagnostics; only the auth
  `_reject` bodies are routed through the registry.

## Unverified

- **A real reverse proxy.** Setups B–E were measured with Starlette's
  `TestClient` sending the path a stripping proxy would forward, not with
  nginx.
- **Python 3.11 / 3.12.** The module was run on 3.10 only. It is pure string
  and AST work plus `TestClient`, with no version-specific asyncio. (On 3.11+
  a `str`-mixin `Enum` would print `AuthMessage.X`; the registry uses a plain
  `str` subclass for exactly that reason, so a member prints its text on
  every version.)
- **The full suite** was run once on the round-6 spike, alone and detached
  (5086 passed, 3 skipped, 80 deselected, 0 failed, 581 s).

## Execution Policy

- execute: effort=low.
- reason: a registry and the call-site changes in two files (about 225/53
  lines), three existing test files moved onto members, one startup check
  and two README paragraphs. Any later PR that adds an auth message meets
  the `TypeError` and the site check, by design.
- Re-run the mutation table, ruff and mypy before requesting review.
- Get a cross-vendor panel CR before merge, as for every PR to main.

## Verbatim bodies

### How to apply

1. Save the source patch, the README patch and the existing-tests patch
   below to `326-src.patch`, `326-readme.patch` and `326-tests.patch`, then
   run `git apply 326-src.patch 326-readme.patch 326-tests.patch` on
   `89559db`.
2. Write the test module below to `tests/test_auth_operator_messages.py`.
3. Add the `CHANGELOG.md` bullet by hand.

### Patch — `src/pmcp/auth.py`, `src/pmcp/transport/http.py`

````diff
diff --git a/src/pmcp/auth.py b/src/pmcp/auth.py
index e929470..3d678c6 100644
--- a/src/pmcp/auth.py
+++ b/src/pmcp/auth.py
@@ -4,9 +4,10 @@ from __future__ import annotations
 
 import json
 import re
+import string
 import time
 import asyncio
-from collections.abc import Mapping
+from collections.abc import Callable, Mapping
 from dataclasses import dataclass
 from ipaddress import IPv4Address, IPv6Address, ip_address, ip_network
 from itertools import product
@@ -24,6 +25,221 @@ from pmcp.redaction_additive import redact_additive
 from pmcp.types import AuthChallengeInfo, AuthMetadataInfo, UrlElicitationInfo
 
 
+class AuthText(str):
+    """One fixed operator-facing auth/HTTP message. Instances exist only as
+    `AuthMessage` attributes (Consiliency/pmcp#326): the auth error classes
+    and the HTTP 401/403/503 response helper refuse anything else, so a
+    message cannot be written inline in a shape a review cannot see."""
+
+    __slots__ = ()
+
+
+_PYJWT_MINT = object()  # held only by `pyjwt_text`
+
+
+class PyJwtText(str):
+    """pyjwt's own text for an error class pmcp keeps verbatim
+    (`_FIXED_TEXT_CLAIM_ERRORS`). Minted only by `pyjwt_text`: the
+    constructor wants a private token, and `render_auth_message` refuses an
+    instance that does not carry it (so `str.__new__(PyJwtText, ...)` does
+    not get through either)."""
+
+    _minted: object
+
+    def __new__(cls, text: str, *, _mint: object = None) -> "PyJwtText":
+        if _mint is not _PYJWT_MINT:
+            raise TypeError("PyJwtText is minted only by pyjwt_text()")
+        obj = super().__new__(cls, text)
+        obj._minted = _PYJWT_MINT
+        return obj
+
+
+class AuthMessage:
+    """The registry of every fixed operator-facing message in `pmcp.auth`
+    and `pmcp.transport.http` (Consiliency/pmcp#326).
+
+    Each one comes through `sanitize_auth_diagnostic` -- the redactor's own
+    rules and the additive rules (#234) -- unchanged; the test module checks
+    every member. `{url}` and `{scopes}` are filled from configuration by the
+    constructor that takes them (`ResourceServerAuthError(..., url=...)`).
+    Add a message here, never inline: the auth error classes and
+    `_auth_response` raise `TypeError` for anything that is not a member, and
+    a static check in the test module requires every raise and `_reject`
+    site to name one.
+    """
+
+    # -- token validation (401 `invalid_token` / 403 `insufficient_scope`) --
+    # Four texts were reworded because the sanitiser rewrote them (#326):
+    # "Missing bearer token." -> "Missing bearer [REDACTED]" (Bearer rule);
+    # "Unsupported token algorithm." -> "Unsupported token [REDACTED]";
+    # "Token could not be verified with the published key." -> "Token
+    # [REDACTED] not be verified..." (keyword rule, `token <word>`); and
+    # "shared-secret auth mode requires auth_token." -> "shared-secret
+    # [REDACTED] mode..." (keyword rule, `secret <word>`).
+    EMPTY_TOKEN = AuthText("Empty token.")
+    TOKEN_ALGORITHM_UNSUPPORTED = AuthText("The token's algorithm is not supported.")
+    JWKS_URL_REQUIRED = AuthText("JWKS URL is required.")
+    KEY_CANNOT_VERIFY_TOKEN = AuthText("The published key cannot verify this token.")
+    NO_MATCHING_JWK = AuthText("No matching JWK found.")
+    INVALID_AUDIENCE = AuthText("Invalid audience.")
+    INVALID_TOKEN = AuthText("Invalid token.")
+    MISSING_SCOPES = AuthText("Missing required scope(s): {scopes}")
+    RS_JWKS_NOT_CONFIGURED = AuthText("Resource Server JWKS is not configured.")
+    # -- JWKS availability (503 `temporarily_unavailable`) --
+    JWKS_BACKING_OFF = AuthText("JWKS refresh recently failed for {url}; backing off.")
+    JWKS_FETCH_FAILED = AuthText("JWKS fetch failed for {url}.")
+    JWKS_REDIRECT_REFUSED = AuthText(
+        "JWKS endpoint returned a redirect for {url}; refusing to follow."
+    )
+    JWKS_TOO_LARGE = AuthText("JWKS response too large for {url}.")
+    JWKS_INVALID_JSON = AuthText("Invalid JWKS JSON from {url}.")
+    JWKS_INVALID_OBJECT = AuthText("Invalid JWKS object from {url}.")
+    JWKS_NO_USABLE_KEYS = AuthText("JWKS contains no usable signing keys.")
+    # -- public auth URL validation --
+    REDIRECTS_NOT_ALLOWED = AuthText("Redirects are not allowed.")
+    PUBLIC_URL_INVALID = AuthText("Invalid public auth URL.")
+    PUBLIC_URL_NOT_ABSOLUTE = AuthText(
+        "Public auth URL must be an absolute HTTP(S) URL."
+    )
+    PUBLIC_URL_HTTP_LOOPBACK_ONLY = AuthText(
+        "Public auth URL only allows http:// URLs for loopback hosts."
+    )
+    PUBLIC_URL_NOT_PUBLIC = AuthText(
+        "Public auth URL host is a non-public IP literal or loopback name."
+    )
+    ELICITATION_URL_INVALID = AuthText("Invalid URL-mode elicitation URL.")
+    # -- HTTP transport startup refusals --
+    UNSUPPORTED_AUTH_MODE = AuthText("Unsupported auth mode.")
+    SHARED_SECRET_NEEDS_TOKEN = AuthText(
+        "auth_token is required when auth_mode is shared-secret."
+    )
+    RESOURCE_SERVER_NEEDS_CONFIG = AuthText(
+        "resource-server auth mode requires issuer, JWKS URL, and audience."
+    )
+    METADATA_NEEDS_RESOURCE = AuthText(
+        "Protected-resource metadata needs a canonical resource: set "
+        "resource_server_audience (--oauth-audience) or an absolute "
+        "protected_resource_metadata_url."
+    )
+    # -- HTTP 401/403/503 response bodies (`_auth_response`) --
+    UNAUTHORIZED = AuthText("Unauthorized")
+    FORBIDDEN = AuthText("Forbidden")
+    SERVICE_UNAVAILABLE = AuthText("Service Unavailable")
+
+
+def auth_messages() -> dict[str, AuthText]:
+    """Every registry member, by name."""
+    return {
+        name: value
+        for name, value in vars(AuthMessage).items()
+        if isinstance(value, AuthText)
+    }
+
+
+# Membership is identity, not type: an `AuthText` minted anywhere else --
+# `AuthText("...")` or `str.__new__(AuthText, ...)` -- is not a member.
+_MEMBER_IDS = frozenset(id(value) for value in auth_messages().values())
+# Each member's placeholder names, which its fields must match exactly.
+_MEMBER_FIELDS = {
+    id(value): frozenset(f for _, f, _, _ in string.Formatter().parse(value) if f)
+    for value in auth_messages().values()
+}
+# Note: `_MEMBER_IDS` holds the identities of THIS import's members. A test
+# that `importlib.reload`s `pmcp.auth` must reload `pmcp.transport.http` too,
+# or http.py keeps passing the old registry's members and every auth
+# response is refused.
+
+# RFC 6749 section 3.3: scope = scope-token *( SP scope-token ),
+# scope-token = 1*NQCHAR, NQCHAR = %x21 / %x23-5B / %x5D-7E.
+_SCOPE_LIST = re.compile(r"[\x21\x23-\x5B\x5D-\x7E]+(?: [\x21\x23-\x5B\x5D-\x7E]+)*")
+
+
+def _is_absolute_http(parsed: Any) -> bool:
+    """The absolute-HTTP(S) rule `sanitize_public_auth_url` applies to every
+    configured auth URL: an http(s) scheme, a netloc and a hostname."""
+    return (
+        parsed.scheme in {"https", "http"}
+        and bool(parsed.netloc)
+        and bool(parsed.hostname)
+    )
+
+
+def _url_field(value: object) -> bool:
+    """A `{url}` field: an absolute http(s) URL with no whitespace -- what a
+    configured JWKS URL is. Prose ("Token expired.") is not."""
+    if not isinstance(value, str) or any(c.isspace() for c in value):
+        return False
+    try:
+        parsed = urlparse(value)
+        _ = parsed.port
+    except ValueError:
+        return False
+    return _is_absolute_http(parsed)
+
+
+def _scopes_field(value: object) -> bool:
+    """A `{scopes}` field: an RFC 6749 scope list (space-separated
+    scope-tokens), what `required_scopes` minus the token's scopes is."""
+    return isinstance(value, str) and _SCOPE_LIST.fullmatch(value) is not None
+
+
+# Every placeholder the registry uses, and the shape its value must have.
+# Code-introduced prose in a field fails here at construction, wherever the
+# value came from (a literal, a variable, a constant).
+_FIELD_VALIDATORS: dict[str, Callable[[object], bool]] = {
+    "url": _url_field,
+    "scopes": _scopes_field,
+}
+
+
+def _check_registry_fields() -> None:
+    """Fail at import if a registry member uses a placeholder that has no
+    validator: every field's shape must be checked."""
+    unvalidated = set().union(*_MEMBER_FIELDS.values()) - set(_FIELD_VALIDATORS)
+    if unvalidated:  # pragma: no cover - a registry edit without a validator
+        raise TypeError(f"AuthMessage placeholders without a validator: {unvalidated}")
+
+
+_check_registry_fields()
+
+
+def pyjwt_text(exc: BaseException) -> PyJwtText:
+    """The one narrow pass-through: pyjwt's text for a class in
+    `_FIXED_TEXT_CLAIM_ERRORS`, whose texts are fixed (the test module
+    generates and checks every one). Anything else is a `TypeError`."""
+    if not isinstance(exc, _FIXED_TEXT_CLAIM_ERRORS):
+        raise TypeError(
+            f"pyjwt_text() takes a fixed-text pyjwt error, not {type(exc).__name__}"
+        )
+    return PyJwtText(str(exc), _mint=_PYJWT_MINT)
+
+
+def render_auth_message(message: AuthText | PyJwtText, **fields: str) -> str:
+    """The text of a registry member with its configuration fields filled,
+    or a pyjwt pass-through. `TypeError` for anything else: a non-member
+    (checked by identity), an unminted `PyJwtText`, or fields that are not
+    exactly the member's placeholders -- a missing field would publish a
+    literal `{url}`, a misnamed one would raise `KeyError` deep in a fetch."""
+    if isinstance(message, PyJwtText):
+        if getattr(message, "_minted", None) is not _PYJWT_MINT or fields:
+            raise TypeError("PyJwtText must come from pyjwt_text(), with no fields")
+        return str(message)
+    if id(message) not in _MEMBER_IDS:
+        raise TypeError(
+            f"auth messages must be an AuthMessage member, not {type(message).__name__}"
+        )
+    expected = _MEMBER_FIELDS[id(message)]
+    if set(fields) != expected:
+        raise TypeError(
+            f"AuthMessage fields must be exactly {sorted(expected)}, "
+            f"got {sorted(fields)}"
+        )
+    bad = sorted(k for k, v in fields.items() if not _FIELD_VALIDATORS[k](v))
+    if bad:
+        raise TypeError(f"AuthMessage field(s) {bad} do not have the expected shape")
+    return message.format(**fields)
+
+
 class _NoRedirectHandler(HTTPRedirectHandler):
     """Refuse HTTP redirects so a public URL cannot 3xx to an internal host."""
 
@@ -36,7 +252,7 @@ class _NoRedirectHandler(HTTPRedirectHandler):
         headers: Any,
         newurl: str,
     ) -> Request | None:
-        raise HTTPError(newurl, code, "Redirects are not allowed.", headers, fp)
+        raise HTTPError(newurl, code, AuthMessage.REDIRECTS_NOT_ALLOWED, headers, fp)
 
 
 _NO_REDIRECT_OPENER = build_opener(_NoRedirectHandler)
@@ -332,22 +548,20 @@ def sanitize_public_auth_url(url: str, *, allow_loopback_http: bool = False) ->
         hostname = parsed.hostname
         _ = parsed.port
     except ValueError as exc:
-        raise ValueError("Invalid public auth URL.") from exc
+        raise ValueError(AuthMessage.PUBLIC_URL_INVALID) from exc
 
-    if parsed.scheme not in {"https", "http"} or not parsed.netloc or not hostname:
-        raise ValueError("Public auth URL must be an absolute HTTP(S) URL.")
+    if not _is_absolute_http(parsed) or not hostname:
+        raise ValueError(AuthMessage.PUBLIC_URL_NOT_ABSOLUTE)
 
     if parsed.scheme == "http" and (
         not allow_loopback_http or not _is_loopback_host(hostname)
     ):
-        raise ValueError("Public auth URL only allows http:// URLs for loopback hosts.")
+        raise ValueError(AuthMessage.PUBLIC_URL_HTTP_LOOPBACK_ONLY)
     if not (
         allow_loopback_http and parsed.scheme == "http" and _is_loopback_host(hostname)
     ):
         if not _is_public_auth_host(hostname):
-            raise ValueError(
-                "Public auth URL host is a non-public IP literal or loopback name."
-            )
+            raise ValueError(AuthMessage.PUBLIC_URL_NOT_PUBLIC)
 
     return redact_auth_url(url)
 
@@ -364,19 +578,29 @@ class ResourceServerTokenClaims:
 
 
 class ResourceServerAuthError(Exception):
-    """Raised for failed Resource Server token validation."""
+    """Raised for failed Resource Server token validation.
 
-    def __init__(self, error: str, description: str) -> None:
+    ``description`` must be an `AuthMessage` member (with its ``url`` /
+    ``scopes`` fields as keywords) or a `pyjwt_text` pass-through; anything
+    else is a `TypeError` at construction (Consiliency/pmcp#326), which a
+    subclass cannot avoid either -- whatever it passes up arrives here.
+    """
+
+    def __init__(
+        self, error: str, description: AuthText | PyJwtText, **fields: str
+    ) -> None:
         self.error = error
-        self.description = sanitize_auth_diagnostic(description)
+        self.description = sanitize_auth_diagnostic(
+            render_auth_message(description, **fields)
+        )
         super().__init__(self.description)
 
 
 class ResourceServerJWKSUnavailable(ResourceServerAuthError):
     """Raised when Resource Server JWKS cannot be fetched."""
 
-    def __init__(self, description: str) -> None:
-        super().__init__("temporarily_unavailable", description)
+    def __init__(self, description: AuthText, **fields: str) -> None:
+        super().__init__("temporarily_unavailable", description, **fields)
 
 
 class AsyncJWKS:
@@ -460,7 +684,7 @@ class AsyncJWKS:
                 if cached is not None:
                     return cached
                 raise ResourceServerJWKSUnavailable(
-                    f"JWKS refresh recently failed for {self.url}; backing off."
+                    AuthMessage.JWKS_BACKING_OFF, url=self.url
                 )
             # (3) A fetch is about to be attempted: the only place the
             # forced-refresh window advances (success or failure alike).
@@ -472,6 +696,11 @@ class AsyncJWKS:
             except ResourceServerJWKSUnavailable:
                 self._last_refresh_failure = time.monotonic()
                 raise
+            except (TypeError, KeyError):
+                # A programming error (e.g. a refused auth message), not an
+                # endpoint failure: never a 503, never a backoff window
+                # (Consiliency/pmcp#326).
+                raise
             except Exception as exc:
                 # Any other failure is still a failed refresh: it opens the
                 # shared backoff and is the same value-free 503, never a 500
@@ -483,7 +712,7 @@ class AsyncJWKS:
                 # stays, because the attempt was made.
                 self._last_refresh_failure = time.monotonic()
                 raise ResourceServerJWKSUnavailable(
-                    f"JWKS fetch failed for {self.url}."
+                    AuthMessage.JWKS_FETCH_FAILED, url=self.url
                 ) from exc
             self._last_refresh_failure = float("-inf")
             self._jwks = jwks
@@ -511,20 +740,21 @@ class AsyncJWKS:
                 ) as response:
                     if 300 <= response.status < 400:
                         raise ResourceServerJWKSUnavailable(
-                            f"JWKS endpoint returned a redirect for {self.url}; "
-                            "refusing to follow."
+                            AuthMessage.JWKS_REDIRECT_REFUSED, url=self.url
                         )
                     response.raise_for_status()
                     content = await response.content.read(self._max_bytes + 1)
-        except ResourceServerJWKSUnavailable:
+        except (ResourceServerJWKSUnavailable, TypeError, KeyError):
+            # TypeError/KeyError: a programming error, not a fetch failure
+            # (Consiliency/pmcp#326).
             raise
         except Exception as exc:
             raise ResourceServerJWKSUnavailable(
-                f"JWKS fetch failed for {self.url}."
+                AuthMessage.JWKS_FETCH_FAILED, url=self.url
             ) from exc
         if len(content) > self._max_bytes:
             raise ResourceServerJWKSUnavailable(
-                f"JWKS response too large for {self.url}."
+                AuthMessage.JWKS_TOO_LARGE, url=self.url
             )
         try:
             jwks = json.loads(content.decode("utf-8"))
@@ -532,10 +762,12 @@ class AsyncJWKS:
             # ValueError covers JSONDecodeError and UnicodeDecodeError; a
             # deeply nested body under the size cap raises RecursionError.
             raise ResourceServerJWKSUnavailable(
-                f"Invalid JWKS JSON from {self.url}."
+                AuthMessage.JWKS_INVALID_JSON, url=self.url
             ) from exc
         if not isinstance(jwks, dict) or not isinstance(jwks.get("keys"), list):
-            raise ResourceServerJWKSUnavailable(f"Invalid JWKS object from {self.url}.")
+            raise ResourceServerJWKSUnavailable(
+                AuthMessage.JWKS_INVALID_OBJECT, url=self.url
+            )
         return jwks
 
 
@@ -557,9 +789,7 @@ def _select_jwk_key(token: str, jwks: Mapping[str, Any]) -> Any:
         # InvalidTokenError, so unmapped it escaped as a 500 (see
         # Consiliency/pmcp#320). Fixed text: never echo pyjwt's message or any
         # JWKS content.
-        raise ResourceServerJWKSUnavailable(
-            "JWKS contains no usable signing keys."
-        ) from exc
+        raise ResourceServerJWKSUnavailable(AuthMessage.JWKS_NO_USABLE_KEYS) from exc
     keys = key_set.keys
     if kid:
         for key in keys:
@@ -567,7 +797,7 @@ def _select_jwk_key(token: str, jwks: Mapping[str, Any]) -> Any:
                 return key.key
     if len(keys) == 1:
         return keys[0].key
-    raise ResourceServerAuthError("invalid_token", "No matching JWK found.")
+    raise ResourceServerAuthError("invalid_token", AuthMessage.NO_MATCHING_JWK)
 
 
 def _claim_scopes(claims: Mapping[str, Any]) -> list[str]:
@@ -626,7 +856,7 @@ def _decode_with_key(
         raise
     except (jwt.PyJWTError, TypeError, ValueError) as exc:
         raise ResourceServerAuthError(
-            "invalid_token", "Token could not be verified with the published key."
+            "invalid_token", AuthMessage.KEY_CANNOT_VERIFY_TOKEN
         ) from exc
 
 
@@ -641,16 +871,18 @@ def validate_resource_server_token(
 ) -> ResourceServerTokenClaims:
     """Validate an AS-issued JWT for PMCP Resource Server mode."""
     if not token:
-        raise ResourceServerAuthError("invalid_token", "Missing bearer token.")
+        raise ResourceServerAuthError("invalid_token", AuthMessage.EMPTY_TOKEN)
     try:
         header = jwt.get_unverified_header(token)
         algorithm = header.get("alg")
         if not isinstance(algorithm, str) or algorithm.lower() == "none":
             raise ResourceServerAuthError(
-                "invalid_token", "Unsupported token algorithm."
+                "invalid_token", AuthMessage.TOKEN_ALGORITHM_UNSUPPORTED
             )
         if jwks is None:
-            raise ResourceServerAuthError("invalid_token", "JWKS URL is required.")
+            raise ResourceServerAuthError(
+                "invalid_token", AuthMessage.JWKS_URL_REQUIRED
+            )
         signing_key = _select_jwk_key(token, jwks)
         claims = _decode_with_key(
             token,
@@ -662,22 +894,26 @@ def validate_resource_server_token(
     except ResourceServerAuthError:
         raise
     except jwt.InvalidAudienceError as exc:
-        raise ResourceServerAuthError("invalid_token", "Invalid audience.") from exc
+        raise ResourceServerAuthError(
+            "invalid_token", AuthMessage.INVALID_AUDIENCE
+        ) from exc
     except _FIXED_TEXT_CLAIM_ERRORS as exc:
         # pyjwt's text for these is fixed (or names a claim from PMCP's own
         # required list), so it is safe to keep as the description.
-        raise ResourceServerAuthError("invalid_token", str(exc)) from exc
+        raise ResourceServerAuthError("invalid_token", pyjwt_text(exc)) from exc
     except jwt.InvalidTokenError as exc:
         # Every other token error may quote the token back (pyjwt names an
         # unknown `crit` extension, for one), so the description is fixed.
-        raise ResourceServerAuthError("invalid_token", "Invalid token.") from exc
+        raise ResourceServerAuthError(
+            "invalid_token", AuthMessage.INVALID_TOKEN
+        ) from exc
 
     scopes = _claim_scopes(claims)
     missing_scopes = sorted(set(required_scopes or []) - set(scopes))
     if missing_scopes:
+        scope_names = " ".join(missing_scopes)
         raise ResourceServerAuthError(
-            "insufficient_scope",
-            "Missing required scope(s): " + " ".join(missing_scopes),
+            "insufficient_scope", AuthMessage.MISSING_SCOPES, scopes=scope_names
         )
     raw_audience = claims.get("aud")
     audiences = raw_audience if isinstance(raw_audience, list) else [raw_audience]
@@ -718,7 +954,7 @@ def sanitize_url_elicitation_url(
             url, allow_loopback_http=provenance == "operator"
         )
     except ValueError as exc:
-        raise ValueError("Invalid URL-mode elicitation URL.") from exc
+        raise ValueError(AuthMessage.ELICITATION_URL_INVALID) from exc
 
 
 def sanitize_auth_diagnostic(value: object, *, max_length: int | None = 400) -> str:
diff --git a/src/pmcp/transport/http.py b/src/pmcp/transport/http.py
index 41af495..c9853f4 100644
--- a/src/pmcp/transport/http.py
+++ b/src/pmcp/transport/http.py
@@ -36,9 +36,12 @@ from starlette.routing import Route
 from pmcp import __version__
 from pmcp.auth import (
     AsyncJWKS,
+    AuthMessage,
+    AuthText,
     ResourceServerAuthError,
     ResourceServerJWKSUnavailable,
     normalize_auth_metadata,
+    render_auth_message,
     sanitize_public_auth_url,
     validate_resource_server_token,
 )
@@ -172,6 +175,18 @@ class _NullResponse(Response):
         pass  # response was already sent by session_manager.handle_request
 
 
+def _auth_response(
+    status_code: int, body: AuthText, headers: dict[str, str] | None = None
+) -> Response:
+    """A 401/403/503 auth response. ``body`` must be an `AuthMessage`
+    member: it goes through the registry's one guard, `render_auth_message`
+    (identity, not type), so anything else is a `TypeError`
+    (Consiliency/pmcp#326)."""
+    return Response(
+        render_auth_message(body), status_code=status_code, headers=headers or {}
+    )
+
+
 async def _check_rate_limit(client_ip: str, max_rpm: int) -> bool:
     """Return True if the request is allowed, False if rate-limited.
 
@@ -311,9 +326,9 @@ def create_http_app(
     if effective_auth_mode is None:
         effective_auth_mode = "shared-secret" if auth_token is not None else "none"
     if effective_auth_mode not in {"none", "shared-secret", "resource-server"}:
-        raise ValueError("Unsupported auth mode.")
+        raise ValueError(AuthMessage.UNSUPPORTED_AUTH_MODE)
     if effective_auth_mode == "shared-secret" and auth_token is None:
-        raise ValueError("shared-secret auth mode requires auth_token.")
+        raise ValueError(AuthMessage.SHARED_SECRET_NEEDS_TOKEN)
     resource_jwks: AsyncJWKS | None = None
     if effective_auth_mode == "resource-server":
         if (
@@ -321,9 +336,7 @@ def create_http_app(
             or not resource_server_jwks_url
             or not resource_server_audience
         ):
-            raise ValueError(
-                "resource-server auth mode requires issuer, JWKS URL, and audience."
-            )
+            raise ValueError(AuthMessage.RESOURCE_SERVER_NEEDS_CONFIG)
         sanitize_public_auth_url(resource_server_jwks_url)
         resource_jwks = AsyncJWKS(resource_server_jwks_url)
     diagnostics = GatewayDiagnosticsInfo(
@@ -461,10 +474,10 @@ def create_http_app(
         return token
 
     def _reject(
-        status_code: int, body: str, headers: dict[str, str] | None = None
+        status_code: int, body: AuthText, headers: dict[str, str] | None = None
     ) -> Response:
         _inc(f"requests_{status_code}")
-        return Response(body, status_code=status_code, headers=headers or {})
+        return _auth_response(status_code, body, headers)
 
     async def handle_health(request: Request) -> Response:
         """Unauthenticated health check — safe for load-balancers and container probes."""
@@ -541,11 +554,11 @@ def create_http_app(
 
         if _origin_rejected(request):
             logger.debug("handle_mcp [%s]: 403 invalid origin", request_id)
-            return _reject(403, "Forbidden")
+            return _reject(403, AuthMessage.FORBIDDEN)
 
         if _host_rejected(request):
             logger.debug("handle_mcp [%s]: 403 invalid host", request_id)
-            return _reject(403, "Forbidden")
+            return _reject(403, AuthMessage.FORBIDDEN)
 
         if effective_auth_mode == "shared-secret":
             incoming = request.headers.get("authorization", "")
@@ -559,16 +572,16 @@ def create_http_app(
                 f"Bearer {auth_token}".encode(),
             ):
                 logger.debug("handle_mcp [%s]: 401 unauthorized", request_id)
-                return _reject(401, "Unauthorized", _auth_headers(request))
+                return _reject(401, AuthMessage.UNAUTHORIZED, _auth_headers(request))
         elif effective_auth_mode == "resource-server":
             token = _bearer_token(request)
             if token is None:
                 logger.debug("handle_mcp [%s]: 401 missing bearer", request_id)
-                return _reject(401, "Unauthorized", _auth_headers(request))
+                return _reject(401, AuthMessage.UNAUTHORIZED, _auth_headers(request))
             try:
                 if resource_jwks is None:
                     raise ResourceServerAuthError(
-                        "invalid_token", "Resource Server JWKS is not configured."
+                        "invalid_token", AuthMessage.RS_JWKS_NOT_CONFIGURED
                     )
                 jwks = await resource_jwks.get_for_token(token)
                 claims = validate_resource_server_token(
@@ -589,7 +602,7 @@ def create_http_app(
                 logger.debug("handle_mcp [%s]: 503 jwks unavailable", request_id)
                 return _reject(
                     503,
-                    "Service Unavailable",
+                    AuthMessage.SERVICE_UNAVAILABLE,
                     _auth_headers(request, error=exc.error),
                 )
             except ResourceServerAuthError as exc:
@@ -598,13 +611,13 @@ def create_http_app(
                     logger.debug("handle_mcp [%s]: 403 insufficient scope", request_id)
                     return _reject(
                         403,
-                        "Forbidden",
+                        AuthMessage.FORBIDDEN,
                         _auth_headers(request, error=exc.error, scope=scope),
                     )
                 logger.debug("handle_mcp [%s]: 401 invalid token", request_id)
                 return _reject(
                     401,
-                    "Unauthorized",
+                    AuthMessage.UNAUTHORIZED,
                     _auth_headers(request, error=exc.error),
                 )
 
@@ -769,6 +782,12 @@ def create_http_app(
     if auth_metadata.protected_resource_metadata_url:
         metadata_path = urlparse(auth_metadata.protected_resource_metadata_url).path
         if metadata_path:
+            # RFC 9728 makes `resource` REQUIRED, so never serve the route with
+            # an empty one. Unreachable today -- a normalized metadata URL is
+            # always absolute -- so this fails closed at startup, not per
+            # request, if a later change makes it reachable (Consiliency/pmcp#326).
+            if not _canonical_resource():
+                raise ValueError(AuthMessage.METADATA_NEEDS_RESOURCE)
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

### Patch — `tests/test_auth.py`, `tests/test_http_transport.py`, `tests/test_scoped_advisor_audit.py`

````diff
diff --git a/tests/test_auth.py b/tests/test_auth.py
index d5dc937..e34a906 100644
--- a/tests/test_auth.py
+++ b/tests/test_auth.py
@@ -17,6 +17,7 @@ from cryptography.hazmat.primitives.asymmetric import rsa
 
 from pmcp.auth import (
     AsyncJWKS,
+    AuthMessage,
     ResourceServerAuthError,
     ResourceServerJWKSUnavailable,
     fetch_json_metadata,
@@ -423,7 +424,8 @@ async def test_async_jwks_fetch_failures_are_sanitized(
 
     async def fake_fetch() -> dict[str, object]:
         raise ResourceServerJWKSUnavailable(
-            "JWKS fetch failed for https://issuer.example/jwks.json?token=secret."
+            AuthMessage.JWKS_FETCH_FAILED,
+            url="https://issuer.example/jwks.json?token=secret",
         )
 
     monkeypatch.setattr(jwks, "_fetch", fake_fetch)
@@ -1458,7 +1460,9 @@ async def test_s07_s08_recovery_after_failed_forced_refresh_waits_out_the_cooldo
         fetches += 1
         if endpoint_down:
             clock.advance(failure_duration)
-            raise ResourceServerJWKSUnavailable("JWKS fetch failed.")
+            raise ResourceServerJWKSUnavailable(
+                AuthMessage.JWKS_FETCH_FAILED, url="https://issuer.example/jwks.json"
+            )
         return rotated
 
     monkeypatch.setattr(jwks, "_fetch", fake_fetch)
@@ -1510,7 +1514,9 @@ async def test_s07_s08_backoff_rejection_does_not_consume_the_cooldown(
         nonlocal fetches
         fetches += 1
         if endpoint_down:
-            raise ResourceServerJWKSUnavailable("JWKS fetch failed.")
+            raise ResourceServerJWKSUnavailable(
+                AuthMessage.JWKS_FETCH_FAILED, url="https://issuer.example/jwks.json"
+            )
         return rotated
 
     monkeypatch.setattr(jwks, "_fetch", fake_fetch)
@@ -1577,7 +1583,9 @@ async def test_s08_concurrent_get_bounds_the_last_waiter(
         fetches += 1
         if fetches == 1:
             await release_first_fetch.wait()
-        raise ResourceServerJWKSUnavailable("JWKS fetch failed.")
+        raise ResourceServerJWKSUnavailable(
+            AuthMessage.JWKS_FETCH_FAILED, url="https://issuer.example/jwks.json"
+        )
 
     monkeypatch.setattr(jwks, "_fetch", fake_fetch)
 
diff --git a/tests/test_http_transport.py b/tests/test_http_transport.py
index e83214a..b5ccf90 100644
--- a/tests/test_http_transport.py
+++ b/tests/test_http_transport.py
@@ -388,7 +388,7 @@ class TestHttpObservabilityContracts:
         )
 
     def test_resource_server_insufficient_scope_gets_403_challenge(self) -> None:
-        from pmcp.auth import ResourceServerAuthError
+        from pmcp.auth import AuthMessage, ResourceServerAuthError
 
         with (
             patch(
@@ -398,7 +398,7 @@ class TestHttpObservabilityContracts:
             patch(
                 "pmcp.transport.http.validate_resource_server_token",
                 side_effect=ResourceServerAuthError(
-                    "insufficient_scope", "Missing required scope(s): write"
+                    "insufficient_scope", AuthMessage.MISSING_SCOPES, scopes="write"
                 ),
             ),
         ):
@@ -441,12 +441,13 @@ class TestHttpObservabilityContracts:
             )
 
     def test_resource_server_jwks_failure_gets_503_challenge(self) -> None:
-        from pmcp.auth import ResourceServerJWKSUnavailable
+        from pmcp.auth import AuthMessage, ResourceServerJWKSUnavailable
 
         with patch(
             "pmcp.transport.http.AsyncJWKS.get_for_token",
             side_effect=ResourceServerJWKSUnavailable(
-                "JWKS fetch failed for https://issuer.example/jwks.json?token=secret."
+                AuthMessage.JWKS_FETCH_FAILED,
+                url="https://issuer.example/jwks.json?token=secret",
             ),
         ):
             client = _make_contract_client(
diff --git a/tests/test_scoped_advisor_audit.py b/tests/test_scoped_advisor_audit.py
index 138e06a..edf4db0 100644
--- a/tests/test_scoped_advisor_audit.py
+++ b/tests/test_scoped_advisor_audit.py
@@ -22,6 +22,7 @@ from mcp.server.session import ServerSession
 from mcp.types import CallToolRequestParams, PaginatedRequestParams
 from pydantic import ValidationError
 
+from pmcp.auth import AuthMessage
 from pmcp.policy.policy import PolicyManager
 from pmcp.identity import acquire_singleton_lock, release_singleton_lock
 from pmcp.scoped_advisor_audit import (
@@ -1501,7 +1502,9 @@ def _open_containers(tool: Any, baseline: dict[str, Any]) -> list[str]:
 #: more than a message. A new such class fails `_raised_exceptions` loudly.
 _EXCEPTION_ARGS: dict[str, tuple[Any, ...]] = {
     "HTTPError": ("https://example.invalid/", 500, "stub handler failed", None, None),
-    "ResourceServerAuthError": ("invalid_token", "stub handler failed"),
+    # The auth errors take only `AuthMessage` members (Consiliency/pmcp#326).
+    "ResourceServerAuthError": ("invalid_token", AuthMessage.INVALID_TOKEN),
+    "ResourceServerJWKSUnavailable": (AuthMessage.JWKS_NO_USABLE_KEYS,),
     "MissingRemoteHeaderAuthError": ("stub", ["STUB_VAR"]),
     "MissingApiKeyError": ("STUB_VAR", "stub", "stub"),
 }
````

### File — `tests/test_auth_operator_messages.py`

````python
"""Consiliency/pmcp#326: every fixed operator-facing auth message survives
pmcp's own diagnostic sanitiser unchanged.

`ResourceServerAuthError.__init__` runs its description through
`sanitize_auth_diagnostic`, so a fixed text the sanitiser reads as a
credential was stored mangled: "Token could not be verified with the
published key." became "Token [REDACTED] not be verified with the published
key.".

Round 4: the texts live in ONE registry, `pmcp.auth.AuthMessage`, and the
code cannot use anything else -- the class fix after three rounds in which a
static collector was beaten by a new code shape. So:

1. every registry member, rendered with sample configuration fields,
   survives the redactor's own rules (`_sanitize_base`), the additive rules
   (`redact_additive`, #234) and the composed `sanitize_auth_diagnostic`;
2. the auth error classes and `_auth_response` raise `TypeError` for a
   message that is not a member (or the narrow `pyjwt_text` pass-through),
   whatever shape produced it -- a literal, a variable, a subclass constant,
   a walrus, an alias;
3. a static check -- syntax only, no resolver -- requires every raise and
   `_reject` site in `auth.py` and `transport/http.py` to name
   `AuthMessage.<NAME>` directly, which covers sites no test reaches;
4. logs are checked end to end through the real `setup_logging`.

A whole `WWW-Authenticate` header is the one deliberate exception, pinned
below: the base `Bearer` rule redacts the challenge's first parameter (it
cannot tell `Bearer resource="..."` from `Bearer <token>`), the base rules
are frozen (additive-only, #234), and pmcp never runs its own challenge
through the sanitiser. Its parameters must survive one by one, and pmcp's own
challenge parser must read them back intact.
"""

from __future__ import annotations

import ast
import asyncio
import functools
import string
from pathlib import Path
import time
from typing import Any, Callable
from unittest.mock import AsyncMock, MagicMock, patch

import jwt
import pytest
from starlette.testclient import TestClient

from pmcp import auth as auth_mod
from pmcp.auth import (
    AsyncJWKS,
    AuthMessage,
    AuthText,
    PyJwtText,
    ResourceServerAuthError,
    ResourceServerJWKSUnavailable,
    _sanitize_base,
    auth_messages,
    parse_www_authenticate,
    pyjwt_text,
    render_auth_message,
    sanitize_auth_diagnostic,
)
from pmcp.redaction_additive import redact_additive
from pmcp.transport import http as http_mod
from pmcp.transport.http import _auth_response, create_http_app

_SAMPLE_FIELDS = {
    "url": "https://issuer.example/.well-known/jwks.json",
    "scopes": "mcp:read mcp:write",
}
_MESSAGES = auth_messages()
_MODULES = {"auth.py": auth_mod, "transport/http.py": http_mod}


def _layers(text: str) -> dict[str, str]:
    return {
        "base": _sanitize_base(text),
        "additive": redact_additive(text),
        "composed": sanitize_auth_diagnostic(text, max_length=None),
    }


def _placeholders(member: str) -> set[str]:
    return {f for _, f, _, _ in string.Formatter().parse(member) if f}


def _fields(member: AuthText) -> dict[str, str]:
    """Sample values for exactly the member's placeholders."""
    names = {f for _, f, _, _ in string.Formatter().parse(member) if f}
    return {k: v for k, v in _SAMPLE_FIELDS.items() if k in names}


def _rendered(member: AuthText) -> str:
    return render_auth_message(member, **_fields(member))


# --- 1. the registry: every member survives the sanitiser -----------------------


def test_the_registry_is_complete_and_typed() -> None:
    """The registry is the list now (no derivation to shrink): 29 members on
    the round-4 spike, each an `AuthText`, no two with the same text."""
    assert len(_MESSAGES) == 29, sorted(_MESSAGES)
    assert len(set(_MESSAGES.values())) == len(_MESSAGES)
    assert all(type(v) is AuthText for v in _MESSAGES.values())
    assert _MESSAGES["KEY_CANNOT_VERIFY_TOKEN"] == (
        "The published key cannot verify this token."
    )


@pytest.mark.parametrize("name", sorted(_MESSAGES))
def test_every_registry_message_survives_the_sanitiser(name: str) -> None:
    text = _rendered(_MESSAGES[name])
    mangled = {k: v for k, v in _layers(text).items() if v != text}
    assert mangled == {}, f"AuthMessage.{name}: {text!r} is rewritten: {mangled}"


@pytest.mark.parametrize("name", sorted(_MESSAGES))
def test_a_stored_description_is_the_text_written(name: str) -> None:
    """End to end through the class that stores it (and sanitises it)."""
    text = _rendered(_MESSAGES[name])
    exc = ResourceServerAuthError(
        "invalid_token", _MESSAGES[name], **_fields(_MESSAGES[name])
    )
    assert exc.description == text and str(exc) == text


# --- 2. the constructors refuse anything else -----------------------------------


class _ClassConstant(ResourceServerAuthError):
    message = "Token could not be verified."  # claude round 3 B1 / codex 1

    def __init__(self) -> None:
        super().__init__("invalid_token", self.message)  # type: ignore[arg-type]


class _InitAttribute(ResourceServerAuthError):
    def __init__(self) -> None:
        self.message = "Token expired."
        super().__init__("invalid_token", self.message)  # type: ignore[arg-type]


class _AugmentedParameter(ResourceServerAuthError):
    def __init__(self, message: str = "Empty token.") -> None:
        message += " Token expired."
        super().__init__("invalid_token", message)  # type: ignore[arg-type]


def _walrus_message() -> ResourceServerAuthError:
    message = "Empty token."
    [(message := "Token expired.") for _ in range(1)]  # codex round 3 (2)
    return ResourceServerAuthError("invalid_token", message)  # type: ignore[arg-type]


_INNER = ResourceServerAuthError
_OUTER = _INNER  # codex round 3 (3): an alias raised from a shadowing scope


def _aliased_message() -> ResourceServerAuthError:
    _INNER = ValueError  # noqa: F841 - the shadow the static resolver got wrong
    return _OUTER("invalid_token", "Token expired.")  # type: ignore[arg-type]


_REFUSED: dict[str, Callable[[], Any]] = {
    # rounds 1-3 shapes
    "literal": lambda: ResourceServerAuthError("invalid_token", "Token expired."),
    "keyword": lambda: ResourceServerAuthError(
        "invalid_token", description="Token audience mismatch."
    ),
    "percent": lambda: ResourceServerAuthError(
        "invalid_token", "JWKS URL is required for token %s." % "checks"
    ),
    "format": lambda: ResourceServerAuthError(
        "invalid_token", "Token {} rejected.".format("checks")
    ),
    "f_string": lambda: ResourceServerAuthError("invalid_token", f"Token {1}"),
    "jwks_unavailable_str": lambda: ResourceServerJWKSUnavailable("Token expired."),
    "plain_str_subclass_of_str": lambda: ResourceServerAuthError(
        "invalid_token", str(AuthMessage.EMPTY_TOKEN)
    ),
    # round 3 shapes
    "class_constant": _ClassConstant,
    "init_attribute": _InitAttribute,
    "augmented_parameter": _AugmentedParameter,
    "comprehension_walrus": _walrus_message,
    "shadowed_alias": _aliased_message,
}


@pytest.mark.parametrize("shape", sorted(_REFUSED))
def test_a_message_outside_the_registry_is_refused_at_construction(shape: str) -> None:
    with pytest.raises(TypeError, match="AuthMessage member"):
        _REFUSED[shape]()  # type: ignore[no-untyped-call]


def test_an_auth_response_body_outside_the_registry_is_refused() -> None:
    with pytest.raises(TypeError, match="AuthMessage member"):
        _auth_response(401, "Unauthorized")  # type: ignore[arg-type]
    assert _auth_response(401, AuthMessage.UNAUTHORIZED).body == b"Unauthorized"


def test_the_pyjwt_pass_through_is_narrow() -> None:
    with pytest.raises(TypeError, match="fixed-text pyjwt error"):
        pyjwt_text(jwt.InvalidTokenError("Token payload: secret"))
    with pytest.raises(TypeError):
        pyjwt_text(ValueError("Token expired."))
    kept = jwt.ExpiredSignatureError("Signature has expired")
    exc = ResourceServerAuthError("invalid_token", pyjwt_text(kept))
    assert exc.description == "Signature has expired"


# --- round 5: membership is identity; fields are exactly the placeholders ------


_NOT_MEMBERS: dict[str, Callable[[], Any]] = {
    # codex round 4 (1) / claude N2: the right type, not a member
    "minted_auth_text": lambda: ResourceServerAuthError(
        "invalid_token", AuthText("Token expired.")
    ),
    "str_new_auth_text": lambda: ResourceServerAuthError(
        "invalid_token", str.__new__(AuthText, "Token expired.")
    ),
    "copy_of_a_member": lambda: ResourceServerAuthError(
        "invalid_token", AuthText(str(AuthMessage.EMPTY_TOKEN))
    ),
    "str_new_pyjwt_text": lambda: ResourceServerAuthError(
        "invalid_token", str.__new__(PyJwtText, "Token expired.")
    ),
}


@pytest.mark.parametrize("shape", sorted(_NOT_MEMBERS))
def test_a_value_of_the_right_type_that_is_not_a_member_is_refused(shape: str) -> None:
    with pytest.raises(TypeError):
        _NOT_MEMBERS[shape]()


def test_pyjwt_text_cannot_be_constructed_directly() -> None:
    with pytest.raises(TypeError, match="minted only by pyjwt_text"):
        PyJwtText("Token expired.")


_BAD_FIELDS: dict[str, Callable[[], Any]] = {
    # claude round 4 B1
    "missing": lambda: ResourceServerJWKSUnavailable(AuthMessage.JWKS_FETCH_FAILED),
    "misnamed": lambda: ResourceServerJWKSUnavailable(
        AuthMessage.JWKS_FETCH_FAILED, uri="https://issuer.example/jwks"
    ),
    "extra": lambda: ResourceServerAuthError(
        "invalid_token", AuthMessage.EMPTY_TOKEN, scopes="admin"
    ),
    "field_on_pyjwt": lambda: ResourceServerAuthError(
        "invalid_token",
        pyjwt_text(jwt.ExpiredSignatureError("Signature has expired")),
        url="x",
    ),
}


@pytest.mark.parametrize("shape", sorted(_BAD_FIELDS))
def test_fields_must_be_exactly_the_placeholders(shape: str) -> None:
    with pytest.raises(TypeError, match="fields|no fields"):
        _BAD_FIELDS[shape]()


async def test_a_programming_error_in_the_fetch_is_not_a_503() -> None:
    """A `TypeError` (e.g. a refused message) inside the JWKS fetch used to be
    re-wrapped as `JWKS_FETCH_FAILED` and open the backoff for every waiter.
    It now propagates, and no backoff opens."""
    jwks = AsyncJWKS("https://issuer.example/jwks.json")

    async def broken_fetch() -> dict[str, object]:
        raise TypeError("auth messages must be an AuthMessage member, not str")

    jwks._fetch = broken_fetch  # type: ignore[method-assign]
    with pytest.raises(TypeError):
        await jwks.get()
    assert jwks._last_refresh_failure == float("-inf")


async def test_a_programming_error_inside_fetch_itself_is_not_rewrapped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    jwks = AsyncJWKS("https://issuer.example/jwks.json")

    def broken_session(*_a: Any, **_k: Any) -> Any:
        raise TypeError("programming error")

    monkeypatch.setattr(auth_mod.aiohttp, "ClientSession", broken_session)
    with pytest.raises(TypeError, match="programming error"):
        await jwks._fetch()


# --- round 6: one guard; field values must have their placeholder's shape --------

_PRETEND_URL = "Token could not be verified."  # claude round 5 N2


def _reason_bound_to_prose() -> Any:
    reason = "Token expired."  # codex round 5 (2)
    return ResourceServerJWKSUnavailable(AuthMessage.JWKS_FETCH_FAILED, url=reason)


_BAD_VALUES: dict[str, Callable[[], Any]] = {
    "url_variable_bound_to_prose": _reason_bound_to_prose,
    "url_constant_bound_to_prose": lambda: ResourceServerJWKSUnavailable(
        AuthMessage.JWKS_FETCH_FAILED, url=_PRETEND_URL
    ),
    "redirect_site_prose": lambda: ResourceServerJWKSUnavailable(
        AuthMessage.JWKS_REDIRECT_REFUSED, url="Token expired."
    ),
    "url_relative": lambda: ResourceServerJWKSUnavailable(
        AuthMessage.JWKS_FETCH_FAILED, url="/jwks.json"
    ),
    "url_with_space": lambda: ResourceServerJWKSUnavailable(
        AuthMessage.JWKS_FETCH_FAILED, url="https://issuer.example/ Token x"
    ),
    "url_not_str": lambda: ResourceServerJWKSUnavailable(
        AuthMessage.JWKS_FETCH_FAILED,
        url=AuthMessage.EMPTY_TOKEN,  # type: ignore[arg-type]
    ),
    "scopes_quoted_prose": lambda: ResourceServerAuthError(
        "insufficient_scope", AuthMessage.MISSING_SCOPES, scopes='Token "expired"'
    ),
    "scopes_empty": lambda: ResourceServerAuthError(
        "insufficient_scope", AuthMessage.MISSING_SCOPES, scopes=""
    ),
    "scopes_double_space": lambda: ResourceServerAuthError(
        "insufficient_scope", AuthMessage.MISSING_SCOPES, scopes="read  write"
    ),
}


@pytest.mark.parametrize("shape", sorted(_BAD_VALUES))
def test_a_field_value_without_its_placeholder_shape_is_refused(shape: str) -> None:
    with pytest.raises(TypeError, match="expected shape"):
        _BAD_VALUES[shape]()


def test_real_field_values_are_accepted() -> None:
    for url in (
        "https://issuer.example/.well-known/jwks.json",
        "http://127.0.0.1:8080/jwks",
        "https://[2606:4700::1]:8443/jwks?token=%5BREDACTED%5D",
    ):
        ResourceServerJWKSUnavailable(AuthMessage.JWKS_FETCH_FAILED, url=url)
    for scopes in ("mcp:read", "mcp:read mcp:write", "secret:read admin"):
        ResourceServerAuthError(
            "insufficient_scope", AuthMessage.MISSING_SCOPES, scopes=scopes
        )


def test_every_registry_placeholder_has_a_validator_and_no_odd_braces() -> None:
    """Claude round 5 N4: only named identifier fields -- no `{}`, no escaped
    `{{`/`}}`, no index, attribute, conversion or format spec -- and every
    field name has a shape validator (an unknown one fails at import)."""
    for name, member in _MESSAGES.items():
        for literal, field, spec, conv in string.Formatter().parse(member):
            assert "{" not in literal and "}" not in literal, name
            if field is None:
                continue
            assert field.isidentifier() and not spec and conv is None, (name, field)
            assert field in auth_mod._FIELD_VALIDATORS, (name, field)


def test_an_auth_response_body_of_the_right_type_that_is_not_a_member_is_refused() -> (
    None
):
    """Codex round 5 (1): `_auth_response` checked type, not membership."""
    for body in (
        AuthText("Token expired."),
        str.__new__(AuthText, "Token expired."),
        AuthText(str(AuthMessage.UNAUTHORIZED)),
    ):
        with pytest.raises(TypeError):
            _auth_response(401, body)


def test_isinstance_auth_text_is_only_used_to_enumerate_the_registry() -> None:
    """One guard: membership is decided by `render_auth_message` (identity).
    `isinstance(..., AuthText)` anywhere else would be a second, weaker
    guard -- the round-5 hole in `_auth_response`."""
    src = Path(auth_mod.__file__ or "").parent
    found = []
    for path in sorted(src.rglob("*.py")):
        tree = ast.parse(path.read_text())
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for call in ast.walk(fn):
                if (
                    isinstance(call, ast.Call)
                    and getattr(call.func, "id", "") == "isinstance"
                    and len(call.args) == 2
                    and "AuthText" in ast.unparse(call.args[1])
                    and fn.name != "auth_messages"
                ):
                    found.append(f"{path.relative_to(src)}:{call.lineno} in {fn.name}")
    assert found == [], found


async def test_the_jwks_redirect_path_raises_its_registry_message() -> None:
    """Claude round 5 N1: nothing drove a JWKS redirect, so a misnamed field
    there would have passed every test and been a 500 in production."""

    async def handle(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        await reader.readuntil(b"\r\n\r\n")
        writer.write(
            b"HTTP/1.1 302 Found\r\nLocation: http://127.0.0.1:1/x\r\n"
            b"Content-Length: 0\r\nConnection: close\r\n\r\n"
        )
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        jwks = AsyncJWKS("https://issuer.example/jwks.json")
        jwks._raw_url = f"http://127.0.0.1:{port}/jwks"
        with pytest.raises(ResourceServerJWKSUnavailable) as caught:
            await asyncio.wait_for(jwks._fetch(), 10)
    finally:
        server.close()
        await server.wait_closed()
    assert caught.value.description == (
        "JWKS endpoint returned a redirect for https://issuer.example/jwks.json; "
        "refusing to follow."
    )


# --- 3. every site names a registry member: a static check, no resolver ----------

# Callee -> (index, keyword) of its message argument. A `raise` of any other
# callee in these two modules fails the check.
_SITES: dict[str, tuple[int, str | None]] = {
    "ResourceServerAuthError": (1, "description"),
    "ResourceServerJWKSUnavailable": (0, "description"),
    "ValueError": (0, None),
    "HTTPError": (2, "msg"),
    "_reject": (1, "body"),
    "_auth_response": (1, "body"),
}
# The only exception classes these modules may define.
_EXCEPTION_CLASSES = {"ResourceServerAuthError", "ResourceServerJWKSUnavailable"}
_ERROR_CODES = {"invalid_token", "insufficient_scope", "temporarily_unavailable"}
# The registry's guards, the only functions that may raise `TypeError`.
_GUARDS = {"render_auth_message", "pyjwt_text", "_check_registry_fields"}


def _is_member(node: ast.expr | None) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "AuthMessage"
        and node.attr in _MESSAGES
    )


def _is_pyjwt_pass_through(node: ast.expr | None) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "pyjwt_text"
        and len(node.args) == 1
        and isinstance(node.args[0], ast.Name)
    )


def _parents(tree: ast.Module) -> dict[int, ast.AST]:
    return {id(c): n for n in ast.walk(tree) for c in ast.iter_child_nodes(n)}


def _enclosing_handlers(
    node: ast.AST, parents: dict[int, ast.AST]
) -> list[ast.ExceptHandler]:
    out: list[ast.ExceptHandler] = []
    cur = parents.get(id(node))
    while cur is not None:
        if isinstance(cur, ast.ExceptHandler):
            out.append(cur)
        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            break  # a handler outside the function does not bind inside it
        cur = parents.get(id(cur))
    return out


def _rebinds(handler: ast.ExceptHandler, name: str) -> bool:
    """Whether anything inside the handler body rebinds ``name``."""
    return any(
        isinstance(n, ast.Name)
        and n.id == name
        and isinstance(n.ctx, (ast.Store, ast.Del))
        for stmt in handler.body
        for n in ast.walk(stmt)
    )


def _bound_by_handler(
    node: ast.AST,
    name: str,
    parents: dict[int, ast.AST],
    type_source: str | None = None,
) -> bool:
    """``name`` is the `except ... as name` of a handler that lexically
    encloses ``node`` (optionally of the given exception type), and nothing in
    that handler rebinds it."""
    for handler in _enclosing_handlers(node, parents):
        if handler.name != name:
            continue
        if type_source is not None and (
            handler.type is None or ast.unparse(handler.type) != type_source
        ):
            return False
        return not _rebinds(handler, name)
    return False


def _site_violations(rel: str, tree: ast.Module) -> list[str]:
    """Syntax only: every raise/`_reject` site passes `AuthMessage.<NAME>`
    directly, its keyword fields are exactly the member's placeholders and
    each is a runtime name, a named raise re-raises its own enclosing
    handler's exception, and `pyjwt_text` appears only in the allowlisted
    handler. No resolver, so no scope rule to get wrong.

    Known limit (claude round 5, N2): a field value that is a *name bound to
    prose* (`url=_PRETEND_URL`) is syntactically a runtime name. That is
    caught at run time instead: every field value must have its
    placeholder's shape (an absolute http(s) URL, an RFC 6749 scope list)."""
    out: list[str] = []
    parents = _parents(tree)
    guard_raises = {
        id(n)
        for fn in ast.walk(tree)
        if (isinstance(fn, ast.FunctionDef) and fn.name in _GUARDS)
        or (isinstance(fn, ast.ClassDef) and fn.name == "PyJwtText")
        for n in ast.walk(fn)
        if isinstance(n, ast.Raise)
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            bases = {ast.unparse(b) for b in node.bases}
            if node.name not in _EXCEPTION_CLASSES and (
                bases & _EXCEPTION_CLASSES
                or any(b.endswith(("Error", "Exception")) for b in bases)
            ):
                out.append(
                    f"{rel}:{node.lineno} class {node.name}: new exception class"
                )
    for node in ast.walk(tree):
        if isinstance(node, ast.Raise) and node.exc is not None:
            exc = node.exc
            if isinstance(exc, ast.Name):
                if not _bound_by_handler(node, exc.id, parents):
                    out.append(f"{rel}:{node.lineno} raise {exc.id}: not a re-raise")
                continue
            if not (isinstance(exc, ast.Call) and isinstance(exc.func, ast.Name)):
                out.append(f"{rel}:{node.lineno} raise {ast.unparse(exc)[:40]}")
                continue
            if exc.func.id == "TypeError" and id(node) in guard_raises:
                continue  # the registry's own refusal, a programmer error
            if exc.func.id not in _SITES:
                out.append(f"{rel}:{node.lineno} raise {exc.func.id}: unknown callee")
        if isinstance(node, ast.Call):
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name == "pyjwt_text" and not (
                len(node.args) == 1
                and isinstance(node.args[0], ast.Name)
                and _bound_by_handler(
                    node, node.args[0].id, parents, "_FIXED_TEXT_CLAIM_ERRORS"
                )
            ):
                out.append(
                    f"{rel}:{node.lineno} pyjwt_text(...) outside "
                    "`except _FIXED_TEXT_CLAIM_ERRORS as <name>`"
                )
            if name not in _SITES:
                continue
            index, keyword = _SITES[name]
            arg = next((k.value for k in node.keywords if k.arg == keyword), None)
            if arg is None and len(node.args) > index:
                arg = node.args[index]
            ok = _is_member(arg) or (
                name == "ResourceServerAuthError" and _is_pyjwt_pass_through(arg)
            )
            if name in ("_reject", "_auth_response") and isinstance(arg, ast.Name):
                ok = ok or arg.id == "body"  # the helper's own parameter
            if not ok:
                out.append(
                    f"{rel}:{node.lineno} {name}(...) message is "
                    f"{ast.unparse(arg) if arg is not None else '<missing>'}"
                )
            if name in (
                "ResourceServerAuthError",
                "ResourceServerJWKSUnavailable",
            ) and (_is_member(arg)):
                assert isinstance(arg, ast.Attribute)
                given = {k.arg for k in node.keywords if k.arg not in (keyword, None)}
                wanted = _placeholders(_MESSAGES[arg.attr])
                if given != wanted:
                    out.append(
                        f"{rel}:{node.lineno} AuthMessage.{arg.attr} fields "
                        f"{sorted(given)} != placeholders {sorted(wanted)}"
                    )
            if name in ("ResourceServerAuthError", "ResourceServerJWKSUnavailable"):
                for kw in node.keywords:
                    if kw.arg in (keyword, None):
                        continue
                    if not isinstance(kw.value, (ast.Name, ast.Attribute)):
                        out.append(
                            f"{rel}:{node.lineno} field {kw.arg}= is "
                            f"{ast.unparse(kw.value)[:40]}, not a runtime name"
                        )
            if name == "ResourceServerAuthError" and node.args:
                code = node.args[0]
                if not (isinstance(code, ast.Constant) and code.value in _ERROR_CODES):
                    if not (isinstance(code, ast.Name) and code.id == "error"):
                        out.append(
                            f"{rel}:{node.lineno} error code {ast.unparse(code)}"
                        )
    return sorted(set(out))


def _module_trees() -> list[tuple[str, ast.Module]]:
    return [
        (rel, ast.parse(Path(module.__file__ or "").read_text()))
        for rel, module in _MODULES.items()
    ]


def test_every_message_site_names_a_registry_member() -> None:
    violations = [
        v for rel, tree in _module_trees() for v in _site_violations(rel, tree)
    ]
    assert violations == [], violations


def test_the_site_check_sees_every_site() -> None:
    """Guards the check itself: it visits every raise and `_reject` in the
    two modules (counted on the round-6 spike: 35 raises -- named re-raises
    included -- 5 of them the guards' own `TypeError`s, and 7 `_reject`
    calls)."""
    counts = {"raise": 0, "_reject": 0}
    for _rel, tree in _module_trees():
        for node in ast.walk(tree):
            if isinstance(node, ast.Raise) and node.exc is not None:
                counts["raise"] += 1
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "_reject":
                counts["_reject"] += 1
    assert counts == {"raise": 35, "_reject": 7}, counts


_STATIC_SHAPES = {
    # every shape the panel found in rounds 1-3, as source the check reads
    "literal": 'def f():\n    raise ResourceServerAuthError("invalid_token", "Token x.")\n',
    "keyword": "def f():\n    raise ResourceServerAuthError("
    '"invalid_token", description="Token x.")\n',
    "variable": 'def f():\n    message = AuthMessage.EMPTY_TOKEN + " Token x."\n'
    '    raise ResourceServerAuthError("invalid_token", message)\n',
    "percent": "def f():\n    raise ValueError(\"Token %s.\" % 'x')\n",
    "runtime_error": 'def f():\n    raise RuntimeError("Unsupported secret mode.")\n',
    "raise_name": 'def f():\n    error = LookupError("Token expired.")\n    raise error\n',
    "alias": "_OUTER = ResourceServerAuthError\n"
    'def f():\n    raise _OUTER("invalid_token", AuthMessage.EMPTY_TOKEN)\n',
    "subclass": "class _TokenRejected(ResourceServerAuthError):\n"
    "    message = 'Token expired.'\n",
    "walrus": "def f():\n    raise ResourceServerAuthError("
    "'invalid_token', [(m := 'Token x.') for _ in range(1)][0])\n",
    "reject_literal": 'def f():\n    return _reject(401, "Unauthorized")\n',
    "attribute_not_member": "def f(self):\n    raise ResourceServerAuthError("
    "'invalid_token', self.message)\n",
    "unknown_member": "def f():\n    raise ValueError(AuthMessage.NOT_A_MEMBER)\n",
    # round 4 of the panel
    "field_literal": "def f():\n    raise ResourceServerJWKSUnavailable("
    "AuthMessage.JWKS_FETCH_FAILED, url='Token expired.')\n",
    "field_f_string": "def f(x):\n    raise ResourceServerAuthError("
    "'insufficient_scope', AuthMessage.MISSING_SCOPES, scopes=f'Token {x}')\n",
    "named_raise_outside_its_handler": "def f():\n    try:\n        g()\n"
    "    except ValueError as exc:\n        pass\n"
    "    exc = RuntimeError('Token expired.')\n    raise exc\n",
    "named_raise_other_function": "def f():\n    try:\n        g()\n"
    "    except ValueError as exc:\n        raise exc\n"
    "def h():\n    exc = RuntimeError('Token expired.')\n    raise exc\n",
    "named_raise_rebound_in_handler": "def f():\n    try:\n        g()\n"
    "    except ValueError as exc:\n        exc = RuntimeError('Token x.')\n"
    "        raise exc\n",
    "pyjwt_text_at_invalid_token": "def f():\n    try:\n        g()\n"
    "    except jwt.InvalidTokenError as exc:\n"
    "        raise ResourceServerAuthError('invalid_token', pyjwt_text(exc))\n",
    "misnamed_field": "def f(self):\n    raise ResourceServerJWKSUnavailable("
    "AuthMessage.JWKS_REDIRECT_REFUSED, uri=self.url)\n",
    "missing_field": "def f():\n    raise ResourceServerJWKSUnavailable("
    "AuthMessage.JWKS_REDIRECT_REFUSED)\n",
    "pyjwt_text_outside_a_handler": "def f(exc):\n"
    "    raise ResourceServerAuthError('invalid_token', pyjwt_text(exc))\n",
}


@pytest.mark.parametrize("shape", sorted(_STATIC_SHAPES))
def test_the_site_check_refuses_every_shape(shape: str) -> None:
    assert _site_violations("x.py", ast.parse(_STATIC_SHAPES[shape])) != []


def test_auth_text_is_built_only_in_the_registry() -> None:
    """`AuthText(...)` outside `AuthMessage` would mint a member-typed value
    the static check cannot see."""
    misplaced = []
    for rel, tree in _module_trees():
        allowed: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == "AuthMessage":
                allowed |= {id(n) for n in ast.walk(node)}
            if isinstance(node, ast.FunctionDef) and node.name == "pyjwt_text":
                allowed |= {id(n) for n in ast.walk(node)}
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and getattr(node.func, "id", "") in ("AuthText", "PyJwtText")
                and id(node) not in allowed
            ):
                misplaced.append(f"{rel}:{node.lineno}")
    assert misplaced == [], misplaced


# --- 4. logs: no sink applies the sanitiser, end to end ---------------------------


_NON_CONSTANT_LOGS: list[str] = []


def _log_templates() -> list[str]:
    """Every `logger.<level>("constant", ...)` template in the two modules. A
    non-constant template is recorded and fails
    `test_the_log_templates_are_collected` (the only static rule logs
    need)."""
    out: list[str] = []
    for rel, tree in _module_trees():
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in ("logger", "log", "logging")
                and node.func.attr
                in (
                    "debug",
                    "info",
                    "warning",
                    "warn",
                    "error",
                    "exception",
                    "critical",
                )
            ):
                arg = node.args[0] if node.args else None
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    out.append(arg.value)
                else:
                    _NON_CONSTANT_LOGS.append(f"{rel}:{node.lineno}")
    return out


_LOG_TEMPLATES = _log_templates()


def test_the_log_templates_are_collected() -> None:
    assert _NON_CONSTANT_LOGS == [], f"non-constant log templates: {_NON_CONSTANT_LOGS}"
    assert len(_LOG_TEMPLATES) == 16, _LOG_TEMPLATES
    assert "Streamable-HTTP session manager started" in _LOG_TEMPLATES


@pytest.mark.parametrize("log_format", ["text", "json"])
def test_no_log_sink_applies_the_sanitiser(
    log_format: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End to end (round 4, claude N3): run the real `setup_logging`, log
    every template through the real `pmcp.transport.http` logger, and read
    what actually reached stderr and the log file. Any redaction -- a filter,
    a record factory, a formatter, or a handler's own `emit()` -- shows up
    here. Today none applies, so the log templates are not reworded (two of
    them, `Streamable-HTTP session manager started/stopped`, would read
    `session [REDACTED]` through the sanitiser)."""
    import io
    import logging
    import sys

    from pmcp import cli

    stderr = io.StringIO()
    monkeypatch.setattr(sys, "stderr", stderr)
    monkeypatch.chdir(tmp_path)
    log_file = tmp_path / "logs" / "gateway.log"
    monkeypatch.setattr(cli, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(cli, "LOG_FILE", log_file)
    root = logging.getLogger()
    before, level = list(root.handlers), root.level
    logger = logging.getLogger("pmcp.transport.http")
    expected = []
    try:
        cli.setup_logging("DEBUG", log_to_file=True, log_format=log_format)
        added = [h for h in root.handlers if h not in before]
        assert len(added) == 2, added  # stderr + rotating file
        for template in _LOG_TEMPLATES:
            holes = template.count("%s") + template.count("%r")
            args = ("req-1",) * holes
            expected.append(template % args if holes else template)
            logger.warning(template, *args)
        for handler in added:
            handler.flush()
    finally:
        for handler in [h for h in root.handlers if h not in before]:
            root.removeHandler(handler)
            handler.close()
        root.setLevel(level)
    outputs = {"stderr": stderr.getvalue(), "file": log_file.read_text()}
    for sink, text in outputs.items():
        missing = [m for m in expected if m not in text]
        assert missing == [], f"{sink} rewrote: {missing}"


def test_no_redacting_log_hook_in_the_source() -> None:
    """Supplement: the hooks that would redact a record outside
    `setup_logging`."""
    src = Path(auth_mod.__file__ or "").parent
    hits = [
        f"{p.relative_to(src)}: {needle}"
        for p in sorted(src.rglob("*.py"))
        for needle in ("addFilter(", "logging.Filter", "setLogRecordFactory(")
        if needle in p.read_text()
    ]
    assert hits == []


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
            "resource_server_jwks_url": _SAMPLE_FIELDS["url"],
            "resource_server_audience": _AUDIENCE,
        }
        kwargs.update(overrides)
        app = create_http_app(MagicMock(), **kwargs)
    return TestClient(app, base_url="http://127.0.0.1", raise_server_exceptions=False)


def _challenges() -> list[tuple[str, int, str | None]]:
    body = {"jsonrpc": "2.0", "method": "notifications/initialized"}
    hs_token = jwt.encode({"iss": _ISSUER}, _KEY, algorithm="HS256")
    out: list[tuple[str, int, str | None]] = []
    for meta in (None, _META):
        extra = {"protected_resource_metadata_url": meta} if meta else {}
        label = "metadata" if meta else "audience"
        plain = _client(**extra)
        scoped = _client(required_scopes=["admin"], **extra)
        unavailable = AsyncMock(
            side_effect=ResourceServerJWKSUnavailable(
                AuthMessage.JWKS_FETCH_FAILED, url="https://issuer.example/jwks"
            )
        )
        missing_scope = AsyncMock(
            side_effect=ResourceServerAuthError(
                "insufficient_scope", AuthMessage.MISSING_SCOPES, scopes="admin"
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
                (f"{name}-{label}", r.status_code, r.headers.get("www-authenticate"))
            )
    return out


_CHALLENGE_CASES = [
    f"{name}-{label}"
    for label in ("audience", "metadata")
    for name in ("401-missing", "401-invalid", "403-scope", "503-jwks")
]
_STATUS = {"401": 401, "403": 403, "503": 503}


@functools.lru_cache(maxsize=1)
def _challenge_map() -> dict[str, tuple[int, str | None]]:
    """Built lazily, inside a test, so a wiring change (a 500 with no
    challenge) fails these tests instead of erroring the whole module at
    collection."""
    out: dict[str, tuple[int, str | None]] = {}
    for case, status, header in _challenges():
        out[case] = (status, header)
    return out


def _challenge(case: str) -> tuple[int, str]:
    status, header = _challenge_map()[case]
    assert status == _STATUS[case[:3]], f"{case}: got HTTP {status}"
    assert header, f"{case}: no WWW-Authenticate"
    return status, header


def test_the_challenges_cover_401_403_503() -> None:
    statuses = {_challenge(case)[0] for case in _CHALLENGE_CASES}
    assert sorted(statuses) == [401, 403, 503]


@pytest.mark.parametrize("case", _CHALLENGE_CASES)
def test_challenge_parameters_survive_the_sanitiser(case: str) -> None:
    _status, header = _challenge(case)
    scheme, _, params = header.partition(" ")
    assert scheme == "Bearer"
    for param in (p.strip() for p in params.split(",")):
        _key, _, value = param.partition("=")
        for text in (param, value.strip('"')):
            mangled = {k: v for k, v in _layers(text).items() if v != text}
            assert mangled == {}, f"{case}: {text!r} is rewritten: {mangled}"


@pytest.mark.parametrize("case", _CHALLENGE_CASES)
def test_pmcp_reads_its_own_challenge_back(case: str) -> None:
    status, header = _challenge(case)
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
