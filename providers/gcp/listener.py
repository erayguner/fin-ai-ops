"""GCP Cloud Audit Log listener for resource creation monitoring.

Monitors GCP Audit Logs via Cloud Logging for resource creation events
(compute.instances.insert, cloudsql.instances.create, etc.) and translates
them into the hub's standardised ResourceCreationEvent model.

Authentication: Uses Workload Identity Federation or Application Default
Credentials. No service account keys permitted (aligned with SPEC.md).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from core.models import CloudProvider, ResourceCreationEvent

from providers.base import BaseCloudProvider

from .cost_analyzer import GCPCostAnalyzer

logger = logging.getLogger(__name__)

__all__ = ["CREATION_METHODS", "GCPEventListener"]

# GCP audit log method names that indicate resource creation
CREATION_METHODS: dict[str, str] = {
    "v1.compute.instances.insert": "compute.instances",
    "v1.compute.disks.insert": "compute.disks",
    "cloudsql.instances.create": "cloudsql.instances",
    "google.container.v1.ClusterManager.CreateCluster": "container.clusters",
    "storage.buckets.create": "storage.buckets",
    "google.cloud.functions.v2.FunctionService.CreateFunction": "cloudfunctions.functions",
    "google.cloud.bigquery.v2.DatasetService.InsertDataset": "bigquery.datasets",
    "google.cloud.redis.v1.CloudRedis.CreateInstance": "redis.instances",
    "google.cloud.memcache.v1.CloudMemcache.CreateInstance": "memcache.instances",
    "google.pubsub.v1.Subscriber.CreateSubscription": "pubsub.subscriptions",
    "v1.compute.addresses.insert": "compute.addresses",
}


class GCPEventListener(BaseCloudProvider):
    """Listens for GCP resource creation events via Cloud Audit Logs."""

    def __init__(self) -> None:
        self._cost_analyzer = GCPCostAnalyzer()
        self._logging_client: Any = None

    @property
    def provider_name(self) -> CloudProvider:
        return CloudProvider.GCP

    def _get_logging_client(self) -> Any:
        """Get a Cloud Logging client. Uses ADC/WIF, never service account keys."""
        if self._logging_client is None:
            try:
                from google.cloud import logging as cloud_logging

                self._logging_client = cloud_logging.Client()
            except ImportError:
                logger.warning(
                    "google-cloud-logging not installed; GCP operations will be simulated"
                )
                return None
        return self._logging_client

    def listen_for_events(self, config: dict[str, Any]) -> list[ResourceCreationEvent]:
        """Poll Cloud Audit Logs for recent resource creation events.

        Config options:
            project_ids: list[str] - GCP project IDs to monitor
            lookback_minutes: int - How far back to look (default 15)
        """
        client = self._get_logging_client()
        if client is None:
            logger.info("No GCP logging client available; returning empty event list")
            return []

        project_ids = config.get("project_ids", [])
        lookback_minutes = config.get("lookback_minutes", 15)
        events: list[ResourceCreationEvent] = []

        for project_id in project_ids:
            try:
                raw_events = self._query_audit_logs(client, project_id, lookback_minutes)
                for raw_event in raw_events:
                    event = self._translate_event(raw_event, project_id)
                    if event:
                        events.append(event)
            except Exception:
                logger.exception("Failed to query audit logs for project %s", project_id)

        return events

    def estimate_monthly_cost(self, resource_type: str, resource_config: dict[str, Any]) -> float:
        return self._cost_analyzer.estimate(resource_type, resource_config)

    def get_resource_tags(self, resource_id: str, resource_type: str) -> dict[str, str]:
        """Get labels for a GCP resource."""
        # Would use Resource Manager API or service-specific APIs
        return {}

    def get_creator_identity(self, event_data: dict[str, Any]) -> tuple[str, str]:
        auth_info = event_data.get("protoPayload", {}).get("authenticationInfo", {})
        principal = auth_info.get("principalEmail", "unknown")
        return principal, principal

    def get_resource_details(self, resource_id: str, resource_type: str) -> dict[str, Any]:
        return {"resource_id": resource_id, "resource_type": resource_type}

    def validate_credentials(self) -> bool:
        client = self._get_logging_client()
        if client is None:
            return False
        try:
            # Attempt a simple API call to validate credentials
            list(client.list_entries(max_results=1))
            return True
        except Exception:
            return False

    def _query_audit_logs(
        self, client: Any, project_id: str, lookback_minutes: int
    ) -> list[dict[str, Any]]:
        """Query Cloud Audit Logs for resource creation events.

        Filters on admin-activity logs and the configured creation method
        names. ``resource.type`` is service-specific (``gce_instance`` for
        Compute, ``gce_disk`` for disks, etc.), so we constrain by method
        name + log stream rather than resource type.
        Timestamps must be RFC 3339 — Cloud Logging does not support the
        ``"15m ago"`` relative-time shorthand.
        """
        method_filter = " OR ".join(
            f'protoPayload.methodName="{method}"' for method in CREATION_METHODS
        )
        since = (datetime.now(UTC) - timedelta(minutes=lookback_minutes)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        log_filter = (
            'logName:"cloudaudit.googleapis.com%2Factivity" AND '
            f"({method_filter}) AND "
            f'timestamp>="{since}"'
        )

        entries: list[dict[str, Any]] = []
        try:
            for entry in client.list_entries(
                filter_=log_filter,
                resource_names=[f"projects/{project_id}"],
                max_results=100,
            ):
                entries.append(entry.to_api_repr())
        except Exception:
            logger.exception("Failed to query audit logs for %s", project_id)

        return entries

    def _translate_event(
        self, raw_event: dict[str, Any], project_id: str
    ) -> ResourceCreationEvent | None:
        """Translate a GCP audit log entry into a ResourceCreationEvent."""
        proto_payload = raw_event.get("protoPayload", {})
        method_name = proto_payload.get("methodName", "")
        resource_type = CREATION_METHODS.get(method_name)

        if not resource_type:
            return None

        principal, email = self.get_creator_identity(raw_event)
        resource_name = proto_payload.get("resourceName", "unknown")
        resource_id = resource_name.split("/")[-1] if "/" in resource_name else resource_name

        resource_config = self._extract_resource_config(raw_event, resource_type)
        estimated_cost = self._cost_analyzer.estimate(resource_type, resource_config)

        region = self._extract_region(raw_event)

        return ResourceCreationEvent(
            provider=CloudProvider.GCP,
            timestamp=datetime.fromisoformat(
                raw_event.get("timestamp", datetime.now(UTC).isoformat())
            ),
            account_id=project_id,
            region=region,
            resource_type=resource_type,
            resource_id=resource_id,
            resource_name=resource_name,
            creator_identity=principal,
            creator_email=email,
            estimated_monthly_cost_usd=estimated_cost,
            raw_event=raw_event,
        )

    def _extract_region(self, raw_event: dict[str, Any]) -> str:
        """Extract the region/zone from a GCP audit log entry."""
        resource = raw_event.get("resource", {})
        labels = resource.get("labels", {})
        return labels.get("zone", labels.get("location", "europe-west2"))

    def _extract_resource_config(
        self, raw_event: dict[str, Any], resource_type: str
    ) -> dict[str, Any]:
        """Extract cost-relevant config from a GCP audit log entry."""
        config: dict[str, Any] = {}
        proto_payload = raw_event.get("protoPayload", {})
        request = proto_payload.get("request", {})

        if resource_type == "compute.instances":
            config["machine_type"] = request.get("machineType", "n2-standard-2")
            # Extract from the machine type URL
            if "/" in config["machine_type"]:
                config["machine_type"] = config["machine_type"].split("/")[-1]
        elif resource_type == "cloudsql.instances":
            settings = request.get("settings", {})
            config["tier"] = settings.get("tier", "db-custom-2-7680")
            config["availability_type"] = settings.get("availabilityType", "ZONAL")
        elif resource_type == "container.clusters":
            node_pools = request.get("nodePools", [{}])
            if node_pools:
                config["node_count"] = node_pools[0].get("initialNodeCount", 3)
                node_config = node_pools[0].get("config", {})
                config["machine_type"] = node_config.get("machineType", "n2-standard-2")

        return config
