"""Canonical agent trace model — ADR-008 §1.

Provider-agnostic primitives for end-to-end agent traceability. Bedrock
``TracePart`` and ADK callback events are translated into these models
by thin adapters in ``providers/aws/agent_trace_adapter.py`` and
``providers/gcp/agent_trace_plugin.py``.

Design notes:

* ``AgentStep`` is a *discriminated union* keyed on ``step_type``.
  Pydantic's v2 discriminator machinery dispatches to the right subclass
  on deserialisation.
* Every step carries ``step_id``, ``parent_step_id``, ``session_id``,
  ``correlation_id``, a ``rationale`` (human-readable reason, best-effort
  from the provider trace), and a ``raw`` blob with the provider-native
  payload for forensic review.
* ``DecisionRecord`` is emitted whenever a gate (policy / budget / filter
  / approval / kill-switch) changes the agent's trajectory. It references
  the ``AgentStep`` that triggered it via ``triggering_step_id``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

from .models import CloudProvider

SCHEMA_VERSION = "1"

__all__ = [
    "SCHEMA_VERSION",
    "AgentStep",
    "AgentStepType",
    "AgentTrace",
    "AgentVerdict",
    "ApprovalRequestStep",
    "DecisionRecord",
    "DecisionVerdict",
    "FailureStep",
    "FilterDecisionStep",
    "GuardrailEvaluationStep",
    "HumanOverrideStep",
    "ModelInvocationStep",
    "ToolInvocationStep",
]


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class AgentStepType(StrEnum):
    MODEL_INVOCATION = "model_invocation"
    TOOL_INVOCATION = "tool_invocation"
    GUARDRAIL_EVALUATION = "guardrail_evaluation"
    APPROVAL_REQUEST = "approval_request"
    HUMAN_OVERRIDE = "human_override"
    FILTER_DECISION = "filter_decision"
    FAILURE = "failure"


class AgentVerdict(StrEnum):
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    HALTED = "halted"
    FAILED = "failed"
    APPROVAL_DENIED = "approval_denied"


class DecisionVerdict(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    APPROVAL_REQUIRED = "approval_required"
    HALT = "halt"


# ---------------------------------------------------------------------------
# Common step base
# ---------------------------------------------------------------------------


class _StepBase(BaseModel):
    """Fields shared by every ``AgentStep`` variant."""

    schema_version: str = Field(default=SCHEMA_VERSION)
    step_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    parent_step_id: str = ""
    session_id: str
    correlation_id: str = ""
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    actor: str = "agent"
    rationale: str = Field(
        default="",
        description="Human-readable reason for this step. Captured from the "
        "provider trace when available, otherwise synthesised by the "
        "adapter.",
    )
    raw: dict[str, Any] = Field(
        default_factory=dict,
        description="Provider-native payload for forensic review.",
    )


# ---------------------------------------------------------------------------
# Step variants
# ---------------------------------------------------------------------------


class ModelInvocationStep(_StepBase):
    step_type: Literal[AgentStepType.MODEL_INVOCATION] = AgentStepType.MODEL_INVOCATION
    model_id: str
    prompt_preview: str = Field(
        default="",
        description="Truncated prompt (512 chars) for audit review without "
        "storing full multi-MB payloads. Full prompt remains in ``raw``.",
    )
    response_preview: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0
    inference_config: dict[str, Any] = Field(default_factory=dict)


class ToolInvocationStep(_StepBase):
    step_type: Literal[AgentStepType.TOOL_INVOCATION] = AgentStepType.TOOL_INVOCATION
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    output_preview: Any = None
    duration_ms: float = 0.0
    succeeded: bool = True
    error: str = ""


class GuardrailEvaluationStep(_StepBase):
    """Provider guardrail verdict — Bedrock Guardrail or Model Armor."""

    step_type: Literal[AgentStepType.GUARDRAIL_EVALUATION] = AgentStepType.GUARDRAIL_EVALUATION
    guardrail_name: str
    guardrail_version: str = ""
    triggered: bool = False
    triggered_filters: list[str] = Field(default_factory=list)
    action: Literal["none", "mask", "block"] = "none"


class ApprovalRequestStep(_StepBase):
    """Captured when the agent requests human approval before proceeding."""

    step_type: Literal[AgentStepType.APPROVAL_REQUEST] = AgentStepType.APPROVAL_REQUEST
    request_id: str
    approver_pool: list[str] = Field(default_factory=list)
    expires_at: datetime | None = None
    approved: bool | None = None
    responded_by: str = ""
    response_notes: str = ""


class HumanOverrideStep(_StepBase):
    """A human operator halted or redirected the agent mid-session."""

    step_type: Literal[AgentStepType.HUMAN_OVERRIDE] = AgentStepType.HUMAN_OVERRIDE
    override_type: Literal["halt", "resume", "redirect"] = "halt"
    operator: str
    reason: str = ""


class FilterDecisionStep(_StepBase):
    """Platform content filter verdict (see ``core.filters``)."""

    step_type: Literal[AgentStepType.FILTER_DECISION] = AgentStepType.FILTER_DECISION
    filter_name: str
    verdict: Literal["allow", "redact", "block"] = "allow"
    matched_categories: list[str] = Field(default_factory=list)


class FailureStep(_StepBase):
    step_type: Literal[AgentStepType.FAILURE] = AgentStepType.FAILURE
    error_type: str = ""
    error_message: str = ""
    recoverable: bool = False


AgentStep = Annotated[
    ModelInvocationStep
    | ToolInvocationStep
    | GuardrailEvaluationStep
    | ApprovalRequestStep
    | HumanOverrideStep
    | FilterDecisionStep
    | FailureStep,
    Field(discriminator="step_type"),
]


# ---------------------------------------------------------------------------
# Decision record
# ---------------------------------------------------------------------------


class DecisionRecord(BaseModel):
    """Emitted whenever a gate alters the agent's trajectory.

    Distinct from :class:`~core.tool_governor.Artifact`: that class is
    tool-governor-specific. ``DecisionRecord`` is the uniform record type
    spanning policy, budget, filter, approval, and kill-switch gates.
    """

    schema_version: str = Field(default=SCHEMA_VERSION)
    decision_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    session_id: str
    correlation_id: str = ""
    triggering_step_id: str = ""
    decision: DecisionVerdict
    gate_name: str = Field(
        description="Which gate made the decision (e.g. 'governor', 'pii_filter')."
    )
    reason: str = ""
    policy_id: str = ""
    actor: str = "system"


# ---------------------------------------------------------------------------
# Trace container
# ---------------------------------------------------------------------------


class AgentTrace(BaseModel):
    """Per-session container for an agent's full trajectory."""

    schema_version: str = Field(default=SCHEMA_VERSION)
    session_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    agent_name: str
    provider: CloudProvider
    correlation_id: str = ""
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    ended_at: datetime | None = None
    verdict: AgentVerdict = AgentVerdict.IN_PROGRESS
    steps: list[AgentStep] = Field(default_factory=list)
    decisions: list[DecisionRecord] = Field(default_factory=list)

    def add_step(self, step: AgentStep) -> None:
        """Append a step, propagating session/correlation IDs if missing."""
        if not step.session_id:
            step.session_id = self.session_id
        if not step.correlation_id and self.correlation_id:
            step.correlation_id = self.correlation_id
        self.steps.append(step)

    def add_decision(self, decision: DecisionRecord) -> None:
        if not decision.session_id:
            decision.session_id = self.session_id
        if not decision.correlation_id and self.correlation_id:
            decision.correlation_id = self.correlation_id
        self.decisions.append(decision)

    def close(self, verdict: AgentVerdict = AgentVerdict.COMPLETED) -> None:
        self.ended_at = datetime.now(UTC)
        self.verdict = verdict

    @property
    def duration_ms(self) -> float:
        end = self.ended_at or datetime.now(UTC)
        return (end - self.started_at).total_seconds() * 1000.0

    def to_otel_spans(self) -> list[dict[str, Any]]:
        """Emit each :class:`AgentStep` as an OpenTelemetry-shaped span dict.

        Framework §9.1 — traces must be emittable as OTel spans so they
        integrate with any OTel-compatible APM (Datadog, Honeycomb,
        Grafana Tempo, Cloud Trace, X-Ray). The mapping is:

        * ``correlation_id`` → ``trace_id``
        * ``step_id``        → ``span_id``
        * ``parent_step_id`` → ``parent_span_id``

        Trace + span IDs are normalised to the OTel-required lengths
        (32 hex chars for trace_id, 16 for span_id) by SHA-256-hashing
        the source UUIDs. Span attributes carry step-type-specific
        details so backend queries can filter by tool name, model id,
        verdict, etc. without parsing the rationale string.

        Returns a list of plain dicts so consumers can serialise to OTLP
        JSON without dragging in the SDK. When the ``opentelemetry`` SDK
        is available, the caller can pass these dicts to ``trace_api``
        manually; we don't import it here to keep this module dependency-free.
        """
        import hashlib

        trace_seed = self.correlation_id or self.session_id

        def _hex(seed: str, length: int) -> str:
            return hashlib.sha256(seed.encode()).hexdigest()[:length] if seed else "0" * length

        trace_id = _hex(trace_seed, 32)
        spans: list[dict[str, Any]] = []

        for step in self.steps:
            span_id = _hex(step.step_id, 16)
            parent_span_id = _hex(step.parent_step_id, 16) if step.parent_step_id else None
            attributes: dict[str, Any] = {
                "agent.session_id": self.session_id,
                "agent.correlation_id": self.correlation_id,
                "agent.name": self.agent_name,
                "agent.provider": self.provider.value,
                "agent.step_type": step.step_type.value,
                "agent.rationale": step.rationale,
                "agent.actor": step.actor,
            }
            # Step-specific attributes.
            if isinstance(step, ModelInvocationStep):
                attributes.update(
                    {
                        "gen_ai.system": "anthropic" if "claude" in step.model_id else "gemini",
                        "gen_ai.request.model": step.model_id,
                        "gen_ai.usage.input_tokens": step.input_tokens,
                        "gen_ai.usage.output_tokens": step.output_tokens,
                        "gen_ai.response.latency_ms": step.latency_ms,
                    }
                )
            elif isinstance(step, ToolInvocationStep):
                attributes.update(
                    {
                        "tool.name": step.tool_name,
                        "tool.duration_ms": step.duration_ms,
                        "tool.succeeded": step.succeeded,
                    }
                )
                if step.error:
                    attributes["tool.error"] = step.error
            elif isinstance(step, GuardrailEvaluationStep):
                attributes.update(
                    {
                        "guardrail.name": step.guardrail_name,
                        "guardrail.triggered": step.triggered,
                        "guardrail.action": step.action,
                    }
                )
            elif isinstance(step, ApprovalRequestStep):
                attributes.update(
                    {
                        "approval.request_id": step.request_id,
                        "approval.approved": step.approved,
                    }
                )
            elif isinstance(step, HumanOverrideStep):
                attributes.update(
                    {
                        "override.type": step.override_type,
                        "override.operator": step.operator,
                    }
                )
            elif isinstance(step, FilterDecisionStep):
                attributes.update(
                    {
                        "filter.name": step.filter_name,
                        "filter.verdict": step.verdict,
                    }
                )
            elif isinstance(step, FailureStep):
                attributes.update(
                    {
                        "failure.error_type": step.error_type,
                        "failure.recoverable": step.recoverable,
                    }
                )

            spans.append(
                {
                    "name": f"agent.{step.step_type.value}",
                    "trace_id": trace_id,
                    "span_id": span_id,
                    "parent_span_id": parent_span_id,
                    "start_time_unix_nano": int(step.timestamp.timestamp() * 1e9),
                    "end_time_unix_nano": int(step.timestamp.timestamp() * 1e9),
                    "kind": "SPAN_KIND_INTERNAL",
                    "attributes": attributes,
                }
            )

        return spans

    def to_markdown(self) -> str:
        """Render a human-readable transcript for post-action review.

        Keep this terse: reviewers scan, they don't read. One line per
        step, one line per decision, timestamps in HH:MM:SS.
        """
        lines: list[str] = [
            f"# Agent session {self.session_id}",
            f"- **Agent:** {self.agent_name} ({self.provider.value})",
            f"- **Started:** {self.started_at.isoformat()}",
            f"- **Ended:** {self.ended_at.isoformat() if self.ended_at else '(in progress)'}",
            f"- **Verdict:** {self.verdict.value}",
            f"- **Duration:** {self.duration_ms:.0f}ms",
            "",
            "## Steps",
        ]
        for step in self.steps:
            ts = step.timestamp.strftime("%H:%M:%S")
            lines.append(f"- `{ts}` **{step.step_type.value}** — {_step_oneliner(step)}")
        if self.decisions:
            lines.append("")
            lines.append("## Decisions")
            for decision in self.decisions:
                ts = decision.timestamp.strftime("%H:%M:%S")
                lines.append(
                    f"- `{ts}` **{decision.gate_name}** → "
                    f"`{decision.decision.value}`: {decision.reason}"
                )
        return "\n".join(lines)


def _step_oneliner(step: _StepBase) -> str:
    """One-line summary for the Markdown render."""
    if isinstance(step, ModelInvocationStep):
        return f"model={step.model_id} in={step.input_tokens} out={step.output_tokens}"
    if isinstance(step, ToolInvocationStep):
        status = "ok" if step.succeeded else f"err:{step.error[:60]}"
        return f"tool={step.tool_name} ({status}) {step.duration_ms:.0f}ms"
    if isinstance(step, GuardrailEvaluationStep):
        return f"guardrail={step.guardrail_name} triggered={step.triggered} action={step.action}"
    if isinstance(step, ApprovalRequestStep):
        state = "approved" if step.approved else "pending" if step.approved is None else "denied"
        return f"approval={step.request_id} {state} by={step.responded_by or '-'}"
    if isinstance(step, HumanOverrideStep):
        return f"{step.override_type} by={step.operator} reason={step.reason[:80]}"
    if isinstance(step, FilterDecisionStep):
        cats = ",".join(step.matched_categories) or "-"
        return f"filter={step.filter_name} verdict={step.verdict} cats={cats}"
    if isinstance(step, FailureStep):
        return f"{step.error_type}: {step.error_message[:80]}"
    return "(unknown step)"
