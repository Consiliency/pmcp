# PMCP - Progressive MCP

<!-- mcp-name: io.github.ViperJuice/pmcp -->

[![PyPI version](https://badge.fury.io/py/pmcp.svg)](https://pypi.org/project/pmcp/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**Progressive disclosure for MCP** - Minimal context bloat with on-demand tool discovery and dynamic server provisioning.

## The Problem

When Claude Code connects directly to multiple MCP servers (GitHub, Jira, DB, etc.), it loads **all** tool schemas into context. This causes:
- **Context bloat**: Dozens of tool definitions consume tokens before you even ask a question
- **Static configuration**: Requires Claude Code restart to see new servers
- **No progressive disclosure**: Full schemas shown even when not needed

Anthropic has [highlighted context bloat](https://www.anthropic.com/news) as a key challenge with MCP tooling.

## The Solution

**PMCP** acts as a single MCP server that Claude Code connects to. Instead of exposing all downstream tools, it provides:

- **26 stable meta-tools** (not the 50+ underlying tools)
- **Lazy by default**: downstream servers are available on demand and only eager-start when listed in `autoStart`
- **Dynamically provisions** new servers on-demand from a manifest of 90+
- **Progressive disclosure**: Compact capability cards first, detailed schemas only on request
- **Policy enforcement**: Output size caps and optional secret redaction

## Quick Start

### Installation

```bash
# With uv (recommended)
uv pip install pmcp

# Or run directly without installing
uvx pmcp

# With pip
pip install pmcp

```

> **Capability matching is built-in** — no API key needed. `gateway.request_capability`
> uses a pure-Python matcher that can return direct CLI guidance for installed
> native tools, MCP server candidates, or registry search guidance.

### Configure with `pmcp setup`

PMCP includes a wizard-style helper that can render ready-to-use MCP client config for Claude and OpenCode.
The generated config only connects your client to the PMCP gateway. Downstream MCP
servers stay lazy until first use unless you add them to `autoStart` in your
`.mcp.json`.

Use `pmcp setup` to print the generated config:

```bash
pmcp setup --client claude --mode stdio    # Claude local stdio
pmcp setup --client claude --mode http     # Claude shared-service HTTP
pmcp setup --client opencode --mode stdio  # OpenCode local stdio
pmcp setup --client opencode --mode http   # OpenCode shared-service HTTP
```

Named profiles cover the common modes:

```bash
pmcp setup --profile local-stdio
pmcp setup --profile shared-local-http
pmcp setup --profile authenticated-shared-http
pmcp setup --profile ci
```

Write directly into your client config with `--write`:

```bash
pmcp setup --client claude --mode http --write
```

Without `--write`, `pmcp setup` prints the config so you can paste it into:
- Claude: `~/.mcp.json`
- OpenCode: `~/.config/opencode/opencode.json`

Use shared-service HTTP mode when running one PMCP service for multiple sessions or clients. Use single-process stdio mode for local testing.

### Shared Service Mode (Manual)

If you prefer manual config, point each client to the shared HTTP endpoint:

```json
{
  "mcpServers": {
    "pmcp": {
      "type": "http",
      "url": "http://127.0.0.1:3344/mcp"
    }
  }
}
```

Why this mode: PMCP uses a singleton lock (`~/.pmcp/gateway.lock`), so multiple local launches can conflict. One shared service avoids lock collisions and keeps tool state consistent.

Shared gateway state:

- All clients connected to one PMCP HTTP gateway share downstream server connections, pending requests, provisioned tools, and live lifecycle state.
- `gateway.refresh(force=true)`, `gateway.disconnect_server(force=true)`, and `gateway.restart_server(force=true)` can cancel or interrupt downstream work started by another client using the same gateway.
- `gateway.health` and live `pmcp status --verbose` show startup policy observations for downstream servers without exposing secret values.
- `--rate-limit` / `PMCP_RATE_LIMIT` applies per observed source IP on `/mcp`; localhost clients and reverse-proxied clients can share one bucket unless the proxy preserves distinct client IPs.

Quick verification:

```bash
systemctl --user is-active pmcp
curl -sS http://127.0.0.1:3344/mcp
```

### Security

**HTTP transport is unauthenticated by default.** For any non-localhost exposure,
choose an HTTP auth mode and terminate TLS in front of PMCP.

`shared-secret` mode is the backward-compatible single-tenant guard. It accepts
one static bearer value on `/mcp`:

```bash
# Start with bearer auth from the environment
PMCP_AUTH_TOKEN=mysecrettoken pmcp --transport http
```

Avoid passing production tokens with `--auth-token`; command-line arguments can
be visible in process listings on shared hosts.

Clients must then include `Authorization: Bearer mysecrettoken` on `/mcp` requests.
`/health` and `/metrics` remain unauthenticated by design; protect them with
firewall rules, IP allowlists, or reverse-proxy policy before any non-localhost
exposure.

`resource-server` mode makes PMCP validate Authorization Server issued access
tokens as an OAuth 2.1 Resource Server. Configure the HTTP app with a public
issuer, JWKS URL, resource audience, required scopes, and exact allowed origins:

```python
create_http_app(
    mcp_server,
    auth_mode="resource-server",
    resource_server_issuer="https://issuer.example",
    resource_server_jwks_url="https://issuer.example/.well-known/jwks.json",
    resource_server_audience="https://pmcp.example/mcp",
    required_scopes=["pmcp.invoke"],
    allowed_origins=["https://app.example"],
)
```

PMCP validates token signature, issuer, expiry, not-before, and audience. It
rejects private, link-local, loopback, multicast, and unspecified hosts in
public auth metadata URLs. PMCP is still not an Authorization Server and does
not provide dynamic client registration, SSO, RBAC, billing, or a complete
multi-tenant identity service.

**Assumptions and trust model:**

- PMCP binds to `127.0.0.1` by default — not safe to expose publicly without
  `PMCP_AUTH_TOKEN`.
- Config files (`.mcp.json`) are trusted inputs — treat them like code; do not load untrusted configs.
- Secrets in `.env` files are passed to child MCP server processes; protect the `.env` file with filesystem permissions.

**Production background service (Linux systemd):**

```ini
# ~/.config/systemd/user/pmcp.service
[Unit]
Description=PMCP MCP Gateway

[Service]
Environment=PMCP_AUTH_TOKEN=replace-with-secret-token
ExecStart=/usr/local/bin/pmcp --transport http
Restart=on-failure

[Install]
WantedBy=default.target
```

```bash
systemctl --user enable --now pmcp
```

Or with nohup:

```bash
PMCP_AUTH_TOKEN=replace-with-secret-token nohup pmcp --transport http >> ~/.pmcp/logs/gateway.log 2>&1 &
```

### TLS / Reverse Proxy

PMCP's HTTP transport is **plaintext**. For any exposure beyond localhost, terminate TLS at a
reverse proxy and forward to `127.0.0.1:3344`. Keep `--host 127.0.0.1` (the default) so PMCP
only listens on the loopback interface.

**Nginx** (`/etc/nginx/sites-available/pmcp`):

```nginx
server {
    listen 443 ssl;
    server_name pmcp.example.com;

    ssl_certificate     /etc/letsencrypt/live/pmcp.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/pmcp.example.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:3344;
        proxy_set_header Authorization $http_authorization;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
    }
}
```

**Caddy** (`Caddyfile`):

```
pmcp.example.com {
    reverse_proxy 127.0.0.1:3344
}
```

Caddy handles TLS automatically via Let's Encrypt.

### Other MCP Clients

PMCP works with any MCP-compatible client. Below are configuration examples for popular clients.

#### Codex CLI

Create `~/.codex/mcp.json` (verify path in Codex documentation):

```json
{
  "mcpServers": {
    "gateway": {
      "command": "pmcp",
      "args": []
    }
  }
}
```

#### Gemini CLI

Create the appropriate config file (verify path in Gemini CLI documentation):

```json
{
  "mcpServers": {
    "gateway": {
      "command": "pmcp",
      "args": []
    }
  }
}
```

> **Note**: Configuration paths and formats vary by client. Verify the exact location and format in each client's official documentation.

### Your First Interaction

```
You: "Take a screenshot of google.com"

Claude uses: gateway.invoke {
  tool_id: "playwright::browser_navigate",
  arguments: { url: "https://google.com" }
}
// Then: gateway.invoke { tool_id: "playwright::browser_screenshot" }

Returns: Screenshot of google.com
```

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        Claude Code                          │
│  Only connects to PMCP (single server in config)            │
└────────────────────────────┬────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────┐
│                          PMCP                               │
│  • 26 meta-tools (catalog, invoke, tasks, config, etc.)     │
│  • Progressive disclosure (compact cards → full schemas)    │
│  • Policy enforcement (allow/deny lists)                    │
└────────────────────────────┬────────────────────────────────┘
                             │
        ┌────────────────────┼────────────────────┐
        ▼                    ▼                    ▼
┌───────────────┐  ┌─────────────────┐  ┌─────────────────┐
│  Explicit     │  │    Manifest     │  │  Custom Servers │
│  autoStart    │  │   (90+ servers  │  │  (your own MCP  │
│  servers      │  │   on-demand)    │  │  servers)       │
└───────────────┘  └─────────────────┘  └─────────────────┘
```

**Key principle**: Users configure ONLY `pmcp` in Claude Code.
The gateway discovers and manages all other servers.

### Why Single-Gateway?

1. **No context bloat** - Claude sees 26 tools, not 50+
2. **No restarts** - Provision new servers without restarting Claude Code
3. **Consistent interface** - All tools accessed via `gateway.invoke`
4. **Policy control** - Centralized allow/deny rules

## Gateway Tools

The gateway exposes **26 meta-tools** organized into four categories:

Tool annotations are preserved as untrusted hints only; policy and safety notes
continue to use PMCP's own risk model. When a tool schema omits `$schema`, PMCP
reports the JSON Schema dialect as `https://json-schema.org/draft/2020-12/schema`.

### Core Tools

| Tool | Purpose |
|------|---------|
| `gateway.catalog_search` | Search available tools, returns compact capability cards with small metadata such as title, icons, execution hints, and schema dialect, plus additive compact CLI hints and registry candidates when relevant |
| `gateway.describe` | Get detailed schema and richer metadata for a specific tool, including output schema, annotations, execution/task support, icons, and schema dialect |
| `gateway.invoke` | Call a downstream tool with argument validation, including task-augmented execution for task-capable tools |
| `gateway.refresh` | Reload backend configs and reconnect; refuses while requests or active MCP tasks are pending unless `force=true` |
| `gateway.health` | Get gateway and server health status |
| `gateway.config_status` | Read effective config and startup/auth status with source attribution |
| `gateway.get_startup_policy` | Read persisted `autoStart` and legacy `disableAutoStart` entries by source |
| `gateway.set_startup_policy` | Preview or explicitly apply `autoStart` add/remove/set operations against one selected source |

### Lifecycle Tools

| Tool | Purpose |
|------|---------|
| `gateway.connect_server` | Connect or start a known configured, manifest/provisioned, or registered discovered server |
| `gateway.disconnect_server` | Runtime-stop a server without editing `.mcp.json` or changing `autoStart` |
| `gateway.restart_server` | Runtime-stop then reconnect a server without changing persistent config |

### Capability Discovery Tools

| Tool | Purpose |
|------|---------|
| `gateway.request_capability` | Natural language capability matching that can return direct CLI guidance or MCP server candidates |
| `gateway.sync_environment` | Detect platform and available CLIs |
| `gateway.provision` | Install and start MCP servers on-demand |
| `gateway.update_server` | Update an MCP server package and reconnect it |
| `gateway.auth_connect` | Store API-key credentials or acknowledge URL-mode elicitation and retry provisioning |
| `gateway.submit_feedback` | Preview/submit technical PMCP feedback issues to GitHub |
| `gateway.provision_status` | Check installation progress |
| `gateway.search_registry` | Search the cached public MCP Registry metadata for external servers |
| `gateway.register_discovered_server` | Register a registry result for provisioning |

### Monitoring Tools

| Tool | Purpose |
|------|---------|
| `gateway.list_pending` | List pending tool invocations with health status |
| `gateway.cancel` | Cancel a pending tool invocation |
| `gateway.tasks_list` | List brokered downstream MCP tasks by opaque task ID |
| `gateway.tasks_get` | Get current status for one downstream MCP task |
| `gateway.tasks_result` | Fetch and process a downstream MCP task result |
| `gateway.tasks_cancel` | Cancel a downstream MCP task |

`gateway.refresh` is intentionally conservative in shared-service mode. If a
downstream request or active MCP task is in flight, refresh returns `ok=false`
without disconnecting or reconnecting servers. Use `gateway.list_pending` to
inspect active PMCP request IDs and `gateway.tasks_list` to inspect downstream
MCP task IDs, then retry with `force=true` only when cancelling that work is
acceptable.

`gateway.disconnect_server` and `gateway.restart_server` follow the same
shared-service disruption policy for the target server: they refuse while that
server has pending requests or active MCP tasks unless `force=true`. With
`force=true`, only pending requests and active tasks for the named server are
cancelled. These controls are runtime-only; they free local resources and update
live gateway state, but they do not edit `.mcp.json`, remove server definitions,
or change `autoStart`. In HTTP shared service mode, stopping or restarting a
downstream server can affect other clients using the same PMCP gateway.

MCP task IDs are downstream server identifiers and remain distinct from PMCP
pending request IDs such as `server::local_id`. Use `gateway.cancel` only for
PMCP request IDs from `gateway.list_pending`; use `gateway.tasks_cancel` for MCP
task IDs. Task records are transient in-memory gateway state. PMCP can bind
visibility to the server and requestor context it observes, but unauthenticated
local transports cannot provide cross-user authorization isolation.

### Auth And Elicitation

PMCP reports downstream authorization as structured, non-secret state. Gateway
outputs and health rows may include `auth_state` values of `none`,
`missing_auth`, `insufficient_scope`, `elicitation_required`, `policy_denied`, or
`unknown`, plus optional `next_step`, `auth_methods`, scope names, sanitized
metadata URLs, and URL-mode elicitation summaries.

Supported flows:

- Local API-key servers continue to use env-store credentials. When
  `gateway.provision` reports `auth_state="missing_auth"` and
  `auth_mode="api_key"`, call `gateway.auth_connect` with a credential and PMCP
  stores it in the selected user or project env file. User scope writes
  `~/.config/pmcp/pmcp.env`; project scope writes `<project>/.env.pmcp`. Project
  scope is useful for local development and CI workspaces, while user scope is
  better for credentials that should follow one operator across projects.
- Remote bearer headers use env placeholders such as
  `Authorization: Bearer ${REMOTE_API_TOKEN}`. PMCP resolves placeholders from
  process env, project env-store, and user env-store values, but status, doctor,
  health, and feedback output only show required or missing env var names, not
  the resolved header value.
- Tenant-aware remote header resolution uses a tenant-scoped credential file
  under the resolved project root. Tenant mode reads only that tenant's values
  and reports missing env var names without printing header values.
- Remote authorization discovery is diagnostic-only. PMCP can preserve and report
  OAuth Protected Resource Metadata, Authorization Server Metadata, OpenID
  Connect discovery, Client ID Metadata Document URLs, and declared scopes when a
  server or `WWW-Authenticate` challenge provides them.
- URL-mode elicitation is out of band. PMCP returns a sanitized URL and
  `elicitation_id`; complete that URL flow outside PMCP, then acknowledge it with
  `gateway.auth_connect(auth_mode="url_elicitation", elicitation_id=..., consent_acknowledged=true)`.

PMCP is not an authorization server and does not implement enterprise SSO,
Cross-App Access, DPoP, workload identity federation, or third-party refresh
token storage. Do not paste OAuth codes or third-party credentials into
URL-mode gateway calls.

### Subordinate MCP Updates

- `gateway.update_server` is the phase-1 update path for subordinate MCPs.
- `pmcp update <server>` and `pmcp update --all` call the same gateway update workflow.
- `gateway.describe`, `gateway.invoke`, and `gateway.provision` may return `update_warning` when a newer package version is detected.
- Background stale-version indexing is active — warnings are zero-latency via hourly pre-population.

### Feedback Telemetry

- PMCP can emit failure feedback hints and generate GitHub issue payload previews for agents.
- Telemetry is technical-only and warns before submission; payloads include PMCP/tool context.
- Disable permanently with `pmcp guidance --telemetry off`.

## Progressive Disclosure Workflow

PMCP follows a progressive disclosure pattern - start with natural language, get recommendations, drill down as needed.

### Step 1: Request a Capability

```
You: "I need to look up library documentation"

gateway.request_capability({ query: "library documentation" })
```

For local work where an installed native CLI is the right surface, PMCP returns
compact CLI guidance and does not execute the command:

```
gateway.request_capability({ query: "git commits", available_clis: ["git"] })
```

Returns:
```json
{
  "status": "use_cli",
  "message": "Use Bash/direct CLI with 'git'. PMCP is recommending the native command here; it is not executing the command or provisioning an MCP server for this path.",
  "cli": {
    "name": "git",
    "description": "Git version control CLI",
    "available": true,
    "help_command": ["git", "--help"],
    "examples": ["git status --short", "git log --oneline -5"],
    "reason": "Matched query against CLI keywords and examples."
  },
  "recommendation": "Run 'git' directly via Bash/direct CLI. Use gateway.request_capability again only if you need an MCP server."
}
```

After `status: "use_cli"`, use Bash/direct CLI. PMCP stops at guidance here:
it does not execute the command and does not fetch full native help output for
the normal compact path. If PMCP returns server candidates instead, continue
with MCP provisioning, `gateway.describe`, and `gateway.invoke`.

Returns:
```json
{
  "status": "candidates",
  "candidates": [{
    "name": "context7",
    "candidate_type": "server",
    "relevance_score": 0.95,
    "is_running": true,
    "reasoning": "Context7 provides up-to-date documentation for any package"
  }],
  "recommendation": "Use context7 - already running"
}
```

### Step 2: Search Available Tools

```
gateway.catalog_search({ query: "documentation" })
```

CLI recommendations are returned separately from MCP tool cards:

```json
gateway.catalog_search({ "query": "git" })
```

Returns:
```json
{
  "results": [{
    "tool_id": "github::list_issues",
    "server": "github",
    "tool_name": "list_issues",
    "short_description": "List issues in a repository",
    "tags": ["github", "git", "search"],
    "availability": "online",
    "risk_hint": "low"
  }],
  "total_available": 3,
  "truncated": false,
  "cli_hints": [{
    "name": "git",
    "description": "Git version control CLI",
    "available": true,
    "path": "/usr/bin/git",
    "help_command": ["git", "--help"],
    "examples": ["git status --short", "git log --oneline -5"],
    "reason": "Matched query against CLI name."
  }]
}
```

Use `cli_hints` as recommendations for Bash/direct CLI commands. They are not
MCP tools, do not appear in `results`, and cannot be passed to
`gateway.describe` or `gateway.invoke`. Start with either
`gateway.request_capability` or `gateway.catalog_search`; when PMCP returns
`use_cli` or matching `cli_hints`, that is enough context to switch to
Bash/direct CLI. Otherwise stay on the MCP path.

Registry-backed matches can appear as `registry_candidates` in
`gateway.catalog_search` or as `status="candidates"` from
`gateway.request_capability`. They are read-only discovery metadata from the
MCP Registry cache and may include package identifiers, transport, server-card
URLs, protected-resource metadata URLs, authorization-server metadata URLs,
declared scopes, and placeholder header names. PMCP does not install, connect,
or pass credentials for a registry result until you explicitly register and
provision the selected server.

### Step 3: Get Tool Details

```
gateway.describe({ tool_id: "context7::get-library-docs" })
```

### Step 4: Invoke the Tool

```
gateway.invoke({
  tool_id: "context7::get-library-docs",
  arguments: { libraryId: "/npm/react/19.0.0" }
})
```

### Offline Tool Discovery

When using `gateway.catalog_search`, you can discover tools from servers that haven't started yet:

```json
// Search all tools including offline/lazy servers
gateway.catalog_search({
  "query": "browser",
  "include_offline": true
})
```

This uses pre-cached tool descriptions from `.mcp-gateway/descriptions.yaml`. To refresh the cache:

```bash
pmcp refresh
```

**Note**: Cached tools show metadata only. Full schemas are available after the server starts (use `gateway.describe` to trigger lazy start).

The MCP Registry cache is stored separately under `.mcp-gateway`; PMCP uses the
cache when the public registry is unavailable. Registry candidates can coexist
with cached offline tool cards without changing `total_available`.

## Dynamic Server Provisioning

PMCP can install and start MCP servers on-demand from a curated manifest of 90+ servers.

### Example: Adding GitHub Support

```
You: "I need to manage GitHub issues"

gateway.request_capability({ query: "github issues" })
```

Returns (if not already configured):
```json
{
  "status": "candidates",
  "candidates": [{
    "name": "github",
    "candidate_type": "server",
    "is_running": false,
    "requires_api_key": true,
    "env_var": "GITHUB_PERSONAL_ACCESS_TOKEN",
    "env_instructions": "Create at https://github.com/settings/tokens with repo scope"
  }]
}
```

### Provisioning

```bash
# 1. Set API key (if required)
export GITHUB_PERSONAL_ACCESS_TOKEN=ghp_...

# 2. Provision via gateway
gateway.provision({ server_name: "github" })
```

## Optional Eager Startup

Packaged manifest servers do not start automatically. They are lazy by default:
PMCP can discover or provision them from the manifest, then connect on first use.

To eagerly start a server every time PMCP starts, list it in top-level
`autoStart`:

```json
{
  "autoStart": ["playwright", "context7"],
  "mcpServers": {}
}
```

Common opt-in choices:

| Server | Description | API Key |
|--------|-------------|---------|
| `playwright` | Browser automation - navigation, screenshots, DOM inspection | Not required |
| `context7` | Library documentation lookup - up-to-date docs for any package | Optional (for higher rate limits) |

Startup policy decisions are visible through `gateway.health` and live
`pmcp status --verbose`. Health rows keep the existing `name`, `status`,
`tool_count`, and `error` fields, and may also include:

| Field | Meaning |
|-------|---------|
| `startup_policy` | `eager`, `lazy`, `skipped`, or `unknown` |
| `startup_source` | Resolver source such as `project`, `user`, `manifest`, `configured`, or `auto_start` |
| `startup_skip_reason` | Machine-readable skip reason such as `policy_denied`, `missing_auth`, or `unknown_auto_start` |
| `startup_env_var` | Required environment variable name for missing-auth skips |
| `auth_state` | Machine-readable downstream auth state such as `missing_auth`, `insufficient_scope`, `elicitation_required`, or `policy_denied` |
| `next_step` | Non-secret suggested next action when an auth state needs operator action |

For persistent administration, use the config tools:

```json
gateway.config_status({})
gateway.get_startup_policy({})
gateway.set_startup_policy({
  "operation": "add",
  "names": ["playwright"],
  "source": "project"
})
```

`gateway.set_startup_policy` is preview-only by default. To write, select exactly
one `source` or `path` and pass both `"apply": true` and `"dry_run": false`.
The writer updates only top-level `autoStart`, preserves unrelated `.mcp.json`
keys and server definitions, writes atomically, and returns a refresh next step
instead of silently reconnecting servers. Diagnostics report stale `autoStart`,
legacy `disableAutoStart` conflicts, policy-denied rows, and missing-auth rows
without printing secret values.

PMCP negotiates the current MCP protocol version with downstream servers and
continues to connect to older supported servers. The local conformance matrix
covers negotiated status handling for `2024-11-05`, `2025-03-26`,
`2025-06-18`, and `2025-11-25`, with `2025-11-25` preferred for new
initialization attempts. `gateway.health` and `pmcp status --json` can include
the negotiated `protocol_version` and declared server capabilities when a
connected server reports them.

Modern MCP task support is conservative. PMCP forwards task-augmented tool calls
only when a tool advertises `execution.taskSupport` and the downstream server
advertises task capability. Required-task tools fail before dispatch if the
server does not advertise task support. Task records are transient gateway state,
not durable PMCP storage.

The tenant code-mode host contract in
`specs/tenant-code-mode-host-contract.md` freezes the PMCP/companion-server
boundary for future hosted sandbox execution. PMCP remains the broker; the
companion tenant server remains the execution authority.

Gateway observability is local and structured. `gateway.invoke` accepts trace
context through `_meta.traceparent`, `_meta.tracestate`, and `_meta.baggage` and
preserves those string values on PMCP-owned downstream request metadata. The
same keys are tolerated on HTTP requests. PMCP does not require or configure an
OpenTelemetry exporter.

`gateway.health` may include `gateway_diagnostics` and recent `audit_events`.
Diagnostics report transport/header compatibility, trace support, audit buffer
readiness, auth metadata presence, and rate-limit configuration without secret
values. Audit events are bounded in memory and include method/action, server or
tool identity, protocol version when known, task ID when present, outcome,
latency, auth state, and redacted error text.

PMCP's Streamable HTTP endpoint remains compatible with existing clients that
send no draft headers. It also tolerates `MCP-Protocol-Version`, `Mcp-Method`,
and `Mcp-Name` request headers for clients experimenting with draft MCP
transport conventions. These headers are compatibility inputs, not a promise
that PMCP implements every draft MCP extension.

Servers stopped with `gateway.disconnect_server` remain visible in health as
`offline` or `lazy` when PMCP still knows their configuration, and startup policy
observation fields are preserved.

Example missing-auth health row:

```json
{
  "name": "github",
  "status": "offline",
  "tool_count": 0,
  "startup_policy": "skipped",
  "startup_source": "manifest",
  "startup_skip_reason": "missing_auth",
  "startup_env_var": "GITHUB_PERSONAL_ACCESS_TOKEN"
}
```

## Available Servers

The manifest includes 90+ servers that can be provisioned on-demand:

### No API Key Required

| Server | Description |
|--------|-------------|
| `filesystem` | File operations - read, write, search |
| `memory` | Persistent knowledge graph |
| `fetch` | HTTP requests with robots.txt compliance |
| `sequential-thinking` | Problem solving through thought sequences |
| `git` | Git operations via MCP |
| `sqlite` | SQLite database operations |
| `time` | Timezone operations |
| `puppeteer` | Headless Chrome automation |

### Requires API Key

| Server | Description | Environment Variable |
|--------|-------------|---------------------|
| `github` | GitHub API - issues, PRs, repos | `GITHUB_PERSONAL_ACCESS_TOKEN` |
| `gitlab` | GitLab API - projects, MRs | `GITLAB_PERSONAL_ACCESS_TOKEN` |
| `slack` | Slack messaging | `SLACK_BOT_TOKEN` |
| `notion` | Notion workspace | `NOTION_TOKEN` |
| `linear` | Linear issue tracking | `LINEAR_API_KEY` |
| `postgres` | PostgreSQL database | `POSTGRES_URL` |
| `brave-search` | Web search | `BRAVE_API_KEY` |
| `google-drive` | Google Drive files | `GDRIVE_CREDENTIALS` |
| `sentry` | Error tracking | `SENTRY_AUTH_TOKEN` |
| `stripe` | Payments and billing | `STRIPE_SECRET_KEY` |
| `github-actions` | CI/CD workflows | `GITHUB_PERSONAL_ACCESS_TOKEN` |
| `datadog` | Monitoring and observability | `DATADOG_API_KEY` |
| `cloudflare` | Edge network and Workers | `CLOUDFLARE_API_TOKEN` |
| `figma` | Design files and components | `FIGMA_ACCESS_TOKEN` |
| `jira` | Issue tracking | `JIRA_API_TOKEN` |
| `airtable` | Spreadsheet database | `AIRTABLE_TOKEN` |
| `hubspot` | CRM and marketing | `HUBSPOT_ACCESS_TOKEN` |
| `twilio` | SMS and voice | `TWILIO_ACCOUNT_SID` |
| `...and 80+ more` | Use `gateway.catalog_search` to explore | — |

See `.env.example` for all supported environment variables.

## Code Execution Guidance

PMCP includes built-in guidance to encourage models to use code execution patterns, reducing context bloat and improving workflow efficiency.

### Guidance Layers

**L0 (MCP Instructions)**: Brief philosophy in server instructions (~30 tokens)
- "Write code to orchestrate tools - use loops, filters, conditionals"

**L1 (Code Hints)**: Ultra-terse hints in search results (~8-12 tokens/card)
- Single-word hints: "loop", "filter", "try/catch", "poll"

**L2 (Code Snippets)**: Minimal examples in describe output (~40-80 tokens, opt-in)
- 3-4 line code examples showing practical usage

**L3 (Methodology Resource)**: Full guide (lazy-loaded, 0 tokens)
- Accessible via `pmcp://guidance/code-execution` resource

### Guidance Configuration

Create `~/.claude/gateway-guidance.yaml`:

```yaml
guidance:
  level: "minimal"  # Options: "off", "minimal", "standard"

  layers:
    mcp_instructions: true   # L0 philosophy
    code_hints: true         # L1 hints
    code_snippets: false     # L2 examples (default: off)
    methodology_resource: true  # L3 guide
```

**Levels**:
- `minimal` (default): L0 + L1 (~200 tokens overhead)
- `standard`: L0 + L1 + L2 (~320 tokens overhead)
- `off`: No guidance

### View Guidance Status

```bash
pmcp guidance                 # Show configuration
pmcp guidance --show-budget  # Show token estimates
```

### Token Budget

- **Minimal mode**: ~200 tokens typical workflow (L0 + search)
- **Standard mode**: ~320 tokens (L0 + search + 1 describe)
- **80% reduction** vs loading all tool schemas upfront!

## Configuration

### Config Discovery

PMCP discovers MCP servers from:

1. **Project config**: `.mcp.json` in project root (highest priority)
2. **User config**: `~/.mcp.json` or `~/.claude/.mcp.json`
3. **Custom config**: Via `--config` flag or `PMCP_CONFIG` env var

### Adding Custom Servers

For MCP servers not in the manifest, add them to `~/.mcp.json`:

```json
{
  "autoStart": ["my-custom-server"],
  "mcpServers": {
    "my-custom-server": {
      "command": "node",
      "args": ["./my-server.js"],
      "env": {
        "API_KEY": "..."
      }
    }
  }
}
```

PMCP supports both local command-based and remote URL-based downstream entries from discovered config files. Entries in `mcpServers` make downstream servers available lazily/on demand; they do not by themselves mean the server should be eagerly started.

The top-level `autoStart` list controls explicit eager startup. Names can refer to
servers defined in `mcpServers` or packaged manifest entries such as `playwright`
and `context7`. Omit a server from `autoStart` to keep it lazy.

The legacy top-level `disableAutoStart` list remains supported for deployments
that temporarily enable `PMCP_LEGACY_MANIFEST_AUTOSTART=1`, but packaged PMCP
defaults no longer require it.

The same policy is available locally from the CLI:

```bash
pmcp config status --json
pmcp config startup-policy
pmcp config set-startup-policy add playwright --source project
pmcp config set-startup-policy add playwright --source project --apply
```

CLI mutation previews by default. `--apply` is required before writing.

Lazy Excalidraw example:

```json
{
  "mcpServers": {
    "excalidraw": {
      "type": "http",
      "url": "https://mcp.excalidraw.com/mcp"
    }
  }
}
```

Eager Excalidraw example:

```json
{
  "autoStart": ["excalidraw"],
  "mcpServers": {
    "excalidraw": {
      "type": "http",
      "url": "https://mcp.excalidraw.com/mcp"
    }
  }
}
```

#### Remote Downstream Servers

You can also configure downstream MCP servers over HTTP/SSE directly in `.mcp.json` using `type: "sse"` or `type: "http"` (or `type: "remote"` for generic remote transport):

```json
{
  "mcpServers": {
    "acme-sse": {
      "type": "sse",
      "url": "https://mcp.acme.dev/sse",
      "headers": {
        "Authorization": "Bearer ${ACME_MCP_TOKEN}",
        "X-Tenant": "${ACME_TENANT_ID}"
      }
    },
    "acme-http": {
      "type": "http",
      "url": "https://mcp.acme.dev/mcp",
      "headers": {
        "Authorization": "Bearer ${ACME_MCP_TOKEN}"
      }
    }
  }
}
```

- `url` should be the full remote endpoint for that server.
- `headers` values support `${ENV_VAR}` interpolation (Issue #40).
- Resolve those environment variables from your shell environment or `~/.config/pmcp/pmcp.env`.

**Important**: Don't add `pmcp` itself to this file. PMCP is configured
in your MCP client config, not in the downstream server list.

#### Tenant Code-Mode Server Registration

PMCP can broker a separate tenant code-mode MCP server as a normal downstream
server. The contract in `specs/tenant-code-mode-host-contract.md` defines the
boundary: PMCP discovers, invokes, monitors, truncates, and redacts through
gateway surfaces; the companion tenant server owns sandbox execution, tenant
authorization, logs, and artifacts. PMCP does not run scripts itself.

Register the hosted server in `.mcp.json` with the configured name
`tenant-code-mode`:

```json
{
  "mcpServers": {
    "tenant-code-mode": {
      "type": "streamable-http",
      "url": "https://tenant.example.com/mcp",
      "headers": {
        "Authorization": "Bearer ${TENANT_CODE_MODE_MCP_TOKEN}",
        "X-Tenant-ID": "${TENANT_CODE_MODE_TENANT_ID}"
      }
    }
  }
}
```

For local companion-server development, use a replaceable stdio command from
that server's checkout:

```json
{
  "mcpServers": {
    "tenant-code-mode": {
      "command": "/path/to/tenant-code-mode-server",
      "args": ["serve", "--transport", "stdio"]
    }
  }
}
```

The registration is lazy by default. Add `tenant-code-mode` to top-level
`autoStart` only when the operator wants PMCP to connect during startup.
Discovery and startup use the existing `gateway.request_capability`,
`gateway.catalog_search` with `include_offline: true`, `gateway.provision`, and
`gateway.invoke` flow.

Tenant runs use the existing task broker. Submit long-running work with
`gateway.invoke` and non-secret `task.metadata`, `task.ttl`,
`task.poll_interval`, `task.requestor_context`, and trace keys such as
`_meta.traceparent`; PMCP forwards those fields to the downstream server only
when the server and tool advertise task support. The returned downstream MCP
task ID is then used with `gateway.tasks_list`, `gateway.tasks_get`,
`gateway.tasks_result`, and `gateway.tasks_cancel`. Do not use PMCP request IDs
from `gateway.list_pending` or `gateway.cancel` for tenant task operations.
`gateway.tasks_result` continues to apply host-side truncation and optional
secret redaction to sandbox-shaped logs and diagnostics.

### Credential Scope Management (`pmcp secrets`)

PMCP stores secrets in environment files by scope:

- `user` scope: `~/.config/pmcp/pmcp.env`
- `project` scope: `<project_root>/.env.pmcp`

You can manage both scopes with `pmcp secrets`:

```bash
# Store a secret in user scope (shared by all projects)
pmcp secrets set API_TOKEN your-token --scope user

# Store a secret in project scope
pmcp secrets set API_TOKEN your-token --scope project --project /path/to/project

# Copy all user-scoped secrets into project scope
pmcp secrets sync --from-scope user --to-scope project --overwrite

# Copy project-scoped secrets into user scope
pmcp secrets sync --from-scope project --to-scope user --overwrite
```

Use scope-appropriate values such as `API_TOKEN` and keep the values in the generated `.env` files; PMCP and downstream MCP servers read from these files according to your active mode.

For service users, `~/.config/pmcp/pmcp.env` is ideal for shared tokens used by all sessions.

### Policy File

Create a policy file to control access and limits:

**~/.claude/gateway-policy.yaml**:
```yaml
servers:
  allowlist: []  # Empty = allow all
  denylist:
    - dangerous-server

tools:
  denylist:
    - "*::delete_*"
    - "*::drop_*"

limits:
  max_tools_per_server: 100
  max_output_bytes: 50000
  max_output_tokens: 4000

redaction:
  patterns:
    - "(api[_-]?key)[\\s]*[:=][\\s]*[\"']?([^\\s\"']+)"
    - "(password|secret)[\\s]*[:=][\\s]*[\"']?([^\\s\"']+)"
```

Tenant code-mode hosting uses the same policy fields. This example allows only
the tenant server, blocks a high-risk submission tool, bounds output, and adds a
tenant artifact redaction pattern without granting access to unrelated MCP
servers:

```yaml
servers:
  allowlist:
    - tenant-code-mode

tools:
  denylist:
    - "tenant-code-mode::run_script"
  allowlist:
    - "tenant-code-mode::get_*"
    - "tenant-code-mode::cancel_*"

limits:
  max_output_bytes: 50000
  max_output_tokens: 4000

redaction:
  patterns:
    - "TENANT_CODE_MODE_[A-Z_]+=[^\\s]+"
    - "artifact_token=[^\\s]+"
```

For hosted tenant auth, keep credentials in PMCP env storage or tenant-scoped
project storage and reference only placeholders from config:
`${TENANT_CODE_MODE_MCP_TOKEN}` and `${TENANT_CODE_MODE_TENANT_ID}`. Use
`pmcp secrets set ... --scope project` or `gateway.auth_connect` to populate
env-store values for non-tenant mode; tenant mode uses isolated per-tenant env
files derived from the resolved project root. PMCP diagnostics report missing
field or env-var names such as
`TENANT_CODE_MODE_MCP_TOKEN`; they must not print token values.

Hosted operators should require Bearer auth on `/mcp`, tune `--rate-limit` or
`PMCP_RATE_LIMIT` for the deployment, and keep `/health` and `/metrics` behind
network controls. `gateway.refresh`, `gateway.disconnect_server`, and
`gateway.restart_server` can disrupt in-flight downstream work unless forced by
policy; use downstream task IDs with `gateway.tasks_cancel` for tenant run
cancellation. PMCP task records are transient. Durable sandbox logs, artifacts,
tenant authorization, and artifact retention remain responsibilities of the
companion tenant server and its deployment controls.

### CLI Commands

```bash
# Start the gateway server (default)
pmcp

# Check server status
pmcp status
pmcp status --json              # JSON output
pmcp status --verbose           # Include startup policy details when available
pmcp status --server playwright # Filter by server

# View logs
pmcp logs
pmcp logs --follow              # Live tail
pmcp logs --tail 100            # Last 100 lines

# Refresh server connections
pmcp refresh
pmcp refresh --server github    # Refresh specific server
pmcp refresh --force            # Force reconnect all

# Initialize config (interactive)
pmcp init

# Render client setup snippets
pmcp setup
pmcp setup --client claude --mode stdio
pmcp setup --client opencode --mode http --write

# Run diagnostics for lock/mode/http checks
pmcp doctor
pmcp doctor --project /path/to/project

# Manage project/user secrets
pmcp secrets set API_TOKEN my-token --scope user
pmcp secrets sync --from-scope user --to-scope project --overwrite
```

### `pmcp doctor` (Recommended before/after upgrades)

Use `pmcp doctor` to diagnose common PMCP startup and connectivity issues. It checks:

- `lock`: detects singleton lock state and stale lock collisions at `~/.pmcp/gateway.lock`
- `mode`: detects local command-mode MCP config conflicts when a shared PMCP system service is running
- `http`: probes the unauthenticated `/health` endpoint derived from `PMCP_GATEWAY_URL` or `http://127.0.0.1:3344/mcp`
- `remote`: detects unresolved remote downstream header environment references
- `install`: detects conflicting `uv tool` and `pip --user` installs

Example:

```bash
pmcp doctor
```

If any checks fail, follow the command in the output and rerun `pmcp doctor`.

### Singleton Lock

By default, PMCP uses a global lock at `~/.pmcp/gateway.lock` to ensure only one gateway runs per user. This prevents multiple gateway instances from spawning duplicate downstream servers.

**Override the lock directory:**

```bash
# CLI flag
pmcp --lock-dir /custom/path

# Environment variable
export PMCP_LOCK_DIR=/custom/path
pmcp
```

**Per-project lock (not recommended):**

```bash
pmcp --lock-dir ./.mcp-gateway
```

## Deprecations

- `mcp-gateway` command naming is deprecated in documentation and examples.
- Use `pmcp` for all CLI commands going forward.
- Migration examples:
  - `mcp-gateway refresh --force` -> `pmcp refresh --force`
  - `mcp-gateway status --json` -> `pmcp status --json`

## Docker

```bash
# Using Docker
docker run -it --rm \
  -v ~/.mcp.json:/home/appuser/.mcp.json:ro \
  -v ~/.env:/app/.env:ro \
  ghcr.io/viperjuice/pmcp:latest

# Using Docker Compose
docker-compose up -d
```

## Development

```bash
# Clone the repo
git clone https://github.com/ViperJuice/pmcp
cd pmcp

# Install with uv (recommended)
uv sync --all-extras

# Run tests
uv run pytest

# Run with debug logging
uv run pmcp --debug
```

### Running Tests

```bash
# Run all tests
uv run pytest

# Run with coverage
uv run pytest --cov=pmcp

# Run specific test file
uv run pytest tests/test_policy.py -v
```

### Project Structure

```
pmcp/
├── src/pmcp/
│   ├── __init__.py
│   ├── __main__.py           # python -m pmcp entry
│   ├── cli.py                # CLI commands (status, logs, init, refresh)
│   ├── server.py             # MCP server implementation
│   ├── config/
│   │   └── loader.py         # Config discovery (.mcp.json)
│   ├── client/
│   │   └── manager.py        # Downstream server connections
│   ├── policy/
│   │   └── policy.py         # Allow/deny lists
│   ├── tools/
│   │   └── handlers.py       # Gateway tool implementations
│   ├── manifest/
│   │   ├── manifest.yaml     # Server manifest (90+ servers)
│   │   ├── loader.py         # Manifest loading
│   │   ├── installer.py      # Server provisioning
│   │   └── environment.py    # Platform/CLI detection
│   └── baml_client/          # BAML-generated client (used for structured parsing; no outbound LLM calls since v1.8.0)
├── tests/                    # 310+ tests
├── Dockerfile
├── docker-compose.yml
├── .env.example
├── pyproject.toml
└── README.md
```

## Troubleshooting

### Server Won't Connect

```bash
pmcp status
pmcp logs --level debug
pmcp refresh --force
```

### Missing API Key

```bash
# Check which key is needed
pmcp status --server github

# Set the key
export GITHUB_PERSONAL_ACCESS_TOKEN=ghp_...
```

### Tool Invocation Fails

```
gateway.catalog_search({ query: "tool-name" })
gateway.describe({ tool_id: "server::tool-name" })
gateway.list_pending()
```

If `gateway.refresh` reports pending requests or active MCP tasks, inspect them
with `gateway.list_pending()` and `gateway.tasks_list()`, or retry refresh with
`force=true` to cancel them before reloading server configuration.

If `gateway.disconnect_server` or `gateway.restart_server` reports pending
requests or active MCP tasks, inspect `gateway.list_pending(server="<name>")`
and `gateway.tasks_list(server_name="<name>")`, or retry with `force=true` to
cancel only that server's pending work.

## License

MIT
