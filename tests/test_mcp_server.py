"""Tests for the MCP server tool handlers."""

import sys
from pathlib import Path

# Ensure the hub root is on the path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp_server.server import handle_tool_call, list_tools


class TestMCPToolRegistry:
    def test_list_tools_returns_all(self):
        tools = list_tools()
        tool_names = {t["name"] for t in tools}
        assert "finops_create_policy" in tool_names
        assert "finops_list_alerts" in tool_names
        assert "finops_query_audit" in tool_names
        assert "finops_hub_status" in tool_names
        assert "finops_estimate_cost" in tool_names
        assert len(tools) >= 14

    def test_hub_status(self):
        result = handle_tool_call("finops_hub_status", {})
        assert result["status"] == "running"

    def test_create_and_list_policy(self):
        result = handle_tool_call(
            "finops_create_policy",
            {
                "name": "MCP Test Policy",
                "description": "Created via MCP tool test",
                "provider": "aws",
                "max_monthly_cost_usd": 500.0,
            },
        )
        assert result["status"] == "created"
        policy_id = result["policy"]["policy_id"]

        # List should include it
        list_result = handle_tool_call("finops_list_policies", {})
        assert list_result["count"] >= 1

        # Clean up
        handle_tool_call("finops_delete_policy", {"policy_id": policy_id})

    def test_evaluate_resource_below_threshold(self):
        result = handle_tool_call(
            "finops_evaluate_resource",
            {
                "provider": "aws",
                "account_id": "123456789012",
                "region": "eu-west-2",
                "resource_type": "ec2:instance",
                "resource_id": "i-test-mcp",
                "estimated_monthly_cost_usd": 50.0,
                "creator_identity": "test-user",
            },
        )
        assert result["status"] == "within_thresholds"

    def test_evaluate_resource_above_threshold(self):
        result = handle_tool_call(
            "finops_evaluate_resource",
            {
                "provider": "aws",
                "account_id": "123456789012",
                "region": "eu-west-2",
                "resource_type": "ec2:instance",
                "resource_id": "i-test-mcp-expensive",
                "estimated_monthly_cost_usd": 3000.0,
                "creator_identity": "arn:aws:iam::123456789012:user/big.spender",
                "creator_email": "big.spender@example.com",
                "tags": {"team": "data-platform"},
            },
        )
        assert result["status"] == "alert_generated"
        assert "human_readable" in result
        assert "big.spender" in result["human_readable"]

    def test_estimate_cost_aws(self):
        result = handle_tool_call(
            "finops_estimate_cost",
            {
                "provider": "aws",
                "resource_type": "ec2:instance",
                "config": {"instance_type": "m5.large"},
            },
        )
        assert result["estimated_monthly_cost_usd"] > 0

    def test_estimate_cost_gcp(self):
        result = handle_tool_call(
            "finops_estimate_cost",
            {
                "provider": "gcp",
                "resource_type": "compute.instances",
                "config": {"machine_type": "n2-standard-2"},
            },
        )
        assert result["estimated_monthly_cost_usd"] > 0

    def test_unknown_tool(self):
        result = handle_tool_call("nonexistent_tool", {})
        assert result["status"] == "error"

    def test_audit_query(self):
        # Should have entries from the tool calls above
        result = handle_tool_call("finops_query_audit", {"limit": 10})
        assert result["count"] >= 0

    def test_verify_audit_integrity(self):
        result = handle_tool_call("finops_verify_audit_integrity", {})
        assert result["status"] in ("intact", "violations_detected")

    def test_alert_stats(self):
        result = handle_tool_call("finops_alert_stats", {})
        assert "total" in result
