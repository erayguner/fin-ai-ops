"""CI-gated regression: runs the 5 seed cases through the offline harness.

Asserts the offline run achieves the per-dimension thresholds. A drop
below threshold blocks the PR — that is the eval-as-merge-gate
required by framework §16.3 + ADR-008 §10.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.agent_eval import EvalDimension, EvalRunner

CASES_DIR = Path(__file__).resolve().parent / "cases"


@pytest.fixture(scope="module")
def runner() -> EvalRunner:
    return EvalRunner.from_seed_dir(CASES_DIR)


def test_eval_harness_passes_all_dimensions(runner: EvalRunner) -> None:
    result = runner.run()
    means = result.per_dimension()
    failing = [
        (dim.value, means[dim])
        for dim in EvalDimension
        if means[dim] < result.threshold_per_dimension[dim]
    ]
    assert not failing, (
        f"Eval failed below threshold on: {failing}. Full report:\n{result.to_markdown()}"
    )


def test_runner_loads_seed_cases() -> None:
    runner = EvalRunner.from_seed_dir(CASES_DIR)
    # 5 seeded cases — keep this assertion as a tripwire so new cases are
    # added intentionally, not silently.
    assert len(runner._cases) == 5


def test_offline_agent_deterministic() -> None:
    runner = EvalRunner.from_seed_dir(CASES_DIR)
    first = runner.run().to_dict()
    second = runner.run().to_dict()
    assert first == second
