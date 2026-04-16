"""Tagging governance domain for the FinOps Automation Hub.

Models and engine dedicated to tagging/labelling health across
AWS accounts and GCP projects. Kept separate from the cost policy
engine so that tagging governance remains a first-class, single-
responsibility concern with provider-specific configuration.

Responsibilities:
  - TaggingPolicy: per-provider required tags/labels with optional
    resource-type scoping, severity, and exemptions.
  - TaggingPolicyEngine: load policies from disk and resolve which
    policy applies to a given event.
  - Taggability registry: classifies resource types as taggable,
    non-taggable, or partial (best-effort) so the agent does not
    flag resources that cannot carry tags.
  - ResourceTagAudit / TaggingHealthReport: aggregate outputs for
    reporting, remediation, and trend analysis.
"""

from __future__ import annotations

import json
import uuid
from collections import defaultdict
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from .audit import AuditLogger
from .models import SCHEMA_VERSION, CloudProvider, ResourceCreationEvent, Severity

__all__ = [
    "TAGGABILITY_REGISTRY",
    "ResourceTagAudit",
    "TagComplianceStatus",
    "TaggabilityClass",
    "TaggingHealthReport",
    "TaggingPolicy",
    "TaggingPolicyEngine",
    "is_resource_taggable",
]


class TagComplianceStatus(StrEnum):
    """Outcome of a tagging compliance evaluation for a single resource."""

    COMPLIANT = "compliant"
    NON_COMPLIANT = "non_compliant"
    NON_TAGGABLE = "non_taggable"
    EXEMPT = "exempt"


class TaggabilityClass(StrEnum):
    """Classification of a resource type's support for tags/labels."""

    TAGGABLE = "taggable"
    NON_TAGGABLE = "non_taggable"
    PARTIAL = "partial"


# Resource-type taggability registry (2026 catalogue).
#
# Conservative: classifies a type as NON_TAGGABLE only when the
# provider genuinely does not expose tags/labels on that resource.
# PARTIAL means tags are possible but commonly not propagated
# (e.g. child resources inheriting parent tags, or tag-per-request
# models that incur API cost).
#
# Coverage reflects AWS and GCP services as of 2026, including
# generative AI (Bedrock, Vertex AI), serverless data (Aurora DSQL,
# BigQuery, AlloyDB), and edge/AI-adjacent compute (SageMaker HyperPod,
# Cloud Run Workers).
TAGGABILITY_REGISTRY: dict[CloudProvider, dict[str, TaggabilityClass]] = {
    CloudProvider.AWS: {
        # --- Compute ---
        "ec2:instance": TaggabilityClass.TAGGABLE,
        "ec2:launch-template": TaggabilityClass.TAGGABLE,
        "ec2:auto-scaling-group": TaggabilityClass.TAGGABLE,
        "ec2:capacity-reservation": TaggabilityClass.TAGGABLE,
        "lambda:function": TaggabilityClass.TAGGABLE,
        "lambda:layer": TaggabilityClass.TAGGABLE,
        "ecs:cluster": TaggabilityClass.TAGGABLE,
        "ecs:service": TaggabilityClass.TAGGABLE,
        "ecs:task-definition": TaggabilityClass.TAGGABLE,
        "eks:cluster": TaggabilityClass.TAGGABLE,
        "eks:nodegroup": TaggabilityClass.TAGGABLE,
        "eks:fargate-profile": TaggabilityClass.TAGGABLE,
        "batch:compute-environment": TaggabilityClass.TAGGABLE,
        "batch:job-queue": TaggabilityClass.TAGGABLE,
        "apprunner:service": TaggabilityClass.TAGGABLE,
        "lightsail:instance": TaggabilityClass.TAGGABLE,
        # --- Containers / build ---
        "ecr:repository": TaggabilityClass.TAGGABLE,
        "ecr-public:repository": TaggabilityClass.TAGGABLE,
        "codebuild:project": TaggabilityClass.TAGGABLE,
        "codepipeline:pipeline": TaggabilityClass.TAGGABLE,
        # --- Databases ---
        "rds:db": TaggabilityClass.TAGGABLE,
        "rds:cluster": TaggabilityClass.TAGGABLE,
        "rds:cluster-aurora-serverless": TaggabilityClass.TAGGABLE,
        "rds:cluster-aurora-dsql": TaggabilityClass.TAGGABLE,
        "dynamodb:table": TaggabilityClass.TAGGABLE,
        "dynamodb:global-table": TaggabilityClass.TAGGABLE,
        "elasticache:cluster": TaggabilityClass.TAGGABLE,
        "elasticache:serverless-cache": TaggabilityClass.TAGGABLE,
        "memorydb:cluster": TaggabilityClass.TAGGABLE,
        "documentdb:cluster": TaggabilityClass.TAGGABLE,
        "neptune:cluster": TaggabilityClass.TAGGABLE,
        "keyspaces:keyspace": TaggabilityClass.TAGGABLE,
        "timestream:database": TaggabilityClass.TAGGABLE,
        "qldb:ledger": TaggabilityClass.TAGGABLE,
        # --- Analytics / data ---
        "redshift:cluster": TaggabilityClass.TAGGABLE,
        "redshift-serverless:workgroup": TaggabilityClass.TAGGABLE,
        "emr:cluster": TaggabilityClass.TAGGABLE,
        "emr-serverless:application": TaggabilityClass.TAGGABLE,
        "glue:job": TaggabilityClass.TAGGABLE,
        "glue:crawler": TaggabilityClass.TAGGABLE,
        "glue:database": TaggabilityClass.TAGGABLE,
        "athena:workgroup": TaggabilityClass.TAGGABLE,
        "msk:cluster": TaggabilityClass.TAGGABLE,
        "msk-serverless:cluster": TaggabilityClass.TAGGABLE,
        "kinesis:stream": TaggabilityClass.TAGGABLE,
        "firehose:delivery-stream": TaggabilityClass.TAGGABLE,
        "kinesis-video:stream": TaggabilityClass.TAGGABLE,
        "opensearch:domain": TaggabilityClass.TAGGABLE,
        "opensearch-serverless:collection": TaggabilityClass.TAGGABLE,
        "quicksight:dataset": TaggabilityClass.TAGGABLE,
        # --- AI / ML (2026) ---
        "bedrock:custom-model": TaggabilityClass.TAGGABLE,
        "bedrock:provisioned-throughput": TaggabilityClass.TAGGABLE,
        "bedrock:agent": TaggabilityClass.TAGGABLE,
        "bedrock:knowledge-base": TaggabilityClass.TAGGABLE,
        "bedrock:guardrail": TaggabilityClass.TAGGABLE,
        "bedrock:flow": TaggabilityClass.TAGGABLE,
        "sagemaker:endpoint": TaggabilityClass.TAGGABLE,
        "sagemaker:training-job": TaggabilityClass.TAGGABLE,
        "sagemaker:notebook-instance": TaggabilityClass.TAGGABLE,
        "sagemaker:pipeline": TaggabilityClass.TAGGABLE,
        "sagemaker:hyperpod-cluster": TaggabilityClass.TAGGABLE,
        "sagemaker:feature-group": TaggabilityClass.TAGGABLE,
        "sagemaker:model-package": TaggabilityClass.TAGGABLE,
        "comprehend:endpoint": TaggabilityClass.TAGGABLE,
        "rekognition:project": TaggabilityClass.TAGGABLE,
        "q-business:application": TaggabilityClass.TAGGABLE,
        # --- Storage ---
        "s3:bucket": TaggabilityClass.TAGGABLE,
        "s3:access-point": TaggabilityClass.TAGGABLE,
        "s3-tables:table-bucket": TaggabilityClass.TAGGABLE,
        "s3-vectors:vector-bucket": TaggabilityClass.TAGGABLE,
        "ebs:volume": TaggabilityClass.TAGGABLE,
        "ebs:snapshot": TaggabilityClass.TAGGABLE,
        "efs:filesystem": TaggabilityClass.TAGGABLE,
        "fsx:filesystem": TaggabilityClass.TAGGABLE,
        "fsx:volume": TaggabilityClass.TAGGABLE,
        "backup:vault": TaggabilityClass.TAGGABLE,
        "backup:plan": TaggabilityClass.TAGGABLE,
        # --- Networking / edge / delivery ---
        "elb:load-balancer": TaggabilityClass.TAGGABLE,
        "elbv2:load-balancer": TaggabilityClass.TAGGABLE,
        "elbv2:target-group": TaggabilityClass.TAGGABLE,
        "cloudfront:distribution": TaggabilityClass.TAGGABLE,
        "cloudfront:kvs": TaggabilityClass.TAGGABLE,
        "global-accelerator:accelerator": TaggabilityClass.TAGGABLE,
        "ec2:vpc": TaggabilityClass.TAGGABLE,
        "ec2:transit-gateway": TaggabilityClass.TAGGABLE,
        "ec2:nat-gateway": TaggabilityClass.TAGGABLE,
        "ec2:vpc-endpoint": TaggabilityClass.TAGGABLE,
        "directconnect:connection": TaggabilityClass.TAGGABLE,
        "network-firewall:firewall": TaggabilityClass.TAGGABLE,
        "vpc-lattice:service": TaggabilityClass.TAGGABLE,
        # --- Application / integration ---
        "apigateway:rest-api": TaggabilityClass.TAGGABLE,
        "apigatewayv2:http-api": TaggabilityClass.TAGGABLE,
        "apigatewayv2:websocket-api": TaggabilityClass.TAGGABLE,
        "appsync:graphql-api": TaggabilityClass.TAGGABLE,
        "sqs:queue": TaggabilityClass.TAGGABLE,
        "sns:topic": TaggabilityClass.TAGGABLE,
        "eventbridge:event-bus": TaggabilityClass.TAGGABLE,
        "eventbridge:pipe": TaggabilityClass.TAGGABLE,
        "eventbridge-scheduler:schedule": TaggabilityClass.TAGGABLE,
        "stepfunctions:state-machine": TaggabilityClass.TAGGABLE,
        "mq:broker": TaggabilityClass.TAGGABLE,
        # --- Observability ---
        "logs:log-group": TaggabilityClass.TAGGABLE,
        "cloudwatch:alarm": TaggabilityClass.TAGGABLE,
        "cloudwatch:dashboard": TaggabilityClass.TAGGABLE,
        "cloudwatch-rum:app-monitor": TaggabilityClass.TAGGABLE,
        "synthetics:canary": TaggabilityClass.TAGGABLE,
        "xray:group": TaggabilityClass.TAGGABLE,
        # --- Security / identity ---
        "iam:role": TaggabilityClass.TAGGABLE,
        "iam:user": TaggabilityClass.TAGGABLE,
        "kms:key": TaggabilityClass.TAGGABLE,
        "secretsmanager:secret": TaggabilityClass.TAGGABLE,
        "acm:certificate": TaggabilityClass.TAGGABLE,
        "wafv2:web-acl": TaggabilityClass.TAGGABLE,
        "shield:protection": TaggabilityClass.TAGGABLE,
        # --- Non-taggable / partial (provider design) ---
        "s3:object": TaggabilityClass.PARTIAL,
        "iam:policy-version": TaggabilityClass.NON_TAGGABLE,
        "ec2:spot-price": TaggabilityClass.NON_TAGGABLE,
        "route53:hostedzone-record": TaggabilityClass.NON_TAGGABLE,
        "cost-explorer:report": TaggabilityClass.NON_TAGGABLE,
        "support:case": TaggabilityClass.NON_TAGGABLE,
        "trusted-advisor:check": TaggabilityClass.NON_TAGGABLE,
        "health:event": TaggabilityClass.NON_TAGGABLE,
    },
    CloudProvider.GCP: {
        # --- Compute ---
        "compute.instances": TaggabilityClass.TAGGABLE,
        "compute.instance-templates": TaggabilityClass.TAGGABLE,
        "compute.instance-groups": TaggabilityClass.TAGGABLE,
        "compute.tpu-vms": TaggabilityClass.TAGGABLE,
        "compute.reservations": TaggabilityClass.TAGGABLE,
        "container.clusters": TaggabilityClass.TAGGABLE,
        "container.node-pools": TaggabilityClass.TAGGABLE,
        "run.services": TaggabilityClass.TAGGABLE,
        "run.jobs": TaggabilityClass.TAGGABLE,
        "run.workers": TaggabilityClass.TAGGABLE,
        "cloudfunctions.functions": TaggabilityClass.TAGGABLE,
        "batch.jobs": TaggabilityClass.TAGGABLE,
        "appengine.services": TaggabilityClass.TAGGABLE,
        # --- Databases ---
        "cloudsql.instances": TaggabilityClass.TAGGABLE,
        "spanner.instances": TaggabilityClass.TAGGABLE,
        "spanner.databases": TaggabilityClass.TAGGABLE,
        "alloydb.clusters": TaggabilityClass.TAGGABLE,
        "alloydb.instances": TaggabilityClass.TAGGABLE,
        "bigtable.instances": TaggabilityClass.TAGGABLE,
        "firestore.databases": TaggabilityClass.TAGGABLE,
        "redis.instances": TaggabilityClass.TAGGABLE,
        "memorystore.clusters": TaggabilityClass.TAGGABLE,
        "memorystore.valkey-instances": TaggabilityClass.TAGGABLE,
        # --- Analytics / data ---
        "bigquery.datasets": TaggabilityClass.TAGGABLE,
        "bigquery.tables": TaggabilityClass.TAGGABLE,
        "bigquery.reservations": TaggabilityClass.TAGGABLE,
        "bigquery-omni.connections": TaggabilityClass.TAGGABLE,
        "biglake.catalogs": TaggabilityClass.TAGGABLE,
        "dataproc.clusters": TaggabilityClass.TAGGABLE,
        "dataproc-serverless.batches": TaggabilityClass.TAGGABLE,
        "dataflow.jobs": TaggabilityClass.TAGGABLE,
        "dataform.repositories": TaggabilityClass.TAGGABLE,
        "composer.environments": TaggabilityClass.TAGGABLE,
        "pubsub.topics": TaggabilityClass.TAGGABLE,
        "pubsub.subscriptions": TaggabilityClass.TAGGABLE,
        "pubsublite.topics": TaggabilityClass.TAGGABLE,
        "looker.instances": TaggabilityClass.TAGGABLE,
        # --- AI / ML (2026) ---
        "aiplatform.endpoints": TaggabilityClass.TAGGABLE,
        "aiplatform.models": TaggabilityClass.TAGGABLE,
        "aiplatform.pipelines": TaggabilityClass.TAGGABLE,
        "aiplatform.training-jobs": TaggabilityClass.TAGGABLE,
        "aiplatform.feature-stores": TaggabilityClass.TAGGABLE,
        "aiplatform.notebooks": TaggabilityClass.TAGGABLE,
        "aiplatform.vector-indexes": TaggabilityClass.TAGGABLE,
        "aiplatform.agents": TaggabilityClass.TAGGABLE,
        "aiplatform.gemini-tuning-jobs": TaggabilityClass.TAGGABLE,
        "discoveryengine.datastores": TaggabilityClass.TAGGABLE,
        "discoveryengine.engines": TaggabilityClass.TAGGABLE,
        # --- Storage ---
        "storage.buckets": TaggabilityClass.TAGGABLE,
        "compute.disks": TaggabilityClass.TAGGABLE,
        "compute.snapshots": TaggabilityClass.TAGGABLE,
        "compute.images": TaggabilityClass.TAGGABLE,
        "filestore.instances": TaggabilityClass.TAGGABLE,
        "backupdr.backup-vaults": TaggabilityClass.TAGGABLE,
        "netapp.volumes": TaggabilityClass.TAGGABLE,
        # --- Networking / edge ---
        "compute.networks": TaggabilityClass.TAGGABLE,
        "compute.subnetworks": TaggabilityClass.TAGGABLE,
        "compute.forwarding-rules": TaggabilityClass.TAGGABLE,
        "compute.target-https-proxies": TaggabilityClass.TAGGABLE,
        "networkservices.edge-cache-services": TaggabilityClass.TAGGABLE,
        "apigee.environments": TaggabilityClass.TAGGABLE,
        "apigateway.gateways": TaggabilityClass.TAGGABLE,
        # --- Integration / orchestration ---
        "workflows.workflows": TaggabilityClass.TAGGABLE,
        "cloudtasks.queues": TaggabilityClass.TAGGABLE,
        "cloudscheduler.jobs": TaggabilityClass.TAGGABLE,
        "eventarc.triggers": TaggabilityClass.TAGGABLE,
        # --- Build / artifact ---
        "cloudbuild.triggers": TaggabilityClass.TAGGABLE,
        "artifactregistry.repositories": TaggabilityClass.TAGGABLE,
        # --- Security / keys ---
        "cloudkms.key-rings": TaggabilityClass.TAGGABLE,
        "cloudkms.crypto-keys": TaggabilityClass.TAGGABLE,
        "secretmanager.secrets": TaggabilityClass.TAGGABLE,
        "certificatemanager.certificates": TaggabilityClass.TAGGABLE,
        # --- Non-taggable / partial (provider design) ---
        "compute.addresses": TaggabilityClass.PARTIAL,
        "iam.serviceaccounts": TaggabilityClass.NON_TAGGABLE,
        "iam.roles": TaggabilityClass.NON_TAGGABLE,
        "dns.managed-zones-records": TaggabilityClass.NON_TAGGABLE,
        "billing.accounts": TaggabilityClass.NON_TAGGABLE,
        "monitoring.metric-descriptors": TaggabilityClass.NON_TAGGABLE,
        "logging.log-entries": TaggabilityClass.NON_TAGGABLE,
    },
    CloudProvider.AZURE: {},
}


def is_resource_taggable(
    provider: CloudProvider,
    resource_type: str,
    *,
    default: TaggabilityClass = TaggabilityClass.TAGGABLE,
) -> TaggabilityClass:
    """Look up the taggability classification for a resource type.

    Unknown resource types default to TAGGABLE so new resources are
    evaluated for tag compliance rather than silently skipped.
    """
    return TAGGABILITY_REGISTRY.get(provider, {}).get(resource_type, default)


class TaggingPolicy(BaseModel):
    """Declarative tagging/labelling policy for a single provider.

    A policy is scoped to exactly one provider so that AWS and GCP
    naming conventions and required keys can differ (e.g. ``team``
    vs ``owner_team``, ``cost-centre`` vs ``cost_centre``).
    """

    schema_version: str = Field(default=SCHEMA_VERSION, description="Model schema version")
    policy_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    description: str = ""
    provider: CloudProvider = Field(
        description="Provider this policy applies to (tagging conventions differ)"
    )
    required_tags: list[str] = Field(
        default_factory=list,
        description="Tag/label keys that MUST be present on matching resources",
    )
    recommended_tags: list[str] = Field(
        default_factory=list,
        description="Tag/label keys that SHOULD be present (warning severity)",
    )
    resource_types: list[str] = Field(
        default_factory=list,
        description="Resource types this policy applies to; empty means all taggable",
    )
    exempt_resource_types: list[str] = Field(
        default_factory=list,
        description="Resource types explicitly exempt from this policy",
    )
    tag_value_patterns: dict[str, str] = Field(
        default_factory=dict,
        description="Optional regex patterns for individual tag values (e.g. cost-centre)",
    )
    severity_when_missing: Severity = Field(
        default=Severity.WARNING,
        description="Severity applied when any required tag is missing",
    )
    enabled: bool = True
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ResourceTagAudit(BaseModel):
    """Result of evaluating a single resource against its tagging policy."""

    schema_version: str = Field(default=SCHEMA_VERSION, description="Model schema version")
    evaluated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    provider: CloudProvider
    account_id: str
    region: str
    resource_type: str
    resource_id: str
    resource_name: str = ""
    status: TagComplianceStatus
    taggability: TaggabilityClass
    policy_id: str = ""
    policy_name: str = ""
    missing_required_tags: list[str] = Field(default_factory=list)
    missing_recommended_tags: list[str] = Field(default_factory=list)
    invalid_tag_values: dict[str, str] = Field(
        default_factory=dict,
        description="Tag keys whose values failed the policy pattern (key -> actual value)",
    )
    severity: Severity = Severity.INFO
    creator_identity: str = ""
    creator_email: str = ""
    team: str = ""
    estimated_monthly_cost_usd: float = 0.0
    source_event_id: str = ""

    @property
    def is_violation(self) -> bool:
        return self.status == TagComplianceStatus.NON_COMPLIANT


class TaggingHealthReport(BaseModel):
    """Aggregate tagging health report (default cadence: weekly)."""

    schema_version: str = Field(default=SCHEMA_VERSION, description="Model schema version")
    report_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    period_start: datetime
    period_end: datetime
    provider: CloudProvider | None = None

    total_resources: int = 0
    compliant: int = 0
    non_compliant: int = 0
    non_taggable: int = 0
    exempt: int = 0
    compliance_rate_pct: float = 0.0

    missing_tag_counts: dict[str, int] = Field(
        default_factory=dict,
        description="Count of resources missing each required tag key",
    )
    non_compliance_by_account: dict[str, int] = Field(default_factory=dict)
    non_compliance_by_resource_type: dict[str, int] = Field(default_factory=dict)
    non_compliance_by_team: dict[str, int] = Field(default_factory=dict)
    unattributed_monthly_cost_usd: float = Field(
        default=0.0,
        description="Estimated monthly cost of non-compliant resources (lost attribution)",
    )

    trend_vs_previous_period_pct: float = Field(
        default=0.0,
        description="Change in compliance rate vs the previous reporting period",
    )
    remediation_priorities: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Ranked remediation items with owner, cost, and action",
    )
    recommendations: list[str] = Field(default_factory=list)


class TaggingPolicyEngine:
    """Loads tagging policies and resolves the policy for a given event.

    Tagging policies live in a dedicated directory so they are
    independent of the CostPolicy file set (ADR-001: JSON on disk).
    """

    def __init__(
        self,
        policy_dir: str | Path,
        audit_logger: AuditLogger,
        *,
        base_dir: Path | None = None,
    ) -> None:
        policy_path = Path(policy_dir).resolve()
        if ".." in str(policy_dir):
            raise ValueError("policy_dir: path traversal ('..') is not allowed")
        if base_dir is not None:
            base = base_dir.resolve()
            try:
                policy_path.relative_to(base)
            except ValueError as exc:
                raise ValueError(f"policy_dir must be within {base}, got {policy_path}") from exc
        self._policy_dir = policy_path
        self._policy_dir.mkdir(parents=True, exist_ok=True)
        self._policies: dict[str, TaggingPolicy] = {}
        self._audit = audit_logger

    def load_policies(self) -> int:
        """Load all tagging policies from disk. Returns count loaded."""
        count = 0
        errors = 0
        for policy_file in sorted(self._policy_dir.glob("*.json")):
            try:
                data = json.loads(policy_file.read_text())
                policy = TaggingPolicy(**data)
                self._policies[policy.policy_id] = policy
                count += 1
            except (json.JSONDecodeError, ValueError, TypeError, KeyError) as e:
                errors += 1
                self._audit.log(
                    action="tagging_policy.load_error",
                    actor="system",
                    target=str(policy_file),
                    outcome="failure",
                    details={"error": str(e), "file": policy_file.name},
                )
        self._audit.log(
            action="tagging_policies.loaded",
            actor="system",
            target=str(self._policy_dir),
            details={"count": count, "errors": errors},
        )
        return count

    def register(self, policy: TaggingPolicy) -> None:
        """Register a policy in-memory (does not persist to disk)."""
        self._policies[policy.policy_id] = policy

    def get_policies(
        self,
        provider: CloudProvider | None = None,
        enabled_only: bool = True,
    ) -> list[TaggingPolicy]:
        policies = list(self._policies.values())
        if provider is not None:
            policies = [p for p in policies if p.provider == provider]
        if enabled_only:
            policies = [p for p in policies if p.enabled]
        return policies

    def resolve(self, event: ResourceCreationEvent) -> TaggingPolicy | None:
        """Return the most specific enabled policy matching an event.

        Preference order:
          1. Policies scoped to the exact resource type.
          2. Provider-wide policies (no resource_types list).
        Exempt resource types are skipped entirely.
        """
        candidates = [
            p
            for p in self._policies.values()
            if p.enabled
            and p.provider == event.provider
            and event.resource_type not in p.exempt_resource_types
        ]

        scoped = [p for p in candidates if event.resource_type in p.resource_types]
        if scoped:
            return scoped[0]

        wildcard = [p for p in candidates if not p.resource_types]
        if wildcard:
            return wildcard[0]

        return None


def build_resource_audit(
    event: ResourceCreationEvent,
    policy: TaggingPolicy | None,
) -> ResourceTagAudit:
    """Evaluate a single event against a resolved tagging policy.

    Pure function — no side effects — so callers can unit-test
    compliance logic without constructing an agent.
    """
    import re

    taggability = is_resource_taggable(event.provider, event.resource_type)

    base: dict[str, Any] = {
        "provider": event.provider,
        "account_id": event.account_id,
        "region": event.region,
        "resource_type": event.resource_type,
        "resource_id": event.resource_id,
        "resource_name": event.resource_name,
        "taggability": taggability,
        "creator_identity": event.creator_identity,
        "creator_email": event.creator_email,
        "team": event.tags.get("team", event.tags.get("Team", "")),
        "estimated_monthly_cost_usd": event.estimated_monthly_cost_usd,
        "source_event_id": event.event_id,
    }

    if taggability == TaggabilityClass.NON_TAGGABLE:
        return ResourceTagAudit(
            status=TagComplianceStatus.NON_TAGGABLE,
            severity=Severity.INFO,
            **base,
        )

    if policy is None:
        return ResourceTagAudit(
            status=TagComplianceStatus.EXEMPT,
            severity=Severity.INFO,
            **base,
        )

    missing_required = [t for t in policy.required_tags if t not in event.tags]
    missing_recommended = [t for t in policy.recommended_tags if t not in event.tags]

    invalid_values: dict[str, str] = {}
    for key, pattern in policy.tag_value_patterns.items():
        value = event.tags.get(key)
        if value is not None and not re.fullmatch(pattern, value):
            invalid_values[key] = value

    is_non_compliant = bool(missing_required) or bool(invalid_values)
    status = (
        TagComplianceStatus.NON_COMPLIANT if is_non_compliant else TagComplianceStatus.COMPLIANT
    )
    severity = policy.severity_when_missing if is_non_compliant else Severity.INFO

    return ResourceTagAudit(
        status=status,
        severity=severity,
        policy_id=policy.policy_id,
        policy_name=policy.name,
        missing_required_tags=missing_required,
        missing_recommended_tags=missing_recommended,
        invalid_tag_values=invalid_values,
        **base,
    )


def aggregate_audits(
    audits: list[ResourceTagAudit],
) -> dict[str, Any]:
    """Reduce a list of audits into report-ready aggregates.

    Exposed as a module function so both the agent and ad-hoc
    callers (e.g. the MCP server) can reuse it.
    """
    total = len(audits)
    compliant = sum(1 for a in audits if a.status == TagComplianceStatus.COMPLIANT)
    non_compliant = sum(1 for a in audits if a.status == TagComplianceStatus.NON_COMPLIANT)
    non_taggable = sum(1 for a in audits if a.status == TagComplianceStatus.NON_TAGGABLE)
    exempt = sum(1 for a in audits if a.status == TagComplianceStatus.EXEMPT)

    missing_counts: dict[str, int] = defaultdict(int)
    by_account: dict[str, int] = defaultdict(int)
    by_resource_type: dict[str, int] = defaultdict(int)
    by_team: dict[str, int] = defaultdict(int)
    unattributed_cost = 0.0

    for a in audits:
        if a.status != TagComplianceStatus.NON_COMPLIANT:
            continue
        for tag in a.missing_required_tags:
            missing_counts[tag] += 1
        by_account[a.account_id] += 1
        by_resource_type[a.resource_type] += 1
        team_key = a.team or "Untagged"
        by_team[team_key] += 1
        unattributed_cost += a.estimated_monthly_cost_usd

    denominator = compliant + non_compliant
    compliance_rate = (compliant / denominator * 100) if denominator else 100.0

    return {
        "total": total,
        "compliant": compliant,
        "non_compliant": non_compliant,
        "non_taggable": non_taggable,
        "exempt": exempt,
        "compliance_rate_pct": round(compliance_rate, 2),
        "missing_tag_counts": dict(missing_counts),
        "non_compliance_by_account": dict(by_account),
        "non_compliance_by_resource_type": dict(by_resource_type),
        "non_compliance_by_team": dict(by_team),
        "unattributed_monthly_cost_usd": round(unattributed_cost, 2),
    }
