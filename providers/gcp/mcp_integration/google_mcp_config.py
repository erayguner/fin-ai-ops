"""Google Cloud MCP Server integration configuration.

Configures native Google MCP servers for use with the GCP FinOps agent.
All servers authenticate via Application Default Credentials or
Workload Identity Federation — no API keys.

Google MCP servers available: https://github.com/google/mcp
Remote MCP servers managed by Google: https://docs.cloud.google.com/mcp/overview
"""

from __future__ import annotations

from typing import Any

__all__ = ["get_adk_mcp_toolset_config", "get_claude_code_mcp_config", "get_google_mcp_servers"]


def get_google_mcp_servers() -> list[dict[str, Any]]:
    """Return configurations for Google's native MCP servers.

    These are Google-managed remote MCP servers that provide native
    access to GCP services. Authentication uses OAuth 2.0 via ADC/WIF.
    No API keys are required.

    Returns:
        List of MCP server configurations.
    """
    return [
        {
            "name": "google-bigquery-mcp",
            "description": (
                "Google BigQuery MCP server for billing data analysis. "
                "Supports SQL queries, schema inspection, and AI forecasting."
            ),
            "type": "remote",
            "endpoint": "https://mcp.googleapis.com/v1alpha/sse",
            "tools": [
                "list_dataset_ids",
                "get_table_info",
                "execute_sql",
                "forecast",
                "ask_data_insights",
            ],
            "auth": {
                "type": "oauth2",
                "method": "application_default_credentials",
                "scopes": [
                    "https://www.googleapis.com/auth/bigquery.readonly",
                ],
            },
            "use_case": "Query billing export data in BigQuery for cost analysis",
        },
        {
            "name": "google-resource-manager-mcp",
            "description": (
                "Google Cloud Resource Manager MCP server for project and "
                "resource hierarchy management."
            ),
            "type": "remote",
            "endpoint": "https://mcp.googleapis.com/v1alpha/sse",
            "auth": {
                "type": "oauth2",
                "method": "application_default_credentials",
                "scopes": [
                    "https://www.googleapis.com/auth/cloud-platform.read-only",
                ],
            },
            "use_case": "Inspect project hierarchy, labels, and IAM policies",
        },
    ]


def get_adk_mcp_toolset_config() -> dict[str, Any]:
    """Return ADK MCPToolset configuration for Google MCP servers.

    This configuration is used with google.adk.tools.mcp_tool.MCPToolset
    to integrate Google's remote MCP servers into ADK agents.

    Example usage:
        from google.adk.tools.mcp_tool import MCPToolset, SseServerParams

        config = get_adk_mcp_toolset_config()
        bigquery_tools = MCPToolset(
            connection_params=SseServerParams(
                url=config["bigquery"]["url"],
                headers=config["bigquery"]["headers"],
            ),
        )
    """
    return {
        "bigquery": {
            "url": "https://mcp.googleapis.com/v1alpha/sse",
            "headers": {
                "Content-Type": "application/json",
                # Auth header is injected by ADC/WIF at runtime
            },
            "description": "BigQuery MCP for billing data queries",
        },
        "resource_manager": {
            "url": "https://mcp.googleapis.com/v1alpha/sse",
            "headers": {
                "Content-Type": "application/json",
            },
            "description": "Resource Manager MCP for project inspection",
        },
    }


def get_claude_code_mcp_config(project_id: str) -> dict[str, Any]:
    """Return MCP server configuration for Claude Code integration.

    Add this to .claude/settings.json to use Google MCP servers
    directly from Claude Code.

    Args:
        project_id: GCP project ID for scoping queries.

    Returns:
        dict suitable for the mcpServers section of Claude Code settings.
    """
    return {
        "google-bigquery": {
            "command": "npx",
            "args": [
                "-y",
                "@anthropic-ai/mcp-server-google-cloud",
                "--project",
                project_id,
                "--service",
                "bigquery",
            ],
            "env": {
                "GOOGLE_APPLICATION_CREDENTIALS": "",  # Uses ADC
            },
        },
    }
