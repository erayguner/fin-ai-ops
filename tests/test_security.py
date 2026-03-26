"""Security-focused tests for the FinOps Automation Hub.

These tests verify that the public API surfaces are hardened against
common attack vectors: injection, SSRF, path traversal, resource
exhaustion, and information leakage.
"""

from __future__ import annotations

import pytest
from core.audit import AuditLogger
from core.policies import PolicyEngine
from mcp_server.server import handle_tool_call


class TestMCPInputValidation:
    """Verify MCP tool calls reject malicious inputs safely."""

    def test_invalid_provider_rejected(self):
        result = handle_tool_call(
            "finops_evaluate_resource",
            {
                "provider": "'; DROP TABLE events; --",
                "account_id": "123456789012",
                "region": "eu-west-2",
                "resource_type": "ec2:instance",
                "resource_id": "i-abc",
                "estimated_monthly_cost_usd": 100.0,
                "creator_identity": "test-user",
            },
        )
        assert result["status"] == "error"

    def test_resource_type_injection_rejected(self):
        result = handle_tool_call(
            "finops_evaluate_resource",
            {
                "provider": "aws",
                "account_id": "123456789012",
                "region": "eu-west-2",
                "resource_type": "ec2; rm -rf /",
                "resource_id": "i-abc",
                "estimated_monthly_cost_usd": 100.0,
                "creator_identity": "test-user",
            },
        )
        assert result["status"] == "error"

    def test_negative_cost_rejected(self):
        result = handle_tool_call(
            "finops_evaluate_resource",
            {
                "provider": "aws",
                "account_id": "123456789012",
                "region": "eu-west-2",
                "resource_type": "ec2:instance",
                "resource_id": "i-abc",
                "estimated_monthly_cost_usd": -1000.0,
                "creator_identity": "test-user",
            },
        )
        assert result["status"] == "error"

    def test_extreme_cost_rejected(self):
        result = handle_tool_call(
            "finops_evaluate_resource",
            {
                "provider": "aws",
                "account_id": "123456789012",
                "region": "eu-west-2",
                "resource_type": "ec2:instance",
                "resource_id": "i-abc",
                "estimated_monthly_cost_usd": 999_999_999_999.0,
                "creator_identity": "test-user",
            },
        )
        assert result["status"] == "error"

    def test_oversized_tags_rejected(self):
        huge_tags = {f"key{i}": f"val{i}" for i in range(100)}
        result = handle_tool_call(
            "finops_evaluate_resource",
            {
                "provider": "aws",
                "account_id": "123456789012",
                "region": "eu-west-2",
                "resource_type": "ec2:instance",
                "resource_id": "i-abc",
                "estimated_monthly_cost_usd": 100.0,
                "creator_identity": "test-user",
                "tags": huge_tags,
            },
        )
        assert result["status"] == "error"

    def test_deeply_nested_config_rejected(self):
        deep_config = {"a": {"b": {"c": {"d": {"e": {"f": "deep"}}}}}}
        result = handle_tool_call(
            "finops_estimate_cost",
            {
                "provider": "aws",
                "resource_type": "ec2:instance",
                "config": deep_config,
            },
        )
        assert result["status"] == "error"

    def test_unknown_tool_rejected(self):
        result = handle_tool_call("finops_delete_everything", {})
        assert result["status"] == "error"
        assert "Unknown tool" in result["message"]


class TestMCPErrorSanitisation:
    """Verify error messages don't leak internal details."""

    def test_no_file_paths_in_errors(self):
        result = handle_tool_call(
            "finops_evaluate_resource",
            {
                "provider": "aws",
                "account_id": "123456789012",
                "region": "eu-west-2",
                "resource_type": "ec2:instance",
                "resource_id": "i-abc",
                "estimated_monthly_cost_usd": "not-a-number",
                "creator_identity": "test-user",
            },
        )
        assert result["status"] == "error"
        assert ".py" not in result["message"]
        assert "/home/" not in result["message"]

    def test_validation_errors_are_informative(self):
        result = handle_tool_call(
            "finops_list_alerts",
            {
                "severity": "MEGA_CRITICAL",
            },
        )
        assert result["status"] == "error"
        assert "must be one of" in result["message"]

    def test_invalid_query_limit_message(self):
        result = handle_tool_call(
            "finops_list_alerts",
            {
                "limit": -5,
            },
        )
        assert result["status"] == "error"
        assert "positive integer" in result["message"]


class TestPathTraversal:
    """Verify path traversal attacks are blocked."""

    def test_audit_logger_blocks_traversal(self):
        with pytest.raises(ValueError, match="path traversal"):
            AuditLogger("../../../etc/shadow")

    def test_policy_engine_blocks_traversal(self):
        audit = AuditLogger("/tmp/test-audit-security")  # noqa: S108
        with pytest.raises(ValueError, match="path traversal"):
            PolicyEngine("../../../etc", audit)

    def test_audit_logger_base_dir_containment(self):
        from pathlib import Path

        with pytest.raises(ValueError, match="must be within"):
            AuditLogger("/etc/passwd", base_dir=Path("/tmp"))  # noqa: S108

    def test_policy_engine_base_dir_containment(self):
        from pathlib import Path

        audit = AuditLogger("/tmp/test-audit-security2")  # noqa: S108
        with pytest.raises(ValueError, match="must be within"):
            PolicyEngine("/etc", audit, base_dir=Path("/tmp/safe"))  # noqa: S108


class TestMCPValidInputsStillWork:
    """Ensure security hardening doesn't break legitimate usage."""

    def test_evaluate_resource_valid(self):
        result = handle_tool_call(
            "finops_evaluate_resource",
            {
                "provider": "aws",
                "account_id": "123456789012",
                "region": "eu-west-2",
                "resource_type": "ec2:instance",
                "resource_id": "i-0123456789abcdef0",
                "estimated_monthly_cost_usd": 100.0,
                "creator_identity": "arn:aws:iam::123456789012:user/jane",
                "creator_email": "jane@example.com",
                "tags": {"team": "platform", "environment": "prod"},
            },
        )
        assert result["status"] in ("within_thresholds", "alert_generated")

    def test_list_policies_valid(self):
        result = handle_tool_call("finops_list_policies", {})
        assert "count" in result

    def test_hub_status_valid(self):
        result = handle_tool_call("finops_hub_status", {})
        assert result["status"] == "running"

    def test_estimate_cost_valid(self):
        result = handle_tool_call(
            "finops_estimate_cost",
            {
                "provider": "aws",
                "resource_type": "ec2:instance",
                "config": {"instance_type": "m5.large"},
            },
        )
        assert result["estimated_monthly_cost_usd"] > 0

    def test_create_and_delete_policy(self):
        result = handle_tool_call(
            "finops_create_policy",
            {
                "name": "Test Security Policy",
                "description": "Created by security test",
                "provider": "aws",
                "resource_types": ["ec2:instance"],
                "max_monthly_cost_usd": 5000.0,
            },
        )
        assert result["status"] == "created"
        policy_id = result["policy"]["policy_id"]

        # Clean up
        handle_tool_call("finops_delete_policy", {"policy_id": policy_id})
