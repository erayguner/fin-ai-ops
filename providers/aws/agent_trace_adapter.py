"""Bedrock Agent ``TracePart`` → canonical ``AgentTrace`` adapter (ADR-008 §2).

Bedrock emits a stream of ``TracePart`` chunks as part of the InvokeAgent
response. Each chunk contains a ``trace`` field that may be one of:

* ``preProcessingTrace``
* ``orchestrationTrace``
* ``postProcessingTrace``
* ``routingClassifierTrace``
* ``customOrchestrationTrace``
* ``guardrailTrace``
* ``failureTrace``

Reference: https://docs.aws.amazon.com/bedrock/latest/userguide/trace-events.html

This adapter is deliberately thin: no business logic, just translation.
Callers are expected to feed chunks in the order Bedrock emits them; the
adapter maintains the per-session ``AgentTrace`` and appends steps.
"""

from __future__ import annotations

from typing import Any, Literal, cast

from core.agent_trace import (
    AgentTrace,
    FailureStep,
    GuardrailEvaluationStep,
    ModelInvocationStep,
    ToolInvocationStep,
)
from core.models import CloudProvider

__all__ = [
    "BedrockTraceAdapter",
]


_PROMPT_PREVIEW_CHARS = 512


class BedrockTraceAdapter:
    """Stateful adapter that accumulates Bedrock trace chunks into a trace."""

    def __init__(
        self,
        *,
        agent_name: str,
        session_id: str,
        correlation_id: str = "",
    ) -> None:
        self._trace = AgentTrace(
            session_id=session_id,
            agent_name=agent_name,
            provider=CloudProvider.AWS,
            correlation_id=correlation_id or session_id,
        )
        # Populated per chunk from callerChain — propagated onto child steps.
        self._current_parent_step_id: str = ""

    @property
    def trace(self) -> AgentTrace:
        return self._trace

    def consume(self, trace_part: dict[str, Any]) -> None:
        """Translate a single Bedrock ``TracePart`` dict into step(s)."""
        trace = trace_part.get("trace", {}) or {}
        session_id = trace_part.get("sessionId", "")
        if session_id and session_id != self._trace.session_id:
            # Bedrock should never switch session mid-stream, but if it
            # does we want the mismatch in the raw payload for forensics.
            import logging

            logging.getLogger(__name__).warning(
                "BedrockTraceAdapter: session_id mismatch (expected=%s, observed=%s) — "
                "still appending to current trace; raw payload preserved.",
                self._trace.session_id,
                session_id,
            )

        # ADR-008 §3.3 / F-9: derive parent_step_id from Bedrock callerChain
        # to preserve multi-agent provenance. Each entry has an agentAliasArn
        # whose chain implies the upstream agent that delegated this hop.
        caller_chain = trace_part.get("callerChain") or []
        parent_id = ""
        if isinstance(caller_chain, list) and caller_chain:
            # The last entry is the immediate caller; earlier entries are the
            # upstream lineage. Hash the alias to derive a deterministic
            # parent_step_id so subsequent hops resolve to the same ID.
            import hashlib

            last_alias = (caller_chain[-1] or {}).get("agentAliasArn", "") or ""
            if last_alias:
                parent_id = hashlib.sha256(last_alias.encode()).hexdigest()[:32]
        self._current_parent_step_id = parent_id

        if "orchestrationTrace" in trace:
            self._consume_orchestration(trace["orchestrationTrace"], trace_part)
        if "preProcessingTrace" in trace:
            self._consume_model("preprocessing", trace["preProcessingTrace"], trace_part)
        if "postProcessingTrace" in trace:
            self._consume_model("postprocessing", trace["postProcessingTrace"], trace_part)
        if "routingClassifierTrace" in trace:
            self._consume_model("routing", trace["routingClassifierTrace"], trace_part)
        if "customOrchestrationTrace" in trace:
            # ADR-008 §2 / F-9 fix: customOrchestrationTrace was previously
            # silently dropped. Treat it as a generic model invocation so the
            # raw payload is retained for forensic review.
            self._consume_model(
                "custom_orchestration", trace["customOrchestrationTrace"], trace_part
            )
        if "guardrailTrace" in trace:
            self._consume_guardrail(trace["guardrailTrace"], trace_part)
        if "failureTrace" in trace:
            self._consume_failure(trace["failureTrace"], trace_part)

    # ------------------------------------------------------------------
    # Orchestration: model invocation + tool invocation in one payload
    # ------------------------------------------------------------------

    def _consume_orchestration(self, orch: dict[str, Any], raw: dict[str, Any]) -> None:
        model_input = orch.get("modelInvocationInput") or {}
        model_output = orch.get("modelInvocationOutput") or {}
        if model_input or model_output:
            metadata = model_output.get("metadata") or {}
            usage = metadata.get("usage") or {}
            step = ModelInvocationStep(
                session_id=self._trace.session_id,
                correlation_id=self._trace.correlation_id,
                parent_step_id=self._current_parent_step_id,
                rationale=_truncate(
                    (model_output.get("rawResponse") or {}).get("content", ""),
                    _PROMPT_PREVIEW_CHARS,
                ),
                raw={"input": model_input, "output": model_output},
                model_id=model_input.get("foundationModel", ""),
                prompt_preview=_truncate(model_input.get("text", ""), _PROMPT_PREVIEW_CHARS),
                response_preview=_truncate(
                    (model_output.get("parsedResponse") or {}).get("text", ""),
                    _PROMPT_PREVIEW_CHARS,
                ),
                input_tokens=int(usage.get("inputTokens", 0)),
                output_tokens=int(usage.get("outputTokens", 0)),
                latency_ms=float(metadata.get("totalTimeMs", 0)),
                inference_config=dict(model_input.get("inferenceConfiguration") or {}),
            )
            self._trace.add_step(step)

        invocation_input = orch.get("invocationInput") or {}
        observation = orch.get("observation") or {}
        if invocation_input or observation:
            tool_name, arguments = _extract_tool_call(invocation_input)
            succeeded = (
                "actionGroupInvocationOutput" in observation
                or "knowledgeBaseLookupOutput" in observation
            )
            error = ""
            if "finalResponse" not in observation and not succeeded:
                error = (observation.get("failureTrace") or {}).get("failureReason", "")
            self._trace.add_step(
                ToolInvocationStep(
                    session_id=self._trace.session_id,
                    correlation_id=self._trace.correlation_id,
                    rationale=(orch.get("rationale") or {}).get("text", "")[:_PROMPT_PREVIEW_CHARS],
                    raw={"invocationInput": invocation_input, "observation": observation},
                    tool_name=tool_name,
                    arguments=arguments,
                    output_preview=_summarise_observation(observation),
                    succeeded=succeeded and not error,
                    error=error,
                )
            )

    # ------------------------------------------------------------------
    # Model-only traces: pre/post/routing share the same shape
    # ------------------------------------------------------------------

    def _consume_model(self, phase: str, payload: dict[str, Any], raw: dict[str, Any]) -> None:
        model_input = payload.get("modelInvocationInput") or {}
        model_output = payload.get("modelInvocationOutput") or {}
        if not model_input and not model_output:
            return
        metadata = model_output.get("metadata") or {}
        usage = metadata.get("usage") or {}
        self._trace.add_step(
            ModelInvocationStep(
                session_id=self._trace.session_id,
                correlation_id=self._trace.correlation_id,
                parent_step_id=self._current_parent_step_id,
                rationale=f"phase={phase}",
                raw={"phase": phase, "input": model_input, "output": model_output},
                model_id=model_input.get("foundationModel", ""),
                prompt_preview=_truncate(model_input.get("text", ""), _PROMPT_PREVIEW_CHARS),
                response_preview=_truncate(
                    (model_output.get("parsedResponse") or {}).get("text", ""),
                    _PROMPT_PREVIEW_CHARS,
                ),
                input_tokens=int(usage.get("inputTokens", 0)),
                output_tokens=int(usage.get("outputTokens", 0)),
                latency_ms=float(metadata.get("totalTimeMs", 0)),
                inference_config=dict(model_input.get("inferenceConfiguration") or {}),
            )
        )

    # ------------------------------------------------------------------

    def _consume_guardrail(self, guardrail: dict[str, Any], raw: dict[str, Any]) -> None:
        action = guardrail.get("action", "NONE")
        triggered = action != "NONE"
        triggered_filters: list[str] = []
        for assessment in guardrail.get("inputAssessments", []) + guardrail.get(
            "outputAssessments", []
        ):
            for filter_name, filters in assessment.items():
                if isinstance(filters, list):
                    triggered_filters.extend(
                        f"{filter_name}:{f.get('type', 'unknown')}" for f in filters
                    )
        action_map: dict[str, Literal["none", "mask", "block"]] = {
            "NONE": "none",
            "GUARDRAIL_INTERVENED": "block",
        }
        step_action: Literal["none", "mask", "block"] = action_map.get(
            action, cast(Literal["none", "mask", "block"], "block")
        )
        self._trace.add_step(
            GuardrailEvaluationStep(
                session_id=self._trace.session_id,
                correlation_id=self._trace.correlation_id,
                parent_step_id=self._current_parent_step_id,
                raw=guardrail,
                guardrail_name="bedrock-guardrail",
                guardrail_version=str(guardrail.get("guardrailVersion", "")),
                triggered=triggered,
                triggered_filters=triggered_filters,
                action=step_action,
            )
        )

    # ------------------------------------------------------------------

    def _consume_failure(self, failure: dict[str, Any], raw: dict[str, Any]) -> None:
        self._trace.add_step(
            FailureStep(
                session_id=self._trace.session_id,
                correlation_id=self._trace.correlation_id,
                parent_step_id=self._current_parent_step_id,
                raw=failure,
                error_type=failure.get("failureCode", "BedrockFailure"),
                error_message=failure.get("failureReason", ""),
                recoverable=False,
            )
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _truncate(text: Any, max_len: int) -> str:
    if text is None:
        return ""
    s = str(text)
    return s if len(s) <= max_len else s[:max_len] + "…"


def _extract_tool_call(invocation_input: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Pull ``(tool_name, arguments)`` out of a Bedrock invocationInput.

    Bedrock has two representations: function-details (newer) and
    OpenAPI/apiInvocationInput (legacy). We cover both.
    """
    fn = invocation_input.get("actionGroupInvocationInput") or {}
    if fn:
        action_group = fn.get("actionGroupName", "")
        function = fn.get("function", "")
        tool_name = f"{action_group}.{function}" if function else action_group or "action_group"
        params = {p.get("name", ""): p.get("value", "") for p in fn.get("parameters", [])}
        return tool_name, params
    api = invocation_input.get("apiInvocationInput") or {}
    if api:
        action_group = api.get("actionGroup", "")
        path = api.get("apiPath", "")
        tool_name = f"{action_group}{path}" if action_group and path else action_group or "api_call"
        api_params: dict[str, Any] = {}
        for p in api.get("parameters", []) or []:
            api_params[p.get("name", "")] = p.get("value", "")
        request_body = api.get("requestBody") or {}
        if request_body:
            api_params["_body"] = request_body
        return tool_name, api_params
    kb = invocation_input.get("knowledgeBaseLookupInput") or {}
    if kb:
        return (
            f"knowledge_base:{kb.get('knowledgeBaseId', '')}",
            {"text": kb.get("text", "")},
        )
    return "unknown_tool", {}


def _summarise_observation(observation: dict[str, Any]) -> Any:
    """Summarise the tool observation without storing full payloads."""
    if "finalResponse" in observation:
        return {"type": "final_response"}
    if "actionGroupInvocationOutput" in observation:
        out = observation["actionGroupInvocationOutput"]
        text = out.get("text", "") if isinstance(out, dict) else ""
        return {"type": "action_group", "text_preview": _truncate(text, 256)}
    if "knowledgeBaseLookupOutput" in observation:
        out = observation["knowledgeBaseLookupOutput"]
        refs = out.get("retrievedReferences", []) if isinstance(out, dict) else []
        return {"type": "knowledge_base", "references": len(refs)}
    if "repromptResponse" in observation:
        return {"type": "reprompt"}
    return {"type": "unknown"}
