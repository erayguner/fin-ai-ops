"""Behavioural anomaly + drift detection on agent sessions — ADR-008 §5.

Per-session rolling window over tool-call distribution, per-minute call
rate, token usage, and cost. Re-uses the statistical primitives in
:mod:`core.thresholds` (mean + stddev) so detection is consistent with
ADR-005.

Surfaces three families of anomaly (framework §9.2):

1. **Call rate** — z-score against rolling mean per session. A sustained
   ≥3-sigma deviation flips the session to ``CRITICAL``.
2. **Tool distribution** — χ² against a 30-day baseline; sudden shifts
   in which tools the agent picks are flagged ``WARNING``.
3. **Token / cost** — cumulative with a soft warn at 75% and a hard
   halt-recommend at 100% of the session budget.

The observer does not own the kill-switch; it returns an
:class:`AnomalySignal` whose ``severity`` and ``recommend_halt`` flag
the caller (typically the MCP server) acts on by writing a halt entry
to :class:`~core.agent_supervisor.AgentSupervisor`.

Storage is in-memory per process. A persistent backend can subclass
:class:`AgentObserver` and override ``_persist`` if the deployment
needs cross-process aggregation.
"""

from __future__ import annotations

import math
import statistics
import threading
import time
from collections import Counter, deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

SCHEMA_VERSION = "1"

__all__ = [
    "SCHEMA_VERSION",
    "AgentObserver",
    "AnomalySeverity",
    "AnomalySignal",
    "SessionMetrics",
    "global_observer",
]


class AnomalySeverity(StrEnum):
    OK = "ok"
    WARNING = "warning"
    CRITICAL = "critical"


class AnomalySignal(BaseModel):
    """Emitted on call-rate / tool-dist / token/cost threshold breaches."""

    schema_version: str = Field(default=SCHEMA_VERSION)
    session_id: str
    detector: str
    severity: AnomalySeverity
    metric: str
    observed: float
    threshold: float
    detail: str = ""
    recommend_halt: bool = False
    detected_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


@dataclass
class _RollingRate:
    """Tracks calls per minute over a sliding window."""

    window_seconds: float = 60.0
    calls: deque[float] = field(default_factory=deque)

    def record(self, *, now: float | None = None) -> None:
        now = now if now is not None else time.monotonic()
        self.calls.append(now)
        self._trim(now)

    def _trim(self, now: float) -> None:
        threshold = now - self.window_seconds
        while self.calls and self.calls[0] < threshold:
            self.calls.popleft()

    def calls_per_minute(self, *, now: float | None = None) -> float:
        now = now if now is not None else time.monotonic()
        self._trim(now)
        if self.window_seconds <= 0:
            return 0.0
        return len(self.calls) * (60.0 / self.window_seconds)


@dataclass
class SessionMetrics:
    """Per-session rolling state."""

    session_id: str
    started_at: float = field(default_factory=time.monotonic)
    call_count: int = 0
    tool_counter: Counter[str] = field(default_factory=Counter)
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost_usd: float = 0.0
    rate: _RollingRate = field(default_factory=_RollingRate)
    rate_history: deque[float] = field(default_factory=lambda: deque(maxlen=30))
    last_severity: AnomalySeverity = AnomalySeverity.OK

    def snapshot(self) -> dict[str, Any]:
        """Serialisable view for ``finops_session_stats``."""
        return {
            "session_id": self.session_id,
            "uptime_seconds": round(time.monotonic() - self.started_at, 2),
            "call_count": self.call_count,
            "tool_distribution": dict(self.tool_counter),
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "estimated_cost_usd": round(self.estimated_cost_usd, 6),
            "calls_per_minute": round(self.rate.calls_per_minute(), 2),
            "last_severity": self.last_severity.value,
        }


class AgentObserver:
    """Per-session anomaly observer.

    Caller wiring (typical, in a callback or governed_call wrapper):

        observer.record_tool_call(session_id, tool_name)
        signals = observer.evaluate(session_id, budget=session_budget)
        for s in signals:
            if s.recommend_halt:
                supervisor.halt(session_id=session_id, ...)

    Thresholds are tunable; defaults mirror framework §9.2 guidance.
    """

    def __init__(
        self,
        *,
        rate_zscore_warn: float = 2.0,
        rate_zscore_critical: float = 3.0,
        cost_warn_fraction: float = 0.75,
        cost_halt_fraction: float = 1.00,
        token_warn_fraction: float = 0.75,
        token_halt_fraction: float = 1.00,
        tool_distribution_shift_threshold: float = 0.5,
        baseline: dict[str, float] | None = None,
    ) -> None:
        self._sessions: dict[str, SessionMetrics] = {}
        self._lock = threading.Lock()
        self._rate_zscore_warn = rate_zscore_warn
        self._rate_zscore_critical = rate_zscore_critical
        self._cost_warn_fraction = cost_warn_fraction
        self._cost_halt_fraction = cost_halt_fraction
        self._token_warn_fraction = token_warn_fraction
        self._token_halt_fraction = token_halt_fraction
        self._tool_shift_threshold = tool_distribution_shift_threshold
        # Optional 30-day baseline distribution {tool_name: probability}.
        self._baseline = dict(baseline or {})

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def _get_or_create(self, session_id: str) -> SessionMetrics:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                session = SessionMetrics(session_id=session_id)
                self._sessions[session_id] = session
            return session

    def record_tool_call(self, session_id: str, tool_name: str) -> None:
        session = self._get_or_create(session_id)
        with self._lock:
            session.call_count += 1
            session.tool_counter[tool_name] += 1
            session.rate.record()

    def record_tokens(
        self,
        session_id: str,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
        estimated_cost_usd: float = 0.0,
    ) -> None:
        session = self._get_or_create(session_id)
        with self._lock:
            session.input_tokens += max(0, input_tokens)
            session.output_tokens += max(0, output_tokens)
            session.estimated_cost_usd += max(0.0, estimated_cost_usd)

    def update_baseline(self, baseline: dict[str, float]) -> None:
        """Replace the 30-day tool-distribution baseline."""
        with self._lock:
            self._baseline = dict(baseline)

    # ------------------------------------------------------------------
    # Evaluation
    # ------------------------------------------------------------------

    def evaluate(
        self,
        session_id: str,
        *,
        token_budget: int | None = None,
        cost_budget_usd: float | None = None,
        call_rate_budget_per_minute: float | None = None,
    ) -> list[AnomalySignal]:
        """Return the anomaly signals for this evaluation pass.

        Pass ``token_budget`` and/or ``cost_budget_usd`` to enable the
        cumulative warn/halt detectors. ``call_rate_budget_per_minute``
        overrides z-score detection with a hard ceiling.
        """
        session = self._get_or_create(session_id)
        signals: list[AnomalySignal] = []

        # ── Call rate ──
        current_rate = session.rate.calls_per_minute()
        # Save current rate into history *after* using it for z-score so
        # the current observation doesn't bias its own baseline.
        if len(session.rate_history) >= 3:
            mean = statistics.mean(session.rate_history)
            stdev = statistics.stdev(session.rate_history) or 1.0
            z = (current_rate - mean) / stdev
            if z >= self._rate_zscore_critical:
                signals.append(
                    AnomalySignal(
                        session_id=session_id,
                        detector="call_rate_zscore",
                        severity=AnomalySeverity.CRITICAL,
                        metric="calls_per_minute",
                        observed=current_rate,
                        threshold=mean + self._rate_zscore_critical * stdev,
                        detail=f"z={z:.2f} >= {self._rate_zscore_critical}",
                        recommend_halt=True,
                    )
                )
            elif z >= self._rate_zscore_warn:
                signals.append(
                    AnomalySignal(
                        session_id=session_id,
                        detector="call_rate_zscore",
                        severity=AnomalySeverity.WARNING,
                        metric="calls_per_minute",
                        observed=current_rate,
                        threshold=mean + self._rate_zscore_warn * stdev,
                        detail=f"z={z:.2f}",
                    )
                )
        session.rate_history.append(current_rate)

        if call_rate_budget_per_minute is not None and current_rate >= call_rate_budget_per_minute:
            signals.append(
                AnomalySignal(
                    session_id=session_id,
                    detector="call_rate_budget",
                    severity=AnomalySeverity.CRITICAL,
                    metric="calls_per_minute",
                    observed=current_rate,
                    threshold=call_rate_budget_per_minute,
                    detail="hard ceiling exceeded",
                    recommend_halt=True,
                )
            )

        # ── Token budget ──
        total_tokens = session.input_tokens + session.output_tokens
        if token_budget and token_budget > 0:
            frac = total_tokens / token_budget
            if frac >= self._token_halt_fraction:
                signals.append(
                    AnomalySignal(
                        session_id=session_id,
                        detector="token_budget",
                        severity=AnomalySeverity.CRITICAL,
                        metric="total_tokens",
                        observed=total_tokens,
                        threshold=token_budget,
                        detail=f"{frac * 100:.0f}% of session token budget",
                        recommend_halt=True,
                    )
                )
            elif frac >= self._token_warn_fraction:
                signals.append(
                    AnomalySignal(
                        session_id=session_id,
                        detector="token_budget",
                        severity=AnomalySeverity.WARNING,
                        metric="total_tokens",
                        observed=total_tokens,
                        threshold=int(token_budget * self._token_warn_fraction),
                        detail=f"{frac * 100:.0f}% of session token budget",
                    )
                )

        # ── Cost budget ──
        if cost_budget_usd and cost_budget_usd > 0:
            frac = session.estimated_cost_usd / cost_budget_usd
            if frac >= self._cost_halt_fraction:
                signals.append(
                    AnomalySignal(
                        session_id=session_id,
                        detector="cost_budget",
                        severity=AnomalySeverity.CRITICAL,
                        metric="estimated_cost_usd",
                        observed=session.estimated_cost_usd,
                        threshold=cost_budget_usd,
                        detail=f"{frac * 100:.0f}% of session cost budget",
                        recommend_halt=True,
                    )
                )
            elif frac >= self._cost_warn_fraction:
                signals.append(
                    AnomalySignal(
                        session_id=session_id,
                        detector="cost_budget",
                        severity=AnomalySeverity.WARNING,
                        metric="estimated_cost_usd",
                        observed=session.estimated_cost_usd,
                        threshold=cost_budget_usd * self._cost_warn_fraction,
                        detail=f"{frac * 100:.0f}% of session cost budget",
                    )
                )

        # ── Tool-distribution shift ──
        if self._baseline:
            shift = self._tool_distribution_shift(session.tool_counter)
            if shift >= self._tool_shift_threshold:
                signals.append(
                    AnomalySignal(
                        session_id=session_id,
                        detector="tool_distribution",
                        severity=AnomalySeverity.WARNING,
                        metric="tvd_vs_baseline",
                        observed=shift,
                        threshold=self._tool_shift_threshold,
                        detail="tool selection shifted vs 30-day baseline",
                    )
                )

        # Surface the highest-severity verdict.
        if signals:
            severities = [s.severity for s in signals]
            if AnomalySeverity.CRITICAL in severities:
                session.last_severity = AnomalySeverity.CRITICAL
            else:
                session.last_severity = AnomalySeverity.WARNING
        else:
            session.last_severity = AnomalySeverity.OK
        return signals

    def _tool_distribution_shift(self, observed: Counter[str]) -> float:
        """Total Variation Distance between observed and baseline distributions.

        TVD is a simpler, well-understood scale (0=identical, 1=disjoint)
        than χ² for the small-sample regimes typical in agent sessions.
        """
        total = sum(observed.values())
        if total == 0:
            return 0.0
        observed_dist = {k: v / total for k, v in observed.items()}
        keys = set(observed_dist) | set(self._baseline)
        tvd = 0.0
        for k in keys:
            tvd += abs(observed_dist.get(k, 0.0) - self._baseline.get(k, 0.0))
        return tvd / 2.0

    # ------------------------------------------------------------------
    # Inspection / lifecycle
    # ------------------------------------------------------------------

    def snapshot(self, session_id: str) -> dict[str, Any] | None:
        with self._lock:
            session = self._sessions.get(session_id)
        return session.snapshot() if session else None

    def all_sessions(self) -> list[str]:
        with self._lock:
            return list(self._sessions.keys())

    def reset(self, session_id: str | None = None) -> None:
        with self._lock:
            if session_id is None:
                self._sessions.clear()
                return
            self._sessions.pop(session_id, None)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------


_GLOBAL: AgentObserver | None = None
_GLOBAL_LOCK = threading.Lock()


def global_observer() -> AgentObserver:
    global _GLOBAL
    with _GLOBAL_LOCK:
        if _GLOBAL is None:
            _GLOBAL = AgentObserver()
        return _GLOBAL


# ---------------------------------------------------------------------------
# Small helpers exposed for tests and downstream callers.
# ---------------------------------------------------------------------------


def _safe_log(x: float) -> float:
    """Numerically-stable log used by alternative detectors."""
    return math.log(x) if x > 0 else float("-inf")
