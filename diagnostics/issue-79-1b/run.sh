#!/usr/bin/env bash
# Reproduce PMCP issue #79 symptom 1b ("MCP server pmcp session expired").
#
# Starts a PMCP gateway (HTTP transport, DEBUG logs) pointed at the synthetic
# slow/large downstream server, then drives repeated long calls through it and
# captures gateway + client logs for root-causing.
#
# Usage: ./run.sh [PORT]
set -euo pipefail

DIAG_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$DIAG_DIR/../.." && pwd)"
# Default to an uncommon port so we don't attach to an already-running gateway
# (e.g. the user's pmcp on 3344). Override with: ./run.sh <PORT>
PORT="${1:-3399}"
PY="$REPO_ROOT/.venv/bin/python"
OUT="$DIAG_DIR/out"
mkdir -p "$OUT"

# Materialize the config with an absolute path to slow_server.py.
sed "s#DIAG_DIR#$DIAG_DIR#g" "$DIAG_DIR/synthetic.mcp.json" > "$OUT/synthetic.mcp.json"

echo "[run] repo=$REPO_ROOT port=$PORT"
echo "[run] gateway log -> $OUT/gateway.log"
echo "[run] client  log -> $OUT/client.log"

# Optional: shrink the stdio read limit to make the big_output candidate fire
# at a smaller, faster size. Comment out to test the real 10 MiB default.
# export PMCP_STDIO_READ_LIMIT=$((4 * 1024 * 1024))

# Start the gateway in the background (HTTP, DEBUG, no auth on localhost).
# Isolated lock-dir so this diagnostic gateway can run ALONGSIDE the user's
# existing pmcp instance (PMCP enforces a single-instance lock by default).
mkdir -p "$OUT/lock"
"$PY" -m pmcp --transport http --host 127.0.0.1 --port "$PORT" \
    --config "$OUT/synthetic.mcp.json" --lock-dir "$OUT/lock" \
    --log-level debug --debug \
    > "$OUT/gateway.log" 2>&1 &
GW_PID=$!
echo "[run] gateway pid=$GW_PID"

cleanup() {
    echo "[run] stopping gateway pid=$GW_PID"
    kill "$GW_PID" 2>/dev/null || true
    wait "$GW_PID" 2>/dev/null || true
}
trap cleanup EXIT

# Wait for the gateway to accept connections (up to ~20s).
for i in $(seq 1 40); do
    if ! kill -0 "$GW_PID" 2>/dev/null; then
        echo "[run] gateway exited during startup; log:"; cat "$OUT/gateway.log"; exit 1
    fi
    if "$PY" - "$PORT" <<'PYEOF' 2>/dev/null
import socket, sys
s = socket.socket(); s.settimeout(0.5)
try:
    s.connect(("127.0.0.1", int(sys.argv[1]))); sys.exit(0)
except Exception:
    sys.exit(1)
PYEOF
    then echo "[run] gateway up after ${i} tries"; break; fi
    sleep 0.5
done

# Give eager downstream connect + tool indexing a moment.
sleep 3

# Drive the repro client.
set +e
"$PY" "$DIAG_DIR/repro_client.py" "http://127.0.0.1:$PORT/mcp" 2>&1 | tee "$OUT/client.log"
CLIENT_RC=${PIPESTATUS[0]}
set -e

echo
echo "[run] client exit code: $CLIENT_RC"
echo "[run] ---- gateway.log: session/timeout/disconnect highlights ----"
grep -niE "session|expired|terminat|timed out|timeout|disconnect|read limit|LimitOverrun|EOF|E303|killpg|reap|stalled|keep-?alive" \
    "$OUT/gateway.log" | tail -60 || true

echo
echo "[run] done. Full logs in $OUT/. See RUNBOOK.md for interpretation."
exit "$CLIENT_RC"
