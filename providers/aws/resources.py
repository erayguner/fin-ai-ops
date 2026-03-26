"""AWS resource catalogue for cost classification.

Maps AWS resource types to their cost characteristics, helping the
hub understand which resources are most likely to generate significant costs.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["AWS_RESOURCE_CATALOGUE", "AWSResourceInfo"]


@dataclass(frozen=True)
class AWSResourceInfo:
    """Metadata about an AWS resource type for cost analysis."""

    resource_type: str
    service: str
    display_name: str
    cost_driver: str
    typical_monthly_range_usd: tuple[float, float]
    requires_approval_default: bool


AWS_RESOURCE_CATALOGUE: dict[str, AWSResourceInfo] = {
    "ec2:instance": AWSResourceInfo(
        resource_type="ec2:instance",
        service="EC2",
        display_name="EC2 Instance",
        cost_driver="Instance type x hours running",
        typical_monthly_range_usd=(9.0, 25000.0),
        requires_approval_default=False,
    ),
    "rds:db": AWSResourceInfo(
        resource_type="rds:db",
        service="RDS",
        display_name="RDS Database Instance",
        cost_driver="Instance class x hours + storage",
        typical_monthly_range_usd=(15.0, 12000.0),
        requires_approval_default=True,
    ),
    "eks:cluster": AWSResourceInfo(
        resource_type="eks:cluster",
        service="EKS",
        display_name="EKS Kubernetes Cluster",
        cost_driver="Control plane + node instances",
        typical_monthly_range_usd=(73.0, 20000.0),
        requires_approval_default=True,
    ),
    "s3:bucket": AWSResourceInfo(
        resource_type="s3:bucket",
        service="S3",
        display_name="S3 Bucket",
        cost_driver="Storage volume + requests + transfer",
        typical_monthly_range_usd=(0.0, 5000.0),
        requires_approval_default=False,
    ),
    "lambda:function": AWSResourceInfo(
        resource_type="lambda:function",
        service="Lambda",
        display_name="Lambda Function",
        cost_driver="Invocations x duration x memory",
        typical_monthly_range_usd=(0.0, 3000.0),
        requires_approval_default=False,
    ),
    "nat_gateway": AWSResourceInfo(
        resource_type="nat_gateway",
        service="VPC",
        display_name="NAT Gateway",
        cost_driver="Hourly + data processed",
        typical_monthly_range_usd=(35.0, 5000.0),
        requires_approval_default=False,
    ),
    "elasticache:cluster": AWSResourceInfo(
        resource_type="elasticache:cluster",
        service="ElastiCache",
        display_name="ElastiCache Cluster",
        cost_driver="Node type x node count x hours",
        typical_monthly_range_usd=(12.0, 8000.0),
        requires_approval_default=True,
    ),
    "redshift:cluster": AWSResourceInfo(
        resource_type="redshift:cluster",
        service="Redshift",
        display_name="Redshift Data Warehouse",
        cost_driver="Node type x node count x hours",
        typical_monthly_range_usd=(182.0, 20000.0),
        requires_approval_default=True,
    ),
    "ebs:volume": AWSResourceInfo(
        resource_type="ebs:volume",
        service="EBS",
        display_name="EBS Volume",
        cost_driver="Volume size x type x IOPS",
        typical_monthly_range_usd=(1.0, 2000.0),
        requires_approval_default=False,
    ),
    "elb:load-balancer": AWSResourceInfo(
        resource_type="elb:load-balancer",
        service="ELB",
        display_name="Application Load Balancer",
        cost_driver="Hourly + LCU usage",
        typical_monthly_range_usd=(18.0, 3000.0),
        requires_approval_default=False,
    ),
    "dynamodb:table": AWSResourceInfo(
        resource_type="dynamodb:table",
        service="DynamoDB",
        display_name="DynamoDB Table",
        cost_driver="Read/write capacity + storage",
        typical_monthly_range_usd=(1.0, 5000.0),
        requires_approval_default=False,
    ),
    "sqs:queue": AWSResourceInfo(
        resource_type="sqs:queue",
        service="SQS",
        display_name="SQS Queue",
        cost_driver="Number of requests",
        typical_monthly_range_usd=(0.0, 500.0),
        requires_approval_default=False,
    ),
    "sns:topic": AWSResourceInfo(
        resource_type="sns:topic",
        service="SNS",
        display_name="SNS Topic",
        cost_driver="Number of notifications + delivery",
        typical_monthly_range_usd=(0.0, 500.0),
        requires_approval_default=False,
    ),
    "cloudfront:distribution": AWSResourceInfo(
        resource_type="cloudfront:distribution",
        service="CloudFront",
        display_name="CloudFront Distribution",
        cost_driver="Data transfer + requests",
        typical_monthly_range_usd=(0.0, 10000.0),
        requires_approval_default=False,
    ),
}
