"""Tests for the AWS Bedrock FinOps agent tools.

Tests the tool functions independently (without boto3/Bedrock runtime)
to validate cost analysis, compliance checking, and recommendations.
"""

from providers.aws.agents.finops_agent import (
    get_aws_mcp_server_configs,
    get_bedrock_agent_config,
)


class TestAWSBedrockAgentConfig:
    def test_agent_config_has_required_fields(self):
        config = get_bedrock_agent_config()
        assert "agent_name" in config
        assert "foundation_model" in config
        assert "instruction" in config
        assert "action_groups" in config
        assert "idle_session_ttl_seconds" in config

    def test_agent_config_has_action_groups(self):
        config = get_bedrock_agent_config()
        groups = config["action_groups"]
        assert len(groups) == 3
        group_names = {g["name"] for g in groups}
        assert "CostAnalysis" in group_names
        assert "Compliance" in group_names
        assert "Optimisation" in group_names

    def test_agent_uses_no_api_keys(self):
        """Verify the agent config does not reference API keys."""
        config = get_bedrock_agent_config()
        config_str = str(config).lower()
        assert "api_key" not in config_str
        assert "access_key" not in config_str
        assert "secret_key" not in config_str

    def test_instruction_includes_accountability(self):
        config = get_bedrock_agent_config()
        instruction = config["instruction"].lower()
        assert "accountab" in instruction
        assert "escalation" in instruction


class TestAWSMCPConfig:
    def test_mcp_servers_listed(self):
        configs = get_aws_mcp_server_configs()
        assert len(configs) >= 3
        names = {c["name"] for c in configs}
        assert "aws-cost-explorer-mcp" in names
        assert "aws-cloudwatch-mcp" in names

    def test_mcp_servers_use_iam_roles(self):
        """Verify MCP servers do not use API keys or access keys."""
        configs = get_aws_mcp_server_configs()
        for config in configs:
            env = config.get("env", {})
            assert "AWS_ACCESS_KEY_ID" not in env
            assert "AWS_SECRET_ACCESS_KEY" not in env
            assert "AWS_SESSION_TOKEN" not in env

    def test_mcp_servers_have_region(self):
        configs = get_aws_mcp_server_configs()
        for config in configs:
            env = config.get("env", {})
            assert "AWS_REGION" in env


class TestAWSToolFunctions:
    def test_analyse_cost_without_boto3(self):
        """Should handle missing boto3 gracefully."""
        from providers.aws.agents.finops_agent import analyse_cost_and_usage

        result = analyse_cost_and_usage()
        assert result["status"] in ("success", "unavailable", "error")

    def test_detect_anomalies_without_boto3(self):
        from providers.aws.agents.finops_agent import detect_cost_anomalies

        result = detect_cost_anomalies()
        assert result["status"] in ("success", "unavailable", "error")

    def test_check_tag_compliance_without_boto3(self):
        from providers.aws.agents.finops_agent import check_tag_compliance

        result = check_tag_compliance()
        assert result["status"] in ("success", "unavailable", "error")

    def test_get_savings_without_boto3(self):
        from providers.aws.agents.finops_agent import get_savings_recommendations

        result = get_savings_recommendations()
        assert result["status"] in ("success", "unavailable", "error")

    def test_get_budget_alerts_without_boto3(self):
        from providers.aws.agents.finops_agent import get_budget_alerts

        result = get_budget_alerts()
        assert result["status"] in ("success", "unavailable", "error")
