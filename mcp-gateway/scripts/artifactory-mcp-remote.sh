#!/bin/bash
# Wrapper that lets Cursor launch mcp-remote reliably against the local gateway.
#
# Why this exists:
#   Cursor spawns MCP "command" servers with its own app-resources directory as
#   the working dir and npm_config_* env pointing inside Cursor.app. That breaks
#   `npx`/`npm` path resolution (ENOENT on .../Resources/app/resources/lib). We
#   avoid it by cd'ing to a stable dir and running a pre-installed mcp-remote via
#   node directly (no runtime npx fetch).
#
# Transport: plain HTTP to the local gateway. mcp-remote allows unencrypted
# connections to localhost with --allow-http, so we do NOT need the old
# lvh.me + mkcert + https-proxy setup or a custom CA bundle. The whole OAuth
# (Google login + gateway consent) flow runs over http://localhost:8090.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNNER_DIR="${SCRIPT_DIR}/../tools/mcp-remote-runner"
NODE_BIN="${NODE_BIN:-$(command -v node || true)}"
TARGET_URL="${MCP_GATEWAY_URL:-http://localhost:8090/artifactory/mcp}"
# Pin a fixed loopback callback port so the OAuth token cache key stays stable
# across restarts. A random port would invalidate the cached token and force a
# fresh interactive browser login on every launch.
CALLBACK_PORT="42833"

cd "$RUNNER_DIR"

if [[ -z "$NODE_BIN" || ! -x "$NODE_BIN" ]]; then
  echo "node not found; install Node.js or set NODE_BIN to your node binary" >&2
  exit 1
fi

if [[ ! -f "$RUNNER_DIR/node_modules/mcp-remote/dist/proxy.js" ]]; then
  echo "mcp-remote is not installed. From $RUNNER_DIR run: npm install" >&2
  exit 1
fi

exec "$NODE_BIN" "$RUNNER_DIR/node_modules/mcp-remote/dist/proxy.js" "$TARGET_URL" "$CALLBACK_PORT" --allow-http
