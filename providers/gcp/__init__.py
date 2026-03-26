"""GCP Provider Module for FinOps Automation Hub.

Uses Google's native agent and MCP ecosystem:
- Google ADK (Agent Development Kit) for agent orchestration
- Google Cloud MCP servers for BigQuery and Resource Manager
- Workload Identity Federation for keyless authentication
- Vertex AI Agent Engine for production deployment

No API keys. Authentication via ADC (dev) or WIF (production).
"""

from .cost_analyzer import GCPCostAnalyzer
from .listener import GCPEventListener
from .resources import GCP_RESOURCE_CATALOGUE

__all__ = ["GCP_RESOURCE_CATALOGUE", "GCPCostAnalyzer", "GCPEventListener"]
