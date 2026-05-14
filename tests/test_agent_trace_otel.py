"""Tests for AgentTrace.to_otel_spans() — framework §9.1."""

from __future__ import annotations

from core.agent_trace import (
    AgentTrace,
    GuardrailEvaluationStep,
    ModelInvocationStep,
    ToolInvocationStep,
)
from core.models import CloudProvider


def _build_trace() -> AgentTrace:
    trace = AgentTrace(
        agent_name="test-agent",
        provider=CloudProvider.AWS,
        correlation_id="corr-1",
    )
    parent = ModelInvocationStep(
        session_id=trace.session_id,
        model_id="claude-sonnet-4-5",
        input_tokens=120,
        output_tokens=50,
        rationale="opening turn",
    )
    trace.add_step(parent)
    tool = ToolInvocationStep(
        session_id=trace.session_id,
        parent_step_id=parent.step_id,
        tool_name="cost_tools.getCosts",
        duration_ms=42.0,
        succeeded=True,
        rationale="cost lookup",
    )
    trace.add_step(tool)
    guard = GuardrailEvaluationStep(
        session_id=trace.session_id,
        parent_step_id=parent.step_id,
        guardrail_name="bedrock-guardrail",
        triggered=False,
        action="none",
        rationale="content filter ran",
    )
    trace.add_step(guard)
    return trace


def test_to_otel_spans_returns_one_span_per_step() -> None:
    trace = _build_trace()
    spans = trace.to_otel_spans()
    assert len(spans) == 3


def test_otel_span_shape_required_fields() -> None:
    trace = _build_trace()
    spans = trace.to_otel_spans()
    for span in spans:
        assert "name" in span
        assert "trace_id" in span and len(span["trace_id"]) == 32
        assert "span_id" in span and len(span["span_id"]) == 16
        assert "attributes" in span
        assert span["attributes"]["agent.session_id"] == trace.session_id


def test_otel_parent_span_id_links_to_parent() -> None:
    trace = _build_trace()
    spans = trace.to_otel_spans()
    parent_id = spans[0]["span_id"]
    # Children (tool + guardrail) should reference the parent's span_id.
    assert spans[1]["parent_span_id"] == parent_id
    assert spans[2]["parent_span_id"] == parent_id


def test_model_invocation_gen_ai_attributes() -> None:
    trace = _build_trace()
    spans = trace.to_otel_spans()
    model_span = spans[0]
    assert model_span["attributes"]["gen_ai.request.model"] == "claude-sonnet-4-5"
    assert model_span["attributes"]["gen_ai.usage.input_tokens"] == 120
    assert model_span["attributes"]["gen_ai.usage.output_tokens"] == 50


def test_tool_invocation_attributes() -> None:
    trace = _build_trace()
    spans = trace.to_otel_spans()
    tool_span = spans[1]
    assert tool_span["attributes"]["tool.name"] == "cost_tools.getCosts"
    assert tool_span["attributes"]["tool.succeeded"] is True


def test_trace_id_deterministic_across_calls() -> None:
    trace = _build_trace()
    s1 = trace.to_otel_spans()[0]["trace_id"]
    s2 = trace.to_otel_spans()[0]["trace_id"]
    assert s1 == s2
