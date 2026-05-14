"""Unit tests for core.approvals — ApprovalGateway, ApprovalStore."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from core.approvals import (
    ApprovalExpired,
    ApprovalNotFound,
    ApprovalRequest,
    ApprovalStore,
    ApprovalVerdict,
    LocalCLIApprovalGateway,
    default_ttl,
    issue_decision_token,
    quorum_for,
    verify_decision_token,
)


def _make_request(**overrides) -> ApprovalRequest:
    base = {
        "session_id": "sess-1",
        "tool_name": "stop_idle_instance",
        "action_summary": "Stop EC2 i-0123",
        "rationale": "tagged idle for 14d",
        "blast_radius": "single instance, dev account",
        "rollback_procedure": "start the instance again",
        "expires_at": datetime.now(UTC) + timedelta(minutes=15),
    }
    base.update(overrides)
    return ApprovalRequest(**base)


def test_quorum_for_irreversible_is_two() -> None:
    assert quorum_for(irreversible=True) == 2
    assert quorum_for(irreversible=False) == 1


def test_default_ttl_batch_vs_per_action() -> None:
    assert default_ttl(batch=False) == 15 * 60
    assert default_ttl(batch=True) == 24 * 3600


def test_store_put_get_roundtrip() -> None:
    store = ApprovalStore()
    req = _make_request()
    store.put(req)
    fetched = store.get(req.request_id)
    assert fetched.request_id == req.request_id


def test_store_get_raises_on_missing() -> None:
    store = ApprovalStore()
    with pytest.raises(ApprovalNotFound):
        store.get("missing")


def test_record_decision_resolves_on_quorum_1() -> None:
    store = ApprovalStore()
    req = _make_request()
    store.put(req)
    resolved = store.record_decision(
        request_id=req.request_id,
        approver="alice",
        verdict=ApprovalVerdict.APPROVED,
    )
    assert resolved.verdict is ApprovalVerdict.APPROVED
    assert resolved.decisions[-1].token  # token issued


def test_record_decision_requires_quorum_two() -> None:
    store = ApprovalStore()
    req = _make_request(quorum=2)
    store.put(req)
    resolved = store.record_decision(
        request_id=req.request_id,
        approver="alice",
        verdict=ApprovalVerdict.APPROVED,
    )
    assert resolved.verdict is ApprovalVerdict.PENDING
    resolved = store.record_decision(
        request_id=req.request_id,
        approver="bob",
        verdict=ApprovalVerdict.APPROVED,
    )
    assert resolved.verdict is ApprovalVerdict.APPROVED


def test_self_approval_rejected() -> None:
    store = ApprovalStore()
    req = _make_request(requested_by="alice")
    store.put(req)
    with pytest.raises(PermissionError):
        store.record_decision(
            request_id=req.request_id,
            approver="alice",
            verdict=ApprovalVerdict.APPROVED,
        )


def test_duplicate_approver_rejected() -> None:
    store = ApprovalStore()
    req = _make_request(quorum=2)
    store.put(req)
    store.record_decision(
        request_id=req.request_id,
        approver="alice",
        verdict=ApprovalVerdict.APPROVED,
    )
    with pytest.raises(PermissionError):
        store.record_decision(
            request_id=req.request_id,
            approver="alice",
            verdict=ApprovalVerdict.APPROVED,
        )


def test_deny_short_circuits_quorum() -> None:
    store = ApprovalStore()
    req = _make_request(quorum=2)
    store.put(req)
    resolved = store.record_decision(
        request_id=req.request_id,
        approver="alice",
        verdict=ApprovalVerdict.DENIED,
    )
    assert resolved.verdict is ApprovalVerdict.DENIED


def test_expired_request_raises_on_decision() -> None:
    store = ApprovalStore()
    req = _make_request(expires_at=datetime.now(UTC) - timedelta(seconds=1))
    store.put(req)
    with pytest.raises(ApprovalExpired):
        store.record_decision(
            request_id=req.request_id,
            approver="alice",
            verdict=ApprovalVerdict.APPROVED,
        )


def test_sweep_expired_flips_status() -> None:
    store = ApprovalStore()
    req = _make_request(expires_at=datetime.now(UTC) - timedelta(seconds=1))
    store.put(req)
    swept = store.sweep_expired()
    assert swept == 1
    assert store.get(req.request_id).verdict is ApprovalVerdict.EXPIRED


def test_list_expired_returns_pending_past_expiry() -> None:
    store = ApprovalStore()
    fresh = _make_request()
    stale = _make_request(expires_at=datetime.now(UTC) - timedelta(minutes=1))
    store.put(fresh)
    store.put(stale)
    expired = store.list_expired()
    assert len(expired) == 1
    assert expired[0].request_id == stale.request_id


def test_revoke_pending() -> None:
    store = ApprovalStore()
    req = _make_request()
    store.put(req)
    revoked = store.revoke(req.request_id, actor="security", reason="duplicate")
    assert revoked.verdict is ApprovalVerdict.REVOKED


def test_token_verification_roundtrip() -> None:
    token = issue_decision_token(
        request_id="r-1", approver="alice", verdict=ApprovalVerdict.APPROVED
    )
    assert verify_decision_token(
        request_id="r-1",
        approver="alice",
        verdict=ApprovalVerdict.APPROVED,
        token=token,
    )
    # Tampering breaks verification.
    assert not verify_decision_token(
        request_id="r-1",
        approver="alice",
        verdict=ApprovalVerdict.DENIED,  # changed
        token=token,
    )


def test_local_cli_gateway_renders_request(capsys) -> None:
    req = _make_request()
    gw = LocalCLIApprovalGateway()
    gw.request(req)
    captured = capsys.readouterr()
    assert req.request_id in captured.err
    assert "Stop EC2 i-0123" in captured.err
