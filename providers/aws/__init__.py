"""AWS Provider Module for FinOps Automation Hub.

Uses AWS's native agent and MCP ecosystem:
- Amazon Bedrock Agents for agent orchestration
- AWS MCP servers for Cost Explorer, CloudWatch, CloudFormation
- IAM roles for authentication (no API keys, no access key IDs)
- Bedrock AgentCore for MCP gateway integration

No API keys or long-lived credentials. Authentication via IAM roles only.
"""

from .cost_analyzer import AWSCostAnalyzer
from .listener import AWSEventListener
from .resources import AWS_RESOURCE_CATALOGUE

__all__ = ["AWS_RESOURCE_CATALOGUE", "AWSCostAnalyzer", "AWSEventListener"]
