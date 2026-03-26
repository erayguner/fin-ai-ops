"""Tests for the Health Check Agent."""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

from agents.health_agent import ComponentHealth, HealthCheckAgent, HealthStatus
from core.circuit_breaker import CircuitBreaker
from core.event_store import InMemoryEventStore
from core.notifications import LogDispatcher


class TestComponentHealth:
    def test_to_dict(self):
        ch = ComponentHealth("test", HealthStatus.HEALTHY, message="all good", latency_ms=1.234)
        d = ch.to_dict()
        assert d["name"] == "test"
        assert d["status"] == "healthy"
        assert d["message"] == "all good"
        assert d["latency_ms"] == 1.23

    def test_defaults(self):
        ch = ComponentHealth("x", HealthStatus.DEGRADED)
        assert ch.message == ""
        assert ch.latency_ms == 0.0
        assert ch.details == {}


class TestHealthCheckAgent:
    def test_all_healthy_with_minimal_config(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            agent = HealthCheckAgent(
                event_store=InMemoryEventStore(),
                audit_dir=Path(tmpdir),
                policy_dir=Path(tmpdir),
            )
            report = agent.check_all()
            assert report["status"] in ("healthy", "degraded")
            assert report["summary"]["total"] >= 4  # event_store, audit, policy, disk

    def test_event_store_healthy(self):
        store = InMemoryEventStore()
        agent = HealthCheckAgent(event_store=store)
        report = agent.check_all()
        checks = {c["name"]: c for c in report["checks"]}
        assert checks["event_store"]["status"] == "healthy"
        assert checks["event_store"]["details"]["events_stored"] == 0

    def test_event_store_not_configured(self):
        agent = HealthCheckAgent()
        report = agent.check_all()
        checks = {c["name"]: c for c in report["checks"]}
        assert checks["event_store"]["status"] == "degraded"

    def test_event_store_unhealthy_on_error(self):
        store = MagicMock()
        store.count.side_effect = RuntimeError("DB locked")
        agent = HealthCheckAgent(event_store=store)
        report = agent.check_all()
        checks = {c["name"]: c for c in report["checks"]}
        assert checks["event_store"]["status"] == "unhealthy"

    def test_audit_trail_healthy(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            agent = HealthCheckAgent(audit_dir=Path(tmpdir))
            report = agent.check_all()
            checks = {c["name"]: c for c in report["checks"]}
            assert checks["audit_trail"]["status"] == "healthy"

    def test_audit_trail_missing_dir(self):
        agent = HealthCheckAgent(audit_dir=Path("/nonexistent/dir/abcxyz"))
        report = agent.check_all()
        checks = {c["name"]: c for c in report["checks"]}
        assert checks["audit_trail"]["status"] == "unhealthy"

    def test_policy_dir_healthy(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            agent = HealthCheckAgent(policy_dir=Path(tmpdir))
            report = agent.check_all()
            checks = {c["name"]: c for c in report["checks"]}
            assert checks["policy_dir"]["status"] == "healthy"

    def test_policy_dir_with_corrupt_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "bad.json").write_text("{invalid json")
            agent = HealthCheckAgent(policy_dir=Path(tmpdir))
            report = agent.check_all()
            checks = {c["name"]: c for c in report["checks"]}
            assert checks["policy_dir"]["status"] == "degraded"
            assert checks["policy_dir"]["details"]["corrupt"] == 1

    def test_dispatcher_check(self):
        dispatcher = LogDispatcher()
        agent = HealthCheckAgent(dispatchers=[dispatcher])
        report = agent.check_all()
        checks = {c["name"]: c for c in report["checks"]}
        assert checks["dispatcher:log"]["status"] == "healthy"

    def test_circuit_breaker_healthy(self):
        cb = CircuitBreaker("webhook", failure_threshold=5)
        agent = HealthCheckAgent(circuit_breakers={"webhook": cb})
        report = agent.check_all()
        checks = {c["name"]: c for c in report["checks"]}
        assert checks["circuit:webhook"]["status"] == "healthy"

    def test_circuit_breaker_open_is_unhealthy(self):
        cb = CircuitBreaker("webhook", failure_threshold=1, recovery_timeout=60.0)
        cb.record_failure()
        agent = HealthCheckAgent(circuit_breakers={"webhook": cb})
        report = agent.check_all()
        checks = {c["name"]: c for c in report["checks"]}
        assert checks["circuit:webhook"]["status"] == "unhealthy"

    def test_overall_unhealthy_if_any_unhealthy(self):
        store = MagicMock()
        store.count.side_effect = RuntimeError("broken")
        agent = HealthCheckAgent(event_store=store)
        report = agent.check_all()
        assert report["status"] == "unhealthy"

    def test_check_history(self):
        agent = HealthCheckAgent(event_store=InMemoryEventStore())
        agent.check_all()
        agent.check_all()
        history = agent.get_check_history()
        assert len(history) == 2

    def test_check_history_limit(self):
        agent = HealthCheckAgent(event_store=InMemoryEventStore())
        for _ in range(5):
            agent.check_all()
        assert len(agent.get_check_history(limit=3)) == 3

    def test_disk_space_check_runs(self):
        agent = HealthCheckAgent()
        report = agent.check_all()
        checks = {c["name"]: c for c in report["checks"]}
        assert "disk_space" in checks
        assert checks["disk_space"]["status"] in ("healthy", "degraded", "unhealthy")
