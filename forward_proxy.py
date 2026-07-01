#!/usr/bin/env python3
"""
OWL-AGENT Forward Proxy v7.1
============================

AI free-tier aggregator with mesh health sync.

v7.1 changes from v7.0:
  - SSRF allowlist (default-deny). Only AI provider domains are reachable
    through the proxy. Loopback, link-local, RFC1918, and cloud-metadata
    targets are rejected before any TCP connection is opened.
  - Optional bearer-token auth (OWL_PROXY_TOKEN). Required when binding
    to non-loopback addresses.
  - GET /health endpoint for container health checks.
  - Deleted: ProxyCache (was dead code, was cache-poisoning risk).
  - Deleted: MeshSync.get_peer_proxies (was dead code).
  - Deleted: PROVIDERS dict (was dead code).
  - Fixed: Semaphore recreation race (AutoTuner no longer mutates the
    semaphore; connection limit is fixed at startup).
  - Fixed: MeshSync uses asyncio DatagramTransport instead of
    run_in_executor + blocking recvfrom.

Environment Variables:
    OWL_PROXY_HOST        - Bind address (default: 127.0.0.1)
    OWL_PROXY_PORT        - Bind port (default: 60000)
    OWL_MAX_CONNECTIONS   - Max concurrent connections (default: 5)
    OWL_CONNECT_TIMEOUT   - CONNECT timeout seconds (default: 15)
    OWL_PROXY_TIMEOUT     - General proxy timeout seconds (default: 20)
    UPSTREAM_PROXY        - Upstream proxy URL, e.g. http://127.0.0.1:7890
    OWL_ENABLE_MESH       - Enable mesh health broadcast (default: false)
    OWL_MESH_PORT         - Mesh UDP port (default: 42100)
    OWL_PROXY_TOKEN       - Bearer token for non-loopback binds (optional
                            for 127.0.0.1, REQUIRED for 0.0.0.0)
    OWL_ALLOW_EXTRA       - Comma-separated extra domains to allowlist
                            (added to the built-in AI provider set)

Usage:
    python forward_proxy.py
"""

from __future__ import annotations

import asyncio
import enum
import ipaddress
import json
import logging
import os
import signal
import socket
import struct
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, Optional, Set, Tuple
from urllib.parse import urlparse

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("owl-proxy")

# ---------------------------------------------------------------------------
# Environment configuration
# ---------------------------------------------------------------------------
OWL_PROXY_HOST: str = os.getenv("OWL_PROXY_HOST", "127.0.0.1")
OWL_PROXY_PORT: int = int(os.getenv("OWL_PROXY_PORT", "60000"))
OWL_MAX_CONNECTIONS: int = int(os.getenv("OWL_MAX_CONNECTIONS", "5"))
OWL_CONNECT_TIMEOUT: int = int(os.getenv("OWL_CONNECT_TIMEOUT", "15"))
OWL_PROXY_TIMEOUT: int = int(os.getenv("OWL_PROXY_TIMEOUT", "20"))
UPSTREAM_PROXY: str = os.getenv("UPSTREAM_PROXY", "")
OWL_ENABLE_MESH: bool = os.getenv("OWL_ENABLE_MESH", "false").lower() in ("true", "1", "yes")
OWL_MESH_PORT: int = int(os.getenv("OWL_MESH_PORT", "42100"))
OWL_PROXY_TOKEN: str = os.getenv("OWL_PROXY_TOKEN", "")
OWL_ALLOW_EXTRA: str = os.getenv("OWL_ALLOW_EXTRA", "")

# ---------------------------------------------------------------------------
# SSRF allowlist — the only hostnames the proxy will connect to.
# Default set is the 6 supported AI providers. Users can extend with
# OWL_ALLOW_EXTRA=foo.example.com,bar.example.com
# ---------------------------------------------------------------------------
ALLOWED_DOMAINS: Set[str] = {
    "antigravity.dev",
    "api.antigravity.dev",
    "anthropic.com",
    "api.anthropic.com",
    "opencode.dev",
    "opencode.ai",
    "api.opencode.dev",
    "api.opencode.ai",
    "copilot.ai",
    "api.githubcopilot.com",
    "githubcopilot.com",
    "kiro.dev",
    "api.kiro.dev",
    "hermes-ai.dev",
    "hermes.ai",
    "api.hermes-ai.dev",
    "api.hermes.ai",
}
if OWL_ALLOW_EXTRA:
    for d in OWL_ALLOW_EXTRA.split(","):
        d = d.strip().lower()
        if d:
            ALLOWED_DOMAINS.add(d)

# Buffer size per connection — memory-optimised for 32 KB.
BUFFER_SIZE: int = 32 * 1024

# Body size limit for plain HTTP requests (10 MiB).
MAX_BODY_SIZE: int = 10 * 1024 * 1024


# ---------------------------------------------------------------------------
# SSRF guard
# ---------------------------------------------------------------------------
def is_allowed_target(host: str) -> bool:
    """Return True iff *host* is on the allowlist AND does not resolve to
    a forbidden IP range.

    Forbidden IP ranges (even if the hostname is allowlisted, to defend
    against DNS rebinding):
        - Loopback (127.0.0.0/8, ::1)
        - Link-local (169.254.0.0/16, fe80::/10) — includes cloud metadata
        - Private (RFC1918 + fc00::/7) — only forbidden when binding non-loopback
        - Multicast (224.0.0.0/4, ff00::/8)
        - 0.0.0.0/8 (unspecified)
    """
    if not host:
        return False

    # Hostname allowlist check
    hostname = host.lower().rstrip(".")
    if hostname == "localhost":
        # localhost is never a valid outbound target for this proxy
        return False

    matched = any(
        hostname == d or hostname.endswith("." + d)
        for d in ALLOWED_DOMAINS
    )
    if not matched:
        return False

    # If the host is already a literal IP, check it directly.
    # (Allowlisted hostnames are domain names, not IPs, so a literal IP
    # reaching this point means the user added it via OWL_ALLOW_EXTRA —
    # in which case we still apply the IP range filter.)
    try:
        ip = ipaddress.ip_address(hostname)
    except ValueError:
        # It's a domain name — we allowlisted it; we'll let the OS resolve
        # it at connect time. DNS-rebinding to a private IP is mitigated by
        # resolving and re-checking the IP just before opening the socket.
        return True

    return _is_safe_public_ip(ip)


def _is_safe_public_ip(ip: ipaddress._BaseAddress) -> bool:
    """Reject loopback, link-local, private, multicast, unspecified."""
    if ip.is_loopback or ip.is_link_local or ip.is_private:
        return False
    if ip.is_multicast or ip.is_unspecified or ip.is_reserved:
        return False
    return True


async def _resolve_and_verify(host: str, port: int) -> Optional[Tuple[str, int]]:
    """Resolve *host* and return a (ip, port) pair where the IP passes
    the SSRF guard. Returns None if no safe IP is found.

    This is the DNS-rebinding defense: even if the hostname is allowlisted,
    we resolve it and refuse to connect to a private/loopback IP.
    """
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(
            host, port, type=socket.SOCK_STREAM
        )
    except socket.gaierror:
        return None

    for family, _, _, _, sockaddr in infos:
        ip_str = sockaddr[0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        if _is_safe_public_ip(ip):
            return (ip_str, port)
    return None


# ---------------------------------------------------------------------------
# AutoTuner — logs recommendations, does not mutate globals
# ---------------------------------------------------------------------------
class AutoTuner:
    """Reads /proc/meminfo every 60 s and logs RAM pressure as a
    recommendation. v7.1 no longer mutates MAX_CONNECTIONS at runtime
    (the v7.0 behavior caused a semaphore-recreation race that dropped
    in-flight requests). AutoTuner is now observability-only.
    """

    CHECK_INTERVAL: float = 60.0
    PRESSURE_SHRINK: float = 85.0
    PRESSURE_GROW: float = 70.0

    def __init__(self) -> None:
        self._task: Optional[asyncio.Task[None]] = None

    def start(self) -> None:
        self._task = asyncio.ensure_future(self._loop())
        logger.info("AutoTuner started (observability-only, interval=%.0fs)", self.CHECK_INTERVAL)

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("AutoTuner stopped")

    async def _loop(self) -> None:
        while True:
            try:
                pressure = self._read_ram_pressure()
                if pressure is not None:
                    if pressure > self.PRESSURE_SHRINK:
                        logger.warning(
                            "AutoTuner: RAM pressure %.1f%% exceeds %.0f%% — "
                            "consider lowering OWL_MAX_CONNECTIONS and restarting",
                            pressure, self.PRESSURE_SHRINK,
                        )
                    elif pressure < self.PRESSURE_GROW:
                        logger.info(
                            "AutoTuner: RAM pressure %.1f%% below %.0f%% — "
                            "headroom available for higher OWL_MAX_CONNECTIONS",
                            pressure, self.PRESSURE_GROW,
                        )
            except Exception as exc:
                logger.warning("AutoTuner error: %s", exc)
            await asyncio.sleep(self.CHECK_INTERVAL)

    @staticmethod
    def _read_ram_pressure() -> Optional[float]:
        try:
            with open("/proc/meminfo", "r") as fh:
                info: Dict[str, int] = {}
                for line in fh:
                    parts = line.split()
                    if len(parts) >= 2:
                        key = parts[0].rstrip(":")
                        info[key] = int(parts[1])
            total = info.get("MemTotal", 0)
            available = info.get("MemAvailable", 0)
            if total <= 0:
                return None
            used = total - available
            return (used / total) * 100.0
        except FileNotFoundError:
            return None
        except Exception:
            return None


# ---------------------------------------------------------------------------
# MeshSync — UDP multicast for broadcasting proxy health (observability)
# ---------------------------------------------------------------------------
class MeshSync:
    """Broadcasts local proxy health over UDP multicast 239.255.255.250:42100
    so that other OWL instances on the same LAN can observe this node's
    capacity. v7.1 is honest about what this is: observability broadcast,
    NOT load balancing. The proxy does not route requests to peers.

    Methods:
        broadcast_loop()  — sends local health every 30 s
        listen_loop()     — receives peer health (for logging / future use)
    """

    MULTICAST_GROUP: str = "239.255.255.250"
    BROADCAST_INTERVAL: float = 30.0

    def __init__(self, port: int = OWL_MESH_PORT) -> None:
        self.port = port
        self._transport: Optional[asyncio.DatagramTransport] = None
        self._recv_transport: Optional[asyncio.DatagramTransport] = None
        self._broadcast_task: Optional[asyncio.Task[None]] = None
        self._peers: Dict[str, Dict[str, Any]] = {}

    # ----- public API --------------------------------------------------

    def start(self) -> None:
        self._setup_send_socket()
        self._setup_recv_socket()
        self._broadcast_task = asyncio.ensure_future(self.broadcast_loop())
        logger.info("MeshSync started on %s:%d (observability-only)", self.MULTICAST_GROUP, self.port)

    async def stop(self) -> None:
        if self._broadcast_task and not self._broadcast_task.done():
            self._broadcast_task.cancel()
            try:
                await self._broadcast_task
            except asyncio.CancelledError:
                pass
        for t in (self._transport, self._recv_transport):
            if t:
                t.close()
        logger.info("MeshSync stopped")

    async def broadcast_loop(self) -> None:
        """Periodically broadcast local proxy health over multicast."""
        while True:
            try:
                payload = json.dumps({
                    "type": "owl-mesh",
                    "host": OWL_PROXY_HOST,
                    "port": OWL_PROXY_PORT,
                    "max_connections": OWL_MAX_CONNECTIONS,
                    "timestamp": time.time(),
                }).encode("utf-8")
                if self._transport:
                    self._transport.sendto(
                        payload, (self.MULTICAST_GROUP, self.port)
                    )
                    logger.debug("MeshSync broadcast sent")
            except Exception as exc:
                logger.warning("MeshSync broadcast error: %s", exc)
            await asyncio.sleep(self.BROADCAST_INTERVAL)

    # ----- internals ---------------------------------------------------

    def _setup_send_socket(self) -> None:
        sock = socket.socket(
            socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP
        )
        sock.setsockopt(
            socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1
        )
        loop = asyncio.get_running_loop()
        coro = loop.create_datagram_endpoint(
            lambda: asyncio.DatagramProtocol(), sock=sock
        )
        asyncio.ensure_future(coro).add_done_callback(self._send_ready)

    def _send_ready(self, fut: asyncio.Future) -> None:
        try:
            self._transport, _ = fut.result()
        except Exception as exc:
            logger.warning("MeshSync send socket setup failed: %s", exc)
            self._transport = None

    def _setup_recv_socket(self) -> None:
        sock = socket.socket(
            socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP
        )
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("", self.port))
        mreq = struct.pack(
            "4sl",
            socket.inet_aton(self.MULTICAST_GROUP),
            socket.INADDR_ANY,
        )
        sock.setsockopt(
            socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq
        )
        loop = asyncio.get_running_loop()
        fut = loop.create_datagram_endpoint(
            lambda: _MeshRecvProtocol(self._on_peer), sock=sock
        )
        asyncio.ensure_future(fut).add_done_callback(self._recv_ready)

    def _recv_ready(self, fut: asyncio.Future) -> None:
        try:
            transport, _ = fut.result()
            self._recv_transport = transport
        except Exception as exc:
            logger.warning("MeshSync recv socket setup failed: %s", exc)

    def _on_peer(self, data: bytes, addr: Tuple[str, int]) -> None:
        try:
            msg = json.loads(data.decode("utf-8"))
            if msg.get("type") == "owl-mesh":
                peer = f"{msg.get('host')}:{msg.get('port')}"
                self._peers[peer] = {**msg, "last_seen": time.monotonic()}
                logger.debug("MeshSync peer seen: %s", peer)
        except json.JSONDecodeError:
            pass


class _MeshRecvProtocol(asyncio.DatagramProtocol):
    def __init__(self, cb):
        self._cb = cb

    def datagram_received(self, data: bytes, addr: Tuple[str, int]) -> None:
        self._cb(data, addr)


# ---------------------------------------------------------------------------
# PredictiveCircuitBreaker — per-domain circuit breaking
# ---------------------------------------------------------------------------
class CircuitState(enum.Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"
    PREDICTIVE_OPEN = "PREDICTIVE_OPEN"


@dataclass
class DomainCircuit:
    state: CircuitState = CircuitState.CLOSED
    latencies: Deque[float] = field(default_factory=lambda: deque(maxlen=20))
    failure_count: int = 0
    last_failure_time: float = 0.0
    opened_at: float = 0.0

    @property
    def p50(self) -> float:
        if not self.latencies:
            return 0.0
        sorted_lat = sorted(self.latencies)
        return sorted_lat[len(sorted_lat) // 2]

    @property
    def has_baseline(self) -> bool:
        return len(self.latencies) >= 5


class PredictiveCircuitBreaker:
    """Tracks the last 20 request latencies per domain and opens the
    circuit predictively when recent requests significantly exceed the
    p50 baseline.

    Predictive trigger: after 5 samples, if the last 3 requests all
    exceed 2x p50 → PREDICTIVE_OPEN.
    Standard trigger: 5 consecutive failures → OPEN.
    Recovery: after 60 s in OPEN/PREDICTIVE_OPEN → HALF_OPEN (one probe
    request allowed). Success closes; failure re-opens.
    """

    FAILURE_THRESHOLD: int = 5
    PREDICTIVE_WINDOW: int = 3
    PREDICTIVE_MULTIPLIER: float = 2.0
    BASELINE_MIN_SAMPLES: int = 5
    RECOVERY_TIMEOUT: float = 60.0

    def __init__(self) -> None:
        self._circuits: Dict[str, DomainCircuit] = {}

    def _get_circuit(self, domain: str) -> DomainCircuit:
        if domain not in self._circuits:
            self._circuits[domain] = DomainCircuit()
        return self._circuits[domain]

    def record_success(self, domain: str, latency: float) -> None:
        c = self._get_circuit(domain)
        c.latencies.append(latency)
        c.failure_count = 0
        if c.state == CircuitState.HALF_OPEN:
            c.state = CircuitState.CLOSED
            logger.info("Circuit CLOSED for %s (recovered)", domain)
        elif c.state == CircuitState.PREDICTIVE_OPEN:
            c.state = CircuitState.CLOSED
            logger.info("Circuit CLOSED for %s (predictive recovered)", domain)
        self._check_predictive(domain, c)

    def record_failure(self, domain: str) -> None:
        c = self._get_circuit(domain)
        c.failure_count += 1
        c.last_failure_time = time.monotonic()
        if c.state == CircuitState.HALF_OPEN:
            c.state = CircuitState.OPEN
            c.opened_at = time.monotonic()
            logger.warning("Circuit OPEN for %s (half-open probe failed)", domain)
            return
        if c.failure_count >= self.FAILURE_THRESHOLD:
            c.state = CircuitState.OPEN
            c.opened_at = time.monotonic()
            logger.warning(
                "Circuit OPEN for %s (%d consecutive failures)",
                domain, c.failure_count,
            )

    def is_open(self, domain: str) -> bool:
        c = self._get_circuit(domain)
        if c.state == CircuitState.CLOSED:
            return False
        if c.state in (CircuitState.OPEN, CircuitState.PREDICTIVE_OPEN):
            if time.monotonic() - c.opened_at >= self.RECOVERY_TIMEOUT:
                c.state = CircuitState.HALF_OPEN
                logger.info("Circuit HALF_OPEN for %s (recovery timeout)", domain)
                return False
            return True
        return False  # HALF_OPEN: allow probe

    def _check_predictive(self, domain: str, c: DomainCircuit) -> None:
        if not c.has_baseline:
            return
        if c.state in (CircuitState.OPEN, CircuitState.PREDICTIVE_OPEN):
            return
        baseline = c.p50
        if baseline <= 0:
            return
        recent = list(c.latencies)[-self.PREDICTIVE_WINDOW:]
        if len(recent) < self.PREDICTIVE_WINDOW:
            return
        threshold = baseline * self.PREDICTIVE_MULTIPLIER
        if all(lat > threshold for lat in recent):
            c.state = CircuitState.PREDICTIVE_OPEN
            c.opened_at = time.monotonic()
            logger.warning(
                "Circuit PREDICTIVE_OPEN for %s (last %d > %.1fx baseline %.0fms)",
                domain, self.PREDICTIVE_WINDOW, self.PREDICTIVE_MULTIPLIER, baseline,
            )


# ---------------------------------------------------------------------------
# Upstream proxy resolution
# ---------------------------------------------------------------------------
def resolve_upstream_proxy() -> Optional[Tuple[str, int]]:
    if not UPSTREAM_PROXY:
        return None
    try:
        p = urlparse(UPSTREAM_PROXY)
        return (p.hostname or "127.0.0.1", p.port or 7890)
    except Exception as exc:
        logger.warning("Invalid UPSTREAM_PROXY '%s': %s", UPSTREAM_PROXY, exc)
        return None


# ---------------------------------------------------------------------------
# Auth check
# ---------------------------------------------------------------------------
def check_auth(headers: list) -> bool:
    """If OWL_PROXY_TOKEN is set, require Proxy-Authorization: Bearer <token>."""
    if not OWL_PROXY_TOKEN:
        return True
    expected = f"Bearer {OWL_PROXY_TOKEN}"
    for h in headers:
        if h.lower().startswith("proxy-authorization:") and h.split(":", 1)[1].strip() == expected:
            return True
    return False


# ---------------------------------------------------------------------------
# Stream relay helper
# ---------------------------------------------------------------------------
async def _relay(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    name: str,
) -> None:
    try:
        while True:
            data = await reader.read(BUFFER_SIZE)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except (ConnectionResetError, BrokenPipeError, asyncio.CancelledError):
        pass
    except Exception as exc:
        logger.debug("%s relay error: %s", name, exc)
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Main proxy handler
# ---------------------------------------------------------------------------
class OwlForwardProxy:
    """Asyncio HTTP/HTTPS forward proxy with SSRF allowlist, circuit
    breaking, optional bearer auth, and mesh health broadcast.
    """

    def __init__(self) -> None:
        self.auto_tuner = AutoTuner()
        self.mesh_sync = MeshSync() if OWL_ENABLE_MESH else None
        self.circuit_breaker = PredictiveCircuitBreaker()
        self._semaphore = asyncio.Semaphore(OWL_MAX_CONNECTIONS)
        self._server: Optional[asyncio.AbstractServer] = None

    async def start(self) -> None:
        # Safety: if binding non-loopback, require auth token.
        if OWL_PROXY_HOST not in ("127.0.0.1", "::1", "localhost") and not OWL_PROXY_TOKEN:
            raise RuntimeError(
                "Refusing to bind non-loopback address without OWL_PROXY_TOKEN. "
                "Set OWL_PROXY_TOKEN=<random> or bind 127.0.0.1."
            )

        self.auto_tuner.start()
        if self.mesh_sync:
            self.mesh_sync.start()

        self._server = await asyncio.start_server(
            self._accept, OWL_PROXY_HOST, OWL_PROXY_PORT,
        )
        logger.info(
            "OWL Forward Proxy v7.1 listening on %s:%d (max_conn=%d)",
            OWL_PROXY_HOST, OWL_PROXY_PORT, OWL_MAX_CONNECTIONS,
        )
        logger.info("Allowed domains: %d entries", len(ALLOWED_DOMAINS))
        if UPSTREAM_PROXY:
            logger.info("Upstream proxy: %s", UPSTREAM_PROXY)
        if OWL_PROXY_TOKEN:
            logger.info("Auth: bearer token required")

    async def run_forever(self) -> None:
        if self._server:
            async with self._server:
                await self._server.serve_forever()

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        await self.auto_tuner.stop()
        if self.mesh_sync:
            await self.mesh_sync.stop()
        logger.info("OWL Forward Proxy stopped")

    # ----- connection acceptance ---------------------------------------

    async def _accept(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Accept a new client connection, enforcing the concurrency limit.

        v7.1: Semaphore is created once at startup and never recreated.
        AutoTuner no longer mutates OWL_MAX_CONNECTIONS at runtime —
        if you want a different limit, restart the proxy.
        """
        async with self._semaphore:
            try:
                await self.handle_client(reader, writer)
            except Exception as exc:
                logger.error("Unhandled client error: %s", exc)
            finally:
                try:
                    writer.close()
                    await writer.wait_closed()
                except Exception:
                    pass

    # ----- main request handler ----------------------------------------

    async def handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        peer = writer.get_extra_info("peername")
        try:
            header_line = await asyncio.wait_for(
                reader.readline(), timeout=OWL_PROXY_TIMEOUT
            )
        except asyncio.TimeoutError:
            return

        if not header_line:
            return

        try:
            request_line = header_line.decode("utf-8", errors="replace").strip()
            parts = request_line.split()
            if len(parts) < 2:
                return
            method, target = parts[0], parts[1]
        except Exception:
            return

        # Health check endpoint (only honored on loopback)
        if method == "GET" and target in ("/health", "/healthz"):
            peer_ip = peer[0] if peer else ""
            if peer_ip not in ("127.0.0.1", "::1"):
                writer.write(b"HTTP/1.1 403 Forbidden\r\n\r\n")
                return
            body = json.dumps({
                "status": "ok",
                "version": "7.1.0",
                "max_connections": OWL_MAX_CONNECTIONS,
                "allowed_domains": len(ALLOWED_DOMAINS),
                "mesh_enabled": OWL_ENABLE_MESH,
            }).encode("utf-8")
            writer.write(
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: application/json\r\n"
                b"Content-Length: " + str(len(body)).encode() + b"\r\n"
                b"Connection: close\r\n\r\n" + body
            )
            return

        logger.debug("%s %s from %s", method, target, peer)

        # Read all headers (needed for both CONNECT and HTTP for auth check)
        headers: list = []
        try:
            while True:
                line = await asyncio.wait_for(
                    reader.readline(), timeout=OWL_PROXY_TIMEOUT
                )
                if line in (b"\r\n", b"\n", b""):
                    break
                headers.append(line.decode("utf-8", errors="replace").strip())
        except asyncio.TimeoutError:
            return

        # Auth check
        if not check_auth(headers):
            writer.write(b"HTTP/1.1 407 Proxy Authentication Required\r\n")
            writer.write(b"Proxy-Authenticate: Bearer\r\n\r\n")
            return

        if method == "CONNECT":
            await self._handle_connect(reader, writer, target)
        else:
            await self._handle_http(reader, writer, method, target, request_line, headers)

    # ----- CONNECT (HTTPS tunnel) --------------------------------------

    async def _handle_connect(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        target: str,
    ) -> None:
        if ":" in target:
            host, port_str = target.rsplit(":", 1)
            try:
                port = int(port_str)
            except ValueError:
                writer.write(b"HTTP/1.1 400 Bad Request\r\n\r\n")
                return
        else:
            host, port = target, 443

        # SSRF allowlist check (hostname level)
        if not is_allowed_target(host):
            logger.warning("SSRF rejected CONNECT to %s (not in allowlist)", host)
            writer.write(b"HTTP/1.1 403 Forbidden\r\n\r\n")
            return

        # Circuit breaker check
        if self.circuit_breaker.is_open(host):
            writer.write(b"HTTP/1.1 503 Service Unavailable\r\n\r\n")
            return

        bypass = is_allowed_target(host)  # always true here; kept for clarity
        upstream = resolve_upstream_proxy()
        _ = bypass  # not used; bypass logic replaced by SSRF allowlist

        start = time.monotonic()
        remote_writer: Optional[asyncio.StreamWriter] = None

        try:
            if upstream:
                # Connect via upstream proxy
                remote_reader, remote_writer = await asyncio.wait_for(
                    asyncio.open_connection(upstream[0], upstream[1]),
                    timeout=OWL_CONNECT_TIMEOUT,
                )
                connect_req = (
                    f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n\r\n"
                ).encode("utf-8")
                remote_writer.write(connect_req)
                await remote_writer.drain()

                resp = await asyncio.wait_for(
                    remote_reader.readline(), timeout=OWL_CONNECT_TIMEOUT
                )
                # Strict status line check (was: b"200" not in resp — too loose)
                if not resp or not resp.startswith(b"HTTP/1.1 200") and not resp.startswith(b"HTTP/1.0 200"):
                    logger.warning("Upstream proxy rejected CONNECT %s: %s",
                                   target, resp.decode("utf-8", errors="replace").strip())
                    writer.write(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
                    return

                # Drain upstream headers
                while True:
                    line = await asyncio.wait_for(
                        remote_reader.readline(), timeout=OWL_CONNECT_TIMEOUT
                    )
                    if line in (b"\r\n", b"\n", b""):
                        break
            else:
                # Direct connection — resolve and verify IP is safe
                # (DNS-rebinding defense)
                resolved = await _resolve_and_verify(host, port)
                if resolved is None:
                    logger.warning("SSRF rejected CONNECT to %s (no safe IP resolved)", host)
                    writer.write(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
                    return
                remote_reader, remote_writer = await asyncio.wait_for(
                    asyncio.open_connection(resolved[0], resolved[1]),
                    timeout=OWL_CONNECT_TIMEOUT,
                )

            writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await writer.drain()

            await asyncio.gather(
                _relay(reader, remote_writer, f"client→{host}"),
                _relay(remote_reader, writer, f"{host}→client"),
            )

            latency = time.monotonic() - start
            self.circuit_breaker.record_success(host, latency)

        except asyncio.TimeoutError:
            self.circuit_breaker.record_failure(host)
            logger.warning("CONNECT timeout to %s", target)
            try:
                writer.write(b"HTTP/1.1 504 Gateway Timeout\r\n\r\n")
            except Exception:
                pass
        except ConnectionRefusedError:
            self.circuit_breaker.record_failure(host)
            try:
                writer.write(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
            except Exception:
                pass
        except Exception as exc:
            self.circuit_breaker.record_failure(host)
            logger.warning("CONNECT error for %s: %s", target, exc)
            try:
                writer.write(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
            except Exception:
                pass
        finally:
            if remote_writer:
                try:
                    remote_writer.close()
                    await remote_writer.wait_closed()
                except Exception:
                    pass

    # ----- HTTP (forward) ----------------------------------------------

    async def _handle_http(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        method: str,
        target: str,
        request_line: str,
        headers: list,
    ) -> None:
        parsed = urlparse(target)
        host = parsed.hostname or ""
        port = parsed.port or 80
        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"

        # SSRF allowlist check
        if not is_allowed_target(host):
            logger.warning("SSRF rejected HTTP %s to %s (not in allowlist)", method, host)
            writer.write(b"HTTP/1.1 403 Forbidden\r\n\r\n")
            return

        # Circuit breaker check
        if self.circuit_breaker.is_open(host):
            writer.write(b"HTTP/1.1 503 Service Unavailable\r\n\r\n")
            return

        # Read body if present (with size limit)
        content_length = 0
        for h in headers:
            if h.lower().startswith("content-length:"):
                try:
                    content_length = int(h.split(":", 1)[1].strip())
                except ValueError:
                    pass
                break

        body = b""
        if content_length > 0:
            if content_length > MAX_BODY_SIZE:
                logger.warning("Request body too large: %d bytes for %s", content_length, target)
                writer.write(b"HTTP/1.1 413 Payload Too Large\r\n\r\n")
                return
            try:
                body = await asyncio.wait_for(
                    reader.readexactly(content_length),
                    timeout=OWL_PROXY_TIMEOUT,
                )
            except (asyncio.TimeoutError, asyncio.IncompleteReadError):
                return

        # Build outgoing request
        outgoing_headers = f"{method} {path} HTTP/1.1\r\n"
        outgoing_headers += f"Host: {host}\r\n"
        for h in headers:
            lower = h.lower()
            if lower.startswith("proxy-") or lower.startswith("connection:"):
                continue
            outgoing_headers += f"{h}\r\n"
        outgoing_headers += "Connection: close\r\n\r\n"

        upstream = resolve_upstream_proxy()
        start = time.monotonic()
        remote_writer: Optional[asyncio.StreamWriter] = None

        try:
            if upstream:
                remote_reader, remote_writer = await asyncio.wait_for(
                    asyncio.open_connection(upstream[0], upstream[1]),
                    timeout=OWL_CONNECT_TIMEOUT,
                )
                proxy_req = f"{method} {target} HTTP/1.1\r\n"
                for h in headers:
                    lower = h.lower()
                    if lower.startswith("proxy-"):
                        continue
                    proxy_req += f"{h}\r\n"
                proxy_req += "Connection: close\r\n\r\n"
                remote_writer.write(proxy_req.encode("utf-8"))
            else:
                # Direct connection — DNS-rebinding defense
                resolved = await _resolve_and_verify(host, port)
                if resolved is None:
                    logger.warning("SSRF rejected HTTP %s to %s (no safe IP resolved)", method, host)
                    writer.write(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
                    return
                remote_reader, remote_writer = await asyncio.wait_for(
                    asyncio.open_connection(resolved[0], resolved[1]),
                    timeout=OWL_CONNECT_TIMEOUT,
                )
                remote_writer.write(outgoing_headers.encode("utf-8"))

            if body:
                remote_writer.write(body)
            await remote_writer.drain()

            await _relay(remote_reader, writer, f"http:{host}")

            latency = time.monotonic() - start
            self.circuit_breaker.record_success(host, latency)

        except asyncio.TimeoutError:
            self.circuit_breaker.record_failure(host)
            logger.warning("HTTP timeout for %s %s", method, target)
        except Exception as exc:
            self.circuit_breaker.record_failure(host)
            logger.warning("HTTP error for %s %s: %s", method, target, exc)
        finally:
            if remote_writer:
                try:
                    remote_writer.close()
                    await remote_writer.wait_closed()
                except Exception:
                    pass


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
async def main() -> None:
    proxy = OwlForwardProxy()
    loop = asyncio.get_running_loop()

    def _shutdown() -> None:
        asyncio.ensure_future(proxy.stop())

    try:
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, _shutdown)
    except (AttributeError, NotImplementedError):
        pass

    await proxy.start()

    logger.info(
        "Config: host=%s port=%d max_conn=%d connect_timeout=%d proxy_timeout=%d "
        "mesh=%s auth=%s",
        OWL_PROXY_HOST, OWL_PROXY_PORT, OWL_MAX_CONNECTIONS,
        OWL_CONNECT_TIMEOUT, OWL_PROXY_TIMEOUT,
        OWL_ENABLE_MESH, bool(OWL_PROXY_TOKEN),
    )

    try:
        await proxy.run_forever()
    except KeyboardInterrupt:
        pass
    finally:
        await proxy.stop()


if __name__ == "__main__":
    asyncio.run(main())
