# Detailed plan: state the accepted auth-URL rule, and a plain-http refusal that is true for every URL it refuses

> Written on main `31c1357` (dev0, a team host), worktree `pmcp-341`, branch
> `plan/341-auth-url-docs`, CPython 3.10.21. Every result below was measured
> on that tree, or on the spike of this plan applied to it. The spike lived
> on a local branch and was never pushed; this PR carries only this file. The
> spike's patches and its new test module are reproduced verbatim at the end
> (*Verbatim bodies*), and the *Embedding proof* section shows them extracted
> from this file, applied to a fresh `31c1357`, and passing.

## Task

Consiliency/pmcp#341 collects four non-blocking findings from the review
panel on Consiliency/pmcp#340, the implementation of Consiliency/pmcp#326:

1. **Refused-list wording.** README and CHANGELOG say an embedded app's
   metadata URL is refused if it is "relative, non-http(s), plain-http
   non-loopback or non-public-IP". Plain http to a loopback host, the name
   `localhost` and an unparseable port are refused too, and "plain-http
   non-loopback" reads as if http to loopback were allowed. State the rule
   as what is **accepted**.
2. **Loopback message.** For `http://127.0.0.1…` (the metadata URL, or the
   JWKS URL through the CLI) the startup error is "Public auth URL only
   allows http:// URLs for loopback hosts." That is false for a loopback
   host. Reword the registry member so it is true for every input that
   reaches it.
3. **Nit.** One README sentence groups the metadata URL with the CLI rule,
   but the CLI never takes a metadata URL.
4. **Nit.** Annotate `_is_absolute_http(parsed: Any)`.

The #326 registry rules apply: a rewording goes through `AuthMessage`, and
the sanitiser test (`test_every_registry_member_survives_the_sanitiser` and
its siblings in `tests/test_auth_operator_messages.py`) must still pass.

## Research summary

### One rule, many entry points

`sanitize_public_auth_url(url, *, allow_loopback_http=False)`
(`src/pmcp/auth.py:603` on `31c1357`) is the only URL rule. It runs four
steps in order:

1. `urlparse(url)`, then `.hostname` and `.port`. A `ValueError` (port out
   of range or not a number, an unclosed IPv6 bracket) raises
   `PUBLIC_URL_INVALID`.
2. `_is_absolute_http(parsed)`: scheme `http`/`https`, a netloc and a
   hostname. Otherwise `PUBLIC_URL_NOT_ABSOLUTE`.
3. `scheme == "http" and (not allow_loopback_http or not
   _is_loopback_host(host))` raises **`PUBLIC_URL_HTTP_LOOPBACK_ONLY`**, the
   member this issue is about.
4. Unless step 3 let a loopback http URL through,
   `_is_public_auth_host(host)` must hold: the host is not `localhost`
   (case-insensitive), and an IP literal, including legacy numeric forms
   read the way `inet_aton` reads them, must be public. Otherwise
   `PUBLIC_URL_NOT_PUBLIC`. A DNS name is accepted without being resolved
   (Consiliency/pmcp#211).

On success it returns `redact_auth_url(url)`, which drops userinfo, the
fragment and a port of `0`, and redacts auth-bearing query values.

Its callers, and how each one surfaces the result:

| Entry point | Call | `allow_loopback_http` | What the operator sees on refusal |
|---|---|---|---|
| CLI `--oauth-jwks-url` / `PMCP_OAUTH_JWKS_URL` (HTTP transport, resource-server mode) | `_check_auth_args` -> `check_auth_config(jwks_url=)` -> `_stored_url_ok` -> sanitiser | False | `error: <member text>`, exit 1 |
| `create_http_app(resource_server_jwks_url=)` (resource-server mode) | sanitiser directly (`transport/http.py:346`), then `AsyncJWKS.__init__` (sanitiser, then `check_auth_config`) | False | `ValueError(<member text>)` |
| `create_http_app(protected_resource_metadata_url=)`, **any auth mode** | `check_auth_config(metadata_url=)` (`transport/http.py:353`) | False | `ValueError(<member text>)` |
| `normalize_auth_metadata` (all five metadata URLs) | sanitiser | False | diagnostic `"<field> ignored: <member text> (<redacted url>)"`, URL dropped |
| `parse_www_authenticate` (`resource_metadata`) | sanitiser | False | none: the URL is dropped |
| `fetch_json_metadata` | sanitiser | False | returned diagnostic `<member text>` |
| `pmcp doctor` (downstream metadata URLs) | sanitiser | False | `"... is invalid: <member text>"` |
| URL-mode elicitation from a downstream server | `sanitize_url_elicitation_url(provenance="remote")` | False | `ELICITATION_URL_INVALID`; the member is only the `__cause__` |
| URL the operator types into `gateway.auth_connect` (`tools/handlers.py:4428`) | `sanitize_url_elicitation_url(provenance="operator")` | **True** | same as above |

So exactly one production caller allows loopback http. The CLI takes no
metadata URL at all (`grep -n metadata src/pmcp/cli.py` finds only the
version metadata and diagnostics output).

Two ordering facts matter for the tests:

- In `create_http_app` the JWKS URL is sanitised (resource-server mode only)
  **before** the metadata URL is checked, so a configuration with two bad
  URLs reports the JWKS one.
- The metadata URL check runs in **every** auth mode, while the CLI's JWKS
  check runs only for the HTTP transport in resource-server mode. That is the
  substance of nit 3.

### Ground truth: inputs through the code, not the docs

Classes come from the parser's grammar: scheme (https, http, other, none),
host kind (DNS name, `localhost`, IPv4, IPv6, legacy numeric, public or not,
none), port (none, valid, empty, `0`, out of range, not a number), userinfo,
and query/fragment. Each row was run with `scratchpad/341/entry.py` (on
`31c1357`) through `create_http_app` (metadata URL and JWKS URL), the CLI,
`normalize_auth_metadata` and both elicitation provenances, and again by the
new test module, which adds `AsyncJWKS` and `check_auth_config` x2
(`test_every_entry_point_applies_the_rule`, 9 entry points x 40 classes =
360 cases). Every strict entry point agreed with the sanitiser on every row.
`parse_www_authenticate`, `fetch_json_metadata` and `pmcp doctor` were
**read, not driven**: each calls the same sanitiser with the default
`allow_loopback_http=False`, so it inherits the strict column. "Strict" means
every caller except the operator's elicitation URL. The message column shows
the member after this plan, and whether its text is true for that input.

| Class | Example | Strict callers | Operator elicitation | Member (after fix) | True for this input? |
|---|---|---|---|---|---|
| https, public DNS name | `https://auth.example.com/jwks.json` | accepted | accepted | - | - |
| https, upper-case scheme | `HTTPS://auth.example.com/…` | accepted | accepted | - | - |
| https, public IPv4 | `https://8.8.8.8/…` | accepted | accepted | - | - |
| https, public IPv6 | `https://[2606:4700:4700::1111]/…` | accepted | accepted | - | - |
| https, port 8443 | `https://auth.example.com:8443/…` | accepted | accepted | - | - |
| https, port `0` | `https://auth.example.com:0/…` | accepted, **port dropped** | accepted | - | - |
| https, empty port | `https://auth.example.com:/…` | accepted | accepted | - | - |
| https, userinfo | `https://user:pass@auth.example.com/…` | accepted, **userinfo dropped** | accepted | - | - |
| https, query + fragment | `https://auth.example.com/j?token=s#f` | accepted, value redacted, fragment dropped | accepted | - | - |
| https, `localhost.` (FQDN form) | `https://localhost./…` | **accepted** (a name, not resolved) | accepted | - | see *Findings* |
| https, `*.localhost` | `https://app.localhost/…` | **accepted** (a name, not resolved) | accepted | - | see *Findings* |
| https, loopback IPv4 | `https://127.0.0.1/…`, `https://127.1.2.3/…` | refused | refused | `PUBLIC_URL_NOT_PUBLIC` | yes: non-public IP literal |
| https, loopback IPv6 | `https://[::1]/…` | refused | refused | `PUBLIC_URL_NOT_PUBLIC` | yes |
| https, loopback name | `https://localhost/…`, `https://LOCALHOST/…` | refused | refused | `PUBLIC_URL_NOT_PUBLIC` | yes: loopback name |
| https, private / link-local / unspecified | `https://10.0.0.5/…`, `https://169.254.169.254/…`, `https://0.0.0.0/…` | refused | refused | `PUBLIC_URL_NOT_PUBLIC` | yes |
| https, legacy numeric loopback | `https://2130706433/…` | refused | refused | `PUBLIC_URL_NOT_PUBLIC` | yes: `inet_aton` reads 127.0.0.1 |
| https, IPv4-mapped loopback | `https://[::ffff:127.0.0.1]/…` | refused | refused | `PUBLIC_URL_NOT_PUBLIC` | yes |
| **http, loopback IPv4** | `http://127.0.0.1:8080/…` | refused | **accepted** | `PUBLIC_URL_PLAIN_HTTP_REFUSED` (was `…HTTP_LOOPBACK_ONLY`) | yes now; **the old text was false** |
| **http, loopback IPv6** | `http://[::1]/…` | refused | **accepted** | same | yes now; old text false |
| **http, `localhost`** | `http://localhost/…` | refused | **accepted** | same | yes now; old text false |
| http, userinfo to loopback | `http://u:p@127.0.0.1/…` | refused | accepted, userinfo dropped | same | yes now; old text false |
| http, legacy numeric loopback | `http://2130706433/…` | refused | refused (not a loopback *literal*) | same | yes |
| http, `*.localhost` | `http://app.localhost/…` | refused | refused | same | yes |
| http, public name / IPv4 | `http://auth.example.com/…`, `http://8.8.8.8/…` | refused | refused | same | yes (old text also true here) |
| http, private IPv4 | `http://10.0.0.5/…` | refused | refused | same | yes |
| relative path | `/.well-known/oauth-protected-resource` | refused | refused | `PUBLIC_URL_NOT_ABSOLUTE` | yes |
| scheme-relative / no scheme | `//auth.example.com/…`, `auth.example.com/…` | refused | refused | `PUBLIC_URL_NOT_ABSOLUTE` | yes |
| non-http scheme | `ftp://auth.example.com/…`, `javascript:alert(1)` | refused | refused | `PUBLIC_URL_NOT_ABSOLUTE` | yes |
| https, no host | `https:///…`, `https://:443/…` | refused | refused | `PUBLIC_URL_NOT_ABSOLUTE` | yes: no host, so not absolute |
| bad port | `https://auth.example.com:65536/…`, `…:abc/…` | refused | refused | `PUBLIC_URL_INVALID` | yes |
| unclosed IPv6 bracket | `https://[::1/…` | refused | refused | `PUBLIC_URL_INVALID` | yes |
| inner whitespace | `https://auth.example.com/key set.json` | sanitiser **accepts**; startup refuses (`check_auth_config`) | accepted | `JWKS_URL_NOT_USABLE` / `METADATA_URL_NOT_USABLE` | yes |
| leading space / trailing newline | `" https://auth.example.com/x"`, `"…/x\n"` | accepted (stripped first) | accepted | - | - |

**The accepted rule, stated positively.** A strict caller accepts an auth URL
if and only if it is an absolute `https://` URL whose host is a public IP
literal or a DNS name other than `localhost`, whose port (if any) parses as
0 to 65535, and which has no inner whitespace once a leading or trailing
newline or space is stripped (the startup check adds that last condition).
DNS names are not resolved. Userinfo is accepted and dropped. Plain
`http://` is never accepted by a strict caller, loopback hosts included. The
operator's `gateway.auth_connect` URL also accepts `http://` to `localhost`
(any case) or to a loopback IP literal (`127.0.0.0/8`, `::1`, and on 3.10
`::ffff:127.0.0.1`). A legacy numeric form is not a loopback literal there.

### Which inputs reach the plain-http member

From step 3: the member fires **if and only if** `scheme == "http"` and
(the caller is strict, or the host is not loopback). Measured:

- **Strict callers:** every `http://` URL that gets past steps 1 and 2. That
  includes loopback (`127.0.0.1`, `::1`, `localhost`), and for those the old
  text "only allows http:// URLs for loopback hosts" is false. These callers
  surface the text as a **startup error**: the CLI JWKS URL,
  `create_http_app`'s JWKS and metadata URLs, and `AsyncJWKS`. It also
  appears in `normalize_auth_metadata` diagnostics, `fetch_json_metadata`
  and `pmcp doctor`.
- **The operator caller:** `http://` to a non-loopback host, for example
  `auth.example.com`, `8.8.8.8`, `10.0.0.5`, `app.localhost`, `localhost.`
  or `2130706433`. The old text is true here, but the operator never sees it:
  `sanitize_url_elicitation_url` re-raises `ELICITATION_URL_INVALID` and
  keeps the member only as `__cause__`.

So any text that says only "plain `http://` is not accepted for this URL" is
true for every member of the reaching set by construction: every such input
has scheme `http` and is refused. The text must not promise that `https://`
would be accepted for the same host. An operator who changes
`http://127.0.0.1` to `https://127.0.0.1` gets `PUBLIC_URL_NOT_PUBLIC`.

### The other three refusal texts were checked too

The issue named one member, but the class is "a refusal text false for an
input that reaches it". `test_each_refusal_is_true_of_its_input` checks all
four sanitiser members against an oracle that does not call PMCP's
classifier (`urlsplit`, `ipaddress`, `socket.inet_aton`):

- `PUBLIC_URL_INVALID`, "Invalid public auth URL.": reached only when
  `urlsplit` raises on the port or host. True.
- `PUBLIC_URL_NOT_ABSOLUTE`, "must be an absolute HTTP(S) URL.": reached
  only for a non-http(s) scheme or no host. True.
- `PUBLIC_URL_NOT_PUBLIC`, "host is a non-public IP literal or loopback
  name.": reached only for `localhost` (any case) or an IP literal (legacy
  numeric included) that is not global or is multicast. True.

No other member needs a rewording.

### Findings outside the issue's examples (not fixed here)

- **`localhost.` and `*.localhost` are accepted as public names** on the
  https path. RFC 6761 reserves `.localhost` for loopback, and
  `transport/http.py`'s own `_is_loopback_host` already treats a
  `.localhost` suffix as loopback, but `auth.py`'s does not. Fixing this
  changes behaviour in both directions. The https path would tighten.
  `gateway.auth_connect` would loosen: `http://app.localhost` would start to
  be accepted. That belongs in a security change of its own, not in a
  docs-and-message issue (Design decision 3). The new table pins today's
  behaviour, so a later fix must update these rows on purpose. Recommended
  follow-up issue: "auth: treat `localhost.` and `*.localhost` as loopback
  names in `pmcp.auth`, consistently with `transport/http.py`".
- **Port `0` is accepted and silently dropped** by `redact_auth_url`
  (`if port:`), so `https://h:0/x` is stored as `https://h/x`. This is
  harmless for a public URL and is pinned as accepted; it can go into the
  same follow-up.

## Design decisions (made explicitly)

### 1. Reword one member, rename it, and keep it a single member

The issue asks for one member reworded so it is true for every input that
reaches it. The new text is:

> `PUBLIC_URL_PLAIN_HTTP_REFUSED = "Plain http:// is not accepted for this public auth URL."`

- It is true by construction for every input in the reaching set: scheme
  `http`, refused.
- It makes no claim about loopback, and it does not promise that `https://`
  would be accepted.
- It contains no word from the redactor's keyword list followed by another
  word, so the #326 sanitiser test passes it unchanged (measured:
  `tests/test_auth_operator_messages.py` passes on the spike).
- The member is **renamed** from `PUBLIC_URL_HTTP_LOOPBACK_ONLY`, because
  the old name made the same false claim. `AuthMessage` is internal (only
  `pmcp.auth`, `pmcp.transport.http` and the tests use it; `grep -rn
  HTTP_LOOPBACK_ONLY src tests` finds the definition, the raise and two test
  table entries), so the rename has no external reader. The CHANGELOG names
  both.
- **Not split** into a strict member and an operator member. A split would
  give the operator caller a more specific text, but the operator never sees
  it: `ELICITATION_URL_INVALID` wraps it. A split would also add a
  `_STARTUP_REFUSALS` row and a second raise site for no visible gain.

### 2. The docs state what is accepted, and a test holds the README to the code

The README gets a positive rule plus a 14-row example table between
`<!-- auth-url-rule:begin -->` / `<!-- auth-url-rule:end -->` markers.
`test_the_readme_url_table_matches_the_code` parses the table and runs every
URL through `check_auth_config(jwks_url=…)` and
`check_auth_config(metadata_url=…)`. It requires the result to match the row
and every refusal reason to have at least one example. That is the
docs-consistency check: flip a row, or drop the only example of a reason,
and it goes red (mutants M14, M15). The prose rule is not machine-checked,
but it is the same sentence as the `sanitize_public_auth_url` docstring, and
the table sits directly under it. `test_the_superseded_wording_is_gone`
pins the removal of the two old phrasings (M16, M17).

The CHANGELOG's #326 bullet is still under `[Unreleased]`, so it is amended
in place to the accepted rule rather than contradicted by a later bullet. A
new `Fixed` bullet records the message change and the rename.

### 3. The `.localhost` and port-0 gaps are reported, not fixed

See *Findings*. They are pinned in the table so they stay visible, and they
are left for a follow-up issue because the change goes beyond rewording.

### 4. `_is_absolute_http(parsed: ParseResult)`

Both call sites pass `urlparse(...)`'s result, so `ParseResult` is the
exact type. The issue suggests `SplitResult | ParseResult` as an option, but
`SplitResult` would admit a type no caller passes. mypy pins it: annotating
`str` or `SplitResult` gives `arg-type` errors at both call sites (measured,
M19). No runtime test is invented for an annotation.

### 5. README nit: separate the JWKS/CLI rule from the metadata URL

The sentence becomes three. The startup refusal is for the JWKS URL (with
its three spellings) and required scopes. Then "The CLI takes no metadata
URL." Then `create_http_app`'s `protected_resource_metadata_url` gets the
same refusal, in any auth mode.

## Changes

### `src/pmcp/auth.py` (modify)

- `AuthMessage.PUBLIC_URL_HTTP_LOOPBACK_ONLY` -> `PUBLIC_URL_PLAIN_HTTP_REFUSED`,
  text "Plain http:// is not accepted for this public auth URL.", with a
  comment that records the old text and why it was false.
- The one raise site in `sanitize_public_auth_url` names the new member.
- `sanitize_public_auth_url`'s docstring states the accepted rule and which
  member each refusal raises, in order.
- `_is_absolute_http(parsed: ParseResult)`; `ParseResult` is imported from
  `urllib.parse`. `Any` stays imported because other code uses it.

### `README.md` (modify)

- The JWKS sentence (around line 167) adds `localhost` to what is rejected
  and points to the accepted form.
- The startup-refusal paragraph is split (nit 3). The accepted rule is then
  stated positively, followed by the example table between markers.

### `CHANGELOG.md` (modify)

- The #326 bullet's refused list is replaced by the accepted rule.
- A new `Fixed` bullet covers the message, the rename and the README table.

### `tests/test_auth_operator_messages.py`, `tests/test_transport_http.py` (modify)

- The `_STARTUP_REFUSALS` key and member move to the new name.
- `test_invalid_metadata_url_does_not_create_route_or_challenge_header`
  matched `"only allows http:// URLs"`; it now matches
  `r"^Plain http:// is not accepted"`.

### `tests/test_auth_public_url_rule.py` (create)

527 tests, about 1.6 s:

1. `test_the_sanitiser_applies_the_rule[class]`: each of the 40 classes,
   strict and operator.
2. `test_each_refusal_is_true_of_its_input[class]`: each refusal's claim
   holds for its input, by an independent oracle.
3. `test_every_accepted_url_is_absolute_https[class]`: the accepted rule,
   seen from the other side.
4. `test_the_plain_http_member_is_reached_by_exactly_the_refused_plain_http[host, scheme, allow]`:
   17 hosts x {http, https} x {strict, operator}. The member is raised if
   and only if the URL is plain http and the caller refuses it.
5. `test_the_plain_http_text_makes_no_loopback_claim`: the exact text, no
   "loopback", and the old name is gone.
6. `test_the_table_covers_every_member_the_sanitiser_raises`: AST. The
   members raised in `sanitize_public_auth_url` must equal the members the
   table expects.
7. `test_every_entry_point_applies_the_rule[class, entry]`: 9 entry points.
   They are `create_http_app` metadata (auth `none`), `create_http_app`
   JWKS, `AsyncJWKS`, `check_auth_config` x2, the CLI (stderr parsed),
   `normalize_auth_metadata` (diagnostic parsed), and elicitation remote and
   operator (`__cause__` read, outer `ELICITATION_URL_INVALID` asserted).
8. `test_inner_whitespace_is_refused_at_startup_not_by_the_sanitiser[entry]`.
9. `test_the_readme_url_table_matches_the_code`: the docs-consistency check.
10. `test_the_superseded_wording_is_gone`.

## Documentation impact

- README: auth section (startup refusal, accepted rule, a note that only the
  exact name `localhost` is loopback, example table).
- CHANGELOG: `[Unreleased]` `Fixed`, one new bullet and one amended bullet.
- `SECURITY.md` is unchanged. `scripts/check_security_claims.py` still
  reports `OK … 129 cited node id(s)` on the spike.

## Dependencies & order

None. The plan applies to `31c1357`. Apply the source patch, the tests patch
and the docs patch, then write the new module. Order does not matter, because
the patches touch disjoint files.

## Verification

From a fresh worktree of `origin/main` on dev0 (a team host):

```bash
git -C ~/code/pmcp worktree add -b fix/341-auth-url-docs "$WORKTREE_ROOT/pmcp-341-fix" origin/main
cd "$WORKTREE_ROOT/pmcp-341-fix"
uv sync --all-extras -p 3.10      # without --all-extras there is no venv pytest
mkdir -p /var/tmp/pmcp-341-bt-$USER
```

Apply *Verbatim bodies* (see *How to apply*), then:

```bash
# 1. the new module (spike: 527 passed, 1.8 s)
.venv/bin/python -m pytest tests/test_auth_public_url_rule.py -q -p no:cacheprovider \
  --basetemp=/var/tmp/pmcp-341-bt-$USER/new --cov-fail-under=0
# 2. the suites that touch auth, the HTTP transport, the CLI and the redactor (spike: 1525 passed, 55 deselected, 0 failed, 83 s)
env -u npm_config_cache -u npm_config_store_dir .venv/bin/python -m pytest \
  tests/test_auth.py tests/test_transport_http.py tests/test_auth_origin_wiring.py \
  tests/test_redaction_additive.py tests/test_auth_operator_messages.py tests/test_auth_public_url_rule.py \
  tests/test_cli.py tests/test_server.py tests/test_http_transport.py tests/test_scoped_advisor_audit.py \
  -q -p no:cacheprovider --basetemp=/var/tmp/pmcp-341-bt-$USER/suites --cov-fail-under=0
# 3. CI gates (spike: all clean)
.venv/bin/ruff check src tests && .venv/bin/ruff format --check src tests
.venv/bin/mypy src/pmcp/auth.py src/pmcp/cli.py src/pmcp/transport/http.py
python3 scripts/check_security_claims.py          # expect OK, 129 cited node ids
# 4. the full suite, once, detached (memory on dev0 is shared) (spike: 6050 passed, 3 skipped, 80 deselected, 0 failed, 518 s)
env -u npm_config_cache -u npm_config_store_dir nohup .venv/bin/python -m pytest -q -p no:cacheprovider \
  --basetemp=/var/tmp/pmcp-341-bt-$USER/full > "$WORKTREE_ROOT/pmcp-341-full.log" 2>&1 &
```

**Red on main.** On `31c1357` with only the new module added:
**116 failed, 411 passed**. The failures are 77 in
`test_every_entry_point_applies_the_rule`, 26 in
`test_the_plain_http_member_is_reached_by_exactly_the_refused_plain_http`, 9
in `test_the_sanitiser_applies_the_rule`, and one each in
`test_the_plain_http_text_makes_no_loopback_claim`,
`test_the_table_covers_every_member_the_sanitiser_raises`,
`test_the_readme_url_table_matches_the_code` (no table) and
`test_the_superseded_wording_is_gone`. The plain-http refusals fail because
main raises the old member, whose text is the false loopback claim. The tests
that pass on main are the rows whose behaviour this plan does not change.

## Acceptance criteria

- [ ] `AuthMessage` has `PUBLIC_URL_PLAIN_HTTP_REFUSED` with the exact text
      above, and no `PUBLIC_URL_HTTP_LOOPBACK_ONLY`.
- [ ] For every class in the ground-truth table, every entry point gives the
      table's result (`test_every_entry_point_applies_the_rule`, 360 cases).
- [ ] Every refusal's text is true for every input that reaches it
      (`test_each_refusal_is_true_of_its_input`,
      `test_the_plain_http_member_is_reached_by_exactly_the_refused_plain_http`).
- [ ] The README states the accepted rule, separates the CLI/JWKS rule from
      the metadata URL, and its example table passes
      `test_the_readme_url_table_matches_the_code`.
- [ ] The CHANGELOG's #326 bullet states the accepted rule, and a #341
      bullet records the rewording and rename.
- [ ] `_is_absolute_http` is annotated `ParseResult`, and mypy is clean.
- [ ] The #326 module `tests/test_auth_operator_messages.py` passes
      unchanged apart from the rename, including the sanitiser test.
- [ ] Every mutant in the table below is red, or listed as equivalent.
- [ ] Verification steps 1 to 4 pass.

## Mutation table

Measured on the proof tree (*Embedding proof*) with
`scratchpad/341/mutants.py`; an earlier run on the spike, without M18, gave
the same result for every other mutant. Each mutant is one
or more exact string edits to `auth.py`, `transport/http.py`, `cli.py`,
`README.md` or `CHANGELOG.md`. Each one runs
`tests/test_auth_public_url_rule.py tests/test_transport_http.py
tests/test_auth_operator_messages.py` with `-o timeout=60` and a 300 s cap,
then restores every touched file from its saved copy in a `finally`. After
the run, every file was checked byte-identical by sha256.

**18 mutants: 17 red, 1 equivalent (M13).** M19 is checked by mypy, not pytest.

| # | Mutant | Red tests (measured) |
|---|---|---|
| M1 | old loopback text restored in the registry | 2 red -- `test_invalid_metadata_url_does_not_create_route_or_challenge_header`, `test_the_plain_http_text_makes_no_loopback_claim` |
| M2 | plain-http guard: `or` -> `and` (strict callers accept http) | 64 red -- `test_every_entry_point_applies_the_rule`, `test_the_plain_http_member_is_reached_by_exactly_the_refused_plain_http`, `test_the_readme_url_table_matches_the_code`, `test_the_sanitiser_applies_the_rule` |
| M3 | plain-http raise names PUBLIC_URL_NOT_PUBLIC | 117 red -- `test_every_builtin_message_site_is_driven`, `test_every_builtin_refusal_goes_through_the_renderer`, `test_every_entry_point_applies_the_rule`, `test_invalid_metadata_url_does_not_create_route_or_challenge_header`, `test_the_plain_http_member_is_reached_by_exactly_the_refused_plain_http`, `test_the_readme_url_table_matches_the_code`, `test_the_sanitiser_applies_the_rule`, `test_the_table_covers_every_member_the_sanitiser_raises` |
| M4 | non-public raise names PUBLIC_URL_PLAIN_HTTP_REFUSED | 129 red -- `test_an_ip_literal_jwks_url_keeps_its_specific_message_through_the_cli`, `test_every_builtin_message_site_is_driven`, `test_every_builtin_refusal_goes_through_the_renderer`, `test_every_entry_point_applies_the_rule`, `test_the_plain_http_member_is_reached_by_exactly_the_refused_plain_http`, `test_the_readme_url_table_matches_the_code`, `test_the_sanitiser_applies_the_rule`, `test_the_table_covers_every_member_the_sanitiser_raises` |
| M5 | not-absolute raise names PUBLIC_URL_INVALID | 79 red -- `test_cli_and_env_paths_refuse_at_startup`, `test_every_builtin_message_site_is_driven`, `test_every_builtin_refusal_goes_through_the_renderer`, `test_every_entry_point_applies_the_rule`, `test_startup_refuses_a_jwks_url_the_renderer_would_refuse`, `test_startup_refuses_a_metadata_url_the_renderer_would_refuse`, `test_the_readme_url_table_matches_the_code`, `test_the_sanitiser_applies_the_rule`, `test_the_table_covers_every_member_the_sanitiser_raises` |
| M6 | port no longer parsed in the sanitiser | 23 red -- `test_a_swapped_member_at_a_builtin_site_is_a_type_error_not_a_placeholder`, `test_every_builtin_refusal_goes_through_the_renderer`, `test_every_entry_point_applies_the_rule`, `test_the_readme_url_table_matches_the_code`, `test_the_sanitiser_applies_the_rule` |
| M7 | `localhost` no longer a non-public name | 21 red -- `test_every_entry_point_applies_the_rule`, `test_the_readme_url_table_matches_the_code`, `test_the_sanitiser_applies_the_rule` |
| M8 | auth `_is_loopback_host` also takes *.localhost | 3 red -- `test_every_entry_point_applies_the_rule`, `test_the_plain_http_member_is_reached_by_exactly_the_refused_plain_http`, `test_the_sanitiser_applies_the_rule` |
| M9 | operator elicitation loses loopback http | 4 red -- `test_every_entry_point_applies_the_rule` |
| M10 | a new refusal in the sanitiser (port 0) without a table row | 12 red -- `test_every_entry_point_applies_the_rule`, `test_the_sanitiser_applies_the_rule`, `test_the_site_check_sees_every_site`, `test_the_table_covers_every_member_the_sanitiser_raises` |
| M11 | CLI skips the JWKS URL check | 37 red -- `test_an_ip_literal_jwks_url_keeps_its_specific_message_through_the_cli`, `test_cli_and_env_paths_refuse_at_startup`, `test_every_entry_point_applies_the_rule`, `test_inner_whitespace_is_refused_at_startup_not_by_the_sanitiser` |
| M12 | create_http_app skips the metadata URL check | 37 red -- `test_every_builtin_refusal_goes_through_the_renderer`, `test_every_entry_point_applies_the_rule`, `test_inner_whitespace_is_refused_at_startup_not_by_the_sanitiser`, `test_invalid_metadata_url_does_not_create_route_or_challenge_header`, `test_startup_refuses_a_metadata_url_the_renderer_would_refuse` |
| M13 | create_http_app skips sanitising the JWKS URL up front | **0 red -- equivalent** (see below) |
| M14 | README row flipped: http loopback shown as accepted | 1 red -- `test_the_readme_url_table_matches_the_code` |
| M15 | README drops the only `invalid URL` example | 1 red -- `test_the_readme_url_table_matches_the_code` |
| M16 | README restores the superseded refused-list wording | 1 red -- `test_the_superseded_wording_is_gone` |
| M17 | CHANGELOG restores the superseded wording | 1 red -- `test_the_superseded_wording_is_gone` |
| M18 | AsyncJWKS stores its URL unsanitised | 3 red -- `test_a_value_the_store_cleans_is_checked_as_stored` |
| M19 | `_is_absolute_http(parsed: str)` or `(parsed: SplitResult)` | mypy: `arg-type` at both call sites (`auth.py:198`, `auth.py:631`); with main's `Any`, mypy is silent |

M13 is **equivalent**. `AsyncJWKS.__init__`, constructed on the next line,
runs the same sanitiser on the same URL, so dropping the early call changes
no observable behaviour. M18 drops the sanitiser inside `AsyncJWKS` instead.
The new module cannot see it either, because `AsyncJWKS` then calls
`check_auth_config(jwks_url=…)`, which sanitises again and refuses the same
URLs with the same members. The #326 module's
`test_a_value_the_store_cleans_is_checked_as_stored` kills it, because the
stored `url` keeps its trailing newline. Every non-equivalent mutant is red.

## Non-goals

- Changing what is accepted. No URL changes outcome. Only the text and name
  of one member change, plus docs and an annotation.
- `.localhost` / `localhost.` / port-0 handling (*Findings*, follow-up).
- Resolving DNS names (Consiliency/pmcp#211, a deliberate non-goal there).
- The elicitation wrapper's own text (`ELICITATION_URL_INVALID`). It is true
  for every input and unchanged.

## Unverified

- **Python 3.11 / 3.12.** Run on 3.10 only. The oracle's
  `IPv6Address.ipv4_mapped` unwrapping and `is_loopback` for
  `::ffff:127.0.0.1` may differ between CPython versions. The rows that depend on it are
  `https v4-mapped` (refused on every version via `_unwrap_embedded_v4`) and
  `[::ffff:127.0.0.1]` in the reaching-set test, whose expectation comes
  from `ipaddress` itself and so follows the running version.
- **A real deployment.** The CLI is driven through `parse_args` and
  `run_server` with `GatewayServer` patched, as the #326 module does.

## Execution Policy

- execute: effort=low.
- reason: one registry member reworded and renamed, one annotation, a
  docstring, two README paragraphs plus a table, two CHANGELOG bullets, a
  rename in two existing test files, and one new test module.
- Re-run the mutation table, ruff and mypy before requesting review.
- Get a cross-vendor panel CR before merge, as for every PR to main.

## Verbatim bodies

### How to apply

1. Save the three patches below to `341-src.patch`, `341-tests.patch` and
   `341-docs.patch`, then run `git apply 341-src.patch 341-tests.patch
   341-docs.patch` on `31c1357`.
2. Write the test module below to `tests/test_auth_public_url_rule.py`.

### Patch — `src/pmcp/auth.py`

````diff
diff --git a/src/pmcp/auth.py b/src/pmcp/auth.py
index 51b2d05..21b85cc 100644
--- a/src/pmcp/auth.py
+++ b/src/pmcp/auth.py
@@ -13,7 +13,7 @@ from ipaddress import IPv4Address, IPv6Address, ip_address, ip_network
 from itertools import product
 from typing import Any, Literal
 from urllib.error import HTTPError
-from urllib.parse import parse_qsl, quote, urlparse, urlunparse
+from urllib.parse import ParseResult, parse_qsl, quote, urlparse, urlunparse
 from urllib.request import HTTPRedirectHandler, Request, build_opener
 
 import aiohttp
@@ -106,8 +106,13 @@ class AuthMessage:
     PUBLIC_URL_NOT_ABSOLUTE = AuthText(
         "Public auth URL must be an absolute HTTP(S) URL."
     )
-    PUBLIC_URL_HTTP_LOOPBACK_ONLY = AuthText(
-        "Public auth URL only allows http:// URLs for loopback hosts."
+    # Consiliency/pmcp#341: was PUBLIC_URL_HTTP_LOOPBACK_ONLY, "Public auth
+    # URL only allows http:// URLs for loopback hosts." -- false for every
+    # caller that does not allow loopback http (the JWKS URL, the metadata
+    # URL, the CLI), where `http://127.0.0.1` reaches it too. It is raised
+    # for exactly the plain-http URLs the caller refuses, and says only that.
+    PUBLIC_URL_PLAIN_HTTP_REFUSED = AuthText(
+        "Plain http:// is not accepted for this public auth URL."
     )
     PUBLIC_URL_NOT_PUBLIC = AuthText(
         "Public auth URL host is a non-public IP literal or loopback name."
@@ -170,7 +175,7 @@ _MEMBER_FIELDS = {
 _SCOPE_LIST = re.compile(r"[\x21\x23-\x5B\x5D-\x7E]+(?: [\x21\x23-\x5B\x5D-\x7E]+)*")
 
 
-def _is_absolute_http(parsed: Any) -> bool:
+def _is_absolute_http(parsed: ParseResult) -> bool:
     """The absolute-HTTP(S) rule `sanitize_public_auth_url` applies to every
     configured auth URL: an http(s) scheme, a netloc and a hostname."""
     return (
@@ -601,7 +606,21 @@ UNVERIFIED_URL_CAVEAT = (
 
 
 def sanitize_public_auth_url(url: str, *, allow_loopback_http: bool = False) -> str:
-    """Validate and redact a public absolute auth metadata or elicitation URL."""
+    """Validate and redact a public absolute auth metadata or elicitation URL.
+
+    Accepted (Consiliency/pmcp#341): an absolute ``https://`` URL whose port,
+    if any, parses (0-65535) and whose host is a public IP literal or a DNS
+    name other than ``localhost`` -- a name is not resolved. With
+    ``allow_loopback_http`` (only the operator's URL-mode elicitation path),
+    also ``http://`` to ``localhost`` (any case) or a loopback IP literal
+    (``127.0.0.0/8``, ``::1``); a legacy numeric form is not one. Userinfo
+    and auth-bearing query values are stripped from what is returned.
+    Refused, in this order: an unparseable port or host
+    (`PUBLIC_URL_INVALID`), no http(s) scheme or no host
+    (`PUBLIC_URL_NOT_ABSOLUTE`), plain ``http://`` the caller does not allow
+    (`PUBLIC_URL_PLAIN_HTTP_REFUSED`), and a non-public IP literal (legacy
+    numeric forms included) or ``localhost`` (`PUBLIC_URL_NOT_PUBLIC`).
+    """
     try:
         parsed = urlparse(url)
         hostname = parsed.hostname
@@ -615,7 +634,7 @@ def sanitize_public_auth_url(url: str, *, allow_loopback_http: bool = False) ->
     if parsed.scheme == "http" and (
         not allow_loopback_http or not _is_loopback_host(hostname)
     ):
-        raise ValueError(render_auth_message(AuthMessage.PUBLIC_URL_HTTP_LOOPBACK_ONLY))
+        raise ValueError(render_auth_message(AuthMessage.PUBLIC_URL_PLAIN_HTTP_REFUSED))
     if not (
         allow_loopback_http and parsed.scheme == "http" and _is_loopback_host(hostname)
     ):
````

### Patch — `tests/test_auth_operator_messages.py`, `tests/test_transport_http.py`

````diff
diff --git a/tests/test_auth_operator_messages.py b/tests/test_auth_operator_messages.py
index 862d690..a0a40c4 100644
--- a/tests/test_auth_operator_messages.py
+++ b/tests/test_auth_operator_messages.py
@@ -1693,9 +1693,9 @@ _STARTUP_REFUSALS: dict[str, tuple[Callable[[], Any], str, type[Exception]]] = {
         "PUBLIC_URL_NOT_ABSOLUTE",
         ValueError,
     ),
-    "PUBLIC_URL_HTTP_LOOPBACK_ONLY": (
+    "PUBLIC_URL_PLAIN_HTTP_REFUSED": (
         lambda: auth_mod.AsyncJWKS("http://issuer.example/jwks.json"),
-        "PUBLIC_URL_HTTP_LOOPBACK_ONLY",
+        "PUBLIC_URL_PLAIN_HTTP_REFUSED",
         ValueError,
     ),
     "PUBLIC_URL_NOT_PUBLIC": (
diff --git a/tests/test_transport_http.py b/tests/test_transport_http.py
index 237759b..188e5b3 100644
--- a/tests/test_transport_http.py
+++ b/tests/test_transport_http.py
@@ -189,7 +189,9 @@ class TestAuthGuardHttp:
         # omit the route. (The `?token=` query alone is not a reason: an
         # https URL with one starts and serves its route.) The message is
         # the registry's and carries none of the URL.
-        with pytest.raises(ValueError, match="only allows http:// URLs") as refused:
+        with pytest.raises(
+            ValueError, match=r"^Plain http:// is not accepted"
+        ) as refused:
             _make_app(
                 auth_token="mysecret",
                 protected_resource_metadata_url=(
````

### Patch — `README.md`, `CHANGELOG.md`

````diff
diff --git a/CHANGELOG.md b/CHANGELOG.md
index 0ae8ba2..3c550c5 100644
--- a/CHANGELOG.md
+++ b/CHANGELOG.md
@@ -437,6 +437,15 @@ and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0
 
 
 ### Fixed
+- **The plain-`http://` auth URL refusal no longer claims loopback is
+  allowed.** A JWKS URL or metadata URL such as `http://127.0.0.1/...` was
+  refused at startup with "Public auth URL only allows http:// URLs for
+  loopback hosts." -- untrue there, since these URLs never accept plain
+  `http://`. The message is now "Plain http:// is not accepted for this
+  public auth URL." (registry member `PUBLIC_URL_PLAIN_HTTP_REFUSED`, was
+  `PUBLIC_URL_HTTP_LOOPBACK_ONLY`). The README now states which auth URLs
+  are accepted, with a table of examples that a test checks against the
+  code. See [Consiliency/pmcp#341](https://github.com/Consiliency/pmcp/issues/341).
 - **Auth operator messages come through pmcp's own sanitiser intact, and
   invalid auth configuration refuses startup.** Four auth and startup
   messages were reworded because pmcp's own sanitiser rewrote them (`Token
@@ -455,8 +464,11 @@ and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0
   (a stray value in another mode is ignored, as before); and on the
   programmatic path, `create_http_app` refuses a protected-resource
   metadata URL that normalisation used to drop silently, omitting the
-  metadata route -- relative, non-http(s), plain http to a non-loopback
-  host, or a non-public IP literal. The metadata route also refuses to
+  metadata route -- any URL that is not an absolute `https://` URL to a
+  public IP literal or a DNS name other than `localhost`, with a valid port
+  (so plain `http://` to any host, loopback included, is refused too; see
+  [Consiliency/pmcp#341](https://github.com/Consiliency/pmcp/issues/341)).
+  The metadata route also refuses to
   start rather than publish an empty `resource`, which no shipped
   configuration reaches. The README now documents deployments behind a
   prefix-stripping proxy; the metadata `404` there is a known follow-up,
diff --git a/README.md b/README.md
index cfdd1b2..64f63c9 100644
--- a/README.md
+++ b/README.md
@@ -165,7 +165,8 @@ audience is bound to the configured `resource_server_audience` (the server's
 canonical resource URI, per RFC 8707); it is never derived from the request
 Host header. `resource-server` mode fails closed at startup if the issuer,
 JWKS URL, or audience is missing, and `resource_server_jwks_url` must be an
-`https` URL and is rejected when its host is a non-public IP literal. Token
+`https` URL and is rejected when its host is a non-public IP literal or
+`localhost` (the accepted form is below). Token
 signatures are only accepted for the operator-configured
 `resource_server_allowed_algorithms` allowlist (default `RS256`/`ES256`); the
 token's own `alg` header is never trusted. JWKS is fetched
@@ -204,17 +205,48 @@ that path to PMCP unchanged. An application that mounts PMCP under a
 not at the URL's own path. The `resource` is never taken from the request
 `Host`. A forged token, including one whose algorithm does not match the
 published key's type, gets `401`, never `500`. In resource-server mode over
-HTTP, PMCP refuses to start, with a one-line error, if the JWKS URL or
-metadata URL -- after the cleanup PMCP applies when it stores a URL, which
-drops a trailing newline or a leading space -- is not a public absolute
-http(s) URL without whitespace, or if a required scope is not a single RFC
-6749 scope (printable ASCII with no space, quote or backslash;
-`--required-scope` and `PMCP_REQUIRED_SCOPES` alike), so every
-`401`/`403`/`503` it later sends can be built. An embedding application
-that passes `protected_resource_metadata_url` gets the same refusal, in
-any auth mode, for a relative, non-http(s), plain-http non-loopback or
-non-public-IP URL, which was previously dropped with the metadata route
-silently omitted.
+HTTP, PMCP refuses to start, with a one-line error, if the JWKS URL
+(`--oauth-jwks-url`, `PMCP_OAUTH_JWKS_URL` or `resource_server_jwks_url`) is
+not an accepted auth URL, or if a required scope is not a single RFC 6749
+scope (printable ASCII with no space, quote or backslash; `--required-scope`
+and `PMCP_REQUIRED_SCOPES` alike), so every `401`/`403`/`503` it later sends
+can be built. The CLI takes no metadata URL. An application that embeds
+PMCP and passes `protected_resource_metadata_url` to `create_http_app` gets
+the same refusal for that URL, in any auth mode; such a URL used to be
+dropped, with the metadata route silently omitted.
+
+An auth URL is accepted only if it is an absolute `https://` URL whose host
+is a public IP literal or a DNS name other than `localhost`, whose port, if
+it has one, is a number no greater than 65535, and which has no whitespace
+once PMCP has dropped a trailing newline or a leading space. A DNS name is
+not resolved (see below), and only the exact name `localhost` is treated as
+loopback: `localhost.` and `*.localhost` are names like any other and pass
+unresolved. Userinfo such as `user:pass@` is accepted and
+dropped. Everything else is refused, including plain `http://` to any host,
+loopback hosts too, and `https://` to `localhost` or a loopback address.
+(Plain `http://` to a loopback host is accepted in one place only: a URL the
+operator types into `gateway.auth_connect`.) For example, as a JWKS URL or a
+metadata URL:
+
+<!-- auth-url-rule:begin -->
+| URL | Result |
+|---|---|
+| `https://auth.example.com/jwks.json` | accepted |
+| `https://8.8.8.8/jwks.json` | accepted |
+| `https://auth.example.com:8443/jwks.json` | accepted |
+| `https://user:pass@auth.example.com/jwks.json` | accepted (userinfo dropped) |
+| `http://auth.example.com/jwks.json` | refused: plain http |
+| `http://127.0.0.1:8080/jwks.json` | refused: plain http |
+| `http://localhost/jwks.json` | refused: plain http |
+| `https://localhost/jwks.json` | refused: non-public host |
+| `https://127.0.0.1/jwks.json` | refused: non-public host |
+| `https://10.0.0.5/jwks.json` | refused: non-public host |
+| `/.well-known/oauth-protected-resource` | refused: not an absolute http(s) URL |
+| `ftp://auth.example.com/jwks.json` | refused: not an absolute http(s) URL |
+| `https://auth.example.com:abc/jwks.json` | refused: invalid URL |
+| `https://auth.example.com/key set.json` | refused: whitespace |
+<!-- auth-url-rule:end -->
+
 In public auth metadata URLs it rejects hosts written as non-public **IP
 literals** — private, CGNAT, link-local, loopback, multicast, site-local, and
 unspecified — including IPv4 addresses embedded in IPv6 literals and legacy
````

### File — `tests/test_auth_public_url_rule.py`

````python
"""Consiliency/pmcp#341: which auth URLs PMCP accepts, and whether each
refusal message is true of the URL it refuses.

`sanitize_public_auth_url` is the one rule behind the JWKS URL (CLI, env,
`create_http_app`, `AsyncJWKS`), the protected-resource metadata URL
(`create_http_app`, `normalize_auth_metadata`) and URL-mode elicitation. Its
plain-http refusal used to read "Public auth URL only allows http:// URLs
for loopback hosts." -- false for `http://127.0.0.1` on every path except
the operator's elicitation URL, the only caller that allows loopback http.

The classes below come from the parser's grammar -- scheme, host kind
(name, `localhost`, IPv4/IPv6/legacy-numeric literal, public or not),
port, userinfo, no host -- not from the issue's examples. Each row carries
the result for a caller that refuses loopback http (`strict`) and for the
one that allows it (`operator`). The tests check that every entry point
applies the row, that each refusal's text is true of the URL that reaches
it (by an oracle that does not call PMCP's classifier), that the plain-http
member is reached by exactly the refused plain-http URLs, and that the
README's example table agrees with the code.
"""

from __future__ import annotations

import ast
import asyncio
import contextlib
import io
import ipaddress
import re
import socket
from pathlib import Path
from typing import Any, Callable
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import urlsplit

import pytest

from pmcp import auth as auth_mod
from pmcp.auth import (
    AuthMessage,
    auth_messages,
    check_auth_config,
    normalize_auth_metadata,
    sanitize_public_auth_url,
    sanitize_url_elicitation_url,
)
from pmcp.transport.http import create_http_app

_ROOT = Path(__file__).resolve().parents[1]

INVALID = "PUBLIC_URL_INVALID"
NOT_ABSOLUTE = "PUBLIC_URL_NOT_ABSOLUTE"
PLAIN_HTTP = "PUBLIC_URL_PLAIN_HTTP_REFUSED"
NOT_PUBLIC = "PUBLIC_URL_NOT_PUBLIC"

# (label, url, strict, operator). `None` is accepted; otherwise the member
# `sanitize_public_auth_url` raises. `strict` is every caller but one;
# `operator` is `allow_loopback_http=True` (gateway.auth_connect's URL).
CLASSES: list[tuple[str, str, str | None, str | None]] = [
    # -- https, accepted ------------------------------------------------------
    ("https public name", "https://auth.example.com/jwks.json", None, None),
    ("https upper-case scheme", "HTTPS://auth.example.com/jwks.json", None, None),
    ("https public IPv4", "https://8.8.8.8/jwks.json", None, None),
    ("https public IPv6", "https://[2606:4700:4700::1111]/jwks.json", None, None),
    ("https port 8443", "https://auth.example.com:8443/jwks.json", None, None),
    ("https port 0", "https://auth.example.com:0/jwks.json", None, None),
    ("https empty port", "https://auth.example.com:/jwks.json", None, None),
    ("https userinfo", "https://user:pass@auth.example.com/jwks.json", None, None),
    ("https query+fragment", "https://auth.example.com/j?token=s#f", None, None),
    # Names are not resolved (Consiliency/pmcp#211): only the exact name
    # `localhost` counts as a loopback name. These two are pinned as they
    # are today; see the plan's follow-up.
    ("https localhost. (FQDN)", "https://localhost./jwks.json", None, None),
    ("https *.localhost", "https://app.localhost/jwks.json", None, None),
    # -- https, non-public host -----------------------------------------------
    ("https loopback IPv4", "https://127.0.0.1/jwks.json", NOT_PUBLIC, NOT_PUBLIC),
    ("https loopback 127/8", "https://127.1.2.3/jwks.json", NOT_PUBLIC, NOT_PUBLIC),
    ("https loopback IPv6", "https://[::1]/jwks.json", NOT_PUBLIC, NOT_PUBLIC),
    ("https localhost", "https://localhost/jwks.json", NOT_PUBLIC, NOT_PUBLIC),
    ("https LOCALHOST", "https://LOCALHOST/jwks.json", NOT_PUBLIC, NOT_PUBLIC),
    ("https private IPv4", "https://10.0.0.5/jwks.json", NOT_PUBLIC, NOT_PUBLIC),
    ("https link-local", "https://169.254.169.254/x", NOT_PUBLIC, NOT_PUBLIC),
    ("https unspecified", "https://0.0.0.0/jwks.json", NOT_PUBLIC, NOT_PUBLIC),
    ("https legacy numeric", "https://2130706433/jwks.json", NOT_PUBLIC, NOT_PUBLIC),
    ("https v4-mapped", "https://[::ffff:127.0.0.1]/x", NOT_PUBLIC, NOT_PUBLIC),
    # -- plain http ------------------------------------------------------------
    ("http loopback IPv4", "http://127.0.0.1:8080/jwks.json", PLAIN_HTTP, None),
    ("http loopback IPv6", "http://[::1]/jwks.json", PLAIN_HTTP, None),
    ("http localhost", "http://localhost/jwks.json", PLAIN_HTTP, None),
    ("http userinfo loopback", "http://u:p@127.0.0.1/jwks.json", PLAIN_HTTP, None),
    ("http legacy numeric", "http://2130706433/jwks.json", PLAIN_HTTP, PLAIN_HTTP),
    ("http *.localhost", "http://app.localhost/jwks.json", PLAIN_HTTP, PLAIN_HTTP),
    ("http public name", "http://auth.example.com/jwks.json", PLAIN_HTTP, PLAIN_HTTP),
    ("http public IPv4", "http://8.8.8.8/jwks.json", PLAIN_HTTP, PLAIN_HTTP),
    ("http private IPv4", "http://10.0.0.5/jwks.json", PLAIN_HTTP, PLAIN_HTTP),
    # -- not an absolute http(s) URL ---------------------------------------------
    (
        "relative path",
        "/.well-known/oauth-protected-resource",
        NOT_ABSOLUTE,
        NOT_ABSOLUTE,
    ),
    ("scheme-relative", "//auth.example.com/jwks.json", NOT_ABSOLUTE, NOT_ABSOLUTE),
    ("no scheme", "auth.example.com/jwks.json", NOT_ABSOLUTE, NOT_ABSOLUTE),
    ("ftp scheme", "ftp://auth.example.com/jwks.json", NOT_ABSOLUTE, NOT_ABSOLUTE),
    ("javascript scheme", "javascript:alert(1)", NOT_ABSOLUTE, NOT_ABSOLUTE),
    ("https no host", "https:///jwks.json", NOT_ABSOLUTE, NOT_ABSOLUTE),
    ("https port only", "https://:443/jwks.json", NOT_ABSOLUTE, NOT_ABSOLUTE),
    # -- unparseable -----------------------------------------------------------
    ("port 65536", "https://auth.example.com:65536/jwks.json", INVALID, INVALID),
    ("port not a number", "https://auth.example.com:abc/jwks.json", INVALID, INVALID),
    ("unclosed IPv6", "https://[::1/jwks.json", INVALID, INVALID),
]
_BY_LABEL = {label: (url, strict, op) for label, url, strict, op in CLASSES}
assert len(_BY_LABEL) == len(CLASSES), "duplicate class label"

_NAMES = {str(text): name for name, text in auth_messages().items()}


def _member(exc: BaseException) -> str:
    """The registry member an exception's text is, by name."""
    return _NAMES.get(str(exc), f"<not a member: {exc!s}>")


def _outcome(call: Callable[[], object]) -> str | None:
    try:
        call()
    except ValueError as exc:
        return _member(exc)
    return None


# --- the oracle: what each refusal claims, without PMCP's classifier --------


def _literal(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """`host` as an IP literal, legacy numeric forms included (inet_aton is
    what a resolver does with them), IPv4-mapped IPv6 unwrapped."""
    try:
        address: ipaddress.IPv4Address | ipaddress.IPv6Address = ipaddress.ip_address(
            host
        )
    except ValueError:
        try:
            address = ipaddress.IPv4Address(socket.inet_aton(host))
        except OSError:
            return None
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        return address.ipv4_mapped
    return address


def _is_loopback(host: str) -> bool:
    """The loopback the operator path allows: the name `localhost` or an IP
    literal `ipaddress` calls loopback (not a legacy numeric form)."""
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _claim_holds(member: str, url: str) -> bool:
    """True if the refusal `member` says something true about `url`."""
    try:
        parts = urlsplit(url)
        host = parts.hostname
        _ = parts.port
    except ValueError:
        return member == INVALID  # "Invalid public auth URL."
    if member == INVALID:
        return False
    if member == NOT_ABSOLUTE:  # "must be an absolute HTTP(S) URL."
        return parts.scheme not in {"http", "https"} or not host
    if member == PLAIN_HTTP:  # "Plain http:// is not accepted for this ..."
        return parts.scheme == "http" and bool(host)
    if member == NOT_PUBLIC:  # "a non-public IP literal or loopback name."
        assert host
        if host.lower() == "localhost":
            return True
        address = _literal(host)
        return address is not None and (not address.is_global or address.is_multicast)
    raise AssertionError(f"no oracle for {member}")


# --- 1. the rule, at the sanitiser ------------------------------------------


@pytest.mark.parametrize("label", list(_BY_LABEL))
def test_the_sanitiser_applies_the_rule(label: str) -> None:
    url, strict, operator = _BY_LABEL[label]
    assert _outcome(lambda: sanitize_public_auth_url(url)) == strict
    assert (
        _outcome(lambda: sanitize_public_auth_url(url, allow_loopback_http=True))
        == operator
    )


@pytest.mark.parametrize("label", list(_BY_LABEL))
def test_each_refusal_is_true_of_its_input(label: str) -> None:
    """The message an input gets states something true about that input."""
    url, strict, operator = _BY_LABEL[label]
    for member in {strict, operator} - {None}:
        assert member is not None
        assert _claim_holds(member, url), f"{member} is untrue for {url!r}"


@pytest.mark.parametrize(
    "label", [label for label, _, strict, _ in CLASSES if strict is None]
)
def test_every_accepted_url_is_absolute_https(label: str) -> None:
    """The README's accepted rule, from the other side: whatever a strict
    caller accepts is `https://` with a host that is not `localhost` and not
    a non-public IP literal."""
    url = _BY_LABEL[label][0]
    parts = urlsplit(url)
    assert parts.scheme == "https"
    assert parts.hostname and parts.hostname.lower() != "localhost"
    address = _literal(parts.hostname)
    assert address is None or (address.is_global and not address.is_multicast)


_HOSTS = [
    "auth.example.com",
    "8.8.8.8",
    "[2606:4700:4700::1111]",
    "127.0.0.1",
    "127.1.2.3",
    "[::1]",
    "[::ffff:127.0.0.1]",
    "localhost",
    "LOCALHOST",
    "localhost.",
    "app.localhost",
    "10.0.0.5",
    "169.254.169.254",
    "2130706433",
    "0x7f.1",
    "u:p@127.0.0.1",
    "127.0.0.1:8080",
]


@pytest.mark.parametrize("allow", [False, True])
@pytest.mark.parametrize("scheme", ["http", "https"])
@pytest.mark.parametrize("host", _HOSTS)
def test_the_plain_http_member_is_reached_by_exactly_the_refused_plain_http(
    host: str, scheme: str, allow: bool
) -> None:
    """Every input that reaches `PUBLIC_URL_PLAIN_HTTP_REFUSED` is a plain
    http URL the caller refuses; every plain-http URL is refused with it
    unless the caller allows loopback and the host is loopback."""
    url = f"{scheme}://{host}/jwks.json"
    got = _outcome(lambda: sanitize_public_auth_url(url, allow_loopback_http=allow))
    hostname = urlsplit(url).hostname or ""
    if scheme == "https":
        assert got != PLAIN_HTTP
    elif allow and _is_loopback(hostname):
        assert got is None
    else:
        assert got == PLAIN_HTTP


def test_the_plain_http_text_makes_no_loopback_claim() -> None:
    """The old text, "only allows http:// URLs for loopback hosts", was false
    for every strict caller. The member says only what is true of every
    input that reaches it."""
    text = str(AuthMessage.PUBLIC_URL_PLAIN_HTTP_REFUSED)
    assert text == "Plain http:// is not accepted for this public auth URL."
    assert "loopback" not in text.lower()
    assert not hasattr(AuthMessage, "PUBLIC_URL_HTTP_LOOPBACK_ONLY")


def test_the_table_covers_every_member_the_sanitiser_raises() -> None:
    """A member added to `sanitize_public_auth_url` without a class here
    fails this test."""
    tree = ast.parse((_ROOT / "src/pmcp/auth.py").read_text())
    func = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "sanitize_public_auth_url"
    )
    raised = {
        node.attr
        for node in ast.walk(func)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "AuthMessage"
    }
    covered = {m for _, _, s, o in CLASSES for m in (s, o) if m is not None}
    assert raised == covered


# --- 2. every entry point applies the same row -------------------------------

_ISSUER = "https://issuer.example"
_AUDIENCE = "https://pmcp.example/mcp"
_GOOD_JWKS = "https://issuer.example/.well-known/jwks.json"


def _app(**kwargs: Any) -> None:
    with patch("pmcp.transport.http.StreamableHTTPSessionManager", autospec=True):
        create_http_app(MagicMock(), **kwargs)


def _metadata_app(url: str) -> None:
    _app(auth_mode="none", protected_resource_metadata_url=url)


def _jwks_app(url: str) -> None:
    _app(
        auth_mode="resource-server",
        resource_server_issuer=_ISSUER,
        resource_server_jwks_url=url,
        resource_server_audience=_AUDIENCE,
    )


def _normalised(url: str) -> None:
    """`normalize_auth_metadata` does not raise: it drops the URL and says
    why in a diagnostic. Re-raise that reason so the row can be compared."""
    info = normalize_auth_metadata(protected_resource_metadata_url=url)
    if info.protected_resource_metadata_url is not None:
        return
    for text in _NAMES:
        if any(
            d.startswith("protected_resource_metadata_url ignored: " + text)
            for d in info.diagnostics
        ):
            raise ValueError(text)
    raise AssertionError(f"dropped without a registry reason: {info.diagnostics}")


def _elicitation(provenance: str) -> Callable[[str], None]:
    def call(url: str) -> None:
        try:
            sanitize_url_elicitation_url(url, provenance=provenance)  # type: ignore[arg-type]
        except ValueError as exc:
            assert str(exc) == AuthMessage.ELICITATION_URL_INVALID
            assert exc.__cause__ is not None
            raise ValueError(str(exc.__cause__)) from exc

    return call


_STDERR = re.compile(r"^error: (?P<text>.*)\n$", re.S)


def _cli_factory(monkeypatch: pytest.MonkeyPatch) -> Callable[[str], None]:
    from pmcp.cli import parse_args, run_server

    def call(url: str) -> None:
        for name in (
            "PMCP_TRANSPORT",
            "PMCP_AUTH_MODE",
            "PMCP_OAUTH_JWKS_URL",
            "PMCP_REQUIRED_SCOPES",
        ):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setattr(
            "sys.argv",
            [
                "pmcp",
                "--transport",
                "http",
                "--auth-mode",
                "resource-server",
                "--oauth-issuer",
                _ISSUER,
                "--oauth-audience",
                _AUDIENCE,
                "--oauth-jwks-url",
                url,
            ],
        )
        err = io.StringIO()
        args = parse_args()
        try:
            with (
                patch("pmcp.server.GatewayServer") as gs,
                contextlib.redirect_stderr(err),
            ):
                gs.return_value.run = AsyncMock()
                asyncio.run(run_server(args))
        except SystemExit as exc:
            assert exc.code == 1
            match = _STDERR.match(err.getvalue())
            assert match, err.getvalue()
            raise ValueError(match["text"]) from None

    return call


_ENTRIES: dict[str, Callable[[pytest.MonkeyPatch], Callable[[str], None]]] = {
    "create_http_app metadata URL": lambda mp: _metadata_app,
    "create_http_app JWKS URL": lambda mp: _jwks_app,
    "AsyncJWKS": lambda mp: auth_mod.AsyncJWKS,
    "check_auth_config jwks_url": lambda mp: lambda u: check_auth_config(jwks_url=u),
    "check_auth_config metadata_url": (
        lambda mp: lambda u: check_auth_config(metadata_url=u)
    ),
    "CLI --oauth-jwks-url": _cli_factory,
    "normalize_auth_metadata": lambda mp: _normalised,
    "elicitation, remote": lambda mp: _elicitation("remote"),
    "elicitation, operator": lambda mp: _elicitation("operator"),
}


@pytest.mark.parametrize("entry", list(_ENTRIES))
@pytest.mark.parametrize("label", list(_BY_LABEL))
def test_every_entry_point_applies_the_rule(
    monkeypatch: pytest.MonkeyPatch, label: str, entry: str
) -> None:
    url, strict, operator = _BY_LABEL[label]
    expected = operator if entry == "elicitation, operator" else strict
    call = _ENTRIES[entry](monkeypatch)
    assert _outcome(lambda: call(url)) == expected


@pytest.mark.parametrize(
    ("entry", "member"),
    [
        ("create_http_app metadata URL", "METADATA_URL_NOT_USABLE"),
        ("create_http_app JWKS URL", "JWKS_URL_NOT_USABLE"),
        ("AsyncJWKS", "JWKS_URL_NOT_USABLE"),
        ("CLI --oauth-jwks-url", "JWKS_URL_NOT_USABLE"),
    ],
)
def test_inner_whitespace_is_refused_at_startup_not_by_the_sanitiser(
    monkeypatch: pytest.MonkeyPatch, entry: str, member: str
) -> None:
    """The sanitiser keeps `key set.json`; the startup check refuses it,
    because a rejection could not carry it in `{url}` (#326)."""
    url = "https://auth.example.com/key set.json"
    assert _outcome(lambda: sanitize_public_auth_url(url)) is None
    call = _ENTRIES[entry](monkeypatch)
    assert _outcome(lambda: call(url)) == member


# --- 3. the README's example table is the code's ------------------------------

_README_REASONS = {
    "accepted": {None},
    "accepted (userinfo dropped)": {None},
    "refused: plain http": {PLAIN_HTTP},
    "refused: non-public host": {NOT_PUBLIC},
    "refused: not an absolute http(s) URL": {NOT_ABSOLUTE},
    "refused: invalid URL": {INVALID},
    "refused: whitespace": {"JWKS_URL_NOT_USABLE", "METADATA_URL_NOT_USABLE"},
}
_ROW = re.compile(r"^\| `(?P<url>[^`]+)` \| (?P<result>[^|]+?) \|$")


def _readme_rows() -> list[tuple[str, str]]:
    text = (_ROOT / "README.md").read_text()
    block = text.split("<!-- auth-url-rule:begin -->", 1)[1].split(
        "<!-- auth-url-rule:end -->", 1
    )[0]
    rows = []
    for line in block.strip().splitlines()[2:]:  # header and rule
        match = _ROW.match(line)
        assert match, f"unparsed README row: {line!r}"
        rows.append((match["url"], match["result"]))
    return rows


def test_the_readme_url_table_matches_the_code() -> None:
    rows = _readme_rows()
    assert rows
    for url, result in rows:
        assert result in _README_REASONS, f"unknown README result {result!r}"
        for kind, call in (
            ("jwks", lambda: check_auth_config(jwks_url=url)),
            ("metadata", lambda: check_auth_config(metadata_url=url)),
        ):
            got = _outcome(call)
            assert got in _README_REASONS[result], (url, kind, got)
        if result == "accepted (userinfo dropped)":
            assert "@" not in sanitize_public_auth_url(url)
    # every reason the README names is shown by at least one example
    assert {result for _, result in rows} == set(_README_REASONS)


def test_the_superseded_wording_is_gone() -> None:
    """The refused-list wording that read as if http to loopback were
    allowed, and the old message, appear nowhere current."""
    readme = (_ROOT / "README.md").read_text()
    changelog = (_ROOT / "CHANGELOG.md").read_text()
    source = (_ROOT / "src/pmcp/auth.py").read_text()
    assert "plain-http non-loopback" not in readme
    assert "plain http to a non-loopback" not in changelog
    assert 'only allows http:// URLs for loopback hosts")' not in source
````

## Embedding proof

The bodies above were taken **back out of this file**, not out of the
spike, and applied to a fresh `31c1357`:

```bash
# extract.py splits this file at "## Verbatim bodies" and writes each
# ```` block to 341-src.patch, 341-tests.patch, 341-docs.patch and
# test_auth_public_url_rule.py
python3 extract.py detailed-341-auth-url-docs-*.md extracted/
cmp extracted/<each> <the spike's own git diff / file>   # all four: identical
git switch -c proof/341 origin/main                       # 31c1357
git apply extracted/341-src.patch extracted/341-tests.patch extracted/341-docs.patch   # clean, no fuzz
cp extracted/test_auth_public_url_rule.py tests/
git diff spike/341 -- src tests README.md CHANGELOG.md     # tracked files: no difference
```

Measured on that proof tree (CPython 3.10.21):

| Step | Result |
|---|---|
| new module | 527 passed in 1.82 s |
| auth / transport / CLI / redactor suites (step 2) | 1525 passed, 55 deselected, 0 failed, 83 s |
| `ruff check src tests` | All checks passed |
| `ruff format --check src tests` | 175 files already formatted |
| `mypy src/pmcp/auth.py src/pmcp/cli.py src/pmcp/transport/http.py` | Success: no issues found in 3 source files |
| `scripts/check_security_claims.py` | OK, 129 cited node id(s) |
| mutation table | 18 mutants: 17 red, 1 equivalent (M13); tree byte-identical afterwards |
| full suite, run alone | 6050 passed, 3 skipped, 80 deselected, 0 failed, 518 s |
| red on main (module only, on `31c1357`) | 116 failed, 411 passed |

