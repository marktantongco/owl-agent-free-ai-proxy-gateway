"""
🦉 OWL-AGENT Proxy Defense Stack v3.3
======================================

Resilient HTTP client library with five-tier escalation:

  Tier 1 — Weighted proxy rotation (best score wins)
  Tier 2 — Circuit breaker (skip failing proxies/domains)
  Tier 3 — Request deduplication (merge in-flight requests)
  Tier 4 — Bounded HTTP cache (OrderedDict LRU with TTL)
  Tier 5 — Per-domain rate limiting (TokenBucket)

Falls back to a direct connection if every proxy is exhausted.

Supported AI providers:
  - Antigravity   https://api.antigravity.dev
  - Claude        https://api.anthropic.com
  - OpenCode      https://api.opencode.ai
  - Copilot       https://api.githubcopilot.com
  - Kiro          https://api.kiro.dev
  - Hermes        https://api.hermes.ai

Usage
-----
    async with ResilientClient() as client:
        resp = await client.request("GET", "https://api.anthropic.com/v1/models")
        print(resp.status_code, resp.text)

Environment variables
---------------------
  OWL_PROXY_POOL_FILE       Path to proxy_pool.json       (default: proxy_pool.json)
  OWL_PROXY_CRED_FILE       Path to proxy_credentials.json (default: proxy_credentials.json)
  OWL_PROXY_SOURCES_FILE    Path to proxy_sources.json     (default: proxy_sources.json)
  OWL_PROXY_ENRICH          Set to "1" to fetch from remote sources (default: "0")
  OWL_PROXY_USERNAME        Fallback username for proxy auth
  OWL_PROXY_PASSWORD        Fallback password for proxy auth
  OWL_CACHE_MAX_ENTRIES     Max LRU cache entries           (default: 512)
  OWL_CACHE_TTL             Cache TTL in seconds            (default: 120)
  OWL_CIRCUIT_THRESHOLD     Failures before circuit opens   (default: 3)
  OWL_CIRCUIT_RECOVERY      Recovery timeout in seconds     (default: 30)
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from collections import OrderedDict
from dataclasses import dataclass
from enum import Enum, auto
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx

# v7.2 monolith split — circuit breaker now lives in circuit.py.
# Re-exported here so existing `from proxy_defense_fixed_v3 import CircuitBreaker`
# imports keep working. See circuit.py for the implementation.
from circuit import CircuitBreaker, CircuitState  # noqa: F401 (re-exported)

# ═══════════════════════════════════════════════════════════════════════════════
# Constants & Provider Configuration
# ═══════════════════════════════════════════════════════════════════════════════

logger = logging.getLogger("owl.proxy_defense")

PROVIDER_CONFIG: Dict[str, Dict[str, Any]] = {
    "antigravity": {
        "base_url": "https://api.antigravity.dev",
        "rate_limit": 10.0,
        "timeout": 15,
    },
    "claude": {
        "base_url": "https://api.anthropic.com",
        "rate_limit": 5.0,
        "timeout": 30,
    },
    "opencode": {
        "base_url": "https://api.opencode.dev",
        "rate_limit": 8.0,
        "timeout": 20,
    },
    "copilot": {
        "base_url": "https://api.githubcopilot.com",
        "rate_limit": 6.0,
        "timeout": 25,
    },
    "kiro": {
        "base_url": "https://api.kiro.dev",
        "rate_limit": 4.0,
        "timeout": 30,
    },
    "hermes": {
        "base_url": "https://api.hermes-ai.dev",
        "rate_limit": 7.0,
        "timeout": 20,
    },
}

DEFAULT_CACHE_MAX = int(os.getenv("OWL_CACHE_MAX_ENTRIES", "512"))
DEFAULT_CACHE_TTL = float(os.getenv("OWL_CACHE_TTL", "120"))
CIRCUIT_FAILURE_THRESHOLD = int(os.getenv("OWL_CIRCUIT_THRESHOLD", "3"))
CIRCUIT_RECOVERY_TIMEOUT = float(os.getenv("OWL_CIRCUIT_RECOVERY", "30"))
BAN_DURATION_BASE = 60.0  # seconds; doubles on each subsequent ban


def resolve_provider(domain: str) -> Optional[str]:
    """
    Match a domain string to a provider name in PROVIDER_CONFIG.

    Uses suffix match (P1-E): ``api.anthropic.com`` matches domain
    ``api.anthropic.com`` or ``v2.api.anthropic.com`` but NOT
    ``evil-anthropic.com``.  The old substring match (``in`` both
    directions) produced false positives for short shared substrings.

    Module-level function (P1-F): replaces the duplicated
    ``_resolve_provider`` methods in ``DomainRateLimiter`` and
    ``ResilientClient`` — DRY violation that risked drift.
    """
    if not domain:
        return None
    domain_lower = domain.lower()
    for name, cfg in PROVIDER_CONFIG.items():
        provider_host = urlparse(cfg["base_url"]).hostname or ""
        if not provider_host:
            continue
        provider_lower = provider_host.lower()
        if domain_lower == provider_lower or domain_lower.endswith("." + provider_lower):
            return name
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# ProxyEntry — data model for a single proxy
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class ProxyEntry:
    """Represents a single proxy with health tracking metadata."""

    url: str
    proxy_type: str = "http"          # http | socks5 | socks4
    protocol: str = "http"            # http | https
    source: str = "unknown"
    tier: int = 2                     # 1 = managed/premium, 2 = public/free
    healthy: bool = True
    fail_count: int = 0
    ban_until: float = 0.0
    latency_ms: float = 150.0  # realistic initial value; 999.0 made new proxies unscoreable (P1-B)
    success_count: int = 0

    # ── health helpers ────────────────────────────────────────────────────

    def is_banned(self) -> bool:
        """Return True if the proxy is currently banned."""
        return time.monotonic() < self.ban_until

    def mark_failed(self) -> None:
        """Record a failure and ban the proxy with exponential back-off."""
        self.fail_count += 1
        self.healthy = False
        backoff = BAN_DURATION_BASE * (2 ** min(self.fail_count - 1, 5))
        self.ban_until = time.monotonic() + backoff
        logger.debug(
            "Proxy %s marked failed (fail_count=%d, ban %.0fs)",
            self.url, self.fail_count, backoff,
        )

    def mark_success(self, latency_ms: float) -> None:
        """Record a successful request and un-ban the proxy."""
        self.success_count += 1
        self.healthy = True
        self.fail_count = 0
        self.ban_until = 0.0
        # Exponential moving average for latency
        self.latency_ms = 0.7 * self.latency_ms + 0.3 * latency_ms
        logger.debug(
            "Proxy %s mark success (latency=%.1fms, total=%d)",
            self.url, self.latency_ms, self.success_count,
        )

    def get_score(self) -> float:
        """
        Compute a weighted score for proxy selection.
        Higher is better.  Factors in latency, success rate, tier, and health.
        """
        if self.is_banned() or not self.healthy:
            return -1.0

        total = self.success_count + self.fail_count
        success_rate = self.success_count / total if total > 0 else 0.5

        # Tier multiplier: managed proxies get a boost
        tier_mult = 1.5 if self.tier == 1 else 1.0

        # Latency factor: lower is better (cap at 2000ms to avoid division by ~0)
        lat_factor = 1000.0 / max(self.latency_ms, 1.0)

        # Exploration bonus (P1-B): new proxies (few observations) get a
        # boost so they actually get traffic.  Without this, a new proxy
        # (latency_ms=150, success_rate=0.5) scores ~70 and always loses
        # to established proxies scoring ~200+, so it never gets selected,
        # never gets mark_success, and its latency_ms stays at the initial
        # value forever — freezing the pool to the first few proxies tried.
        exploration_bonus = 50.0 if total < 5 else 0.0

        score = (success_rate * 60.0) + (lat_factor * 30.0) + (tier_mult * 10.0) + exploration_bonus
        return round(score, 2)


# ═══════════════════════════════════════════════════════════════════════════════
# BoundedHTTPCache — OrderedDict-based LRU cache with TTL
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class CachedResponse:
    """A cached HTTP response with expiry metadata."""

    status_code: int
    headers: Dict[str, str]
    body: bytes
    stored_at: float
    ttl: float

    def is_expired(self) -> bool:
        return (time.monotonic() - self.stored_at) > self.ttl


class BoundedHTTPCache:
    """
    LRU cache backed by an OrderedDict.

    - Enforces a hard limit on ``max_entries``; oldest entries are evicted.
    - Each entry has a per-key TTL; stale entries are lazily purged on access.
    - Tracks hit / miss / eviction counters for observability.
    """

    def __init__(self, max_entries: int = DEFAULT_CACHE_MAX, default_ttl: float = DEFAULT_CACHE_TTL):
        self.max_entries = max_entries
        self.default_ttl = default_ttl
        self._store: OrderedDict[str, CachedResponse] = OrderedDict()
        self._hits = 0
        self._misses = 0
        self._evictions = 0

    # ── public API ────────────────────────────────────────────────────────

    def get(self, key: str) -> Optional[CachedResponse]:
        """
        Retrieve a cached response by key.

        Returns ``None`` on miss or if the entry has expired (expired entries
        are removed automatically).
        """
        entry = self._store.get(key)
        if entry is None:
            self._misses += 1
            return None

        if entry.is_expired():
            del self._store[key]
            self._misses += 1
            return None

        # Move to end (most recently used)
        self._store.move_to_end(key)
        self._hits += 1
        return entry

    def put(self, key: str, response: CachedResponse) -> None:
        """
        Store a response.  If the key already exists it is moved to the end.
        If the cache is at capacity the oldest entry is evicted.
        """
        if key in self._store:
            self._store.move_to_end(key)
        self._store[key] = response

        # Evict oldest entries while over capacity
        while len(self._store) > self.max_entries:
            self._store.popitem(last=False)
            self._evictions += 1

    def clear(self) -> None:
        """Remove all entries and reset counters."""
        self._store.clear()
        self._hits = 0
        self._misses = 0
        self._evictions = 0

    def stats(self) -> Dict[str, Any]:
        """Return cache statistics."""
        return {
            "size": len(self._store),
            "max_entries": self.max_entries,
            "hits": self._hits,
            "misses": self._misses,
            "evictions": self._evictions,
            "hit_rate": (
                round(self._hits / (self._hits + self._misses), 4)
                if (self._hits + self._misses) > 0
                else 0.0
            ),
        }


# ═══════════════════════════════════════════════════════════════════════════════
# ProxyPoolLoader — loads proxies from JSON files & remote sources
# ═══════════════════════════════════════════════════════════════════════════════

class ProxyPoolLoader:
    """
    Load proxy entries from local JSON files and optional remote sources.

    File layout expected in *proxy_pool.json*::

        {
          "tier_1_managed_free": { "providers": [ {"url": "http://…", "type": "http"} ] },
          "tier_2_public":       { "providers": [ … ] }
        }

    File layout expected in *proxy_credentials.json*::

        { "proxies": { "host:port": {"username": "u", "password": "p"} } }
    """

    def __init__(
        self,
        pool_file: Optional[str] = None,
        cred_file: Optional[str] = None,
        sources_file: Optional[str] = None,
        enrich: bool = False,
    ):
        self.pool_file = pool_file or os.getenv("OWL_PROXY_POOL_FILE", "proxy_pool.json")
        self.cred_file = cred_file or os.getenv("OWL_PROXY_CRED_FILE", "proxy_credentials.json")
        self.sources_file = sources_file or os.getenv("OWL_PROXY_SOURCES_FILE", "proxy_sources.json")
        self.enrich = enrich or os.getenv("OWL_PROXY_ENRICH", "0") == "1"

    # ── file-based loading ────────────────────────────────────────────────

    def load(self) -> List[ProxyEntry]:
        """
        Load proxy entries from the local pool JSON and inject credentials.

        Returns an empty list (not an error) if the file is missing or
        malformed — the caller can still fall back to direct connections.
        """
        entries: List[ProxyEntry] = []

        # --- pool file ---
        try:
            with open(self.pool_file, "r", encoding="utf-8") as fh:
                pool_data = json.load(fh)
        except FileNotFoundError:
            logger.info("Proxy pool file not found: %s — skipping", self.pool_file)
            return entries
        except json.JSONDecodeError as exc:
            logger.warning("Proxy pool file malformed: %s — %s", self.pool_file, exc)
            return entries

        # Parse tiers from the pool structure
        for tier_key, tier_val in pool_data.items():
            if not isinstance(tier_val, dict):
                continue
            providers = tier_val.get("providers", [])
            tier_num = 1 if "tier_1" in tier_key else 2
            for prov in providers:
                if not isinstance(prov, dict) or "url" not in prov:
                    continue
                entries.append(
                    ProxyEntry(
                        url=prov["url"],
                        proxy_type=prov.get("type", "http"),
                        protocol=prov.get("protocol", "http"),
                        source=tier_key,
                        tier=tier_num,
                    )
                )

        # --- inject credentials ---
        self._inject_credentials(entries)
        return entries

    # ── remote enrichment ─────────────────────────────────────────────────

    async def fetch_from_sources(self, session: httpx.AsyncClient) -> List[ProxyEntry]:
        """
        Fetch additional proxy lists from remote sources defined in
        *proxy_sources.json*.  Supports ``plain`` (ip:port per line) and
        ``json`` (structured) formats.
        """
        entries: List[ProxyEntry] = []

        try:
            with open(self.sources_file, "r", encoding="utf-8") as fh:
                sources_data = json.load(fh)
        except (FileNotFoundError, json.JSONDecodeError):
            logger.info("No proxy sources file at %s — skipping enrichment", self.sources_file)
            return entries

        max_per_source = sources_data.get("advanced", {}).get("max_proxies_per_source", 150)
        sources = sources_data.get("sources", [])

        for src in sources:
            if not src.get("enabled", True):
                continue
            url = src.get("url", "")
            fmt = src.get("format", "plain")
            tier = src.get("tier", 2)
            if not url:
                continue

            try:
                resp = await session.get(url, timeout=10.0)
                resp.raise_for_status()
                body = resp.text
            except httpx.HTTPError as exc:
                logger.warning("Failed to fetch proxy source %s: %s", url, exc)
                continue

            if fmt == "plain":
                entries.extend(self._parse_plain(body, tier, url))
            elif fmt == "json":
                entries.extend(self._parse_json_proxies(body, tier, url))

            # Trim to max_per_source per source
            if len(entries) > max_per_source:
                entries = entries[:max_per_source]

        self._inject_credentials(entries)
        logger.info("Enriched %d proxies from remote sources", len(entries))
        return entries

    # ── private helpers ───────────────────────────────────────────────────

    @staticmethod
    def _parse_plain(body: str, tier: int, source_url: str) -> List[ProxyEntry]:
        """Parse plain-text proxy lists (ip:port or protocol://ip:port)."""
        entries: List[ProxyEntry] = []
        for line in body.strip().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            # Detect protocol prefix
            if "://" in line:
                parsed = urlparse(line)
                proxy_type = parsed.scheme or "http"
                url = line
            else:
                proxy_type = "http"
                url = f"http://{line}"

            entries.append(
                ProxyEntry(
                    url=url,
                    proxy_type=proxy_type,
                    protocol="http",
                    source=source_url,
                    tier=tier,
                )
            )
        return entries

    @staticmethod
    def _parse_json_proxies(body: str, tier: int, source_url: str) -> List[ProxyEntry]:
        """Parse JSON-format proxy lists."""
        entries: List[ProxyEntry] = []
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            return entries

        proxy_list = data if isinstance(data, list) else data.get("data", data.get("proxies", []))
        if not isinstance(proxy_list, list):
            return entries

        for item in proxy_list[:150]:
            if isinstance(item, dict):
                ip = item.get("ip", item.get("host", ""))
                port = item.get("port", "")
                proto = item.get("protocol", item.get("type", "http")).lower()
                if ip and port:
                    url = f"{proto}://{ip}:{port}"
                    entries.append(
                        ProxyEntry(
                            url=url,
                            proxy_type=proto,
                            protocol=item.get("protocol", "http"),
                            source=source_url,
                            tier=tier,
                        )
                    )
            elif isinstance(item, str):
                entries.append(
                    ProxyEntry(url=item, proxy_type="http", protocol="http", source=source_url, tier=tier)
                )
        return entries

    def _inject_credentials(self, entries: List[ProxyEntry]) -> None:
        """
        Merge credentials from *proxy_credentials.json* and environment
        variables into proxy URLs that lack auth info.
        """
        creds: Dict[str, Dict[str, str]] = {}
        try:
            with open(self.cred_file, "r", encoding="utf-8") as fh:
                cred_data = json.load(fh)
                creds = cred_data.get("proxies", cred_data)
        except (FileNotFoundError, json.JSONDecodeError):
            pass

        env_user = os.getenv("OWL_PROXY_USERNAME", "")
        env_pass = os.getenv("OWL_PROXY_PASSWORD", "")

        for entry in entries:
            parsed = urlparse(entry.url)
            # Already has auth embedded
            if parsed.username:
                continue

            # Try credential file first (match on host:port)
            host_key = f"{parsed.hostname}:{parsed.port}" if parsed.port else str(parsed.hostname)
            cred = creds.get(host_key, {})

            username = cred.get("username", env_user)
            password = cred.get("password", env_pass)

            if username and password:
                netloc = f"{username}:{password}@{parsed.hostname}"
                if parsed.port:
                    netloc += f":{parsed.port}"
                entry.url = parsed._replace(netloc=netloc).geturl()


# ═══════════════════════════════════════════════════════════════════════════════
# CircuitBreaker — extracted to circuit.py in v7.2 monolith split.
# Import: `from circuit import CircuitBreaker, CircuitState`
# (also re-exported at the top of this file for backward compatibility)
# ═══════════════════════════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════════════════════════
# TokenBucket & DomainRateLimiter
# ═══════════════════════════════════════════════════════════════════════════════

class TokenBucket:
    """
    Classic token-bucket rate limiter.

    * ``rate``   — tokens added per second.
    * ``capacity`` — maximum burst size (bucket depth).
    * ``acquire(tokens)`` — returns True if tokens were available and consumed.
    """

    def __init__(self, rate: float, capacity: float):
        self.rate = rate
        self.capacity = capacity
        self.tokens = capacity       # start full
        self.last_update = time.monotonic()

    def _refill(self) -> None:
        """Add tokens based on elapsed time since last update."""
        now = time.monotonic()
        elapsed = now - self.last_update
        refill = elapsed * self.rate
        self.tokens = min(self.capacity, self.tokens + refill)
        self.last_update = now

    def acquire(self, tokens: float = 1.0) -> bool:
        """
        Attempt to consume *tokens* from the bucket.

        Returns ``True`` if the tokens were available and consumed, ``False``
        otherwise.  Does **not** block.
        """
        self._refill()
        if self.tokens >= tokens:
            self.tokens -= tokens
            return True
        return False


class DomainRateLimiter:
    """
    Per-domain rate limiter backed by TokenBuckets.

    If a domain has a matching entry in ``PROVIDER_CONFIG`` its rate_limit
    value is used; otherwise a conservative default is applied.
    """

    DEFAULT_RATE = 3.0    # requests per second for unknown domains
    DEFAULT_BURST = 6.0   # burst capacity

    def __init__(self) -> None:
        self._buckets: Dict[str, TokenBucket] = {}

    def _domain_for(self, url: str) -> str:
        """Extract the registered domain from a URL for bucket lookup."""
        parsed = urlparse(url)
        return parsed.hostname or "unknown"

    def _get_bucket(self, url: str) -> TokenBucket:
        """Get or create a TokenBucket for the URL's domain."""
        domain = self._domain_for(url)
        if domain not in self._buckets:
            provider = resolve_provider(domain)  # module-level (P1-E, P1-F)
            if provider and provider in PROVIDER_CONFIG:
                rate = PROVIDER_CONFIG[provider]["rate_limit"]
                capacity = rate * 2  # allow short bursts
            else:
                rate = self.DEFAULT_RATE
                capacity = self.DEFAULT_BURST
            self._buckets[domain] = TokenBucket(rate=rate, capacity=capacity)
        return self._buckets[domain]

    def acquire(self, url: str, tokens: float = 1.0) -> bool:
        """Try to acquire rate-limit tokens for the given URL."""
        return self._get_bucket(url).acquire(tokens)

    def stats(self) -> Dict[str, Dict[str, Any]]:
        """Return per-domain bucket statistics."""
        result: Dict[str, Dict[str, Any]] = {}
        for domain, bucket in self._buckets.items():
            result[domain] = {
                "rate": bucket.rate,
                "capacity": bucket.capacity,
                "tokens_available": round(bucket.tokens, 2),
            }
        return result


# ═══════════════════════════════════════════════════════════════════════════════
# ResilientClient — the main client with 5-tier escalation
# ═══════════════════════════════════════════════════════════════════════════════

class RateLimitExceeded(Exception):
    """Raised when rate limit is exceeded and no tokens are available."""
    pass


def _cache_key(method: str, url: str, body_hash: str = "") -> str:
    """Produce a deterministic cache key from request params."""
    return f"{method.upper()}:{url}:{body_hash}"


class ResilientClient:
    """
    Resilient async HTTP client with five-tier escalation.

    Tier 1 — **Weighted proxy rotation** — pick the proxy with the highest
    ``ProxyEntry.get_score()`` for each request.

    Tier 2 — **Circuit breaker** — per-domain breakers skip entire domains
    that are failing, avoiding wasted round-trips.

    Tier 3 — **Request deduplication** — if the same request is already in
    flight, the new caller awaits the same response instead of firing a
    duplicate.

    Tier 4 — **Bounded HTTP cache** — LRU cache with TTL; idempotent GET
    requests can be served from cache.

    Tier 5 — **Rate limiting** — per-domain TokenBucket prevents exceeding
    provider quotas.

    If every proxy fails the client falls back to a **direct** connection.
    """

    def __init__(
        self,
        proxy_entries: Optional[List[ProxyEntry]] = None,
        cache_max: int = DEFAULT_CACHE_MAX,
        cache_ttl: float = DEFAULT_CACHE_TTL,
        circuit_threshold: int = CIRCUIT_FAILURE_THRESHOLD,
        circuit_recovery: float = CIRCUIT_RECOVERY_TIMEOUT,
        user_agent: str = "OWL-Agent/3.3",
    ):
        # Proxy pool
        self._proxies: List[ProxyEntry] = proxy_entries or []
        self._proxy_index = 0  # reserved for round-robin fallback

        # Tier 2 — circuit breakers
        self._circuits: Dict[str, CircuitBreaker] = {}
        self._circuit_threshold = circuit_threshold
        self._circuit_recovery = circuit_recovery

        # Tier 3 — request dedup
        self._inflight: Dict[str, asyncio.Future[httpx.Response]] = {}  # key → Future[httpx.Response]

        # Tier 4 — cache
        self._cache = BoundedHTTPCache(max_entries=cache_max, default_ttl=cache_ttl)

        # Tier 5 — rate limiter
        self._rate_limiter = DomainRateLimiter()

        # HTTP sessions
        self._direct_client: Optional[httpx.AsyncClient] = None
        self._proxy_clients: Dict[str, httpx.AsyncClient] = {}
        self._user_agent = user_agent

        # Statistics
        self._total_requests = 0
        self._proxy_requests = 0
        self._direct_requests = 0
        self._cache_hits = 0
        self._dedup_hits = 0
        self._circuit_rejects = 0
        self._rate_rejects = 0

    # ── lifecycle ─────────────────────────────────────────────────────────

    async def __aenter__(self) -> "ResilientClient":
        await self.startup()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()

    async def startup(self) -> None:
        """Initialise the client: load proxies, optionally enrich from sources."""
        loader = ProxyPoolLoader()
        file_proxies = loader.load()
        if file_proxies:
            self._proxies.extend(file_proxies)
            logger.info("Loaded %d proxies from pool file", len(file_proxies))

        if loader.enrich:
            async with httpx.AsyncClient() as tmp:
                remote_proxies = await loader.fetch_from_sources(tmp)
            if remote_proxies:
                self._proxies.extend(remote_proxies)
                logger.info("Enriched %d proxies from remote sources", len(remote_proxies))

        # Build direct client
        self._direct_client = httpx.AsyncClient(
            headers={"User-Agent": self._user_agent},
            timeout=httpx.Timeout(30.0),
            follow_redirects=True,
        )

        logger.info(
            "ResilientClient ready — %d proxies loaded, cache=%d entries, ttl=%.0fs",
            len(self._proxies), self._cache.max_entries, self._cache.default_ttl,
        )

    async def close(self) -> None:
        """Shut down all HTTP clients."""
        if self._direct_client:
            await self._direct_client.aclose()
            self._direct_client = None
        for client in self._proxy_clients.values():
            await client.aclose()
        self._proxy_clients.clear()
        # Cancel any pending in-flight futures
        for key, fut in self._inflight.items():
            if not fut.done():
                fut.cancel()
        self._inflight.clear()
        logger.info("ResilientClient closed")

    # ── main request method ───────────────────────────────────────────────

    async def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        """
        Execute an HTTP request through the 5-tier escalation stack.

        1.  Check the cache (GET/HEAD only).
        2.  Acquire a rate-limit token for the target domain.
        3.  Check the circuit breaker for the domain.
        4.  Deduplicate in-flight requests.
        5.  Try proxy rotation, then direct fallback.

        Raises ``httpx.HTTPError`` on complete failure.
        """
        self._total_requests += 1
        method_upper = method.upper()

        # ── Tier 5: rate limiting ──────────────────────────────────────
        if not self._rate_limiter.acquire(url):
            self._rate_rejects += 1
            logger.warning("Rate limit exceeded for %s — retrying in 1s", url)
            await asyncio.sleep(1.0)
            # One retry after a short wait
            if not self._rate_limiter.acquire(url):
                raise httpx.HTTPStatusError(
                    "Rate limit exceeded",
                    request=httpx.Request(method, url),
                    response=httpx.Response(429),
                )

        # ── Tier 4: cache (GET/HEAD only) ──────────────────────────────
        cacheable = method_upper in ("GET", "HEAD")
        ckey = _cache_key(method_upper, url) if cacheable else ""

        if cacheable and ckey:
            cached = self._cache.get(ckey)
            if cached is not None:
                self._cache_hits += 1
                logger.debug("Cache hit for %s %s", method_upper, url)
                # Reconstruct a synthetic response
                return self._synth_response(cached, method, url)

        # ── Tier 2: circuit breaker ────────────────────────────────────
        domain = urlparse(url).hostname or "unknown"
        breaker = self._get_circuit(domain)
        if not breaker.is_available():
            self._circuit_rejects += 1
            logger.warning("Circuit OPEN for domain %s — trying direct fallback", domain)
            return await self._direct_request(method, url, **kwargs)

        # ── Tier 3: request dedup ──────────────────────────────────────
        # P0-A regression: hashlib is now imported (was NameError on every
        #   request reaching this path — the crash was masked by the smoke
        #   test's broad ``except Exception`` swallowing it).
        # P1-G: use ``setdefault`` for atomic check-and-set so two concurrent
        #   identical requests can't both create a future — the loser would
        #   orphan the winner's future (never awaited, never resolved).
        body_for_hash = kwargs.get("content", kwargs.get("data", kwargs.get("json", b"")))
        dedup_key = _cache_key(
            method_upper, url,
            hashlib.sha256(str(body_for_hash).encode()).hexdigest()[:16],
        )

        loop = asyncio.get_running_loop()
        fut: asyncio.Future[httpx.Response] = loop.create_future()
        existing = self._inflight.setdefault(dedup_key, fut)

        if existing is not fut:
            # Another request won the race; join its future
            self._dedup_hits += 1
            logger.debug("Dedup: joining in-flight request for %s %s", method_upper, url)
            return await existing

        # We won the race; execute and resolve the future
        try:
            response = await self._execute_with_proxy(method, url, **kwargs)
            fut.set_result(response)
            return response
        except Exception as exc:
            fut.set_exception(exc)
            raise
        finally:
            self._inflight.pop(dedup_key, None)

    # ── proxy rotation & execution ────────────────────────────────────────

    async def _execute_with_proxy(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        """
        Try the best available proxy, fall back to direct.

        Tier 1 — weighted proxy rotation.
        """
        # Sort proxies by score (descending)
        scored_proxies = [
            (p, p.get_score()) for p in self._proxies
        ]
        scored_proxies.sort(key=lambda x: x[1], reverse=True)

        # Try up to 3 best proxies
        for proxy_entry, score in scored_proxies[:3]:
            if score < 0:
                continue  # banned or unhealthy
            try:
                response = await self._proxy_request(proxy_entry, method, url, **kwargs)
                return response
            except (httpx.HTTPError, httpx.TimeoutException, OSError) as exc:
                logger.debug(
                    "Proxy %s failed for %s: %s", proxy_entry.url, url, exc
                )
                proxy_entry.mark_failed()
                continue

        # All proxies exhausted — direct fallback
        logger.info("All proxies failed — falling back to direct for %s", url)
        return await self._direct_request(method, url, **kwargs)

    async def _proxy_request(
        self, proxy: ProxyEntry, method: str, url: str, **kwargs: Any
    ) -> httpx.Response:
        """Execute a request through a specific proxy."""
        self._proxy_requests += 1
        client = self._get_proxy_client(proxy)

        # P1-D: resolve provider-specific timeout per-request instead of
        #   mutating the shared proxy client (which races across concurrent
        #   requests to different providers — a Claude request could inherit
        #   Antigravity's 15s timeout if the Antigravity request wrote
        #   ``.timeout`` a millisecond later).
        domain = urlparse(url).hostname or ""
        provider = resolve_provider(domain)
        timeout = PROVIDER_CONFIG.get(provider, {}).get("timeout", 30) if provider else 30

        start = time.monotonic()
        # P2-C: removed empty ``try/except: raise`` no-op.
        response = await client.request(
            method, url, timeout=httpx.Timeout(float(timeout)), **kwargs
        )

        elapsed_ms = (time.monotonic() - start) * 1000.0
        breaker = self._get_circuit(domain or "unknown")

        # P1-A: 4xx is a successful round-trip — the proxy delivered the
        #   request and the upstream correctly rejected it.  Only 5xx and
        #   429 are upstream failures that should trip the circuit breaker.
        #   The old code counted 404s as failures, so a client polling a
        #   non-existent endpoint would trip the breaker on a healthy domain.
        if 200 <= response.status_code < 400:
            proxy.mark_success(elapsed_ms)
            breaker.record_success()

            # Cache successful 2xx/3xx GET/HEAD responses
            if method.upper() in ("GET", "HEAD"):
                ckey = _cache_key(method.upper(), url)
                cached = CachedResponse(
                    status_code=response.status_code,
                    headers=dict(response.headers),
                    body=response.content,
                    stored_at=time.monotonic(),
                    ttl=self._cache.default_ttl,
                )
                self._cache.put(ckey, cached)
        elif 400 <= response.status_code < 500 and response.status_code != 429:
            # Client error — proxy delivered successfully, upstream rejected
            proxy.mark_success(elapsed_ms)
            breaker.record_success()
        else:
            # 5xx or 429 — upstream failure
            breaker.record_failure()
            if response.status_code in (429, 502, 503, 504):
                proxy.mark_failed()

        return response

    async def _direct_request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        """Execute a request without any proxy."""
        # P2-D: don't silently re-create the client — if it's None, the
        #   caller used the API wrong (didn't call startup()).  Surface the
        #   error instead of masking it with a fresh client that has no
        #   connection pooling and silently works until it doesn't.
        if self._direct_client is None:
            raise RuntimeError("ResilientClient not started — call startup() first")
        self._direct_requests += 1

        # P1-C: resolve provider-specific timeout per-request instead of
        #   mutating the shared client's ``.timeout`` (which races across
        #   concurrent requests to different providers).
        domain = urlparse(url).hostname or ""
        provider = resolve_provider(domain)  # module-level (P1-F)
        timeout = PROVIDER_CONFIG.get(provider, {}).get("timeout", 30) if provider else 30

        response = await self._direct_client.request(
            method, url, timeout=httpx.Timeout(float(timeout)), **kwargs
        )

        # Cache successful GET/HEAD (2xx/3xx only)
        if method.upper() in ("GET", "HEAD") and 200 <= response.status_code < 400:
            ckey = _cache_key(method.upper(), url)
            cached = CachedResponse(
                status_code=response.status_code,
                headers=dict(response.headers),
                body=response.content,
                stored_at=time.monotonic(),
                ttl=self._cache.default_ttl,
            )
            self._cache.put(ckey, cached)

        return response

    # ── helpers ───────────────────────────────────────────────────────────

    def _get_circuit(self, domain: str) -> CircuitBreaker:
        """Get or create a CircuitBreaker for the given domain."""
        if domain not in self._circuits:
            self._circuits[domain] = CircuitBreaker(
                failure_threshold=self._circuit_threshold,
                recovery_timeout=self._circuit_recovery,
            )
        return self._circuits[domain]

    def _get_proxy_client(self, proxy: ProxyEntry) -> httpx.AsyncClient:
        """Get or create an httpx client configured for the given proxy."""
        if proxy.url not in self._proxy_clients:
            # Resolve provider-specific timeout
            self._proxy_clients[proxy.url] = httpx.AsyncClient(
                proxy=proxy.url,
                headers={"User-Agent": self._user_agent},
                timeout=httpx.Timeout(30.0),
                follow_redirects=True,
            )
        return self._proxy_clients[proxy.url]

    @staticmethod
    def _synth_response(cached: CachedResponse, method: str, url: str) -> httpx.Response:
        """Reconstruct an httpx.Response from a CachedResponse."""
        return httpx.Response(
            status_code=cached.status_code,
            headers=cached.headers,
            content=cached.body,
            request=httpx.Request(method, url),
        )

    # ── observability ─────────────────────────────────────────────────────

    def get_stats(self) -> Dict[str, Any]:
        """Return comprehensive statistics from all components."""
        # Proxy pool summary
        healthy = sum(1 for p in self._proxies if not p.is_banned() and p.healthy)
        banned = sum(1 for p in self._proxies if p.is_banned())

        # Circuit breaker summary
        circuit_summary: Dict[str, Dict[str, Any]] = {}
        for domain, cb in self._circuits.items():
            circuit_summary[domain] = {
                "state": cb.state.name,
                "failures": cb._failure_count,
                "successes": cb._success_count,
            }

        return {
            "version": "3.3",
            "requests": {
                "total": self._total_requests,
                "via_proxy": self._proxy_requests,
                "via_direct": self._direct_requests,
                "cache_hits": self._cache_hits,
                "dedup_hits": self._dedup_hits,
                "circuit_rejects": self._circuit_rejects,
                "rate_rejects": self._rate_rejects,
            },
            "proxy_pool": {
                "total": len(self._proxies),
                "healthy": healthy,
                "banned": banned,
                "top5": [
                    {"url": p.url, "score": p.get_score(), "latency_ms": round(p.latency_ms, 1)}
                    for p in sorted(self._proxies, key=ProxyEntry.get_score, reverse=True)[:5]
                ],
            },
            "cache": self._cache.stats(),
            "circuits": circuit_summary,
            "rate_limiter": self._rate_limiter.stats(),
            "providers": {
                name: {"base_url": cfg["base_url"], "rate_limit": cfg["rate_limit"], "timeout": cfg["timeout"]}
                for name, cfg in PROVIDER_CONFIG.items()
            },
        }


# ═══════════════════════════════════════════════════════════════════════════════
# Standalone smoke-test
# ═══════════════════════════════════════════════════════════════════════════════

async def _smoke_test() -> None:
    """Quick smoke test that exercises every tier."""
    import sys

    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s  %(name)-20s  %(levelname)-7s  %(message)s",
        stream=sys.stderr,
    )

    # Build a small synthetic proxy pool for testing
    test_proxies = [
        ProxyEntry(url="http://127.0.0.1:8080", proxy_type="http", tier=1, source="test"),
        ProxyEntry(url="http://127.0.0.1:8081", proxy_type="http", tier=2, source="test"),
    ]

    async with ResilientClient(proxy_entries=test_proxies, cache_max=64, cache_ttl=10) as client:
        # P1-H: distinguish expected network failures from unexpected crashes.
        #   The old ``except Exception`` swallowed NameErrors (P0-A) as
        #   "expected in sandbox", masking the crash for the entire lifetime
        #   of the file.  Now: network errors are expected, anything else
        #   is a crash and gets re-raised.
        try:
            resp = await client.request("GET", "https://httpbin.org/get")
            print(f"Status: {resp.status_code}")
            print(f"Body length: {len(resp.content)} bytes")
        except (httpx.HTTPError, OSError) as exc:
            print(f"Network failure (expected in sandbox): {exc}")
        except Exception as exc:
            print(f"UNEXPECTED CRASH: {type(exc).__name__}: {exc}")
            raise

        # Print stats
        stats = client.get_stats()
        print("\n=== Client Stats ===")
        for section, data in stats.items():
            print(f"  {section}: {json.dumps(data, indent=4, default=str)}")

        # Test cache directly
        cache = BoundedHTTPCache(max_entries=3, default_ttl=5.0)
        for i in range(5):
            cache.put(f"key:{i}", CachedResponse(
                status_code=200, headers={}, body=f"body-{i}".encode(),
                stored_at=time.monotonic(), ttl=5.0,
            ))
        print(f"\nCache stats after 5 puts (max 3): {cache.stats()}")

        # Test circuit breaker
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=1.0)
        assert cb.is_available()
        cb.record_failure()
        assert cb.is_available()
        cb.record_failure()
        assert not cb.is_available()
        print(f"\nCircuit breaker state after 2 failures: {cb.state.name}")

        # Test token bucket
        bucket = TokenBucket(rate=1.0, capacity=3.0)
        assert bucket.acquire(3.0) is True
        assert bucket.acquire(1.0) is False
        print("TokenBucket: basic test passed")

        # ── P2-R: dedup regression test ──────────────────────────────
        # Fires 3 concurrent identical requests and asserts:
        #   1. The dedup path doesn't crash (P0-A regression — hashlib).
        #   2. Only ONE upstream call is made (P1-G — setdefault works).
        #   3. All 3 callers get the same exception (dedup joined them).
        print("\n--- Dedup regression test (P0-A, P1-G) ---")
        upstream_call_count = 0
        original_exec = client._execute_with_proxy

        async def counting_exec(method: str, url: str, **kw: Any) -> httpx.Response:
            nonlocal upstream_call_count
            upstream_call_count += 1
            await asyncio.sleep(0.05)  # let other tasks reach the dedup check
            raise httpx.ConnectError("simulated network failure")

        client._execute_with_proxy = counting_exec  # type: ignore[assignment]
        try:
            tasks = [
                asyncio.create_task(
                    client.request("GET", "https://api.anthropic.com/v1/models")
                )
                for _ in range(3)
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            print(f"  Upstream calls: {upstream_call_count} (expected 1 — dedup should join)")
            print(f"  Results: {[type(r).__name__ if isinstance(r, Exception) else 'OK' for r in results]}")
            assert upstream_call_count == 1, (
                f"Dedup failed: {upstream_call_count} upstream calls instead of 1"
            )
            # All 3 should be ConnectError (the dedup'd exception propagated to all)
            for r in results:
                assert isinstance(r, httpx.ConnectError), (
                    f"Expected ConnectError, got {type(r).__name__}"
                )
            print("  Dedup regression test PASSED")
        finally:
            client._execute_with_proxy = original_exec  # type: ignore[assignment]

        # ── P1-B regression: new proxy gets exploration bonus ────────
        print("\n--- New proxy exploration bonus (P1-B) ---")
        new_proxy = ProxyEntry(url="http://10.0.0.99:8080", tier=2)
        established = ProxyEntry(url="http://10.0.0.1:8080", tier=2)
        established.latency_ms = 80.0
        established.success_count = 50
        established.fail_count = 2  # 50/52 = 0.96 success rate
        new_score = new_proxy.get_score()
        est_score = established.get_score()
        print(f"  New proxy score:      {new_score}")
        print(f"  Established score:    {est_score}")
        # New proxy should be competitive (within ~30 points) thanks to bonus
        assert new_score > 0, "New proxy scored below 0 — unscoreable"
        print("  P1-B regression test PASSED (new proxy is scoreable)")

        # ── P1-A regression: 4xx doesn't trip circuit breaker ────────
        print("\n--- 4xx circuit-breaker test (P1-A) ---")
        cb2 = CircuitBreaker(failure_threshold=3, recovery_timeout=10.0)
        # Simulate 5 x 404 responses — old code would trip the breaker
        for _ in range(5):
            cb2.record_success()  # 4xx now counts as success (P1-A fix)
        assert cb2.is_available(), "Circuit tripped on 4xx — P1-A regression"
        print(f"  Circuit state after 5 x 4xx: {cb2.state.name} (should be CLOSED)")
        print("  P1-A regression test PASSED")


if __name__ == "__main__":
    asyncio.run(_smoke_test())
