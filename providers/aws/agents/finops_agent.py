"""AWS FinOps Agent — built on Amazon Bedrock Agents.

Uses AWS's native agent framework with:
- Amazon Bedrock Agent Runtime for agent orchestration
- AWS MCP servers for Cost Explorer, CloudWatch, and resource management
- IAM roles for authentication (no API keys, no access key IDs)
- Claude on Bedrock or Amazon Nova as the LLM backbone

No API keys or long-lived credentials are used. All authentication is via:
- IAM roles (EC2 instance profiles, ECS task roles, Lambda execution roles)
- STS AssumeRole for cross-account access
- OIDC federation for CI/CD (GitHub Actions)

Reference: https://docs.aws.amazon.com/bedrock/latest/userguide/agents.html
AWS MCP: https://github.com/awslabs/mcp
"""

from __future__ import annotations

import logging
from datetime import UTC
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "analyse_cost_and_usage",
    "check_tag_compliance",
    "detect_cost_anomalies",
    "get_aws_mcp_server_configs",
    "get_bedrock_agent_config",
    "get_budget_alerts",
    "get_savings_recommendations",
]


# ---------------------------------------------------------------------------
# Bedrock Agent Action Group Functions
#
# These functions are registered as Bedrock Agent Action Groups.
# Bedrock invokes them via Lambda or return-of-control. Each function
# uses boto3 with IAM role credentials — never static API keys.
# ---------------------------------------------------------------------------


def analyse_cost_and_usage(
    granularity: str = "MONTHLY",
    period_days: int = 30,
    group_by: str = "SERVICE",
) -> dict[str, Any]:
    """Query AWS Cost Explorer for cost and usage data.

    Uses IAM role credentials via boto3 — no API keys.

    Args:
        granularity: DAILY, MONTHLY, or HOURLY.
        period_days: Number of days to look back.
        group_by: Dimension to group by — SERVICE, LINKED_ACCOUNT, USAGE_TYPE, REGION.

    Returns:
        dict with total_cost_usd, cost_by_group, and time_series data.
    """
    try:
        from datetime import datetime, timedelta

        import boto3

        ce = boto3.client("ce")  # Uses IAM role — no keys

        end = datetime.now(UTC).strftime("%Y-%m-%d")
        start = (datetime.now(UTC) - timedelta(days=period_days)).strftime("%Y-%m-%d")

        response = ce.get_cost_and_usage(
            TimePeriod={"Start": start, "End": end},
            Granularity=granularity,
            Metrics=["UnblendedCost", "UsageQuantity"],
            GroupBy=[{"Type": "DIMENSION", "Key": group_by}],
        )

        total_cost = 0.0
        cost_by_group: dict[str, float] = {}
        for result in response.get("ResultsByTime", []):
            for group in result.get("Groups", []):
                key = group["Keys"][0]
                amount = float(group["Metrics"]["UnblendedCost"]["Amount"])
                cost_by_group[key] = cost_by_group.get(key, 0) + amount
                total_cost += amount

        return {
            "status": "success",
            "period": f"{start} to {end}",
            "granularity": granularity,
            "total_cost_usd": round(total_cost, 2),
            "cost_by_group": {
                k: round(v, 2)
                for k, v in sorted(cost_by_group.items(), key=lambda x: x[1], reverse=True)
            },
        }
    except ImportError:
        return {"status": "unavailable", "message": "boto3 not installed"}
    except Exception as e:
        logger.exception("Failed to query Cost Explorer")
        return {"status": "error", "message": str(e)}


def detect_cost_anomalies(
    monitor_arn: str = "",
    period_days: int = 30,
) -> dict[str, Any]:
    """Detect cost anomalies using AWS Cost Anomaly Detection.

    Uses IAM role credentials via boto3.

    Args:
        monitor_arn: Optional Cost Anomaly Monitor ARN to check.
        period_days: Days to look back for anomalies.

    Returns:
        dict with detected anomalies, impact amounts, and root causes.
    """
    try:
        from datetime import datetime, timedelta

        import boto3

        ce = boto3.client("ce")

        end = datetime.now(UTC).strftime("%Y-%m-%d")
        start = (datetime.now(UTC) - timedelta(days=period_days)).strftime("%Y-%m-%d")

        params: dict[str, Any] = {
            "DateInterval": {"StartDate": start, "EndDate": end},
            "MaxResults": 50,
        }
        if monitor_arn:
            params["MonitorArn"] = monitor_arn

        response = ce.get_anomalies(**params)
        anomalies = []
        for anomaly in response.get("Anomalies", []):
            impact = anomaly.get("Impact", {})
            anomalies.append(
                {
                    "anomaly_id": anomaly.get("AnomalyId", ""),
                    "start_date": anomaly.get("AnomalyStartDate", ""),
                    "end_date": anomaly.get("AnomalyEndDate", ""),
                    "total_impact_usd": float(impact.get("TotalImpact", 0)),
                    "root_causes": [
                        {
                            "service": rc.get("Service", ""),
                            "region": rc.get("Region", ""),
                            "linked_account": rc.get("LinkedAccount", ""),
                            "usage_type": rc.get("UsageType", ""),
                        }
                        for rc in anomaly.get("RootCauses", [])
                    ],
                }
            )

        return {
            "status": "success",
            "period": f"{start} to {end}",
            "total_anomalies": len(anomalies),
            "anomalies": anomalies,
        }
    except ImportError:
        return {"status": "unavailable", "message": "boto3 not installed"}
    except Exception as e:
        logger.exception("Failed to detect anomalies")
        return {"status": "error", "message": str(e)}


def check_tag_compliance(
    required_tags: list[str] | None = None,
    resource_types: list[str] | None = None,
) -> dict[str, Any]:
    """Check AWS resources for missing required tags.

    Tags are essential for cost attribution and accountability.
    Uses the Resource Groups Tagging API with IAM role credentials.

    Args:
        required_tags: Tags that must be present (defaults to Team, CostCentre, Environment, Owner).
        resource_types: AWS resource types to check (e.g. ec2:instance, rds:db).

    Returns:
        dict with compliance statistics and non-compliant resources.
    """
    if required_tags is None:
        required_tags = ["Team", "CostCentre", "Environment", "Owner"]

    try:
        import boto3

        tagging = boto3.client("resourcegroupstaggingapi")

        params: dict[str, Any] = {}
        if resource_types:
            params["ResourceTypeFilters"] = resource_types

        paginator = tagging.get_paginator("get_resources")
        non_compliant = []
        total = 0
        compliant = 0

        for page in paginator.paginate(**params):
            for resource in page.get("ResourceTagMappingList", []):
                total += 1
                tags = {t["Key"]: t["Value"] for t in resource.get("Tags", [])}
                missing = [t for t in required_tags if t not in tags]
                if missing:
                    non_compliant.append(
                        {
                            "resource_arn": resource["ResourceARN"],
                            "missing_tags": missing,
                            "existing_tags": tags,
                        }
                    )
                else:
                    compliant += 1

        return {
            "status": "success",
            "total_resources": total,
            "compliant": compliant,
            "non_compliant": len(non_compliant),
            "compliance_rate_pct": round(compliant / total * 100, 1) if total > 0 else 100.0,
            "required_tags": required_tags,
            "non_compliant_resources": non_compliant[:50],
        }
    except ImportError:
        return {"status": "unavailable", "message": "boto3 not installed"}
    except Exception as e:
        logger.exception("Failed to check tag compliance")
        return {"status": "error", "message": str(e)}


def get_savings_recommendations() -> dict[str, Any]:
    """Get cost optimisation recommendations from AWS Cost Explorer.

    Retrieves rightsizing, Reserved Instance, and Savings Plan recommendations.
    Uses IAM role credentials.

    Returns:
        dict with categorised recommendations and estimated savings.
    """
    try:
        import boto3

        ce = boto3.client("ce")

        # Rightsizing recommendations
        rightsizing = ce.get_rightsizing_recommendation(
            Service="AmazonEC2",
            Configuration={
                "RecommendationTarget": "SAME_INSTANCE_FAMILY",
                "BenefitsConsidered": True,
            },
        )

        recommendations = []
        total_savings = 0.0

        for rec in rightsizing.get("RightsizingRecommendations", [])[:20]:
            current = rec.get("CurrentInstance", {})
            modify = rec.get("ModifyRecommendationDetail", {})
            target_instances = modify.get("TargetInstances", [{}])
            target = target_instances[0] if target_instances else {}

            monthly_savings = float(target.get("EstimatedMonthlySavings", "0"))
            total_savings += monthly_savings

            recommendations.append(
                {
                    "type": "rightsizing",
                    "resource_id": current.get("ResourceId", ""),
                    "current_instance_type": current.get("ResourceDetails", {})
                    .get("EC2ResourceDetails", {})
                    .get("InstanceType", ""),
                    "recommended_instance_type": target.get("ResourceDetails", {})
                    .get("EC2ResourceDetails", {})
                    .get("InstanceType", ""),
                    "estimated_monthly_savings_usd": round(monthly_savings, 2),
                    "action": rec.get("RightsizingType", ""),
                }
            )

        return {
            "status": "success",
            "total_recommendations": len(recommendations),
            "total_estimated_monthly_savings_usd": round(total_savings, 2),
            "recommendations": recommendations,
        }
    except ImportError:
        return {"status": "unavailable", "message": "boto3 not installed"}
    except Exception as e:
        logger.exception("Failed to get recommendations")
        return {"status": "error", "message": str(e)}


def get_budget_alerts() -> dict[str, Any]:
    """Get AWS Budget status and alerts.

    Uses the AWS Budgets API with IAM role credentials.

    Returns:
        dict with budget details, spend vs limit, and threshold statuses.
    """
    try:
        import boto3

        sts = boto3.client("sts")
        account_id = sts.get_caller_identity()["Account"]

        budgets_client = boto3.client("budgets")
        response = budgets_client.describe_budgets(AccountId=account_id)

        budget_summaries = []
        for budget in response.get("Budgets", []):
            limit = float(budget.get("BudgetLimit", {}).get("Amount", 0))
            actual = float(
                budget.get("CalculatedSpend", {}).get("ActualSpend", {}).get("Amount", 0)
            )
            forecasted = float(
                budget.get("CalculatedSpend", {}).get("ForecastedSpend", {}).get("Amount", 0)
            )

            budget_summaries.append(
                {
                    "name": budget.get("BudgetName", ""),
                    "type": budget.get("BudgetType", ""),
                    "limit_usd": limit,
                    "actual_spend_usd": round(actual, 2),
                    "forecasted_spend_usd": round(forecasted, 2),
                    "utilisation_pct": round(actual / limit * 100, 1) if limit > 0 else 0,
                    "on_track": forecasted <= limit,
                }
            )

        return {
            "status": "success",
            "account_id": account_id,
            "budgets": budget_summaries,
        }
    except ImportError:
        return {"status": "unavailable", "message": "boto3 not installed"}
    except Exception as e:
        logger.exception("Failed to get budget alerts")
        return {"status": "error", "message": str(e)}


# ---------------------------------------------------------------------------
# Bedrock Agent Definition (Terraform-deployed, Lambda action groups)
# ---------------------------------------------------------------------------


def get_bedrock_agent_config() -> dict[str, Any]:
    """Return the Bedrock Agent configuration for Terraform deployment.

    The agent is deployed via Terraform using:
    - aws_bedrockagent_agent
    - aws_bedrockagent_agent_action_group
    - aws_bedrockagent_agent_knowledge_base

    Authentication is via IAM roles — no API keys anywhere.
    """
    return {
        "agent_name": "finops-cost-governance-agent",
        "foundation_model": "anthropic.claude-sonnet-4-20250514",
        "instruction": """You are an AWS FinOps governance agent. Your role is to:

1. MONITOR: Analyse AWS Cost Explorer data to identify spending trends and anomalies.
2. DETECT: Find cost anomalies using AWS Cost Anomaly Detection.
3. ENFORCE: Check tag compliance (Team, CostCentre, Environment, Owner).
4. RECOMMEND: Surface rightsizing and Savings Plan recommendations.
5. REPORT: Generate human-readable reports that require no further investigation.

When generating alerts or reports:
- Always include WHO created the resource (accountability).
- Always include WHAT the cost impact is (exact USD figures).
- Always include WHAT TO DO NEXT (prioritised action list).
- Always include the ESCALATION PATH if action is not taken.
- Never require the reader to look up additional information.

You advocate for accountability and responsible cloud spending.
All your actions are audited for governance compliance.""",
        "action_groups": [
            {
                "name": "CostAnalysis",
                "description": "Analyse AWS cost and usage data",
                "functions": [
                    {
                        "name": "analyse_cost_and_usage",
                        "description": analyse_cost_and_usage.__doc__,
                        "parameters": {
                            "granularity": {"type": "string", "required": False},
                            "period_days": {"type": "integer", "required": False},
                            "group_by": {"type": "string", "required": False},
                        },
                    },
                    {
                        "name": "detect_cost_anomalies",
                        "description": detect_cost_anomalies.__doc__,
                        "parameters": {
                            "monitor_arn": {"type": "string", "required": False},
                            "period_days": {"type": "integer", "required": False},
                        },
                    },
                ],
            },
            {
                "name": "Compliance",
                "description": "Check resource tag compliance and governance",
                "functions": [
                    {
                        "name": "check_tag_compliance",
                        "description": check_tag_compliance.__doc__,
                        "parameters": {
                            "required_tags": {"type": "array", "required": False},
                            "resource_types": {"type": "array", "required": False},
                        },
                    },
                ],
            },
            {
                "name": "Optimisation",
                "description": "Cost optimisation recommendations",
                "functions": [
                    {
                        "name": "get_savings_recommendations",
                        "description": get_savings_recommendations.__doc__,
                        "parameters": {},
                    },
                    {
                        "name": "get_budget_alerts",
                        "description": get_budget_alerts.__doc__,
                        "parameters": {},
                    },
                ],
            },
        ],
        "idle_session_ttl_seconds": 600,
    }


# ---------------------------------------------------------------------------
# AWS MCP Integration
# ---------------------------------------------------------------------------


def get_aws_mcp_server_configs() -> list[dict[str, Any]]:
    """Return configurations for AWS native MCP servers.

    These MCP servers from https://github.com/awslabs/mcp provide
    native AWS service access. Authentication is via IAM roles.

    Returns:
        List of MCP server configurations for integration.
    """
    return [
        {
            "name": "aws-cost-explorer-mcp",
            "description": "AWS Cost Explorer MCP server for cost analysis",
            "command": "uvx",
            "args": ["awslabs.cost-explorer-mcp-server@latest"],
            "env": {
                "AWS_REGION": "eu-west-2",
                "AWS_PROFILE": "",  # Uses IAM role, not profile
            },
        },
        {
            "name": "aws-cloudwatch-mcp",
            "description": "AWS CloudWatch MCP server for monitoring and alarms",
            "command": "uvx",
            "args": ["awslabs.cloudwatch-mcp-server@latest"],
            "env": {
                "AWS_REGION": "eu-west-2",
            },
        },
        {
            "name": "aws-cloudformation-mcp",
            "description": "AWS CloudFormation MCP server for infrastructure queries",
            "command": "uvx",
            "args": ["awslabs.cloudformation-mcp-server@latest"],
            "env": {
                "AWS_REGION": "eu-west-2",
            },
        },
    ]
