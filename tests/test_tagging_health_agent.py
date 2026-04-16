"""Tests for the TaggingHealthAgent and tagging policy engine."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from agents.tagging_health_agent import TaggingHealthAgent
from core.audit import AuditLogger
from core.models import CloudProvider, ResourceCreationEvent, Severity
from core.tagging import (
    TAGGABILITY_REGISTRY,
    TagComplianceStatus,
    TaggabilityClass,
    TaggingPolicy,
    TaggingPolicyEngine,
    aggregate_audits,
    build_resource_audit,
    is_resource_taggable,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_event(
    *,
    provider: CloudProvider = CloudProvider.AWS,
    resource_type: str = "ec2:instance",
    resource_id: str = "i-1",
    account_id: str = "111122223333",
    region: str = "eu-west-2",
    tags: dict[str, str] | None = None,
    cost: float = 100.0,
    ts: datetime | None = None,
    creator_email: str = "alice@example.com",
) -> ResourceCreationEvent:
    return ResourceCreationEvent(
        provider=provider,
        timestamp=ts or datetime.now(UTC),
        account_id=account_id,
        region=region,
        resource_type=resource_type,
        resource_id=resource_id,
        resource_name=resource_id,
        creator_identity=f"arn:aws:iam::{account_id}:user/alice",
        creator_email=creator_email,
        estimated_monthly_cost_usd=cost,
        tags=tags or {},
        raw_event={},
    )


@pytest.fixture
def audit(tmp_path: Path) -> AuditLogger:
    return AuditLogger(tmp_path / "audit")


@pytest.fixture
def aws_policy() -> TaggingPolicy:
    return TaggingPolicy(
        policy_id="aws-default",
        name="AWS Default",
        provider=CloudProvider.AWS,
        required_tags=["team", "cost-centre", "environment"],
        recommended_tags=["owner"],
        tag_value_patterns={"cost-centre": r"CC-[0-9]{4}"},
        exempt_resource_types=["s3:object"],
        severity_when_missing=Severity.WARNING,
    )


@pytest.fixture
def gcp_policy() -> TaggingPolicy:
    return TaggingPolicy(
        policy_id="gcp-default",
        name="GCP Default",
        provider=CloudProvider.GCP,
        required_tags=["team", "cost_centre"],
        severity_when_missing=Severity.CRITICAL,
    )


@pytest.fixture
def engine(
    audit: AuditLogger, aws_policy: TaggingPolicy, gcp_policy: TaggingPolicy, tmp_path: Path
) -> TaggingPolicyEngine:
    eng = TaggingPolicyEngine(tmp_path / "tagging-policies", audit)
    eng.register(aws_policy)
    eng.register(gcp_policy)
    return eng


@pytest.fixture
def agent(engine: TaggingPolicyEngine, audit: AuditLogger) -> TaggingHealthAgent:
    return TaggingHealthAgent(policy_engine=engine, audit_logger=audit)


# ---------------------------------------------------------------------------
# Taggability registry
# ---------------------------------------------------------------------------


class TestTaggabilityRegistry:
    def test_known_aws_resource_is_taggable(self):
        assert is_resource_taggable(CloudProvider.AWS, "ec2:instance") == TaggabilityClass.TAGGABLE

    def test_known_gcp_resource_is_taggable(self):
        assert (
            is_resource_taggable(CloudProvider.GCP, "compute.instances")
            == TaggabilityClass.TAGGABLE
        )

    def test_non_taggable_resource_classified(self):
        assert (
            is_resource_taggable(CloudProvider.AWS, "route53:hostedzone-record")
            == TaggabilityClass.NON_TAGGABLE
        )
        assert (
            is_resource_taggable(CloudProvider.GCP, "iam.serviceaccounts")
            == TaggabilityClass.NON_TAGGABLE
        )

    def test_unknown_resource_defaults_to_taggable(self):
        """Unknown types default to taggable so they're evaluated."""
        assert is_resource_taggable(CloudProvider.AWS, "novel:service") == TaggabilityClass.TAGGABLE

    def test_registry_covers_both_providers(self):
        assert CloudProvider.AWS in TAGGABILITY_REGISTRY
        assert CloudProvider.GCP in TAGGABILITY_REGISTRY


# ---------------------------------------------------------------------------
# build_resource_audit — pure evaluation logic
# ---------------------------------------------------------------------------


class TestBuildResourceAudit:
    def test_compliant_resource(self, aws_policy: TaggingPolicy):
        event = _make_event(
            tags={"team": "platform", "cost-centre": "CC-1234", "environment": "prod"}
        )
        audit = build_resource_audit(event, aws_policy)
        assert audit.status == TagComplianceStatus.COMPLIANT
        assert audit.missing_required_tags == []
        assert audit.invalid_tag_values == {}
        assert audit.severity == Severity.INFO
        assert not audit.is_violation

    def test_missing_required_tag(self, aws_policy: TaggingPolicy):
        event = _make_event(tags={"team": "platform"})
        audit = build_resource_audit(event, aws_policy)
        assert audit.status == TagComplianceStatus.NON_COMPLIANT
        assert set(audit.missing_required_tags) == {"cost-centre", "environment"}
        assert audit.severity == Severity.WARNING
        assert audit.is_violation

    def test_invalid_tag_value_pattern(self, aws_policy: TaggingPolicy):
        event = _make_event(tags={"team": "p", "cost-centre": "bogus", "environment": "prod"})
        audit = build_resource_audit(event, aws_policy)
        assert audit.status == TagComplianceStatus.NON_COMPLIANT
        assert audit.invalid_tag_values == {"cost-centre": "bogus"}

    def test_exempt_resource_type(self, aws_policy: TaggingPolicy):
        event = _make_event(resource_type="s3:object", tags={})
        # Even with policy present, exempt types should resolve as exempt
        # (The engine resolve() filters exempt types out; here we pass None.)
        audit = build_resource_audit(event, None)
        # s3:object is PARTIAL in registry, so without a policy it's EXEMPT
        assert audit.status == TagComplianceStatus.EXEMPT

    def test_non_taggable_resource_not_flagged(self):
        event = _make_event(resource_type="route53:hostedzone-record", tags={})
        audit = build_resource_audit(event, None)
        assert audit.status == TagComplianceStatus.NON_TAGGABLE
        assert audit.taggability == TaggabilityClass.NON_TAGGABLE
        assert audit.severity == Severity.INFO

    def test_no_policy_means_exempt(self):
        event = _make_event(resource_type="ec2:instance", tags={})
        audit = build_resource_audit(event, None)
        assert audit.status == TagComplianceStatus.EXEMPT

    def test_recommended_tags_tracked_but_not_a_violation(self, aws_policy: TaggingPolicy):
        event = _make_event(
            tags={"team": "platform", "cost-centre": "CC-1234", "environment": "prod"}
        )
        audit = build_resource_audit(event, aws_policy)
        assert audit.status == TagComplianceStatus.COMPLIANT
        assert audit.missing_recommended_tags == ["owner"]


# ---------------------------------------------------------------------------
# TaggingPolicyEngine
# ---------------------------------------------------------------------------


class TestTaggingPolicyEngine:
    def test_resolve_matches_provider(self, engine: TaggingPolicyEngine, aws_policy: TaggingPolicy):
        event = _make_event(provider=CloudProvider.AWS)
        resolved = engine.resolve(event)
        assert resolved is not None
        assert resolved.policy_id == aws_policy.policy_id

    def test_resolve_picks_resource_scoped_first(
        self, engine: TaggingPolicyEngine, audit: AuditLogger
    ):
        scoped = TaggingPolicy(
            policy_id="aws-ec2",
            name="AWS EC2",
            provider=CloudProvider.AWS,
            resource_types=["ec2:instance"],
            required_tags=["owner"],
        )
        engine.register(scoped)
        event = _make_event(resource_type="ec2:instance")
        resolved = engine.resolve(event)
        assert resolved is not None
        assert resolved.policy_id == "aws-ec2"

    def test_resolve_skips_exempt_types(
        self, engine: TaggingPolicyEngine, aws_policy: TaggingPolicy
    ):
        event = _make_event(resource_type="s3:object")
        resolved = engine.resolve(event)
        assert resolved is None

    def test_resolve_returns_none_when_no_match(self, engine: TaggingPolicyEngine):
        event = _make_event(provider=CloudProvider.AZURE)
        assert engine.resolve(event) is None

    def test_load_policies_from_disk(self, audit: AuditLogger, tmp_path: Path):
        pdir = tmp_path / "tagging"
        pdir.mkdir()
        (pdir / "aws.json").write_text(
            json.dumps(
                {
                    "policy_id": "p1",
                    "name": "AWS",
                    "provider": "aws",
                    "required_tags": ["team"],
                }
            )
        )
        eng = TaggingPolicyEngine(pdir, audit)
        count = eng.load_policies()
        assert count == 1
        assert eng.get_policies()[0].policy_id == "p1"

    def test_load_policies_skips_corrupt(self, audit: AuditLogger, tmp_path: Path):
        pdir = tmp_path / "tagging"
        pdir.mkdir()
        (pdir / "bad.json").write_text("{not json")
        (pdir / "ok.json").write_text(
            json.dumps({"policy_id": "p1", "name": "ok", "provider": "aws"})
        )
        eng = TaggingPolicyEngine(pdir, audit)
        count = eng.load_policies()
        assert count == 1

    def test_path_traversal_blocked(self, audit: AuditLogger):
        with pytest.raises(ValueError):
            TaggingPolicyEngine("../evil", audit)

    def test_get_policies_filters_by_provider(self, engine: TaggingPolicyEngine):
        aws_only = engine.get_policies(provider=CloudProvider.AWS)
        assert len(aws_only) == 1
        assert aws_only[0].provider == CloudProvider.AWS


# ---------------------------------------------------------------------------
# aggregate_audits
# ---------------------------------------------------------------------------


class TestAggregateAudits:
    def test_empty_input_yields_100_percent(self):
        agg = aggregate_audits([])
        assert agg["total"] == 0
        assert agg["compliance_rate_pct"] == 100.0

    def test_mixed_statuses(self, aws_policy: TaggingPolicy):
        events = [
            _make_event(
                resource_id="good",
                tags={"team": "p", "cost-centre": "CC-1234", "environment": "prod"},
            ),
            _make_event(resource_id="bad", tags={}),
            _make_event(resource_id="nt", resource_type="route53:hostedzone-record"),
        ]
        audits = [
            build_resource_audit(events[0], aws_policy),
            build_resource_audit(events[1], aws_policy),
            build_resource_audit(events[2], None),
        ]
        agg = aggregate_audits(audits)
        assert agg["compliant"] == 1
        assert agg["non_compliant"] == 1
        assert agg["non_taggable"] == 1
        # rate computed over compliant + non_compliant only (non-taggable excluded)
        assert agg["compliance_rate_pct"] == 50.0
        assert agg["missing_tag_counts"]["team"] == 1
        assert agg["non_compliance_by_account"]["111122223333"] == 1


# ---------------------------------------------------------------------------
# TaggingHealthAgent
# ---------------------------------------------------------------------------


class TestTaggingHealthAgent:
    def test_scan_filters_by_provider(self, agent: TaggingHealthAgent):
        events = [
            _make_event(provider=CloudProvider.AWS),
            _make_event(provider=CloudProvider.GCP, resource_type="compute.instances"),
        ]
        audits = agent.scan(events, provider=CloudProvider.AWS)
        assert len(audits) == 1
        assert audits[0].provider == CloudProvider.AWS

    def test_scan_writes_audit_entry(self, agent: TaggingHealthAgent, audit: AuditLogger):
        agent.scan([_make_event()])
        entries = audit.get_entries(action="tagging.scan_completed")
        assert len(entries) == 1
        assert entries[0].details["evaluated"] == 1

    def test_weekly_report_filters_period(self, agent: TaggingHealthAgent):
        now = datetime.now(UTC)
        old = _make_event(resource_id="old", ts=now - timedelta(days=30))
        new = _make_event(resource_id="new", ts=now - timedelta(days=1))
        report = agent.generate_weekly_report([old, new], period_end=now)
        # Only the fresh event should be evaluated
        assert report.total_resources == 1

    def test_weekly_report_unattributed_cost(self, agent: TaggingHealthAgent):
        now = datetime.now(UTC)
        events = [
            _make_event(resource_id="bad1", cost=250.0, tags={}, ts=now),
            _make_event(resource_id="bad2", cost=750.0, tags={}, ts=now),
        ]
        report = agent.generate_weekly_report(events, period_end=now)
        assert report.non_compliant == 2
        assert report.unattributed_monthly_cost_usd == 1000.0

    def test_remediation_priorities_ranked_by_cost(self, agent: TaggingHealthAgent):
        now = datetime.now(UTC)
        events = [
            _make_event(resource_id="cheap", cost=10.0, tags={}, ts=now),
            _make_event(resource_id="pricey", cost=5000.0, tags={}, ts=now),
        ]
        report = agent.generate_weekly_report(events, period_end=now)
        assert report.remediation_priorities[0]["resource_id"] == "pricey"
        assert report.remediation_priorities[1]["resource_id"] == "cheap"

    def test_remediation_priority_includes_owner(self, agent: TaggingHealthAgent):
        now = datetime.now(UTC)
        events = [_make_event(creator_email="bob@example.com", tags={}, ts=now)]
        report = agent.generate_weekly_report(events, period_end=now)
        assert report.remediation_priorities[0]["owner"] == "bob@example.com"

    def test_trend_against_previous_report(self, agent: TaggingHealthAgent):
        now = datetime.now(UTC)
        # First report: 100% compliant
        compliant = _make_event(
            ts=now,
            tags={"team": "p", "cost-centre": "CC-1234", "environment": "prod"},
        )
        first = agent.generate_weekly_report([compliant], period_end=now)
        assert first.compliance_rate_pct == 100.0

        # Second report: 0% compliant (trend should be -100 pp)
        later = now + timedelta(days=7)
        non_compliant = _make_event(ts=later, tags={})
        second = agent.generate_weekly_report([non_compliant], period_end=later)
        assert second.trend_vs_previous_period_pct == -100.0

    def test_recommendations_mention_low_compliance(self, agent: TaggingHealthAgent):
        now = datetime.now(UTC)
        events = [_make_event(resource_id=f"r{i}", tags={}, ts=now) for i in range(5)]
        report = agent.generate_weekly_report(events, period_end=now)
        assert any("below 80%" in r for r in report.recommendations)

    def test_recommendations_mention_untagged_team(self, agent: TaggingHealthAgent):
        now = datetime.now(UTC)
        events = [_make_event(tags={}, ts=now)]
        report = agent.generate_weekly_report(events, period_end=now)
        assert any("no team tag" in r for r in report.recommendations)

    def test_recommendations_all_compliant(self, agent: TaggingHealthAgent):
        now = datetime.now(UTC)
        events = [
            _make_event(
                tags={"team": "p", "cost-centre": "CC-1234", "environment": "prod"},
                ts=now,
            )
        ]
        report = agent.generate_weekly_report(events, period_end=now)
        assert any("tag-compliant" in r for r in report.recommendations)

    def test_format_report_for_humans(self, agent: TaggingHealthAgent):
        now = datetime.now(UTC)
        events = [_make_event(tags={}, ts=now)]
        report = agent.generate_weekly_report(events, period_end=now)
        text = agent.format_report_for_humans(report)
        assert "FINOPS TAGGING HEALTH REPORT" in text
        assert "Compliance Rate" in text
        assert report.report_id in text

    def test_report_history_capped(self, agent: TaggingHealthAgent):
        from agents.tagging_health_agent import MAX_REPORT_HISTORY

        for i in range(MAX_REPORT_HISTORY + 5):
            agent.generate_weekly_report(
                [_make_event(resource_id=f"r{i}")],
                period_end=datetime.now(UTC) + timedelta(days=i),
            )
        assert len(agent.get_report_history(limit=10_000)) == MAX_REPORT_HISTORY

    def test_invalid_window_days_rejected(self, engine: TaggingPolicyEngine, audit: AuditLogger):
        with pytest.raises(ValueError):
            TaggingHealthAgent(policy_engine=engine, audit_logger=audit, report_window_days=0)

    def test_non_taggable_resources_never_violate(self, agent: TaggingHealthAgent):
        now = datetime.now(UTC)
        events = [
            _make_event(resource_type="route53:hostedzone-record", tags={}, ts=now),
            _make_event(
                provider=CloudProvider.GCP,
                resource_type="iam.serviceaccounts",
                tags={},
                ts=now,
            ),
        ]
        report = agent.generate_weekly_report(events, period_end=now)
        assert report.non_compliant == 0
        assert report.non_taggable == 2

    def test_shipped_policy_files_load(self, audit: AuditLogger):
        """The JSON files shipped under policies/tagging/ must be valid."""
        policies_path = Path(__file__).resolve().parent.parent / "policies" / "tagging"
        eng = TaggingPolicyEngine(policies_path, audit)
        count = eng.load_policies()
        assert count >= 4  # aws-default, gcp-default, aws-ai-ml, gcp-ai-ml
        # Must include one policy per provider
        providers = {p.provider for p in eng.get_policies()}
        assert CloudProvider.AWS in providers
        assert CloudProvider.GCP in providers


# ---------------------------------------------------------------------------
# 2026 resource coverage — registry and shipped policies
# ---------------------------------------------------------------------------


AWS_2026_SERVICES = [
    "bedrock:custom-model",
    "bedrock:agent",
    "bedrock:knowledge-base",
    "bedrock:guardrail",
    "sagemaker:hyperpod-cluster",
    "sagemaker:endpoint",
    "rds:cluster-aurora-dsql",
    "elasticache:serverless-cache",
    "memorydb:cluster",
    "opensearch-serverless:collection",
    "msk-serverless:cluster",
    "emr-serverless:application",
    "s3-tables:table-bucket",
    "s3-vectors:vector-bucket",
    "apprunner:service",
    "eventbridge-scheduler:schedule",
    "q-business:application",
    "vpc-lattice:service",
]

GCP_2026_SERVICES = [
    "aiplatform.endpoints",
    "aiplatform.gemini-tuning-jobs",
    "aiplatform.agents",
    "aiplatform.vector-indexes",
    "discoveryengine.datastores",
    "alloydb.clusters",
    "alloydb.instances",
    "memorystore.valkey-instances",
    "bigquery.reservations",
    "bigquery-omni.connections",
    "biglake.catalogs",
    "dataproc-serverless.batches",
    "run.jobs",
    "run.workers",
    "artifactregistry.repositories",
    "compute.tpu-vms",
    "netapp.volumes",
    "backupdr.backup-vaults",
]


class TestCurrent2026Coverage:
    """Guards that the registry and shipped policies keep pace with 2026 services."""

    @pytest.mark.parametrize("resource_type", AWS_2026_SERVICES)
    def test_aws_2026_resources_registered_taggable(self, resource_type: str):
        assert (
            is_resource_taggable(CloudProvider.AWS, resource_type) == TaggabilityClass.TAGGABLE
        ), f"{resource_type} should be classified TAGGABLE in 2026 registry"

    @pytest.mark.parametrize("resource_type", GCP_2026_SERVICES)
    def test_gcp_2026_resources_registered_taggable(self, resource_type: str):
        assert (
            is_resource_taggable(CloudProvider.GCP, resource_type) == TaggabilityClass.TAGGABLE
        ), f"{resource_type} should be classified TAGGABLE in 2026 registry"

    def test_registry_size_thresholds(self):
        """Rough lower bounds so the registry doesn't regress silently."""
        assert len(TAGGABILITY_REGISTRY[CloudProvider.AWS]) >= 80
        assert len(TAGGABILITY_REGISTRY[CloudProvider.GCP]) >= 60

    def test_known_non_taggable_still_filtered(self):
        # These remain non-taggable by provider design
        assert (
            is_resource_taggable(CloudProvider.AWS, "support:case") == TaggabilityClass.NON_TAGGABLE
        )
        assert (
            is_resource_taggable(CloudProvider.GCP, "billing.accounts")
            == TaggabilityClass.NON_TAGGABLE
        )

    def test_default_aws_policy_enforces_2026_tag_set(self, audit: AuditLogger):
        policies_path = Path(__file__).resolve().parent.parent / "policies" / "tagging"
        eng = TaggingPolicyEngine(policies_path, audit)
        eng.load_policies()
        aws_policies = eng.get_policies(provider=CloudProvider.AWS)
        default = next(p for p in aws_policies if not p.resource_types)
        for tag in (
            "team",
            "cost-centre",
            "environment",
            "owner",
            "application",
            "managed-by",
            "data-classification",
        ):
            assert tag in default.required_tags, f"AWS default must require {tag}"

    def test_default_gcp_policy_enforces_2026_label_set(self, audit: AuditLogger):
        policies_path = Path(__file__).resolve().parent.parent / "policies" / "tagging"
        eng = TaggingPolicyEngine(policies_path, audit)
        eng.load_policies()
        gcp_policies = eng.get_policies(provider=CloudProvider.GCP)
        default = next(p for p in gcp_policies if not p.resource_types)
        for tag in (
            "team",
            "cost_centre",
            "environment",
            "owner",
            "application",
            "managed_by",
            "data_classification",
        ):
            assert tag in default.required_tags, f"GCP default must require {tag}"

    def test_ai_ml_policy_resolves_for_bedrock_resources(self, audit: AuditLogger):
        """Bedrock resources must match the AI/ML policy, not the generic default."""
        policies_path = Path(__file__).resolve().parent.parent / "policies" / "tagging"
        eng = TaggingPolicyEngine(policies_path, audit)
        eng.load_policies()

        event = _make_event(
            provider=CloudProvider.AWS,
            resource_type="bedrock:agent",
            tags={},
        )
        resolved = eng.resolve(event)
        assert resolved is not None
        assert "model-family" in resolved.required_tags
        assert resolved.severity_when_missing == Severity.CRITICAL

    def test_ai_ml_policy_resolves_for_vertex_resources(self, audit: AuditLogger):
        policies_path = Path(__file__).resolve().parent.parent / "policies" / "tagging"
        eng = TaggingPolicyEngine(policies_path, audit)
        eng.load_policies()

        event = _make_event(
            provider=CloudProvider.GCP,
            resource_type="aiplatform.endpoints",
            tags={},
        )
        resolved = eng.resolve(event)
        assert resolved is not None
        assert "model_family" in resolved.required_tags
        assert "workload_class" in resolved.required_tags

    def test_ai_ml_policy_flags_missing_model_family(self, audit: AuditLogger):
        """A SageMaker endpoint with baseline tags but no model-family must fail."""
        policies_path = Path(__file__).resolve().parent.parent / "policies" / "tagging"
        eng = TaggingPolicyEngine(policies_path, audit)
        eng.load_policies()

        event = _make_event(
            resource_type="sagemaker:endpoint",
            tags={
                "team": "ml-platform",
                "cost-centre": "CC-1234",
                "environment": "prod",
                "owner": "alice@example.com",
                "application": "product-recs",
                "managed-by": "terraform",
                "data-classification": "internal",
            },
        )
        resolved = eng.resolve(event)
        assert resolved is not None
        audit_result = build_resource_audit(event, resolved)
        assert audit_result.status == TagComplianceStatus.NON_COMPLIANT
        assert "model-family" in audit_result.missing_required_tags
        assert audit_result.severity == Severity.CRITICAL
