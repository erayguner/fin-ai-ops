"""Configuration provider abstraction.

Externalises all hardcoded settings into a layered configuration system:
  1. Built-in defaults (always available)
  2. YAML config file (hub_config.yaml)
  3. Environment variables (highest priority)

Any component can read configuration without knowing where it comes from.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["HubConfig"]

# Default configuration — used when no config file or env var is set.
_DEFAULTS: dict[str, Any] = {
    # ── General ──
    "hub.version": "0.3.0",
    "hub.audit_dir": "audit_store",
    "hub.policy_dir": "policies",
    "hub.event_store_backend": "memory",
    "hub.event_store_path": "events.db",
    # ── Thresholds (USD/month) ──
    "thresholds.anomaly_multiplier": 2.0,
    "thresholds.min_datapoints": 3,
    "thresholds.defaults.ec2:instance": {"warning": 500, "critical": 2000, "emergency": 5000},
    "thresholds.defaults.rds:db": {"warning": 800, "critical": 3000, "emergency": 8000},
    "thresholds.defaults.rds:cluster": {"warning": 800, "critical": 3000, "emergency": 8000},
    "thresholds.defaults.eks:cluster": {"warning": 2000, "critical": 5000, "emergency": 15000},
    "thresholds.defaults.s3:bucket": {"warning": 200, "critical": 1000, "emergency": 3000},
    "thresholds.defaults.lambda:function": {"warning": 100, "critical": 500, "emergency": 2000},
    "thresholds.defaults.elasticache:cluster": {
        "warning": 500,
        "critical": 2000,
        "emergency": 5000,
    },
    "thresholds.defaults.redshift:cluster": {"warning": 1000, "critical": 5000, "emergency": 15000},
    "thresholds.defaults.nat_gateway": {"warning": 300, "critical": 1000, "emergency": 3000},
    "thresholds.defaults.compute.instances": {"warning": 500, "critical": 2000, "emergency": 5000},
    "thresholds.defaults.cloudsql.instances": {"warning": 800, "critical": 3000, "emergency": 8000},
    "thresholds.defaults.container.clusters": {
        "warning": 2000,
        "critical": 5000,
        "emergency": 15000,
    },
    "thresholds.defaults.storage.buckets": {"warning": 200, "critical": 1000, "emergency": 3000},
    "thresholds.defaults.cloudfunctions.functions": {
        "warning": 100,
        "critical": 500,
        "emergency": 2000,
    },
    "thresholds.defaults.bigquery.datasets": {"warning": 500, "critical": 2000, "emergency": 8000},
    "thresholds.defaults.redis.instances": {"warning": 500, "critical": 2000, "emergency": 5000},
    # ── New resource types ──
    "thresholds.defaults.ebs:volume": {"warning": 100, "critical": 500, "emergency": 2000},
    "thresholds.defaults.elb:load-balancer": {"warning": 200, "critical": 800, "emergency": 3000},
    "thresholds.defaults.compute.disks": {"warning": 100, "critical": 500, "emergency": 2000},
    "thresholds.defaults.compute.addresses": {"warning": 50, "critical": 200, "emergency": 500},
    "thresholds.defaults.run.services": {"warning": 100, "critical": 500, "emergency": 2000},
    "thresholds.defaults.dataflow.jobs": {"warning": 500, "critical": 2000, "emergency": 8000},
    # ── Tags ──
    "tags.required": ["team", "cost-centre", "environment", "owner"],
    "tags.case_sensitive": False,
    # ── Escalation timeframes ──
    "escalation.emergency": "1 hour",
    "escalation.critical": "24 hours",
    "escalation.warning": "7 days",
    "escalation.info": "next review cycle",
    # ── Notifications ──
    "notifications.channels": [],
    # ── Monitoring ──
    "monitoring.poll_interval_seconds": 900,
    # ── Pricing ──
    "pricing.default_region.aws": "eu-west-2",
    "pricing.default_region.gcp": "europe-west2",
    "pricing.cache_ttl_hours": 24,
}

# Mapping from env vars to config keys
_ENV_MAP: dict[str, str] = {
    "FINOPS_AUDIT_DIR": "hub.audit_dir",
    "FINOPS_POLICY_DIR": "hub.policy_dir",
    "FINOPS_EVENT_STORE": "hub.event_store_backend",
    "FINOPS_EVENT_STORE_PATH": "hub.event_store_path",
    "FINOPS_ANOMALY_MULTIPLIER": "thresholds.anomaly_multiplier",
    "FINOPS_POLL_INTERVAL": "monitoring.poll_interval_seconds",
    "FINOPS_REQUIRED_TAGS": "tags.required",
    "FINOPS_AWS_REGION": "pricing.default_region.aws",
    "FINOPS_GCP_REGION": "pricing.default_region.gcp",
    "FINOPS_SLACK_WEBHOOK": "notifications.slack_webhook",
    "FINOPS_PAGERDUTY_KEY": "notifications.pagerduty_routing_key",
    "FINOPS_WEBHOOK_URL": "notifications.webhook_url",
    "FINOPS_AWS_REGIONS": "aws.regions",
    "FINOPS_GCP_PROJECTS": "gcp.projects",
}


class HubConfig:
    """Layered configuration: defaults → YAML file → env vars."""

    def __init__(self, config_path: str | Path | None = None) -> None:
        self._data: dict[str, Any] = dict(_DEFAULTS)
        if config_path:
            self._load_yaml(Path(config_path))
        else:
            # Try default location
            default_path = Path("hub_config.yaml")
            if default_path.exists():
                self._load_yaml(default_path)
        self._apply_env_overrides()

    def _load_yaml(self, path: Path) -> None:
        """Load config from a YAML file (optional dependency).

        Parse errors are logged and skipped so the system starts with
        defaults rather than crashing on a malformed config file.
        """
        try:
            import yaml

            with path.open() as f:
                data = yaml.safe_load(f) or {}
            if not isinstance(data, dict):
                logger.warning("Config file %s did not parse to a dict; ignoring", path)
                return
            self._merge(data)
            logger.info("Loaded config from %s", path)
        except ImportError:
            # YAML not installed — try JSON fallback
            if path.suffix in (".json",):
                try:
                    import json

                    with path.open() as f:
                        data = json.load(f)
                    self._merge(data)
                except (json.JSONDecodeError, ValueError) as e:
                    logger.warning("Config file %s has invalid JSON: %s", path, e)
            else:
                logger.debug("PyYAML not installed; skipping %s", path)
        except FileNotFoundError:
            logger.debug("Config file not found: %s", path)
        except Exception as e:
            logger.warning("Failed to load config from %s: %s", path, e)

    def _merge(self, data: dict[str, Any], prefix: str = "") -> None:
        """Recursively merge a dict into the flat config store."""
        for key, value in data.items():
            full_key = f"{prefix}.{key}" if prefix else key
            if isinstance(value, dict) and not self._is_threshold_dict(value):
                self._merge(value, full_key)
            else:
                self._data[full_key] = value

    @staticmethod
    def _is_threshold_dict(d: dict) -> bool:
        """Detect threshold dicts like {"warning": 500, "critical": 2000, ...}."""
        return set(d.keys()) <= {"warning", "critical", "emergency"}

    def _apply_env_overrides(self) -> None:
        for env_var, config_key in _ENV_MAP.items():
            value = os.environ.get(env_var)
            if value is not None:
                self._data[config_key] = self._coerce(config_key, value)

    def _coerce(self, key: str, value: str) -> Any:
        """Coerce string env values to the correct type.

        Returns the raw string if coercion fails, so a bad env var
        doesn't crash the system at startup.
        """
        existing = self._data.get(key)
        if isinstance(existing, bool):
            return value.lower() in ("true", "1", "yes")
        if isinstance(existing, int):
            try:
                return int(value)
            except ValueError:
                logger.warning("Env var for '%s' is not a valid int: %r", key, value)
                return existing
        if isinstance(existing, float):
            try:
                return float(value)
            except ValueError:
                logger.warning("Env var for '%s' is not a valid float: %r", key, value)
                return existing
        if isinstance(existing, list):
            return [v.strip() for v in value.split(",")]
        return value

    def get(self, key: str, default: Any = None) -> Any:
        """Get a config value by dotted key."""
        return self._data.get(key, default)

    def get_str(self, key: str, default: str = "") -> str:
        return str(self._data.get(key, default))

    def get_int(self, key: str, default: int = 0) -> int:
        return int(self._data.get(key, default))

    def get_float(self, key: str, default: float = 0.0) -> float:
        return float(self._data.get(key, default))

    def get_list(self, key: str, default: list | None = None) -> list:
        val = self._data.get(key, default or [])
        return val if isinstance(val, list) else [val]

    def get_threshold_defaults(self) -> dict[str, dict[str, float]]:
        """Return all threshold defaults as {resource_type: {warning, critical, emergency}}."""
        prefix = "thresholds.defaults."
        return {
            key[len(prefix) :]: value
            for key, value in self._data.items()
            if key.startswith(prefix) and isinstance(value, dict)
        }

    def get_escalation_timeframe(self, severity: str) -> str:
        return self.get_str(f"escalation.{severity}", "7 days")

    def get_required_tags(self) -> list[str]:
        return self.get_list("tags.required", ["team", "cost-centre", "environment", "owner"])

    def set(self, key: str, value: Any) -> None:
        """Set a config value (runtime override)."""
        self._data[key] = value

    def as_dict(self) -> dict[str, Any]:
        """Return the full config as a flat dict."""
        return dict(self._data)
