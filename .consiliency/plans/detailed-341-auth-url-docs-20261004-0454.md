# Detailed plan: state the accepted auth-URL rule, classify an auth URL's host as the fetcher reads it, and a plain-http refusal that is true for every URL it refuses

> Written on main `31c1357` (dev0, a team host), worktree `pmcp-341`, branch
> `plan/341-auth-url-docs`, CPython 3.10.21. Every result below was measured
> on that tree, or on the spike of this plan applied to it. The spike lived
> on a local branch and was never pushed; this PR carries only this file. The
> spike's patches and its new test module are reproduced verbatim at the end
> (*Verbatim bodies*), and the *Embedding proof* section shows them extracted
> from this file, applied to a fresh `31c1357`, and passing.
>
> **Rev 2** (after the claude seat's review of Consiliency/pmcp#346): the
> rev-1 sweep had no **host-encoding axis**, and the rule it documented,
> "loopback refused", was false. Strict callers accepted hosts that are not
> plain ASCII. yarl/aiohttp and browsers NFKC- and IDNA-map such hosts to
> loopback or private addresses before connecting: `https://１２７.0.0.1/`,
> `127。0。0。1`, full-width `localhost` and full-width `169.254.169.254`.
> Measured here, a real aiohttp fetch of nine such spellings got `200` from a
> server bound to 127.0.0.1 (*The host as the fetcher reads it*). This was
> already true on main (F001, blocking). Rev 2 removes the degree of
> freedom: the sanitiser now **refuses any host not written in canonical
> ASCII** (Design decision 6). A differential test requires pmcp to agree
> with `yarl.URL(url).raw_host`, and with a WHATWG `new URL()`, on 287
> hosts generated from the host grammar. Also taken:
>
> - F002: a control character inside the URL is refused, not silently
>   deleted.
> - F003: trailing-dot and percent-encoded hosts are covered by F001's class.
> - F004: the `gateway.auth_connect` loopback set (IPv4-mapped included,
>   zone ids never) is now stated, and it no longer depends on the running
>   Python.
>
> Every number below was re-measured on the rev-2 spike and proof tree.

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

5. **(rev 2, F001, blocking)** Strict callers accept hosts that are not
   plain ASCII, which the HTTP client and a browser map to loopback or
   private addresses (full-width digits, `127。0。0。1`, full-width
   `localhost`). Classify the host as the fetcher will see it, either by
   canonicalising or by refusing non-canonical hosts. Derive the
   host-encoding axis from the grammar, and add a differential test against
   `yarl.URL(...).host`.
6. **(rev 2, F002)** Tab, CR and LF are stripped anywhere in a URL
   (`key\tset.json` is stored as `keyset.json`) and NUL passes. Refuse
   control characters, or state the behaviour exactly.
7. **(rev 2, F004)** The `gateway.auth_connect` loopback docstring omits the
   IPv4-mapped forms and zone ids.

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
| https, punycode IDN | `https://xn--bcher-kva.example/…` | accepted | accepted | - | - |
| https, IPv4-mapped public | `https://[::ffff:8.8.8.8]/…` | accepted | accepted | - | - |
| https, `*.localhost` | `https://app.localhost/…` | **accepted** (a name, not resolved) | accepted | - | see *Findings* |
| **host not in canonical ASCII (rev 2)** | `https://１２７.0.0.1/…`, `127。0。0。1`, full-width `localhost`, `bücher.example`, `localhost.`, `127.0.0.1.`, `127%2E0%2E0%2E1`, `2130706433`, `0177.0.0.1`, `134744072`, `example.123`, `[v1.fe]`, `[::1%25lo]`, `my_host…`, `a..b` | refused (**main accepted** every non-numeric one, and `134744072`) | refused | `PUBLIC_URL_HOST_NOT_CANONICAL` (new) | yes: none of them is an LDH name, a dotted quad or a zone-less IPv6 literal |
| **control character inside the URL (rev 2)** | `…/key\tset.json`, `\r`, `\n` in the host, NUL, DEL | refused (main stored `keyset.json`, passed NUL) | refused | `PUBLIC_URL_CONTROL_CHARACTER` (new) | yes |
| https, loopback IPv4 | `https://127.0.0.1/…`, `https://127.1.2.3/…` | refused | refused | `PUBLIC_URL_NOT_PUBLIC` | yes: non-public IP literal |
| https, loopback IPv6 | `https://[::1]/…` | refused | refused | `PUBLIC_URL_NOT_PUBLIC` | yes |
| https, loopback name | `https://localhost/…`, `https://LOCALHOST/…` | refused | refused | `PUBLIC_URL_NOT_PUBLIC` | yes: loopback name |
| https, private / link-local / unspecified | `https://10.0.0.5/…`, `https://169.254.169.254/…`, `https://0.0.0.0/…` | refused | refused | `PUBLIC_URL_NOT_PUBLIC` | yes |
| https, IPv4-mapped loopback | `https://[::ffff:127.0.0.1]/…` | refused | refused | `PUBLIC_URL_NOT_PUBLIC` | yes |
| **http, loopback IPv4** | `http://127.0.0.1:8080/…` | refused | **accepted** | `PUBLIC_URL_PLAIN_HTTP_REFUSED` (was `…HTTP_LOOPBACK_ONLY`) | yes now; **the old text was false** |
| **http, loopback IPv6** | `http://[::1]/…` | refused | **accepted** | same | yes now; old text false |
| **http, `localhost`** | `http://localhost/…` | refused | **accepted** | same | yes now; old text false |
| http, userinfo to loopback | `http://u:p@127.0.0.1/…` | refused | accepted, userinfo dropped | same | yes now; old text false |
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
| leading space or control / trailing tab, CR, LF | `" https://auth.example.com/x"`, `"…/x\n"`, `"…/x\r\n"` | accepted (stripped first) | accepted | - | - |
| trailing space | `"https://auth.example.com/x "` | sanitiser accepts; startup refuses (whitespace), as on main | accepted | `JWKS_URL_NOT_USABLE` / `METADATA_URL_NOT_USABLE` | yes |

**The accepted rule, stated positively (rev 2).** First, leading spaces and
C0 controls and a trailing tab, CR or LF are stripped. A strict caller then
accepts an auth URL if and only if all of these hold:

- it contains no other C0 control or DEL;
- it is an absolute `https://` URL;
- its port, if any, parses as 0 to 65535;
- its host is written in **canonical ASCII**: letters-digits-hyphens labels
  with no empty label (an IDN in `xn--` form), a dotted-quad IPv4 address
  with no leading zeros, or a bracketed IPv6 address without a zone id;
- that host is a public address, or a DNS name other than `localhost`.

For a JWKS or metadata URL, the startup check also refuses any whitespace.
DNS names are not resolved. Userinfo is accepted and dropped. Plain
`http://` is never accepted by a strict caller, loopback hosts included.

The operator's `gateway.auth_connect` URL also accepts `http://` to
`localhost` (any case), `127.0.0.0/8`, `[::1]` or `[::ffff:127.x.y.z]`. It
does not accept `[::127.0.0.1]` (IPv4-compatible), a legacy numeric form or
a zone id. The IPv4-mapped form is unwrapped explicitly in
`_is_loopback_host`, so the answer does not depend on how the running
Python's `IPv6Address.is_loopback` treats mapped addresses.

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
  `auth.example.com`, `8.8.8.8`, `10.0.0.5` or `app.localhost`. (Since
  rev 2, `localhost.` and `2130706433` are refused one step earlier as
  non-canonical.) The old text is true here, but the operator never sees it:
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

### The host as the fetcher reads it (rev 2)

**Who fetches each URL.**

- `AsyncJWKS` fetches the configured JWKS URL with aiohttp, whose URL parser
  is yarl. It fetches the URL **as configured** (`self._raw_url`), not the
  stored form.
- The protected-resource metadata URL is published, and clients fetch it,
  usually with a WHATWG URL parser.
- An elicitation URL is opened in the operator's browser, which is a WHATWG
  parser.
- `fetch_json_metadata` uses urllib, and since #211 it refuses before
  fetching anything that is not a verified public IP literal.

**The axis.** pmcp classifies `urlparse(url).hostname`, which keeps Unicode,
`%` escapes and a trailing dot as written. Any spelling that yarl or a
WHATWG parser maps to a *different* host is a way for the classifier and the
fetcher to disagree. From the two parsers' grammars:

- **NFKC.** IDNA's UTS-46 mapping normalises full-width, mathematical and
  circled characters and ideographic dots.
- **IDNA mapping.** It deletes soft hyphens and joiners, and lowercases.
- **Trailing dot.** A WHATWG parser reads `127.0.0.1.` as an IPv4 address.
- **Percent-decoding of the host.** A WHATWG parser decodes it; yarl does not.
- **IPv6 zone ids.**
- **Bracketed non-IPv6.** urllib unbrackets `[v1.fe]` to `v1.fe`.
- **IPv4-mapped and IPv4-compatible IPv6.**
- **Legacy numeric and octal IPv4.** `inet_aton` and a WHATWG parser read
  these as addresses.
- **WHATWG "ends in a number".** A host whose last label is numeric is an
  IPv4 address or a parse failure.

The table below was measured on the rev-2 spike (`scratchpad/341r2/table.py`):

- **yarl** is 1.22.0, under aiohttp 3.14.3.
- **aiohttp fetch** is a real `ClientSession.get` of `http://<host>:<port>/x`
  against an aiohttp server bound to 127.0.0.1. It was attempted only where
  yarl's host is loopback, and `-` means not attempted.
- **WHATWG** is node v24.20.0, `new URL()`.
- **main** is the sanitiser from `31c1357`, loaded alongside.

| Host class | Written | yarl `raw_host` (aiohttp) | aiohttp fetch, local 127.0.0.1 server | WHATWG `new URL().hostname` | main, strict (https) | rev 2, strict (https) | rev 2, operator (`http://`) |
|---|---|---|---|---|---|---|---|
| ASCII name | `auth.example.com` | `'auth.example.com'` | - | `auth.example.com` | accepted | accepted | refused PLAIN_HTTP_REFUSED |
| upper-case ASCII name | `AUTH.Example.COM` | `'auth.example.com'` | - | `auth.example.com` | accepted | accepted | refused PLAIN_HTTP_REFUSED |
| punycode IDN | `xn--bcher-kva.example` | `'xn--bcher-kva.example'` | - | `xn--bcher-kva.example` | accepted | accepted | refused PLAIN_HTTP_REFUSED |
| Unicode IDN | `bücher.example` | `'xn--bcher-kva.example'` | - | `xn--bcher-kva.example` | accepted | refused HOST_NOT_CANONICAL | refused HOST_NOT_CANONICAL |
| full-width digits | `１２７.0.0.1` | `'127.0.0.1'` | **200 from 127.0.0.1** | `127.0.0.1` | accepted | refused HOST_NOT_CANONICAL | refused HOST_NOT_CANONICAL |
| full-width digits and dots | `１２７．０．０．１` | `'127.0.0.1'` | **200 from 127.0.0.1** | `127.0.0.1` | accepted | refused HOST_NOT_CANONICAL | refused HOST_NOT_CANONICAL |
| ideographic full stop | `127。0。0。1` | `'127.0.0.1'` | **200 from 127.0.0.1** | `127.0.0.1` | accepted | refused HOST_NOT_CANONICAL | refused HOST_NOT_CANONICAL |
| half-width ideographic stop | `127｡0｡0｡1` | `'127.0.0.1'` | **200 from 127.0.0.1** | `127.0.0.1` | accepted | refused HOST_NOT_CANONICAL | refused HOST_NOT_CANONICAL |
| math-bold digits | `𝟏𝟐𝟕.0.0.1` | `'127.0.0.1'` | **200 from 127.0.0.1** | `127.0.0.1` | accepted | refused HOST_NOT_CANONICAL | refused HOST_NOT_CANONICAL |
| Arabic-Indic digits | `١٢٧.0.0.1` | `'xn--9hbcp.0.0.1'` | - | invalid URL | accepted | refused HOST_NOT_CANONICAL | refused HOST_NOT_CANONICAL |
| full-width localhost | `ｌｏｃａｌｈｏｓｔ` | `'localhost'` | **200 from 127.0.0.1** | `localhost` | accepted | refused HOST_NOT_CANONICAL | refused HOST_NOT_CANONICAL |
| full-width link-local | `１６９.２５４.１６９.２５４` | `'169.254.169.254'` | - | `169.254.169.254` | accepted | refused HOST_NOT_CANONICAL | refused HOST_NOT_CANONICAL |
| circled l (NFKC) | `ⓛocalhost` | `'localhost'` | **200 from 127.0.0.1** | `localhost` | accepted | refused HOST_NOT_CANONICAL | refused HOST_NOT_CANONICAL |
| soft hyphen (IDNA-ignored) | `local­host` | `'localhost'` | **200 from 127.0.0.1** | `localhost` | accepted | refused HOST_NOT_CANONICAL | refused HOST_NOT_CANONICAL |
| zero-width joiner | `local‍host` | `'localhost'` | **200 from 127.0.0.1** | invalid URL | accepted | refused HOST_NOT_CANONICAL | refused HOST_NOT_CANONICAL |
| trailing-dot IPv4 | `127.0.0.1.` | `'127.0.0.1.'` | - | `127.0.0.1` | accepted | refused HOST_NOT_CANONICAL | refused HOST_NOT_CANONICAL |
| trailing-dot name | `localhost.` | `'localhost.'` | - | `localhost.` | accepted | refused HOST_NOT_CANONICAL | refused HOST_NOT_CANONICAL |
| *.localhost | `app.localhost` | `'app.localhost'` | - | `app.localhost` | accepted | accepted | refused PLAIN_HTTP_REFUSED |
| percent-encoded IPv4 | `127%2E0%2E0%2E1` | `'127%2e0%2e0%2e1'` | - | `127.0.0.1` | accepted | refused HOST_NOT_CANONICAL | refused HOST_NOT_CANONICAL |
| percent-encoded name | `local%68ost` | `'local%68ost'` | - | `localhost` | accepted | refused HOST_NOT_CANONICAL | refused HOST_NOT_CANONICAL |
| legacy decimal | `2130706433` | `'2130706433'` | - | `127.0.0.1` | refused NOT_PUBLIC | refused HOST_NOT_CANONICAL | refused HOST_NOT_CANONICAL |
| legacy hex | `0x7f.1` | `'0x7f.1'` | - | `127.0.0.1` | refused NOT_PUBLIC | refused HOST_NOT_CANONICAL | refused HOST_NOT_CANONICAL |
| legacy octal | `0177.0.0.1` | `'0177.0.0.1'` | - | `127.0.0.1` | refused NOT_PUBLIC | refused HOST_NOT_CANONICAL | refused HOST_NOT_CANONICAL |
| leading-zero quad | `127.000.000.001` | `'127.000.000.001'` | - | `127.0.0.1` | refused NOT_PUBLIC | refused HOST_NOT_CANONICAL | refused HOST_NOT_CANONICAL |
| public legacy decimal | `134744072` | `'134744072'` | - | `8.8.8.8` | accepted | refused HOST_NOT_CANONICAL | refused HOST_NOT_CANONICAL |
| ends in a number | `example.123` | `'example.123'` | - | invalid URL | accepted | refused HOST_NOT_CANONICAL | refused HOST_NOT_CANONICAL |
| ends in hex | `example.0x7f` | `'example.0x7f'` | - | invalid URL | accepted | refused HOST_NOT_CANONICAL | refused HOST_NOT_CANONICAL |
| IPv6 loopback | `[::1]` | `'::1'` | - | `[::1]` | refused NOT_PUBLIC | refused NOT_PUBLIC | accepted |
| IPv6 loopback, uncompressed | `[0:0:0:0:0:0:0:1]` | `'::1'` | - | `[::1]` | refused NOT_PUBLIC | refused NOT_PUBLIC | accepted |
| IPv4-mapped loopback, dotted | `[::ffff:127.0.0.1]` | `'::ffff:127.0.0.1'` | **200 from 127.0.0.1** | `[::ffff:7f00:1]` | refused NOT_PUBLIC | refused NOT_PUBLIC | accepted |
| IPv4-mapped loopback, hex | `[::ffff:7f00:1]` | `'::ffff:127.0.0.1'` | **200 from 127.0.0.1** | `[::ffff:7f00:1]` | refused NOT_PUBLIC | refused NOT_PUBLIC | accepted |
| IPv4-mapped public | `[::ffff:8.8.8.8]` | `'::ffff:8.8.8.8'` | - | `[::ffff:808:808]` | accepted | accepted | refused PLAIN_HTTP_REFUSED |
| IPv4-compatible loopback | `[::127.0.0.1]` | `'::7f00:1'` | - | `[::7f00:1]` | refused NOT_PUBLIC | refused NOT_PUBLIC | refused PLAIN_HTTP_REFUSED |
| IPv6 zone id | `[fe80::1%25eth0]` | `'fe80::1%25eth0'` | - | invalid URL | refused NOT_PUBLIC | refused HOST_NOT_CANONICAL | refused HOST_NOT_CANONICAL |
| loopback with zone id | `[::1%25lo]` | `'::1%25lo'` | - | invalid URL | refused NOT_PUBLIC | refused HOST_NOT_CANONICAL | refused HOST_NOT_CANONICAL |
| public IPv6 with zone id | `[2606:4700:4700::1111%25eth0]` | `'2606:4700:4700::1111%25eth0'` | - | invalid URL | accepted | refused HOST_NOT_CANONICAL | refused HOST_NOT_CANONICAL |
| public IPv6 | `[2606:4700:4700::1111]` | `'2606:4700:4700::1111'` | - | `[2606:4700:4700::1111]` | accepted | accepted | refused PLAIN_HTTP_REFUSED |
| bracketed non-IPv6 | `[v1.fe]` | `'v1.fe'` | - | invalid URL | accepted | refused HOST_NOT_CANONICAL | refused HOST_NOT_CANONICAL |
| bracketed IPv4 | `[127.0.0.1]` | `ValueError` | - | invalid URL | refused INVALID | refused INVALID | refused INVALID |
| underscore | `my_host.example.com` | `'my_host.example.com'` | - | `my_host.example.com` | accepted | refused HOST_NOT_CANONICAL | refused HOST_NOT_CANONICAL |
| empty label | `a..example.com` | `'a..example.com'` | - | `a..example.com` | accepted | refused HOST_NOT_CANONICAL | refused HOST_NOT_CANONICAL |
| leading dot | `.example.com` | `'.example.com'` | - | `.example.com` | accepted | refused HOST_NOT_CANONICAL | refused HOST_NOT_CANONICAL |

What the table shows:

- **Main accepted every non-ASCII spelling.** yarl turns most of them into
  the address they spell, and aiohttp then **connected to 127.0.0.1**. That
  covers full-width digits, ideographic stops, math-bold digits, full-width
  `localhost`, a circled `l`, a soft hyphen and a zero-width joiner. The
  JWKS URL is exposed: `AsyncJWKS` fetches the raw form, so a configured
  JWKS URL of `https://１２７.0.0.1/jwks.json` sends the startup and
  per-request key fetches to the gateway host's own loopback.
- **A WHATWG parser also maps `%`-escaped hosts and a trailing dot.**
  `127%2E0%2E0%2E1`, `local%68ost` and `127.0.0.1.` all become loopback, so
  a published metadata URL or an elicitation URL using them sends a browser
  there. aiohttp refuses these spellings (`is not a canonical IPv4
  address`) or fails DNS on them.
- **`[v1.fe]` was accepted as the name `v1.fe`.** urllib unbrackets it,
  and a WHATWG parser rejects it.
- **A zone id on a public IPv6 address was accepted**:
  `[2606:4700:4700::1111%25eth0]`.
- **After rev 2, every one of these is refused** with
  `PUBLIC_URL_HOST_NOT_CANONICAL`. The accepted hosts are those yarl and
  WHATWG read as the same name or address that pmcp classified and stored:
  ASCII names, `xn--` IDNs, dotted quads and zone-less IPv6.

### Findings outside the issue's examples (not fixed here)

- **`*.localhost` is accepted as a public name** on the https path. Rev 2
  refuses `localhost.`, but only because a trailing dot is not canonical.
  RFC 6761 reserves `.localhost` for loopback, and
  `transport/http.py`'s own `_is_loopback_host` already treats a
  `.localhost` suffix as loopback, but `auth.py`'s does not. Fixing this
  changes behaviour in both directions. The https path would tighten.
  `gateway.auth_connect` would loosen: `http://app.localhost` would start to
  be accepted. That belongs in a security change of its own, not in a
  docs-and-message issue (Design decision 3). The new table pins today's
  behaviour, so a later fix must update these rows on purpose. Recommended
  follow-up issue: "auth: treat `*.localhost` as loopback names in
  `pmcp.auth`, consistently with `transport/http.py`".
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

### 3. The `*.localhost` and port-0 gaps are reported, not fixed

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

### 6. (rev 2) Refuse a non-canonical host; do not canonicalise it

The panel offered two shapes. One is to canonicalise the host as yarl does
(`yarl.URL(url).host`, or IDNA after NFKC) and classify the canonical form.
The other is to refuse any host that is not already canonical ASCII. This
plan **refuses**, for four reasons:

- **There are two fetchers, and they do not agree.** yarl decodes neither
  `%` escapes nor a trailing-dot IPv4 address, and a WHATWG parser decodes
  both (see the table). A WHATWG parser rejects `[v1.fe]`, a zone id, a
  zero-width joiner and Arabic-Indic digits, and yarl accepts them. Any one
  canonicaliser matches one fetcher and not the other. Refusing every
  spelling on which they could differ needs no choice between them.
- **It fails closed.** A new Unicode mapping in a future IDNA or UTS-46
  table cannot reopen this, because nothing non-ASCII is accepted.
- **Stored equals checked equals fetched.** The accepted host is already
  canonical, so the stored and published value (`redact_auth_url`) and the
  raw URL that `AsyncJWKS` fetches (`self._raw_url`) carry the same host
  that was classified. Nothing is rewritten, so nothing raw is stored.
- **It is simpler.** One predicate (`_is_canonical_host`) replaces an IDNA
  dependency and a choice of mapping table.

**What it costs legitimate operators.**

- A Unicode IDN must be written in its `xn--` form
  (`xn--bcher-kva.example`, not `bücher.example`). `xn--` labels stay
  accepted. They are ASCII, and yarl and WHATWG agree on them (both
  `raw_host` and `hostname` are `xn--bcher-kva.example`).
- Underscores are refused. Public CAs do not issue certificates for them,
  so an `https://` auth URL with one could not validate anyway.
- A trailing-dot FQDN, a legacy numeric address and a host ending in a
  number are refused. None has a use as a public auth endpoint.
- **One measured behaviour change on a public host:** `https://134744072/`
  (8.8.8.8 in legacy decimal) was accepted on main and is refused now.

**IPv6 is checked for validity, not for one text form.** `[0:…:1]`,
`[::1]` and `[::ffff:7f00:1]` are all accepted as IPv6 literals. Every
parser reads every RFC 4291 text form as the same 128-bit address. The
differential compares addresses, not strings, and it passes. Requiring one
text form would make acceptance depend on the running Python's
`IPv6Address.__str__`, which prints IPv4-mapped addresses differently
across versions. Zone ids are refused: no public address has one, and yarl
keeps `%25eth0` while WHATWG rejects it.

**The order of checks:** control character, then `INVALID`, then
`NOT_ABSOLUTE`, then `HOST_NOT_CANONICAL`, then `PLAIN_HTTP_REFUSED`, then
`NOT_PUBLIC`. So `http://１２７.0.0.1` gets the canonical refusal, which is
true for it, rather than the plain-http one.

### 7. (rev 2) Control characters: refuse them inside a URL, strip them only at the ends

`urlsplit` deletes tab, CR and LF anywhere in a URL (bpo-43882), so main
stored `https://h/key\tset.json` as `https://h/keyset.json`, and it passed
NUL. Rev 2 does two things:

- It keeps main's useful cleaning. A file-backed secret's trailing newline
  and a leading space still work, and the #326 test
  `test_a_value_the_store_cleans_is_checked_as_stored` still passes. So
  leading C0 controls and spaces (what WHATWG and `urlsplit` strip) and a
  trailing tab, CR or LF are stripped.
- It refuses any other C0 control or DEL with
  `PUBLIC_URL_CONTROL_CHARACTER`.

A trailing **space** is not stripped. It is still refused at startup as
whitespace, exactly as on main (#326's `_BAD_JWKS_URLS` row), so no
previously refused value starts.

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
- (rev 2) Two new registry members: `PUBLIC_URL_CONTROL_CHARACTER` ("Public
  auth URL contains a control character.") and
  `PUBLIC_URL_HOST_NOT_CANONICAL` ("Public auth URL host must be plain
  ASCII: a DNS name of letters, digits and hyphens (an IDN in its xn--
  form), a dotted-quad IPv4 address, or a bracketed IPv6 address without a
  zone."). Both pass the #326 sanitiser test unchanged.
- (rev 2) `_raw_host(netloc)` returns the host as written, with brackets and
  case kept, so `[v1.fe]` is seen. `_is_canonical_host(raw)` implements
  Design decision 6. `_C0_OR_SPACE`, `_TRAILING_NEWLINE`, `_LDH_LABEL` and
  `_WHATWG_NUMBER` are its constants.
- (rev 2) `sanitize_public_auth_url` strips, refuses control characters, and
  checks `_is_canonical_host(_raw_host(parsed.netloc))` after the
  absolute-URL check.
- (rev 2) `_is_loopback_host` unwraps an IPv4-mapped address itself (F004,
  version-independent), and the `sanitize_url_elicitation_url` docstring
  lists the operator's loopback set and states that zone ids are refused.

### `README.md` (modify)

- The JWKS sentence (around line 167) adds `localhost` to what is rejected
  and points to the accepted form.
- The startup-refusal paragraph is split (nit 3). The accepted rule is then
  stated positively, followed by the example table between markers.
- (rev 2) The rule names the canonical-ASCII host requirement and lists
  example spellings that are refused. It states the control-character
  behaviour exactly (F002) and the operator's loopback set (F004). The
  table gains six rows: one accepted `xn--` IDN and five refused with "host
  not in plain ASCII".

### `CHANGELOG.md` (modify)

- The #326 bullet's refused list is replaced by the accepted rule.
- A new `Fixed` bullet covers the message, the rename and the README table.
- (rev 2) A new `Fixed` bullet covers the canonical-host refusal and the
  control-character refusal. It names `134744072`, `example.123` and
  trailing-dot hosts as newly refused.

### `tests/test_auth_operator_messages.py`, `tests/test_transport_http.py`, `tests/test_auth.py` (modify)

- The `_STARTUP_REFUSALS` key and member move to the new name.
- (rev 2) `_STARTUP_REFUSALS` gains rows for the two new members. The
  registry count goes from 32 to 34, and the raise count from 39 to 41.
- (rev 2) `tests/test_auth.py`:
  - `test_public_auth_url_rejects_non_public_ip_literals` accepts
    `NOT_PUBLIC` or `HOST_NOT_CANONICAL`. Legacy numeric forms are now
    refused one step earlier, still for their host, and never accepted.
  - `test_public_auth_url_still_accepts_non_numeric_hosts_after_canonicalisation`
    moves `999.999.999.999`, `0xdeadbeefcafe` and `1.2.3.4.5` (hosts that
    end in a number) to refused.
  - `test_public_auth_host_does_not_read_python_int_quirks_as_addresses`
    still checks that `_legacy_numeric_addresses` reads none of `1_0`,
    `١٢٧` or `+2852039166` as an address, and now expects all three to be
    refused as non-canonical.
- `test_invalid_metadata_url_does_not_create_route_or_challenge_header`
  matched `"only allows http:// URLs"`; it now matches
  `r"^Plain http:// is not accepted"`.

### `tests/test_auth_public_url_rule.py` (create)

1134 tests, about 3.5 s:

1. `test_the_sanitiser_applies_the_rule[class]`: each of the 69 classes,
   strict and operator.
2. `test_each_refusal_is_true_of_its_input[class]`: each refusal's claim
   holds for its input, by an independent oracle.
3. `test_every_accepted_url_is_absolute_https[class]`: the accepted rule,
   seen from the other side.
4. `test_the_plain_http_member_is_reached_by_exactly_the_refused_plain_http[host, scheme, allow]`:
   15 canonical hosts x {http, https} x {strict, operator}. The member is
   raised if and only if the URL is plain http and the caller refuses it.
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
11. (rev 2) `test_pmcp_classifies_the_host_yarl_will_connect_to[host]`: the
    differential, over 287 hosts generated from the grammar:
    - addresses: {127.0.0.1, 169.254.169.254, 10.0.0.5, 0.0.0.0, 8.8.8.8}
      x 4 digit scripts x 5 dot forms x {no trailing dot, trailing dot},
      plus six legacy numeric forms each, plus IPv4-mapped, IPv4-compatible
      and bracketed forms;
    - names: {`localhost`, `auth.example.com`, `app.localhost`} x
      {upper-case, full-width, trailing dot, soft hyphen, zero-width joiner,
      `%`-escape, circled letter, leading dot, empty label};
    - IDNs, underscores, ends-in-a-number hosts, and IPv6 with zone ids,
      full-width colons and `[v1.fe]`.

    For `https` (strict) and `http` (operator), the test requires three
    things. If pmcp accepts, yarl parses the URL to the same host pmcp
    stored, and that host is public (strict) or loopback (operator). If
    yarl's host is non-public, pmcp refuses. The canonical refusal must
    agree with an independent oracle (`socket.inet_pton` and character
    sets). 165 of the 287 hosts map to a non-public host in yarl.
12. (rev 2) `test_pmcp_classifies_the_host_a_browser_will_open`: the same
    differential against node's WHATWG `new URL()`. It is skipped when
    `node` is not installed, and it ran here.
13. (rev 2) `test_the_panel_hosts_map_to_non_public_hosts_in_yarl_and_are_refused[host]`:
    F001's four hosts, as a tripwire on the premise. yarl maps each to a
    non-public host, and the sanitiser, `check_auth_config`,
    `create_http_app` and remote elicitation all refuse it as
    non-canonical.

The oracle `_claim_holds` gains `CONTROL` and `NOT_CANONICAL`, so
`test_each_refusal_is_true_of_its_input` checks the two new texts against
every class that reaches them.

## Documentation impact

- README: auth section (startup refusal, accepted rule with the
  canonical-ASCII host requirement and the control-character rule, a note
  that only the exact name `localhost` is loopback, example table).
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
# 1. the new module (spike: 1134 passed, 3.9 s)
.venv/bin/python -m pytest tests/test_auth_public_url_rule.py -q -p no:cacheprovider \
  --basetemp=/var/tmp/pmcp-341-bt-$USER/new --cov-fail-under=0
# 2. the suites that touch auth, the HTTP transport, the CLI and the redactor (spike: 2138 passed, 55 deselected, 0 failed, 85 s)
env -u npm_config_cache -u npm_config_store_dir .venv/bin/python -m pytest \
  tests/test_auth.py tests/test_transport_http.py tests/test_auth_origin_wiring.py \
  tests/test_redaction_additive.py tests/test_auth_operator_messages.py tests/test_auth_public_url_rule.py \
  tests/test_cli.py tests/test_server.py tests/test_http_transport.py tests/test_scoped_advisor_audit.py \
  -q -p no:cacheprovider --basetemp=/var/tmp/pmcp-341-bt-$USER/suites --cov-fail-under=0
# 3. CI gates (spike: all clean)
.venv/bin/ruff check src tests && .venv/bin/ruff format --check src tests
.venv/bin/mypy src/pmcp/auth.py src/pmcp/cli.py src/pmcp/transport/http.py
python3 scripts/check_security_claims.py          # expect OK, 129 cited node ids
# 4. the full suite, once, detached (memory on dev0 is shared) (spike: 6663 passed, 3 skipped, 80 deselected, 0 failed, 487 s)
env -u npm_config_cache -u npm_config_store_dir nohup .venv/bin/python -m pytest -q -p no:cacheprovider \
  --basetemp=/var/tmp/pmcp-341-bt-$USER/full > "$WORKTREE_ROOT/pmcp-341-full.log" 2>&1 &
```

**Red on main (rev 2).** On `31c1357` with only the new module added:
**606 failed, 528 passed**. By test:

| Test | Failed |
|---|---|
| `test_every_entry_point_applies_the_rule` | 311 |
| `test_pmcp_classifies_the_host_yarl_will_connect_to` | 230 |
| `test_the_sanitiser_applies_the_rule` | 35 |
| `test_the_plain_http_member_is_reached_by_exactly_the_refused_plain_http` | 21 |
| `test_the_panel_hosts_map_to_non_public_hosts_in_yarl_and_are_refused` | 4 |
| `test_pmcp_classifies_the_host_a_browser_will_open` | 1 |
| `test_the_plain_http_text_makes_no_loopback_claim` | 1 |
| `test_the_table_covers_every_member_the_sanitiser_raises` | 1 |
| `test_the_readme_url_table_matches_the_code` | 1 |
| `test_the_superseded_wording_is_gone` | 1 |

The two differentials fail on main for the F001 reason. Main accepts hosts
that yarl or WHATWG read as loopback or private, and hosts that they read as
a different host from the one stored. The *Embedding proof* section also
runs the rev-2 differential against rev 1's sanitiser: 235 failed. The
plain-http rows fail because main raises the old member, whose text is the
false loopback claim. The tests that pass on main are the rows whose
behaviour this plan does not change.

## Acceptance criteria

- [ ] `AuthMessage` has `PUBLIC_URL_PLAIN_HTTP_REFUSED` with the exact text
      above, and no `PUBLIC_URL_HTTP_LOOPBACK_ONLY`.
- [ ] For every class in the ground-truth table, every entry point gives the
      table's result (`test_every_entry_point_applies_the_rule`, 621 cases).
- [ ] Every refusal's text is true for every input that reaches it
      (`test_each_refusal_is_true_of_its_input`,
      `test_the_plain_http_member_is_reached_by_exactly_the_refused_plain_http`).
- [ ] The README states the accepted rule, separates the CLI/JWKS rule from
      the metadata URL, and its example table passes
      `test_the_readme_url_table_matches_the_code`.
- [ ] The CHANGELOG's #326 bullet states the accepted rule, and a #341
      bullet records the rewording and rename.
- [ ] `_is_absolute_http` is annotated `ParseResult`, and mypy is clean.
- [ ] (rev 2) Every host in the host-encoding table gets the rev-2 column's
      result, and both differentials pass: pmcp never accepts a host that
      yarl or a WHATWG parser reads as a non-public host, or as a different
      host from the one stored.
- [ ] (rev 2) A control character inside a URL is refused; leading
      spaces/controls and a trailing tab/CR/LF are still dropped.
- [ ] The #326 module `tests/test_auth_operator_messages.py` passes
      unchanged apart from the rename, including the sanitiser test.
- [ ] Every mutant in the table below is red, or listed as equivalent.
- [ ] Verification steps 1 to 4 pass.

## Mutation table

Measured on the proof tree (*Embedding proof*) with
`scratchpad/341r2/mutants.py`; an earlier run on the spike, without M18, gave
the same result for every other mutant. Each mutant is one
or more exact string edits to `auth.py`, `transport/http.py`, `cli.py`,
`README.md` or `CHANGELOG.md`. Each one runs
`tests/test_auth_public_url_rule.py tests/test_auth.py tests/test_transport_http.py
tests/test_auth_operator_messages.py` with `-o timeout=60` and a 300 s cap,
then restores every touched file from its saved copy in a `finally`. After
the run, every file was checked byte-identical by sha256.

**30 mutants: 27 red, 3 equivalent (M13, M21, M30).** M19 is checked by mypy, not pytest. M20 to M31 are rev 2's.

| # | Mutant | Red tests (measured) |
|---|---|---|
| M1 | old loopback text restored in the registry | 2 red -- `test_invalid_metadata_url_does_not_create_route_or_challenge_header`, `test_the_plain_http_text_makes_no_loopback_claim` |
| M2 | plain-http guard: `or` -> `and` (strict callers accept http) | 69 red -- `test_every_entry_point_applies_the_rule`, `test_pmcp_classifies_the_host_yarl_will_connect_to`, `test_the_plain_http_member_is_reached_by_exactly_the_refused_plain_http`, `test_the_readme_url_table_matches_the_code`, `test_the_sanitiser_applies_the_rule` |
| M3 | plain-http raise names PUBLIC_URL_NOT_PUBLIC | 102 red -- `test_every_builtin_message_site_is_driven`, `test_every_builtin_refusal_goes_through_the_renderer`, `test_every_entry_point_applies_the_rule`, `test_invalid_metadata_url_does_not_create_route_or_challenge_header`, `test_the_plain_http_member_is_reached_by_exactly_the_refused_plain_http`, `test_the_readme_url_table_matches_the_code`, `test_the_sanitiser_applies_the_rule`, `test_the_table_covers_every_member_the_sanitiser_raises` |
| M4 | non-public raise names PUBLIC_URL_PLAIN_HTTP_REFUSED | 140 red -- `test_an_ip_literal_jwks_url_keeps_its_specific_message_through_the_cli`, `test_every_builtin_message_site_is_driven`, `test_every_builtin_refusal_goes_through_the_renderer`, `test_every_entry_point_applies_the_rule`, `test_public_auth_url_error_message_does_not_claim_the_host_was_verified`, `test_public_auth_url_rejects_non_public_ip_literals`, `test_the_plain_http_member_is_reached_by_exactly_the_refused_plain_http`, `test_the_readme_url_table_matches_the_code`, `test_the_sanitiser_applies_the_rule`, `test_the_table_covers_every_member_the_sanitiser_raises` |
| M5 | not-absolute raise names PUBLIC_URL_INVALID | 79 red -- `test_cli_and_env_paths_refuse_at_startup`, `test_every_builtin_message_site_is_driven`, `test_every_builtin_refusal_goes_through_the_renderer`, `test_every_entry_point_applies_the_rule`, `test_startup_refuses_a_jwks_url_the_renderer_would_refuse`, `test_startup_refuses_a_metadata_url_the_renderer_would_refuse`, `test_the_readme_url_table_matches_the_code`, `test_the_sanitiser_applies_the_rule`, `test_the_table_covers_every_member_the_sanitiser_raises` |
| M6 | port no longer parsed in the sanitiser | 23 red -- `test_a_swapped_member_at_a_builtin_site_is_a_type_error_not_a_placeholder`, `test_every_builtin_refusal_goes_through_the_renderer`, `test_every_entry_point_applies_the_rule`, `test_the_readme_url_table_matches_the_code`, `test_the_sanitiser_applies_the_rule` |
| M7 | `localhost` no longer a non-public name | 25 red -- `test_every_entry_point_applies_the_rule`, `test_pmcp_classifies_the_host_a_browser_will_open`, `test_pmcp_classifies_the_host_yarl_will_connect_to`, `test_public_auth_url_rejects_non_public_ip_literals`, `test_the_readme_url_table_matches_the_code`, `test_the_sanitiser_applies_the_rule` |
| M8 | auth `_is_loopback_host` also takes *.localhost | 5 red -- `test_every_entry_point_applies_the_rule`, `test_pmcp_classifies_the_host_yarl_will_connect_to`, `test_the_plain_http_member_is_reached_by_exactly_the_refused_plain_http`, `test_the_sanitiser_applies_the_rule` |
| M9 | operator elicitation loses loopback http | 5 red -- `test_every_entry_point_applies_the_rule`, `test_sanitize_url_elicitation_url_allows_loopback_http_for_operator` |
| M10 | a new refusal in the sanitiser (port 0) without a table row | 12 red -- `test_every_entry_point_applies_the_rule`, `test_the_sanitiser_applies_the_rule`, `test_the_site_check_sees_every_site`, `test_the_table_covers_every_member_the_sanitiser_raises` |
| M11 | CLI skips the JWKS URL check | 62 red -- `test_an_ip_literal_jwks_url_keeps_its_specific_message_through_the_cli`, `test_cli_and_env_paths_refuse_at_startup`, `test_every_entry_point_applies_the_rule`, `test_inner_whitespace_is_refused_at_startup_not_by_the_sanitiser` |
| M12 | create_http_app skips the metadata URL check | 66 red -- `test_every_builtin_refusal_goes_through_the_renderer`, `test_every_entry_point_applies_the_rule`, `test_inner_whitespace_is_refused_at_startup_not_by_the_sanitiser`, `test_invalid_metadata_url_does_not_create_route_or_challenge_header`, `test_startup_refuses_a_metadata_url_the_renderer_would_refuse`, `test_the_panel_hosts_map_to_non_public_hosts_in_yarl_and_are_refused` |
| M13 | create_http_app skips sanitising the JWKS URL up front | **0 red -- equivalent** (see below) |
| M14 | README row flipped: http loopback shown as accepted | 1 red -- `test_the_readme_url_table_matches_the_code` |
| M15 | README drops the only `invalid URL` example | 1 red -- `test_the_readme_url_table_matches_the_code` |
| M16 | README restores the superseded refused-list wording | 1 red -- `test_the_superseded_wording_is_gone` |
| M17 | CHANGELOG restores the superseded wording | 1 red -- `test_the_superseded_wording_is_gone` |
| M18 | AsyncJWKS stores its URL unsanitised | 3 red -- `test_a_value_the_store_cleans_is_checked_as_stored` |
| M20 | rev 2: the canonical-host check removed | 459 red -- `test_every_builtin_refusal_goes_through_the_renderer`, `test_every_entry_point_applies_the_rule`, `test_pmcp_classifies_the_host_a_browser_will_open`, `test_pmcp_classifies_the_host_yarl_will_connect_to`, `test_public_auth_host_does_not_read_python_int_quirks_as_addresses`, `test_public_auth_url_still_accepts_non_numeric_hosts_after_canonicalisation`, `test_the_panel_hosts_map_to_non_public_hosts_in_yarl_and_are_refused`, `test_the_readme_url_table_matches_the_code`, `test_the_sanitiser_applies_the_rule` |
| M21 | rev 2: non-ASCII hosts allowed (isascii check dropped) | **0 red -- equivalent** (see below) |
| M22 | rev 2: IPv6 zone ids allowed | 23 red -- `test_every_entry_point_applies_the_rule`, `test_pmcp_classifies_the_host_a_browser_will_open`, `test_the_sanitiser_applies_the_rule` |
| M23 | rev 2: no WHATWG ends-in-a-number branch (legacy numeric read as names) | 80 red -- `test_every_entry_point_applies_the_rule`, `test_pmcp_classifies_the_host_a_browser_will_open`, `test_pmcp_classifies_the_host_yarl_will_connect_to`, `test_public_auth_url_still_accepts_non_numeric_hosts_after_canonicalisation`, `test_the_readme_url_table_matches_the_code`, `test_the_sanitiser_applies_the_rule` |
| M24 | rev 2: host checked unbracketed and lowercased (`parsed.hostname`) | 91 red -- `test_every_entry_point_applies_the_rule`, `test_pmcp_classifies_the_host_a_browser_will_open`, `test_pmcp_classifies_the_host_yarl_will_connect_to`, `test_s10_metadata_resource_fallback_keeps_ipv6_brackets`, `test_sanitize_url_elicitation_url_allows_loopback_http_for_operator`, `test_the_readme_url_table_matches_the_code`, `test_the_sanitiser_applies_the_rule` |
| M25 | rev 2: control characters inside the URL allowed | 51 red -- `test_every_builtin_refusal_goes_through_the_renderer`, `test_every_entry_point_applies_the_rule`, `test_the_sanitiser_applies_the_rule` |
| M26 | rev 2: trailing newline no longer stripped | 24 red -- `test_a_value_the_store_cleans_is_checked_as_stored`, `test_configs_that_start_on_main_still_start`, `test_every_entry_point_applies_the_rule`, `test_the_sanitiser_applies_the_rule` |
| M27 | rev 2: LDH labels allow underscore | 12 red -- `test_every_entry_point_applies_the_rule`, `test_pmcp_classifies_the_host_yarl_will_connect_to`, `test_public_auth_host_does_not_read_python_int_quirks_as_addresses`, `test_the_sanitiser_applies_the_rule` |
| M28 | rev 2: empty labels allowed (trailing dot passes) | 45 red -- `test_every_entry_point_applies_the_rule`, `test_pmcp_classifies_the_host_a_browser_will_open`, `test_pmcp_classifies_the_host_yarl_will_connect_to`, `test_the_readme_url_table_matches_the_code`, `test_the_sanitiser_applies_the_rule` |
| M29 | rev 2: IPv4 not required to be in canonical text (leading zeros) | 67 red -- `test_every_entry_point_applies_the_rule`, `test_pmcp_classifies_the_host_a_browser_will_open`, `test_pmcp_classifies_the_host_yarl_will_connect_to`, `test_the_readme_url_table_matches_the_code`, `test_the_sanitiser_applies_the_rule` |
| M30 | rev 2: operator loopback no longer unwraps IPv4-mapped | **0 red -- equivalent** (see below) |
| M31 | rev 2: README canonical row flipped to accepted | 1 red -- `test_the_readme_url_table_matches_the_code` |
| M19 | `_is_absolute_http(parsed: str)` or `(parsed: SplitResult)` | mypy: `arg-type` at both call sites (`auth.py:211`, `auth.py:714` on the rev-2 spike); with main's `Any`, mypy is silent |

M13 is **equivalent**. `AsyncJWKS.__init__`, constructed on the next line,
runs the same sanitiser on the same URL, so dropping the early call changes
no observable behaviour. M18 drops the sanitiser inside `AsyncJWKS` instead.
The new module cannot see it either, because `AsyncJWKS` then calls
`check_auth_config(jwks_url=…)`, which sanitises again and refuses the same
URLs with the same members. The #326 module's
`test_a_value_the_store_cleans_is_checked_as_stored` kills it, because the
stored `url` keeps its trailing newline.

M21 is **equivalent** too. `_is_canonical_host`'s leading `isascii()` is
defence in depth. Every later test is ASCII-only: `_LDH_LABEL` and
`_WHATWG_NUMBER` use explicit `[0-9A-Za-z]` ranges, not `\d`, and
`IPv4Address` and `IPv6Address` check ASCII digit sets. So no non-ASCII host
gets through without it. The 287-host differential confirms this: it
stayed green.

M30 is **equivalent on the measured interpreter**. CPython 3.10.21's
`IPv6Address.is_loopback` already treats `::ffff:127.0.0.1` as loopback, as
does every 3.10+ release that has the 2024 `ipaddress` IPv4-mapped fix. The
explicit unwrap pins that behaviour for interpreters that predate the fix,
and no supported test interpreter here can show the difference.

Every other mutant is red.

## Non-goals

- Changing what is accepted. No URL changes outcome. Only the text and name
  of one member change, plus docs and an annotation.
- `*.localhost` / port-0 handling (*Findings*, follow-up).
- Canonicalising (IDNA/NFKC) instead of refusing (Design decision 6).
- Resolving DNS names (Consiliency/pmcp#211, a deliberate non-goal there).
- The elicitation wrapper's own text (`ELICITATION_URL_INVALID`). It is true
  for every input and unchanged.

## Unverified

- **Python 3.11 / 3.12.** Run on 3.10 only. (Rev 2's `_is_loopback_host`
  removes one version dependency: see M30.) The oracle's
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
index 51b2d05..1d58e38 100644
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
@@ -106,12 +106,30 @@ class AuthMessage:
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
     )
+    # Consiliency/pmcp#341 rev 2: a host is classified only in the one form
+    # every fetcher reads the same way. yarl/aiohttp NFKC- and IDNA-map
+    # `１２７.0.0.1` and `127。0。0。1` to 127.0.0.1, and a WHATWG parser
+    # decodes `127%2E0%2E0%2E1`; pmcp saw names there. Anything else is
+    # refused, not rewritten.
+    PUBLIC_URL_CONTROL_CHARACTER = AuthText(
+        "Public auth URL contains a control character."
+    )
+    PUBLIC_URL_HOST_NOT_CANONICAL = AuthText(
+        "Public auth URL host must be plain ASCII: a DNS name of letters, "
+        "digits and hyphens (an IDN in its xn-- form), a dotted-quad IPv4 "
+        "address, or a bracketed IPv6 address without a zone."
+    )
     ELICITATION_URL_INVALID = AuthText("Invalid URL-mode elicitation URL.")
     # -- HTTP transport startup refusals --
     UNSUPPORTED_AUTH_MODE = AuthText("Unsupported auth mode.")
@@ -170,7 +188,7 @@ _MEMBER_FIELDS = {
 _SCOPE_LIST = re.compile(r"[\x21\x23-\x5B\x5D-\x7E]+(?: [\x21\x23-\x5B\x5D-\x7E]+)*")
 
 
-def _is_absolute_http(parsed: Any) -> bool:
+def _is_absolute_http(parsed: ParseResult) -> bool:
     """The absolute-HTTP(S) rule `sanitize_public_auth_url` applies to every
     configured auth URL: an http(s) scheme, a netloc and a hostname."""
     return (
@@ -397,12 +415,75 @@ def redact_auth_url(url: str) -> str:
 
 
 def _is_loopback_host(hostname: str) -> bool:
+    """`localhost`, an IPv4 address in 127.0.0.0/8, `::1`, or an IPv4-mapped
+    form of 127.0.0.0/8 (`::ffff:127.0.0.1`). The mapped form is unwrapped
+    here so the answer does not depend on the running Python's
+    `IPv6Address.is_loopback`. A zone id never gets this far: the host has
+    already passed `_is_canonical_host` (Consiliency/pmcp#341)."""
     if hostname.lower() == "localhost":
         return True
     try:
-        return ip_address(hostname).is_loopback
+        address = ip_address(hostname)
     except ValueError:
         return False
+    if isinstance(address, IPv6Address) and address.ipv4_mapped is not None:
+        return address.ipv4_mapped.is_loopback
+    return address.is_loopback
+
+
+# Consiliency/pmcp#341 rev 2: the one host form every fetcher reads alike.
+# What is stripped before a URL is checked: leading C0 controls and spaces
+# (as `urlsplit` and a WHATWG parser strip them) and a trailing tab, CR or
+# LF (a file-backed secret's newline, which `urlsplit` deletes anyway). Any
+# other C0 control or DEL is refused: `urlsplit` silently deletes tab, CR
+# and LF *inside* a URL (`key\tset.json` -> `keyset.json`) and passes NUL.
+# A trailing space is kept, as before, and refused at startup (#326).
+_C0_OR_SPACE = "".join(chr(code) for code in range(0x21))
+_TRAILING_NEWLINE = "\t\r\n"
+_LDH_LABEL = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?")
+# WHATWG URL "ends in a number": a last label that is all decimal digits or
+# `0x` hex makes the host an IPv4 address or a parse failure in a browser,
+# while `getaddrinfo` reads `2130706433` or `0x7f.1` as 127.0.0.1.
+_WHATWG_NUMBER = re.compile(r"[0-9]+|0[xX][0-9A-Fa-f]*")
+
+
+def _raw_host(netloc: str) -> str:
+    """The host exactly as written in `netloc`: no userinfo, no port, brackets
+    kept, case kept. `ParseResult.hostname` lowercases and unbrackets it,
+    which hides `[v1.fe]` and would let a check pass on a rewritten form."""
+    hostinfo = netloc.rpartition("@")[2]
+    if hostinfo.startswith("["):
+        end = hostinfo.find("]")
+        return hostinfo if end < 0 else hostinfo[: end + 1]
+    return hostinfo.partition(":")[0]
+
+
+def _is_canonical_host(raw: str) -> bool:
+    """True only for a host that yarl/aiohttp, `getaddrinfo` and a WHATWG
+    parser all read as the same name or address as `ipaddress` does: ASCII
+    letter-digit-hyphen labels with no empty label (so no trailing dot), a
+    strict dotted-quad IPv4 address, or a bracketed IPv6 address without a
+    zone id. Refused rather than rewritten: Unicode (NFKC/IDNA mapping turns
+    `１２７.0.0.1` into 127.0.0.1), `%` escapes, underscores, legacy numeric
+    and octal forms, and a bracketed host that is not IPv6 (`[v1.fe]`)."""
+    if not raw.isascii():
+        return False
+    if raw.startswith("["):
+        inner = raw[1:-1]
+        if not raw.endswith("]") or "%" in inner:
+            return False
+        try:
+            IPv6Address(inner)
+        except ValueError:
+            return False
+        return True
+    labels = raw.split(".")
+    if _WHATWG_NUMBER.fullmatch(labels[-1]):
+        try:
+            return str(IPv4Address(raw)) == raw
+        except ValueError:
+            return False
+    return all(_LDH_LABEL.fullmatch(label) for label in labels)
 
 
 # Every IPv6 format that carries an IPv4 address in its low 32 bits. The set is
@@ -601,7 +682,28 @@ UNVERIFIED_URL_CAVEAT = (
 
 
 def sanitize_public_auth_url(url: str, *, allow_loopback_http: bool = False) -> str:
-    """Validate and redact a public absolute auth metadata or elicitation URL."""
+    """Validate and redact a public absolute auth metadata or elicitation URL.
+
+    Accepted (Consiliency/pmcp#341): an absolute ``https://`` URL whose port,
+    if any, parses (0-65535) and whose host is written in canonical ASCII
+    (`_is_canonical_host`) and is a public IPv4/IPv6 address or a DNS name
+    other than ``localhost`` -- a name is not resolved. With
+    ``allow_loopback_http`` (only the operator's URL-mode elicitation path),
+    also ``http://`` to ``localhost`` (any case), ``127.0.0.0/8``, ``::1`` or
+    ``::ffff:127.0.0.0/104`` (`_is_loopback_host`). Leading and trailing
+    spaces and C0 controls, and a trailing tab, CR or LF, are stripped first. Userinfo and auth-bearing
+    query values are stripped from what is returned, so the stored host is
+    the one checked. Refused, in this order: a control character inside the
+    URL (`PUBLIC_URL_CONTROL_CHARACTER`), an unparseable port or host
+    (`PUBLIC_URL_INVALID`), no http(s) scheme or no host
+    (`PUBLIC_URL_NOT_ABSOLUTE`), a host not in canonical ASCII
+    (`PUBLIC_URL_HOST_NOT_CANONICAL`), plain ``http://`` the caller does not
+    allow (`PUBLIC_URL_PLAIN_HTTP_REFUSED`), and a non-public IP address or
+    ``localhost`` (`PUBLIC_URL_NOT_PUBLIC`).
+    """
+    url = url.lstrip(_C0_OR_SPACE).rstrip(_TRAILING_NEWLINE)
+    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in url):
+        raise ValueError(render_auth_message(AuthMessage.PUBLIC_URL_CONTROL_CHARACTER))
     try:
         parsed = urlparse(url)
         hostname = parsed.hostname
@@ -611,11 +713,13 @@ def sanitize_public_auth_url(url: str, *, allow_loopback_http: bool = False) ->
 
     if not _is_absolute_http(parsed) or not hostname:
         raise ValueError(render_auth_message(AuthMessage.PUBLIC_URL_NOT_ABSOLUTE))
+    if not _is_canonical_host(_raw_host(parsed.netloc)):
+        raise ValueError(render_auth_message(AuthMessage.PUBLIC_URL_HOST_NOT_CANONICAL))
 
     if parsed.scheme == "http" and (
         not allow_loopback_http or not _is_loopback_host(hostname)
     ):
-        raise ValueError(render_auth_message(AuthMessage.PUBLIC_URL_HTTP_LOOPBACK_ONLY))
+        raise ValueError(render_auth_message(AuthMessage.PUBLIC_URL_PLAIN_HTTP_REFUSED))
     if not (
         allow_loopback_http and parsed.scheme == "http" and _is_loopback_host(hostname)
     ):
@@ -1004,7 +1108,11 @@ def sanitize_url_elicitation_url(
     ``operator``
         The URL was typed by the operator into ``gateway.auth_connect``. Local
         OAuth redirects back to ``http://127.0.0.1``, and refusing that would
-        break the frozen local-consent flow, so loopback HTTP stays allowed.
+        break the frozen local-consent flow, so loopback HTTP stays allowed:
+        ``localhost``, ``127.0.0.0/8``, ``[::1]`` and the IPv4-mapped
+        ``[::ffff:127.x.y.z]``. Not ``[::127.0.0.1]`` (IPv4-compatible), not
+        a legacy numeric form, and never an IPv6 zone id (``[::1%25lo]``),
+        which is refused as a non-canonical host (Consiliency/pmcp#341).
 
     The default is the strict side deliberately: a call site this change misses
     should lose loopback, not silently keep it. Returns ``str`` -- the frozen
````

### Patch — `tests/test_auth.py`, `tests/test_auth_operator_messages.py`, `tests/test_transport_http.py`

````diff
diff --git a/tests/test_auth.py b/tests/test_auth.py
index e34a906..7a5e262 100644
--- a/tests/test_auth.py
+++ b/tests/test_auth.py
@@ -20,6 +20,7 @@ from pmcp.auth import (
     AuthMessage,
     ResourceServerAuthError,
     ResourceServerJWKSUnavailable,
+    _legacy_numeric_addresses,
     fetch_json_metadata,
     is_verified_public_auth_url,
     normalize_auth_metadata,
@@ -933,8 +934,13 @@ def test_public_auth_url_rejects_non_public_ip_literals(
     with pytest.raises(ValueError) as excinfo:
         sanitize_public_auth_url(_auth_url(host))
     # Guard against passing for the wrong reason -- a malformed URL raises the
-    # same exception type from a different branch.
-    assert "non-public IP literal" in str(excinfo.value), rationale
+    # same exception type from a different branch. Since Consiliency/pmcp#341
+    # rev 2 a legacy numeric form is refused one step earlier, as a host not
+    # written in canonical ASCII: still refused for its host, never accepted.
+    assert str(excinfo.value) in {
+        AuthMessage.PUBLIC_URL_NOT_PUBLIC,
+        AuthMessage.PUBLIC_URL_HOST_NOT_CANONICAL,
+    }, rationale
 
 
 def test_public_auth_url_error_message_does_not_claim_the_host_was_verified() -> None:
@@ -969,23 +975,35 @@ def test_public_auth_url_still_accepts_non_numeric_hosts_after_canonicalisation(
         "auth.example.com",
         "metadata.google.internal",
         "1.example.com",  # a numeric label, but the host is not a number
+    ]:
+        assert sanitize_public_auth_url(_auth_url(host)) == _auth_url(host)
+    # Consiliency/pmcp#341 rev 2: a host whose last label is a number is an
+    # IPv4 address or nothing to a WHATWG parser, so these are refused, not
+    # passed through as names.
+    for host in [
         "999.999.999.999",  # numeric-looking, but no valid reading exists
         "0xdeadbeefcafe",  # exceeds 32 bits, so it is not an address
         "1.2.3.4.5",  # too many parts for inet_aton
     ]:
-        assert sanitize_public_auth_url(_auth_url(host)) == _auth_url(host)
+        with pytest.raises(ValueError) as excinfo:
+            sanitize_public_auth_url(_auth_url(host))
+        assert str(excinfo.value) == AuthMessage.PUBLIC_URL_HOST_NOT_CANONICAL
 
 
 def test_public_auth_host_does_not_read_python_int_quirks_as_addresses() -> None:
     """`int()` accepts separators, signs, and non-ASCII digits; the parser must not.
 
     `int("1_0")` is 10 and `int("١٢٧")` is 127, so canonicalising with a bare
-    `int(part)` would turn these hostnames into addresses. No resolver reads them
-    that way, so they stay on the name path. The parser matches ASCII character
-    classes before converting, which is what keeps that true.
+    `int(part)` would turn these hostnames into addresses. The numeric parser
+    matches ASCII character classes before converting, which is what keeps
+    that true. Since Consiliency/pmcp#341 rev 2 none of them is a canonical
+    ASCII host either, so all three are refused before any classification.
     """
     for host in ["1_0", "١٢٧", "+2852039166"]:
-        assert sanitize_public_auth_url(_auth_url(host)) == _auth_url(host)
+        assert _legacy_numeric_addresses(host) == set()
+        with pytest.raises(ValueError) as excinfo:
+            sanitize_public_auth_url(_auth_url(host))
+        assert str(excinfo.value) == AuthMessage.PUBLIC_URL_HOST_NOT_CANONICAL
 
 
 def test_normalize_auth_metadata_omits_invalid_urls_with_safe_diagnostics() -> None:
diff --git a/tests/test_auth_operator_messages.py b/tests/test_auth_operator_messages.py
index 862d690..a7fd71b 100644
--- a/tests/test_auth_operator_messages.py
+++ b/tests/test_auth_operator_messages.py
@@ -113,7 +113,7 @@ def _rendered(member: AuthText) -> str:
 def test_the_registry_is_complete_and_typed() -> None:
     """The registry is the list now (no derivation to shrink): 29 members on
     the round-4 spike, each an `AuthText`, no two with the same text."""
-    assert len(_MESSAGES) == 32, sorted(_MESSAGES)
+    assert len(_MESSAGES) == 34, sorted(_MESSAGES)  # +2 in #341 rev 2
     assert len(set(_MESSAGES.values())) == len(_MESSAGES)
     assert all(type(v) is AuthText for v in _MESSAGES.values())
     assert _MESSAGES["KEY_CANNOT_VERIFY_TOKEN"] == (
@@ -850,7 +850,7 @@ def test_the_site_check_sees_every_site() -> None:
                 counts["raise"] += 1
             if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "_reject":
                 counts["_reject"] += 1
-    assert counts == {"raise": 39, "_reject": 7}, counts
+    assert counts == {"raise": 41, "_reject": 7}, counts  # +2 in #341 rev 2
 
 
 _STATIC_SHAPES = {
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
@@ -1703,6 +1703,16 @@ _STARTUP_REFUSALS: dict[str, tuple[Callable[[], Any], str, type[Exception]]] = {
         "PUBLIC_URL_NOT_PUBLIC",
         ValueError,
     ),
+    "PUBLIC_URL_CONTROL_CHARACTER": (
+        lambda: auth_mod.AsyncJWKS("https://issuer.example/key\x00set.json"),
+        "PUBLIC_URL_CONTROL_CHARACTER",
+        ValueError,
+    ),
+    "PUBLIC_URL_HOST_NOT_CANONICAL": (
+        lambda: auth_mod.AsyncJWKS("https://\uff11\uff12\uff17.0.0.1/jwks.json"),
+        "PUBLIC_URL_HOST_NOT_CANONICAL",
+        ValueError,
+    ),
     "ELICITATION_URL_INVALID": (
         lambda: auth_mod.sanitize_url_elicitation_url("ftp://x.example/"),
         "ELICITATION_URL_INVALID",
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
index 0ae8ba2..6d5b6a2 100644
--- a/CHANGELOG.md
+++ b/CHANGELOG.md
@@ -437,6 +437,32 @@ and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0
 
 
 ### Fixed
+- **An auth URL's host is checked as the HTTP client will read it.** A JWKS
+  URL, a protected-resource metadata URL or a URL-mode elicitation URL whose
+  host was not plain ASCII passed as a "name", but aiohttp (yarl) and
+  browsers rewrite such hosts before connecting: `https://１２７.0.0.1/`
+  (full-width digits), `127。0。0。1` and full-width `localhost` reached
+  127.0.0.1, and `127%2E0%2E0%2E1` decodes to it in a browser. PMCP now
+  refuses, rather than rewrites, any host that is not a letters-digits-
+  hyphens DNS name (IDNs in `xn--` form), a dotted-quad IPv4 address or a
+  bracketed IPv6 address without a zone id (`PUBLIC_URL_HOST_NOT_CANONICAL`).
+  This also refuses trailing-dot hosts (`localhost.`, `127.0.0.1.`), legacy
+  numeric and octal forms that were accepted when public (`134744072`),
+  hosts ending in a number (`example.123`), underscores, `[v1.fe]` and IPv6
+  zone ids. A control character inside the URL is refused
+  (`PUBLIC_URL_CONTROL_CHARACTER`) instead of being silently deleted
+  (`key<TAB>set.json` was stored as `keyset.json`) or passed (NUL); leading
+  spaces and controls and a trailing newline are still dropped. See
+  [Consiliency/pmcp#341](https://github.com/Consiliency/pmcp/issues/341).
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
@@ -455,8 +481,12 @@ and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0
   (a stray value in another mode is ignored, as before); and on the
   programmatic path, `create_http_app` refuses a protected-resource
   metadata URL that normalisation used to drop silently, omitting the
-  metadata route -- relative, non-http(s), plain http to a non-loopback
-  host, or a non-public IP literal. The metadata route also refuses to
+  metadata route -- any URL that is not an absolute `https://` URL to a
+  public IP address or a DNS name other than `localhost`, written in plain
+  ASCII, with a valid port
+  (so plain `http://` to any host, loopback included, is refused too; see
+  [Consiliency/pmcp#341](https://github.com/Consiliency/pmcp/issues/341)).
+  The metadata route also refuses to
   start rather than publish an empty `resource`, which no shipped
   configuration reaches. The README now documents deployments behind a
   prefix-stripping proxy; the metadata `404` there is a known follow-up,
diff --git a/README.md b/README.md
index cfdd1b2..10e5dd9 100644
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
@@ -204,17 +205,65 @@ that path to PMCP unchanged. An application that mounts PMCP under a
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
+An auth URL is accepted only if it is an absolute `https://` URL whose port,
+if it has one, is a number no greater than 65535, and whose host is written
+in plain ASCII -- a DNS name of letters, digits and hyphens (an
+internationalised name in its `xn--` form), a dotted-quad IPv4 address, or a
+bracketed IPv6 address without a zone id -- and is a public address or a
+name other than `localhost`. A host in any other spelling is refused rather
+than rewritten, because the HTTP client and a browser would rewrite it
+first: `https://１２７.0.0.1/` (full-width digits), `127。0。0。1`, full-width
+`localhost`, `127%2E0%2E0%2E1`, `127.0.0.1.` (trailing dot), legacy numeric
+and octal forms such as `2130706433` or `0177.0.0.1`, `[v1.fe]` and
+`[fe80::1%25eth0]` are all refused, as are `bücher.example` (write
+`xn--bcher-kva.example`), underscores and empty labels. PMCP drops leading
+spaces and control characters and a trailing tab, CR or LF; a control
+character anywhere else (a tab, CR, LF, NUL or DEL) is refused, and so is a
+space when the URL is a JWKS or metadata URL. A DNS name is not resolved
+(see below), and only the exact name `localhost` is treated as loopback:
+`*.localhost` is a name like any other and passes unresolved. Userinfo such
+as `user:pass@` is accepted and dropped. Everything else is refused,
+including plain `http://` to any host, loopback hosts too, and `https://`
+to `localhost` or a loopback address. (Plain `http://` to `localhost`,
+`127.0.0.0/8`, `[::1]` or `[::ffff:127.x.y.z]` is accepted in one place
+only: a URL the operator types into `gateway.auth_connect`.) For example,
+as a JWKS URL or a metadata URL:
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
+| `https://xn--bcher-kva.example/jwks.json` | accepted |
+| `https://１２７.0.0.1/jwks.json` | refused: host not in plain ASCII |
+| `https://127。0。0。1/jwks.json` | refused: host not in plain ASCII |
+| `https://127.0.0.1./jwks.json` | refused: host not in plain ASCII |
+| `https://2130706433/jwks.json` | refused: host not in plain ASCII |
+| `https://[v1.fe]/jwks.json` | refused: host not in plain ASCII |
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

Rev 2 (the #346 panel, F001): a host is also classified *as the fetcher
reads it*. yarl/aiohttp NFKC- and IDNA-map `１２７.0.0.1`, `127。0。0。1` and
full-width `localhost` to loopback, and a WHATWG parser decodes
`127%2E0%2E0%2E1`; pmcp used to see names there. The host-encoding axis --
Unicode digits and dots, IDNA mapping, trailing dots, `%` escapes, zone ids,
bracketed non-IPv6, IPv4-mapped, legacy numeric and octal -- is generated
from that grammar, and a differential test requires pmcp to agree with
`yarl.URL(url).raw_host` (and, where `node` is installed, with a WHATWG
`new URL()`) on every generated host.
"""

from __future__ import annotations

import ast
import asyncio
import contextlib
import io
import ipaddress
import re
import shutil
import socket
import string
import subprocess
import json
from pathlib import Path
from typing import Any, Callable
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import urlsplit

import pytest
import yarl

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
CONTROL = "PUBLIC_URL_CONTROL_CHARACTER"
NOT_CANONICAL = "PUBLIC_URL_HOST_NOT_CANONICAL"

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
    ("https punycode IDN", "https://xn--bcher-kva.example/jwks.json", None, None),
    ("https v4-mapped public", "https://[::ffff:8.8.8.8]/jwks.json", None, None),
    ("leading space", " https://auth.example.com/jwks.json", None, None),
    ("trailing newline", "https://auth.example.com/jwks.json\n", None, None),
    ("trailing CRLF", "https://auth.example.com/jwks.json\r\n", None, None),
    # Names are not resolved (Consiliency/pmcp#211): only the exact name
    # `localhost` counts as a loopback name. Pinned as it is today; see the
    # plan's follow-up.
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
    ("https v4-mapped", "https://[::ffff:127.0.0.1]/x", NOT_PUBLIC, NOT_PUBLIC),
    # -- plain http ------------------------------------------------------------
    ("http loopback IPv4", "http://127.0.0.1:8080/jwks.json", PLAIN_HTTP, None),
    ("http loopback IPv6", "http://[::1]/jwks.json", PLAIN_HTTP, None),
    ("http localhost", "http://localhost/jwks.json", PLAIN_HTTP, None),
    ("http userinfo loopback", "http://u:p@127.0.0.1/jwks.json", PLAIN_HTTP, None),
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
    # -- host not written in canonical ASCII (rev 2) ------------------------------
    (
        "fullwidth digits",
        "https://\uff11\uff12\uff17.0.0.1/x",
        NOT_CANONICAL,
        NOT_CANONICAL,
    ),
    (
        "ideographic stop",
        "https://127\u30020\u30020\u30021/x",
        NOT_CANONICAL,
        NOT_CANONICAL,
    ),
    (
        "fullwidth localhost",
        "http://\uff4c\uff4f\uff43\uff41\uff4c\uff48\uff4f\uff53\uff54/x",
        NOT_CANONICAL,
        NOT_CANONICAL,
    ),
    (
        "fullwidth link-local",
        "https://\uff11\uff16\uff19.\uff12\uff15\uff14.\uff11\uff16\uff19.\uff12\uff15\uff14/x",
        NOT_CANONICAL,
        NOT_CANONICAL,
    ),
    (
        "unicode IDN",
        "https://b\u00fccher.example/jwks.json",
        NOT_CANONICAL,
        NOT_CANONICAL,
    ),
    ("soft hyphen", "https://local\u00adhost/x", NOT_CANONICAL, NOT_CANONICAL),
    ("trailing-dot IPv4", "http://127.0.0.1./x", NOT_CANONICAL, NOT_CANONICAL),
    ("trailing-dot name", "https://localhost./jwks.json", NOT_CANONICAL, NOT_CANONICAL),
    ("percent-encoded IPv4", "https://127%2E0%2E0%2E1/x", NOT_CANONICAL, NOT_CANONICAL),
    ("percent-encoded name", "http://local%68ost/x", NOT_CANONICAL, NOT_CANONICAL),
    ("legacy decimal", "https://2130706433/jwks.json", NOT_CANONICAL, NOT_CANONICAL),
    (
        "legacy decimal, http",
        "http://2130706433/jwks.json",
        NOT_CANONICAL,
        NOT_CANONICAL,
    ),
    (
        "public legacy decimal",
        "https://134744072/jwks.json",
        NOT_CANONICAL,
        NOT_CANONICAL,
    ),
    ("legacy hex", "https://0x7f.1/jwks.json", NOT_CANONICAL, NOT_CANONICAL),
    ("legacy octal", "https://0177.0.0.1/jwks.json", NOT_CANONICAL, NOT_CANONICAL),
    ("leading-zero quad", "https://127.000.0.1/x", NOT_CANONICAL, NOT_CANONICAL),
    ("ends in a number", "https://example.123/jwks.json", NOT_CANONICAL, NOT_CANONICAL),
    ("IPv6 zone id", "http://[::1%25lo]/x", NOT_CANONICAL, NOT_CANONICAL),
    (
        "public IPv6 zone id",
        "https://[2606:4700::1111%25eth0]/x",
        NOT_CANONICAL,
        NOT_CANONICAL,
    ),
    ("bracketed non-IPv6", "https://[v1.fe]/jwks.json", NOT_CANONICAL, NOT_CANONICAL),
    ("underscore", "https://my_host.example.com/x", NOT_CANONICAL, NOT_CANONICAL),
    ("empty label", "https://a..example.com/x", NOT_CANONICAL, NOT_CANONICAL),
    # -- a control character inside the URL (rev 2, F002) ------------------------
    ("inner tab", "https://auth.example.com/key\tset.json", CONTROL, CONTROL),
    ("inner CR", "https://auth.example.com/key\rset.json", CONTROL, CONTROL),
    ("inner LF in host", "https://auth.exam\nple.com/x", CONTROL, CONTROL),
    ("NUL", "https://auth.example.com/key\x00set.json", CONTROL, CONTROL),
    ("DEL", "https://auth.example.com/key\x7fset.json", CONTROL, CONTROL),
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
    literal that is loopback once an IPv4-mapped form is unwrapped (not a
    legacy numeric form)."""
    if host.lower() == "localhost":
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        return address.ipv4_mapped.is_loopback
    return address.is_loopback


_LDH = set(string.ascii_letters + string.digits + "-")


def _written_host(url: str) -> str:
    """The host as written: after `//`, before the path, without userinfo or
    port, brackets kept. Written independently of `auth._raw_host`."""
    after = url.split("//", 1)[1]
    for stop in "/?#":
        after = after.split(stop, 1)[0]
    after = after.rsplit("@", 1)[-1]
    if after.startswith("["):
        return after[: after.index("]") + 1] if "]" in after else after
    return after.split(":", 1)[0]


def _canonical_by_oracle(raw: str) -> bool:
    """The canonical-ASCII rule, by `socket.inet_pton` and character sets
    rather than by `ipaddress` or pmcp's regexes."""
    if not raw.isascii():
        return False
    if raw.startswith("["):
        if not raw.endswith("]") or "%" in raw:
            return False
        try:
            socket.inet_pton(socket.AF_INET6, raw[1:-1])
        except OSError:
            return False
        return True
    labels = raw.split(".")
    last = labels[-1].lower()
    if last.isdigit() or (
        last.startswith("0x") and set(last[2:]) <= set(string.hexdigits)
    ):
        try:
            socket.inet_pton(socket.AF_INET, raw)
        except OSError:
            return False
        return all(str(int(label)) == label for label in labels)
    return all(
        0 < len(label) <= 63
        and set(label) <= _LDH
        and not label.startswith("-")
        and not label.endswith("-")
        for label in labels
    )


def _claim_holds(member: str, url: str) -> bool:
    """True if the refusal `member` says something true about `url`."""
    stripped = url.lstrip("".join(map(chr, range(0x21)))).rstrip("\t\r\n")
    has_control = any(ord(c) < 0x20 or ord(c) == 0x7F for c in stripped)
    if member == CONTROL:  # "contains a control character."
        return has_control
    if has_control:
        return False
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
    if member == NOT_CANONICAL:  # "host must be plain ASCII: ..."
        return bool(host) and not _canonical_by_oracle(_written_host(stripped))
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
    assert _canonical_by_oracle(_written_host(url.strip()))
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
    "[::ffff:7f00:1]",
    "localhost",
    "LOCALHOST",
    "app.localhost",
    "10.0.0.5",
    "169.254.169.254",
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
    unless the caller allows loopback and the host is loopback. (These hosts
    are all canonical; a non-canonical host is refused one step earlier, see
    the host-encoding tests.)"""
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
    "refused: host not in plain ASCII": {NOT_CANONICAL},
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


# --- 4. rev 2: the host as the fetcher reads it --------------------------------
#
# Generated from the host grammar, not from the panel's examples: each axis is
# a way a fetcher's parser can read a different host than the one written.

_DIGIT_SCRIPTS = {
    "ascii": "0123456789",
    "fullwidth": "０１２３４５６７８９",
    "math-bold": "".join(chr(0x1D7CE + i) for i in range(10)),
    "arabic-indic": "".join(chr(0x0660 + i) for i in range(10)),
}
_DOTS = {
    "full stop": ".",
    "ideographic": "。",
    "fullwidth": "．",
    "halfwidth ideographic": "｡",
    "percent": "%2E",
}
_ADDRESSES = ["127.0.0.1", "169.254.169.254", "10.0.0.5", "0.0.0.0", "8.8.8.8"]


def _ipv4_forms(address: str) -> list[str]:
    value = int(ipaddress.IPv4Address(address))
    octets = address.split(".")
    return [
        str(value),  # legacy decimal
        hex(value),  # legacy hex
        ".".join(f"0{int(o):o}" for o in octets),  # dotted octal
        ".".join(hex(int(o)) for o in octets),  # dotted hex
        ".".join(f"{int(o):03d}" for o in octets),  # leading zeros
        f"{octets[0]}.{octets[1]}.{int(octets[2]) * 256 + int(octets[3])}",  # 3-part
    ]


def _generated_hosts() -> list[str]:
    hosts: list[str] = []
    for address in _ADDRESSES:
        for digits in _DIGIT_SCRIPTS.values():
            table = str.maketrans("0123456789", digits)
            for dot in _DOTS.values():
                for suffix in ("", "."):
                    hosts.append(address.translate(table).replace(".", dot) + suffix)
        hosts.extend(_ipv4_forms(address))
        hosts.append(f"[::ffff:{address}]")
        hosts.append(f"[::{address}]")
        hosts.append(f"[{address}]")
    fullwidth = str.maketrans(
        string.ascii_lowercase, "".join(chr(0xFF41 + i) for i in range(26))
    )
    for name in ("localhost", "auth.example.com", "app.localhost"):
        hosts += [
            name,
            name.upper(),
            name.translate(fullwidth),
            name + ".",
            name.replace("o", "­o", 1),  # soft hyphen (IDNA: ignored)
            name.replace("o", "‍o", 1),  # zero-width joiner
            name.replace("l", "%6C", 1),
            name.replace("l", "ⓛ", 1),  # circled l (NFKC: l)
            "." + name,
            name.replace(".", "..", 1),
        ]
    hosts += [
        "xn--bcher-kva.example",
        "bücher.example",
        "my_host.example.com",
        "example.123",
        "example.0x7f",
        "[::1]",
        "[0:0:0:0:0:0:0:1]",
        "[::ffff:7f00:1]",
        "[::1%25lo]",
        "[fe80::1%25eth0]",
        "[2606:4700:4700::1111]",
        "[2606:4700:4700::1111%25eth0]",
        "[v1.fe]",
        "[：：1]",  # fullwidth colons
    ]
    return list(dict.fromkeys(hosts))


_GENERATED = _generated_hosts()


def _is_public(host: str) -> bool:
    """Whether a host a fetcher will connect to is public: `localhost` is
    not, an IP literal (legacy numeric via `inet_aton`, IPv4-mapped
    unwrapped) is public only if global and not multicast, and any other
    name is public by assumption (names are not resolved, #211)."""
    host = host.strip("[]")
    if host.lower().rstrip(".") == "localhost":
        return False
    address = _literal(host.split("%", 1)[0].rstrip("."))
    return address is None or (address.is_global and not address.is_multicast)


def _same_host(stored: str, fetched: str) -> bool:
    try:
        return ipaddress.ip_address(stored.strip("[]")) == ipaddress.ip_address(
            fetched.strip("[]")
        )
    except ValueError:
        return stored.lower() == fetched.lower()


def _stored_host(url: str) -> str:
    return urlsplit(sanitize_public_auth_url(url)).hostname or ""


def _yarl_host(url: str) -> str | None:
    try:
        return yarl.URL(url).raw_host
    except ValueError:
        return None


@pytest.mark.parametrize("host", _GENERATED)
def test_pmcp_classifies_the_host_yarl_will_connect_to(host: str) -> None:
    """The differential: whatever pmcp accepts, yarl (aiohttp's URL parser,
    which `AsyncJWKS` fetches with) reads as the same host, and that host
    is public; whatever yarl reads as a non-public host, pmcp refuses."""
    for scheme, allow in (("https", False), ("http", True)):
        url = f"{scheme}://{host}/jwks.json"
        got = _outcome(lambda: sanitize_public_auth_url(url, allow_loopback_http=allow))
        fetched = _yarl_host(url)
        if got is None:
            assert fetched is not None, url
            stored = urlsplit(
                sanitize_public_auth_url(url, allow_loopback_http=allow)
            ).hostname
            assert stored and _same_host(stored, fetched), (url, stored, fetched)
            if scheme == "https":
                assert _is_public(fetched), (url, fetched)
            else:
                assert _is_loopback(fetched), (url, fetched)
        if fetched is not None and not _is_public(fetched) and scheme == "https":
            assert got is not None, (url, fetched)
        if got is None or got == NOT_CANONICAL:
            assert (got is None) == _canonical_by_oracle(_written_host(url)), url


_NODE = shutil.which("node")


@pytest.mark.skipif(_NODE is None, reason="node is not installed")
def test_pmcp_classifies_the_host_a_browser_will_open() -> None:
    """The same differential against a WHATWG URL parser (`new URL()` in
    node), which is what opens an elicitation URL and what most clients use
    on a published metadata URL."""
    urls = [f"https://{host}/x" for host in _GENERATED]
    assert _NODE is not None
    out = subprocess.run(
        [
            _NODE,
            "-e",
            "const u=JSON.parse(require('fs').readFileSync(0,'utf8'));"
            "console.log(JSON.stringify(u.map(x=>{try{return new URL(x).hostname}"
            "catch(e){return null}})))",
        ],
        input=json.dumps(urls),
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    ).stdout
    for url, browser in zip(urls, json.loads(out)):
        got = _outcome(lambda: sanitize_public_auth_url(url))
        if got is None:
            assert browser is not None, url
            assert _same_host(_stored_host(url), browser), (url, browser)
            assert _is_public(browser), (url, browser)
        if browser is not None and not _is_public(browser):
            assert got is not None, (url, browser)


@pytest.mark.parametrize(
    "host",
    [
        "１２７.0.0.1",
        "127。0。0。1",
        "ｌｏｃａｌｈｏｓｔ",
        "１６９.２５４.１６９.２５４",
    ],
)
def test_the_panel_hosts_map_to_non_public_hosts_in_yarl_and_are_refused(
    host: str,
) -> None:
    """F001's four hosts, as a tripwire on the premise: yarl does map them
    to a loopback or link-local host, and every strict entry point refuses
    them as non-canonical."""
    url = f"https://{host}/jwks.json"
    fetched = _yarl_host(url)
    assert fetched is not None and not _is_public(fetched)
    assert _outcome(lambda: sanitize_public_auth_url(url)) == NOT_CANONICAL
    assert _outcome(lambda: check_auth_config(jwks_url=url)) == NOT_CANONICAL
    assert _outcome(lambda: _metadata_app(url)) == NOT_CANONICAL
    assert _outcome(lambda: _elicitation("remote")(url)) == NOT_CANONICAL
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
git switch -c proof3/341 origin/main                      # 31c1357
git apply extracted/341-src.patch extracted/341-tests.patch extracted/341-docs.patch   # clean, no fuzz
cp extracted/test_auth_public_url_rule.py tests/
git diff spike2/341 -- src tests README.md CHANGELOG.md   # tracked files: no difference
```

Measured on that proof tree (CPython 3.10.21, yarl 1.22.0, aiohttp 3.14.3,
node v24.20.0):

| Step | Result |
|---|---|
| new module | 1134 passed, 0 skipped (the WHATWG differential ran) |
| auth / transport / CLI / redactor suites (step 2) | 2138 passed, 55 deselected, 0 failed, 85 s |
| `ruff check src tests` | All checks passed |
| `ruff format --check src tests` | 175 files already formatted |
| `mypy src/pmcp/auth.py src/pmcp/cli.py src/pmcp/transport/http.py` | Success: no issues found in 3 source files |
| `scripts/check_security_claims.py` | OK, 129 cited node id(s) |
| mutation table | 30 mutants: 27 red, 3 equivalent (M13, M21, M30); tree byte-identical afterwards; identical to the spike run |
| full suite, run alone, `env -u npm_config_cache -u npm_config_store_dir` | 6663 passed, 3 skipped, 80 deselected, 0 failed, 487 s |
| red on main (module only, on `31c1357`) | 606 failed, 528 passed |
| the rev-2 differentials against rev 1's `auth.py` (`-k "yarl or browser"`) | 235 failed, 57 passed |
| a live aiohttp fetch of the F001 spellings (main's accept set) | `200` from a server bound to 127.0.0.1 for 9 hosts main accepted (see the host-encoding table) |

