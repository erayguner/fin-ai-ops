"""GCP resource catalogue for cost classification.

Maps GCP resource types to their cost characteristics, helping the
hub understand which resources are most likely to generate significant costs.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["GCP_RESOURCE_CATALOGUE", "GCPResourceInfo"]


@dataclass(frozen=True)
class GCPResourceInfo:
    """Metadata about a GCP resource type for cost analysis."""

    resource_type: str
    service: str
    display_name: str
    cost_driver: str
    typical_monthly_range_usd: tuple[float, float]
    requires_approval_default: bool


GCP_RESOURCE_CATALOGUE: dict[str, GCPResourceInfo] = {
    "compute.instances": GCPResourceInfo(
        resource_type="compute.instances",
        service="Compute Engine",
        display_name="Compute Engine VM",
        cost_driver="Machine type x hours running",
        typical_monthly_range_usd=(7.0, 22000.0),
        requires_approval_default=False,
    ),
    "cloudsql.instances": GCPResourceInfo(
        resource_type="cloudsql.instances",
        service="Cloud SQL",
        display_name="Cloud SQL Database",
        cost_driver="Tier x hours + storage",
        typical_monthly_range_usd=(9.0, 12000.0),
        requires_approval_default=True,
    ),
    "container.clusters": GCPResourceInfo(
        resource_type="container.clusters",
        service="GKE",
        display_name="GKE Kubernetes Cluster",
        cost_driver="Management fee + node VMs",
        typical_monthly_range_usd=(73.0, 20000.0),
        requires_approval_default=True,
    ),
    "storage.buckets": GCPResourceInfo(
        resource_type="storage.buckets",
        service="Cloud Storage",
        display_name="Cloud Storage Bucket",
        cost_driver="Storage volume + operations + egress",
        typical_monthly_range_usd=(0.0, 5000.0),
        requires_approval_default=False,
    ),
    "cloudfunctions.functions": GCPResourceInfo(
        resource_type="cloudfunctions.functions",
        service="Cloud Functions",
        display_name="Cloud Function",
        cost_driver="Invocations x duration x memory",
        typical_monthly_range_usd=(0.0, 3000.0),
        requires_approval_default=False,
    ),
    "bigquery.datasets": GCPResourceInfo(
        resource_type="bigquery.datasets",
        service="BigQuery",
        display_name="BigQuery Dataset",
        cost_driver="Storage + query processing",
        typical_monthly_range_usd=(0.0, 10000.0),
        requires_approval_default=False,
    ),
    "redis.instances": GCPResourceInfo(
        resource_type="redis.instances",
        service="Memorystore",
        display_name="Memorystore for Redis",
        cost_driver="Capacity GB x hours x tier",
        typical_monthly_range_usd=(35.0, 8000.0),
        requires_approval_default=True,
    ),
    "compute.disks": GCPResourceInfo(
        resource_type="compute.disks",
        service="Compute Engine",
        display_name="Persistent Disk",
        cost_driver="Size GB x disk type",
        typical_monthly_range_usd=(1.0, 2000.0),
        requires_approval_default=False,
    ),
    "compute.addresses": GCPResourceInfo(
        resource_type="compute.addresses",
        service="Compute Engine",
        display_name="Static External IP",
        cost_driver="Hourly when unused",
        typical_monthly_range_usd=(0.0, 50.0),
        requires_approval_default=False,
    ),
    "run.services": GCPResourceInfo(
        resource_type="run.services",
        service="Cloud Run",
        display_name="Cloud Run Service",
        cost_driver="CPU + memory + requests",
        typical_monthly_range_usd=(0.0, 3000.0),
        requires_approval_default=False,
    ),
    "dataflow.jobs": GCPResourceInfo(
        resource_type="dataflow.jobs",
        service="Dataflow",
        display_name="Dataflow Job",
        cost_driver="Worker vCPUs + memory + storage",
        typical_monthly_range_usd=(10.0, 15000.0),
        requires_approval_default=True,
    ),
    "pubsub.topics": GCPResourceInfo(
        resource_type="pubsub.topics",
        service="Pub/Sub",
        display_name="Pub/Sub Topic",
        cost_driver="Data volume ingested + delivered",
        typical_monthly_range_usd=(0.0, 1000.0),
        requires_approval_default=False,
    ),
    "spanner.instances": GCPResourceInfo(
        resource_type="spanner.instances",
        service="Cloud Spanner",
        display_name="Cloud Spanner Instance",
        cost_driver="Node count x hours + storage",
        typical_monthly_range_usd=(657.0, 20000.0),
        requires_approval_default=True,
    ),
}
