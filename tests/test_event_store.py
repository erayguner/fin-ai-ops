"""Tests for event store abstractions (in-memory and SQLite)."""

from __future__ import annotations

from core.event_store import InMemoryEventStore, SQLiteEventStore
from core.models import CloudProvider

from tests.helpers import make_event as _make_event


class TestInMemoryEventStore:
    def test_store_and_count(self):
        store = InMemoryEventStore()
        assert store.count() == 0
        store.store(_make_event())
        assert store.count() == 1

    def test_deduplication(self):
        store = InMemoryEventStore()
        event = _make_event()
        store.store(event)
        store.store(event)
        assert store.count() == 1

    def test_store_batch(self):
        store = InMemoryEventStore()
        events = [_make_event(resource_id=f"i-{i}") for i in range(5)]
        stored = store.store_batch(events)
        assert stored == 5
        assert store.count() == 5

    def test_query_by_provider(self):
        store = InMemoryEventStore()
        store.store(_make_event(provider=CloudProvider.AWS))
        store.store(_make_event(provider=CloudProvider.GCP, resource_id="gcp-1"))
        assert len(store.query(provider=CloudProvider.AWS)) == 1

    def test_query_by_resource_type(self):
        store = InMemoryEventStore()
        store.store(_make_event(resource_type="ec2:instance"))
        store.store(_make_event(resource_type="rds:db", resource_id="rds-1"))
        assert len(store.query(resource_type="rds:db")) == 1

    def test_query_by_creator(self):
        store = InMemoryEventStore()
        store.store(_make_event(creator_email="alice@co.com", resource_id="i-1"))
        store.store(_make_event(creator_email="bob@co.com", resource_id="i-2"))
        results = store.query(creator="alice@co.com")
        assert len(results) == 1

    def test_exists(self):
        store = InMemoryEventStore()
        event = _make_event()
        assert not store.exists(event.event_id)
        store.store(event)
        assert store.exists(event.event_id)

    def test_cost_summary(self):
        store = InMemoryEventStore()
        store.store(_make_event(estimated_monthly_cost_usd=100.0, resource_id="i-1"))
        store.store(_make_event(estimated_monthly_cost_usd=200.0, resource_id="i-2"))
        summary = store.get_cost_summary()
        assert summary["total_events"] == 2
        assert summary["total_cost"] == 300.0

    def test_query_limit(self):
        store = InMemoryEventStore()
        for i in range(10):
            store.store(_make_event(resource_id=f"i-{i}"))
        assert len(store.query(limit=3)) == 3


class TestSQLiteEventStore:
    def _make_store(self) -> SQLiteEventStore:
        return SQLiteEventStore(":memory:")

    def test_store_and_count(self):
        store = self._make_store()
        assert store.count() == 0
        store.store(_make_event())
        assert store.count() == 1

    def test_deduplication(self):
        store = self._make_store()
        event = _make_event()
        store.store(event)
        store.store(event)
        assert store.count() == 1

    def test_store_batch(self):
        store = self._make_store()
        events = [_make_event(resource_id=f"i-{i}") for i in range(5)]
        stored = store.store_batch(events)
        assert stored == 5

    def test_query_by_provider(self):
        store = self._make_store()
        store.store(_make_event(provider=CloudProvider.AWS))
        store.store(_make_event(provider=CloudProvider.GCP, resource_id="gcp-1"))
        assert len(store.query(provider=CloudProvider.AWS)) == 1

    def test_exists(self):
        store = self._make_store()
        event = _make_event()
        assert not store.exists(event.event_id)
        store.store(event)
        assert store.exists(event.event_id)

    def test_cost_summary(self):
        store = self._make_store()
        store.store(_make_event(estimated_monthly_cost_usd=100.0, resource_id="i-1"))
        store.store(_make_event(estimated_monthly_cost_usd=200.0, resource_id="i-2"))
        summary = store.get_cost_summary()
        assert summary["total_events"] == 2
        assert summary["total_cost"] == 300.0

    def test_query_roundtrip_preserves_data(self):
        store = self._make_store()
        original = _make_event(
            resource_name="web-server",
            tags={"team": "platform", "env": "prod"},
        )
        store.store(original)
        results = store.query()
        assert len(results) == 1
        restored = results[0]
        assert restored.event_id == original.event_id
        assert restored.resource_name == "web-server"
        assert restored.tags == {"team": "platform", "env": "prod"}
