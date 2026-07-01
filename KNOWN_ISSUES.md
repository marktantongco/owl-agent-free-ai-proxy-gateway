# KNOWN ISSUES — OWL-AGENT v7.0

> **Status:** All 24 issues confirmed. Fixes targeted for v7.1.
> **Tag:** `v7.0-deprecated` points at the broken commit.
> **Public deployments:** Down (GitHub Pages deleted, Vercel pending takedown).

## P0 — Security (4 issues)

### P0-1 — SSRF: proxy accepts CONNECT to any host:port
**File:** `forward_proxy.py:829` (`_handle_connect`) and `forward_proxy.py:961` (`_handle_http`)
**Bug:** No allowlist on the target host. An attacker (or a misconfigured client) can issue `CONNECT 127.0.0.1:22 HTTP/1.1` and the proxy will open a TCP tunnel to the host's SSH port. `CONNECT 169.254.169.254:80` will exfiltrate AWS/GCP instance metadata. `CONNECT 10.0.0.1:6379` will poke at internal Redis.
**Impact:** Server-Side Request Forgery against the host running the proxy.
**Fix (v7.1):** Default-deny target allowlist. Only the configured AI-provider domains (`anthropic.com`, `api.githubcopilot.com`, `kiro.dev`, `opencode.dev`, `antigravity.dev`, `hermes-ai.dev`) and explicit user-added domains are permitted. RFC1918, link-local, loopback, and cloud-metadata IPs are rejected before any TCP connection is opened.

### P0-2 — Cache poisoning: ProxyCache keys on hostname only
**File:** `forward_proxy.py:592` (`ProxyCache`) — though the cache is currently dead code, the design is broken and was about to be wired in.
**Bug:** Cache key is the bare hostname. A request to `evil.com/` populates `cache["evil.com"]`; a subsequent request to `evil.com/admin` would have hit the same key (had the cache been wired up) and returned the wrong cached body. Even worse: a DNS-rebinding attack where `evil.com` first resolves to attacker IP, gets cached, then resolves to victim IP — the cache would still serve the attacker's response.
**Impact:** Cross-request data leakage once the cache is enabled.
**Fix (v7.1):** Delete `ProxyCache` entirely. v7.1 ships cache-free; caching is opt-in via `OWL_CACHE_ENABLED=true` and uses `(method, host, path, query)` as the key with a strict max-body-size limit.

### P0-3 — No auth on the proxy listener
**File:** `forward_proxy.py:62` (`OWL_PROXY_HOST` default `127.0.0.1` is fine; `Containerfile:80` overrides to `0.0.0.0`).
**Bug:** The container exposes the proxy on `0.0.0.0:60000` with no auth, no TLS, no allowlist on client IPs. Anyone who can reach the container port gets a free open proxy.
**Impact:** Open-proxy abuse — relay spam, anonymous scanning, IP rotation for credential stuffing.
**Fix (v7.1):** Container defaults to `127.0.0.1`. To bind `0.0.0.0`, the user must set `OWL_PROXY_TOKEN=<random>`; requests without `Proxy-Authorization: Bearer <token>` are rejected with 407.

### P0-4 — `iptables-save` redirect fails silently under `sudo`
**File:** `install_owl_unified.sh:1359` (original; line number approximate)
**Bug:** `sudo iptables-save > /etc/iptables/rules.v4` — the `>` redirect is performed by the calling shell, which does not have root. The file is not written; no error is surfaced.
**Impact:** Persistence rules silently fail to save. On reboot, the host loses firewall rules and the proxy may be exposed.
**Fix (v7.1):** `sudo sh -c 'iptables-save > /etc/iptables/rules.v4'` or `iptables-save | sudo tee /etc/iptables/rules.v4`.

## P0 — Dead code / broken paths (6 issues)

### P0-5 — `ProxyCache` is dead code
**File:** `forward_proxy.py:592-642`
**Bug:** `self.cache = ProxyCache()` is created in `OwlForwardProxy.__init__` but `self.cache.get()` and `self.cache.put()` are never called anywhere in the request handlers. The cache cleanup loop runs every 60s doing nothing.
**Impact:** Wasted memory (~200 entries × payload size), wasted CPU (60s cleanup loop), misleading "cache enabled" log lines.
**Fix (v7.1):** Delete `ProxyCache` class. Delete `_cache_cleanup_loop`. Remove `OWL_CACHE_MAX_ENTRIES` env var.

### P0-6 — `MeshSync.get_peer_proxies()` is dead code
**File:** `forward_proxy.py:317`
**Bug:** Method exists, collects peer proxy data via multicast, but is never called by the request handler. The mesh collects peer health and never uses it.
**Impact:** Wasted UDP traffic, wasted memory for `_peers` dict. The "mesh" feature is a one-way broadcast with no consumer.
**Fix (v7.1):** Either delete `MeshSync` entirely (and admit mesh is observability-only) or wire `get_peer_proxies()` into the request handler so the proxy actually load-balances across peers. v7.1 takes the former path — mesh stays for health broadcast only, and the README is honest about it.

### P0-7 — `PROVIDERS` dict is dead code
**File:** `forward_proxy.py:76-107`
**Bug:** Dict defined with priorities and domain lists, but bypass logic actually uses `BYPASS_DOMAINS` (a flat list). `PROVIDERS` is never read by any function except a startup log line.
**Impact:** Misleading — readers think the dict drives routing; it doesn't.
**Fix (v7.1):** Delete `PROVIDERS`. Replace `BYPASS_DOMAINS` with a single `ALLOWED_DOMAINS` set that drives both bypass logic AND the SSRF allowlist (fixes P0-1).

### P0-8 — `mesh_alternatives.RedisPubSubMesh` is unused
**File:** `mesh_alternatives.py:261-367`
**Bug:** `RedisPubSubMesh` class is defined but never instantiated by `forward_proxy.py`. `create_mesh()` factory returns `None` for the default UDP mode; no caller ever asks for `redis` mode.
**Impact:** ~100 lines of dead code; pulls in optional `aioredis` import path that adds a dependency the installer doesn't actually install.
**Fix (v7.1):** Delete `RedisPubSubMesh`. Keep `TCPGossipMesh` (it's the cloud-viable path) and the factory.

### P0-9 — `diagnose.sh --fix` mode is dangerous and unused
**File:** `diagnose.sh:24, 63-77,` plus scattered `try_fix` calls throughout.
**Bug:** `--fix` mode attempts automatic repair (restart services, clear caches, modify configs) with `2>/dev/null` swallowing all error output. A failed fix prints `✗ Could not fix: ...` but the underlying error is gone. Worse, `try_fix` runs as the calling user (often root) and can silently break systemd units.
**Impact:** False confidence — users think fixes succeeded; debugging is impossible because errors are discarded.
**Fix (v7.1):** Delete `--fix` mode entirely. `diagnose.sh` reports only; humans fix. Print the exact command to run for each detected issue instead.

### P0-10 — `OfflineQueue` in `owl_resilient_mcp.py` is unused
**File:** `owl_resilient_mcp.py` (OfflineQueue class)
**Bug:** Queue is defined, has `enqueue`/`peek_all`/`drain` methods, but the MCP server's `call_tool` handler never enqueues failed requests. The queue is always empty.
**Impact:** Dead code, ~80 lines.
**Fix (v7.1):** Delete `OfflineQueue`. If retry semantics are needed, document them as a v7.2 feature.

## P0 — Broken installer (5 issues)

### P0-11 — Installer writes stub `forward_proxy.py` over the real one
**File:** `install_owl_unified.sh:461-491`
**Bug:** The installer contains an embedded ~30-line "stub" `forward_proxy.py` that it writes to `$OWL_HOME/forward_proxy.py`, overwriting the real 1150-line file. The stub uses non-existent aiohttp APIs (`request.protocol.get_reader()`, `request.host`, `request.port`) and never relays data.
**Impact:** Every bare-metal `install_owl_unified.sh` install produces a non-functional proxy. The container image works because the Containerfile uses `COPY forward_proxy.py` (the real file) — but anyone running the installer directly is broken.
**Fix (v7.1):** Delete the embedded stub. Installer `cp`s the real `forward_proxy.py` from the repo to `$OWL_HOME/`.

### P0-12 — Installer writes stub `proxy_defense_fixed_v3.py` over the real one
**File:** `install_owl_unified.sh:525`
**Bug:** Same pattern as P0-11. Stub overwrites the real file.
**Impact:** Defense module broken on bare-metal install.
**Fix (v7.1):** Same — `cp` the real file.

### P0-13 — Installer writes stub `owl_resilient_mcp.py` over the real one
**File:** `install_owl_unified.sh:631`
**Bug:** Same pattern. Stub never starts the MCP server (`asyncio.run()` is missing).
**Impact:** MCP server broken on bare-metal install.
**Fix (v7.1):** Same — `cp` the real file.

### P0-14 — `write_file()` uses `echo` which strips trailing newlines
**File:** `install_owl_unified.sh` (`write_file()` helper)
**Bug:** `write_file()` calls `echo "$content" > path`. Bash's `echo` strips trailing newlines from the variable expansion, so any file written via `write_file` is missing its final newline. This breaks shellcheck, breaks `wc -l`, and breaks `source`-able scripts in subtle ways.
**Impact:** Data corruption for any file written by the installer.
**Fix (v7.1):** Use `printf '%s\n' "$content" > path` instead of `echo`.

### P0-15 — `iptables-save` redirect under `sudo` silently fails
(See P0-4 above — same bug, listed in security because the impact is a silent security regression.)

## P0 — Data / correctness (3 issues)

### P0-16 — `OfflineQueue.peek_all()` returns wrong data
**File:** `owl_resilient_mcp.py` (original; fixed in audit but the fix was never deployed to the repo).
**Bug:** `peek_all()` was returning the entire queue as a single concatenated list rather than a list of items. The `queue_status` MCP tool reported `n_items=1` for a queue with 5 items.
**Impact:** Queue monitoring is wrong.
**Fix (v7.1):** N/A — `OfflineQueue` is deleted (see P0-10).

### P0-17 — Multicast group mismatch between installer and runtime
**File:** `install_owl_unified.sh:1374` (uses `239.255.42.100`) vs `forward_proxy.py:277` (uses `239.255.255.250`).
**Bug:** Installer configures the host to join one multicast group; the runtime listens on a different group. Mesh nodes configured by the installer can never discover nodes using the standalone script.
**Impact:** Mesh is non-functional across mixed installs.
**Fix (v7.1):** Hardcode `239.255.255.250` (the standard SSDP/mesh group) in both places. Delete the env var override — there's no good reason for users to change it.

### P0-18 — `ResponseValidator` rejects 5xx in the wrong direction
**File:** `owl_resilient_mcp.py` (original).
**Bug:** Validator rejected 5xx/429/502-504 responses as "invalid" instead of treating them as transient failures to retry. A 503 from an upstream provider would surface as "response invalid" rather than "circuit breaker tripped".
**Impact:** Circuit breaker never trips on real upstream failures.
**Fix (v7.1):** Validator only rejects malformed JSON / wrong content-type. Transient HTTP failures are handled by the circuit breaker.

## P0 — Race conditions (3 issues)

### P0-19 — Semaphore recreation race in `_accept`
**File:** `forward_proxy.py:770-773`
**Bug:** When `AutoTuner` shrinks `MAX_CONNECTIONS`, `_accept` tries to "recreate" the semaphore: `self._semaphore = asyncio.Semaphore(MAX_CONNECTIONS)`. This discards any in-flight waiters — requests that were queued waiting for a slot are silently dropped (their `async with self._semaphore:` never resolves).
**Impact:** Under RAM pressure, the proxy silently drops queued connections.
**Fix (v7.1):** Don't recreate the semaphore. Use a token-bucket or a counter+condition variable. Or accept that AutoTuner only adjusts the cache size, not the connection limit (the simpler fix — v7.1 takes this path).

### P0-20 — AutoTuner reads `MAX_CONNECTIONS` global mid-request
**File:** `forward_proxy.py:259-260`
**Bug:** `MAX_CONNECTIONS = self.max_connections` mutates a module-level global that other coroutines read concurrently. No lock. A request in flight may see `MAX_CONNECTIONS=5` on one line and `MAX_CONNECTIONS=3` on the next.
**Impact:** Unpredictable behavior under tuning transitions.
**Fix (v7.1):** AutoTuner writes only to its own instance attributes. The semaphore reads `auto_tuner.max_connections` once at startup and is not dynamically resized.

### P0-21 — MeshSync `_recv_sock` set to timeout 2s, but `recvfrom` runs in executor
**File:** `forward_proxy.py:410, 362-364`
**Bug:** `self._recv_sock.settimeout(2)` is set, then `recvfrom` is called inside `loop.run_in_executor(None, self._recv_sock.recvfrom, 4096)`. The executor thread blocks for 2s, returns nothing, the loop sleeps 1s, retries. This is a busy-poll that burns a thread.
**Impact:** Wasted CPU and a thread-pool slot for mesh listening.
**Fix (v7.1):** Use `asyncio.DatagramTransport` instead of run_in_executor.

## P0 — Container (3 issues)

### P0-22 — Build tools shipped in production image
**File:** `Containerfile:39-43`
**Bug:** `build-essential`, `libffi-dev`, `libssl-dev`, `python3-dev`, `git` are all installed in the single-stage build. These are needed to compile `cryptography`/`httpx[http2]` but should be removed in a final stage.
**Impact:** Image is ~400 MB larger than necessary; attack surface includes `gcc`, `make`, `git`.
**Fix (v7.1):** Multi-stage build. Stage 1 (`builder`) installs build deps and pip installs to a venv. Stage 2 (`runtime`) copies the venv to a slim `python:3.11-slim` base.

### P0-23 — Redis port exposed unnecessarily
**File:** `podman-compose.yml:83-84` (per audit).
**Bug:** The compose file maps Redis port 6379 to the host. Redis is only used by `RedisPubSubMesh` (which is deleted in v7.1), so the port mapping is pure attack surface.
**Impact:** Anyone who can reach the host can poke at Redis (which has no auth by default).
**Fix (v7.1):** Delete the Redis service from `podman-compose.yml` entirely. If a user wants Redis for their own purposes, they can add it back.

### P0-24 — Health check hits `httpbin.org`
**File:** `Containerfile:94` (original; already fixed in audit but the fix was never deployed to the repo).
**Bug:** Health check was `curl http://httpbin.org/status/200` — an external dependency that fails when the network is down, even if the proxy itself is healthy.
**Impact:** False-negative health checks during network outages. Container orchestrator restarts a healthy proxy.
**Fix (v7.1):** `curl -sf http://127.0.0.1:60000/health` — and the proxy implements a `GET /health` endpoint that returns 200.

## Summary

| Class          | Count |
|----------------|-------|
| Security       | 4     |
| Dead code      | 6     |
| Broken install | 5     |
| Data/correctness | 3   |
| Race conditions| 3     |
| Container      | 3     |
| **Total**      | **24** |

## What v7.1 deletes

- `ProxyCache` class (~50 lines)
- `MeshSync.get_peer_proxies()` + wiring (~30 lines)
- `PROVIDERS` dict (~32 lines)
- `mesh_alternatives.RedisPubSubMesh` (~100 lines)
- `diagnose.sh --fix` mode (~40 lines)
- `OfflineQueue` in `owl_resilient_mcp.py` (~80 lines)
- Embedded stubs in `install_owl_unified.sh` (~200 lines)

**Net deletion:** ~530 lines (~10% of total), plus the security fixes that close all 4 P0 security issues.

## What v7.1 adds

- SSRF allowlist (`ALLOWED_DOMAINS` set, default-deny, configurable)
- Optional bearer-token auth (`OWL_PROXY_TOKEN`)
- `GET /health` endpoint
- Multi-stage Containerfile
- Honest README that calls it an "AI free-tier aggregator with mesh health sync"
