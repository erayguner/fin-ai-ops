"""Tests for retry with exponential backoff."""

import contextlib
from unittest.mock import MagicMock

import pytest
from core.circuit_breaker import CircuitBreaker, CircuitOpenError
from core.retry import RetryExhaustedError, retry_with_backoff


class TestRetryWithBackoff:
    def test_succeeds_on_first_attempt(self):
        fn = MagicMock(return_value="ok")
        result = retry_with_backoff(fn, max_attempts=3, base_delay=0.01)
        assert result == "ok"
        fn.assert_called_once()

    def test_retries_on_failure_then_succeeds(self):
        fn = MagicMock(side_effect=[ValueError("fail"), "ok"])
        fn.__name__ = "test_fn"
        result = retry_with_backoff(
            fn, max_attempts=3, base_delay=0.01, retryable_exceptions=(ValueError,)
        )
        assert result == "ok"
        assert fn.call_count == 2

    def test_raises_retry_exhausted_after_max_attempts(self):
        fn = MagicMock(side_effect=ValueError("always fails"))
        fn.__name__ = "test_fn"
        with pytest.raises(RetryExhaustedError) as exc_info:
            retry_with_backoff(
                fn, max_attempts=3, base_delay=0.01, retryable_exceptions=(ValueError,)
            )
        assert exc_info.value.attempts == 3
        assert isinstance(exc_info.value.last_error, ValueError)
        assert fn.call_count == 3

    def test_non_retryable_exception_raises_immediately(self):
        fn = MagicMock(side_effect=TypeError("not retryable"))
        with pytest.raises(TypeError):
            retry_with_backoff(
                fn, max_attempts=3, base_delay=0.01, retryable_exceptions=(ValueError,)
            )
        fn.assert_called_once()

    def test_on_retry_callback_called(self):
        fn = MagicMock(side_effect=[ValueError("fail"), "ok"])
        fn.__name__ = "test_fn"
        callback = MagicMock()
        retry_with_backoff(
            fn,
            max_attempts=3,
            base_delay=0.01,
            retryable_exceptions=(ValueError,),
            on_retry=callback,
        )
        callback.assert_called_once()
        attempt, error = callback.call_args[0]
        assert attempt == 1
        assert isinstance(error, ValueError)

    def test_circuit_breaker_blocks_when_open(self):
        cb = CircuitBreaker("test", failure_threshold=1, recovery_timeout=60.0)
        cb.record_failure()  # Opens the circuit
        fn = MagicMock(return_value="ok")
        with pytest.raises(CircuitOpenError):
            retry_with_backoff(fn, max_attempts=3, base_delay=0.01, circuit_breaker=cb)
        fn.assert_not_called()

    def test_circuit_breaker_records_success(self):
        cb = CircuitBreaker("test", failure_threshold=5)
        fn = MagicMock(return_value="ok")
        retry_with_backoff(fn, max_attempts=1, circuit_breaker=cb)
        # After success, failure count should be 0
        assert cb._failure_count == 0

    def test_circuit_breaker_records_failure_on_retry(self):
        cb = CircuitBreaker("test", failure_threshold=10)
        fn = MagicMock(side_effect=ValueError("fail"))
        fn.__name__ = "test_fn"
        with contextlib.suppress(RetryExhaustedError):
            retry_with_backoff(
                fn,
                max_attempts=2,
                base_delay=0.01,
                retryable_exceptions=(ValueError,),
                circuit_breaker=cb,
            )
        assert cb._failure_count == 2

    def test_passes_args_and_kwargs(self):
        def fn(a, b, c=None):
            return f"{a}-{b}-{c}"

        result = retry_with_backoff(fn, "x", "y", max_attempts=1, c="z")
        assert result == "x-y-z"
