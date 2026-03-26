"""AWS CloudTrail event listener for resource creation monitoring.

Monitors CloudTrail for resource creation events (RunInstances,
CreateDBInstance, CreateCluster, etc.) and translates them into
the hub's standardised ResourceCreationEvent model.

Authentication: Uses IAM roles (no long-lived keys). Supports
assumed roles for cross-account monitoring via AWS Organizations.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from core.models import CloudProvider, ResourceCreationEvent

from providers.base import BaseCloudProvider

from .cost_analyzer import AWSCostAnalyzer

logger = logging.getLogger(__name__)

__all__ = ["CREATION_EVENTS", "AWSEventListener"]

# CloudTrail event names that indicate resource creation
CREATION_EVENTS: dict[str, str] = {
    "RunInstances": "ec2:instance",
    "CreateDBInstance": "rds:db",
    "CreateDBCluster": "rds:cluster",
    "CreateCluster": "eks:cluster",
    "CreateBucket": "s3:bucket",
    "CreateFunction20150331": "lambda:function",
    "CreateCacheCluster": "elasticache:cluster",
    "CreateReplicationGroup": "elasticache:replication-group",
    "CreateRedshiftCluster": "redshift:cluster",
    "CreateNatGateway": "nat_gateway",
    "CreateLoadBalancer": "elb:load-balancer",
    "CreateVolume": "ebs:volume",
}


class AWSEventListener(BaseCloudProvider):
    """Listens for AWS resource creation events via CloudTrail."""

    def __init__(self) -> None:
        self._cost_analyzer = AWSCostAnalyzer()
        self._boto_session = None

    @property
    def provider_name(self) -> CloudProvider:
        return CloudProvider.AWS

    def _get_session(self) -> Any:
        """Get or create a boto3 session. Uses IAM roles, never static keys."""
        if self._boto_session is None:
            try:
                import boto3

                self._boto_session = boto3.Session()
            except ImportError:
                logger.warning("boto3 not installed; AWS operations will be simulated")
                return None
        return self._boto_session

    def listen_for_events(self, config: dict[str, Any]) -> list[ResourceCreationEvent]:
        """Poll CloudTrail for recent resource creation events.

        Config options:
            regions: list[str] - AWS regions to monitor
            lookback_minutes: int - How far back to look (default 15)
            account_ids: list[str] - AWS account IDs to monitor
        """
        session = self._get_session()
        if session is None:
            logger.info("No AWS session available; returning empty event list")
            return []

        regions = config.get("regions", ["eu-west-2"])
        lookback_minutes = config.get("lookback_minutes", 15)
        events: list[ResourceCreationEvent] = []

        for region in regions:
            try:
                cloudtrail = session.client("cloudtrail", region_name=region)
                raw_events = self._lookup_creation_events(cloudtrail, lookback_minutes)
                for raw_event in raw_events:
                    event = self._translate_event(raw_event, region)
                    if event:
                        events.append(event)
            except Exception:
                logger.exception("Failed to poll CloudTrail in %s", region)

        return events

    def estimate_monthly_cost(self, resource_type: str, resource_config: dict[str, Any]) -> float:
        return self._cost_analyzer.estimate(resource_type, resource_config)

    def get_resource_tags(self, resource_id: str, resource_type: str) -> dict[str, str]:
        session = self._get_session()
        if session is None:
            return {}
        try:
            tagging = session.client("resourcegroupstaggingapi")
            response = tagging.get_resources(
                ResourceARNList=[resource_id],
            )
            for resource in response.get("ResourceTagMappingList", []):
                return {tag["Key"]: tag["Value"] for tag in resource.get("Tags", [])}
        except Exception:
            logger.exception("Failed to get tags for %s", resource_id)
        return {}

    def get_creator_identity(self, event_data: dict[str, Any]) -> tuple[str, str]:
        user_identity = event_data.get("userIdentity", {})
        principal = user_identity.get("arn", user_identity.get("principalId", "unknown"))
        email = user_identity.get("userName", "")
        if not email and "sessionContext" in user_identity:
            email = user_identity["sessionContext"].get("sessionIssuer", {}).get("userName", "")
        return principal, email

    def get_resource_details(self, resource_id: str, resource_type: str) -> dict[str, Any]:
        """Retrieve details for an AWS resource."""
        session = self._get_session()
        if session is None:
            return {"resource_id": resource_id, "resource_type": resource_type}
        # Resource-specific describe calls would go here
        return {"resource_id": resource_id, "resource_type": resource_type}

    def validate_credentials(self) -> bool:
        session = self._get_session()
        if session is None:
            return False
        try:
            sts = session.client("sts")
            sts.get_caller_identity()
            return True
        except Exception:
            return False

    def _lookup_creation_events(
        self, cloudtrail_client: Any, lookback_minutes: int
    ) -> list[dict[str, Any]]:
        """Query CloudTrail for resource creation events."""
        from datetime import timedelta

        start_time = datetime.now(UTC) - timedelta(minutes=lookback_minutes)
        events: list[dict[str, Any]] = []

        for event_name in CREATION_EVENTS:
            try:
                response = cloudtrail_client.lookup_events(
                    LookupAttributes=[{"AttributeKey": "EventName", "AttributeValue": event_name}],
                    StartTime=start_time,
                    MaxResults=50,
                )
                events.extend(response.get("Events", []))
            except Exception:
                logger.exception("Failed to lookup event %s", event_name)

        return events

    def _translate_event(
        self, raw_event: dict[str, Any], region: str
    ) -> ResourceCreationEvent | None:
        """Translate a raw CloudTrail event into a ResourceCreationEvent."""
        event_name = raw_event.get("EventName", "")
        resource_type = CREATION_EVENTS.get(event_name)
        if not resource_type:
            return None

        principal, email = self.get_creator_identity(raw_event)

        resources = raw_event.get("Resources", [])
        resource_id = resources[0]["ResourceName"] if resources else "unknown"

        resource_config = self._extract_resource_config(raw_event, resource_type)
        estimated_cost = self._cost_analyzer.estimate(resource_type, resource_config)

        return ResourceCreationEvent(
            provider=CloudProvider.AWS,
            timestamp=raw_event.get("EventTime", datetime.now(UTC)),
            account_id=raw_event.get("recipientAccountId", "unknown"),
            region=region,
            resource_type=resource_type,
            resource_id=resource_id,
            resource_name=raw_event.get("ResourceName", ""),
            creator_identity=principal,
            creator_email=email,
            estimated_monthly_cost_usd=estimated_cost,
            raw_event=raw_event,
        )

    def _extract_resource_config(
        self, raw_event: dict[str, Any], resource_type: str
    ) -> dict[str, Any]:
        """Extract cost-relevant configuration from a CloudTrail event."""
        config: dict[str, Any] = {}
        request_params = raw_event.get("requestParameters", {})

        if resource_type == "ec2:instance":
            config["instance_type"] = request_params.get("instanceType", "m5.large")
            instances = request_params.get("instancesSet", {}).get("items", [{}])
            config["count"] = len(instances) if instances else 1
        elif resource_type == "rds:db":
            config["instance_class"] = request_params.get("dBInstanceClass", "db.m5.large")
            config["engine"] = request_params.get("engine", "postgres")
            config["multi_az"] = request_params.get("multiAZ", False)
        elif resource_type == "eks:cluster":
            config["node_count"] = 3  # default
        elif resource_type == "nat_gateway":
            config["count"] = 1

        return config
