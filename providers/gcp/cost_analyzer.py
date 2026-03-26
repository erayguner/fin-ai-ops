"""GCP cost estimation engine.

Estimates monthly costs for GCP resources based on their configuration.
Uses a local pricing catalogue for quick estimation, with optional
BigQuery Billing Export integration for historical accuracy.

Pricing is approximate and based on europe-west2 (London) on-demand rates.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["GCPCostAnalyzer"]

# Approximate monthly on-demand pricing (europe-west2, USD)
COMPUTE_PRICING: dict[str, float] = {
    "e2-micro": 7.67,
    "e2-small": 15.33,
    "e2-medium": 30.66,
    "e2-standard-2": 67.98,
    "e2-standard-4": 135.96,
    "n2-standard-2": 80.30,
    "n2-standard-4": 160.60,
    "n2-standard-8": 321.20,
    "n2-standard-16": 642.40,
    "n2-standard-32": 1284.80,
    "n2-highmem-2": 107.60,
    "n2-highmem-4": 215.19,
    "c2-standard-4": 167.68,
    "c2-standard-8": 335.36,
    "a2-highgpu-1g": 3066.30,
    "g2-standard-4": 876.00,
}

CLOUDSQL_PRICING: dict[str, float] = {
    "db-f1-micro": 9.37,
    "db-g1-small": 27.28,
    "db-custom-1-3840": 52.50,
    "db-custom-2-7680": 105.00,
    "db-custom-4-15360": 210.00,
    "db-custom-8-30720": 420.00,
    "db-custom-16-61440": 840.00,
}


class GCPCostAnalyzer:
    """Estimates monthly costs for GCP resources."""

    def estimate(self, resource_type: str, config: dict[str, Any]) -> float:
        """Estimate monthly cost for a resource type with given configuration."""
        estimators = {
            "compute.instances": self._estimate_compute,
            "compute.disks": self._estimate_disk,
            "cloudsql.instances": self._estimate_cloudsql,
            "container.clusters": self._estimate_gke,
            "storage.buckets": self._estimate_storage,
            "cloudfunctions.functions": self._estimate_functions,
            "bigquery.datasets": self._estimate_bigquery,
            "redis.instances": self._estimate_redis,
            "compute.addresses": self._estimate_static_ip,
        }

        estimator = estimators.get(resource_type)
        if estimator:
            return estimator(config)

        logger.warning("No cost estimator for resource type: %s", resource_type)
        return 0.0

    def _estimate_compute(self, config: dict[str, Any]) -> float:
        machine_type = config.get("machine_type", "n2-standard-2")
        count = config.get("count", 1)
        base_cost = COMPUTE_PRICING.get(machine_type, 80.30)
        return round(base_cost * count, 2)

    def _estimate_disk(self, config: dict[str, Any]) -> float:
        size_gb = config.get("size_gb", 100)
        disk_type = config.get("disk_type", "pd-balanced")
        pricing = {
            "pd-standard": 0.04,
            "pd-balanced": 0.10,
            "pd-ssd": 0.17,
            "pd-extreme": 0.125,
        }
        per_gb = pricing.get(disk_type, 0.10)
        return round(size_gb * per_gb, 2)

    def _estimate_cloudsql(self, config: dict[str, Any]) -> float:
        tier = config.get("tier", "db-custom-2-7680")
        base_cost = CLOUDSQL_PRICING.get(tier, 105.00)
        if config.get("availability_type") == "REGIONAL":
            base_cost *= 2
        storage_gb = config.get("storage_gb", 100)
        storage_cost = storage_gb * 0.17  # SSD
        return round(base_cost + storage_cost, 2)

    def _estimate_gke(self, config: dict[str, Any]) -> float:
        management_fee = 73.00  # GKE standard cluster
        node_count = config.get("node_count", 3)
        machine_type = config.get("machine_type", "n2-standard-2")
        node_cost = COMPUTE_PRICING.get(machine_type, 80.30)
        return round(management_fee + (node_count * node_cost), 2)

    def _estimate_storage(self, config: dict[str, Any]) -> float:
        storage_gb = config.get("storage_gb", 0)
        storage_class = config.get("storage_class", "STANDARD")
        pricing = {
            "STANDARD": 0.020,
            "NEARLINE": 0.010,
            "COLDLINE": 0.004,
            "ARCHIVE": 0.0012,
        }
        per_gb = pricing.get(storage_class, 0.020)
        return round(storage_gb * per_gb, 2)

    def _estimate_functions(self, config: dict[str, Any]) -> float:
        invocations = config.get("invocations_per_month", 1_000_000)
        avg_duration_ms = config.get("avg_duration_ms", 200)
        memory_mb = config.get("memory_mb", 256)
        gb_seconds = (invocations * avg_duration_ms / 1000) * (memory_mb / 1024)
        compute_cost = gb_seconds * 0.0000025
        invocation_cost = invocations * 0.0000004
        return round(compute_cost + invocation_cost, 2)

    def _estimate_bigquery(self, config: dict[str, Any]) -> float:
        storage_gb = config.get("storage_gb", 100)
        queries_tb_month = config.get("queries_tb_month", 1)
        storage_cost = storage_gb * 0.02  # Active storage
        query_cost = queries_tb_month * 6.25  # On-demand
        return round(storage_cost + query_cost, 2)

    def _estimate_redis(self, config: dict[str, Any]) -> float:
        capacity_gb = config.get("capacity_gb", 5)
        tier = config.get("tier", "STANDARD_HA")
        base_per_gb = 0.049  # Standard
        if tier == "STANDARD_HA":
            base_per_gb = 0.098
        return round(capacity_gb * base_per_gb * 730, 2)  # hourly to monthly

    def _estimate_static_ip(self, config: dict[str, Any]) -> float:
        # Unused static IPs cost ~$7.30/month
        count = config.get("count", 1)
        return round(7.30 * count, 2)
