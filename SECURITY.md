# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| 1.10.x  | ✅ Active  |
| 1.9.x   | ✅ Maintenance |
| < 1.9   | ❌ No longer supported |

## Threat Model

PMCP is a local-first MCP gateway. Its default security posture assumes:

- **Bind address**: `127.0.0.1` (loopback only). The HTTP port is not exposed to external
  networks unless you explicitly bind to `0.0.0.0` or place it behind a reverse proxy.
- **Trust boundary**: processes running on the same host as PMCP are trusted. Remote clients
  (via reverse proxy) are untrusted and must present a valid Bearer token when
  `--auth-token` / `PMCP_AUTH_TOKEN` is configured.
- **TLS**: PMCP does not terminate TLS. For any network exposure, terminate TLS at a reverse
  proxy (nginx, Caddy) and proxy to `127.0.0.1:3344`. See the README for example configs.

### What PMCP protects against (when correctly configured)

- Unauthenticated tool invocations via Bearer token guard on `/mcp`
- Timing oracle attacks on token comparison (`hmac.compare_digest`)
- Request floods via per-source-IP sliding-window rate limiting (`--rate-limit`
  / `PMCP_RATE_LIMIT`) on `/mcp`
- Oversized payloads causing OOM (`Content-Length > 10 MB → 413`)
- Hanging downstream tools consuming connections indefinitely (60 s request timeout)
- Multiple gateway instances fighting over resources (fcntl singleton lock)
- Reconnect storms from crashing downstream servers (per-server reconnect flag)

### Known limitations

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
- **Authorization discovery is diagnostic**: PMCP can surface protected-resource,
  authorization-server, OIDC discovery, Client ID Metadata Document, scope, and
  URL-mode elicitation hints, but it is not an authorization server and does not
  store third-party OAuth refresh tokens.
- **URL-mode elicitation is out of band**: never paste OAuth codes, third-party
  passwords, or provider refresh tokens into gateway tools. `gateway.auth_connect`
  accepts API-key credentials only for local env-store flows; URL-mode flows only
  accept an elicitation identifier and consent acknowledgement.
- **Redaction is best-effort defense in depth**: PMCP redacts bearer tokens, API
  keys, common secrets, URL userinfo, authorization codes, and auth-bearing query
  parameters from gateway outputs, status/doctor diagnostics, feedback payloads,
  and HTTP diagnostics. Treat all logs as operational data and avoid adding
  secrets to server names, tool names, or free-form descriptions.
- **No per-user credential isolation**: user-scope env-store files are owned by
  the local OS account, project-scope env-store files are owned by the project
  directory, and remote header placeholders resolve from those stores plus
  process environment. PMCP does not provide a multi-tenant authorization layer
  or cross-user credential separation inside one running gateway.
- **Subprocess spawning**: PMCP forks child processes for downstream MCP servers. A malicious
  MCP server config entry could cause PMCP to spawn arbitrary executables. Only configure
  servers you trust.
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
- **Draft protocol compatibility is additive**: PMCP tolerates current/draft MCP
  protocol and Streamable HTTP header metadata where documented, but unsupported
  draft extensions remain out of scope until PMCP explicitly claims them.

## Reporting a Vulnerability

Please **do not** open a public GitHub issue for security vulnerabilities.

Report via **GitHub private security advisory**:
[https://github.com/ViperJuice/pmcp/security/advisories/new](https://github.com/ViperJuice/pmcp/security/advisories/new)

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
