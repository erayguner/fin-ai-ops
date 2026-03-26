"""Health Check Agent — self-monitoring and component probes.

Provides liveness, readiness, and deep health probes for every hub
component. Emits structured health reports that enable automated
recovery decisions.

Probe types (modelled on Kubernetes):
  - liveness:  Is the process alive and responding?
  - readiness: Can it accept new work?
  - deep:      Are all dependencies healthy?
"""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from core.circuit_breaker import CircuitBreaker
from core.event_store import BaseEventStore
from core.notifications import BaseNotificationDispatcher

__all__ = ["ComponentHealth", "HealthCheckAgent", "HealthStatus"]


class HealthStatus:
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


class ComponentHealth:
    """Health result for a single component."""

    def __init__(
        self,
        name: str,
        status: str,
        *,
        message: str = "",
        latency_ms: float = 0.0,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.name = name
        self.status = status
        self.message = message
        self.latency_ms = latency_ms
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "message": self.message,
            "latency_ms": round(self.latency_ms, 2),
            "details": self.details,
        }


class HealthCheckAgent:
    """Runs health probes against all hub components.

    Usage:
        agent = HealthCheckAgent(event_store=store, audit_dir=Path("audit_store"), ...)
        report = agent.check_all()
        if report["status"] == "unhealthy":
            # trigger recovery
    """

    def __init__(
        self,
        *,
        event_store: BaseEventStore | None = None,
        audit_dir: Path | None = None,
        policy_dir: Path | None = None,
        dispatchers: list[BaseNotificationDispatcher] | None = None,
        circuit_breakers: dict[str, CircuitBreaker] | None = None,
    ) -> None:
        self._event_store = event_store
        self._audit_dir = audit_dir
        self._policy_dir = policy_dir
        self._dispatchers = dispatchers or []
        self._circuit_breakers = circuit_breakers or {}
        self._check_history: list[dict[str, Any]] = []

    def check_all(self) -> dict[str, Any]:
        """Run all health probes and return a combined report."""
        checks: list[ComponentHealth] = []

        checks.append(self._check_event_store())
        checks.append(self._check_audit_trail())
        checks.append(self._check_policy_dir())
        checks.append(self._check_disk_space())

        for dispatcher in self._dispatchers:
            checks.append(self._check_dispatcher(dispatcher))

        for name, cb in self._circuit_breakers.items():
            checks.append(self._check_circuit_breaker(name, cb))

        # Determine overall status
        statuses = [c.status for c in checks]
        if any(s == HealthStatus.UNHEALTHY for s in statuses):
            overall = HealthStatus.UNHEALTHY
        elif any(s == HealthStatus.DEGRADED for s in statuses):
            overall = HealthStatus.DEGRADED
        else:
            overall = HealthStatus.HEALTHY

        report = {
            "status": overall,
            "timestamp": datetime.now(UTC).isoformat(),
            "checks": [c.to_dict() for c in checks],
            "summary": {
                "total": len(checks),
                "healthy": sum(1 for s in statuses if s == HealthStatus.HEALTHY),
                "degraded": sum(1 for s in statuses if s == HealthStatus.DEGRADED),
                "unhealthy": sum(1 for s in statuses if s == HealthStatus.UNHEALTHY),
            },
        }

        self._check_history.append(report)
        # Keep only last 100 checks
        if len(self._check_history) > 100:
            self._check_history = self._check_history[-100:]

        return report

    def get_check_history(self, limit: int = 10) -> list[dict[str, Any]]:
        """Return recent health check results."""
        return self._check_history[-limit:]

    def _check_event_store(self) -> ComponentHealth:
        """Probe the event store: can we read count without error?"""
        if self._event_store is None:
            return ComponentHealth("event_store", HealthStatus.DEGRADED, message="Not configured")

        start = time.monotonic()
        try:
            count = self._event_store.count()
            latency = (time.monotonic() - start) * 1000
            return ComponentHealth(
                "event_store",
                HealthStatus.HEALTHY,
                latency_ms=latency,
                details={"events_stored": count},
            )
        except Exception as e:
            latency = (time.monotonic() - start) * 1000
            return ComponentHealth(
                "event_store",
                HealthStatus.UNHEALTHY,
                message=str(e),
                latency_ms=latency,
            )

    def _check_audit_trail(self) -> ComponentHealth:
        """Probe the audit trail: is the directory writable?"""
        if self._audit_dir is None:
            return ComponentHealth("audit_trail", HealthStatus.DEGRADED, message="Not configured")

        if not self._audit_dir.exists():
            return ComponentHealth(
                "audit_trail",
                HealthStatus.UNHEALTHY,
                message=f"Directory does not exist: {self._audit_dir}",
            )

        # Check writability
        test_file = self._audit_dir / ".health_check"
        try:
            test_file.write_text("ok")
            test_file.unlink()
            log_files = list(self._audit_dir.glob("audit-*.jsonl"))
            return ComponentHealth(
                "audit_trail",
                HealthStatus.HEALTHY,
                details={"log_files": len(log_files), "writable": True},
            )
        except OSError as e:
            return ComponentHealth(
                "audit_trail",
                HealthStatus.UNHEALTHY,
                message=f"Not writable: {e}",
            )

    def _check_policy_dir(self) -> ComponentHealth:
        """Probe the policy directory: does it exist with readable policies?"""
        if self._policy_dir is None:
            return ComponentHealth("policy_dir", HealthStatus.DEGRADED, message="Not configured")

        if not self._policy_dir.exists():
            return ComponentHealth(
                "policy_dir",
                HealthStatus.UNHEALTHY,
                message=f"Directory does not exist: {self._policy_dir}",
            )

        policy_files = list(self._policy_dir.glob("*.json"))
        corrupt = 0
        for pf in policy_files:
            try:
                import json

                json.loads(pf.read_text())
            except (json.JSONDecodeError, OSError):
                corrupt += 1

        if corrupt > 0:
            return ComponentHealth(
                "policy_dir",
                HealthStatus.DEGRADED,
                message=f"{corrupt} corrupt policy file(s)",
                details={"total": len(policy_files), "corrupt": corrupt},
            )

        return ComponentHealth(
            "policy_dir",
            HealthStatus.HEALTHY,
            details={"policy_files": len(policy_files)},
        )

    def _check_disk_space(self) -> ComponentHealth:
        """Check available disk space for audit and event storage."""
        try:
            stat = os.statvfs("/")
            available_gb = (stat.f_bavail * stat.f_frsize) / (1024**3)
            total_gb = (stat.f_blocks * stat.f_frsize) / (1024**3)
            used_pct = ((total_gb - available_gb) / total_gb) * 100 if total_gb > 0 else 0

            if available_gb < 0.5:
                status = HealthStatus.UNHEALTHY
                msg = f"Critical: only {available_gb:.2f}GB free"
            elif available_gb < 2.0:
                status = HealthStatus.DEGRADED
                msg = f"Low disk: {available_gb:.2f}GB free"
            else:
                status = HealthStatus.HEALTHY
                msg = ""

            return ComponentHealth(
                "disk_space",
                status,
                message=msg,
                details={
                    "available_gb": round(available_gb, 2),
                    "total_gb": round(total_gb, 2),
                    "used_pct": round(used_pct, 1),
                },
            )
        except OSError:
            return ComponentHealth("disk_space", HealthStatus.DEGRADED, message="Unable to check")

    def _check_dispatcher(self, dispatcher: BaseNotificationDispatcher) -> ComponentHealth:
        """Check if a notification dispatcher is properly configured."""
        name = f"dispatcher:{dispatcher.channel_name}"
        try:
            valid = dispatcher.validate_config()
            return ComponentHealth(
                name,
                HealthStatus.HEALTHY if valid else HealthStatus.DEGRADED,
                message="" if valid else "Configuration invalid",
            )
        except Exception as e:
            return ComponentHealth(name, HealthStatus.UNHEALTHY, message=str(e))

    def _check_circuit_breaker(self, name: str, cb: CircuitBreaker) -> ComponentHealth:
        """Report circuit breaker state."""
        status_map = {
            "closed": HealthStatus.HEALTHY,
            "half_open": HealthStatus.DEGRADED,
            "open": HealthStatus.UNHEALTHY,
        }
        cb_status = cb.get_status()
        return ComponentHealth(
            f"circuit:{name}",
            status_map.get(cb_status["state"], HealthStatus.DEGRADED),
            message=f"State: {cb_status['state']}",
            details=cb_status,
        )
