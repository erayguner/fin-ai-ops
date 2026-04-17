"""GCP FinOps Agent — built on Google Agent Development Kit (ADK).

Uses Google's native agent framework with:
- Google ADK (google-adk) for agent orchestration
- Google Cloud MCP servers for BigQuery billing and Resource Manager
- Workload Identity Federation for keyless authentication
- Vertex AI as the LLM backend (Gemini models)

No API keys are used. All authentication is via:
- Application Default Credentials (development)
- Workload Identity Federation (production on GKE/Cloud Run)

Reference: https://docs.cloud.google.com/agent-builder/agent-development-kit/overview
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "analyse_billing_costs",
    "check_label_compliance",
    "create_gcp_finops_agent",
    "create_gcp_finops_agent_with_mcp",
    "detect_costly_resources",
    "get_budget_status",
    "recommend_cost_optimisations",
]


# ---------------------------------------------------------------------------
# ADK Tool Functions — exposed to the Gemini-powered agent
#
# Each function's docstring is read by the LLM to decide when to invoke it.
# The ToolContext parameter enables session state and auth flows.
# ---------------------------------------------------------------------------


def analyse_billing_costs(
    bq_project: str,
    bq_dataset: str,
    billing_account_id: str,
    project_id: str | None = None,
    period: str = "LAST_30_DAYS",
) -> dict[str, Any]:
    """Query GCP billing data from BigQuery billing export.

    Uses Application Default Credentials or Workload Identity — never API keys.

    The billing-export table lives in the *host* project of the BigQuery
    dataset (not the project being analysed) and is named
    ``gcp_billing_export_v1_<BILLING_ACCOUNT_ID>`` (hyphens replaced with
    underscores). See docs.cloud.google.com/billing/docs/how-to/export-data-bigquery-tables.

    Args:
        bq_project: Project that hosts the BigQuery billing dataset.
        bq_dataset: Dataset name containing the export table.
        billing_account_id: Billing account ID (e.g. ``XXXXXX-XXXXXX-XXXXXX``).
        project_id: Optional project to scope the results to.
        period: Time period — LAST_7_DAYS, LAST_30_DAYS, LAST_90_DAYS, or YYYY-MM.

    Returns:
        dict with total_cost_usd, cost_by_service, cost_by_sku, top_resources.
    """
    try:
        from google.cloud import bigquery

        client = bigquery.Client()  # Uses ADC/WIF — no keys

        period_clause = _build_period_clause(period)
        table_suffix = billing_account_id.replace("-", "_")
        table_ref = f"{bq_project}.{bq_dataset}.gcp_billing_export_v1_{table_suffix}"
        params: list[bigquery.ScalarQueryParameter] = []
        project_clause = ""
        if project_id:
            project_clause = " AND project.id = @project_id"
            params.append(bigquery.ScalarQueryParameter("project_id", "STRING", project_id))

        query = f"""
            SELECT
                service.description AS service,
                sku.description AS sku,
                SUM(cost) AS total_cost,
                SUM(usage.amount) AS total_usage,
                usage.unit AS usage_unit
            FROM `{table_ref}`
            WHERE {period_clause}{project_clause}
            GROUP BY service, sku, usage_unit
            ORDER BY total_cost DESC
            LIMIT 50
        """  # noqa: S608  # nosec B608
        job_config = bigquery.QueryJobConfig(query_parameters=params) if params else None
        results = client.query(query, job_config=job_config).result()
        rows = [dict(row) for row in results]

        total = sum(r.get("total_cost", 0) for r in rows)
        by_service: dict[str, float] = {}
        for row in rows:
            svc = row.get("service", "Unknown")
            by_service[svc] = by_service.get(svc, 0) + row.get("total_cost", 0)

        return {
            "status": "success",
            "bq_project": bq_project,
            "bq_dataset": bq_dataset,
            "billing_account_id": billing_account_id,
            "project_id": project_id,
            "period": period,
            "total_cost_usd": round(total, 2),
            "cost_by_service": {k: round(v, 2) for k, v in by_service.items()},
            "top_skus": rows[:10],
        }
    except ImportError:
        logger.warning("google-cloud-bigquery not installed")
        return {"status": "unavailable", "message": "BigQuery client not installed"}
    except Exception as e:
        logger.exception("Failed to query billing data")
        return {"status": "error", "message": str(e)}


def detect_costly_resources(
    project_id: str,
    threshold_usd: float = 500.0,
) -> dict[str, Any]:
    """Detect GCP resources exceeding a monthly cost threshold.

    Queries Cloud Asset Inventory and billing data to find resources
    with high estimated costs. Uses ADC/WIF for authentication.

    Args:
        project_id: GCP project to scan.
        threshold_usd: Monthly cost threshold in USD.

    Returns:
        dict with costly_resources list, each containing resource details,
        creator, cost, and recommended actions.
    """
    try:
        from google.cloud import asset_v1  # type: ignore[attr-defined]

        client = asset_v1.AssetServiceClient()  # Uses ADC/WIF
        request = asset_v1.ListAssetsRequest(
            parent=f"projects/{project_id}",
            asset_types=[
                "compute.googleapis.com/Instance",
                "sqladmin.googleapis.com/Instance",
                "container.googleapis.com/Cluster",
                "redis.googleapis.com/Instance",
            ],
            content_type=asset_v1.ContentType.RESOURCE,
        )
        assets = list(client.list_assets(request=request))

        costly = []
        for asset in assets:
            resource = asset.resource
            estimated_cost = _estimate_resource_cost(asset)
            if estimated_cost >= threshold_usd:
                costly.append(
                    {
                        "resource_name": asset.name,
                        "resource_type": asset.asset_type,
                        "location": resource.location if resource else "unknown",
                        "estimated_monthly_cost_usd": round(estimated_cost, 2),
                        "labels": dict(resource.data.get("labels", {}))
                        if resource and resource.data
                        else {},
                        "create_time": str(resource.data.get("creationTimestamp", ""))
                        if resource and resource.data
                        else "",
                    }
                )

        return {
            "status": "success",
            "project_id": project_id,
            "threshold_usd": threshold_usd,
            "total_costly_resources": len(costly),
            "costly_resources": costly,
        }
    except ImportError:
        return {"status": "unavailable", "message": "google-cloud-asset not installed"}
    except Exception as e:
        logger.exception("Failed to detect costly resources")
        return {"status": "error", "message": str(e)}


def check_label_compliance(
    project_id: str,
    required_labels: list[str] | None = None,
) -> dict[str, Any]:
    """Check GCP resources for missing required labels.

    Labels are essential for cost attribution and accountability.
    Uses Cloud Asset Inventory API with ADC/WIF authentication.

    Args:
        project_id: GCP project to audit.
        required_labels: Labels that must be present (defaults to team, cost-centre, environment, owner).

    Returns:
        dict with compliance stats and non_compliant resources list.
    """
    if required_labels is None:
        required_labels = ["team", "cost-centre", "environment", "owner"]

    try:
        from google.cloud import asset_v1  # type: ignore[attr-defined]

        client = asset_v1.AssetServiceClient()
        request = asset_v1.ListAssetsRequest(
            parent=f"projects/{project_id}",
            content_type=asset_v1.ContentType.RESOURCE,
        )
        assets = list(client.list_assets(request=request))

        non_compliant = []
        total = 0
        compliant_count = 0

        for asset in assets:
            resource = asset.resource
            if not resource or not resource.data:
                continue
            total += 1
            labels = dict(resource.data.get("labels", {}))
            missing = [lbl for lbl in required_labels if lbl not in labels]
            if missing:
                non_compliant.append(
                    {
                        "resource_name": asset.name,
                        "resource_type": asset.asset_type,
                        "missing_labels": missing,
                        "existing_labels": labels,
                    }
                )
            else:
                compliant_count += 1

        return {
            "status": "success",
            "project_id": project_id,
            "total_resources": total,
            "compliant": compliant_count,
            "non_compliant": len(non_compliant),
            "compliance_rate_pct": round(compliant_count / total * 100, 1) if total > 0 else 100.0,
            "required_labels": required_labels,
            "non_compliant_resources": non_compliant[:50],
        }
    except ImportError:
        return {"status": "unavailable", "message": "google-cloud-asset not installed"}
    except Exception as e:
        logger.exception("Failed to check label compliance")
        return {"status": "error", "message": str(e)}


def get_budget_status(
    billing_account_id: str,
) -> dict[str, Any]:
    """Get current budgets for a Cloud Billing account.

    Budgets are scoped to a billing account, not a project — see
    docs.cloud.google.com/billing/docs/how-to/budget-api. Uses the Cloud
    Billing Budget API with ADC/WIF authentication.

    Args:
        billing_account_id: Billing account ID (e.g. ``XXXXXX-XXXXXX-XXXXXX``).

    Returns:
        dict with budgets list (display name, amount, threshold rules).
    """
    try:
        from google.cloud.billing import budgets

        client = budgets.BudgetServiceClient()
        parent = f"billingAccounts/{billing_account_id}"
        results: list[dict[str, Any]] = []
        for budget in client.list_budgets(request={"parent": parent}):
            amount = budget.amount
            entry: dict[str, Any] = {
                "name": budget.name,
                "display_name": budget.display_name,
                "threshold_rules": [
                    {
                        "threshold_percent": rule.threshold_percent,
                        "spend_basis": rule.spend_basis.name
                        if rule.spend_basis
                        else "UNSPECIFIED",
                    }
                    for rule in budget.threshold_rules
                ],
            }
            if "specified_amount" in amount:
                entry["specified_amount_units"] = amount.specified_amount.units
                entry["currency_code"] = amount.specified_amount.currency_code
            elif "last_period_amount" in amount:
                entry["dynamic_last_period_amount"] = True
            results.append(entry)

        return {
            "status": "success",
            "billing_account_id": billing_account_id,
            "budgets": results,
            "total_budgets": len(results),
        }
    except ImportError:
        return {"status": "unavailable", "message": "google-cloud-billing-budgets not installed"}
    except Exception as e:
        logger.exception("Failed to list budgets")
        return {"status": "error", "message": str(e)}


def recommend_cost_optimisations(
    project_id: str,
) -> dict[str, Any]:
    """Get cost optimisation recommendations from GCP Recommender API.

    Retrieves machine type, idle resource, and commitment recommendations.
    Uses ADC/WIF authentication — no API keys.

    Args:
        project_id: GCP project to get recommendations for.

    Returns:
        dict with categorised recommendations and estimated savings.
    """
    try:
        from google.cloud import recommender_v1  # type: ignore[attr-defined]

        client = recommender_v1.RecommenderClient()

        recommender_types = [
            "google.compute.instance.MachineTypeRecommender",
            "google.compute.instance.IdleResourceRecommender",
            "google.compute.commitment.UsageCommitmentRecommender",
        ]

        all_recs = []
        for rec_type in recommender_types:
            parent = f"projects/{project_id}/locations/-/recommenders/{rec_type}"
            try:
                recs = list(client.list_recommendations(parent=parent))
                for rec in recs:
                    all_recs.append(
                        {
                            "name": rec.name,
                            "description": rec.description,
                            "recommender": rec_type.split(".")[-1],
                            "priority": rec.priority.name if rec.priority else "UNSET",
                            "state": rec.state_info.state.name if rec.state_info else "UNKNOWN",
                        }
                    )
            except Exception:
                logger.debug("No recommendations for %s", rec_type)

        return {
            "status": "success",
            "project_id": project_id,
            "total_recommendations": len(all_recs),
            "recommendations": all_recs,
        }
    except ImportError:
        return {"status": "unavailable", "message": "google-cloud-recommender not installed"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


# ---------------------------------------------------------------------------
# ADK Agent Definition
# ---------------------------------------------------------------------------


def create_gcp_finops_agent():
    """Create the GCP FinOps ADK agent.

    Returns:
        A google.adk Agent configured with FinOps tools and Gemini model.
    """
    try:
        from google.adk.agents import Agent

        return Agent(
            model="gemini-2.5-flash",
            name="gcp_finops_agent",
            description=(
                "GCP FinOps agent that monitors cloud costs, detects anomalies, "
                "enforces tagging compliance, and recommends optimisations. "
                "Uses native GCP APIs with Workload Identity Federation."
            ),
            instruction="""You are a GCP FinOps governance agent. Your role is to:

1. MONITOR: Analyse billing data to identify cost trends and anomalies.
2. DETECT: Find costly resources that exceed thresholds.
3. ENFORCE: Check label compliance (team, cost-centre, environment, owner).
4. RECOMMEND: Suggest optimisations using GCP Recommender API data.
5. REPORT: Generate human-readable reports that require no further investigation.

When generating alerts or reports:
- Always include WHO created the resource (accountability).
- Always include WHAT the cost impact is (exact figures).
- Always include WHAT TO DO NEXT (prioritised action list).
- Always include the ESCALATION PATH if action is not taken.
- Never require the reader to look up additional information.

You advocate for accountability and responsible cloud spending.
All your actions are audited for governance compliance.""",
            tools=[
                analyse_billing_costs,
                detect_costly_resources,
                check_label_compliance,
                get_budget_status,
                recommend_cost_optimisations,
            ],
        )
    except ImportError:
        logger.warning("google-adk not installed. Install with: pip install google-adk")
        return None


# ---------------------------------------------------------------------------
# ADK Agent with Google MCP Servers
# ---------------------------------------------------------------------------


def create_gcp_finops_agent_with_mcp():
    """Create a GCP FinOps ADK agent with Google's native MCP servers.

    Integrates the BigQuery MCP server for billing data queries. Per
    docs.cloud.google.com/bigquery/docs/use-bigquery-mcp, the endpoint is
    ``https://bigquery.googleapis.com/mcp`` over streamable HTTP — there
    is no unified ``mcp.googleapis.com`` entrypoint.
    """
    try:
        from google.adk.agents import Agent
        from google.adk.tools.mcp_tool import McpToolset
        from google.adk.tools.mcp_tool.mcp_session_manager import (
            StreamableHTTPConnectionParams,
        )

        # Google's managed BigQuery MCP server
        bigquery_mcp = McpToolset(
            connection_params=StreamableHTTPConnectionParams(
                url="https://bigquery.googleapis.com/mcp",
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json, text/event-stream",
                },
            ),
        )

        return Agent(
            model="gemini-2.5-flash",
            name="gcp_finops_agent_mcp",
            description=(
                "GCP FinOps agent using Google's native MCP servers for "
                "BigQuery billing analysis and Resource Manager operations."
            ),
            instruction="""You are a GCP FinOps governance agent with access to Google Cloud
MCP servers. Use the BigQuery MCP tools to query billing export data and the
Resource Manager tools to inspect project resources and labels.

Always produce fully contextualised, human-readable outputs with accountability
attribution and recommended next steps.""",
            tools=[
                bigquery_mcp,
                analyse_billing_costs,
                detect_costly_resources,
                check_label_compliance,
                get_budget_status,
                recommend_cost_optimisations,
            ],
        )
    except ImportError:
        logger.warning("google-adk not installed for MCP integration")
        return create_gcp_finops_agent()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_period_clause(period: str) -> str:
    """Build a BigQuery WHERE clause for the given time period."""
    if period == "LAST_7_DAYS":
        return "usage_start_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 7 DAY)"
    if period == "LAST_30_DAYS":
        return "usage_start_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 DAY)"
    if period == "LAST_90_DAYS":
        return "usage_start_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 90 DAY)"
    # Assume YYYY-MM format
    return f"FORMAT_TIMESTAMP('%Y-%m', usage_start_time) = '{period}'"


def _estimate_resource_cost(asset: Any) -> float:
    """Estimate monthly cost for a GCP asset based on its type and config."""
    from providers.gcp.cost_analyzer import GCPCostAnalyzer

    analyzer = GCPCostAnalyzer()
    asset_type = asset.asset_type

    type_map = {
        "compute.googleapis.com/Instance": "compute.instances",
        "sqladmin.googleapis.com/Instance": "cloudsql.instances",
        "container.googleapis.com/Cluster": "container.clusters",
        "redis.googleapis.com/Instance": "redis.instances",
    }

    resource_type = type_map.get(asset_type, "")
    if not resource_type:
        return 0.0

    config: dict[str, Any] = {}
    resource = asset.resource
    if resource and resource.data:
        data = resource.data
        if resource_type == "compute.instances":
            machine_type = data.get("machineType", "")
            if "/" in machine_type:
                machine_type = machine_type.split("/")[-1]
            config["machine_type"] = machine_type
        elif resource_type == "cloudsql.instances":
            settings = data.get("settings", {})
            config["tier"] = settings.get("tier", "db-custom-2-7680")
            config["availability_type"] = settings.get("availabilityType", "ZONAL")
        elif resource_type == "container.clusters":
            node_pools = data.get("nodePools", [{}])
            if node_pools:
                config["node_count"] = node_pools[0].get("initialNodeCount", 3)

    return analyzer.estimate(resource_type, config)
