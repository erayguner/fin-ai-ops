"""Tests for agent lifecycle state machine."""

from core.lifecycle import AgentLifecycle, AgentState


class TestAgentLifecycle:
    def test_starts_in_created_state(self):
        lc = AgentLifecycle("test")
        assert lc.state == AgentState.CREATED
        assert not lc.is_operational

    def test_valid_transition_created_to_initialized(self):
        lc = AgentLifecycle("test")
        assert lc.transition(AgentState.INITIALIZED) is True
        assert lc.state == AgentState.INITIALIZED

    def test_valid_transition_to_active(self):
        lc = AgentLifecycle("test")
        lc.transition(AgentState.INITIALIZED)
        lc.transition(AgentState.ACTIVE)
        assert lc.state == AgentState.ACTIVE
        assert lc.is_operational

    def test_invalid_transition_rejected(self):
        lc = AgentLifecycle("test")
        # Cannot go from CREATED directly to ACTIVE
        assert lc.transition(AgentState.ACTIVE) is False
        assert lc.state == AgentState.CREATED

    def test_noop_same_state(self):
        lc = AgentLifecycle("test")
        assert lc.transition(AgentState.CREATED) is True

    def test_degraded_is_operational(self):
        lc = AgentLifecycle("test")
        lc.transition(AgentState.INITIALIZED)
        lc.transition(AgentState.ACTIVE)
        lc.transition(AgentState.DEGRADED)
        assert lc.is_operational

    def test_error_increments_count(self):
        lc = AgentLifecycle("test")
        lc.transition(AgentState.INITIALIZED)
        lc.transition(AgentState.ACTIVE)
        lc.transition(AgentState.ERROR, reason="timeout")
        assert lc._error_count == 1
        lc.transition(AgentState.ACTIVE)
        lc.transition(AgentState.ERROR, reason="rate limit")
        assert lc._error_count == 2

    def test_terminated_is_terminal(self):
        lc = AgentLifecycle("test")
        lc.transition(AgentState.INITIALIZED)
        lc.transition(AgentState.TERMINATED)
        assert lc.state == AgentState.TERMINATED
        assert not lc.is_operational
        # Cannot transition out of terminated
        assert lc.transition(AgentState.ACTIVE) is False

    def test_recovery_from_error(self):
        lc = AgentLifecycle("test")
        lc.transition(AgentState.INITIALIZED)
        lc.transition(AgentState.ACTIVE)
        lc.transition(AgentState.ERROR)
        lc.transition(AgentState.ACTIVE)
        assert lc.state == AgentState.ACTIVE

    def test_transition_history_recorded(self):
        lc = AgentLifecycle("test")
        lc.transition(AgentState.INITIALIZED)
        lc.transition(AgentState.ACTIVE, reason="startup complete")
        history = lc.get_transition_history()
        assert len(history) == 2
        assert history[0]["from"] == "created"
        assert history[0]["to"] == "initialized"
        assert history[1]["reason"] == "startup complete"

    def test_get_status(self):
        lc = AgentLifecycle("my_agent")
        lc.transition(AgentState.INITIALIZED)
        lc.transition(AgentState.ACTIVE)
        status = lc.get_status()
        assert status["agent"] == "my_agent"
        assert status["state"] == "active"
        assert status["is_operational"] is True
        assert status["error_count"] == 0
        assert "created_at" in status

    def test_pause_and_resume(self):
        lc = AgentLifecycle("test")
        lc.transition(AgentState.INITIALIZED)
        lc.transition(AgentState.ACTIVE)
        lc.transition(AgentState.PAUSED)
        assert not lc.is_operational
        lc.transition(AgentState.ACTIVE)
        assert lc.is_operational

    def test_history_bounded(self):
        lc = AgentLifecycle("test")
        lc.transition(AgentState.INITIALIZED)
        lc.transition(AgentState.ACTIVE)
        # Generate many transitions
        for _ in range(250):
            lc.transition(AgentState.DEGRADED)
            lc.transition(AgentState.ACTIVE)
        assert len(lc._transition_history) <= 200
