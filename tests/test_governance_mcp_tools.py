"""Tests for the 5 new governance MCP tools + the kill-switch + per-principal budget."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from core.approvals import ApprovalRequest, ApprovalVerdict, issue_decision_token


@pytest.fixture(autouse=True)
def _reset_governance_singletons(monkeypatch):
    """Each test gets a fresh supervisor/observer/store via the MCP module."""
    from mcp_server import server

    server.supervisor.clear()
    server.observer.reset()
    # Clear approval store records by replacing it.
    server.approval_store._records.clear()
    yield


def test_finops_halt_session_records_halt() -> None:
    from mcp_server import server

    result = server.finops_halt_session(session_id="s1", operator="alice", reason="anomaly")
    assert result["status"] == "halted"
    assert server.supervisor.is_halted("s1") is not None


def test_finops_resume_session_lifts_halt() -> None:
    from mcp_server import server

    server.finops_halt_session(session_id="s1", operator="alice")
    result = server.finops_resume_session(session_id="s1", operator="bob", reason="clear")
    assert result["status"] == "resumed"
    assert server.supervisor.is_halted("s1") is None


def test_finops_resume_session_no_op_when_not_halted() -> None:
    from mcp_server import server

    result = server.finops_resume_session(session_id="s1", operator="bob")
    assert result["status"] == "not_halted"


def test_finops_pending_approvals_lists_pending() -> None:
    from mcp_server import server

    req = ApprovalRequest(
        session_id="s1",
        action_summary="stop instance",
        expires_at=datetime.now(UTC) + timedelta(minutes=15),
    )
    server.approval_store.put(req)
    result = server.finops_pending_approvals()
    assert result["count"] == 1
    assert result["requests"][0]["request_id"] == req.request_id


def test_finops_respond_approval_approves() -> None:
    from mcp_server import server

    req = ApprovalRequest(
        session_id="s1",
        action_summary="apply tags",
        expires_at=datetime.now(UTC) + timedelta(minutes=15),
    )
    server.approval_store.put(req)
    result = server.finops_respond_approval(
        request_id=req.request_id,
        approver="alice",
        verdict="approved",
        notes="ok",
    )
    assert result["status"] == "approved"
    assert result["decision_token"]


def test_finops_respond_approval_rejects_invalid_verdict() -> None:
    from mcp_server import server

    result = server.finops_respond_approval(request_id="r1", approver="alice", verdict="probably")
    assert result["status"] == "error"


def test_finops_respond_approval_validates_token_when_supplied() -> None:
    from mcp_server import server

    req = ApprovalRequest(
        session_id="s1",
        action_summary="x",
        expires_at=datetime.now(UTC) + timedelta(minutes=15),
    )
    server.approval_store.put(req)
    # Forge a wrong token; expect rejection.
    bad_token = issue_decision_token(
        request_id="other-id", approver="alice", verdict=ApprovalVerdict.APPROVED
    )
    result = server.finops_respond_approval(
        request_id=req.request_id,
        approver="alice",
        verdict="approved",
        token=bad_token,
    )
    assert result["status"] == "error"
    assert "invalid decision token" in result["message"]


def test_finops_session_stats_returns_snapshot() -> None:
    from mcp_server import server

    server.observer.record_tool_call("s1", "finops_hub_status")
    result = server.finops_session_stats(session_id="s1")
    assert result["session_id"] == "s1"
    assert result["metrics"]["call_count"] >= 1


def test_finops_replay_session_returns_transcript() -> None:
    from mcp_server import server

    server.audit_logger.log(
        action="agent.step.tool_invocation",
        actor="agent",
        target="s-replay",
        correlation_id="s-replay",
        details={"step_id": "step1"},
    )
    result = server.finops_replay_session(session_id="s-replay", fmt="markdown")
    assert result["status"] == "ok"
    assert "Session replay" in result["markdown"]
    assert result["count"] >= 1


def test_finops_replay_session_missing_returns_not_found() -> None:
    from mcp_server import server

    result = server.finops_replay_session(session_id="never-existed", fmt="markdown")
    assert result["status"] == "not_found"


def test_handle_tool_call_strips_reserved_args() -> None:
    """`_principal_id` and `_session_id` must NOT reach the underlying tool."""
    from mcp_server import server

    # Use a tool that takes no arguments — hub_status — so the reserved keys
    # are stripped without affecting the call.
    result = server.handle_tool_call(
        "finops_hub_status",
        {"_principal_id": "alice", "_session_id": "s1"},
    )
    assert result.get("status") == "running"


def test_handle_tool_call_halted_session_denied() -> None:
    from mcp_server import server

    server.supervisor.halt(session_id="s-bad", operator="security")
    result = server.handle_tool_call(
        "finops_hub_status",
        {"_principal_id": "alice", "_session_id": "s-bad"},
    )
    assert result["status"] == "denied"
    assert "halted" in result["message"].lower()
