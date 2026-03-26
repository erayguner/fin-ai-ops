"""Base provider interface.

All cloud providers must implement this interface to be pluggable
into the FinOps Automation Hub. This ensures consistent behaviour
across AWS, GCP, and any future providers.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from core.models import CloudProvider, ResourceCreationEvent

__all__ = ["BaseCloudProvider"]


class BaseCloudProvider(ABC):
    """Abstract base class for cloud provider integrations."""

    @property
    @abstractmethod
    def provider_name(self) -> CloudProvider:
        """Return the provider enum value."""

    @abstractmethod
    def listen_for_events(self, config: dict[str, Any]) -> list[ResourceCreationEvent]:
        """Poll or receive resource creation events from provider audit logs.

        Args:
            config: Provider-specific configuration (regions, filters, etc.)

        Returns:
            List of new resource creation events since last poll.
        """

    @abstractmethod
    def estimate_monthly_cost(self, resource_type: str, resource_config: dict[str, Any]) -> float:
        """Estimate the monthly cost of a resource based on its configuration.

        Args:
            resource_type: Provider-specific resource type identifier.
            resource_config: Resource specification (size, storage, etc.)

        Returns:
            Estimated monthly cost in USD.
        """

    @abstractmethod
    def get_resource_tags(self, resource_id: str, resource_type: str) -> dict[str, str]:
        """Retrieve tags/labels for a specific resource.

        Args:
            resource_id: The provider-specific resource identifier.
            resource_type: The resource type.

        Returns:
            Dictionary of tag key-value pairs.
        """

    @abstractmethod
    def get_creator_identity(self, event_data: dict[str, Any]) -> tuple[str, str]:
        """Extract the creator's identity from a raw event.

        Args:
            event_data: Raw audit log event from the provider.

        Returns:
            Tuple of (principal_identity, email_address).
        """

    @abstractmethod
    def get_resource_details(self, resource_id: str, resource_type: str) -> dict[str, Any]:
        """Get detailed information about a specific resource.

        Args:
            resource_id: The provider-specific resource identifier.
            resource_type: The resource type.

        Returns:
            Dictionary of resource details.
        """

    @abstractmethod
    def validate_credentials(self) -> bool:
        """Validate that the provider credentials are configured and working.

        Returns:
            True if credentials are valid.
        """
