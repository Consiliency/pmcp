# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| 2.0.x   | ✅ Active  |
| 1.22.x  | ✅ Security fixes only |
| < 1.22  | ❌ No longer supported |

2.0.0 is a breaking release: `GET /mcp` is retired (405) and the
`PMCP_KEEPALIVE_MAX_SECONDS` lifetime cap is removed with no replacement by
design. See the CHANGELOG before upgrading. 1.22.x receives security fixes
only; it is the last 1.x line and is pinned to `mcp` 1.x, which no longer
receives upstream releases.

## Threat Model

PMCP is a local-first MCP gateway. Its default security posture assumes:

- **Bind address**: `127.0.0.1` (loopback only). The HTTP port is not exposed to external
  networks unless you explicitly bind to `0.0.0.0` or place it behind a reverse proxy.
- **Trust boundary**: processes running on the same host as PMCP are trusted. Remote clients
  (via reverse proxy) are untrusted and must present a valid Bearer token when
  `shared-secret` or `resource-server` auth is configured.
- **TLS**: PMCP does not terminate TLS. For any network exposure, terminate TLS at a reverse
  proxy (nginx, Caddy) and proxy to `127.0.0.1:3344`. See the README for example configs.

### What PMCP protects against (when correctly configured)

- Unauthenticated tool invocations via Bearer token guard on `/mcp`
- AS-issued access tokens in `resource-server` mode via JWKS signature,
  issuer, expiry, not-before, and audience validation. The audience is bound to
  the operator-configured canonical resource URI (`resource_server_audience`,
  per RFC 8707) and is never derived from the request Host header. Signatures
  are only accepted for the operator-configured algorithm allowlist (default
  `RS256`/`ES256`); the token's own `alg` header is never trusted. The mode
  fails closed at startup without an issuer, JWKS URL, and audience, and the
  JWKS URL must be `https` and is rejected when its host is a non-public IP
  literal (see the DNS-name limitation below). JWKS is fetched asynchronously and
  cached so validation never blocks the event loop; an unreachable JWKS endpoint
  returns `503` while an invalid or wrong-audience token returns `401`.
- Timing oracle attacks on token comparison (`hmac.compare_digest`)
- Request floods via per-source-IP sliding-window rate limiting (`--rate-limit`
  / `PMCP_RATE_LIMIT`) on `/mcp`
- Oversized payloads causing OOM (`Content-Length > 10 MB → 413`)
- Hanging downstream tools consuming connections indefinitely (60 s request
  timeout, `--request-timeout` / `PMCP_REQUEST_TIMEOUT`) — applies to every
  `/mcp` POST **except** `subscriptions/listen`, which is a long-lived stream
  by design; see "Known limitations" below for what bounds it instead.
- Unbounded long-lived streams on `/mcp` (`PMCP_MAX_LISTEN_STREAMS`, default
  64, bounds concurrent `subscriptions/listen` subscriptions; retired GET's
  pre-session keep-alive concurrency cap is this guard's one-for-one
  predecessor)
- Multiple gateway instances fighting over resources (fcntl singleton lock)
- Reconnect storms from crashing downstream servers (per-server reconnect flag)
- **Unilateral credential relaxation**: a manifest server's `requires_api_key`
  can only be relaxed by a variable the entry itself names in
  `api_key_optional_when` — an operator's overlay can supply that variable's
  value, but cannot make a server's credential optional unless the manifest
  entry already declared it relaxable. A server also cannot name its own
  credential as its relaxer. Every unset, malformed, self-referencing, or
  placeholder (`${VAR}`) relaxer value fails closed and the credential stays
  required (Consiliency/pmcp#114).
- **Mutable CI dependencies**: every GitHub Action this repository runs — in
  `.github/workflows/` and in the local composite action under
  `.github/actions/` — is pinned to a full commit SHA with the release named in
  a trailing comment (`owner/action@<sha> # vX.Y.Z`). A tag or branch is
  mutable: whoever controls the action's repository controls what runs with
  the job's permissions, and the release workflow holds `id-token: write` for
  PyPI trusted publishing. `scripts/check_workflows.py` fails CI on any remote
  `uses:` that is not in that form, and on any change to the release
  workflow's exact action set; Dependabot maintains the pins, including a
  dedicated entry for the composite action's directory
  ([#217](https://github.com/Consiliency/pmcp/issues/217)).

### Known limitations

- **The auth-URL host check filters IP literals only**: a public auth metadata
  or JWKS URL is rejected when its host is a non-public IP literal — private,
  CGNAT, link-local, loopback, multicast, site-local or unspecified, including
  IPv4 addresses embedded in IPv6 literals (RFC 4291 mapped and compatible,
  RFC 6052 NAT64, RFC 3056 6to4, RFC 4380 Teredo, RFC 5214 ISATAP) and legacy
  numeric forms such as `2852039166` or `0177.0.0.1`. **A DNS name is accepted
  without being resolved**, so a name pointing at an internal address is not
  caught. PMCP does not resolve names deliberately: a lookup is
  TOCTOU-vulnerable and is not SSRF defence without connection-time IP pinning.
  What it does instead is stop claiming otherwise — **a server-supplied URL is
  relayed unverified and presented as such**, via
  `UrlElicitationInfo.url_verified`, `AuthMetadataInfo.verified_urls` (one entry
  per URL field), and `AuthChallengeInfo.resource_metadata_url_verified`, all
  defaulting to unverified, with the caveat carried in the `next_step` string an
  agent follows and in `pmcp auth` output. Paths where PMCP fetches the URL
  itself fail closed and require a verified public literal, and a URL from a
  downstream server's payload may not be loopback `http://`
  ([#211](https://github.com/Consiliency/pmcp/issues/211)).
- **No mTLS**: clients are not authenticated by certificate; only Bearer token.
- **No per-tool ACL on HTTP**: any valid token can invoke any tool. Tool-level policy is
  enforced at the MCP layer, not the HTTP layer.
- **Rate-limit source IPs may be shared**: localhost clients usually share the
  same observed source IP, and reverse-proxied clients may share one bucket
  unless the proxy preserves distinct client IPs for PMCP.
- **`/health` and `/metrics` are unauthenticated by design**: load balancers and Prometheus
  scrapers typically cannot present Bearer tokens. Bearer auth for `/mcp` does
  not protect these endpoints. Do not expose them on a public interface without
  separate network-layer control (firewall rule, IP allowlist, or reverse-proxy
  policy).
- **Resource Server, not Authorization Server**: PMCP can validate
  Authorization Server issued access tokens in `resource-server` mode, but it
  does not provide an Authorization Server, DCR, SSO, RBAC, billing, or a
  complete multi-tenant identity service.
- **Authorization discovery is diagnostic**: PMCP can surface protected-resource,
  authorization-server, OIDC discovery, Client ID Metadata Document, scope, and
  URL-mode elicitation hints, but it does not store third-party OAuth refresh
  tokens.
- **URL-mode elicitation is out of band**: never paste OAuth codes, third-party
  passwords, or provider refresh tokens into gateway tools. `gateway.auth_connect`
  accepts API-key credentials only for local env-store flows; URL-mode flows only
  accept an elicitation identifier and consent acknowledgement.
- **Redaction is best-effort defense in depth**: PMCP redacts bearer tokens, API
  keys, bare provider tokens (`sk-`, `ghp_`, `github_pat_`), common secrets, URL
  userinfo, authorization codes, and auth-bearing query parameters from gateway
  outputs, status/doctor diagnostics, feedback payloads, and HTTP diagnostics.
  Redaction is applied across every task-emitting surface — `gateway.invoke`,
  `gateway.tasks_result`, `gateway.tasks_list`, and `gateway.tasks_get`, including
  task `status_message` and raw fields — and truncation summaries are built from
  post-redaction text. Treat all logs as operational data and avoid adding
  secrets to server names, tool names, or free-form descriptions.
- **Credential isolation is scoped, not identity-complete**: user-scope env-store files are owned by
  the local OS account, project-scope env-store files are owned by the project
  directory, and remote header placeholders resolve from those stores plus
  process environment in non-tenant mode. Tenant remote-header mode reads
  tenant-scoped files derived from the resolved project root and must not read
  another tenant's file, but PMCP still does not provide cross-user identity or
  authorization isolation by itself.
- **Tenant code-mode hosting keeps execution outside PMCP**: the host contract
  in `specs/tenant-code-mode-host-contract.md` treats PMCP as the broker and
  the companion tenant server as the sandbox execution authority. The contract
  does not add PMCP-owned sandbox isolation, tenant auth, or durable execution
  logs. PMCP policy can allow or deny `tenant-code-mode` and
  `tenant-code-mode::*`, apply output caps, and redact returned diagnostics, but
  production multi-tenant isolation still requires companion-server and
  deployment controls.
- **No audit log persistence**: the per-call audit log (`tool_call tool=... ok=...`) is
  written to stderr/stdout, and structured `gateway.health.audit_events` are
  bounded in memory. There is no database, log rotation, or tamper-evident
  storage.
- **Trace context is metadata, not identity**: PMCP preserves accepted
  `traceparent`, `tracestate`, and `baggage` strings only through explicit
  PMCP-owned fields or request metadata. Do not put bearer tokens, API keys,
  auth codes, user identifiers, or other secrets in trace baggage.
- **MCP task records are transient**: task IDs are downstream server identifiers
  held in gateway memory for visibility and cancellation. They are not durable
  audit records and do not provide cross-user authorization isolation on
  unauthenticated local transports.
- **Two protocol eras are served on one endpoint, through the same policy
  gate**: the `MCP-Protocol-Version: 2026-07-28` header plus a `params._meta`
  envelope route a request to the modern era instead of the
  `initialize`-negotiated handshake era (`2024-11-05`–`2025-11-25`) — see
  README's protocol-negotiation section. Both eras dispatch through the same
  registered handlers, so the same policy, audit, and input-schema validation
  applies regardless of which era selected the request; the modern era is not
  a lower-trust bypass. Unsupported draft extensions beyond the six proxied
  operations, `server/discover`, and `subscriptions/listen` remain out of
  scope until PMCP explicitly claims them.
- **`subscriptions/listen` has no absolute-lifetime cap, deliberately**: it is
  exempt from the `request_timeout` wrapper that bounds every other `/mcp`
  POST, because that wrapper silently truncated every subscription stream at
  `request_timeout` seconds (60s by default) with no graceful close frame —
  the defect this exemption fixes, not a property worth preserving for a
  connection that is long-lived by design. What bounds exposure instead:
  `PMCP_MAX_LISTEN_STREAMS` caps concurrent subscriptions (default 64, same
  as the retired pre-session keep-alive's concurrency cap), the SDK's own
  per-stream event-backlog cap bounds a single subscription's memory, and
  `/mcp` auth applies to `subscriptions/listen` exactly as to every other
  method whenever `auth_mode` is configured. There is no configurable
  replacement for the retired `PMCP_KEEPALIVE_MAX_SECONDS` absolute-lifetime
  bound; an operator who needs one must enforce it at a reverse proxy or load
  balancer in front of PMCP.

## The v13 trust boundary

<!-- TRUST-MODEL-CLAIMS: BEGIN -->
### The v13 trust model

#### Identity, not labels

- A discovered server is provisioned only after its package is resolved to a concrete registry entry, pinned to an exact version, and approved by identity rather than by the name its configuration gives it [C-01].
- Default-deny holds for a discovered server across `provision`, `connect`, `restart` and `update`, while a manifest-backed server provisions unchanged [C-02].
- A `packages.denylist` entry outranks an operator approval, so a package the operator trusted is still refused when policy denies it [C-03].
- An approved pin still refuses an unpinned argv, so an approved server whose command is a bare `npx -y pkg` cannot re-resolve a floating version at spawn [C-04].
- An agent-supplied package-manager variable is part of a package's identity and is refused at registration and at `auth_connect` for a discovered server [C-05].
- The install argv is logged before the spawn with credentials redacted and the package identity left intact [C-06].
- A denylisted manifest package is refused at every spawning door, including one named only by its install argv or selected by an `npx` option [C-07].

#### Consent for repository-supplied configuration

- The project sources `.pmcp/manifest.yaml`, `.mcp.json` and `.mcp-gateway-policy.yaml` are ignored until an operator approves them, so an unapproved source never replaces a shipped command and an unapproved project policy is never read [C-08].
- Approval is keyed to a source's content, so an approved source edited afterwards is refused again [C-09].
- Each source is read exactly once and the bytes that were gated are the bytes that are used, closing the window between the check and the use at every loader [C-10].
- A refused source hands back no bytes and fails closed on a store error or an unreadable file rather than raising [C-11].
- A refusal names the absolute-path `pmcp trust approve` command that would grant it and reaches the operator as exactly one warning [C-12].
- An approved project policy can only narrow the operator's policy: it cannot widen what the operator allows and cannot drop the default redaction patterns [C-13].
- An unapproved overlay that adds a new server is not applied, and the added name is not treated as manifest-backed at `provision` or `connect` [C-14].
- The approval store is content-keyed and user-scoped, and a store resident in the served project, or in any checkout enclosing the served project or the current directory, is refused whether reached directly or through a symlink; an approval recorded in such a store grants nothing [C-15].
- A record marked denied is never treated as approved, and an absent record refuses [C-16].
- A checkout-set `PMCP_MANIFEST_PATH`, `PMCP_CONFIG` or `PMCP_POLICY` is refused by provenance at both the startup and runtime doors, while an override the operator exported into their own shell still applies [C-17].
- Keys that a `.env` load introduced are stripped from every spawned server, and the install-spawn strip resolves the project store from the root the gateway was given rather than the working directory it happens to run in, while a server's own declared credential still resolves [C-18].
- A fresh operator with no approval store and no project files provisions, connects and restarts a manifest-backed server unchanged, consults no consent gate, and sees no new startup warning [C-19].
- Every refusal across the consent, provision and feedback gates carries a non-empty remedy that is runnable as printed, names only `pmcp` verbs the CLI dispatches, and renders a path holding a space or shell metacharacters inert [C-20].

#### Outbound actions

- Outbound feedback submission is off unless an operator both turns it on and passes an explicit per-call confirmation, and a gateway with no guidance configuration refuses to submit [C-21].
- Authorisation to submit is decided by a token's provenance rather than its presence, so an ambient `GITHUB_TOKEN` attempts no request and spawns no `gh`, and only an operator-supplied token is honoured [C-22].
- A checkout-supplied token or repository override is refused and a malformed or hostile override is rendered inert, so the destination stays the packaged repository [C-23].
- No egress door survives in the handler beyond the gated path, and a dispatched post is reported as submitted only when its creation is observed, never when the outcome is unconfirmed [C-24].
- Feedback provenance is tracked by an additive registry that records what the gateway wrote at runtime and does not widen the `.env` strip [C-25].

#### Limitations

- **Limitation.** The approval store reserves a denied decision that nothing shipped writes: the CLI dispatches `approve`, `list` and `revoke`, and no verb records a denial [C-26].
- **Limitation.** An operator approval binds no integrity digest, because the CLI records it as none, so the pin is a name and a version and not the bytes the registry serves [C-27].
- **Limitation.** Setting the startup policy rewrites the selected source and silently invalidates a content-keyed approval it does not mention, so the operator learns only at the next startup's consent refusal [C-28].
- **Limitation.** For a server name in neither the manifest nor the discovered table, `auth_connect` admits a credential-shaped override whose value is written to a gateway-managed store and inherited by no spawned server, though the gateway process itself holds it [C-29].
- **Limitation.** No per-socket timeout bounds a whole request, so a feedback worker abandoned by a cancelled handler can outlive it, although it is forbidden to send by then [C-30].
- **Limitation.** A credential store written and removed out of band, that this process never read, is invisible to every provenance source [C-31].
- **Limitation.** Secrets the operator exports into the shell that starts the gateway are still inherited by spawned servers, deliberately and out of scope [C-32].
- **Limitation.** A store resident in the served checkout is genuinely refused, but the refusal is raised per read and surfaces through the consent gate's not-approved message naming `pmcp trust approve`, rather than as a distinct abort at startup [C-33].
- **Limitation.** The npm version-check User-Agent still names the old repository, tracked as a follow-up rather than as a trust-boundary property [C-34].
- **Limitation.** The approval-store write is not atomic and can truncate before it finishes, tracked as a follow-up to the durability of the operator's own records [C-35].
- **Limitation.** The `pmcp trust approve` verb now also refuses a store resident in the checkout containing the file being approved -- closing the case where approve wrote into a checkout-resident store that `serve --project` then refused, and aligning nested and sibling checkout layouts of that shape through the shared enclosing-checkout walk -- leaving a narrow residual only because serve treats the served root itself as a boundary verbatim while approve keys on the checkout enclosing the approved path, so the two are not guaranteed identical for a served root that is not itself a marked checkout [C-36].
- **Limitation.** PMCP spawns a child process for every downstream server, and although a project configuration entry is gated by consent and a discovered package is bound to an approved identity, a server an operator approves still runs, so configure only servers you trust [C-37].

<!-- CLAIM-LEDGER: BEGIN -->
| Claim | Kind | Proof |
|---|---|---|
| C-01 | guarantee | `tests/test_package_identity.py::test_an_exact_pin_resolves_to_that_version`, `tests/test_package_identity.py::test_an_invalid_package_name_is_refused_before_any_fetch`, `tests/test_package_approvals.py::test_an_approval_names_one_registry_name_and_resolved_version`, `tests/test_package_identity_gate.py::test_an_approved_pinned_discovered_server_still_connects`, `tests/test_trust_boundaries_e2e.py::test_s01_provision_refuses_an_arbitrary_package_under_an_allowlisted_name` |
| C-02 | guarantee | `tests/test_trust_boundaries_e2e.py::test_s01_provision_refuses_an_arbitrary_package_under_an_allowlisted_name`, `tests/test_trust_boundaries_e2e.py::test_s01_connect_server_refuses_the_same_registration`, `tests/test_trust_boundaries_e2e.py::test_s01_restart_server_refuses_the_same_registration`, `tests/test_trust_boundaries_e2e.py::test_s01_update_server_refuses_a_discovered_server_before_any_probe`, `tests/test_package_identity_gate.py::test_a_manifest_backed_server_provisions_unchanged` |
| C-03 | guarantee | `tests/test_trust_boundaries_composition.py::test_a_denylisted_package_outranks_an_operator_approval`, `tests/test_policy_package_identifiers.py::test_evaluate_package_policy_denylist_beats_allowlist`, `tests/test_policy_package_identifiers.py::test_a_project_denied_package_is_denied_even_when_the_user_allows_it` |
| C-04 | guarantee | `tests/test_trust_boundaries_composition.py::test_an_approved_pin_still_refuses_an_unpinned_argv`, `tests/test_package_identity_gate.py::test_a_configured_server_with_unpinned_argv_is_refused`, `tests/test_pkgid_manifest_npx_selectors.py::test_a_selected_or_positional_package_is_still_refused` |
| C-05 | guarantee | `tests/test_trust_boundaries_e2e.py::test_s01_a_package_manager_variable_is_refused_at_registration`, `tests/test_trust_boundaries_e2e.py::test_s01_auth_connect_refuses_a_package_manager_variable_for_a_discovered_server` |
| C-06 | guarantee | `tests/test_install_argv_logging.py::test_a_credential_bearing_argument_is_redacted_but_the_package_identity_is_not`, `tests/test_install_argv_logging.py::test_start_install_logs_rendered_argv_at_warning`, `tests/test_pkgid_spawn_logging.py::test_the_stdio_spawn_of_a_package_runner_logs_its_argv_before_spawning`, `tests/test_pkgid_spawn_logging.py::test_the_update_probe_redacts_a_credential_but_not_a_pinned_package` |
| C-07 | guarantee | `tests/test_pkgid_panel_fixes.py::test_a_denylisted_manifest_package_is_refused_by_every_spawning_door`, `tests/test_pkgid_panel_fixes.py::test_a_manifest_package_named_only_by_its_install_argv_is_refused`, `tests/test_pkgid_manifest_npx_selectors.py::test_a_denylisted_package_named_by_a_selector_is_refused` |
| C-08 | guarantee | `tests/test_trust_boundaries_e2e.py::test_s03_an_unapproved_overlay_does_not_replace_a_shipped_command`, `tests/test_trust_boundaries_e2e.py::test_s03_an_unapproved_project_mcp_json_is_not_applied`, `tests/test_trust_boundaries_e2e.py::test_s11_an_unapproved_project_policy_is_not_read_at_all`, `tests/test_project_consent_gate.py::test_an_unrecorded_path_is_refused`, `tests/test_project_source_consent_manifest.py::test_approved_overlay_is_applied`, `tests/test_project_source_consent_config.py::test_approved_project_mcp_json_is_applied` |
| C-09 | guarantee | `tests/test_trust_boundaries_e2e.py::test_s03_an_approved_source_edited_afterwards_is_refused_again`, `tests/test_trust_cli.py::test_approve_replaces_the_record_when_the_file_changes`, `tests/test_trust_cli.py::test_approve_records_the_file_and_makes_its_content_approved` |
| C-10 | guarantee | `tests/test_project_consent_gate.py::test_read_and_gate_opens_the_path_exactly_once`, `tests/test_trust_boundaries_e2e.py::test_s03_the_parsed_bytes_are_the_gated_bytes_at_every_loader` |
| C-11 | guarantee | `tests/test_project_consent_gate.py::test_read_and_gate_returns_none_bytes_when_refused`, `tests/test_project_consent_gate.py::test_a_store_error_is_refused_not_raised`, `tests/test_project_consent_gate.py::test_an_unreadable_source_is_refused_not_raised` |
| C-12 | guarantee | `tests/test_project_consent_gate.py::test_remediation_is_the_absolute_path_trust_approve_command`, `tests/test_project_consent_gate.py::test_log_refusal_emits_one_warning_naming_the_remediation`, `tests/test_refusal_remedies.py::test_a_consent_refusal_reaches_the_operator_as_one_warning_naming_its_remedy` |
| C-13 | guarantee | `tests/test_trust_boundaries_e2e.py::test_s11_an_approved_project_policy_cannot_widen_the_user_policy`, `tests/test_trust_boundaries_e2e.py::test_s11_an_approved_project_policy_cannot_drop_the_default_redaction_patterns`, `tests/test_project_source_consent_policy.py::test_project_redaction_patterns_extend_rather_than_replace_defaults` |
| C-14 | guarantee | `tests/test_trust_boundaries_composition.py::test_an_unapproved_overlay_that_adds_a_server_is_not_applied`, `tests/test_trust_boundaries_composition.py::test_an_added_server_is_not_manifest_backed_at_provision`, `tests/test_trust_boundaries_composition.py::test_an_added_server_is_not_manifest_backed_at_connect` |
| C-15 | guarantee | `tests/test_trust_store.py::test_is_approved_judges_supplied_bytes_not_the_path`, `tests/test_trust_store.py::test_a_store_directory_pmcp_creates_is_not_group_or_world_accessible`, `tests/test_trust_boundaries_composition.py::test_a_trust_store_shipped_inside_the_checkout_is_refused`, `tests/test_trust_boundaries_composition.py::test_a_checkout_resident_store_is_refused_through_a_symlink`, `tests/test_trust_boundaries_composition.py::test_an_approval_in_a_checkout_resident_store_grants_nothing`, `tests/test_trust_store_residency_root.py::test_serving_a_project_refuses_its_checkout_resident_store_from_elsewhere`, `tests/test_trust_store_residency_root.py::test_serving_one_project_still_refuses_a_store_resident_in_the_launch_checkout`, `tests/test_trust_store_residency_root.py::test_serving_still_loads_an_operator_store_outside_the_checkout`, `tests/test_trust_store_residency_root.py::test_serving_a_subdirectory_refuses_a_store_resident_in_the_enclosing_checkout`, `tests/test_trust_store_residency_root.py::test_a_bare_serve_from_a_subdirectory_refuses_a_store_in_the_enclosing_checkout` |
| C-16 | guarantee | `tests/test_trust_store.py::test_a_denied_record_is_not_approved`, `tests/test_trust_cli.py::test_revoke_removes_the_record_so_the_content_is_no_longer_approved` |
| C-17 | guarantee | `tests/test_env_overlay_provenance.py::test_predicate_true_for_a_shell_exported_var`, `tests/test_env_overlay_provenance.py::test_predicate_false_when_dotenv_sourced`, `tests/test_env_overlay_provenance.py::test_predicate_false_when_pmcp_introduced`, `tests/test_env_overlay_provenance.py::test_startup_door_does_not_apply_the_injected_overlay`, `tests/test_env_overlay_provenance.py::test_runtime_door_does_not_apply_the_injected_overlay`, `tests/test_env_overlay_provenance.py::test_operator_exported_manifest_path_still_applies`, `tests/test_env_overlay_provenance.py::test_operator_exported_config_still_a_source`, `tests/test_env_overlay_provenance.py::test_operator_exported_policy_still_adopted` |
| C-18 | guarantee | `tests/test_env_leak_229.py::test_a_startup_env_secret_never_reaches_a_spawned_server`, `tests/test_env_leak_229.py::test_only_the_declared_key_is_injected`, `tests/test_env_leak_229.py::test_the_servers_own_credential_still_resolves`, `tests/test_install_child_env_project_root.py::test_an_install_spawn_does_not_inherit_a_project_scoped_credential_when_the_cwd_differs`, `tests/test_install_child_env_project_root.py::test_the_production_install_path_passes_the_gateways_project_root`, `tests/test_install_child_env_project_root.py::test_a_project_scoped_credential_is_stripped_when_the_cwd_is_the_project_root`, `tests/test_install_child_env_project_root.py::test_the_helper_falls_back_to_the_working_directory_walk_when_given_no_root`, `tests/test_install_child_env_project_root.py::test_no_production_call_site_omits_the_project_root`, `tests/test_trust_boundaries_composition.py::test_an_install_spawn_strips_the_credentials_pmcp_stores_hold`, `tests/test_trust_boundaries_composition.py::test_the_install_spawn_strip_uses_the_gateways_project_root_not_the_working_directory` |
| C-19 | guarantee | `tests/test_fresh_operator_baseline.py::test_a_fresh_home_holds_no_trust_store_and_no_package_approvals`, `tests/test_fresh_operator_baseline.py::test_a_manifest_backed_server_provisions_with_no_trust_store`, `tests/test_fresh_operator_baseline.py::test_a_manifest_backed_server_connects_with_no_trust_store`, `tests/test_fresh_operator_baseline.py::test_a_manifest_backed_server_restarts_with_no_trust_store`, `tests/test_fresh_operator_baseline.py::test_no_project_file_means_no_consent_gate_is_consulted`, `tests/test_fresh_operator_baseline.py::test_a_fresh_operator_sees_no_new_startup_warning`, `tests/test_fresh_operator_baseline.py::test_every_shipped_manifest_entry_is_determinable_without_a_denylist` |
| C-20 | guarantee | `tests/test_refusal_remedies.py::test_every_refusal_carries_a_non_empty_remedy`, `tests/test_refusal_remedies.py::test_every_pmcp_command_in_a_remedy_names_a_verb_the_cli_dispatches`, `tests/test_refusal_remedies.py::test_every_remedy_is_runnable_as_printed`, `tests/test_refusal_remedies.py::test_a_path_containing_a_space_yields_a_runnable_approve_command`, `tests/test_refusal_remedies.py::test_a_path_carrying_shell_metacharacters_is_rendered_inert`, `tests/test_refusal_remedies.py::test_every_consent_refusal_reason_is_driven_by_this_audit`, `tests/test_refusal_remedies.py::test_every_provision_refusal_reason_is_driven_by_this_audit`, `tests/test_refusal_remedies.py::test_every_feedback_refusal_reason_is_driven_by_this_audit` |
| C-21 | guarantee | `tests/test_feedback_submission_flag.py::test_feedback_submission_defaults_to_off`, `tests/test_feedback_submission_flag.py::test_the_cli_verb_persists_the_submission_flag`, `tests/test_feedback_egress.py::test_a_gateway_with_no_guidance_config_refuses_to_submit`, `tests/test_refusal_remedies.py::test_a_feedback_refusal_reaches_the_tool_output_with_its_remedy` |
| C-22 | guarantee | `tests/test_trust_boundaries_e2e.py::test_s04_an_ambient_github_token_submits_nothing_and_spawns_no_gh`, `tests/test_feedback_egress.py::test_an_ambient_github_token_attempts_no_request`, `tests/test_feedback_egress_gate.py::test_a_checkout_sourced_token_is_refused`, `tests/test_egress_panel_fixes.py::test_a_missing_store_still_allows_an_operator_exported_token` |
| C-23 | guarantee | `tests/test_trust_boundaries_e2e.py::test_s04_a_checkout_supplied_token_or_repository_is_refused`, `tests/test_feedback_egress.py::test_a_checkout_env_file_cannot_redirect_the_destination`, `tests/test_feedback_egress.py::test_a_checkout_env_file_cannot_supply_the_token`, `tests/test_feedback_egress.py::test_a_hostile_repository_override_is_rendered_inert_in_the_refusal`, `tests/test_feedback_egress_gate.py::test_a_checkout_sourced_repository_override_is_refused`, `tests/test_feedback_egress_gate.py::test_a_malformed_repository_override_is_refused` |
| C-24 | guarantee | `tests/test_trust_boundaries_e2e.py::test_s04_no_egress_door_remains_in_the_handler`, `tests/test_feedback_egress.py::test_a_dispatched_but_unconfirmed_post_is_not_reported_as_submitted`, `tests/test_feedback_egress.py::test_a_creation_observed_before_the_timeout_is_reported_as_submitted`, `tests/test_feedback_egress.py::test_a_failed_submission_opens_no_second_door`, `tests/test_feedback_egress_gate.py::test_a_dispatched_post_with_no_response_is_reported_as_unconfirmed` |
| C-25 | guarantee | `tests/test_feedback_provenance.py::test_a_key_pmcp_wrote_at_runtime_is_recorded`, `tests/test_feedback_provenance.py::test_the_new_registry_does_not_widen_the_dotenv_strip`, `tests/test_feedback_provenance.py::test_the_strict_lookup_raises_where_the_lenient_one_swallows` |
| C-26 | limitation | — |
| C-27 | limitation | characterizes: `tests/test_package_identity.py::test_a_missing_integrity_is_none_not_a_fabrication`, `tests/test_package_approvals.py::test_an_integrity_that_contradicts_the_record_is_not_approved` |
| C-28 | limitation | — |
| C-29 | limitation | — |
| C-30 | limitation | characterizes: `tests/test_egress_panel_fixes.py::test_a_cancelled_handler_abandons_the_claim_so_the_worker_never_posts`, `tests/test_egress_panel_fixes.py::test_a_cancelled_handler_re_raises_rather_than_answering` |
| C-31 | limitation | — |
| C-32 | limitation | characterizes: `tests/test_env_leak_229.py::test_a_shell_provided_variable_sharing_a_name_is_not_stripped`, `tests/test_env_overlay_provenance.py::test_predicate_true_for_a_shell_exported_var` |
| C-33 | limitation | — |
| C-34 | limitation | — |
| C-35 | limitation | — |
| C-36 | limitation | characterizes: `tests/test_trust_store_residency_root.py::test_trust_approve_verb_inside_a_checkout_uses_cwd`, `tests/test_trust_store_residency_root.py::test_trust_approve_verb_still_refuses_a_checkout_resident_store`, `tests/test_trust_store_residency_root.py::test_trust_approve_from_outside_refuses_a_store_in_the_approved_paths_checkout`, `tests/test_trust_store_residency_root.py::test_trust_approve_from_outside_with_a_store_outside_still_approves` |
| C-37 | limitation | — |
<!-- CLAIM-LEDGER: END -->
<!-- TRUST-MODEL-CLAIMS: END -->

## Reporting a Vulnerability

Please **do not** open a public GitHub issue for security vulnerabilities.

Report via **GitHub private security advisory**:
[https://github.com/Consiliency/pmcp/security/advisories/new](https://github.com/Consiliency/pmcp/security/advisories/new)

Include:
- A description of the vulnerability and its impact
- Steps to reproduce (proof-of-concept if possible)
- Affected versions
- Any suggested mitigation

**Response timeline**:
- Acknowledgment within **7 days**
- Fix or mitigation plan within **30 days** for critical/high severity
- Coordinated disclosure after patch is available

## Security Hardening Checklist

Before exposing PMCP beyond localhost:

- [ ] Set `PMCP_AUTH_TOKEN` (do not use `--auth-token`; token visible in `ps aux`)
- [ ] Terminate TLS at your reverse proxy; proxy to `127.0.0.1:3344`
- [ ] Bind to loopback (`--host 127.0.0.1`, the default)
- [ ] Set `--rate-limit` appropriate for your traffic (e.g. `60` for 1 req/sec per observed source IP)
- [ ] Firewall `/health` and `/metrics` to internal networks only
- [ ] Run as a non-root user (Docker image already uses `appuser`)
- [ ] Review downstream MCP server configs — only trust servers you control
- [ ] For `tenant-code-mode`, keep `${TENANT_CODE_MODE_MCP_TOKEN}` and
      `${TENANT_CODE_MODE_TENANT_ID}` in env stores or process environment, not
      config files or logs
- [ ] For hosted tenant runs, use `gateway.tasks_cancel` with downstream task
      IDs and keep durable logs, artifacts, artifact retention, tenant auth,
      SSO/RBAC, and billing outside PMCP
