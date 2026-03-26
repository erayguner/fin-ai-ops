"""AWS MCP Server integration configuration.

Configures native AWS MCP servers from https://github.com/awslabs/mcp
for use with the AWS FinOps agent. All servers authenticate via
IAM roles — no API keys or access key IDs.

AWS MCP servers run as local processes (stdio transport) and inherit
IAM credentials from the environment (instance profile, task role,
or OIDC federation).
"""

from __future__ import annotations

from typing import Any

__all__ = ["get_aws_mcp_servers", "get_bedrock_mcp_config", "get_claude_code_mcp_config"]


def get_aws_mcp_servers() -> list[dict[str, Any]]:
    """Return configurations for AWS native MCP servers.

    All servers from https://github.com/awslabs/mcp use IAM role
    authentication. No AWS_ACCESS_KEY_ID or AWS_SECRET_ACCESS_KEY
    environment variables should be set.

    Returns:
        List of MCP server configurations.
    """
    return [
        {
            "name": "aws-cost-explorer-mcp",
            "description": (
                "AWS Cost Explorer MCP server for cost and usage analysis. "
                "Provides tools for querying cost data, forecasting, and "
                "identifying cost anomalies."
            ),
            "command": "uvx",
            "args": ["awslabs.cost-explorer-mcp-server@latest"],
            "env": {
                "AWS_REGION": "eu-west-2",
                # No AWS_ACCESS_KEY_ID — uses IAM role
                # No AWS_SECRET_ACCESS_KEY — uses IAM role
            },
            "tools": [
                "get_cost_and_usage",
                "get_cost_forecast",
                "get_anomalies",
                "get_savings_plans_utilization",
                "get_reservation_utilization",
            ],
            "auth": {
                "type": "iam_role",
                "required_permissions": [
                    "ce:GetCostAndUsage",
                    "ce:GetCostForecast",
                    "ce:GetAnomalies",
                    "ce:GetSavingsPlansUtilization",
                    "ce:GetReservationUtilization",
                ],
            },
            "use_case": "Query AWS cost data for FinOps reporting and anomaly detection",
        },
        {
            "name": "aws-cloudwatch-mcp",
            "description": (
                "AWS CloudWatch MCP server for metrics, alarms, and log queries. "
                "Used to monitor resource utilisation and detect waste."
            ),
            "command": "uvx",
            "args": ["awslabs.cloudwatch-mcp-server@latest"],
            "env": {
                "AWS_REGION": "eu-west-2",
            },
            "tools": [
                "get_metric_data",
                "describe_alarms",
                "get_log_events",
                "put_metric_alarm",
            ],
            "auth": {
                "type": "iam_role",
                "required_permissions": [
                    "cloudwatch:GetMetricData",
                    "cloudwatch:DescribeAlarms",
                    "logs:GetLogEvents",
                    "logs:FilterLogEvents",
                ],
            },
            "use_case": "Monitor resource utilisation metrics and manage cost alarms",
        },
        {
            "name": "aws-cloudformation-mcp",
            "description": (
                "AWS CloudFormation MCP server for infrastructure queries. "
                "Used to understand deployed resources and their relationships."
            ),
            "command": "uvx",
            "args": ["awslabs.cloudformation-mcp-server@latest"],
            "env": {
                "AWS_REGION": "eu-west-2",
            },
            "auth": {
                "type": "iam_role",
                "required_permissions": [
                    "cloudformation:DescribeStacks",
                    "cloudformation:ListStackResources",
                ],
            },
            "use_case": "Query infrastructure to map resources to owners and teams",
        },
    ]


def get_bedrock_mcp_config() -> dict[str, Any]:
    """Return MCP configuration for Bedrock Agent integration.

    Bedrock AgentCore can connect to MCP servers as tool providers.
    This configuration maps MCP servers to Bedrock action groups.

    Reference: https://github.com/awslabs/amazon-bedrock-agentcore-samples
    """
    return {
        "mcp_servers": get_aws_mcp_servers(),
        "bedrock_integration": {
            "agent_core_runtime": True,
            "mcp_gateway": {
                "description": (
                    "Bedrock AgentCore MCP Gateway enables Bedrock agents "
                    "to invoke MCP server tools as action groups."
                ),
                "auth": {
                    "type": "iam_role",
                    "description": "Uses the Bedrock agent execution role",
                },
            },
        },
    }


def get_claude_code_mcp_config() -> dict[str, Any]:
    """Return MCP server configuration for Claude Code integration.

    Add these to .claude/settings.json to use AWS MCP servers
    directly from Claude Code. All use IAM role auth.

    Returns:
        dict suitable for the mcpServers section of Claude Code settings.
    """
    return {
        "aws-cost-explorer": {
            "command": "uvx",
            "args": ["awslabs.cost-explorer-mcp-server@latest"],
            "env": {
                "AWS_REGION": "eu-west-2",
                # IAM role credentials inherited from environment
            },
        },
        "aws-cloudwatch": {
            "command": "uvx",
            "args": ["awslabs.cloudwatch-mcp-server@latest"],
            "env": {
                "AWS_REGION": "eu-west-2",
            },
        },
        "aws-cloudformation": {
            "command": "uvx",
            "args": ["awslabs.cloudformation-mcp-server@latest"],
            "env": {
                "AWS_REGION": "eu-west-2",
            },
        },
    }
