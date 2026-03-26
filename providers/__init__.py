"""Cloud provider modules for FinOps Automation Hub.

Each provider (AWS, GCP) implements a common interface for:
- Listening to resource creation events via audit logs
- Estimating costs using provider-native billing APIs
- Mapping resources to cost catalogues
"""

from .base import BaseCloudProvider

__all__ = ["BaseCloudProvider"]
