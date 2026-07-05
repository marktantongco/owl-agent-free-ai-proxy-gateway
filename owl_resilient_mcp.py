#!/usr/bin/env python3
"""
OWL-AGENT Resilient MCP Server v1.1
====================================
A Model Context Protocol server exposing resilient HTTP tools for AI agents.
Communicates via stdin/stdout using JSON-RPC 2.0.

Providers: Antigravity, Claude, OpenCode, Copilot, Kiro, Hermes

Defense stack: LRU Cache → Rate Limiter → Circuit Breaker → Response Validator
"""

from __future__ import annotations

import json
import logging
import sys
import threading
import time
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

try:
    import httpx
except ImportError:
    httpx = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PROVIDERS = ["antigravity", "claude", "opencode", "copilot", "kiro", "hermes"]

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "owl-resilient-http"
SERVER_VERSION = "7.1.0"

MAX_RESPONSE_SIZE = 10 * 1024 * 1024  # 10 MiB
DEFAULT_TTL = 300  # seconds
DEFAULT_CACHE_MAX = 512
RATE_LIMIT_CAPACITY = 40.0
RATE_LIMIT_REFILL = 2.0  # tokens per second
CIRCUIT_FAILURE_THRESHOLD = 3
CIRCUIT_RECOVERY_TIMEOUT = 30  # seconds

LOGGER = logging.getLogger("owl-resilient-mcp")
LOGGER.setLevel(logging.INFO)
_handler = logging.StreamHandler(sys.stderr)
_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
LOGGER.addHandler(_handler)

# ---------------------------------------------------------------------------
# BoundedCache — thread-safe LRU with TTL
# ---------------------------------------------------------------------------


class BoundedCache:
    """Thread-safe OrderedDict LRU cache with per-entry TTL."""

    def __init__(self, max_entries: int = DEFAULT_CACHE_MAX) -> None:
        self._max = max_entries
        self._lock = threading.Lock()
        self._store: OrderedDict[str, Tuple[float, Any]] = OrderedDict()
        self._hits = 0
        self._misses = 0

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                self._misses += 1
                return None
            expires_at, value = entry
            if time.monotonic() > expires_at:
                del self._store[key]
                self._misses += 1
                return None
            self._store.move_to_end(key)
            self._hits += 1
            return value

    def put(self, key: str, value: Any, ttl: float = DEFAULT_TTL) -> None:
        with self._lock:
            expires_at = time.monotonic() + ttl
            if key in self._store:
                self._store.move_to_end(key)
                self._store[key] = (expires_at, value)
            else:
                self._store[key] = (expires_at, value)
            while len(self._store) > self._max:
                self._store.popitem(last=False)

    def invalidate(self, key: str) -> bool:
        with self._lock:
            if key in self._store:
                del self._store[key]
                return True
            return False

    def clear(self) -> int:
        with self._lock:
            count = len(self._store)
            self._store.clear()
            self._hits = 0
            self._misses = 0
            return count

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            total = self._hits + self._misses
            return {
                "entries": len(self._store),
                "max_entries": self._max,
                "hits": self._hits,
                "misses": self._misses,
                "hit_rate": round(self._hits / total, 4) if total else 0.0,
            }


# ---------------------------------------------------------------------------
# TokenBucket — thread-safe rate limiter
# ---------------------------------------------------------------------------


class TokenBucket:
    """Thread-safe token-bucket rate limiter."""

    def __init__(
        self,
        capacity: float = RATE_LIMIT_CAPACITY,
        refill_rate: float = RATE_LIMIT_REFILL,
    ) -> None:
        self._capacity = capacity
        self._refill_rate = refill_rate
        self._tokens = capacity
        self._last_refill = time.monotonic()
        self._lock = threading.Lock()
        self._granted = 0
        self._denied = 0

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(self._capacity, self._tokens + elapsed * self._refill_rate)
        self._last_refill = now

    def acquire(self, tokens: float = 1.0) -> bool:
        with self._lock:
            self._refill()
            if self._tokens >= tokens:
                self._tokens -= tokens
                self._granted += 1
                return True
            self._denied += 1
            return False

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            self._refill()
            return {
                "capacity": self._capacity,
                "refill_rate": self._refill_rate,
                "tokens_available": round(self._tokens, 2),
                "granted": self._granted,
                "denied": self._denied,
            }


# ---------------------------------------------------------------------------
# DomainCircuitBreaker — per-domain circuit breaker
# ---------------------------------------------------------------------------


class DomainCircuitBreaker:
    """Per-domain circuit breaker with configurable thresholds."""

    def __init__(
        self,
        failure_threshold: int = CIRCUIT_FAILURE_THRESHOLD,
        recovery_timeout: float = CIRCUIT_RECOVERY_TIMEOUT,
    ) -> None:
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._lock = threading.Lock()
        self._domains: Dict[str, Dict[str, Any]] = {}

    def _ensure_domain(self, domain: str) -> Dict[str, Any]:
        if domain not in self._domains:
            self._domains[domain] = {
                "failures": 0,
                "state": "closed",       # closed | open | half_open
                "opened_at": 0.0,
                "successes": 0,
            }
        return self._domains[domain]

    def record_failure(self, domain: str) -> str:
        with self._lock:
            d = self._ensure_domain(domain)
            d["failures"] += 1
            if d["failures"] >= self._failure_threshold and d["state"] != "open":
                d["state"] = "open"
                d["opened_at"] = time.monotonic()
                return "opened"
            return d["state"]

    def record_success(self, domain: str) -> str:
        with self._lock:
            d = self._ensure_domain(domain)
            d["successes"] += 1
            d["failures"] = 0
            d["state"] = "closed"
            return d["state"]

    def is_available(self, domain: str) -> bool:
        with self._lock:
            d = self._ensure_domain(domain)
            if d["state"] == "closed":
                return True
            if d["state"] == "open":
                elapsed = time.monotonic() - d["opened_at"]
                if elapsed >= self._recovery_timeout:
                    d["state"] = "half_open"
                    return True
                return False
            # half_open — allow probe
            return True

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            result: Dict[str, Any] = {}
            for domain, d in self._domains.items():
                result[domain] = {
                    "state": d["state"],
                    "failures": d["failures"],
                    "successes": d["successes"],
                }
            return {"domains": result, "failure_threshold": self._failure_threshold}


# ---------------------------------------------------------------------------
# ResponseValidator — validates HTTP responses
# ---------------------------------------------------------------------------

# v7.1: OfflineQueue class deleted (was a no-op stub). The v7.0 queue stored
# failed requests but never retried them. v7.1 is honest: there is no queue.
# Retry semantics are deferred to v7.2 (see V72_SCOPE.md §2). The queue_status
# MCP tool still returns a response, but it's a static "disabled" payload.


class ResponseValidator:
    """Validates HTTP responses for status, content-type and size limits."""

    ALLOWED_CONTENT_PREFIXES = (
        "application/json",
        "text/",
        "application/xml",
        "application/x-www-form-urlencoded",
        "application/octet-stream",
    )

    def validate(self, response: httpx.Response) -> Tuple[bool, str]:
        # Status code — only reject server errors and rate-limit responses
        # Client errors (4xx) are legitimate responses, not infrastructure failures
        if response.status_code >= 500:
            return False, f"server error: {response.status_code}"
        if response.status_code == 429:
            return False, f"rate limited: {response.status_code}"
        if response.status_code in (502, 503, 504):
            return False, f"gateway error: {response.status_code}"

        # Content-Type
        ct = response.headers.get("content-type", "")
        if ct and not any(ct.startswith(prefix) for prefix in self.ALLOWED_CONTENT_PREFIXES):
            return False, f"disallowed content-type: {ct}"

        # Size
        content_length = response.headers.get("content-length")
        if content_length and int(content_length) > MAX_RESPONSE_SIZE:
            return False, f"response too large: {content_length} bytes"
        if len(response.content) > MAX_RESPONSE_SIZE:
            return False, f"response body exceeds limit: {len(response.content)} bytes"

        return True, "ok"


# ---------------------------------------------------------------------------
# MCPServer — JSON-RPC server over stdin/stdout
# ---------------------------------------------------------------------------


class MCPServer:
    """Model Context Protocol server handling JSON-RPC on stdin/stdout."""

    def __init__(self) -> None:
        self.cache = BoundedCache()
        self.rate_limiter = TokenBucket()
        self.circuit_breaker = DomainCircuitBreaker()
        self.validator = ResponseValidator()
        self._started_at = time.time()
        self._request_count = 0
        self._lock = threading.Lock()
        self._running = True

    # -- JSON-RPC plumbing --------------------------------------------------

    def _make_response(self, request_id: Any, result: Any) -> Dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    def _make_error(
        self, request_id: Any, code: int, message: str, data: Any = None
    ) -> Dict[str, Any]:
        err: Dict[str, Any] = {"code": code, "message": message}
        if data is not None:
            err["data"] = data
        return {"jsonrpc": "2.0", "id": request_id, "error": err}

    # -- Protocol handlers --------------------------------------------------

    def _handle_initialize(self, params: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        }

    def _handle_tools_list(self, params: Dict[str, Any]) -> List[Dict[str, Any]]:
        return [
            {
                "name": "fetch_resilient",
                "description": (
                    "Make an HTTP request with the full resilience stack: "
                    "LRU cache, rate limiter, circuit breaker, and response "
                    "validation. Supports providers: "
                    + ", ".join(PROVIDERS)
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "Target URL"},
                        "method": {
                            "type": "string",
                            "description": "HTTP method",
                            "default": "GET",
                            "enum": ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"],
                        },
                        "headers": {
                            "type": "object",
                            "description": "Request headers",
                            "additionalProperties": {"type": "string"},
                        },
                        "body": {
                            "type": "string",
                            "description": "Request body (raw string)",
                        },
                        "provider": {
                            "type": "string",
                            "description": "Routing provider hint",
                            "enum": PROVIDERS,
                        },
                        "ttl": {
                            "type": "number",
                            "description": "Cache TTL in seconds",
                            "default": DEFAULT_TTL,
                        },
                        "timeout": {
                            "type": "number",
                            "description": "Request timeout in seconds",
                            "default": 30,
                        },
                    },
                    "required": ["url"],
                },
            },
            {
                "name": "fetch_status",
                "description": "Get statistics from cache, circuit breaker, and rate limiter.",
                "inputSchema": {"type": "object", "properties": {}},
            },
            {
                "name": "fetch_clear_cache",
                "description": "Clear the HTTP response cache.",
                "inputSchema": {"type": "object", "properties": {}},
            },
            {
                "name": "health_check",
                "description": "Return server health status.",
                "inputSchema": {"type": "object", "properties": {}},
            },
            {
                "name": "queue_status",
                "description": "Return offline queue status and pending items.",
                "inputSchema": {"type": "object", "properties": {}},
            },
        ]

    def _handle_tools_call(self, params: Dict[str, Any]) -> Dict[str, Any]:
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})

        dispatch = {
            "fetch_resilient": self._tool_fetch_resilient,
            "fetch_status": self._tool_fetch_status,
            "fetch_clear_cache": self._tool_fetch_clear_cache,
            "health_check": self._tool_health_check,
            "queue_status": self._tool_queue_status,
        }

        handler = dispatch.get(tool_name)
        if handler is None:
            return {
                "isError": True,
                "content": [{"type": "text", "text": f"unknown tool: {tool_name}"}],
            }

        try:
            return handler(arguments)
        except Exception as exc:
            LOGGER.exception("tool %s failed", tool_name)
            return {
                "isError": True,
                "content": [{"type": "text", "text": f"tool error: {exc}"}],
            }

    # -- Tool implementations -----------------------------------------------

    def _extract_domain(self, url: str) -> str:
        try:
            parsed = urlparse(url)
            return parsed.hostname or url
        except Exception:
            return url

    def _tool_fetch_resilient(self, args: Dict[str, Any]) -> Dict[str, Any]:
        url = args.get("url", "")
        method = (args.get("method") or "GET").upper()
        headers = args.get("headers") or {}
        body = args.get("body")
        provider = args.get("provider", "")
        ttl = float(args.get("ttl", DEFAULT_TTL))
        timeout = float(args.get("timeout", 30))

        if not url:
            return {
                "isError": True,
                "content": [{"type": "text", "text": "url is required"}],
            }

        domain = self._extract_domain(url)

        # --- Cache lookup (GET / HEAD only) ---
        if method in ("GET", "HEAD"):
            cache_key = f"{method}:{url}:{json.dumps(headers, sort_keys=True)}"
            cached = self.cache.get(cache_key)
            if cached is not None:
                LOGGER.info("cache hit for %s", url)
                return {
                    "isError": False,
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(
                                {**cached, "cache": "hit", "provider": provider or "cache"},
                                indent=2,
                            ),
                        }
                    ],
                }

        # --- Rate limiter ---
        if not self.rate_limiter.acquire():
            return {
                "isError": True,
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps({
                            "error": "rate_limited",
                            "provider": provider or "unknown",
                            "queued_for_retry": False,  # v7.1: queue disabled (deferred to v7.2)
                        }),
                    }
                ],
            }

        # --- Circuit breaker ---
        if not self.circuit_breaker.is_available(domain):
            return {
                "isError": True,
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps({
                            "error": "circuit_open",
                            "domain": domain,
                            "provider": provider or "unknown",
                            "queued_for_retry": False,  # v7.1: queue disabled (deferred to v7.2)
                        }),
                    }
                ],
            }

        # --- HTTP request ---
        if httpx is None:
            return {
                "isError": True,
                "content": [{"type": "text", "text": "httpx is not installed"}],
            }

        with self._lock:
            self._request_count += 1

        try:
            with httpx.Client(timeout=timeout, follow_redirects=True, max_redirects=5) as client:
                response = client.request(method, url, headers=headers, content=body)

            # --- Validate response ---
            valid, reason = self.validator.validate(response)
            if not valid:
                self.circuit_breaker.record_failure(domain)
                return {
                    "isError": True,
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps({
                                "error": "validation_failed",
                                "reason": reason,
                                "status": response.status_code,
                                "domain": domain,
                                "provider": provider or "unknown",
                            }),
                        }
                    ],
                }

            self.circuit_breaker.record_success(domain)

            result = {
                "status": response.status_code,
                "headers": dict(response.headers),
                "body": response.text[:MAX_RESPONSE_SIZE],
                "url": str(response.url),
                "domain": domain,
                "provider": provider or "unknown",
                "cache": "miss",
            }

            # --- Cache store ---
            if method in ("GET", "HEAD") and response.status_code < 400:
                self.cache.put(cache_key, result, ttl=ttl)

            return {
                "isError": False,
                "content": [{"type": "text", "text": json.dumps(result, indent=2)}],
            }

        except httpx.TimeoutException:
            self.circuit_breaker.record_failure(domain)
            return {
                "isError": True,
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps({
                            "error": "timeout",
                            "domain": domain,
                            "provider": provider or "unknown",
                            "timeout": timeout,
                            "queued": False,  # v7.1: queue disabled (deferred to v7.2)
                        }),
                    }
                ],
            }

        except httpx.HTTPError as exc:
            self.circuit_breaker.record_failure(domain)
            return {
                "isError": True,
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps({
                            "error": "http_error",
                            "detail": str(exc),
                            "domain": domain,
                            "provider": provider or "unknown",
                            "queued": False,  # v7.1: queue disabled (deferred to v7.2)
                        }),
                    }
                ],
            }

    def _tool_fetch_status(self, args: Dict[str, Any]) -> Dict[str, Any]:
        status = {
            "cache": self.cache.stats(),
            "circuit_breaker": self.circuit_breaker.stats(),
            "rate_limiter": self.rate_limiter.stats(),
            "offline_queue": {"size": 0, "enabled": False},  # v7.1: queue disabled
            "providers": PROVIDERS,
        }
        return {
            "isError": False,
            "content": [{"type": "text", "text": json.dumps(status, indent=2)}],
        }

    def _tool_fetch_clear_cache(self, args: Dict[str, Any]) -> Dict[str, Any]:
        cleared = self.cache.clear()
        return {
            "isError": False,
            "content": [
                {"type": "text", "text": json.dumps({"cleared_entries": cleared})}
            ],
        }

    def _tool_health_check(self, args: Dict[str, Any]) -> Dict[str, Any]:
        uptime = time.time() - self._started_at
        with self._lock:
            req_count = self._request_count
        health = {
            "status": "healthy",
            "server": SERVER_NAME,
            "version": SERVER_VERSION,
            "protocol_version": PROTOCOL_VERSION,
            "uptime_seconds": round(uptime, 1),
            "total_requests": req_count,
            "providers": PROVIDERS,
            "cache_entries": self.cache.stats()["entries"],
            "circuit_breaker_domains": len(self.circuit_breaker.stats()["domains"]),
            "rate_limiter_tokens": self.rate_limiter.stats()["tokens_available"],
            "offline_queue_size": 0,  # v7.1: queue disabled (deferred to v7.2)
        }
        return {
            "isError": False,
            "content": [{"type": "text", "text": json.dumps(health, indent=2)}],
        }

    def _tool_queue_status(self, args: Dict[str, Any]) -> Dict[str, Any]:
        # v7.1: OfflineQueue deleted. Retry semantics deferred to v7.2.
        status = {
            "size": 0,
            "max_size": 0,
            "enabled": False,
            "pending_items": [],
            "note": "Queue disabled in v7.1. Retry semantics planned for v7.2 (see V72_SCOPE.md).",
        }
        return {
            "isError": False,
            "content": [{"type": "text", "text": json.dumps(status, indent=2)}],
        }

    # -- Main loop ----------------------------------------------------------

    def _process_message(self, message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        jsonrpc = message.get("jsonrpc")
        method = message.get("method")
        request_id = message.get("id")
        params = message.get("params") or {}

        if jsonrpc != "2.0":
            if request_id is not None:
                return self._make_error(request_id, -32600, "invalid jsonrpc version")
            return None

        # --- notifications (no id) — ignore ---
        if request_id is None and method in ("initialized", "cancelled"):
            return None

        # --- dispatch ---
        if method == "initialize":
            result = self._handle_initialize(params)
            return self._make_response(request_id, result)

        if method == "tools/list":
            result = self._handle_tools_list(params)
            return self._make_response(request_id, result)

        if method == "tools/call":
            result = self._handle_tools_call(params)
            return self._make_response(request_id, result)

        if method == "ping":
            return self._make_response(request_id, {})

        if request_id is not None:
            return self._make_error(request_id, -32601, f"method not found: {method}")
        return None

    def run(self) -> None:
        """Read JSON-RPC from stdin, process, write responses to stdout."""
        LOGGER.info(
            "%s v%s starting — protocol %s",
            SERVER_NAME,
            SERVER_VERSION,
            PROTOCOL_VERSION,
        )
        LOGGER.info("providers: %s", ", ".join(PROVIDERS))

        reader = sys.stdin
        writer = sys.stdout

        while self._running:
            try:
                line = reader.readline()
            except KeyboardInterrupt:
                break
            if not line:
                break

            line = line.strip()
            if not line:
                continue

            try:
                message = json.loads(line)
            except json.JSONDecodeError as exc:
                error_resp = self._make_error(None, -32700, f"parse error: {exc}")
                writer.write(json.dumps(error_resp) + "\n")
                writer.flush()
                continue

            response = self._process_message(message)
            if response is not None:
                writer.write(json.dumps(response) + "\n")
                writer.flush()

        LOGGER.info("%s shutting down", SERVER_NAME)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    server = MCPServer()
    server.run()


if __name__ == "__main__":
    main()
