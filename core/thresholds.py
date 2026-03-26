"""Dynamic threshold calculation engine.

Computes cost thresholds based on rolling baselines, standard deviations,
and configurable anomaly multipliers. Designed to adapt to spending patterns
rather than relying on static limits.

Thresholds are loaded from HubConfig, allowing customisation via YAML
or environment variables without code changes.
"""

from __future__ import annotations

import statistics
from datetime import UTC, datetime
from typing import ClassVar

from .config import HubConfig
from .models import CloudProvider, CostThreshold

__all__ = ["ThresholdEngine"]


class ThresholdEngine:
    """Calculates and manages dynamic cost thresholds per resource type."""

    # Hardcoded fallback (used only when no config is available)
    _FALLBACK: ClassVar[dict[str, float]] = {"warning": 500, "critical": 2000, "emergency": 5000}

    def __init__(
        self,
        anomaly_multiplier: float = 2.0,
        config: HubConfig | None = None,
    ) -> None:
        self._config = config
        self._anomaly_multiplier = (
            config.get_float("thresholds.anomaly_multiplier", anomaly_multiplier)
            if config
            else anomaly_multiplier
        )
        self._min_datapoints = config.get_int("thresholds.min_datapoints", 3) if config else 3
        self._cost_history: dict[str, list[float]] = {}

        # Build default thresholds from config or hardcoded values
        self._default_thresholds: dict[str, dict[str, float]] = (
            config.get_threshold_defaults() if config else {}
        )

    @property
    def DEFAULT_THRESHOLDS(self) -> dict[str, dict[str, float]]:  # noqa: N802
        """Public accessor retained for backward compatibility with tests."""
        return dict(self._default_thresholds)

    def record_cost(self, resource_type: str, monthly_cost_usd: float) -> None:
        """Record a monthly cost observation for a resource type."""
        self._cost_history.setdefault(resource_type, []).append(monthly_cost_usd)

    def calculate_threshold(
        self,
        provider: CloudProvider,
        resource_type: str,
    ) -> CostThreshold:
        """Calculate dynamic threshold based on historical data or defaults."""
        history = self._cost_history.get(resource_type, [])

        if len(history) >= self._min_datapoints:
            baseline = statistics.mean(history)
            stddev = statistics.stdev(history)
            warning = baseline + stddev
            critical = baseline + (2 * stddev)
            emergency = baseline + (3 * stddev)
        else:
            defaults = self._default_thresholds.get(resource_type, self._FALLBACK)
            baseline = defaults["warning"] * 0.5
            warning = defaults["warning"]
            critical = defaults["critical"]
            emergency = defaults["emergency"]

        return CostThreshold(
            provider=provider,
            resource_type=resource_type,
            warning_usd=round(warning, 2),
            critical_usd=round(critical, 2),
            emergency_usd=round(emergency, 2),
            baseline_monthly_usd=round(baseline, 2),
            anomaly_multiplier=self._anomaly_multiplier,
            last_updated=datetime.now(UTC),
        )

    def is_anomaly(self, resource_type: str, cost_usd: float) -> bool:
        """Check if a cost is anomalous relative to historical baseline."""
        history = self._cost_history.get(resource_type, [])
        if len(history) < self._min_datapoints:
            return False
        baseline = statistics.mean(history)
        return baseline > 0 and cost_usd > (baseline * self._anomaly_multiplier)

    def get_cost_increase_pct(self, resource_type: str, cost_usd: float) -> float:
        """Calculate the percentage increase over the baseline."""
        history = self._cost_history.get(resource_type, [])
        if not history:
            return 0.0
        baseline = statistics.mean(history)
        if baseline <= 0:
            return 0.0
        return round(((cost_usd - baseline) / baseline) * 100, 2)
