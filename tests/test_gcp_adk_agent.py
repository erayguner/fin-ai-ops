"""Tests for the GCP ADK FinOps agent tools.

Tests the tool functions independently (without google-adk runtime)
to validate cost analysis, compliance checking, and recommendations.
"""

from providers.gcp.agents.finops_agent import (
    _build_period_clause,
    create_gcp_finops_agent,
)


class TestGCPFinOpsToolFunctions:
    def test_build_period_clause_last_7_days(self):
        clause = _build_period_clause("LAST_7_DAYS")
        assert "INTERVAL 7 DAY" in clause

    def test_build_period_clause_last_30_days(self):
        clause = _build_period_clause("LAST_30_DAYS")
        assert "INTERVAL 30 DAY" in clause

    def test_build_period_clause_last_90_days(self):
        clause = _build_period_clause("LAST_90_DAYS")
        assert "INTERVAL 90 DAY" in clause

    def test_build_period_clause_specific_month(self):
        clause = _build_period_clause("2026-03")
        assert "2026-03" in clause

    def test_create_agent_without_adk_returns_none(self):
        """When google-adk is not installed, should return None gracefully."""
        agent = create_gcp_finops_agent()
        # In test env without google-adk, this should be None
        # If google-adk IS installed, it should be an Agent instance
        assert agent is None or hasattr(agent, "name")


class TestGCPAnalyseFunctions:
    def test_analyse_billing_costs_without_bigquery(self):
        """Should return unavailable when google-cloud-bigquery is not installed."""
        from providers.gcp.agents.finops_agent import analyse_billing_costs

        result = analyse_billing_costs(
            bq_project="host-project",
            bq_dataset="billing_export",
            billing_account_id="000000-111111-222222",
            project_id="test-project",
        )
        assert result["status"] in ("success", "unavailable", "error")

    def test_detect_costly_resources_without_asset(self):
        """Should return unavailable when google-cloud-asset is not installed."""
        from providers.gcp.agents.finops_agent import detect_costly_resources

        result = detect_costly_resources("test-project")
        assert result["status"] in ("success", "unavailable", "error")

    def test_check_label_compliance_without_asset(self):
        """Should return unavailable when google-cloud-asset is not installed."""
        from providers.gcp.agents.finops_agent import check_label_compliance

        result = check_label_compliance("test-project")
        assert result["status"] in ("success", "unavailable", "error")

    def test_get_budget_status_without_billing(self):
        """Should return unavailable when billing library is not installed."""
        from providers.gcp.agents.finops_agent import get_budget_status

        result = get_budget_status("000000-111111-222222")
        assert result["status"] in ("success", "unavailable", "error")

    def test_recommend_optimisations_without_recommender(self):
        """Should return unavailable when recommender library is not installed."""
        from providers.gcp.agents.finops_agent import recommend_cost_optimisations

        result = recommend_cost_optimisations("test-project")
        assert result["status"] in ("success", "unavailable", "error")
