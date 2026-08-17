#!/usr/bin/env python3
"""Regenerate the derived parts of the MCP gateway dashboard.

Does two things, both idempotent:

1. Rewrites every counter total to be range-scoped and reset-aware.
2. Adds (or replaces) the security / access-control section.

Kept as a script rather than hand-edited JSON so the layout can be regenerated
without re-resolving gridPos conflicts by hand.

Usage:  python3 build-dashboard.py
Then:   docker restart grafana   (or wait for the 10s provisioning interval)
"""
import json
import pathlib
import urllib.error
import urllib.request

DASHBOARD = pathlib.Path(__file__).parent / "dashboards" / "mcp-gateway.json"
GRAFANA_UID_URL = "http://localhost:3000/api/dashboards/uid/mcp-gateway-overview"

PROM = {"type": "prometheus", "uid": "prometheus"}
REJECTED = "mcp_gateway_rejected_requests_total"
DURATION = "mcp_client_operation_duration_milliseconds_count"

# Identity is unverified on a rejected request, so these panels deliberately
# ignore the $user variable — filtering by user would hide exactly the
# anonymous denials that matter most.
ANON_NOTE = (
    "Not filtered by the $user variable: a rejected request has no verified "
    "identity, so most denials here are necessarily anonymous."
)

SECTION_TITLE = "Security & access control"
START_Y = 28


# Counter tiles are evaluated as instant queries at "now" rather than reduced
# over the time range. A range query feeding a lastNotNull reduction keeps
# reporting the final value of a gateway process that has since exited, so after
# a restart the tiles showed ghost totals from a dead container while the tables,
# which already queried at "now", correctly showed nothing.
#
# increase() over $__range would respect the time picker, but it extrapolates at
# the window edges: on this data it turned 8 real 401s into 10. Approximation is
# tolerable for a throughput graph and not for a security count, so these read
# the counter exactly and are cumulative since the gateway process started.
COUNTER_SCOPE_NOTE = (
    "Counted exactly, cumulative since the gateway process started, so it resets "
    "when the gateway restarts. Not affected by the time range picker."
)


def total(selector, by=None):
    return f"sum by ({by}) ({selector})" if by else f"sum({selector})"


def distinct(label, selector):
    """Number of distinct label values currently reporting."""
    return f"count(count by ({label}) ({selector}))"


def zero_default(expr):
    """Render an absent series as 0.

    An aggregation over no matching series returns an empty result, which a stat
    panel renders as "No data" — indistinguishable from a broken query. Zero
    denials is a real and reassuring answer, so say it.
    """
    return f"{expr} or vector(0)"


# Existing hand-written panels read their counters raw. Correct them in place
# rather than restating the panels, so upstream edits to titles, layout and
# datasources survive a regeneration.
COUNTER_OVERRIDES = {
    1: total(f'{DURATION}{{gen_ai_tool_name!="", user_id=~"$user"}}'),
    2: distinct("user_id", f'{DURATION}{{user_id!=""}}'),
    3: distinct("gen_ai_tool_name", f'{DURATION}{{gen_ai_tool_name!=""}}'),
    4: total(f'{DURATION}{{error_type!=""}}'),
    7: total(
        f'{DURATION}{{gen_ai_tool_name!="", user_id=~"$user"}}',
        by="user_id, gateway_mcp_server_name, gen_ai_tool_name, "
        "gateway_oauth_provider",
    ),
    10: total(
        f'{DURATION}{{gen_ai_tool_name!="", user_id=~"$user"}}',
        by="user_id, gateway_client_type, gateway_mcp_server_name, "
        "gen_ai_tool_name",
    ),
}


def stat(pid, title, expr, x, y, description):
    return {
        "id": pid,
        "type": "stat",
        "title": title,
        "description": description,
        "datasource": PROM,
        "gridPos": {"h": 4, "w": 6, "x": x, "y": y},
        "fieldConfig": {
            "defaults": {
                "unit": "short",
                "decimals": 0,
                "color": {"mode": "thresholds"},
                "thresholds": {
                    "mode": "absolute",
                    "steps": [
                        {"color": "green", "value": None},
                        {"color": "red", "value": 1},
                    ],
                },
            },
            "overrides": [],
        },
        "options": {
            "reduceOptions": {
                "calcs": ["lastNotNull"],
                "fields": "",
                "values": False,
            },
            "textMode": "auto",
        },
        "targets": [{"refId": "A", "expr": zero_default(expr), "instant": True}],
    }


def timeseries(pid, title, targets, x, y, description, w=12, h=8):
    return {
        "id": pid,
        "type": "timeseries",
        "title": title,
        "description": description,
        "datasource": PROM,
        "gridPos": {"h": h, "w": w, "x": x, "y": y},
        "fieldConfig": {
            "defaults": {
                "custom": {"fillOpacity": 15, "lineWidth": 2, "showPoints": "auto"},
                "unit": "reqps",
            },
            "overrides": [],
        },
        "targets": targets,
    }


def table(pid, title, expr, x, y, description, w=12, h=8):
    return {
        "id": pid,
        "type": "table",
        "title": title,
        "description": description,
        "datasource": PROM,
        "gridPos": {"h": h, "w": w, "x": x, "y": y},
        "fieldConfig": {"defaults": {"decimals": 0}, "overrides": []},
        "targets": [{"refId": "A", "expr": expr, "format": "table", "instant": True}],
        "transformations": [
            {"id": "organize", "options": {"excludeByName": {"Time": True}}}
        ],
    }


def build_security_panels():
    y = START_Y
    panels = [
        {
            "id": 100,
            "type": "row",
            "title": SECTION_TITLE,
            "collapsed": False,
            "gridPos": {"h": 1, "w": 24, "x": 0, "y": y},
            "panels": [],
        }
    ]

    y += 1
    panels += [
        stat(
            101,
            "Rejected requests",
            total(REJECTED),
            0,
            y,
            "Every request the gateway refused before it reached an upstream MCP "
            f"server. {ANON_NOTE} {COUNTER_SCOPE_NOTE}",
        ),
        stat(
            102,
            "Unauthorized (401)",
            total(
                f'{REJECTED}{{gateway_rejection_reason=~"missing_token|invalid_token"}}'
            ),
            6,
            y,
            "No bearer token presented, or a token the gateway could not verify "
            "(bad signature, expired, or issued for another namespace). "
            f"{COUNTER_SCOPE_NOTE}",
        ),
        stat(
            103,
            "Rate-limited",
            total(f'{REJECTED}{{gateway_rejection_reason=~"rate_limited_.*"}}'),
            12,
            y,
            "Authenticated users blocked by a server-wide or per-tool rate limit. "
            "Stays at zero unless rate_limiting is enabled in the gateway config.",
        ),
        stat(
            104,
            "Unknown namespace probes",
            total(f'{REJECTED}{{gateway_rejection_reason="unknown_namespace"}}'),
            18,
            y,
            "Requests addressed to an MCP server that does not exist on this "
            "gateway — the signature of scanning or a misconfigured client.",
        ),
    ]

    y += 4
    panels += [
        timeseries(
            105,
            "Rejections / sec by reason",
            [
                {
                    "refId": "A",
                    "expr": "sum by (gateway_rejection_reason) "
                    f"(rate({REJECTED}[$__rate_interval]))",
                    "legendFormat": "{{gateway_rejection_reason}}",
                }
            ],
            0,
            y,
            "Rate of refusals by cause. A sustained rise in invalid_token or "
            "unknown_namespace is the interesting signal. " + ANON_NOTE,
        ),
        table(
            106,
            "Rejections by reason / server / client",
            total(
                REJECTED,
                by="gateway_rejection_reason, gateway_mcp_server_name, "
                "gateway_client_type, gateway_oauth_provider",
            ),
            12,
            y,
            "Which caller is being turned away, from which MCP server. "
            "client type 'rejection-demo' is synthetic traffic from "
            "scripts/generate-rejections.sh.",
        ),
    ]

    y += 8
    panels += [
        table(
            107,
            "Failed tool calls by OAuth provider",
            total(
                f'{DURATION}{{error_type!=""}}',
                by="gateway_oauth_provider, gen_ai_tool_name, error_type, "
                "rpc_response_status_code, user_id",
            ),
            0,
            y,
            "Calls that reached the upstream server and came back an error, "
            "attributed to the OAuth provider that authorized them. Rows with an "
            "empty tool name are protocol-level failures, not tool failures.",
        ),
        table(
            108,
            "Rate-limited users and tools",
            total(
                f'{REJECTED}{{gateway_rejection_reason=~"rate_limited_.*"}}',
                by="user_id, gen_ai_tool_name, gateway_mcp_server_name",
            ),
            12,
            y,
            "Who is hitting limits, and on which tool. Identity is available here "
            "because a rate limit is only applied after the token is verified.",
        ),
    ]

    return panels


def apply_counter_overrides(panels):
    patched = 0
    for panel in panels:
        expr = COUNTER_OVERRIDES.get(panel["id"])
        if not expr:
            continue
        panel["targets"][0]["expr"] = (
            zero_default(expr) if panel["type"] == "stat" else expr
        )
        panel["targets"][0]["instant"] = True
        defaults = panel.setdefault("fieldConfig", {}).setdefault("defaults", {})
        defaults["decimals"] = 0
        panel["fieldConfig"].setdefault("overrides", [])
        description = panel.get("description", "")
        if COUNTER_SCOPE_NOTE not in description:
            panel["description"] = (
                f"{description} {COUNTER_SCOPE_NOTE}".strip()
            )
        if panel["type"] == "table":
            panel["targets"][0].setdefault("format", "table")
        patched += 1
    return patched


def live_version():
    """Version Grafana currently holds, or None if it is not reachable.

    Only relevant when the provisioner runs with allowUiUpdates, which makes it
    ignore any file whose version is not greater than the stored one.
    """
    try:
        with urllib.request.urlopen(GRAFANA_UID_URL, timeout=3) as response:
            return json.load(response)["dashboard"].get("version")
    except (urllib.error.URLError, KeyError, TimeoutError, OSError):
        return None


def main():
    dashboard = json.loads(DASHBOARD.read_text())

    security = build_security_panels()
    security_ids = {p["id"] for p in security}

    kept = [
        p
        for p in dashboard["panels"]
        if p["id"] not in security_ids and p.get("title") != SECTION_TITLE
    ]

    patched = apply_counter_overrides(kept)

    dashboard["panels"] = kept + security
    dashboard["version"] = max(dashboard.get("version", 1), live_version() or 0) + 1

    DASHBOARD.write_text(json.dumps(dashboard, indent=2) + "\n")
    print(
        f"wrote {DASHBOARD.name} v{dashboard['version']}: "
        f"{len(kept)} existing panels ({patched} counter queries corrected) "
        f"+ {len(security)} security panels"
    )


if __name__ == "__main__":
    main()
