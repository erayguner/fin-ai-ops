"""AWS cost estimation engine.

Delegates to the centralised ``core.pricing.LocalPricingService`` to
avoid duplicating pricing tables. Keeps the same public interface
(``estimate(resource_type, config) -> float``) so callers are unaffected.
"""

from __future__ import annotations

import logging
from typing import Any

from core.pricing import LocalPricingService

logger = logging.getLogger(__name__)

__all__ = ["AWSCostAnalyzer"]


class AWSCostAnalyzer:
    """Estimates monthly costs for AWS resources."""

    def __init__(self) -> None:
        self._pricing = LocalPricingService()

    def estimate(self, resource_type: str, config: dict[str, Any]) -> float:
        """Estimate monthly cost for a resource type with given configuration."""
        cost = self._pricing.get_monthly_cost("aws", resource_type, config)
        if cost == 0.0 and resource_type not in self._pricing.list_supported_resource_types("aws"):
            logger.warning("No cost estimator for resource type: %s", resource_type)
        return cost
