#!/usr/bin/env python3
"""PMCP CLI."""

from __future__ import annotations

import argparse
import asyncio
import importlib.metadata
import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

from dotenv import load_dotenv
from pmcp.cli_commands.doctor import collect_remote_header_diagnostics
from pmcp.cli_commands.secrets import (
    run_secrets_check,
    run_secrets_set,
    run_secrets_sync,
)


from logging.handlers import RotatingFileHandler

LOG_DIR = Path(".pmcp/logs")
LOG_FILE = LOG_DIR / "gateway.log"


def setup_logging(level: str, log_to_file: bool = True) -> None:
    """Configure logging with optional file output."""
    log_level = getattr(logging, level.upper(), logging.INFO)
    log_format = "[%(asctime)s] [%(levelname)s] %(message)s"
    date_format = "%Y-%m-%dT%H:%M:%S"

    # Log to stderr to avoid interfering with MCP stdio
    logging.basicConfig(
        level=log_level,
        format=log_format,
        datefmt=date_format,
        stream=sys.stderr,
    )

    # Also log to file for later viewing with 'pmcp logs'
    if log_to_file:
        try:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            file_handler = RotatingFileHandler(
                LOG_FILE,
                maxBytes=1024 * 1024,  # 1MB
                backupCount=5,
            )
            file_handler.setLevel(log_level)
            file_handler.setFormatter(
                logging.Formatter(log_format, datefmt=date_format)
            )
            logging.getLogger().addHandler(file_handler)
        except Exception:
            # If we can't write logs, continue without file logging
            pass


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    examples = """Examples:
  pmcp
  pmcp --transport http --host 127.0.0.1 --port 3344
  pmcp setup --client claude --mode stdio
  pmcp setup --client claude --mode sse --write
  pmcp setup --client opencode --mode sse --write
  pmcp doctor
  pmcp secrets set API_TOKEN my-token --scope user
  pmcp secrets sync --from-scope user --to-scope project --overwrite
  pmcp status --json
  pmcp refresh --force

Environment overrides:
  PMCP_CONFIG, PMCP_POLICY, PMCP_LOG_LEVEL,
  PMCP_TRANSPORT, PMCP_HOST, PMCP_PORT, PMCP_LOCK_DIR
"""

    parser = argparse.ArgumentParser(
        description="PMCP - Progressive MCP: Minimal context bloat with on-demand tool discovery",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=examples,
    )

    # Create subparsers for commands
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # Default: run server (no subcommand needed)
    parser.add_argument(
        "-p",
        "--project",
        type=Path,
        help="Project root directory (for .mcp.json discovery)",
    )
    parser.add_argument(
        "-c",
        "--config",
        type=Path,
        help="Custom MCP config file path",
    )
    parser.add_argument(
        "--policy",
        type=Path,
        help="Policy file path (YAML or JSON)",
    )
    parser.add_argument(
        "-l",
        "--log-level",
        choices=["debug", "info", "warn", "error"],
        default="info",
        help="Log level (default: info)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Only show errors",
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "http"],
        default="stdio",
        help="Transport mode (default: stdio)",
    )
    parser.add_argument(
        "--host",
        type=str,
        default="127.0.0.1",
        help="HTTP bind address (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=3344,
        help="HTTP port (default: 3344)",
    )
    parser.add_argument(
        "--lock-dir",
        type=Path,
        default=None,
        help="Directory for singleton lock file. Default: ~/.pmcp (global per-user lock)",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"pmcp {importlib.metadata.version('pmcp')}",
    )

    # Refresh command
    refresh_parser = subparsers.add_parser(
        "refresh",
        help="Refresh capability descriptions for MCP servers",
        description="Pre-generate L1/L2 descriptions for MCP servers. "
        "This avoids LLM calls on every startup.",
    )
    refresh_parser.add_argument(
        "--server",
        "-s",
        type=str,
        help="Refresh only this server (default: all)",
    )
    refresh_parser.add_argument(
        "--force",
        "-f",
        action="store_true",
        help="Force refresh even if not stale",
    )
    refresh_parser.add_argument(
        "--check-versions",
        action="store_true",
        help="Check for package version updates",
    )
    refresh_parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path(".pmcp"),
        help="Cache directory (default: .pmcp)",
    )
    refresh_parser.add_argument(
        "-l",
        "--log-level",
        choices=["debug", "info", "warn", "error"],
        default="info",
        help="Log level (default: info)",
    )

    # Status command
    status_parser = subparsers.add_parser(
        "status",
        help="Show gateway and server status",
        description="Display status of connected MCP servers and pending requests.",
    )
    status_parser.add_argument(
        "--json",
        action="store_true",
        help="Output as JSON",
    )
    status_parser.add_argument(
        "--server",
        "-s",
        type=str,
        help="Show only specific server",
    )
    status_parser.add_argument(
        "--pending",
        action="store_true",
        help="Show pending requests",
    )
    status_parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Show detailed information",
    )
    status_parser.add_argument(
        "-p",
        "--project",
        type=Path,
        help="Project root directory (for .mcp.json discovery)",
    )
    status_parser.add_argument(
        "-c",
        "--config",
        type=Path,
        help="Custom MCP config file path",
    )
    status_parser.add_argument(
        "--policy",
        type=Path,
        help="Policy file path (YAML or JSON)",
    )
    status_parser.add_argument(
        "-l",
        "--log-level",
        choices=["debug", "info", "warn", "error"],
        default="warn",  # Default to warn for status command
        help="Log level (default: warn)",
    )

    # Logs command
    logs_parser = subparsers.add_parser(
        "logs",
        help="View gateway logs",
        description="View recent gateway log output.",
    )
    logs_parser.add_argument(
        "--follow",
        "-f",
        action="store_true",
        help="Follow log output (like tail -f)",
    )
    logs_parser.add_argument(
        "--tail",
        "-n",
        type=int,
        default=50,
        help="Number of lines to show (default: 50)",
    )
    logs_parser.add_argument(
        "--level",
        choices=["debug", "info", "warn", "error"],
        help="Filter by log level",
    )
    logs_parser.add_argument(
        "--server",
        "-s",
        type=str,
        help="Filter by server name",
    )

    # Init command
    init_parser = subparsers.add_parser(
        "init",
        help="Initialize PMCP configuration",
        description="Create a .mcp.json configuration file interactively.",
    )
    init_parser.add_argument(
        "--project",
        "-p",
        type=Path,
        help="Project directory (default: current directory)",
    )
    init_parser.add_argument(
        "--force",
        "-f",
        action="store_true",
        help="Overwrite existing configuration",
    )

    # Setup command
    setup_parser = subparsers.add_parser(
        "setup",
        help="Render PMCP client config",
        description="Render PMCP config for Claude/OpenCode; optionally write it.",
    )
    setup_parser.add_argument(
        "--mode",
        choices=["stdio", "sse"],
        default="sse",
        help="Connection mode to configure (default: sse)",
    )
    setup_parser.add_argument(
        "--client",
        choices=["claude", "opencode"],
        default="claude",
        help="Client config format to render (default: claude)",
    )
    setup_parser.add_argument(
        "--write",
        action="store_true",
        help="Write merged config to the client config file",
    )

    # Guidance command
    guidance_parser = subparsers.add_parser(
        "guidance",
        help="Show code execution guidance configuration",
        description="Display current guidance settings and token budget.",
    )
    guidance_parser.add_argument(
        "--show-budget",
        action="store_true",
        help="Show estimated token budget for current config",
    )

    # Doctor command
    doctor_parser = subparsers.add_parser(
        "doctor",
        help="Diagnose PMCP configuration conflicts",
        description="Detect lock/mode/SSE issues and print remediation steps.",
    )
    doctor_parser.add_argument(
        "-p",
        "--project",
        type=Path,
        help="Project root directory (defaults to auto-discovery)",
    )
    doctor_parser.add_argument(
        "--timeout",
        type=float,
        default=3.0,
        help="SSE probe timeout in seconds (default: 3.0)",
    )
    doctor_parser.add_argument(
        "-l",
        "--log-level",
        choices=["debug", "info", "warn", "error"],
        default="warn",
        help="Log level (default: warn)",
    )

    # Secrets command
    secrets_parser = subparsers.add_parser(
        "secrets",
        help="Manage PMCP secret environment files",
        description="Set, sync, and check secrets in user/project PMCP .env files.",
    )
    secrets_subparsers = secrets_parser.add_subparsers(
        dest="secrets_command",
        required=True,
        help="Secrets subcommands",
    )

    secrets_set_parser = secrets_subparsers.add_parser(
        "set",
        help="Set a secret value in PMCP env file",
    )
    secrets_set_parser.add_argument("key", type=str, help="Secret key name")
    secrets_set_parser.add_argument("value", type=str, help="Secret value")
    secrets_set_parser.add_argument(
        "--scope",
        choices=["user", "project"],
        default="project",
        help="Where to store the secret (default: project)",
    )
    secrets_set_parser.add_argument(
        "--project",
        type=Path,
        help="Project root directory (for project scope)",
    )

    secrets_sync_parser = secrets_subparsers.add_parser(
        "sync",
        help="Sync secrets between PMCP env scopes",
    )
    secrets_sync_parser.add_argument(
        "--from-scope",
        choices=["user", "project"],
        default="user",
        help="Source scope (default: user)",
    )
    secrets_sync_parser.add_argument(
        "--to-scope",
        choices=["user", "project"],
        default="project",
        help="Target scope (default: project)",
    )
    secrets_sync_parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing keys in target scope",
    )
    secrets_sync_parser.add_argument(
        "--project",
        type=Path,
        help="Project root directory (for project scope)",
    )

    secrets_check_parser = secrets_subparsers.add_parser(
        "check",
        help="Check required secrets and availability",
    )
    secrets_check_parser.add_argument(
        "--project",
        type=Path,
        help="Project root directory (for project scope)",
    )

    return parser.parse_args()


async def run_refresh(args: argparse.Namespace) -> None:
    """Run the refresh command."""
    from pmcp.manifest.refresher import (
        check_staleness,
        get_cache_path,
        refresh_all,
    )

    setup_logging(args.log_level)
    logger = logging.getLogger(__name__)

    cache_path = get_cache_path(args.cache_dir)

    if args.check_versions:
        # Just check for updates
        logger.info("Checking for package version updates...")
        stale = await check_staleness()

        if not stale:
            print("All cached descriptions are up to date.")
        else:
            print(f"Found {len(stale)} servers with newer versions:")
            for name, (old, new) in stale.items():
                print(f"  {name}: {old} -> {new}")
            print("\nRun 'pmcp refresh --force' to update.")
        return

    # Refresh descriptions
    servers = [args.server] if args.server else None

    logger.info("Refreshing capability descriptions...")
    if servers:
        print(f"Refreshing server: {servers[0]}")
    else:
        print("Refreshing all servers in manifest...")

    try:
        cache = await refresh_all(
            cache_path=cache_path,
            force=args.force,
            servers=servers,
        )

        print(f"\nRefreshed {len(cache.servers)} servers:")
        for name, desc in cache.servers.items():
            print(f"  {name}: {len(desc.tools)} tools (v{desc.version})")

        print(f"\nCache saved to: {cache_path}")

    except Exception as e:
        logger.error(f"Refresh failed: {e}")
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


async def run_status(args: argparse.Namespace) -> None:
    """Display gateway and server status."""
    import json
    import time
    from datetime import datetime

    from pmcp.client.manager import ClientManager
    from pmcp.config.loader import load_configs
    from pmcp.identity import filter_self_references
    from pmcp.policy.policy import PolicyManager
    from pmcp.types import ServerStatusEnum

    setup_logging(args.log_level)
    logger = logging.getLogger(__name__)

    # Initialize components
    policy_path = args.policy if hasattr(args, "policy") else None
    policy_manager = PolicyManager(policy_path)
    client_manager = ClientManager(
        max_tools_per_server=policy_manager.get_max_tools_per_server()
    )

    # Load configs
    project_root = args.project if hasattr(args, "project") else None
    config_path = args.config if hasattr(args, "config") else None
    configs = load_configs(project_root=project_root, custom_config_path=config_path)

    # Exclude self-referential gateway entries (e.g. pmcp/mcp-gateway)
    # so `pmcp status` only reports downstream servers.
    configs = filter_self_references(configs)

    # Filter by policy
    allowed_configs = [c for c in configs if policy_manager.is_server_allowed(c.name)]

    if not allowed_configs:
        if args.json:
            print(
                json.dumps(
                    {"servers": [], "tools": 0, "message": "No servers configured"}
                )
            )
        else:
            print("No MCP servers configured.")
        return

    # Connect to servers
    logger.info(f"Connecting to {len(allowed_configs)} servers...")
    await client_manager.connect_all(allowed_configs)

    try:
        statuses = client_manager.get_all_server_statuses()
        tools = client_manager.get_all_tools()
        revision_id, last_refresh = client_manager.get_registry_meta()

        # Filter by server if specified
        if args.server:
            statuses = [s for s in statuses if s.name == args.server]
            tools = [t for t in tools if t.server_name == args.server]

        if args.json:
            # JSON output
            output = {
                "revision_id": revision_id,
                "last_refresh_ts": last_refresh,
                "last_refresh_iso": datetime.fromtimestamp(last_refresh).isoformat(),
                "servers": [
                    {
                        "name": s.name,
                        "status": s.status.value,
                        "tool_count": s.tool_count,
                        "last_error": s.last_error,
                        "pending_requests": s.pending_request_count,
                        "avg_response_time_ms": s.avg_response_time_ms,
                    }
                    for s in statuses
                ],
                "total_tools": len(tools),
            }

            if args.pending:
                pending = client_manager.get_pending_requests(args.server)
                output["pending_requests"] = [
                    {
                        "request_id": f"{p.server_name}::{p.request_id}",
                        "tool_id": p.tool_id,
                        "elapsed_seconds": time.time() - p.started_at,
                        "state": client_manager.get_request_state(p).value,
                    }
                    for p in pending
                ]

            print(json.dumps(output, indent=2))
        else:
            # Human-readable output
            online = sum(1 for s in statuses if s.status == ServerStatusEnum.ONLINE)
            offline = len(statuses) - online

            print("PMCP Status")
            print("==================\n")

            if statuses:
                print(f"Servers ({online} online, {offline} offline):")
                for s in statuses:
                    if s.status == ServerStatusEnum.ONLINE:
                        icon = "\u2713"  # checkmark
                        avg_time = (
                            f"{s.avg_response_time_ms:.0f}ms"
                            if s.avg_response_time_ms
                            else "<1s"
                        )
                        details = f"{s.tool_count:>3} tools   {avg_time} avg"
                    else:
                        icon = "\u2717"  # x mark
                        details = f"({s.last_error or s.status.value})"

                    print(f"  {icon} {s.name:<16} {s.status.value:<10} {details}")
            else:
                print("No servers found.")

            print(f"\nTools: {len(tools)} indexed")

            # Format last refresh time
            elapsed = time.time() - last_refresh
            if elapsed < 60:
                time_str = "just now"
            elif elapsed < 3600:
                time_str = f"{int(elapsed / 60)} minutes ago"
            else:
                time_str = f"{int(elapsed / 3600)} hours ago"
            print(f"Last refresh: {time_str}")

            if args.pending:
                pending = client_manager.get_pending_requests(args.server)
                if pending:
                    print(f"\nPending Requests ({len(pending)}):")
                    for p in pending:
                        elapsed_s = time.time() - p.started_at
                        state = client_manager.get_request_state(p)
                        warn = " \u26a0" if state.value == "stalled" else ""
                        print(f"  {p.tool_id}  {elapsed_s:.1f}s  [{state.value}]{warn}")
                else:
                    print("\nNo pending requests.")

            if args.verbose:
                print(f"\nRevision: {revision_id}")

    finally:
        # Disconnect from all servers
        await client_manager.disconnect_all()


async def run_logs(args: argparse.Namespace) -> None:
    """View gateway logs."""

    if not LOG_FILE.exists():
        print("No log file found. Start the gateway first to generate logs.")
        print(f"Log file location: {LOG_FILE}")
        return

    def filter_line(line: str) -> bool:
        """Check if line matches filters."""
        if args.level:
            level_upper = args.level.upper()
            if f"[{level_upper}]" not in line:
                return False
        if args.server:
            if args.server not in line:
                return False
        return True

    def read_last_lines(n: int) -> list[str]:
        """Read last n lines from log file."""
        with open(LOG_FILE) as f:
            lines = f.readlines()
        return [line.rstrip() for line in lines[-n:] if filter_line(line)]

    # Show initial lines
    lines = read_last_lines(args.tail)
    for line in lines:
        print(line)

    # Follow mode
    if args.follow:
        print("\n--- Following logs (Ctrl+C to stop) ---\n")
        try:
            last_pos = LOG_FILE.stat().st_size
            while True:
                await asyncio.sleep(0.5)
                current_size = LOG_FILE.stat().st_size
                if current_size > last_pos:
                    with open(LOG_FILE) as f:
                        f.seek(last_pos)
                        new_lines = f.readlines()
                        for line in new_lines:
                            if filter_line(line):
                                print(line.rstrip())
                    last_pos = current_size
                elif current_size < last_pos:
                    # File was rotated
                    last_pos = 0
        except KeyboardInterrupt:
            print("\nStopped following logs.")


async def run_init(args: argparse.Namespace) -> None:
    """Initialize PMCP configuration."""
    import json

    from pmcp.manifest.loader import load_manifest

    project_dir = args.project or Path.cwd()
    config_path = project_dir / ".mcp.json"

    # Check if config already exists
    if config_path.exists() and not args.force:
        print(f"Configuration already exists: {config_path}")
        print("Use --force to overwrite.")
        return

    print("PMCP Configuration\n")

    # Load manifest to get available servers
    try:
        manifest = load_manifest()
        available_servers = list(manifest.servers.keys())
    except Exception:
        available_servers = []
        print("Warning: Could not load server manifest.")

    # Common servers to suggest
    common_servers = [
        ("filesystem", "@modelcontextprotocol/server-filesystem", None),
        (
            "github",
            "@modelcontextprotocol/server-github",
            "GITHUB_PERSONAL_ACCESS_TOKEN",
        ),
        ("postgres", "@modelcontextprotocol/server-postgres", "POSTGRES_URL"),
        ("sqlite", "@modelcontextprotocol/server-sqlite", None),
        ("puppeteer", "@anthropics/mcp-server-puppeteer", None),
    ]

    selected_servers: dict[str, dict] = {}

    print("Select MCP servers to enable:\n")
    for name, package, env_var in common_servers:
        # Check if in manifest or known
        is_available = name in available_servers or package.startswith("@")

        if is_available:
            prompt = f"  Enable {name} ({package})? [y/N]: "
            response = input(prompt).strip().lower()

            if response in ("y", "yes"):
                config: dict = {
                    "command": "npx",
                    "args": ["-y", package],
                }

                # Check for API key
                if env_var:
                    env_value = os.environ.get(env_var)
                    if env_value:
                        print(f"    Found {env_var} in environment")
                        config["env"] = {env_var: f"${{{env_var}}}"}
                    else:
                        print(f"    Note: Set {env_var} in .env for this server")
                        config["env"] = {env_var: f"${{{env_var}}}"}

                selected_servers[name] = config
                print(f"    Added {name}")

    if not selected_servers:
        print("\nNo servers selected. Creating minimal config...")
        selected_servers["filesystem"] = {
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-filesystem", "."],
        }

    # Write config
    config_data = {"mcpServers": selected_servers}

    config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(config_path, "w") as f:
        json.dump(config_data, f, indent=2)

    print(f"\nConfiguration saved to: {config_path}")
    print(f"Servers configured: {len(selected_servers)}")
    print("\nRun 'pmcp' to start the gateway.")


def _build_setup_config(mode: str, client: str) -> dict:
    """Build client config snippet for PMCP."""
    if client == "claude":
        if mode == "sse":
            return {
                "mcpServers": {
                    "pmcp": {
                        "type": "sse",
                        "url": "http://127.0.0.1:3344/sse",
                    }
                }
            }
        return {
            "mcpServers": {
                "pmcp": {
                    "command": "pmcp",
                    "args": [],
                }
            }
        }

    # OpenCode
    if mode == "sse":
        return {
            "mcp": {
                "pmcp": {
                    "type": "remote",
                    "url": "http://127.0.0.1:3344/sse",
                    "enabled": True,
                }
            }
        }
    return {
        "mcp": {
            "pmcp": {
                "type": "local",
                "command": "pmcp",
                "enabled": True,
            }
        }
    }


def _get_setup_target_path(client: str) -> Path:
    """Get the destination config path for a supported client."""
    home = Path.home()
    if client == "claude":
        return home / ".mcp.json"
    return home / ".config" / "opencode" / "opencode.json"


def _merge_setup_config(existing: dict, generated: dict) -> dict:
    """Merge generated top-level config keys into existing config."""
    merged = dict(existing)
    for key, value in generated.items():
        if isinstance(value, dict):
            current = merged.get(key)
            if not isinstance(current, dict):
                current = {}
            current.update(value)
            merged[key] = current
        else:
            merged[key] = value
    return merged


def _atomic_write_json(path: Path, data: dict) -> None:
    """Atomically write JSON data to path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", dir=path.parent, delete=False, encoding="utf-8"
    ) as tmp_file:
        json.dump(data, tmp_file, indent=2)
        tmp_file.write("\n")
        tmp_path = Path(tmp_file.name)
    tmp_path.replace(path)


def run_setup(args: argparse.Namespace) -> None:
    """Render or write PMCP config for a supported client."""
    config = _build_setup_config(mode=args.mode, client=args.client)

    if not args.write:
        print(json.dumps(config, indent=2))
        return

    target_path = _get_setup_target_path(args.client)
    existing: dict = {}
    if target_path.exists():
        try:
            parsed = json.loads(target_path.read_text())
            if isinstance(parsed, dict):
                existing = parsed
            else:
                raise ValueError("Top-level config must be a JSON object")
        except Exception as exc:
            print(
                f"Error: Could not parse existing config at {target_path}: {exc}",
                file=sys.stderr,
            )
            sys.exit(1)

    merged = _merge_setup_config(existing, config)
    _atomic_write_json(target_path, merged)
    print(f"Wrote PMCP setup to: {target_path}")


def _is_pmcp_system_service_active() -> bool | None:
    """Return user-service status when systemd is available."""
    if os.name != "posix":
        return None
    if shutil.which("systemctl") is None:
        return None

    try:
        proc = subprocess.run(
            ["systemctl", "--user", "is-active", "pmcp"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None

    return proc.returncode == 0 and proc.stdout.strip() == "active"


def _load_local_mcp_json(project_root: Path | None) -> tuple[Path, dict | None]:
    """Load local .mcp.json, if present and valid JSON."""
    from pmcp.config.loader import find_project_root

    base_dir = project_root or find_project_root(Path.cwd()) or Path.cwd()
    config_path = base_dir / ".mcp.json"
    if not config_path.exists():
        return config_path, None

    try:
        with open(config_path) as f:
            parsed = json.load(f)
            return config_path, parsed if isinstance(parsed, dict) else None
    except Exception:
        return config_path, None


def _extract_mode_signals(
    config_data: dict | None,
) -> tuple[list[str], list[tuple[str, str]]]:
    """Extract local command entries and SSE URLs from mcpServers."""
    command_servers: list[str] = []
    sse_endpoints: list[tuple[str, str]] = []

    if not isinstance(config_data, dict):
        return command_servers, sse_endpoints

    servers = config_data.get("mcpServers")
    if not isinstance(servers, dict):
        return command_servers, sse_endpoints

    for name, server_cfg in servers.items():
        if not isinstance(server_cfg, dict):
            continue
        command = server_cfg.get("command")
        if isinstance(command, str) and command.strip():
            command_servers.append(name)
        if server_cfg.get("type") == "sse":
            url = server_cfg.get("url")
            if isinstance(url, str) and url.strip():
                sse_endpoints.append((name, url.strip()))

    return command_servers, sse_endpoints


async def _probe_sse_endpoint(url: str, timeout_s: float) -> tuple[bool, str]:
    """Probe SSE endpoint and return (ok, details)."""
    import httpx

    try:
        timeout = httpx.Timeout(timeout=timeout_s)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            async with client.stream(
                "GET",
                url,
                headers={"Accept": "text/event-stream"},
            ) as response:
                if response.status_code == 200:
                    return True, "endpoint responded with HTTP 200"
                return False, f"endpoint returned HTTP {response.status_code}"
    except Exception as exc:
        return False, str(exc)


async def run_doctor(args: argparse.Namespace) -> None:
    """Diagnose local PMCP conflict conditions and suggest fixes."""
    setup_logging(args.log_level)

    checks: list[tuple[str, str, str]] = []

    lock_path = Path.home() / ".pmcp" / "gateway.lock"
    if lock_path.exists():
        checks.append(
            (
                "lock",
                "warn",
                "Lock file exists at ~/.pmcp/gateway.lock. If no gateway is running, remove stale lock: rm ~/.pmcp/gateway.lock",
            )
        )
    else:
        checks.append(("lock", "ok", "No gateway lock file detected."))

    service_active = _is_pmcp_system_service_active()
    config_path, config_data = _load_local_mcp_json(
        args.project if hasattr(args, "project") else None
    )
    command_servers, sse_endpoints = _extract_mode_signals(config_data)

    if service_active and command_servers:
        joined = ", ".join(sorted(command_servers))
        checks.append(
            (
                "mode",
                "fail",
                f"Local config {config_path} uses command mode for [{joined}] while system service is active. Use remote URL instead of command (type: sse, url: http://127.0.0.1:3344/sse).",
            )
        )
    elif service_active:
        checks.append(
            (
                "mode",
                "ok",
                "System service is active and no local command conflict was found.",
            )
        )
    else:
        checks.append(("mode", "ok", "No active PMCP system service detected."))

    if sse_endpoints:
        for server_name, sse_url in sse_endpoints:
            ok, detail = await _probe_sse_endpoint(sse_url, args.timeout)
            if ok:
                checks.append(
                    (
                        "sse",
                        "ok",
                        f"{server_name}: {sse_url} reachable ({detail}).",
                    )
                )
            else:
                checks.append(
                    (
                        "sse",
                        "fail",
                        f"{server_name}: {sse_url} probe failed ({detail}). Ensure pmcp service is running and URL is correct.",
                    )
                )
    else:
        checks.append(("sse", "ok", "No SSE endpoint configured in local .mcp.json."))

    remote_checks = collect_remote_header_diagnostics(config_data)
    if remote_checks:
        checks.extend(remote_checks)
    else:
        checks.append(("remote", "ok", "No remote downstream header issues detected."))

    status_icon = {"ok": "OK", "warn": "WARN", "fail": "FAIL"}
    print("PMCP Doctor")
    print("===========")
    for check_name, status, message in checks:
        icon = status_icon.get(status, status.upper())
        print(f"[{icon}] {check_name}: {message}")

    if any(status == "fail" for _, status, _ in checks):
        sys.exit(1)


async def run_server(args: argparse.Namespace) -> None:
    """Run the MCP gateway server."""
    from pmcp.server import GatewayServer

    # Check environment variables
    if not args.config and os.environ.get("PMCP_CONFIG"):
        args.config = Path(os.environ["PMCP_CONFIG"])
    if not args.policy and os.environ.get("PMCP_POLICY"):
        args.policy = Path(os.environ["PMCP_POLICY"])
    if os.environ.get("PMCP_LOG_LEVEL"):
        args.log_level = os.environ["PMCP_LOG_LEVEL"]

    # Transport environment overrides
    if os.environ.get("PMCP_TRANSPORT"):
        args.transport = os.environ["PMCP_TRANSPORT"]
    if os.environ.get("PMCP_HOST"):
        args.host = os.environ["PMCP_HOST"]
    if os.environ.get("PMCP_PORT"):
        args.port = int(os.environ["PMCP_PORT"])

    # Lock directory - CLI flag takes precedence over env var
    lock_dir = getattr(args, "lock_dir", None)
    if not lock_dir and os.environ.get("PMCP_LOCK_DIR"):
        lock_dir = Path(os.environ["PMCP_LOCK_DIR"])

    # Determine log level
    if args.debug:
        log_level = "debug"
    elif args.quiet:
        log_level = "error"
    else:
        log_level = args.log_level

    setup_logging(log_level)
    logger = logging.getLogger(__name__)

    logger.info("Starting PMCP...")

    server = GatewayServer(
        project_root=args.project,
        custom_config_path=args.config,
        policy_path=args.policy,
        host=args.host,
        port=args.port,
        lock_dir=lock_dir,
    )

    # Handle graceful shutdown
    shutdown_event = asyncio.Event()

    def handle_signal(sig: signal.Signals) -> None:
        logger.info(f"Received {sig.name}, shutting down...")
        shutdown_event.set()

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, handle_signal, sig)

    try:
        # Run server with shutdown handling
        server_task = asyncio.create_task(server.run(transport=args.transport))
        shutdown_task = asyncio.create_task(shutdown_event.wait())

        done, pending = await asyncio.wait(
            [server_task, shutdown_task],
            return_when=asyncio.FIRST_COMPLETED,
        )

        # Cancel pending tasks and await them properly
        for task in pending:
            task.cancel()

        # Wait for cancelled tasks to complete with timeout
        if pending:
            await asyncio.wait(pending, timeout=5.0)

        # Check if server task raised an exception
        if server_task in done:
            exc = server_task.exception()
            if exc:
                raise exc

    except asyncio.CancelledError:
        logger.info("Server cancelled")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        raise


def run_guidance(args: argparse.Namespace) -> None:
    """Show guidance configuration status."""
    from pmcp.config.guidance import load_guidance_config

    setup_logging(args.log_level)

    # Load guidance config
    config = load_guidance_config()

    print("Code Execution Guidance Configuration")
    print("=" * 50)
    print(f"Level: {config.level}")
    print("Config file: ~/.claude/gateway-guidance.yaml")
    print()
    print("Layers:")
    print(f"  L0 MCP Instructions: {'✓' if config.include_mcp_instructions else '✗'}")
    print(f"  L1 Code Hints: {'✓' if config.include_code_hints else '✗'}")
    print(f"  L2 Code Snippets: {'✓' if config.include_code_snippets else '✗'}")
    print(
        f"  L3 Methodology Resource: {'✓' if config.include_methodology_resource else '✗'}"
    )
    print()

    if args.show_budget:
        print("Token Budget (Estimated):")
        print("-" * 50)
        minimal = config.estimated_token_cost(num_search_results=15, num_describes=0)
        standard = config.estimated_token_cost(num_search_results=15, num_describes=1)
        heavy = config.estimated_token_cost(num_search_results=20, num_describes=2)

        print(f"  Minimal workflow (L0 + search): ~{minimal} tokens")
        print(f"  Standard workflow (+ 1 describe): ~{standard} tokens")
        print(f"  Heavy workflow (+ 2 describes): ~{heavy} tokens")
        print()

    print("To change configuration:")
    print("  Edit ~/.claude/gateway-guidance.yaml")
    print("  Or set level: off | minimal | standard")


async def async_main(args: argparse.Namespace) -> None:
    """Async main entry point - dispatch to appropriate command."""
    if args.command == "refresh":
        await run_refresh(args)
    elif args.command == "status":
        await run_status(args)
    elif args.command == "logs":
        await run_logs(args)
    elif args.command == "init":
        await run_init(args)
    elif args.command == "setup":
        run_setup(args)  # Synchronous command
    elif args.command == "guidance":
        run_guidance(args)  # Synchronous command
    elif args.command == "doctor":
        await run_doctor(args)
    elif args.command == "secrets":
        if args.secrets_command == "set":
            output = await run_secrets_set(args)
        elif args.secrets_command == "sync":
            output = await run_secrets_sync(args)
        elif args.secrets_command == "check":
            output = await run_secrets_check(args)
        else:
            output = {
                "ok": False,
                "command": "secrets",
                "error": f"Unknown secrets subcommand: {args.secrets_command}",
            }
        print(json.dumps(output, indent=2))
    else:
        # Default: run server
        await run_server(args)


def main() -> None:
    """Main entry point."""
    # Load .env file from current directory or project root
    load_dotenv()

    args = parse_args()

    try:
        asyncio.run(async_main(args))
    except KeyboardInterrupt:
        pass
    except Exception as e:
        print(f"Fatal error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
