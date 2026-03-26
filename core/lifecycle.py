"""Agent lifecycle state machine.

Provides a lightweight state machine mixin for tracking agent status.
Follows the EDA best practice of explicit state transitions with
guard conditions and event emission on every transition.

States:
    CREATED     → Agent instance exists but is not initialized
    INITIALIZED → Dependencies loaded, ready to activate
    ACTIVE      → Processing events/tasks normally
    DEGRADED    → Running with reduced capability (fallback mode)
    PAUSED      → Temporarily halted (manual or backpressure)
    ERROR       → Failed, awaiting recovery or restart
    TERMINATED  → Shut down gracefully or after unrecoverable error
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["AgentLifecycle", "AgentState"]


class AgentState(StrEnum):
    CREATED = "created"
    INITIALIZED = "initialized"
    ACTIVE = "active"
    DEGRADED = "degraded"
    PAUSED = "paused"
    ERROR = "error"
    TERMINATED = "terminated"


# Valid state transitions: {from_state: {to_states}}
_VALID_TRANSITIONS: dict[AgentState, set[AgentState]] = {
    AgentState.CREATED: {AgentState.INITIALIZED, AgentState.TERMINATED},
    AgentState.INITIALIZED: {AgentState.ACTIVE, AgentState.ERROR, AgentState.TERMINATED},
    AgentState.ACTIVE: {
        AgentState.DEGRADED,
        AgentState.PAUSED,
        AgentState.ERROR,
        AgentState.TERMINATED,
    },
    AgentState.DEGRADED: {
        AgentState.ACTIVE,
        AgentState.ERROR,
        AgentState.PAUSED,
        AgentState.TERMINATED,
    },
    AgentState.PAUSED: {AgentState.ACTIVE, AgentState.DEGRADED, AgentState.TERMINATED},
    AgentState.ERROR: {
        AgentState.ACTIVE,
        AgentState.DEGRADED,
        AgentState.INITIALIZED,
        AgentState.TERMINATED,
    },
    AgentState.TERMINATED: set(),  # Terminal state — no transitions out
}


class AgentLifecycle:
    """Lightweight state machine for agent lifecycle management.

    Usage:
        lifecycle = AgentLifecycle("cost_monitor")
        lifecycle.transition(AgentState.INITIALIZED)
        lifecycle.transition(AgentState.ACTIVE)
        # ... agent runs ...
        lifecycle.transition(AgentState.ERROR, reason="CloudTrail API timeout")
        lifecycle.transition(AgentState.ACTIVE)  # recovered
    """

    def __init__(self, agent_name: str) -> None:
        self.agent_name = agent_name
        self._state = AgentState.CREATED
        self._state_since = time.monotonic()
        self._created_at = datetime.now(UTC)
        self._transition_history: list[dict[str, Any]] = []
        self._error_count = 0

    @property
    def state(self) -> AgentState:
        return self._state

    @property
    def uptime_seconds(self) -> float:
        return time.monotonic() - self._state_since

    @property
    def is_operational(self) -> bool:
        """True if the agent can accept work."""
        return self._state in (AgentState.ACTIVE, AgentState.DEGRADED)

    def transition(self, new_state: AgentState, *, reason: str = "") -> bool:
        """Attempt a state transition. Returns True if valid.

        Invalid transitions are logged and rejected (not raised) to
        prevent state machine errors from crashing the agent.
        """
        old_state = self._state

        if new_state == old_state:
            return True  # No-op

        if new_state not in _VALID_TRANSITIONS.get(old_state, set()):
            logger.warning(
                "Agent '%s': invalid transition %s → %s (reason: %s)",
                self.agent_name,
                old_state.value,
                new_state.value,
                reason,
            )
            return False

        self._state = new_state
        self._state_since = time.monotonic()

        if new_state == AgentState.ERROR:
            self._error_count += 1

        record = {
            "from": old_state.value,
            "to": new_state.value,
            "timestamp": datetime.now(UTC).isoformat(),
            "reason": reason,
        }
        self._transition_history.append(record)

        # Keep history bounded
        if len(self._transition_history) > 200:
            self._transition_history = self._transition_history[-200:]

        logger.info(
            "Agent '%s': %s → %s%s",
            self.agent_name,
            old_state.value,
            new_state.value,
            f" ({reason})" if reason else "",
        )
        return True

    def get_status(self) -> dict[str, Any]:
        """Return current lifecycle status for health checks."""
        return {
            "agent": self.agent_name,
            "state": self._state.value,
            "is_operational": self.is_operational,
            "uptime_seconds": round(self.uptime_seconds, 1),
            "created_at": self._created_at.isoformat(),
            "error_count": self._error_count,
            "recent_transitions": self._transition_history[-5:],
        }

    def get_transition_history(self, limit: int = 20) -> list[dict[str, Any]]:
        return self._transition_history[-limit:]
