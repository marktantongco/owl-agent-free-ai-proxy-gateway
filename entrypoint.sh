#!/usr/bin/env bash
# =============================================================================
# OWL-AGENT Container Entrypoint
# Supports: proxy | mcp | diagnose | shell
# =============================================================================
set -euo pipefail

OWL_DIR="/home/owl/.owl-agent"

# Default environment variables if not set
export OWL_PROXY_HOST="${OWL_PROXY_HOST:-0.0.0.0}"
export OWL_PROXY_PORT="${OWL_PROXY_PORT:-60000}"
export OWL_MAX_CONNECTIONS="${OWL_MAX_CONNECTIONS:-5}"
export OWL_CACHE_MAX_ENTRIES="${OWL_CACHE_MAX_ENTRIES:-200}"
export OWL_CONNECT_TIMEOUT="${OWL_CONNECT_TIMEOUT:-15}"
export OWL_PROXY_TIMEOUT="${OWL_PROXY_TIMEOUT:-20}"
export OWL_ENABLE_MESH="${OWL_ENABLE_MESH:-false}"
export OWL_ENRICH_ENABLED="${OWL_ENRICH_ENABLED:-false}"

case "${1:-proxy}" in
    proxy)
        echo "OWL-AGENT Forward Proxy v7.0 starting (Antigravity · Claude · OpenCode · Copilot · Kiro · Hermes)..."
        echo "  Bind: ${OWL_PROXY_HOST}:${OWL_PROXY_PORT}"
        echo "  Max connections: ${OWL_MAX_CONNECTIONS}"
        echo "  Mesh: ${OWL_ENABLE_MESH}"
        echo "  Enrich: ${OWL_ENRICH_ENABLED}"
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
        echo "OWL-AGENT Diagnostics..."
        exec bash "$OWL_DIR/diagnose.sh"
        ;;
    shell)
        exec /bin/bash
        ;;
    *)
        echo "Usage: entrypoint.sh {proxy|mcp|defense|diagnose|shell}"
        echo "  proxy    — Start forward proxy (default)"
        echo "  mcp      — Start MCP server"
        echo "  defense  — Start proxy defense"
        echo "  diagnose — Run diagnostic checks"
        echo "  shell    — Open interactive shell"
        exit 1
        ;;
esac
