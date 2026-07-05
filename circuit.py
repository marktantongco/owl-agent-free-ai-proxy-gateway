"""
🦉 OWL-AGENT Circuit Breaker
============================

Per-domain circuit breaker extracted from ``proxy_defense_fixed_v3.py``
as step 1 of the v7.2 monolith split (Task ID 6).

States
------
* **CLOSED**    — requests flow normally; failures are counted.
* **OPEN**      — requests are rejected immediately; after ``recovery_timeout``
  the breaker transitions to HALF_OPEN.
* **HALF_OPEN** — a single probe request is allowed; if it succeeds the
  breaker goes CLOSED, otherwise it re-opens.

Configuration
-------------
Defaults can be overridden at construction time, or globally via env vars:

    OWL_CIRCUIT_THRESHOLD  Failures before circuit opens   (default: 3)
    OWL_CIRCUIT_RECOVERY   Recovery timeout in seconds     (default: 30)

Usage
-----
    breaker = CircuitBreaker(failure_threshold=5, recovery_timeout=60)
    if breaker.is_available():
        try:
            response = await client.request(...)
            breaker.record_success()
        except Exception:
            breaker.record_failure()
    else:
        # Circuit is OPEN — fail fast or fall back
        ...

Why a separate module?
----------------------
``proxy_defense_fixed_v3.py`` was 1244 lines with 10 classes tangled together.
Extracting ``CircuitBreaker`` into its own module:

* makes the breaker independently testable
* lets operators import it without pulling in httpx, the proxy pool, the cache, etc.
* gives it its own logger namespace (``owl.circuit``) so circuit events can be
  filtered separately from proxy/defense logs
* removes the circular dependency between ``CircuitState`` (which was duplicated
  in ``owl_resilient_mcp.py``) and the rest of the defense stack
"""

from __future__ import annotations

import logging
import os
import time
from enum import Enum, auto
from typing import Final

# Module-level logger — log lines prefixed with "owl.circuit" so they can be
# filtered separately from "owl.proxy_defense" log output.
logger = logging.getLogger("owl.circuit")

# Defaults read from the same env vars that proxy_defense_fixed_v3.py reads,
# so this module is self-contained. proxy_defense_fixed_v3.py passes explicit
# values when constructing CircuitBreaker, so these defaults are only used if
# CircuitBreaker() is instantiated with no args.
_DEFAULT_FAILURE_THRESHOLD: Final[int] = int(os.getenv("OWL_CIRCUIT_THRESHOLD", "3"))
_DEFAULT_RECOVERY_TIMEOUT: Final[float] = float(os.getenv("OWL_CIRCUIT_RECOVERY", "30"))


class CircuitState(Enum):
    """Possible states of a circuit breaker."""
    CLOSED = auto()
    OPEN = auto()
    HALF_OPEN = auto()


class CircuitBreaker:
    """
    Per-domain circuit breaker.

    * **CLOSED**  — requests flow normally; failures are counted.
    * **OPEN**    — requests are rejected immediately; after
      ``recovery_timeout`` the breaker transitions to HALF_OPEN.
    * **HALF_OPEN** — a single probe request is allowed; if it succeeds the
      breaker goes CLOSED, otherwise it re-opens.
    """

    def __init__(
        self,
        failure_threshold: int = _DEFAULT_FAILURE_THRESHOLD,
        recovery_timeout: float = _DEFAULT_RECOVERY_TIMEOUT,
    ):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._last_failure_time = 0.0
        self._success_count = 0

    @property
    def state(self) -> CircuitState:
        """Current state, automatically transitioning OPEN → HALF_OPEN on timeout."""
        if self._state == CircuitState.OPEN:
            if (time.monotonic() - self._last_failure_time) >= self.recovery_timeout:
                self._state = CircuitState.HALF_OPEN
        return self._state

    def record_failure(self) -> CircuitState:
        """Record a failure.  Returns the new state."""
        self._failure_count += 1
        self._last_failure_time = time.monotonic()

        if self._state == CircuitState.HALF_OPEN:
            # Probe failed — re-open immediately
            self._state = CircuitState.OPEN
            logger.info("Circuit breaker probe failed — re-opened")
        elif self._failure_count >= self.failure_threshold:
            self._state = CircuitState.OPEN
            logger.info(
                "Circuit breaker opened after %d failures", self._failure_count
            )

        return self._state

    def record_success(self) -> CircuitState:
        """Record a success.  Returns the new state."""
        self._success_count += 1
        self._failure_count = 0
        if self._state == CircuitState.HALF_OPEN:
            self._state = CircuitState.CLOSED
            logger.info("Circuit breaker probe succeeded — closed")
        return self._state

    def is_available(self) -> bool:
        """True if the circuit allows a request (CLOSED or HALF_OPEN)."""
        return self.state != CircuitState.OPEN


__all__ = ["CircuitState", "CircuitBreaker"]
