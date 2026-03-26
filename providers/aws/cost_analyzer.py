"""AWS cost estimation engine.

Estimates monthly costs for AWS resources based on their configuration.
Uses a local pricing catalogue for quick estimation, with optional
Cost Explorer API integration for historical accuracy.

Pricing is approximate and based on eu-west-2 (London) on-demand rates.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["AWSCostAnalyzer"]

# Approximate monthly on-demand pricing (eu-west-2, USD)
EC2_PRICING: dict[str, float] = {
    "t3.micro": 9.20,
    "t3.small": 18.40,
    "t3.medium": 36.79,
    "t3.large": 73.58,
    "m5.large": 96.36,
    "m5.xlarge": 192.72,
    "m5.2xlarge": 385.44,
    "m5.4xlarge": 770.88,
    "m6i.large": 96.36,
    "m6i.xlarge": 192.72,
    "c5.large": 81.76,
    "c5.xlarge": 163.52,
    "c5.2xlarge": 327.04,
    "r5.large": 126.14,
    "r5.xlarge": 252.29,
    "r5.2xlarge": 504.58,
    "p3.2xlarge": 2441.28,
    "p4d.24xlarge": 24412.80,
    "g4dn.xlarge": 526.68,
}

RDS_PRICING: dict[str, float] = {
    "db.t3.micro": 14.60,
    "db.t3.small": 29.20,
    "db.t3.medium": 58.40,
    "db.m5.large": 138.70,
    "db.m5.xlarge": 277.40,
    "db.m5.2xlarge": 554.80,
    "db.r5.large": 182.50,
    "db.r5.xlarge": 365.00,
}


class AWSCostAnalyzer:
    """Estimates monthly costs for AWS resources."""

    def estimate(self, resource_type: str, config: dict[str, Any]) -> float:
        """Estimate monthly cost for a resource type with given configuration."""
        estimators = {
            "ec2:instance": self._estimate_ec2,
            "rds:db": self._estimate_rds,
            "rds:cluster": self._estimate_rds,
            "eks:cluster": self._estimate_eks,
            "s3:bucket": self._estimate_s3,
            "lambda:function": self._estimate_lambda,
            "elasticache:cluster": self._estimate_elasticache,
            "nat_gateway": self._estimate_nat_gateway,
            "ebs:volume": self._estimate_ebs,
            "elb:load-balancer": self._estimate_elb,
            "redshift:cluster": self._estimate_redshift,
        }

        estimator = estimators.get(resource_type)
        if estimator:
            return estimator(config)

        logger.warning("No cost estimator for resource type: %s", resource_type)
        return 0.0

    def _estimate_ec2(self, config: dict[str, Any]) -> float:
        instance_type = config.get("instance_type", "m5.large")
        count = config.get("count", 1)
        base_cost = EC2_PRICING.get(instance_type, 96.36)
        return round(base_cost * count, 2)

    def _estimate_rds(self, config: dict[str, Any]) -> float:
        instance_class = config.get("instance_class", "db.m5.large")
        base_cost = RDS_PRICING.get(instance_class, 138.70)
        if config.get("multi_az", False):
            base_cost *= 2
        storage_gb = config.get("storage_gb", 100)
        storage_cost = storage_gb * 0.115  # gp3 pricing
        return round(base_cost + storage_cost, 2)

    def _estimate_eks(self, config: dict[str, Any]) -> float:
        cluster_cost = 73.00  # EKS control plane
        node_count = config.get("node_count", 3)
        node_type = config.get("node_type", "m5.large")
        node_cost = EC2_PRICING.get(node_type, 96.36)
        return round(cluster_cost + (node_count * node_cost), 2)

    def _estimate_s3(self, config: dict[str, Any]) -> float:
        storage_gb = config.get("storage_gb", 0)
        return round(storage_gb * 0.023, 2)  # Standard tier

    def _estimate_lambda(self, config: dict[str, Any]) -> float:
        # Estimate based on invocations and duration
        invocations_per_month = config.get("invocations_per_month", 1_000_000)
        avg_duration_ms = config.get("avg_duration_ms", 200)
        memory_mb = config.get("memory_mb", 256)
        gb_seconds = (invocations_per_month * avg_duration_ms / 1000) * (memory_mb / 1024)
        compute_cost = gb_seconds * 0.0000166667
        request_cost = invocations_per_month * 0.0000002
        return round(compute_cost + request_cost, 2)

    def _estimate_elasticache(self, config: dict[str, Any]) -> float:
        node_type = config.get("node_type", "cache.m5.large")
        node_count = config.get("node_count", 1)
        # Approximate pricing
        pricing = {"cache.t3.micro": 12.41, "cache.m5.large": 124.10, "cache.r5.large": 165.00}
        base = pricing.get(node_type, 124.10)
        return round(base * node_count, 2)

    def _estimate_nat_gateway(self, config: dict[str, Any]) -> float:
        hourly = 0.048  # eu-west-2
        data_gb = config.get("data_processed_gb_month", 100)
        base = hourly * 730  # hours in a month
        data_cost = data_gb * 0.048
        return round(base + data_cost, 2)

    def _estimate_ebs(self, config: dict[str, Any]) -> float:
        size_gb = config.get("size_gb", 100)
        volume_type = config.get("volume_type", "gp3")
        pricing = {"gp3": 0.08, "gp2": 0.10, "io1": 0.125, "st1": 0.045, "sc1": 0.025}
        per_gb = pricing.get(volume_type, 0.08)
        return round(size_gb * per_gb, 2)

    def _estimate_elb(self, config: dict[str, Any]) -> float:
        # ALB pricing
        hourly = 0.0252
        lcu_cost = 0.008 * config.get("lcu_estimate", 5)
        return round((hourly * 730) + (lcu_cost * 730), 2)

    def _estimate_redshift(self, config: dict[str, Any]) -> float:
        node_type = config.get("node_type", "dc2.large")
        node_count = config.get("node_count", 2)
        pricing = {"dc2.large": 182.50, "dc2.8xlarge": 3650.00, "ra3.xlplus": 274.30}
        base = pricing.get(node_type, 182.50)
        return round(base * node_count, 2)
