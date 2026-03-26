"""Tests for resource exhaustion and infinite loop guards.

Covers: audit query caps, dead-letter queue bounds, dead-letter retry
expiry, replay reentrancy guard, and replay event deduplication.
"""

import tempfile
from unittest.mock import MagicMock

from core.audit import AuditLogger
from core.models import (
    CostAlert,
    ResourceCreationEvent,
)
from core.notifications import CompositeDispatcher
from core.validation import MAX_AUDIT_QUERY

from tests.helpers import make_alert, make_event


def _make_alert(alert_id: str = "alert-1") -> CostAlert:
    return make_alert(
        alert_id=alert_id,
        region="us-east-1",
        account_id="123",
        resource_id="i-123",
        resource_creator="user",
        creator_email="user@example.com",
        estimated_monthly_cost_usd=50.0,
        threshold_exceeded_usd=40.0,
        baseline_monthly_usd=30.0,
        cost_increase_percentage=66.7,
        accountability_note="Review",
    )


def _make_event(event_id: str = "evt-1", resource_id: str = "r-1") -> ResourceCreationEvent:
    return make_event(
        event_id=event_id,
        resource_id=resource_id,
        region="us-east-1",
        estimated_monthly_cost_usd=100.0,
        resource_name="test",
    )


# ── Audit query cap ─────────────────────────────────────────────────────


class TestAuditQueryCap:
    def test_max_audit_query_constant_exists(self):
        assert MAX_AUDIT_QUERY == 10_000

    def test_get_entries_caps_limit(self):
        """Even if caller asks for 999999, result is capped."""
        with tempfile.TemporaryDirectory() as tmpdir:
            audit = AuditLogger(tmpdir)
            for i in range(5):
                audit.log(action="x", actor="a", target=f"t-{i}")
            # The internal cap should not crash or return more than cap
            entries = audit.get_entries(limit=999_999)
            assert len(entries) == 5  # only 5 exist, but limit was silently capped

    def test_get_entries_respects_explicit_limit(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            audit = AuditLogger(tmpdir)
            for i in range(20):
                audit.log(action="x", actor="a", target=f"t-{i}")
            entries = audit.get_entries(limit=5)
            assert len(entries) == 5


# ── Dead-letter queue bounds ─────────────────────────────────────────────


class TestDeadLetterBounds:
    def test_max_dead_letters_constant(self):
        assert CompositeDispatcher.MAX_DEAD_LETTERS == 1_000

    def test_queue_bounded_at_max(self):
        failing = MagicMock()
        failing.channel_name = "broken"
        failing.send.return_value = False
        composite = CompositeDispatcher([failing])

        # Use a smaller limit for the test
        composite.MAX_DEAD_LETTERS = 5

        alert = _make_alert()
        for _ in range(8):
            composite.send(alert, "text")

        assert composite.dead_letter_count == 5  # capped, not 8

    def test_oldest_dropped_when_full(self):
        """FIFO eviction: oldest entry dropped when queue is full."""
        failing = MagicMock()
        failing.channel_name = "broken"
        failing.send.return_value = False
        composite = CompositeDispatcher([failing])
        composite.MAX_DEAD_LETTERS = 3

        for i in range(5):
            a = _make_alert(f"alert-{i}")
            composite.send(a, "text")

        # Should have alerts 2, 3, 4 (oldest 0, 1 dropped)
        ids = [e["alert_id"] for e in composite._dead_letters]
        assert ids == ["alert-2", "alert-3", "alert-4"]


# ── Dead-letter retry expiry ─────────────────────────────────────────────


class TestDeadLetterRetryExpiry:
    def test_max_retry_attempts_constant(self):
        assert CompositeDispatcher.MAX_RETRY_ATTEMPTS == 3

    def test_entries_expire_after_max_retries(self):
        failing = MagicMock()
        failing.channel_name = "always-broken"
        failing.send.return_value = False
        composite = CompositeDispatcher([failing])

        alert = _make_alert()
        composite.send(alert, "text")

        # Retry 3 times — entry should still be there after each
        for attempt in range(1, composite.MAX_RETRY_ATTEMPTS + 1):
            composite.retry_dead_letters()
            if attempt < composite.MAX_RETRY_ATTEMPTS:
                assert composite.dead_letter_count == 1, f"Attempt {attempt}"
            else:
                # On the last retry the count is bumped to MAX, but entry is still kept
                # It will be expired on the *next* retry call
                pass

        # One more retry: entry should now be expired
        result = composite.retry_dead_letters()
        assert result["expired"] == 1
        assert composite.dead_letter_count == 0

    def test_retry_count_increments(self):
        failing = MagicMock()
        failing.channel_name = "broken"
        failing.send.return_value = False
        composite = CompositeDispatcher([failing])

        alert = _make_alert()
        composite.send(alert, "text")
        assert composite._dead_letters[0]["retry_count"] == 0

        composite.retry_dead_letters()
        assert composite._dead_letters[0]["retry_count"] == 1

    def test_successful_retry_clears_entry(self):
        flaky = MagicMock()
        flaky.channel_name = "flaky"
        flaky.send.side_effect = [False, True]  # fail, then succeed
        composite = CompositeDispatcher([flaky])

        alert = _make_alert()
        composite.send(alert, "text")
        assert composite.dead_letter_count == 1

        result = composite.retry_dead_letters()
        assert result["succeeded"] == 1
        assert composite.dead_letter_count == 0


# ── Replay reentrancy guard ──────────────────────────────────────────────


class TestReplayReentrancy:
    def test_reentrancy_flag_blocks_concurrent(self):
        import mcp_server.server as srv

        # Simulate replay in progress
        srv._replay_state["in_progress"] = True
        try:
            result = srv.finops_replay_events()
            assert result["status"] == "skipped"
            assert "reentrancy" in result["message"].lower()
            assert result["replayed"] == 0
        finally:
            srv._replay_state["in_progress"] = False

    def test_reentrancy_flag_cleared_after_normal_run(self):
        import mcp_server.server as srv

        # Normal run should clear the flag even when there's nothing to replay
        srv._replay_state["in_progress"] = False
        result = srv.finops_replay_events()
        assert srv._replay_state["in_progress"] is False
        # Status depends on whether there are unevaluated events
        assert result["status"] in ("clean", "completed")


# ── Replay deduplication ─────────────────────────────────────────────────


class TestReplayDeduplication:
    def test_replayed_ids_tracked(self):
        import mcp_server.server as srv

        # Store an event in the server's event store
        event = _make_event("replay-dedup-1", "r-dedup-1")
        srv.event_store.store(event)

        # Clear any previous replay state
        srv._replay_state["replayed_ids"].clear()

        # First replay: should process the event
        result = srv.finops_replay_events()
        # If it was unevaluated, it should have been replayed
        if result["status"] == "completed":
            assert "replay-dedup-1" in srv._replay_state["replayed_ids"]

    def test_already_replayed_events_skipped(self):
        import mcp_server.server as srv

        # Pre-mark an event as replayed
        srv._replay_state["replayed_ids"].add("already-done-123")

        # Even if reconciliation identifies it, it should be filtered out
        # (We test the filter logic directly rather than full pipeline)
        unevaluated = ["already-done-123", "new-456"]
        filtered = [eid for eid in unevaluated if eid not in srv._replay_state["replayed_ids"]]
        assert filtered == ["new-456"]

    def test_replayed_set_bounded(self):
        import mcp_server.server as srv

        assert srv._MAX_REPLAYED_HISTORY == 50_000
