# Contributing to PMCP

Thank you for your interest in contributing to PMCP (Progressive MCP)!

## Development Setup

```bash
# Clone the repository
git clone https://github.com/Consiliency/pmcp
cd pmcp

# Install with uv (recommended; CI tests Python 3.10, 3.11 and 3.12)
uv sync --all-extras -p 3.10

# Or with pip (the tests need the http extra as well as dev)
pip install -e ".[all]"
```

## Running Tests

```bash
# Run all tests (live and slow tests are deselected by default)
uv run pytest

# What CI runs (coverage must stay at or above 60%)
uv run pytest tests/ -v --cov=pmcp --cov-report=term-missing

# Run with coverage
uv run pytest --cov=pmcp --cov-report=term-missing

# Run specific test file
uv run pytest tests/test_policy.py -v

# Run integration tests (need MCP servers available via config or manifest)
uv run pytest tests/test_integration.py -v

# Run the slow tier (excluded by default: the redactor's large
# differentials and regex sweeps)
uv run pytest -m 'slow and not live'
```

### Timing in tests

Never assert an upper bound on elapsed wall time (`assert elapsed < 0.2`) — it
fails on a loaded runner even when the code is correct, and it is the whole of
this suite's historical flake. Wait for the property instead: `eventually` from
`tests/_timing.py` polls until it holds, and `Rendezvous` proves that several
coroutines were in flight together. A `timeout=` on either is a hang guard so a
broken test fails loudly, never a measurement to tighten. Lower bounds on a real
timer (`assert elapsed > 8` after `sleep(8.2)`) are safe. See the module
docstring in `tests/_timing.py`.

## Adding a Server to the Manifest

The manifest (`src/pmcp/manifest/manifest.yaml`) contains 90+ MCP servers
that can be provisioned on-demand via `gateway.provision`.

### Steps to Add a New Server

1. Edit `src/pmcp/manifest/manifest.yaml`
2. Add an entry under `servers:`:

```yaml
my-server:
  description: "Brief description of what this server does"
  keywords: [keyword1, keyword2, keyword3]
  install:
    mac: ["npx", "-y", "@scope/server-name"]
    linux: ["npx", "-y", "@scope/server-name"]
    windows: ["npx.cmd", "-y", "@scope/server-name"]
  command: "npx"
  args: ["-y", "@scope/server-name"]
  requires_api_key: true          # Set to false if no API key needed
  env_var: "MY_SERVER_API_KEY"    # Required if requires_api_key is true
  env_instructions: "Get your API key from https://..."
  # Optional: names of extra_env variables whose presence (e.g. a self-hosted
  # base URL) makes the credential above unnecessary. Both parties must act —
  # you declare the field here, and an operator must separately supply the
  # named variable via extra_env or an overlay server_env patch — so the field
  # alone never relaxes anything. An unset, self-referencing, or placeholder
  # value fails closed and the credential stays required.
  api_key_optional_when: ["MY_SERVER_BASE_URL"]
  auto_start: false               # Leave false; see Auto-Start below
```

3. Add tests in `tests/test_manifest.py`
4. Run tests and submit a PR

### Auto-Start Servers

No shipped manifest entry sets `auto_start: true`, and new entries should not:
servers start lazily, on first use. The manifest's `auto_start` field is a
legacy switch that is honoured only when `PMCP_LEGACY_MANIFEST_AUTOSTART=1` is
set. An operator who wants a server started eagerly lists it under `autoStart`
in their `.mcp.json` (or uses `pmcp config set-startup-policy`).

## Code Style

- **Formatting**: Use `ruff format` for consistent formatting
- **Linting**: Use `ruff check` for linting
- **Type hints**: Required for all public functions (mypy checked in CI)
- **Tests**: Required for new features
- **Dependency bounds**: Set them by installing, not by reading source. `uv.lock`
  pins development checkouts, so the full suite can pass while a declared bound
  is wrong in both directions. Two CI jobs cover this: `install-smoke` resolves
  fresh with no lockfile (the ceiling), and `min-version-smoke` installs pinned
  at exactly the declared floor and serves a real downstream tool call through
  the booted gateway (the floor). If you change a bound in `pyproject.toml`, run
  `uv lock` in the same commit.

```bash
# Format code
uv run ruff format src/ tests/

# Check formatting and linting (as CI does)
uv run ruff format --check src/ tests/
uv run ruff check src/ tests/

# Check types (as CI does)
uv run mypy src/pmcp --exclude baml_client
```

## Architecture Overview

See the [README Architecture section](README.md#architecture) for a visual overview.

### Key Modules

| Module | Purpose |
|--------|---------|
| `server.py` | MCP server implementation, tool handlers |
| `client/manager.py` | Downstream server connections (parallel, retry) |
| `config/loader.py` | Config discovery from `.mcp.json` files |
| `manifest/loader.py` | Server manifest loading |
| `manifest/installer.py` | On-demand server provisioning |
| `manifest/matcher.py` | Natural language capability matching |
| `policy/policy.py` | Allow/deny lists for servers, tools, resources, prompts |
| `tools/handlers.py` | Gateway tool implementations |

### Request Flow

1. Claude Code calls `gateway.invoke({ tool_id, arguments })`
2. Gateway parses tool_id to extract server name and tool name
3. Gateway checks policy for tool access
4. Gateway forwards request to downstream server
5. Response is truncated/redacted per policy
6. Result returned to Claude Code

## Pull Request Guidelines

1. **Create a branch** from `main`
2. **Write tests** for new functionality
3. **Run the full test suite** before submitting
4. **Update documentation** if adding features
5. **Keep PRs focused** - one feature or fix per PR

## Reporting Issues

Please include:
- Python version
- OS and version
- Steps to reproduce
- Expected vs actual behavior
- Relevant logs (`pmcp logs --level debug`)

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
