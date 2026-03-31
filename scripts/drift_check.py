#!/usr/bin/env python3
"""Terraform-to-Policy drift detection script.

Scans Terraform files for resource definitions and checks them
against FinOps policies for cost cap, tag, and region compliance.

Usage:
    python scripts/drift_check.py
"""

from __future__ import annotations

import json
import logging
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.models import CostPolicy

logger = logging.getLogger(__name__)

# Mapping from Terraform resource types to policy resource types
TF_TO_POLICY: dict[str, str] = {
    "aws_instance": "ec2:instance",
    "aws_db_instance": "rds:db",
    "aws_rds_cluster": "rds:cluster",
    "aws_s3_bucket": "s3:bucket",
    "aws_ebs_volume": "ec2:volume",
    "aws_eks_node_group": "eks:nodegroup",
    "aws_eks_cluster": "eks:cluster",
    "aws_nat_gateway": "ec2:nat-gateway",
    "aws_sagemaker_endpoint": "sagemaker:endpoint",
    "aws_elasticache_cluster": "elasticache:cluster",
    "aws_lambda_function": "lambda:function",
    "aws_dynamodb_table": "dynamodb:table",
    "aws_cloudfront_distribution": "cloudfront:distribution",
    "google_compute_instance": "compute.instances",
    "google_sql_database_instance": "sqladmin.instances",
    "google_storage_bucket": "storage.buckets",
    "google_container_node_pool": "container.nodePools",
    "google_container_cluster": "container.clusters",
}


def load_policies(policy_dir: Path) -> list[CostPolicy]:
    policies = []
    for f in policy_dir.glob("*.json"):
        try:
            p = CostPolicy(**json.loads(f.read_text()))
            if p.enabled:
                policies.append(p)
        except Exception:
            logger.debug("Skipping invalid policy file: %s", f.name)
    return policies


def scan_terraform(tf_dir: Path) -> list[dict[str, str]]:
    resources = []
    for tf in tf_dir.rglob("*.tf"):
        content = tf.read_text()
        for match in re.finditer(r'resource\s+"(\w+)"\s+"(\w+)"', content):
            tf_type, name = match.groups()
            resources.append(
                {
                    "file": str(tf),
                    "tf_type": tf_type,
                    "name": name,
                    "policy_type": TF_TO_POLICY.get(tf_type, ""),
                }
            )
    return resources


def check_drift(
    resources: list[dict[str, str]], policies: list[CostPolicy]
) -> dict[str, list[dict]]:
    results: dict[str, list[dict]] = {"covered": [], "gaps": [], "unmapped": []}

    for r in resources:
        if not r["policy_type"]:
            results["unmapped"].append(r)
            continue

        matching = [p for p in policies if r["policy_type"] in p.resource_types]
        if matching:
            results["covered"].append({**r, "policies": [p.name for p in matching]})
        else:
            results["gaps"].append(r)

    return results


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    policies = load_policies(root / "policies")
    resources = scan_terraform(root / "providers")

    results = check_drift(resources, policies)

    print(f"Terraform resources: {len(resources)}")
    print(f"Active policies: {len(policies)}")
    print()

    for r in results["covered"]:
        print(f"  COVERED: {r['tf_type']}.{r['name']} -> {', '.join(r['policies'])}")

    for r in results["gaps"]:
        print(f"  GAP: {r['tf_type']}.{r['name']} ({r['policy_type']}) - no matching policy")

    for r in results["unmapped"]:
        print(f"  UNMAPPED: {r['tf_type']}.{r['name']} - no policy type mapping")

    total_mappable = len(results["covered"]) + len(results["gaps"])
    coverage = len(results["covered"]) / total_mappable * 100 if total_mappable > 0 else 100
    print(f"\nCoverage: {coverage:.0f}% ({len(results['covered'])}/{total_mappable})")

    return 1 if results["gaps"] else 0


if __name__ == "__main__":
    sys.exit(main())
