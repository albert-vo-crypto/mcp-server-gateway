#!/bin/bash
# Cursor wrapper: mcp-remote -> local MCP gateway /gdrive namespace (OAuth-protected).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNNER_DIR="${SCRIPT_DIR}/../tools/mcp-remote-runner"
NODE_BIN="/Users/hgaggar/.nvm/versions/node/v20.20.0/bin/node"
TARGET_URL="${MCP_GATEWAY_URL:-http://localhost:8090/gdrive/mcp}"
# Pin a fixed loopback callback port so the OAuth token cache stays stable across
# restarts (a random port forces a fresh browser login every launch).
CALLBACK_PORT="42835"

cd "$RUNNER_DIR"

if [[ ! -x "$NODE_BIN" ]]; then
  echo "node not found at $NODE_BIN; set NODE_BIN to your node binary" >&2
  exit 1
fi

exec "$NODE_BIN" "$RUNNER_DIR/node_modules/mcp-remote/dist/proxy.js" "$TARGET_URL" "$CALLBACK_PORT" --allow-http
