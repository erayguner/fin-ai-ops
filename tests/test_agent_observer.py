"""Unit tests for core.agent_observer — anomaly + drift detection."""

from __future__ import annotations

from core.agent_observer import AgentObserver, AnomalySeverity


def test_record_tool_call_increments_metrics() -> None:
    obs = AgentObserver()
    obs.record_tool_call("s1", "tool_a")
    obs.record_tool_call("s1", "tool_b")
    obs.record_tool_call("s1", "tool_a")
    snap = obs.snapshot("s1")
    assert snap is not None
    assert snap["call_count"] == 3
    assert snap["tool_distribution"] == {"tool_a": 2, "tool_b": 1}


def test_record_tokens_accumulates() -> None:
    obs = AgentObserver()
    obs.record_tokens("s1", input_tokens=100, output_tokens=50, estimated_cost_usd=0.001)
    obs.record_tokens("s1", input_tokens=200, output_tokens=80, estimated_cost_usd=0.002)
    snap = obs.snapshot("s1")
    assert snap["input_tokens"] == 300
    assert snap["output_tokens"] == 130
    assert snap["estimated_cost_usd"] == 0.003


def test_token_budget_warns_at_75_pct() -> None:
    obs = AgentObserver()
    obs.record_tokens("s1", input_tokens=750, output_tokens=0)
    signals = obs.evaluate("s1", token_budget=1000)
    assert any(
        s.severity is AnomalySeverity.WARNING and s.detector == "token_budget" for s in signals
    )


def test_token_budget_halts_at_100_pct() -> None:
    obs = AgentObserver()
    obs.record_tokens("s1", input_tokens=600, output_tokens=400)
    signals = obs.evaluate("s1", token_budget=1000)
    halt_signals = [s for s in signals if s.recommend_halt]
    assert halt_signals
    assert halt_signals[0].detector == "token_budget"


def test_cost_budget_halts_at_100_pct() -> None:
    obs = AgentObserver()
    obs.record_tokens("s1", estimated_cost_usd=10.0)
    signals = obs.evaluate("s1", cost_budget_usd=10.0)
    halt_signals = [s for s in signals if s.recommend_halt]
    assert halt_signals
    assert halt_signals[0].detector == "cost_budget"


def test_tool_distribution_shift_emits_warning() -> None:
    baseline = {"tool_a": 0.9, "tool_b": 0.1}
    obs = AgentObserver(baseline=baseline, tool_distribution_shift_threshold=0.3)
    for _ in range(10):
        obs.record_tool_call("s1", "tool_b")
    signals = obs.evaluate("s1")
    shift_signals = [s for s in signals if s.detector == "tool_distribution"]
    assert shift_signals
    assert shift_signals[0].severity is AnomalySeverity.WARNING


def test_no_signals_when_within_baseline() -> None:
    obs = AgentObserver()
    obs.record_tool_call("s1", "tool_a")
    signals = obs.evaluate("s1")  # no budgets passed → nothing to alarm on
    assert all(not s.recommend_halt for s in signals)


def test_call_rate_zscore_warns_after_history() -> None:
    obs = AgentObserver(rate_zscore_warn=1.0, rate_zscore_critical=5.0)
    # Seed three evaluations to build up rate_history.
    for _ in range(3):
        obs.evaluate("s1")
    # Now slam in a burst so the next evaluate sees a rate spike.
    for _ in range(50):
        obs.record_tool_call("s1", "tool_a")
    signals = obs.evaluate("s1")
    assert any(s.detector == "call_rate_zscore" for s in signals)


def test_reset_clears_session() -> None:
    obs = AgentObserver()
    obs.record_tool_call("s1", "tool_a")
    obs.reset("s1")
    assert obs.snapshot("s1") is None
