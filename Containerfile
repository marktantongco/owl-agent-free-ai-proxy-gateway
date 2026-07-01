# =============================================================================
# OWL-AGENT Unified Synergy — Podman Containerfile v7.1
#
# v7.1 changes:
#   - Multi-stage build. Stage 1 (builder) has build-essential, libffi-dev,
#     libssl-dev, python3-dev, git. Stage 2 (runtime) is python:3.11-slim
#     with only the pre-built venv — no compiler, no git, no build headers.
#   - Health check uses local /health endpoint (was: external httpbin.org).
#   - Default OWL_PROXY_HOST is 127.0.0.1 (was: 0.0.0.0). To bind
#     non-loopback, set OWL_PROXY_HOST=0.0.0.0 AND OWL_PROXY_TOKEN=<secret>.
#
# Build:
#   podman build -t owl-agent:7.1 -t owl-agent:latest .
#
# Run (standalone, loopback only):
#   podman run -d --name owl-proxy \
#     -p 60000:60000 \
#     owl-agent:7.1
#
# Run (with upstream proxy + mesh + auth):
#   podman run -d --name owl-full \
#     -p 60000:60000 -p 42100:42100/udp \
#     -e OWL_PROXY_HOST=0.0.0.0 \
#     -e OWL_PROXY_TOKEN=$(openssl rand -hex 32) \
#     -e UPSTREAM_PROXY=http://host.containers.internal:7890 \
#     -e OWL_ENABLE_MESH=true \
#     owl-agent:7.1
#
# Compose:
#   podman-compose up -d
#
# Why Podman over Docker?
#   - Rootless by default (no daemon, no socket exposure)
#   - Podman pods share network namespace (like k8s)
#   - systemd integration via quadlet (.container files)
#   - Compatible with Docker CLI (alias docker=podman)
#   - SELinux/AppArmor friendly on Ubuntu
# =============================================================================

# ── Stage 1: builder ─────────────────────────────────────────────────────────
FROM ubuntu:22.04 AS builder

LABEL maintainer="OWL-AGENT"
LABEL version="7.1"

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip python3-venv python3-dev \
    libffi-dev libssl-dev build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir 'httpx[http2]' aiohttp aiofiles

# ── Stage 2: runtime ─────────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

LABEL maintainer="OWL-AGENT"
LABEL version="7.1"
LABEL description="OWL-AGENT v7.1 - AI free-tier aggregator with mesh health sync"

# Copy the pre-built venv from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Create non-root user
RUN useradd -m -s /bin/bash owl
USER owl
WORKDIR /home/owl

# App structure
RUN mkdir -p /home/owl/.owl-agent/{config,logs,cache/http}

# Copy application files (real files, not stubs)
COPY --chown=owl:owl forward_proxy.py            /home/owl/.owl-agent/
COPY --chown=owl:owl proxy_defense_fixed_v3.py    /home/owl/.owl-agent/
COPY --chown=owl:owl owl_resilient_mcp.py         /home/owl/.owl-agent/
COPY --chown=owl:owl mesh_alternatives.py         /home/owl/.owl-agent/
COPY --chown=owl:owl diagnose.sh                  /home/owl/.owl-agent/
COPY --chown=owl:owl entrypoint.sh                /home/owl/
COPY --chown=owl:owl proxy_pool.json              /home/owl/.owl-agent/config/
COPY --chown=owl:owl proxy_sources.json           /home/owl/.owl-agent/config/
RUN chmod +x /home/owl/entrypoint.sh /home/owl/.owl-agent/diagnose.sh \
              /home/owl/.owl-agent/forward_proxy.py \
              /home/owl/.owl-agent/proxy_defense_fixed_v3.py \
              /home/owl/.owl-agent/owl_resilient_mcp.py

# Ports:
# 60000 — Forward Proxy (loopback by default)
# 42100 — Mesh Sync UDP (optional, for observability broadcast)
EXPOSE 60000/tcp 42100/udp

# Environment defaults (8GB RAM optimized, loopback-only by default)
ENV OWL_PROXY_HOST=127.0.0.1
ENV OWL_PROXY_PORT=60000
ENV OWL_MAX_CONNECTIONS=5
ENV OWL_PROXY_TIMEOUT=20
ENV OWL_CONNECT_TIMEOUT=15
ENV OWL_ENABLE_MESH=false
ENV OWL_MESH_PORT=42100
ENV OWL_PROXY_TOKEN=""
ENV UPSTREAM_PROXY=""

# Health check uses local /health endpoint (no external dependency)
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -sf --max-time 3 http://127.0.0.1:60000/health || exit 1

ENTRYPOINT ["/home/owl/entrypoint.sh"]
CMD ["proxy"]
