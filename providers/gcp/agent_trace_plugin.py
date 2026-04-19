"""ADK Plugin that emits canonical ``AgentStep`` events (ADR-008 §2).

Registered once on the ADK ``Runner``; the plugin hooks every callback
point the ADK exposes and translates each event into the canonical
``AgentStep`` model in ``core.agent_trace``.

Design notes:

* The plugin is intentionally small and *defensive*. The ADK Plugin ABI
  is still evolving (see ADR-008 tradeoffs), so we import lazily and
  expose a plain Python class that matches ``google.adk.plugins.BasePlugin``
  by duck typing. If ``google-adk`` is missing, the module still imports
  (so tests can exercise the trace construction without the SDK).
* The plugin delegates to a user-provided ``AgentTrace`` so the caller
  controls session identity and correlation.
* No network calls. No business logic.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Literal, cast

from core.agent_trace import (
    AgentTrace,
    FailureStep,
    GuardrailEvaluationStep,
    ModelInvocationStep,
    ToolInvocationStep,
)
from core.models import CloudProvider

if TYPE_CHECKING:  # pragma: no cover — typing only
    try:
        from google.adk.plugins import BasePlugin
    except ImportError:
        BasePlugin = object  # type: ignore[assignment,misc]

logger = logging.getLogger(__name__)

__all__ = ["ADKTracePlugin", "create_trace_plugin"]


_PROMPT_PREVIEW_CHARS = 512


class ADKTracePlugin:
    """ADK Plugin that populates a canonical :class:`AgentTrace`.

    Subclasses ``BasePlugin`` at runtime when ``google-adk`` is available.
    This avoids a hard import dependency for the test suite.
    """

    name = "finops-trace-plugin"

    def __init__(self, trace: AgentTrace) -> None:
        self._trace = trace

    @property
    def trace(self) -> AgentTrace:
        return self._trace

    # ------------------------------------------------------------------
    # Model hooks
    # ------------------------------------------------------------------

    async def before_model_callback(
        self,
        *,
        callback_context: Any,
        llm_request: Any,
    ) -> None:
        """Capture the prompt preview before the LLM is called."""
        self._trace.add_step(
            ModelInvocationStep(
                session_id=self._trace.session_id,
                correlation_id=self._trace.correlation_id,
                rationale=f"agent={_safe_attr(callback_context, 'agent_name')}",
                raw={"llm_request": _safe_dump(llm_request)},
                model_id=_safe_attr(llm_request, "model") or "",
                prompt_preview=_preview_prompt(llm_request),
                inference_config=_safe_dump(_safe_attr(llm_request, "config")),
            )
        )
        return None

    async def after_model_callback(
        self,
        *,
        callback_context: Any,
        llm_response: Any,
    ) -> None:
        """Attach response preview + token counts to the last model step."""
        last = self._last_step_of_type(ModelInvocationStep)
        if last is None:
            return None
        usage = _safe_attr(llm_response, "usage_metadata") or {}
        last.response_preview = _preview_response(llm_response)
        last.input_tokens = int(_attr_or_key(usage, "prompt_token_count", 0) or 0)
        last.output_tokens = int(_attr_or_key(usage, "candidates_token_count", 0) or 0)
        last.raw["llm_response"] = _safe_dump(llm_response)
        return None

    # ------------------------------------------------------------------
    # Tool hooks
    # ------------------------------------------------------------------

    async def before_tool_callback(
        self,
        *,
        tool: Any,
        args: dict[str, Any],
        tool_context: Any,
    ) -> None:
        self._trace.add_step(
            ToolInvocationStep(
                session_id=self._trace.session_id,
                correlation_id=self._trace.correlation_id,
                rationale=f"agent={_safe_attr(tool_context, 'agent_name')}",
                raw={"args": dict(args or {})},
                tool_name=_safe_attr(tool, "name") or tool.__class__.__name__,
                arguments=dict(args or {}),
            )
        )
        return None

    async def after_tool_callback(
        self,
        *,
        tool: Any,
        args: dict[str, Any],
        tool_context: Any,
        tool_response: Any,
    ) -> None:
        last = self._last_step_of_type(ToolInvocationStep)
        if last is None:
            return None
        last.output_preview = _summarise_output(tool_response)
        last.raw["tool_response"] = _safe_dump(tool_response)
        return None

    # ------------------------------------------------------------------
    # Guardrail / Model-Armor hook
    # ------------------------------------------------------------------

    def record_model_armor_verdict(
        self,
        *,
        template: str,
        action: str,
        triggered_filters: list[str],
        raw: dict[str, Any] | None = None,
    ) -> None:
        """Called by the Model Armor integration after each screening call.

        Kept as an explicit method (rather than a callback) because Model
        Armor is not part of the ADK lifecycle — the caller screens the
        prompt/response and reports the verdict here.
        """
        action_map: dict[str, Literal["none", "mask", "block"]] = {
            "allow": "none",
            "mask": "mask",
        }
        step_action: Literal["none", "mask", "block"] = action_map.get(
            action, cast(Literal["none", "mask", "block"], "block")
        )
        self._trace.add_step(
            GuardrailEvaluationStep(
                session_id=self._trace.session_id,
                correlation_id=self._trace.correlation_id,
                raw=dict(raw or {}),
                guardrail_name=f"model-armor:{template}",
                triggered=action != "allow",
                triggered_filters=triggered_filters,
                action=step_action,
            )
        )

    # ------------------------------------------------------------------
    # Failure hook
    # ------------------------------------------------------------------

    def record_failure(self, exc: BaseException, *, recoverable: bool = False) -> None:
        self._trace.add_step(
            FailureStep(
                session_id=self._trace.session_id,
                correlation_id=self._trace.correlation_id,
                raw={"exception_class": type(exc).__name__},
                error_type=type(exc).__name__,
                error_message=str(exc)[:512],
                recoverable=recoverable,
            )
        )

    # ------------------------------------------------------------------

    def _last_step_of_type(self, step_type: type) -> Any:
        for step in reversed(self._trace.steps):
            if isinstance(step, step_type):
                return step
        return None


def create_trace_plugin(
    *,
    agent_name: str,
    session_id: str,
    correlation_id: str = "",
) -> ADKTracePlugin:
    """Construct an :class:`ADKTracePlugin` with a fresh :class:`AgentTrace`."""
    trace = AgentTrace(
        session_id=session_id,
        agent_name=agent_name,
        provider=CloudProvider.GCP,
        correlation_id=correlation_id or session_id,
    )
    return ADKTracePlugin(trace)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_attr(obj: Any, attr: str) -> Any:
    try:
        return getattr(obj, attr, None)
    except Exception:
        return None


def _attr_or_key(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return _safe_attr(obj, name) or default


def _safe_dump(obj: Any) -> dict[str, Any]:
    """Best-effort dict dump for trace forensics."""
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return dict(obj)
    dump = getattr(obj, "model_dump", None)
    if callable(dump):
        try:
            return dump(mode="json")
        except Exception:
            return {"type": type(obj).__name__}
    return {"type": type(obj).__name__, "repr": repr(obj)[:_PROMPT_PREVIEW_CHARS]}


def _preview_prompt(llm_request: Any) -> str:
    contents = _safe_attr(llm_request, "contents")
    if not contents:
        return ""
    try:
        last = contents[-1]
        parts = _safe_attr(last, "parts") or []
        text = " ".join(str(_safe_attr(p, "text") or "") for p in parts)
    except Exception:
        return ""
    return text[:_PROMPT_PREVIEW_CHARS]


def _preview_response(llm_response: Any) -> str:
    content = _safe_attr(llm_response, "content")
    if content is None:
        return ""
    parts = _safe_attr(content, "parts") or []
    try:
        text = " ".join(str(_safe_attr(p, "text") or "") for p in parts)
    except Exception:
        return ""
    return text[:_PROMPT_PREVIEW_CHARS]


def _summarise_output(output: Any) -> Any:
    if output is None:
        return None
    if isinstance(output, (str, int, float, bool)):
        return output if not isinstance(output, str) or len(output) <= 256 else output[:256] + "…"
    if isinstance(output, dict):
        return {"type": "dict", "keys": sorted(output)[:20]}
    if isinstance(output, (list, tuple)):
        return {"type": "list", "length": len(output)}
    return {"type": type(output).__name__}
