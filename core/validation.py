"""Input validation and sanitisation for public API boundaries.

All external input (MCP tool arguments, config values, webhook URLs) must
pass through these validators before reaching core logic. This module
enforces size limits, format constraints, and safety checks to prevent
injection, SSRF, and resource exhaustion attacks.
"""

from __future__ import annotations

import ipaddress
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

__all__ = [
    "MAX_AUDIT_QUERY",
    "MAX_DICT_DEPTH",
    "MAX_QUERY_LIMIT",
    "MAX_STRING_LENGTH",
    "MAX_TAGS",
    "ValidationError",
    "safe_error_message",
    "sanitise_string",
    "validate_account_id",
    "validate_cost",
    "validate_dict_depth",
    "validate_email",
    "validate_provider",
    "validate_query_limit",
    "validate_resource_id",
    "validate_resource_type",
    "validate_safe_path",
    "validate_severity",
    "validate_status",
    "validate_tags",
    "validate_webhook_url",
]

# ---------------------------------------------------------------------------
# Size limits — prevent resource exhaustion
# ---------------------------------------------------------------------------

MAX_STRING_LENGTH = 2048
MAX_TAGS = 50
MAX_TAG_KEY_LENGTH = 128
MAX_TAG_VALUE_LENGTH = 256
MAX_QUERY_LIMIT = 10_000
MAX_AUDIT_QUERY = 10_000
MAX_DICT_DEPTH = 5

# ---------------------------------------------------------------------------
# Safe string patterns
# ---------------------------------------------------------------------------

# Account IDs: AWS 12-digit or GCP project-id format
_AWS_ACCOUNT_RE = re.compile(r"^\d{12}$")
_GCP_PROJECT_RE = re.compile(r"^[a-z][a-z0-9\-]{4,28}[a-z0-9]$")

# Resource IDs: alphanumeric with dashes, underscores, dots, colons, slashes
_RESOURCE_ID_RE = re.compile(r"^[\w\-.:/@]{1,512}$")

# Email: basic format check (not full RFC 5322)
_EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}$")

# Provider names
_VALID_PROVIDERS = {"aws", "gcp", "azure"}

# Severity and status values
_VALID_SEVERITIES = {"info", "warning", "critical", "emergency"}
_VALID_STATUSES = {"pending", "acknowledged", "in_progress", "resolved", "escalated"}


class ValidationError(ValueError):
    """Raised when input fails validation."""


# ---------------------------------------------------------------------------
# String sanitisation
# ---------------------------------------------------------------------------


def sanitise_string(value: str, field_name: str, max_length: int = MAX_STRING_LENGTH) -> str:
    """Sanitise a string input: strip, enforce length, remove control chars."""
    if not isinstance(value, str):
        raise ValidationError(f"{field_name}: expected string, got {type(value).__name__}")
    value = value.strip()
    if len(value) > max_length:
        raise ValidationError(f"{field_name}: exceeds maximum length of {max_length} characters")
    # Strip control characters (allow newlines and tabs in multi-line fields)
    cleaned = "".join(c for c in value if c == "\n" or c == "\t" or (ord(c) >= 32))
    return cleaned


def validate_provider(provider: str) -> str:
    """Validate a cloud provider name."""
    provider = sanitise_string(provider, "provider", max_length=32).lower()
    if provider not in _VALID_PROVIDERS:
        raise ValidationError(
            f"provider: must be one of {sorted(_VALID_PROVIDERS)}, got '{provider}'"
        )
    return provider


def validate_severity(severity: str) -> str:
    """Validate a severity level."""
    severity = sanitise_string(severity, "severity", max_length=32).lower()
    if severity not in _VALID_SEVERITIES:
        raise ValidationError(
            f"severity: must be one of {sorted(_VALID_SEVERITIES)}, got '{severity}'"
        )
    return severity


def validate_status(status: str) -> str:
    """Validate an action status."""
    status = sanitise_string(status, "status", max_length=32).lower()
    if status not in _VALID_STATUSES:
        raise ValidationError(f"status: must be one of {sorted(_VALID_STATUSES)}, got '{status}'")
    return status


def validate_account_id(account_id: str) -> str:
    """Validate an AWS account ID or GCP project ID."""
    account_id = sanitise_string(account_id, "account_id", max_length=64)
    if not account_id:
        raise ValidationError("account_id: cannot be empty")
    # Accept AWS 12-digit, GCP project-id, or generic alphanumeric identifiers
    if not (
        _AWS_ACCOUNT_RE.match(account_id)
        or _GCP_PROJECT_RE.match(account_id)
        or re.match(r"^[\w\-.:]{1,64}$", account_id)
    ):
        raise ValidationError("account_id: contains invalid characters")
    return account_id


def validate_resource_id(resource_id: str) -> str:
    """Validate a resource identifier."""
    resource_id = sanitise_string(resource_id, "resource_id", max_length=512)
    if not resource_id:
        raise ValidationError("resource_id: cannot be empty")
    if not _RESOURCE_ID_RE.match(resource_id):
        raise ValidationError("resource_id: contains invalid characters")
    return resource_id


def validate_resource_type(resource_type: str) -> str:
    """Validate a resource type string."""
    resource_type = sanitise_string(resource_type, "resource_type", max_length=128)
    if not resource_type:
        raise ValidationError("resource_type: cannot be empty")
    if not re.match(r"^[\w\-.:]{1,128}$", resource_type):
        raise ValidationError("resource_type: contains invalid characters")
    return resource_type


def validate_email(email: str) -> str:
    """Validate an email address (basic format check)."""
    if not email:
        return ""
    email = sanitise_string(email, "email", max_length=320)
    if not _EMAIL_RE.match(email):
        raise ValidationError("email: invalid format")
    return email


def validate_tags(tags: dict[str, str] | None) -> dict[str, str]:
    """Validate and sanitise a tags dictionary."""
    if tags is None:
        return {}
    if not isinstance(tags, dict):
        raise ValidationError("tags: expected dictionary")
    if len(tags) > MAX_TAGS:
        raise ValidationError(f"tags: exceeds maximum of {MAX_TAGS} tags")
    cleaned: dict[str, str] = {}
    for key, value in tags.items():
        k = sanitise_string(str(key), "tag key", max_length=MAX_TAG_KEY_LENGTH)
        v = sanitise_string(str(value), "tag value", max_length=MAX_TAG_VALUE_LENGTH)
        cleaned[k] = v
    return cleaned


def validate_query_limit(limit: int) -> int:
    """Validate a query limit parameter."""
    if not isinstance(limit, int) or limit < 1:
        raise ValidationError("limit: must be a positive integer")
    return min(limit, MAX_QUERY_LIMIT)


def validate_cost(cost: float, field_name: str = "cost") -> float:
    """Validate a cost value."""
    if not isinstance(cost, (int, float)):
        raise ValidationError(f"{field_name}: expected number")
    if cost < 0:
        raise ValidationError(f"{field_name}: cannot be negative")
    if cost > 1_000_000_000:
        raise ValidationError(f"{field_name}: exceeds maximum of $1B")
    return float(cost)


# ---------------------------------------------------------------------------
# URL / SSRF validation
# ---------------------------------------------------------------------------

# Private/internal IP ranges that should never be targets
_BLOCKED_HOSTS = {
    "localhost",
    "127.0.0.1",
    "0.0.0.0",  # noqa: S104  # nosec B104
    "::1",
    "metadata.google.internal",
    "169.254.169.254",  # Cloud metadata endpoint
}


def _is_private_ip(hostname: str) -> bool:
    """Check if a hostname resolves to a private/internal IP range."""
    try:
        addr = ipaddress.ip_address(hostname)
        return addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved
    except ValueError:
        return False


def validate_webhook_url(
    url: str,
    *,
    allowed_schemes: frozenset[str] = frozenset({"https"}),
    allow_http: bool = False,
) -> str:
    """Validate a webhook URL, blocking SSRF targets.

    By default only HTTPS is allowed. Set allow_http=True for development.
    """
    url = sanitise_string(url, "webhook_url", max_length=2048)
    if not url:
        raise ValidationError("webhook_url: cannot be empty")

    parsed = urlparse(url)

    # Scheme check
    schemes = allowed_schemes | (frozenset({"http"}) if allow_http else frozenset())
    if parsed.scheme not in schemes:
        raise ValidationError(
            f"webhook_url: scheme must be one of {sorted(schemes)}, got '{parsed.scheme}'"
        )

    # Host check
    hostname = parsed.hostname or ""
    if not hostname:
        raise ValidationError("webhook_url: missing hostname")

    hostname_lower = hostname.lower()
    if hostname_lower in _BLOCKED_HOSTS:
        raise ValidationError("webhook_url: blocked internal host")

    if _is_private_ip(hostname):
        raise ValidationError("webhook_url: private/internal IP addresses are not allowed")

    # Port check — block uncommon ports that might target internal services
    port = parsed.port
    if port is not None and port not in (80, 443, 8080, 8443):
        raise ValidationError(f"webhook_url: non-standard port {port} is not allowed")

    return url


# ---------------------------------------------------------------------------
# Path safety
# ---------------------------------------------------------------------------


def validate_safe_path(
    path: str | Path,
    *,
    base_dir: Path | None = None,
    field_name: str = "path",
) -> Path:
    """Validate a filesystem path is safe (no traversal, within base_dir).

    If base_dir is provided, the resolved path must be within it.
    """
    p = Path(path).resolve()

    # Block obvious traversal patterns in the raw input
    raw = str(path)
    if ".." in raw:
        raise ValidationError(f"{field_name}: path traversal ('..') is not allowed")

    if base_dir is not None:
        base = base_dir.resolve()
        try:
            p.relative_to(base)
        except ValueError as exc:
            raise ValidationError(f"{field_name}: path must be within {base}") from exc

    return p


# ---------------------------------------------------------------------------
# Dict depth check (prevent deeply nested payloads)
# ---------------------------------------------------------------------------


def validate_dict_depth(
    data: Any, field_name: str = "data", max_depth: int = MAX_DICT_DEPTH
) -> None:
    """Ensure a nested dict/list structure doesn't exceed max depth."""

    def _check(obj: Any, depth: int) -> None:
        if depth > max_depth:
            raise ValidationError(f"{field_name}: exceeds maximum nesting depth of {max_depth}")
        if isinstance(obj, dict):
            for v in obj.values():
                _check(v, depth + 1)
        elif isinstance(obj, list):
            for item in obj:
                _check(item, depth + 1)

    _check(data, 0)


# ---------------------------------------------------------------------------
# Sanitise error messages for external consumers
# ---------------------------------------------------------------------------


def safe_error_message(error: Exception) -> str:
    """Produce a safe error message that doesn't leak internal details.

    Strips file paths, tracebacks, and internal class names.
    """
    msg = str(error)

    # Strip absolute file paths
    msg = re.sub(r"(/[\w\-./]+\.py)", "<redacted-path>", msg)
    msg = re.sub(r"(line \d+)", "", msg)

    # Strip class/module prefixes from error messages
    msg = re.sub(r"[\w.]+Error: ", "", msg)

    # Truncate overly long messages
    if len(msg) > 500:
        msg = msg[:497] + "..."

    return msg.strip()
