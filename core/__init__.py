"""FinOps Automation Hub - Core Engine.

Provides cost analysis, threshold calculation, alert generation,
audit logging, and policy management aligned with UK NCSC Secure by Design.

Public API:
    Models:        CloudProvider, ResourceCreationEvent, CostAlert, CostPolicy, etc.
    Event Store:   BaseEventStore, InMemoryEventStore, SQLiteEventStore
    Notifications: BaseNotificationDispatcher, WebhookDispatcher, SlackDispatcher, etc.
    Config:        HubConfig
    Pricing:       BasePricingService, LocalPricingService, CachedPricingService
    Validation:    ValidationError, validate_*, sanitise_*
    Audit:         AuditLogger
    Policies:      PolicyEngine
    Alerts:        AlertEngine
    Thresholds:    ThresholdEngine
"""

__version__ = "0.3.0"

# Explicit imports so static analysis tools (CodeQL, mypy, pyright) can resolve them
from core.alert_store import BaseAlertStore, InMemoryAlertStore, SQLiteAlertStore
from core.alerts import AlertEngine
from core.audit import AuditLogger
from core.config import HubConfig
from core.event_store import BaseEventStore, InMemoryEventStore, SQLiteEventStore
from core.lifecycle import AgentLifecycle, AgentState
from core.logging_config import configure_logging
from core.policies import PolicyEngine
from core.thresholds import ThresholdEngine

__all__ = [
    # Re-exported for convenience -- canonical definitions live in submodules
    "AgentLifecycle",
    "AgentState",
    "AlertEngine",
    "AuditLogger",
    "BaseAlertStore",
    "BaseEventStore",
    "HubConfig",
    "InMemoryAlertStore",
    "InMemoryEventStore",
    "PolicyEngine",
    "SQLiteAlertStore",
    "SQLiteEventStore",
    "ThresholdEngine",
    "__version__",
    "configure_logging",
]
