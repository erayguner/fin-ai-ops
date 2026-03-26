"""Pricing service abstraction.

Provides cost estimation through a pluggable pricing backend:
  - LocalPricingService: Uses hardcoded pricing tables (offline, fast)
  - CachedPricingService: Wraps any backend with a TTL cache

Future backends can integrate with AWS Pricing API, GCP Cloud Billing
Catalog, or BigQuery Billing Export for real-time pricing.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Any, ClassVar

__all__ = [
    "BasePricingService",
    "CachedPricingService",
    "LocalPricingService",
]


class BasePricingService(ABC):
    """Abstract interface for cloud resource pricing."""

    @abstractmethod
    def get_monthly_cost(
        self,
        provider: str,
        resource_type: str,
        config: dict[str, Any],
        *,
        region: str | None = None,
    ) -> float:
        """Estimate monthly cost in USD for a resource configuration."""

    @abstractmethod
    def get_price_per_unit(
        self,
        provider: str,
        resource_type: str,
        unit_type: str,
        *,
        region: str | None = None,
    ) -> float | None:
        """Get the price for a specific billing unit (e.g. per-hour, per-GB)."""

    @abstractmethod
    def list_supported_resource_types(self, provider: str) -> list[str]:
        """List resource types this service can price."""


class LocalPricingService(BasePricingService):
    """Offline pricing using bundled pricing tables.

    Suitable for quick estimation and environments without API access.
    Prices are approximate on-demand rates for the configured default region.
    """

    # ── AWS on-demand pricing (eu-west-2, USD/month) ──
    AWS_EC2: ClassVar[dict[str, float]] = {
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
        "m6i.2xlarge": 385.44,
        "m7i.large": 100.74,
        "m7i.xlarge": 201.48,
        "c5.large": 81.76,
        "c5.xlarge": 163.52,
        "c5.2xlarge": 327.04,
        "c6i.large": 81.76,
        "c6i.xlarge": 163.52,
        "c7i.large": 85.78,
        "c7i.xlarge": 171.55,
        "r5.large": 126.14,
        "r5.xlarge": 252.29,
        "r5.2xlarge": 504.58,
        "r6i.large": 126.14,
        "r6i.xlarge": 252.29,
        "r7i.large": 132.86,
        "r7i.xlarge": 265.72,
        "p3.2xlarge": 2441.28,
        "p4d.24xlarge": 24412.80,
        "g4dn.xlarge": 526.68,
        "g5.xlarge": 876.00,
        "inf2.xlarge": 548.10,
    }

    AWS_RDS: ClassVar[dict[str, float]] = {
        "db.t3.micro": 14.60,
        "db.t3.small": 29.20,
        "db.t3.medium": 58.40,
        "db.m5.large": 138.70,
        "db.m5.xlarge": 277.40,
        "db.m5.2xlarge": 554.80,
        "db.m6i.large": 145.64,
        "db.m6i.xlarge": 291.27,
        "db.r5.large": 182.50,
        "db.r5.xlarge": 365.00,
        "db.r6i.large": 191.63,
        "db.r6i.xlarge": 383.25,
    }

    # ── GCP on-demand pricing (europe-west2, USD/month) ──
    GCP_COMPUTE: ClassVar[dict[str, float]] = {
        "e2-micro": 7.67,
        "e2-small": 15.33,
        "e2-medium": 30.66,
        "e2-standard-2": 67.98,
        "e2-standard-4": 135.96,
        "e2-standard-8": 271.92,
        "n2-standard-2": 80.30,
        "n2-standard-4": 160.60,
        "n2-standard-8": 321.20,
        "n2-standard-16": 642.40,
        "n2-standard-32": 1284.80,
        "n2-highmem-2": 107.60,
        "n2-highmem-4": 215.19,
        "n2-highmem-8": 430.38,
        "n2d-standard-2": 70.08,
        "n2d-standard-4": 140.16,
        "c2-standard-4": 167.68,
        "c2-standard-8": 335.36,
        "c3-standard-4": 176.06,
        "c3-standard-8": 352.13,
        "a2-highgpu-1g": 3066.30,
        "g2-standard-4": 876.00,
    }

    GCP_CLOUDSQL: ClassVar[dict[str, float]] = {
        "db-f1-micro": 9.37,
        "db-g1-small": 27.28,
        "db-custom-1-3840": 52.50,
        "db-custom-2-7680": 105.00,
        "db-custom-4-15360": 210.00,
        "db-custom-8-30720": 420.00,
        "db-custom-16-61440": 840.00,
    }

    AWS_RESOURCE_TYPES: ClassVar[list[str]] = [
        "ec2:instance",
        "rds:db",
        "rds:cluster",
        "eks:cluster",
        "s3:bucket",
        "lambda:function",
        "elasticache:cluster",
        "nat_gateway",
        "ebs:volume",
        "elb:load-balancer",
        "redshift:cluster",
        "dynamodb:table",
        "sqs:queue",
        "sns:topic",
        "cloudfront:distribution",
    ]

    GCP_RESOURCE_TYPES: ClassVar[list[str]] = [
        "compute.instances",
        "compute.disks",
        "compute.addresses",
        "cloudsql.instances",
        "container.clusters",
        "storage.buckets",
        "cloudfunctions.functions",
        "bigquery.datasets",
        "redis.instances",
        "run.services",
        "dataflow.jobs",
        "pubsub.topics",
        "spanner.instances",
    ]

    def get_monthly_cost(
        self,
        provider: str,
        resource_type: str,
        config: dict[str, Any],
        *,
        region: str | None = None,
    ) -> float:
        if provider == "aws":
            return self._estimate_aws(resource_type, config)
        if provider == "gcp":
            return self._estimate_gcp(resource_type, config)
        return 0.0

    def get_price_per_unit(
        self,
        provider: str,
        resource_type: str,
        unit_type: str,
        *,
        region: str | None = None,
    ) -> float | None:
        if provider == "aws" and resource_type == "ec2:instance":
            monthly = self.AWS_EC2.get(unit_type)
            return round(monthly / 730, 6) if monthly else None
        if provider == "gcp" and resource_type == "compute.instances":
            monthly = self.GCP_COMPUTE.get(unit_type)
            return round(monthly / 730, 6) if monthly else None
        return None

    def list_supported_resource_types(self, provider: str) -> list[str]:
        if provider == "aws":
            return list(self.AWS_RESOURCE_TYPES)
        if provider == "gcp":
            return list(self.GCP_RESOURCE_TYPES)
        return []

    # ── AWS estimators ──

    def _estimate_aws(self, resource_type: str, config: dict[str, Any]) -> float:
        estimators: dict[str, Any] = {
            "ec2:instance": self._aws_ec2,
            "rds:db": self._aws_rds,
            "rds:cluster": self._aws_rds,
            "eks:cluster": self._aws_eks,
            "s3:bucket": self._aws_s3,
            "lambda:function": self._aws_lambda,
            "elasticache:cluster": self._aws_elasticache,
            "nat_gateway": self._aws_nat,
            "ebs:volume": self._aws_ebs,
            "elb:load-balancer": self._aws_elb,
            "redshift:cluster": self._aws_redshift,
            "dynamodb:table": self._aws_dynamodb,
        }
        fn = estimators.get(resource_type)
        return round(fn(config), 2) if fn else 0.0

    def _aws_ec2(self, c: dict[str, Any]) -> float:
        return self.AWS_EC2.get(c.get("instance_type", "m5.large"), 96.36) * c.get("count", 1)

    def _aws_rds(self, c: dict[str, Any]) -> float:
        base = self.AWS_RDS.get(c.get("instance_class", "db.m5.large"), 138.70)
        if c.get("multi_az", False):
            base *= 2
        return base + c.get("storage_gb", 100) * 0.115

    def _aws_eks(self, c: dict[str, Any]) -> float:
        nodes = c.get("node_count", 3)
        node_cost = self.AWS_EC2.get(c.get("node_type", "m5.large"), 96.36)
        return 73.0 + nodes * node_cost

    def _aws_s3(self, c: dict[str, Any]) -> float:
        return c.get("storage_gb", 0) * 0.023

    def _aws_lambda(self, c: dict[str, Any]) -> float:
        inv = c.get("invocations_per_month", 1_000_000)
        dur = c.get("avg_duration_ms", 200)
        mem = c.get("memory_mb", 256)
        gb_s = (inv * dur / 1000) * (mem / 1024)
        return gb_s * 0.0000166667 + inv * 0.0000002

    def _aws_elasticache(self, c: dict[str, Any]) -> float:
        prices = {"cache.t3.micro": 12.41, "cache.m5.large": 124.10, "cache.r5.large": 165.00}
        return prices.get(c.get("node_type", "cache.m5.large"), 124.10) * c.get("node_count", 1)

    def _aws_nat(self, c: dict[str, Any]) -> float:
        return 0.048 * 730 + c.get("data_processed_gb_month", 100) * 0.048

    def _aws_ebs(self, c: dict[str, Any]) -> float:
        prices = {"gp3": 0.08, "gp2": 0.10, "io1": 0.125, "io2": 0.125, "st1": 0.045, "sc1": 0.025}
        return c.get("size_gb", 100) * prices.get(c.get("volume_type", "gp3"), 0.08)

    def _aws_elb(self, c: dict[str, Any]) -> float:
        return 0.0252 * 730 + 0.008 * c.get("lcu_estimate", 5) * 730

    def _aws_redshift(self, c: dict[str, Any]) -> float:
        prices = {"dc2.large": 182.50, "dc2.8xlarge": 3650.0, "ra3.xlplus": 274.30}
        return prices.get(c.get("node_type", "dc2.large"), 182.50) * c.get("node_count", 2)

    def _aws_dynamodb(self, c: dict[str, Any]) -> float:
        wcu = c.get("write_capacity_units", 5)
        rcu = c.get("read_capacity_units", 5)
        return wcu * 0.000735 * 730 + rcu * 0.000147 * 730

    # ── GCP estimators ──

    def _estimate_gcp(self, resource_type: str, config: dict[str, Any]) -> float:
        estimators: dict[str, Any] = {
            "compute.instances": self._gcp_compute,
            "compute.disks": self._gcp_disk,
            "compute.addresses": self._gcp_static_ip,
            "cloudsql.instances": self._gcp_cloudsql,
            "container.clusters": self._gcp_gke,
            "storage.buckets": self._gcp_storage,
            "cloudfunctions.functions": self._gcp_functions,
            "bigquery.datasets": self._gcp_bigquery,
            "redis.instances": self._gcp_redis,
            "run.services": self._gcp_cloud_run,
        }
        fn = estimators.get(resource_type)
        return round(fn(config), 2) if fn else 0.0

    def _gcp_compute(self, c: dict[str, Any]) -> float:
        return self.GCP_COMPUTE.get(c.get("machine_type", "n2-standard-2"), 80.30) * c.get(
            "count", 1
        )

    def _gcp_disk(self, c: dict[str, Any]) -> float:
        prices = {"pd-standard": 0.04, "pd-balanced": 0.10, "pd-ssd": 0.17, "pd-extreme": 0.125}
        return c.get("size_gb", 100) * prices.get(c.get("disk_type", "pd-balanced"), 0.10)

    def _gcp_static_ip(self, c: dict[str, Any]) -> float:
        return 7.30 * c.get("count", 1)

    def _gcp_cloudsql(self, c: dict[str, Any]) -> float:
        base = self.GCP_CLOUDSQL.get(c.get("tier", "db-custom-2-7680"), 105.00)
        if c.get("availability_type") == "REGIONAL":
            base *= 2
        return base + c.get("storage_gb", 100) * 0.17

    def _gcp_gke(self, c: dict[str, Any]) -> float:
        nodes = c.get("node_count", 3)
        node_cost = self.GCP_COMPUTE.get(c.get("machine_type", "n2-standard-2"), 80.30)
        return 73.0 + nodes * node_cost

    def _gcp_storage(self, c: dict[str, Any]) -> float:
        prices = {"STANDARD": 0.020, "NEARLINE": 0.010, "COLDLINE": 0.004, "ARCHIVE": 0.0012}
        return c.get("storage_gb", 0) * prices.get(c.get("storage_class", "STANDARD"), 0.020)

    def _gcp_functions(self, c: dict[str, Any]) -> float:
        inv = c.get("invocations_per_month", 1_000_000)
        dur = c.get("avg_duration_ms", 200)
        mem = c.get("memory_mb", 256)
        gb_s = (inv * dur / 1000) * (mem / 1024)
        return gb_s * 0.0000025 + inv * 0.0000004

    def _gcp_bigquery(self, c: dict[str, Any]) -> float:
        return c.get("storage_gb", 100) * 0.02 + c.get("queries_tb_month", 1) * 6.25

    def _gcp_redis(self, c: dict[str, Any]) -> float:
        base = 0.098 if c.get("tier") == "STANDARD_HA" else 0.049
        return c.get("capacity_gb", 5) * base * 730

    def _gcp_cloud_run(self, c: dict[str, Any]) -> float:
        requests = c.get("requests_per_month", 1_000_000)
        cpu_s = c.get("cpu_seconds_per_month", 50000)
        mem_gib_s = c.get("memory_gib_seconds_per_month", 100000)
        return requests * 0.0000004 + cpu_s * 0.00002400 + mem_gib_s * 0.0000025


class CachedPricingService(BasePricingService):
    """TTL cache wrapper around any pricing backend."""

    def __init__(self, backend: BasePricingService, *, cache_ttl_seconds: int = 86400) -> None:
        self._backend = backend
        self._ttl = cache_ttl_seconds
        self._cache: dict[str, tuple[float, float]] = {}

    def _cache_key(
        self, provider: str, resource_type: str, config: dict[str, Any], region: str | None
    ) -> str:
        import hashlib
        import json

        raw = json.dumps(
            {"p": provider, "r": resource_type, "c": config, "reg": region}, sort_keys=True
        )
        return hashlib.md5(raw.encode()).hexdigest()  # noqa: S324  # nosec B324

    def get_monthly_cost(
        self,
        provider: str,
        resource_type: str,
        config: dict[str, Any],
        *,
        region: str | None = None,
    ) -> float:
        key = self._cache_key(provider, resource_type, config, region)
        cached = self._cache.get(key)
        if cached and (time.time() - cached[1]) < self._ttl:
            return cached[0]
        cost = self._backend.get_monthly_cost(provider, resource_type, config, region=region)
        self._cache[key] = (cost, time.time())
        return cost

    def get_price_per_unit(
        self,
        provider: str,
        resource_type: str,
        unit_type: str,
        *,
        region: str | None = None,
    ) -> float | None:
        return self._backend.get_price_per_unit(provider, resource_type, unit_type, region=region)

    def list_supported_resource_types(self, provider: str) -> list[str]:
        return self._backend.list_supported_resource_types(provider)
