# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| 1.9.x   | ✅ Active  |
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
- Request floods via per-IP sliding-window rate limiting (`--rate-limit`)
- Oversized payloads causing OOM (`Content-Length > 10 MB → 413`)
- Hanging downstream tools consuming connections indefinitely (60 s request timeout)
- Multiple gateway instances fighting over resources (fcntl singleton lock)
- Reconnect storms from crashing downstream servers (per-server reconnect flag)

### Known limitations

- **No mTLS**: clients are not authenticated by certificate; only Bearer token.
- **No per-tool ACL on HTTP**: any valid token can invoke any tool. Tool-level policy is
  enforced at the MCP layer, not the HTTP layer.
- **`/health` and `/metrics` are unauthenticated by design**: load balancers and Prometheus
  scrapers typically cannot present Bearer tokens. Do not expose these endpoints on a public
  interface without a separate network-layer control (firewall rule, IP allowlist).
- **Subprocess spawning**: PMCP forks child processes for downstream MCP servers. A malicious
  MCP server config entry could cause PMCP to spawn arbitrary executables. Only configure
  servers you trust.
- **No audit log persistence**: the per-call audit log (`tool_call tool=... ok=...`) is
  written to stderr/stdout only. There is no log rotation or tamper-evident storage.

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
- [ ] Set `--rate-limit` appropriate for your traffic (e.g. `60` for 1 req/sec per IP)
- [ ] Firewall `/health` and `/metrics` to internal networks only
- [ ] Run as a non-root user (Docker image already uses `appuser`)
- [ ] Review downstream MCP server configs — only trust servers you control
