#!/usr/bin/env bash
#
# Generate synthetic gateway rejections so the security panels on the
# "MCP Gateway — Usage & Traces" dashboard have something to show.
#
# Every request below is refused by the gateway before it reaches an upstream
# MCP server, so nothing touches Artifactory and no real credential is used.
# Traffic is tagged x-mcp-client-type: rejection-demo, so it can be told apart
# from (or filtered out of) genuine denials.
#
# Rate-limit rejections are deliberately not generated here: tripping a limit
# requires a *valid* token, and the gateway config enables no limits by default.
# To demo those, add a rate_limit block to a server in configs/secure-mcp-config.yml
# and a rate_limiting.enabled flag to configs/local.yml, then replay a real
# session faster than the limit allows.
#
# Usage:  ./scripts/generate-rejections.sh [BASE_URL] [ROUNDS]
set -euo pipefail

BASE_URL="${1:-http://localhost:8090}"
ROUNDS="${2:-5}"
NAMESPACE="${NAMESPACE:-artifactory}"

BODY='{"jsonrpc":"2.0","id":1,"method":"tools/list"}'

post() {
  local path="$1" label="$2"
  shift 2
  local code
  code=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$BASE_URL$path" \
    -H 'Content-Type: application/json' \
    -H 'x-mcp-client-type: rejection-demo' \
    "$@" -d "$BODY")
  printf '  %-18s HTTP %s\n' "$label" "$code"
}

echo "Generating $ROUNDS round(s) of rejections against $BASE_URL"

for i in $(seq 1 "$ROUNDS"); do
  echo "round $i"
  # No credential presented at all.
  post "/$NAMESPACE/mcp" missing_token
  # Well-formed Bearer header carrying a token the gateway cannot verify.
  post "/$NAMESPACE/mcp" invalid_token -H "Authorization: Bearer not-a-real-token-$i"
  # Probing for an MCP server that does not exist on this gateway.
  post "/no-such-server/mcp" unknown_namespace -H 'Authorization: Bearer irrelevant'
done

echo
echo "Done. Metrics land in Prometheus within ~15s (60s OTLP export + 5s scrape)."
echo "Check:  mcp_gateway_rejected_requests_total"
