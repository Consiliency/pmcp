"""PMCP - A meta-server for minimal Claude Code tool bloat."""

from pmcp.argument_errors import install_log_scrubber as _install_log_scrubber

__version__ = "2.7.3"

# Every entry point imports this package first -- the gateway, the `pmcp`
# CLI (`pmcp refresh` talks to downstreams through the MCP SDK's client),
# `python -m pmcp` and any embedder -- so the log-record scrubber is in place
# before any library can log a rejected value (Consiliency/pmcp#297).
# Idempotent; `pmcp.client.manager` and `GatewayServer` call it too.
_install_log_scrubber()
