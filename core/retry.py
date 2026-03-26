"""Retry with exponential backoff for transient failures.

Provides a decorator and a context manager for retrying operations
that fail with transient errors (network timeouts, rate limiting,
temporary unavailability).

Integrates with CircuitBreaker to respect open circuits.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any, TypeVar

from .circuit_breaker import CircuitBreaker, CircuitOpenError

logger = logging.getLogger(__name__)

__all__ = ["RetryExhaustedError", "retry_with_backoff"]

T = TypeVar("T")


class RetryExhaustedError(RuntimeError):
    """All retry attempts failed."""

    def __init__(self, attempts: int, last_error: Exception) -> None:
        super().__init__(f"All {attempts} retry attempts failed: {last_error}")
        self.attempts = attempts
        self.last_error = last_error


def retry_with_backoff(  # noqa: UP047
    fn: Callable[..., T],
    *args: Any,
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    backoff_factor: float = 2.0,
    retryable_exceptions: tuple[type[Exception], ...] = (Exception,),
    circuit_breaker: CircuitBreaker | None = None,
    on_retry: Callable[[int, Exception], None] | None = None,
    **kwargs: Any,
) -> T:
    """Execute a function with exponential backoff retry.

    Args:
        fn: The function to call.
        max_attempts: Maximum number of attempts (including first).
        base_delay: Initial delay in seconds.
        max_delay: Maximum delay cap in seconds.
        backoff_factor: Multiplier for each subsequent delay.
        retryable_exceptions: Exception types that trigger retry.
        circuit_breaker: Optional circuit breaker to respect.
        on_retry: Optional callback(attempt, error) on each retry.

    Returns:
        The function's return value on success.

    Raises:
        RetryExhaustedError: If all attempts fail.
        CircuitOpenError: If the circuit breaker is open.
    """
    last_error: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        # Check circuit breaker before attempting
        if circuit_breaker and not circuit_breaker.allow_request():
            raise CircuitOpenError(
                circuit_breaker.name,
                circuit_breaker._last_failure_time + circuit_breaker._recovery_timeout,
            )

        try:
            result = fn(*args, **kwargs)
            if circuit_breaker:
                circuit_breaker.record_success()
            return result
        except retryable_exceptions as e:
            last_error = e
            if circuit_breaker:
                circuit_breaker.record_failure()

            if attempt == max_attempts:
                break

            delay = min(base_delay * (backoff_factor ** (attempt - 1)), max_delay)
            logger.warning(
                "Attempt %d/%d failed for %s: %s — retrying in %.1fs",
                attempt,
                max_attempts,
                fn.__name__,
                e,
                delay,
            )
            if on_retry:
                on_retry(attempt, e)
            time.sleep(delay)

    raise RetryExhaustedError(max_attempts, last_error)  # type: ignore[arg-type]
