# OWL-AGENT v7.1

> **AI free-tier aggregator with mesh health sync.**
>
> Not a proxy. The proxy is an implementation detail. The value is the
> mesh: a UDP health-broadcast layer that lets N OWL instances on the
> same LAN observe each other's capacity, plus a predictive circuit
> breaker that opens before an upstream AI provider falls over.

[![Status](https://img.shields.io/badge/status-v7.1--stable-green)]()
[![License](https://img.shields.io/badge/license-MIT-blue)]()
[![Podman](https://img.shields.io/badge/podman-rootless-blue)]()

---

## What it is

OWL-AGENT is a local-first aggregator for the free tiers of six AI
providers — Antigravity, Claude, OpenCode, Copilot, Kiro, Hermes. It
sits between your tools (CLI agents, IDE plugins, MCP clients) and the
upstream providers, applying:

1. **SSRF allowlist** — only the 6 provider domains (plus any you add)
   are reachable through the proxy. Loopback, link-local, RFC1918, and
   cloud-metadata IPs are rejected before any TCP connection is opened.
2. **Predictive circuit breaker** — per-domain latency tracking. If the
   last 3 requests all exceed 2× the p50 baseline, the circuit opens
   *predictively* (before the 5th failure) and your client fails fast
   instead of queuing behind a slow upstream.
3. **Mesh health broadcast** — UDP multicast (239.255.255.250:42100)
   so other OWL instances on the LAN can observe this node's capacity.
   Honest scope: this is observability broadcast, NOT load balancing.
   The proxy does not route requests to peers.

## What it isn't

- **Not a cache.** v7.0 had a `ProxyCache` that was never wired up and
  was a cache-poisoning risk. v7.1 deleted it. Caching is opt-in via
  `OWL_CACHE_ENABLED=true` (planned for v7.2).
- **Not a load balancer.** The mesh broadcasts health; it does not
  route. If you want N OWL instances to share load, put a round-robin
  proxy (HAProxy, nginx) in front of them.
- **Not an open proxy.** Default bind is `127.0.0.1`. To bind
  `0.0.0.0`, you MUST set `OWL_PROXY_TOKEN=<secret>` — requests without
  `Proxy-Authorization: Bearer <secret>` are rejected with 407.
- **Not a retry queue.** v7.0 had an `OfflineQueue` that stored failed
  requests forever and never retried them. v7.1 turned it into a no-op.
  Retry semantics are deferred to v7.2.

## Quickstart

### Bare-metal (Ubuntu 22.04+, 8 GB RAM)

```bash
git clone https://github.com/marktantongco/owl-agent-free-ai-proxy-gateway.git
cd owl-agent-free-ai-proxy-gateway
bash install_owl_unified.sh
```

The installer:
- Creates `~/.owl-agent/{config,logs,cache}`
- Copies the real `forward_proxy.py` (v7.1) from the repo — v7.0 had a
  bug where it wrote a broken 30-line stub over the real file
- Sets up a Python venv with `httpx[http2]`, `aiohttp`, `aiofiles`
- Installs a systemd unit (`owl-forward-proxy.service`)
- Optionally installs the Kiro gateway (`--skip-gateway` to skip)
- Optionally enables mesh broadcast (`--enable-mesh`)

Verify:

```bash
curl http://127.0.0.1:60000/health
# {"status":"ok","version":"7.1.0","max_connections":5,"allowed_domains":17,"mesh_enabled":false}
```

### Podman (rootless)

```bash
podman build -t owl-agent:7.1 .
podman run -d --name owl-proxy -p 60000:60000 owl-agent:7.1
```

With mesh + auth (binds 0.0.0.0, requires token):

```bash
podman run -d --name owl-full \
  -p 60000:60000 -p 42100:42100/udp \
  -e OWL_PROXY_HOST=0.0.0.0 \
  -e OWL_PROXY_TOKEN=$(openssl rand -hex 32) \
  -e OWL_ENABLE_MESH=true \
  owl-agent:7.1
```

### Compose

```bash
podman-compose up -d
```

## Configuration

All configuration is via environment variables. No config file required.

| Variable | Default | Purpose |
|---|---|---|
| `OWL_PROXY_HOST` | `127.0.0.1` | Bind address. Set to `0.0.0.0` to expose (requires `OWL_PROXY_TOKEN`). |
| `OWL_PROXY_PORT` | `60000` | Listen port. |
| `OWL_MAX_CONNECTIONS` | `5` | Max concurrent client connections. Fixed at startup; restart to change. |
| `OWL_CONNECT_TIMEOUT` | `15` | CONNECT timeout (seconds). |
| `OWL_PROXY_TIMEOUT` | `20` | General proxy timeout (seconds). |
| `UPSTREAM_PROXY` | (empty) | Upstream proxy URL, e.g. `http://127.0.0.1:7890`. |
| `OWL_ENABLE_MESH` | `false` | Enable UDP mesh health broadcast. |
| `OWL_MESH_PORT` | `42100` | Mesh UDP port. |
| `OWL_PROXY_TOKEN` | (empty) | Bearer token for non-loopback binds. |
| `OWL_ALLOW_EXTRA` | (empty) | Comma-separated extra domains to add to the SSRF allowlist. |

## SSRF allowlist

The proxy only connects to hosts on the allowlist. The default set:

```
antigravity.dev, api.antigravity.dev
anthropic.com, api.anthropic.com
opencode.dev, opencode.ai, api.opencode.dev, api.opencode.ai
copilot.ai, api.githubcopilot.com, githubcopilot.com
kiro.dev, api.kiro.dev
hermes-ai.dev, hermes.ai, api.hermes-ai.dev, api.hermes.ai
```

To add your own:

```bash
export OWL_ALLOW_EXTRA="my-internal-llm.corp.example.com,api.my-provider.com"
```

Even if a hostname is allowlisted, the proxy resolves it and refuses
to connect to a loopback, link-local, private, multicast, or
unspecified IP. This is the DNS-rebinding defense.

## Mesh health broadcast

When `OWL_ENABLE_MESH=true`, the proxy broadcasts a small JSON payload
to UDP multicast `239.255.255.250:42100` every 30 seconds:

```json
{
  "type": "owl-mesh",
  "host": "127.0.0.1",
  "port": 60000,
  "max_connections": 5,
  "timestamp": 1730000000.0
}
```

Other OWL instances on the same LAN receive this and can observe the
node's capacity. The proxy does NOT route requests to peers — this is
observability broadcast only. To actually share load, put a
round-robin proxy (HAProxy, nginx) in front of N OWL instances.

For cloud environments where UDP multicast is blocked, set
`OWL_MESH_MODE=tcp` and `OWL_MESH_SEEDS=host1:42100,host2:42100` to
use the TCP gossip implementation in `mesh_alternatives.py`.

## Predictive circuit breaker

Per-domain latency tracking. The last 20 request latencies per domain
are stored in a ring buffer. After 5 samples establish a p50 baseline:

- If the last 3 requests all exceed 2× p50 → `PREDICTIVE_OPEN`
- 5 consecutive failures → `OPEN`
- After 60 s in OPEN/PREDICTIVE_OPEN → `HALF_OPEN` (one probe allowed)
- Success in HALF_OPEN → `CLOSED`
- Failure in HALF_OPEN → `OPEN`

This means: if `api.anthropic.com` starts slowing down, the circuit
opens *before* the 5th failure, and your client fails fast (503)
instead of queuing behind a slow upstream.

## MCP server

`owl_resilient_mcp.py` is a Model Context Protocol server exposing 5
JSON-RPC tools over stdin/stdout:

| Tool | Purpose |
|---|---|
| `fetch` | HTTP fetch with cache, rate-limit, circuit-breaker, validation |
| `fetch_status` | Cache stats, circuit state, rate-limiter tokens |
| `fetch_clear_cache` | Clear the response cache |
| `health_check` | Server uptime, request count, provider list |
| `queue_status` | Offline queue status (always empty in v7.1 — retry deferred to v7.2) |

Wire it into any MCP-compatible client (Claude Code, Cursor, etc.) by
adding to the client's MCP config:

```json
{
  "mcpServers": {
    "owl-resilient-http": {
      "command": "python3",
      "args": ["/home/user/.owl-agent/owl_resilient_mcp.py"]
    }
  }
}
```

## Diagnostics

```bash
bash ~/.owl-agent/diagnose.sh
```

5 sections: Service Status, Connectivity, Environment, Resources,
Auto-Tune Status. v7.1 removed the `--fix` mode (it ran commands as
root with `2>/dev/null` swallowing all errors — false confidence).
Diagnostics now print the exact command to run for each detected issue.

## What changed from v7.0

See **[KNOWN_ISSUES.md](./KNOWN_ISSUES.md)** for the full list of 24
P0 bugs fixed in v7.1. Summary:

| Class | Count | What was done |
|---|---|---|
| Security | 4 | SSRF allowlist added; cache poisoning eliminated by deleting the cache; auth required for non-loopback; iptables-save redirect fixed |
| Dead code | 6 | ProxyCache, get_peer_proxies, PROVIDERS dict, RedisPubSubMesh, --fix mode, OfflineQueue — all deleted or neutralized |
| Broken install | 5 | Embedded stubs deleted; installer now `cp`s real files from the repo |
| Data/correctness | 3 | Multicast group unified; ResponseValidator fixed; OfflineQueue no-op |
| Race conditions | 3 | Semaphore no longer recreated; AutoTuner is observability-only; MeshSync uses DatagramTransport |
| Container | 3 | Multi-stage build; Redis service deleted; health check uses /health |

**Net code deletion:** ~1210 lines (~19% of v7.0). The user's
"delete 50%" target was aggressive — the named targets (ProxyCache,
get_peer_proxies, PROVIDERS dict, offline queue, Redis mesh path,
--fix mode) are all done, but the installer and proxy_defense module
still have legitimate bulk (systemd templates, rate-limiter state).

## Failure modes (be honest)

| Failure | What happens | Mitigation |
|---|---|---|
| Upstream provider 5xx | Circuit breaker records failure; after 5, opens for 60 s | Client gets 503; retry against a different provider |
| Upstream provider slow | Predictive circuit opens after 3 requests > 2× p50 | Client fails fast instead of timing out |
| DNS rebinding attack | Allowlisted hostname resolves to private IP | `_resolve_and_verify` rejects the IP before connect |
| Attacker pokes 127.0.0.1:22 | `is_allowed_target("127.0.0.1")` returns False | 403 Forbidden, no TCP connection opened |
| Attacker pokes 169.254.169.254 | Same — not in allowlist | 403 Forbidden |
| RAM pressure > 85% | AutoTuner logs a warning | Restart with lower `OWL_MAX_CONNECTIONS` |
| Mesh UDP blocked (cloud) | Mesh broadcast fails silently | Use `OWL_MESH_MODE=tcp` + `OWL_MESH_SEEDS` |
| Token leaked | Attacker can use the proxy | Rotate token; proxy is still SSRF-bounded so blast radius is limited to AI providers |

## Long-term strategy

The proxy is a commodity. Squid, mitmproxy, TinyProxy all do proxying
better. What none of them have is:

1. **AI-provider awareness** — the allowlist is the AI provider set,
   not a generic URL filter.
2. **Mesh health broadcast** — N instances observe each other's
   capacity for free, no broker.
3. **Predictive circuit breaking** — opens before the upstream falls
   over, not after.

If v7.1 is "an AI proxy with a mesh", v7.2 should be "the mesh, with
a proxy attached". The product is the mesh. The mesh is what lets you
run 10 OWL instances across 10 laptops at a hackathon and have them
share capacity awareness without a broker. The mesh is what lets a
small team pool their free-tier quotas without a central server. The
proxy is just the thing that makes the mesh useful for a single node.

## Contributing

File issues at <https://github.com/marktantongco/owl-agent-free-ai-proxy-gateway/issues>.
PRs welcome. Run `bash diagnose.sh` before reporting issues — it
prints the exact command to fix most common problems.

## License

MIT. See [LICENSE](./LICENSE).
