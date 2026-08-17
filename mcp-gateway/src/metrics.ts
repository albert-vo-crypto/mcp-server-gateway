import { metrics, type Attributes } from '@opentelemetry/api'
import {
  ATTR_GEN_AI_TOOL_NAME,
  ATTR_USER_ID,
} from '@opentelemetry/semantic-conventions/incubating'

const meter = metrics.getMeter('mcp-gateway.otel')

/**
 * Requests the gateway refused before they reached an upstream MCP server.
 *
 * Authentication and rate limiting both short-circuit earlier in the request
 * path than `wrapRequest` in `mcp/otel.ts`, so rejected requests never produce
 * an mcp.client.operation.duration sample. Without this counter the only calls
 * a dashboard can see are the ones that got through, which makes a refusal and
 * a request that was never made look identical.
 */
const rejections = meter.createCounter('mcp.gateway.rejected_requests', {
  description:
    'Requests rejected by the gateway before reaching an upstream MCP server, by reason.',
})

export const RejectionReason = {
  /** Namespace requires auth and the request carried no bearer token. */
  MissingToken: 'missing_token',
  /** Bearer token present but failed verification: bad signature, expired, or wrong namespace. */
  InvalidToken: 'invalid_token',
  /** Request addressed an MCP namespace that does not exist. */
  UnknownNamespace: 'unknown_namespace',
  /** Server-wide rate limit tripped. */
  RateLimitedServer: 'rate_limited_server',
  /** Per-tool rate limit tripped. */
  RateLimitedTool: 'rate_limited_tool',
  /** Upstream expects an OIDC id_token but the session has none. */
  MissingIdToken: 'missing_id_token',
} as const

export type RejectionReason =
  (typeof RejectionReason)[keyof typeof RejectionReason]

type RejectionAttributes = {
  reason: RejectionReason
  namespace?: string
  /**
   * Identity is only trustworthy once a token has been verified, so the
   * rejections that matter most for intrusion detection — missing and invalid
   * tokens — are necessarily anonymous. Attributing them to a user would mean
   * believing an unverified claim.
   */
  userId?: string | null
  provider?: string
  clientId?: string
  clientType?: string | null
  toolName?: string
}

export function recordRejection({
  reason,
  namespace,
  userId,
  provider,
  clientId,
  clientType,
  toolName,
}: RejectionAttributes) {
  const attributes: Attributes = { 'gateway.rejection_reason': reason }

  if (namespace) attributes['gateway.mcp_server.name'] = namespace
  if (userId) attributes[ATTR_USER_ID] = userId
  if (provider) attributes['gateway.oauth_provider'] = provider
  if (clientId) attributes['gateway.oauth_client_id'] = clientId
  if (clientType) attributes['gateway.client_type'] = clientType
  if (toolName) attributes[ATTR_GEN_AI_TOOL_NAME] = toolName

  rejections.add(1, attributes)
}
