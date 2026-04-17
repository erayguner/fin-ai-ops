"""Tests for provider trace adapters (ADR-008 §2)."""

from __future__ import annotations

from core.agent_trace import (
    AgentStepType,
    FailureStep,
    GuardrailEvaluationStep,
    ModelInvocationStep,
    ToolInvocationStep,
)
from providers.aws.agent_trace_adapter import BedrockTraceAdapter
from providers.gcp.agent_trace_plugin import create_trace_plugin


class TestBedrockAdapter:
    def _adapter(self) -> BedrockTraceAdapter:
        return BedrockTraceAdapter(
            agent_name="aws-finops",
            session_id="session-abc",
            correlation_id="corr-xyz",
        )

    def test_orchestration_trace_creates_model_and_tool_steps(self):
        adapter = self._adapter()
        chunk = {
            "sessionId": "session-abc",
            "trace": {
                "orchestrationTrace": {
                    "modelInvocationInput": {
                        "foundationModel": "eu.anthropic.claude-sonnet-4-5-20250929-v1:0",
                        "text": "User: list alerts",
                        "inferenceConfiguration": {"temperature": 0.0},
                    },
                    "modelInvocationOutput": {
                        "metadata": {"usage": {"inputTokens": 120, "outputTokens": 30}},
                        "parsedResponse": {"text": "I will call finops_list_alerts."},
                    },
                    "rationale": {"text": "User asked for alerts; route to list tool."},
                    "invocationInput": {
                        "actionGroupInvocationInput": {
                            "actionGroupName": "Alerts",
                            "function": "finops_list_alerts",
                            "parameters": [{"name": "limit", "value": "10"}],
                        }
                    },
                    "observation": {
                        "actionGroupInvocationOutput": {"text": "alerts: 3"},
                    },
                }
            },
        }
        adapter.consume(chunk)
        types = [s.step_type for s in adapter.trace.steps]
        assert AgentStepType.MODEL_INVOCATION in types
        assert AgentStepType.TOOL_INVOCATION in types

        model_step = next(s for s in adapter.trace.steps if isinstance(s, ModelInvocationStep))
        assert model_step.model_id.startswith("eu.anthropic.claude")
        assert model_step.input_tokens == 120
        assert model_step.output_tokens == 30

        tool_step = next(s for s in adapter.trace.steps if isinstance(s, ToolInvocationStep))
        assert tool_step.tool_name == "Alerts.finops_list_alerts"
        assert tool_step.arguments == {"limit": "10"}
        assert tool_step.succeeded is True

    def test_guardrail_trace_records_blocking_action(self):
        adapter = self._adapter()
        adapter.consume(
            {
                "trace": {
                    "guardrailTrace": {
                        "action": "GUARDRAIL_INTERVENED",
                        "guardrailVersion": "DRAFT",
                        "inputAssessments": [
                            {"contentPolicy": [{"type": "PROMPT_ATTACK"}]}
                        ],
                    }
                }
            }
        )
        step = adapter.trace.steps[-1]
        assert isinstance(step, GuardrailEvaluationStep)
        assert step.triggered is True
        assert step.action == "block"
        assert any("PROMPT_ATTACK" in f for f in step.triggered_filters)

    def test_failure_trace_yields_failure_step(self):
        adapter = self._adapter()
        adapter.consume(
            {
                "trace": {
                    "failureTrace": {
                        "failureCode": "InvalidAction",
                        "failureReason": "tool not in action group",
                    }
                }
            }
        )
        step = adapter.trace.steps[-1]
        assert isinstance(step, FailureStep)
        assert step.error_type == "InvalidAction"

    def test_api_invocation_input_is_extracted(self):
        adapter = self._adapter()
        adapter.consume(
            {
                "trace": {
                    "orchestrationTrace": {
                        "invocationInput": {
                            "apiInvocationInput": {
                                "actionGroup": "Weather",
                                "apiPath": "/get-weather",
                                "parameters": [{"name": "location", "value": "seattle"}],
                            }
                        },
                        "observation": {"finalResponse": {}},
                    }
                }
            }
        )
        tool_step = next(s for s in adapter.trace.steps if isinstance(s, ToolInvocationStep))
        assert tool_step.tool_name == "Weather/get-weather"
        assert tool_step.arguments == {"location": "seattle"}


class TestADKPlugin:
    def test_record_model_armor_verdict_adds_guardrail_step(self):
        plugin = create_trace_plugin(agent_name="gcp-finops", session_id="s-1")
        plugin.record_model_armor_verdict(
            template="finops-floor",
            action="block",
            triggered_filters=["prompt_injection"],
            raw={"mode": "INSPECT_AND_BLOCK"},
        )
        step = plugin.trace.steps[-1]
        assert isinstance(step, GuardrailEvaluationStep)
        assert step.guardrail_name == "model-armor:finops-floor"
        assert step.triggered is True
        assert step.action == "block"

    def test_record_failure_adds_failure_step(self):
        plugin = create_trace_plugin(agent_name="gcp-finops", session_id="s-1")
        plugin.record_failure(RuntimeError("transient"), recoverable=True)
        step = plugin.trace.steps[-1]
        assert isinstance(step, FailureStep)
        assert step.error_type == "RuntimeError"
        assert step.recoverable is True
