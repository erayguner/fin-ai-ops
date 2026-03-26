"""Tests for the input validation and sanitisation module."""

from __future__ import annotations

from pathlib import Path

import pytest
from core.validation import (
    ValidationError,
    safe_error_message,
    sanitise_string,
    validate_account_id,
    validate_cost,
    validate_dict_depth,
    validate_email,
    validate_provider,
    validate_query_limit,
    validate_resource_id,
    validate_resource_type,
    validate_safe_path,
    validate_severity,
    validate_status,
    validate_tags,
    validate_webhook_url,
)


class TestSanitiseString:
    def test_strips_whitespace(self):
        assert sanitise_string("  hello  ", "field") == "hello"

    def test_enforces_max_length(self):
        with pytest.raises(ValidationError, match="exceeds maximum length"):
            sanitise_string("x" * 100, "field", max_length=50)

    def test_strips_control_characters(self):
        result = sanitise_string("hello\x00world\x01", "field")
        assert result == "helloworld"

    def test_preserves_newlines_and_tabs(self):
        result = sanitise_string("hello\nworld\there", "field")
        assert "\n" in result
        assert "\t" in result

    def test_rejects_non_string(self):
        with pytest.raises(ValidationError, match="expected string"):
            sanitise_string(123, "field")


class TestValidateProvider:
    def test_valid_providers(self):
        assert validate_provider("aws") == "aws"
        assert validate_provider("GCP") == "gcp"
        assert validate_provider("  Azure  ") == "azure"

    def test_invalid_provider(self):
        with pytest.raises(ValidationError, match="must be one of"):
            validate_provider("oracle")

    def test_empty_provider(self):
        with pytest.raises(ValidationError, match="must be one of"):
            validate_provider("")


class TestValidateSeverity:
    def test_valid_severities(self):
        assert validate_severity("critical") == "critical"
        assert validate_severity("WARNING") == "warning"

    def test_invalid_severity(self):
        with pytest.raises(ValidationError):
            validate_severity("panic")


class TestValidateStatus:
    def test_valid_statuses(self):
        assert validate_status("pending") == "pending"
        assert validate_status("RESOLVED") == "resolved"

    def test_invalid_status(self):
        with pytest.raises(ValidationError):
            validate_status("cancelled")


class TestValidateAccountId:
    def test_valid_aws_account(self):
        assert validate_account_id("123456789012") == "123456789012"

    def test_valid_gcp_project(self):
        assert validate_account_id("my-project-123") == "my-project-123"

    def test_empty_account_id(self):
        with pytest.raises(ValidationError, match="cannot be empty"):
            validate_account_id("")

    def test_invalid_characters(self):
        with pytest.raises(ValidationError, match="invalid characters"):
            validate_account_id("account;DROP TABLE")


class TestValidateResourceId:
    def test_valid_resource_id(self):
        assert validate_resource_id("i-0123456789abcdef0") == "i-0123456789abcdef0"

    def test_valid_arn(self):
        result = validate_resource_id("arn:aws:ec2:us-east-1:123456789012:instance/i-abc")
        assert result.startswith("arn:")

    def test_empty_resource_id(self):
        with pytest.raises(ValidationError, match="cannot be empty"):
            validate_resource_id("")


class TestValidateResourceType:
    def test_valid_types(self):
        assert validate_resource_type("ec2:instance") == "ec2:instance"
        assert validate_resource_type("compute.instances") == "compute.instances"

    def test_empty_type(self):
        with pytest.raises(ValidationError, match="cannot be empty"):
            validate_resource_type("")

    def test_injection_attempt(self):
        with pytest.raises(ValidationError, match="invalid characters"):
            validate_resource_type("ec2; rm -rf /")


class TestValidateEmail:
    def test_valid_email(self):
        assert validate_email("user@example.com") == "user@example.com"

    def test_empty_email_allowed(self):
        assert validate_email("") == ""

    def test_invalid_format(self):
        with pytest.raises(ValidationError, match="invalid format"):
            validate_email("not-an-email")


class TestValidateTags:
    def test_valid_tags(self):
        result = validate_tags({"team": "platform", "env": "prod"})
        assert result == {"team": "platform", "env": "prod"}

    def test_none_returns_empty(self):
        assert validate_tags(None) == {}

    def test_exceeds_max_tags(self):
        big_tags = {f"key{i}": f"val{i}" for i in range(51)}
        with pytest.raises(ValidationError, match="exceeds maximum"):
            validate_tags(big_tags)

    def test_non_dict_rejected(self):
        with pytest.raises(ValidationError, match="expected dictionary"):
            validate_tags("not-a-dict")


class TestValidateQueryLimit:
    def test_valid_limit(self):
        assert validate_query_limit(100) == 100

    def test_clamps_to_max(self):
        assert validate_query_limit(999999) == 10_000

    def test_rejects_negative(self):
        with pytest.raises(ValidationError, match="positive integer"):
            validate_query_limit(-1)

    def test_rejects_zero(self):
        with pytest.raises(ValidationError, match="positive integer"):
            validate_query_limit(0)


class TestValidateCost:
    def test_valid_cost(self):
        assert validate_cost(100.0) == 100.0

    def test_zero_cost(self):
        assert validate_cost(0.0) == 0.0

    def test_negative_cost(self):
        with pytest.raises(ValidationError, match="cannot be negative"):
            validate_cost(-1.0)

    def test_extreme_cost(self):
        with pytest.raises(ValidationError, match="exceeds maximum"):
            validate_cost(2_000_000_000.0)


class TestValidateWebhookUrl:
    def test_valid_https(self):
        url = validate_webhook_url("https://example.com/hook")
        assert url == "https://example.com/hook"

    def test_blocks_http_by_default(self):
        with pytest.raises(ValidationError, match="scheme must be"):
            validate_webhook_url("http://example.com/hook")

    def test_allows_http_when_enabled(self):
        url = validate_webhook_url("http://example.com/hook", allow_http=True)
        assert url.startswith("http://")

    def test_blocks_localhost(self):
        with pytest.raises(ValidationError, match="blocked internal host"):
            validate_webhook_url("https://localhost/hook")

    def test_blocks_metadata_endpoint(self):
        with pytest.raises(ValidationError, match="blocked internal host"):
            validate_webhook_url("https://169.254.169.254/latest/meta-data/")

    def test_blocks_google_metadata(self):
        with pytest.raises(ValidationError, match="blocked internal host"):
            validate_webhook_url("https://metadata.google.internal/computeMetadata/")

    def test_blocks_private_ips(self):
        with pytest.raises(ValidationError, match="private/internal IP"):
            validate_webhook_url("https://10.0.0.1/hook")
        with pytest.raises(ValidationError, match="private/internal IP"):
            validate_webhook_url("https://192.168.1.1/hook")
        with pytest.raises(ValidationError, match="private/internal IP"):
            validate_webhook_url("https://172.16.0.1/hook")

    def test_blocks_loopback(self):
        with pytest.raises(ValidationError, match="blocked internal host"):
            validate_webhook_url("https://127.0.0.1/hook")

    def test_blocks_unusual_ports(self):
        with pytest.raises(ValidationError, match="non-standard port"):
            validate_webhook_url("https://example.com:9090/hook")

    def test_allows_standard_ports(self):
        validate_webhook_url("https://example.com:443/hook")

    def test_blocks_empty_url(self):
        with pytest.raises(ValidationError, match="cannot be empty"):
            validate_webhook_url("")

    def test_blocks_ftp(self):
        with pytest.raises(ValidationError, match="scheme must be"):
            validate_webhook_url("ftp://example.com/hook")


class TestValidateSafePath:
    def test_valid_path(self):
        p = validate_safe_path("/tmp/test")  # noqa: S108
        assert isinstance(p, Path)

    def test_blocks_traversal(self):
        with pytest.raises(ValidationError, match="path traversal"):
            validate_safe_path("/tmp/../etc/passwd")  # noqa: S108

    def test_base_dir_containment(self):
        with pytest.raises(ValidationError, match="must be within"):
            validate_safe_path("/etc/passwd", base_dir=Path("/tmp"))  # noqa: S108

    def test_allows_path_within_base(self):
        p = validate_safe_path("/tmp/subdir", base_dir=Path("/tmp"))  # noqa: S108
        assert p == Path("/tmp/subdir").resolve()  # noqa: S108


class TestValidateDictDepth:
    def test_shallow_dict(self):
        validate_dict_depth({"a": "b"})

    def test_deep_dict_blocked(self):
        deep = {"a": {"b": {"c": {"d": {"e": {"f": "too deep"}}}}}}
        with pytest.raises(ValidationError, match="exceeds maximum nesting depth"):
            validate_dict_depth(deep)

    def test_nested_lists(self):
        deep = [[[[[[[["too deep"]]]]]]]]
        with pytest.raises(ValidationError, match="exceeds maximum nesting depth"):
            validate_dict_depth(deep)


class TestSafeErrorMessage:
    def test_strips_file_paths(self):
        msg = safe_error_message(Exception("Error in /home/user/app/core/models.py at line 42"))
        assert "/home/user" not in msg
        assert "line 42" not in msg

    def test_strips_error_prefix(self):
        msg = safe_error_message(ValueError("invalid literal"))
        assert "ValueError" not in msg

    def test_truncates_long_messages(self):
        msg = safe_error_message(Exception("x" * 1000))
        assert len(msg) <= 500

    def test_preserves_useful_content(self):
        msg = safe_error_message(ValidationError("provider: must be one of ['aws', 'gcp']"))
        assert "provider" in msg
