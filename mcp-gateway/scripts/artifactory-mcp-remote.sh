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

RUNNER_DIR="/Users/hgaggar/books/mcp-gateway/tools/mcp-remote-runner"
NODE_BIN="/Users/hgaggar/.nvm/versions/node/v20.20.0/bin/node"
TARGET_URL="http://localhost:8090/artifactory/mcp"
# Pin a fixed loopback callback port so the OAuth token cache key stays stable
# across restarts. A random port would invalidate the cached token and force a
# fresh interactive browser login on every launch.
CALLBACK_PORT="42833"

cd "$RUNNER_DIR"

exec "$NODE_BIN" "$RUNNER_DIR/node_modules/mcp-remote/dist/proxy.js" "$TARGET_URL" "$CALLBACK_PORT" --allow-http
