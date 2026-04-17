"""Tests for core.agent_trace — canonical agent trace model (ADR-008 §1)."""

from __future__ import annotations

import json

import pytest
from core.agent_trace import (
    SCHEMA_VERSION,
    AgentStepType,
    AgentTrace,
    AgentVerdict,
    ApprovalRequestStep,
    DecisionRecord,
    DecisionVerdict,
    FailureStep,
    FilterDecisionStep,
    GuardrailEvaluationStep,
    HumanOverrideStep,
    ModelInvocationStep,
    ToolInvocationStep,
)
from core.models import CloudProvider
from pydantic import ValidationError


def _new_trace() -> AgentTrace:
    return AgentTrace(
        agent_name="test-agent",
        provider=CloudProvider.AWS,
    )


class TestAgentTraceBasics:
    def test_defaults_are_populated(self):
        trace = _new_trace()
        assert trace.schema_version == SCHEMA_VERSION
        assert trace.session_id  # uuid
        assert trace.verdict == AgentVerdict.IN_PROGRESS
        assert trace.steps == []
        assert trace.decisions == []

    def test_add_step_propagates_session_and_correlation(self):
        trace = _new_trace()
        trace.correlation_id = "corr-123"
        step = ModelInvocationStep(model_id="claude", session_id="")
        trace.add_step(step)
        assert step.session_id == trace.session_id
        assert step.correlation_id == "corr-123"

    def test_add_step_preserves_caller_overrides(self):
        trace = _new_trace()
        step = ModelInvocationStep(
            model_id="claude",
            session_id="session-override",
            correlation_id="corr-override",
        )
        trace.add_step(step)
        assert step.session_id == "session-override"
        assert step.correlation_id == "corr-override"

    def test_close_sets_verdict_and_end(self):
        trace = _new_trace()
        trace.close(AgentVerdict.HALTED)
        assert trace.verdict == AgentVerdict.HALTED
        assert trace.ended_at is not None
        assert trace.duration_ms >= 0

    def test_add_decision_propagates_session(self):
        trace = _new_trace()
        trace.correlation_id = "c1"
        trace.add_decision(
            DecisionRecord(
                session_id="",
                decision=DecisionVerdict.DENY,
                gate_name="governor",
                reason="tool not in allow list",
            )
        )
        assert trace.decisions[0].session_id == trace.session_id
        assert trace.decisions[0].correlation_id == "c1"


class TestDiscriminatedUnion:
    def test_round_trip_each_step_type(self):
        trace = _new_trace()
        trace.add_step(ModelInvocationStep(session_id="", model_id="claude-4"))
        trace.add_step(ToolInvocationStep(session_id="", tool_name="finops_list_policies"))
        trace.add_step(
            GuardrailEvaluationStep(
                session_id="",
                guardrail_name="bedrock-guardrail",
                triggered=True,
                action="block",
            )
        )
        trace.add_step(
            ApprovalRequestStep(session_id="", request_id="req-1", approver_pool=["alice"])
        )
        trace.add_step(HumanOverrideStep(session_id="", operator="alice", reason="manual halt"))
        trace.add_step(
            FilterDecisionStep(
                session_id="",
                filter_name="pii",
                verdict="redact",
                matched_categories=["email"],
            )
        )
        trace.add_step(
            FailureStep(
                session_id="",
                error_type="TimeoutError",
                error_message="bedrock call timed out",
            )
        )

        # Round-trip via JSON
        payload = trace.model_dump(mode="json")
        restored = AgentTrace.model_validate(payload)
        assert [s.step_type for s in restored.steps] == [
            AgentStepType.MODEL_INVOCATION,
            AgentStepType.TOOL_INVOCATION,
            AgentStepType.GUARDRAIL_EVALUATION,
            AgentStepType.APPROVAL_REQUEST,
            AgentStepType.HUMAN_OVERRIDE,
            AgentStepType.FILTER_DECISION,
            AgentStepType.FAILURE,
        ]

    def test_discriminator_rejects_unknown_step_type(self):
        payload = {
            "agent_name": "x",
            "provider": "aws",
            "steps": [{"step_type": "bogus", "session_id": "s1"}],
        }
        with pytest.raises(ValidationError):
            AgentTrace.model_validate(payload)


class TestMarkdownRender:
    def test_markdown_contains_every_step(self):
        trace = _new_trace()
        trace.session_id = "session-1"
        trace.add_step(ModelInvocationStep(session_id="", model_id="claude", input_tokens=10))
        trace.add_step(
            ToolInvocationStep(session_id="", tool_name="finops_hub_status", duration_ms=42.0)
        )
        trace.add_decision(
            DecisionRecord(
                session_id="", decision=DecisionVerdict.ALLOW, gate_name="governor", reason="ok"
            )
        )
        md = trace.to_markdown()
        assert "session-1" in md
        assert "model_invocation" in md
        assert "tool_invocation" in md
        assert "governor" in md
        assert "`allow`" in md

    def test_json_serialisation_is_stable(self):
        """Ensure model_dump output is JSON-serialisable without loss."""
        trace = _new_trace()
        trace.add_step(ToolInvocationStep(session_id="", tool_name="t", arguments={"a": 1}))
        payload = trace.model_dump(mode="json")
        assert json.dumps(payload)  # raises if not serialisable
