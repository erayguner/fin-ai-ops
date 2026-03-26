"""Tests for the pricing service abstraction."""

from __future__ import annotations

from core.pricing import CachedPricingService, LocalPricingService


class TestLocalPricingService:
    def setup_method(self):
        self.svc = LocalPricingService()

    def test_aws_ec2_estimate(self):
        cost = self.svc.get_monthly_cost("aws", "ec2:instance", {"instance_type": "m5.large"})
        assert cost > 0
        assert cost == 96.36

    def test_aws_ec2_count(self):
        cost = self.svc.get_monthly_cost(
            "aws", "ec2:instance", {"instance_type": "t3.micro", "count": 3}
        )
        assert cost == round(9.20 * 3, 2)

    def test_aws_rds_estimate(self):
        cost = self.svc.get_monthly_cost(
            "aws", "rds:db", {"instance_class": "db.m5.large", "storage_gb": 100}
        )
        assert cost > 138.0

    def test_aws_rds_multi_az(self):
        single = self.svc.get_monthly_cost("aws", "rds:db", {"instance_class": "db.m5.large"})
        multi = self.svc.get_monthly_cost(
            "aws", "rds:db", {"instance_class": "db.m5.large", "multi_az": True}
        )
        assert multi > single

    def test_aws_s3_estimate(self):
        cost = self.svc.get_monthly_cost("aws", "s3:bucket", {"storage_gb": 1000})
        assert cost == 23.0

    def test_aws_lambda_estimate(self):
        cost = self.svc.get_monthly_cost(
            "aws",
            "lambda:function",
            {
                "invocations_per_month": 1_000_000,
                "avg_duration_ms": 200,
                "memory_mb": 256,
            },
        )
        assert cost > 0

    def test_aws_dynamodb_estimate(self):
        cost = self.svc.get_monthly_cost(
            "aws",
            "dynamodb:table",
            {
                "write_capacity_units": 10,
                "read_capacity_units": 10,
            },
        )
        assert cost > 0

    def test_gcp_compute_estimate(self):
        cost = self.svc.get_monthly_cost(
            "gcp", "compute.instances", {"machine_type": "n2-standard-2"}
        )
        assert cost == 80.30

    def test_gcp_cloudsql_regional(self):
        single = self.svc.get_monthly_cost(
            "gcp", "cloudsql.instances", {"tier": "db-custom-2-7680"}
        )
        regional = self.svc.get_monthly_cost(
            "gcp",
            "cloudsql.instances",
            {
                "tier": "db-custom-2-7680",
                "availability_type": "REGIONAL",
            },
        )
        assert regional > single

    def test_gcp_cloud_run(self):
        cost = self.svc.get_monthly_cost(
            "gcp",
            "run.services",
            {
                "requests_per_month": 1_000_000,
                "cpu_seconds_per_month": 50000,
            },
        )
        assert cost > 0

    def test_gcp_bigquery(self):
        cost = self.svc.get_monthly_cost(
            "gcp",
            "bigquery.datasets",
            {
                "storage_gb": 100,
                "queries_tb_month": 1,
            },
        )
        assert cost > 0

    def test_unknown_resource_returns_zero(self):
        cost = self.svc.get_monthly_cost("aws", "unknown:type", {})
        assert cost == 0.0

    def test_unknown_provider_returns_zero(self):
        cost = self.svc.get_monthly_cost("azure", "vm", {})
        assert cost == 0.0

    def test_get_price_per_unit_aws(self):
        hourly = self.svc.get_price_per_unit("aws", "ec2:instance", "m5.large")
        assert hourly is not None
        assert hourly > 0

    def test_get_price_per_unit_unknown(self):
        assert self.svc.get_price_per_unit("aws", "ec2:instance", "nonexistent") is None

    def test_list_supported_aws(self):
        types = self.svc.list_supported_resource_types("aws")
        assert "ec2:instance" in types
        assert "dynamodb:table" in types

    def test_list_supported_gcp(self):
        types = self.svc.list_supported_resource_types("gcp")
        assert "compute.instances" in types
        assert "run.services" in types

    def test_expanded_instance_types(self):
        # New generation instance types
        assert self.svc.get_monthly_cost("aws", "ec2:instance", {"instance_type": "m7i.large"}) > 0
        assert (
            self.svc.get_monthly_cost("gcp", "compute.instances", {"machine_type": "c3-standard-4"})
            > 0
        )


class TestCachedPricingService:
    def test_caches_results(self):
        backend = LocalPricingService()
        cached = CachedPricingService(backend, cache_ttl_seconds=3600)
        cost1 = cached.get_monthly_cost("aws", "ec2:instance", {"instance_type": "m5.large"})
        cost2 = cached.get_monthly_cost("aws", "ec2:instance", {"instance_type": "m5.large"})
        assert cost1 == cost2

    def test_delegates_to_backend(self):
        backend = LocalPricingService()
        cached = CachedPricingService(backend)
        types = cached.list_supported_resource_types("aws")
        assert "ec2:instance" in types
