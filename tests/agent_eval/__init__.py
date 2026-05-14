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

This package ships the scoring primitives (string-match, list-overlap
rubric scorers) so the harness runs **offline** in CI. Production
deployments swap the offline scorers for LLM-judge / ADK Evaluate
backends via the ``EVAL_BACKEND`` env var.
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
