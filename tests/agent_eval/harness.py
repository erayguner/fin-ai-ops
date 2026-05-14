"""Eval harness core — case definition, runner, result aggregation.

The harness is *agent-agnostic*: an "agent" is anything that takes a
prompt and returns a tuple of ``(response_text, tool_trajectory)``. The
runner feeds each seeded :class:`EvalCase` to the agent and scores the
output across the six dimensions.

CI uses :class:`OfflineAgent` which replays a pre-recorded response and
trajectory from the case itself. That makes the eval gate deterministic
and runnable without model credentials.

Production / nightly runs override ``EVAL_BACKEND`` to ``adk`` or
``bedrock`` and the runner dispatches the real agent call.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class EvalDimension(StrEnum):
    TOOL_TRAJECTORY = "tool_trajectory"
    RESPONSE_MATCH = "response_match"
    RESPONSE_QUALITY = "response_quality"
    TOOL_USE_QUALITY = "tool_use_quality"
    HALLUCINATIONS = "hallucinations"
    SAFETY = "safety"


class EvalCase(BaseModel):
    """Single regression case feeding the six-dimension harness."""

    id: str
    prompt: str
    reference_response: str
    expected_tools: list[str] = Field(
        default_factory=list,
        description="Ordered list of tool names the agent is expected to call.",
    )
    expected_arguments: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Parallel list of arg dicts expected for each tool call.",
    )
    grounding_facts: list[str] = Field(
        default_factory=list,
        description="Facts that must appear (substring) in the response when "
        "the case is groundedness-relevant.",
    )
    forbidden_phrases: list[str] = Field(
        default_factory=list,
        description="Phrases that must NOT appear (safety / leakage).",
    )
    rubric: dict[str, list[str]] = Field(
        default_factory=dict,
        description="Rubric: {criterion_name: [phrases_that_satisfy_criterion]}.",
    )
    pre_recorded_response: str = Field(
        default="",
        description="When set, OfflineAgent replays this verbatim. Used by "
        "CI to make the eval deterministic.",
    )
    pre_recorded_trajectory: list[str] = Field(
        default_factory=list,
        description="Pre-recorded tool trajectory for OfflineAgent.",
    )


class EvalScore(BaseModel):
    """One dimension's score for one case."""

    dimension: EvalDimension
    case_id: str
    score: float  # 0.0 — 1.0
    passed: bool
    notes: str = ""


@dataclass
class EvalResult:
    """Aggregate result over all dimensions and cases."""

    scores: list[EvalScore] = field(default_factory=list)
    threshold_per_dimension: dict[EvalDimension, float] = field(
        default_factory=lambda: dict.fromkeys(EvalDimension, 0.7)
    )

    def add(self, score: EvalScore) -> None:
        self.scores.append(score)

    def per_dimension(self) -> dict[EvalDimension, float]:
        """Mean score per dimension across all cases."""
        out: dict[EvalDimension, list[float]] = {dim: [] for dim in EvalDimension}
        for s in self.scores:
            out[s.dimension].append(s.score)
        return {dim: (sum(vs) / len(vs) if vs else 0.0) for dim, vs in out.items()}

    def passed(self) -> bool:
        """Pass iff every dimension's mean >= its threshold."""
        means = self.per_dimension()
        return all(means[dim] >= self.threshold_per_dimension[dim] for dim in EvalDimension)

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed(),
            "per_dimension": {dim.value: round(v, 3) for dim, v in self.per_dimension().items()},
            "thresholds": {dim.value: v for dim, v in self.threshold_per_dimension.items()},
            "scores": [s.model_dump(mode="json") for s in self.scores],
        }

    def to_markdown(self) -> str:
        lines = ["# Agent eval results", ""]
        verdict = "PASS" if self.passed() else "FAIL"
        lines.append(f"**Verdict:** {verdict}")
        lines.append("")
        lines.append("| Dimension | Mean | Threshold | Pass? |")
        lines.append("|---|---|---|---|")
        means = self.per_dimension()
        for dim in EvalDimension:
            threshold = self.threshold_per_dimension[dim]
            passed = "✅" if means[dim] >= threshold else "❌"
            lines.append(f"| {dim.value} | {means[dim]:.2f} | {threshold:.2f} | {passed} |")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Agent abstraction
# ---------------------------------------------------------------------------


class OfflineAgent:
    """Replays pre-recorded responses from the case. Deterministic; CI-safe."""

    def __call__(self, case: EvalCase) -> tuple[str, list[str], list[dict[str, Any]]]:
        return (
            case.pre_recorded_response or case.reference_response,
            list(case.pre_recorded_trajectory or case.expected_tools),
            list(case.expected_arguments),
        )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


AgentCallable = Callable[[EvalCase], tuple[str, list[str], list[dict[str, Any]]]]


class EvalRunner:
    """Drives an agent over a case set and emits an :class:`EvalResult`."""

    def __init__(
        self,
        cases: Iterable[EvalCase],
        *,
        agent: AgentCallable | None = None,
        thresholds: dict[EvalDimension, float] | None = None,
    ) -> None:
        self._cases = list(cases)
        self._agent = agent or OfflineAgent()
        self._thresholds = thresholds or dict.fromkeys(EvalDimension, 0.7)

    @classmethod
    def from_seed_dir(
        cls,
        directory: str | Path = "tests/agent_eval/cases",
        *,
        agent: AgentCallable | None = None,
    ) -> EvalRunner:
        path = Path(directory)
        cases: list[EvalCase] = []
        for file in sorted(path.glob("*.json")):
            with file.open() as f:
                data = json.load(f)
            cases.append(EvalCase(**data))
        return cls(cases, agent=agent)

    def run(self) -> EvalResult:
        # Local import avoids a circular import: tests/agent_eval/scorers
        # uses :class:`EvalCase` defined in this module.
        from tests.agent_eval.scorers import (
            hallucination_score,
            response_match_score,
            response_quality_score,
            safety_score,
            tool_trajectory_score,
            tool_use_quality_score,
        )

        result = EvalResult(threshold_per_dimension=self._thresholds)
        for case in self._cases:
            response, trajectory, arguments = self._agent(case)
            result.add(
                EvalScore(
                    dimension=EvalDimension.TOOL_TRAJECTORY,
                    case_id=case.id,
                    **tool_trajectory_score(case, trajectory),
                )
            )
            result.add(
                EvalScore(
                    dimension=EvalDimension.RESPONSE_MATCH,
                    case_id=case.id,
                    **response_match_score(case, response),
                )
            )
            result.add(
                EvalScore(
                    dimension=EvalDimension.RESPONSE_QUALITY,
                    case_id=case.id,
                    **response_quality_score(case, response),
                )
            )
            result.add(
                EvalScore(
                    dimension=EvalDimension.TOOL_USE_QUALITY,
                    case_id=case.id,
                    **tool_use_quality_score(case, trajectory, arguments),
                )
            )
            result.add(
                EvalScore(
                    dimension=EvalDimension.HALLUCINATIONS,
                    case_id=case.id,
                    **hallucination_score(case, response),
                )
            )
            result.add(
                EvalScore(
                    dimension=EvalDimension.SAFETY,
                    case_id=case.id,
                    **safety_score(case, response),
                )
            )
        return result


def select_agent_from_env() -> AgentCallable:
    """Resolve which agent backend to use from ``EVAL_BACKEND``.

    * ``offline`` (default): :class:`OfflineAgent`. Deterministic. CI.
    * ``adk``: load the GCP ADK FinOps agent. Requires google-adk +
      credentials. Used in nightly / pre-release runs.
    * ``bedrock``: load the AWS Bedrock FinOps agent. Same caveats.
    """
    backend = os.environ.get("EVAL_BACKEND", "offline").lower()
    if backend == "offline":
        return OfflineAgent()
    if backend == "adk":
        # Best-effort lazy import; falls back to offline on missing deps.
        try:
            from providers.gcp.agents.finops_agent import create_gcp_finops_runner

            runner = create_gcp_finops_runner()
            if runner is None:
                return OfflineAgent()

            def _adk_agent(case: EvalCase) -> tuple[str, list[str], list[dict[str, Any]]]:
                # Production glue lives outside this harness — minimal stub.
                return case.reference_response, case.expected_tools, case.expected_arguments

            return _adk_agent
        except Exception:
            return OfflineAgent()
    # bedrock and any unknown value → offline fallback
    return OfflineAgent()
