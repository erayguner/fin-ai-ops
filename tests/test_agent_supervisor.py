"""Unit tests for core.agent_supervisor — kill-switch backend."""

from __future__ import annotations

from core.agent_supervisor import AgentSupervisor


def test_halt_marks_session_halted() -> None:
    sup = AgentSupervisor()
    entry = sup.halt(session_id="s1", operator="alice", reason="anomaly")
    assert entry.is_active
    assert sup.is_halted("s1") is not None
    assert sup.is_halted("s1").reason == "anomaly"


def test_halt_is_idempotent() -> None:
    sup = AgentSupervisor()
    first = sup.halt(session_id="s1", operator="alice")
    second = sup.halt(session_id="s1", operator="bob", reason="another")
    assert first.halt_id == second.halt_id
    # First reason wins (idempotent)
    assert second.operator == "alice"


def test_resume_lifts_halt() -> None:
    sup = AgentSupervisor()
    sup.halt(session_id="s1", operator="alice", reason="x")
    resumed = sup.resume(session_id="s1", operator="bob", reason="clear")
    assert resumed is not None
    assert not resumed.is_active
    assert sup.is_halted("s1") is None


def test_resume_returns_none_on_unhalted_session() -> None:
    sup = AgentSupervisor()
    assert sup.resume(session_id="s1", operator="bob") is None


def test_is_halted_returns_none_for_unknown() -> None:
    sup = AgentSupervisor()
    assert sup.is_halted("unknown") is None


def test_all_active_excludes_resumed() -> None:
    sup = AgentSupervisor()
    sup.halt(session_id="s1", operator="op")
    sup.halt(session_id="s2", operator="op")
    sup.resume(session_id="s1", operator="op")
    active = sup.all_active()
    assert {e.session_id for e in active} == {"s2"}


def test_status_payload_shape() -> None:
    sup = AgentSupervisor()
    sup.halt(session_id="s1", operator="op", reason="rev")
    status = sup.status("s1")
    assert status is not None
    assert status["operator"] == "op"
    assert status["reason"] == "rev"
    assert status["resumed_at"] is None


def test_clear_removes_records() -> None:
    sup = AgentSupervisor()
    sup.halt(session_id="s1", operator="op")
    sup.halt(session_id="s2", operator="op")
    assert sup.clear({"s1"}) == 1
    assert sup.is_halted("s1") is None
    assert sup.is_halted("s2") is not None
    assert sup.clear() == 1
