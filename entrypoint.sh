#!/usr/bin/env bash
# =============================================================================
# OWL-AGENT Container Entrypoint v7.1
# Supports: proxy | mcp | diagnose | shell
#
# v7.1 changes:
#   - Default OWL_PROXY_HOST is 127.0.0.1 (was 0.0.0.0). The v7.0 default
#     created an open proxy on every container start. The new forward_proxy.py
#     refuses to bind non-loopback without OWL_PROXY_TOKEN, so the old default
#     would crash the container — this fix aligns the entrypoint with the
#     security guard.
#   - Removed dead env vars: OWL_CACHE_MAX_ENTRIES (forward_proxy.py doesn't
#     read it — only proxy_defense_fixed_v3.py does, and it's not run by the
#     proxy mode), OWL_ENRICH_ENABLED (not a real env var; the real one is
#     OWL_PROXY_ENRICH in proxy_defense, also not read by the proxy).
# =============================================================================
set -euo pipefail

OWL_DIR="/home/owl/.owl-agent"

# Default environment variables if not set
export OWL_PROXY_HOST="${OWL_PROXY_HOST:-127.0.0.1}"
export OWL_PROXY_PORT="${OWL_PROXY_PORT:-60000}"
export OWL_MAX_CONNECTIONS="${OWL_MAX_CONNECTIONS:-5}"
export OWL_CONNECT_TIMEOUT="${OWL_CONNECT_TIMEOUT:-15}"
export OWL_PROXY_TIMEOUT="${OWL_PROXY_TIMEOUT:-20}"
export OWL_ENABLE_MESH="${OWL_ENABLE_MESH:-false}"

case "${1:-proxy}" in
    proxy)
        echo "OWL-AGENT Forward Proxy v7.1 starting (Antigravity · Claude · OpenCode · Copilot · Kiro · Hermes)..."
        echo "  Bind: ${OWL_PROXY_HOST}:${OWL_PROXY_PORT}"
        echo "  Max connections: ${OWL_MAX_CONNECTIONS}"
        echo "  Mesh: ${OWL_ENABLE_MESH}"
        if [[ "${OWL_PROXY_HOST}" != "127.0.0.1" && "${OWL_PROXY_HOST}" != "::1" && "${OWL_PROXY_HOST}" != "localhost" ]]; then
            if [[ -z "${OWL_PROXY_TOKEN:-}" ]]; then
                echo "  ERROR: Non-loopback bind requires OWL_PROXY_TOKEN. Refusing to start." >&2
                exit 1
            fi
            echo "  Auth: bearer token required"
        fi
        exec python3 "$OWL_DIR/forward_proxy.py"
        ;;
    mcp)
        echo "OWL-AGENT MCP Server v1.1 starting..."
        exec python3 "$OWL_DIR/owl_resilient_mcp.py"
        ;;
    defense)
        echo "OWL-AGENT Proxy Defense v3.3 starting..."
        exec python3 "$OWL_DIR/proxy_defense_fixed_v3.py"
        ;;
    diagnose)
        echo "OWL-AGENT Diagnostics v3.0..."
        exec bash "$OWL_DIR/diagnose.sh"
        ;;
    shell)
        exec /bin/bash
        ;;
    *)
        echo "Usage: entrypoint.sh {proxy|mcp|defense|diagnose|shell}"
        echo "  proxy    — Start forward proxy (default, loopback-only)"
        echo "  mcp      — Start MCP server"
        echo "  defense  — Start proxy defense (standalone smoke test)"
        echo "  diagnose — Run diagnostic checks"
        echo "  shell    — Open interactive shell"
        exit 1
        ;;
esac
