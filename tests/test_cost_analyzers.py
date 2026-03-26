"""Tests for AWS and GCP cost analyzers."""

from providers.aws.cost_analyzer import AWSCostAnalyzer
from providers.gcp.cost_analyzer import GCPCostAnalyzer


class TestAWSCostAnalyzer:
    def setup_method(self):
        self.analyzer = AWSCostAnalyzer()

    def test_ec2_estimate(self):
        cost = self.analyzer.estimate("ec2:instance", {"instance_type": "m5.large"})
        assert cost > 0
        assert cost == 96.36

    def test_ec2_multiple_instances(self):
        cost = self.analyzer.estimate("ec2:instance", {"instance_type": "m5.large", "count": 3})
        assert cost == 96.36 * 3

    def test_rds_estimate(self):
        cost = self.analyzer.estimate(
            "rds:db", {"instance_class": "db.m5.large", "storage_gb": 100}
        )
        assert cost > 138.0  # base + storage

    def test_rds_multi_az(self):
        single = self.analyzer.estimate(
            "rds:db", {"instance_class": "db.m5.large", "multi_az": False}
        )
        multi = self.analyzer.estimate(
            "rds:db", {"instance_class": "db.m5.large", "multi_az": True}
        )
        assert multi > single

    def test_eks_estimate(self):
        cost = self.analyzer.estimate("eks:cluster", {"node_count": 3})
        assert cost > 73.0  # Control plane + nodes

    def test_nat_gateway_estimate(self):
        cost = self.analyzer.estimate("nat_gateway", {})
        assert cost > 0

    def test_unknown_resource(self):
        cost = self.analyzer.estimate("unknown:thing", {})
        assert cost == 0.0


class TestGCPCostAnalyzer:
    def setup_method(self):
        self.analyzer = GCPCostAnalyzer()

    def test_compute_estimate(self):
        cost = self.analyzer.estimate("compute.instances", {"machine_type": "n2-standard-2"})
        assert cost > 0
        assert cost == 80.30

    def test_cloudsql_estimate(self):
        cost = self.analyzer.estimate("cloudsql.instances", {"tier": "db-custom-2-7680"})
        assert cost > 100.0

    def test_cloudsql_ha(self):
        zonal = self.analyzer.estimate(
            "cloudsql.instances",
            {"tier": "db-custom-2-7680", "availability_type": "ZONAL"},
        )
        regional = self.analyzer.estimate(
            "cloudsql.instances",
            {"tier": "db-custom-2-7680", "availability_type": "REGIONAL"},
        )
        assert regional > zonal

    def test_gke_estimate(self):
        cost = self.analyzer.estimate("container.clusters", {"node_count": 3})
        assert cost > 73.0

    def test_static_ip(self):
        cost = self.analyzer.estimate("compute.addresses", {"count": 1})
        assert cost == 7.30

    def test_unknown_resource(self):
        cost = self.analyzer.estimate("unknown:thing", {})
        assert cost == 0.0
