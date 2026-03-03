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

- **11 stable meta-tools** (not the 50+ underlying tools)
- **Auto-starts** essential servers (Playwright, Context7) with no configuration
- **Dynamically provisions** new servers on-demand from a manifest of 25+
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

# With LLM-enhanced features (optional, see below)
uv pip install pmcp[llm]
```

### Advanced LLM Features (Optional)

PMCP can use an LLM for smarter capability matching and summarization. Without an API key, it falls back to keyword matching and templates.

**Features enabled with LLM:**

| Feature | Without API Key | With API Key |
|---------|-----------------|--------------|
| Capability matching | Keyword-based | Semantic understanding |
| Tool summaries | Static templates | LLM-generated descriptions |
| Code snippets | Static examples | Dynamic, context-aware examples |

**Setup:**

1. Get a free API key from [Groq Console](https://console.groq.com/keys)
2. Add to your `.env` file:

```bash
# In your project's .env file (or ~/.env)
GROQ_API_KEY=gsk_your_groq_api_key_here
```

3. Install with LLM support:

```bash
uv pip install pmcp[llm]
```

PMCP uses [BAML](https://docs.boundaryml.com/) with Groq's fast inference API for sub-second LLM responses. The LLM features are entirely optional - PMCP works fully without them.

### Configure with `pmcp setup`

PMCP includes a wizard-style helper that can render ready-to-use MCP client config for Claude and OpenCode.

Use `pmcp setup` to print the generated config:

```bash
pmcp setup --client claude --mode stdio    # Claude local stdio
pmcp setup --client claude --mode sse      # Claude shared-service SSE
pmcp setup --client opencode --mode stdio  # OpenCode local stdio
pmcp setup --client opencode --mode sse    # OpenCode shared-service SSE
```

Write directly into your client config with `--write`:

```bash
pmcp setup --client claude --mode sse --write
```

Without `--write`, `pmcp setup` prints the config so you can paste it into:
- Claude: `~/.mcp.json`
- OpenCode: `~/.config/opencode/opencode.json`

Use SSE mode when running one shared PMCP service for multiple sessions/clients. Use stdio mode for single-process local testing.

### Shared Service Mode (Manual)

If you prefer manual config, point each client to the shared SSE endpoint:

```json
{
  "mcpServers": {
    "gateway": {
      "type": "sse",
      "url": "http://127.0.0.1:3344/sse"
    }
  }
}
```

Why this mode: PMCP uses a singleton lock (`~/.pmcp/gateway.lock`), so multiple local launches can conflict. One shared service avoids lock collisions and keeps tool state consistent.

Quick verification:

```bash
systemctl --user is-active pmcp
curl -sS -D - http://127.0.0.1:3344/sse -o /dev/null
```

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
│  • 11 meta-tools (catalog, invoke, provision, etc.)         │
│  • Progressive disclosure (compact cards → full schemas)    │
│  • Policy enforcement (allow/deny lists)                    │
└────────────────────────────┬────────────────────────────────┘
                             │
        ┌────────────────────┼────────────────────┐
        ▼                    ▼                    ▼
┌───────────────┐  ┌─────────────────┐  ┌─────────────────┐
│  Auto-Start   │  │    Manifest     │  │  Custom Servers │
│  (Playwright, │  │   (25+ servers  │  │  (your own MCP  │
│   Context7)   │  │   on-demand)    │  │  servers)       │
└───────────────┘  └─────────────────┘  └─────────────────┘
```

**Key principle**: Users configure ONLY `pmcp` in Claude Code.
The gateway discovers and manages all other servers.

### Why Single-Gateway?

1. **No context bloat** - Claude sees 11 tools, not 50+
2. **No restarts** - Provision new servers without restarting Claude Code
3. **Consistent interface** - All tools accessed via `gateway.invoke`
4. **Policy control** - Centralized allow/deny rules

## Gateway Tools

The gateway exposes **11 meta-tools** organized into three categories:

### Core Tools

| Tool | Purpose |
|------|---------|
| `gateway.catalog_search` | Search available tools, returns compact capability cards |
| `gateway.describe` | Get detailed schema for a specific tool |
| `gateway.invoke` | Call a downstream tool with argument validation |
| `gateway.refresh` | Reload backend configs and reconnect |
| `gateway.health` | Get gateway and server health status |

### Capability Discovery Tools

| Tool | Purpose |
|------|---------|
| `gateway.request_capability` | Natural language capability matching with CLI preference |
| `gateway.sync_environment` | Detect platform and available CLIs |
| `gateway.provision` | Install and start MCP servers on-demand |
| `gateway.provision_status` | Check installation progress |

### Monitoring Tools

| Tool | Purpose |
|------|---------|
| `gateway.list_pending` | List pending tool invocations with health status |
| `gateway.cancel` | Cancel a pending tool invocation |

## Progressive Disclosure Workflow

PMCP follows a progressive disclosure pattern - start with natural language, get recommendations, drill down as needed.

### Step 1: Request a Capability

```
You: "I need to look up library documentation"

gateway.request_capability({ query: "library documentation" })
```

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

## Dynamic Server Provisioning

PMCP can install and start MCP servers on-demand from a curated manifest of 25+ servers.

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

## Auto-Start Servers

These servers start automatically (no configuration required):

| Server | Description | API Key |
|--------|-------------|---------|
| `playwright` | Browser automation - navigation, screenshots, DOM inspection | Not required |
| `context7` | Library documentation lookup - up-to-date docs for any package | Optional (for higher rate limits) |

## Available Servers

The manifest includes 25+ servers that can be provisioned on-demand:

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

PMCP supports both local command-based and remote URL-based downstream entries from discovered config files.

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

### CLI Commands

```bash
# Start the gateway server (default)
pmcp

# Check server status
pmcp status
pmcp status --json              # JSON output
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
pmcp setup --client opencode --mode sse --write

# Run diagnostics for lock/mode/SSE checks
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
- `sse`: probes discovered SSE endpoints in local `.mcp.json` and reports health

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
│   │   ├── manifest.yaml     # Server manifest (25+ servers)
│   │   ├── loader.py         # Manifest loading
│   │   ├── installer.py      # Server provisioning
│   │   └── environment.py    # Platform/CLI detection
│   └── baml_client/          # BAML-generated LLM client (optional)
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

## License

MIT
