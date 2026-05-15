"""Agent regression eval harness — ADR-008 §10, framework §16.3.

A compliance-worthy harness covers six dimensions (ADR-008 §10):

* ``tool_trajectory``        — did the agent call the right tools in the
                                right order?
* ``response_match``         — does the final response match the
                                reference (ROUGE / LLM-judge)?
* ``response_quality``       — rubric-scored quality of the response.
* ``tool_use_quality``       — rubric-scored correctness of tool use
                                (right args, no over-calling).
* ``hallucinations``         — groundedness against retrieved context.
* ``safety``                 — harm rating against a fixed rubric.

Multi-turn cases additionally cover the ``multi_turn_task_success_v1``
analogue: the ``rubric`` key ``multi_turn_context_carried`` enforces
that context from earlier turns appears in later responses.

This package ships the scoring primitives (string-match, list-overlap
rubric scorers) so the harness runs **offline** in CI. Production
deployments swap the offline scorers for managed evaluators:

* **GCP / Vertex AI**: agent emits OTel-shaped traces (via
  :meth:`core.agent_trace.AgentTrace.to_otel_spans`) into Cloud Trace,
  then **Vertex AI Online Monitors** consumes those traces and runs
  ``rubric_based_*`` / ``hallucinations_v1`` / ``safety_v1`` scorers
  asynchronously. See
  https://docs.cloud.google.com/gemini-enterprise-agent-platform/scale#agent-evaluation
* **AWS / AgentCore**: Bedrock Agent traces and the
  ``agent_trace_adapter`` emit OTel-compatible spans; pipe these into
  **Amazon Bedrock AgentCore Evaluations**, which runs evaluations on
  sessions/traces/spans (GA, see
  https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/what-is-bedrock-agentcore.html).

The ``EVAL_BACKEND`` env var selects between offline (default), adk, or
bedrock backends. The offline harness gates every PR; managed backends
run nightly / pre-release.
"""

from __future__ import annotations

from tests.agent_eval.harness import (
    EvalCase,
    EvalDimension,
    EvalResult,
    EvalRunner,
    EvalScore,
    OfflineAgent,
)
from tests.agent_eval.scorers import (
    hallucination_score,
    response_match_score,
    response_quality_score,
    safety_score,
    tool_trajectory_score,
    tool_use_quality_score,
)

__all__ = [
    "EvalCase",
    "EvalDimension",
    "EvalResult",
    "EvalRunner",
    "EvalScore",
    "OfflineAgent",
    "hallucination_score",
    "response_match_score",
    "response_quality_score",
    "safety_score",
    "tool_trajectory_score",
    "tool_use_quality_score",
]
