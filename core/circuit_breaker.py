"""Circuit breaker pattern for fault-tolerant external calls.

Prevents cascading failures by tracking error rates and temporarily
disabling calls to failing services. Implements three states:

  CLOSED  → calls pass through normally
  OPEN    → calls are immediately rejected (fail-fast)
  HALF_OPEN → one probe call allowed to test recovery

Based on Netflix Hystrix / resilience4j patterns.
"""

from __future__ import annotations

import logging
import threading
import time
from enum import StrEnum
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["CircuitBreaker", "CircuitOpenError", "CircuitState"]


class CircuitState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(RuntimeError):
    """Raised when a call is rejected because the circuit is open."""

    def __init__(self, name: str, until: float) -> None:
        remaining = max(0, until - time.monotonic())
        super().__init__(f"Circuit '{name}' is OPEN — retries blocked for {remaining:.0f}s")
        self.circuit_name = name
        self.retry_after = remaining


class CircuitBreaker:
    """Thread-safe circuit breaker with configurable thresholds.

    Args:
        name: Human-readable name for logging.
        failure_threshold: Consecutive failures before opening.
        recovery_timeout: Seconds to wait before half-open probe.
        success_threshold: Successes in half-open before closing.
    """

    def __init__(
        self,
        name: str,
        *,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
        success_threshold: int = 2,
    ) -> None:
        self.name = name
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._success_threshold = success_threshold

        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time: float = 0.0
        self._lock = threading.Lock()

    @property
    def state(self) -> CircuitState:
        with self._lock:
            if (
                self._state == CircuitState.OPEN
                and time.monotonic() - self._last_failure_time >= self._recovery_timeout
            ):
                self._state = CircuitState.HALF_OPEN
                self._success_count = 0
                logger.info("Circuit '%s' → HALF_OPEN (probing)", self.name)
            return self._state

    def allow_request(self) -> bool:
        """Check if a request should be allowed through."""
        state = self.state
        if state == CircuitState.CLOSED:
            return True
        return state == CircuitState.HALF_OPEN

    def record_success(self) -> None:
        """Record a successful call."""
        with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._success_count += 1
                if self._success_count >= self._success_threshold:
                    self._state = CircuitState.CLOSED
                    self._failure_count = 0
                    self._success_count = 0
                    logger.info("Circuit '%s' → CLOSED (recovered)", self.name)
            else:
                self._failure_count = 0

    def record_failure(self) -> None:
        """Record a failed call."""
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.monotonic()

            if self._state == CircuitState.HALF_OPEN:
                self._state = CircuitState.OPEN
                logger.warning("Circuit '%s' → OPEN (probe failed)", self.name)
            elif self._failure_count >= self._failure_threshold:
                self._state = CircuitState.OPEN
                logger.warning(
                    "Circuit '%s' → OPEN (%d consecutive failures)",
                    self.name,
                    self._failure_count,
                )

    def reset(self) -> None:
        """Manually reset the circuit to CLOSED."""
        with self._lock:
            self._state = CircuitState.CLOSED
            self._failure_count = 0
            self._success_count = 0
            logger.info("Circuit '%s' manually reset → CLOSED", self.name)

    def get_status(self) -> dict[str, Any]:
        """Return current circuit status for health checks."""
        return {
            "name": self.name,
            "state": self.state.value,
            "failure_count": self._failure_count,
            "failure_threshold": self._failure_threshold,
            "recovery_timeout_seconds": self._recovery_timeout,
        }
