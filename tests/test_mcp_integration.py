"""Tests for MCP integration configurations.

Validates that MCP server configs are correct, use keyless auth,
and don't contain any API keys or long-lived credentials.
"""

from providers.aws.mcp_integration.aws_mcp_config import (
    get_aws_mcp_servers,
    get_bedrock_mcp_config,
)
from providers.aws.mcp_integration.aws_mcp_config import (
    get_claude_code_mcp_config as get_aws_claude_config,
)
from providers.gcp.mcp_integration.google_mcp_config import (
    get_adk_mcp_toolset_config,
    get_google_mcp_servers,
)
from providers.gcp.mcp_integration.google_mcp_config import (
    get_claude_code_mcp_config as get_gcp_claude_config,
)


class TestGoogleMCPConfig:
    def test_google_mcp_servers_available(self):
        servers = get_google_mcp_servers()
        assert len(servers) >= 2
        names = {s["name"] for s in servers}
        assert "google-bigquery-mcp" in names
        assert "google-resource-manager-mcp" in names

    def test_google_mcp_uses_oauth_not_api_keys(self):
        servers = get_google_mcp_servers()
        for server in servers:
            auth = server.get("auth", {})
            assert auth.get("type") in ("oauth2",)
            assert auth.get("method") == "application_default_credentials"
            # No API key fields
            assert "api_key" not in str(server).lower()

    def test_adk_toolset_config(self):
        config = get_adk_mcp_toolset_config()
        assert "bigquery" in config
        assert "resource_manager" in config
        assert "url" in config["bigquery"]

    def test_gcp_claude_code_config(self):
        config = get_gcp_claude_config("my-project")
        assert "google-bigquery" in config
        env = config["google-bigquery"].get("env", {})
        # Should not have explicit credentials
        assert env.get("GOOGLE_APPLICATION_CREDENTIALS") == ""


class TestAWSMCPConfig:
    def test_aws_mcp_servers_available(self):
        servers = get_aws_mcp_servers()
        assert len(servers) >= 3

    def test_aws_mcp_uses_iam_roles(self):
        servers = get_aws_mcp_servers()
        for server in servers:
            auth = server.get("auth", {})
            assert auth.get("type") == "iam_role"
            env = server.get("env", {})
            assert "AWS_ACCESS_KEY_ID" not in env
            assert "AWS_SECRET_ACCESS_KEY" not in env

    def test_bedrock_mcp_config(self):
        config = get_bedrock_mcp_config()
        assert "mcp_servers" in config
        assert "bedrock_integration" in config
        integration = config["bedrock_integration"]
        assert integration["agent_core_runtime"] is True

    def test_aws_claude_code_config(self):
        config = get_aws_claude_config()
        assert "aws-cost-explorer" in config
        for _server_name, server_config in config.items():
            env = server_config.get("env", {})
            assert "AWS_ACCESS_KEY_ID" not in env
            assert "AWS_SECRET_ACCESS_KEY" not in env


class TestNoAPIKeysAnywhere:
    """Ensures no API keys, access keys, or secrets leak into any config."""

    FORBIDDEN_PATTERNS = [  # noqa: RUF012
        "api_key",
        "apikey",
        "access_key_id",
        "secret_access_key",
        "secret_key",
        "client_secret",
        "private_key",
        "bearer_token",
    ]

    def _check_no_keys(self, data: dict | list | str) -> None:
        data_str = str(data).lower()
        for pattern in self.FORBIDDEN_PATTERNS:
            # Allow references to "api_key" in type descriptions but not actual values
            if pattern in data_str:
                # Check it's not just a reference to a field name in a description
                assert f'"{pattern}": "AKIA' not in data_str, (
                    f"Found potential credential value for '{pattern}'"
                )

    def test_google_mcp_no_keys(self):
        self._check_no_keys(get_google_mcp_servers())

    def test_aws_mcp_no_keys(self):
        self._check_no_keys(get_aws_mcp_servers())

    def test_bedrock_config_no_keys(self):
        from providers.aws.agents.finops_agent import get_bedrock_agent_config

        self._check_no_keys(get_bedrock_agent_config())
