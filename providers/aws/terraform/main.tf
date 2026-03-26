################################################################################
# AWS FinOps Automation Hub Infrastructure
#
# Deploys:
# - CloudTrail event monitoring for resource creation
# - EventBridge rules for cost-relevant events
# - SNS alerting with encryption
# - Amazon Bedrock Agent for FinOps governance
# - Cost Anomaly Detection monitors
# - Budget alarms
#
# Authentication: IAM roles only. No API keys, no long-lived credentials.
# Aligned with UK NCSC Secure by Design principles.
#
# Terraform >= 1.14 required. AWS provider >= 6.0.
################################################################################

terraform {
  required_version = ">= 1.14.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 6.0"
    }
  }
}

# ---------- Data Sources ----------

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

# ---------- CloudTrail for Resource Creation Monitoring ----------

resource "aws_cloudtrail" "finops_trail" {
  name                          = "${var.name_prefix}-finops-trail"
  s3_bucket_name                = aws_s3_bucket.trail_bucket.id
  include_global_service_events = true
  is_multi_region_trail         = var.multi_region
  enable_logging                = true

  cloud_watch_logs_group_arn = "${aws_cloudwatch_log_group.trail_logs.arn}:*"
  cloud_watch_logs_role_arn  = aws_iam_role.cloudtrail_cloudwatch.arn

  event_selector {
    read_write_type           = "WriteOnly"
    include_management_events = true
  }

  tags = merge(var.tags, {
    Component = "finops-automation-hub"
    Purpose   = "cost-monitoring"
  })
}

# ---------- S3 Bucket for CloudTrail Logs (Encrypted, Versioned) ----------

resource "aws_s3_bucket" "trail_bucket" {
  bucket        = "${var.name_prefix}-finops-trail-${data.aws_caller_identity.current.account_id}"
  force_destroy = false

  tags = merge(var.tags, {
    Component = "finops-automation-hub"
    Purpose   = "audit-storage"
  })
}

resource "aws_s3_bucket_versioning" "trail_bucket" {
  bucket = aws_s3_bucket.trail_bucket.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "trail_bucket" {
  bucket = aws_s3_bucket.trail_bucket.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = var.kms_key_arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "trail_bucket" {
  bucket                  = aws_s3_bucket.trail_bucket.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "trail_bucket" {
  bucket = aws_s3_bucket.trail_bucket.id
  rule {
    id     = "archive-old-logs"
    status = "Enabled"
    transition {
      days          = 90
      storage_class = "GLACIER"
    }
    expiration {
      days = var.log_retention_days
    }
  }
}

resource "aws_s3_bucket_policy" "trail_bucket" {
  bucket = aws_s3_bucket.trail_bucket.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "AWSCloudTrailAclCheck"
        Effect    = "Allow"
        Principal = { Service = "cloudtrail.amazonaws.com" }
        Action    = "s3:GetBucketAcl"
        Resource  = aws_s3_bucket.trail_bucket.arn
      },
      {
        Sid       = "AWSCloudTrailWrite"
        Effect    = "Allow"
        Principal = { Service = "cloudtrail.amazonaws.com" }
        Action    = "s3:PutObject"
        Resource  = "${aws_s3_bucket.trail_bucket.arn}/AWSLogs/${data.aws_caller_identity.current.account_id}/*"
        Condition = {
          StringEquals = { "s3:x-amz-acl" = "bucket-owner-full-control" }
        }
      },
      {
        Sid       = "DenyUnencryptedTransport"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:*"
        Resource = [
          aws_s3_bucket.trail_bucket.arn,
          "${aws_s3_bucket.trail_bucket.arn}/*"
        ]
        Condition = {
          Bool = { "aws:SecureTransport" = "false" }
        }
      }
    ]
  })
}

# ---------- CloudWatch Log Group for CloudTrail ----------

resource "aws_cloudwatch_log_group" "trail_logs" {
  name              = "/finops/${var.name_prefix}/cloudtrail"
  retention_in_days = var.log_retention_days
  kms_key_id        = var.kms_key_arn

  tags = merge(var.tags, {
    Component = "finops-automation-hub"
  })
}

# ---------- IAM Role for CloudTrail -> CloudWatch ----------

resource "aws_iam_role" "cloudtrail_cloudwatch" {
  name = "${var.name_prefix}-cloudtrail-cw-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "cloudtrail.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = var.tags
}

resource "aws_iam_role_policy" "cloudtrail_cloudwatch" {
  name = "${var.name_prefix}-cloudtrail-cw-policy"
  role = aws_iam_role.cloudtrail_cloudwatch.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "logs:CreateLogStream",
        "logs:PutLogEvents"
      ]
      Resource = "${aws_cloudwatch_log_group.trail_logs.arn}:*"
    }]
  })
}

# ---------- EventBridge Rules for Resource Creation ----------

resource "aws_cloudwatch_event_rule" "costly_resource_creation" {
  name        = "${var.name_prefix}-costly-resource-creation"
  description = "Captures resource creation events for FinOps cost monitoring"

  event_pattern = jsonencode({
    source      = ["aws.ec2", "aws.rds", "aws.eks", "aws.elasticache", "aws.redshift"]
    detail-type = ["AWS API Call via CloudTrail"]
    detail = {
      eventName = [
        "RunInstances",
        "CreateDBInstance",
        "CreateDBCluster",
        "CreateCluster",
        "CreateCacheCluster",
        "CreateReplicationGroup",
        "CreateNatGateway"
      ]
    }
  })

  tags = merge(var.tags, {
    Component = "finops-automation-hub"
  })
}

resource "aws_cloudwatch_event_target" "sns_target" {
  rule      = aws_cloudwatch_event_rule.costly_resource_creation.name
  target_id = "finops-sns"
  arn       = aws_sns_topic.finops_alerts.arn
}

# ---------- SNS Topic for Alerts ----------

resource "aws_sns_topic" "finops_alerts" {
  name              = "${var.name_prefix}-finops-alerts"
  kms_master_key_id = var.kms_key_arn

  tags = merge(var.tags, {
    Component = "finops-automation-hub"
  })
}

resource "aws_sns_topic_policy" "finops_alerts" {
  arn = aws_sns_topic.finops_alerts.arn

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "AllowEventBridge"
      Effect    = "Allow"
      Principal = { Service = "events.amazonaws.com" }
      Action    = "sns:Publish"
      Resource  = aws_sns_topic.finops_alerts.arn
    }]
  })
}

resource "aws_sns_topic_subscription" "email_alerts" {
  count     = length(var.alert_email_addresses)
  topic_arn = aws_sns_topic.finops_alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email_addresses[count.index]
}

# ---------- Amazon Bedrock Agent for FinOps Governance ----------

resource "aws_iam_role" "bedrock_agent" {
  name = "${var.name_prefix}-bedrock-finops-agent-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "bedrock.amazonaws.com" }
      Action    = "sts:AssumeRole"
      Condition = {
        StringEquals = {
          "aws:SourceAccount" = data.aws_caller_identity.current.account_id
        }
      }
    }]
  })

  tags = merge(var.tags, {
    Component = "finops-automation-hub"
    Purpose   = "bedrock-agent"
  })
}

resource "aws_iam_role_policy" "bedrock_agent_model" {
  name = "${var.name_prefix}-bedrock-agent-model-policy"
  role = aws_iam_role.bedrock_agent.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "bedrock:InvokeModel",
        "bedrock:InvokeModelWithResponseStream"
      ]
      Resource = "arn:aws:bedrock:${data.aws_region.current.name}::foundation-model/${var.bedrock_model_id}"
    }]
  })
}

resource "aws_iam_role_policy" "bedrock_agent_finops" {
  name = "${var.name_prefix}-bedrock-agent-finops-policy"
  role = aws_iam_role.bedrock_agent.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "CostExplorerReadOnly"
        Effect = "Allow"
        Action = [
          "ce:GetCostAndUsage",
          "ce:GetCostForecast",
          "ce:GetAnomalies",
          "ce:GetAnomalyMonitors",
          "ce:GetSavingsPlansUtilization",
          "ce:GetReservationUtilization",
          "ce:GetRightsizingRecommendation"
        ]
        Resource = "*"
      },
      {
        Sid    = "BudgetsReadOnly"
        Effect = "Allow"
        Action = [
          "budgets:DescribeBudgets",
          "budgets:ViewBudget"
        ]
        Resource = "*"
      },
      {
        Sid    = "TaggingReadOnly"
        Effect = "Allow"
        Action = [
          "tag:GetResources",
          "tag:GetTagKeys",
          "tag:GetTagValues"
        ]
        Resource = "*"
      },
      {
        Sid    = "CloudWatchReadOnly"
        Effect = "Allow"
        Action = [
          "cloudwatch:GetMetricData",
          "cloudwatch:DescribeAlarms",
          "logs:FilterLogEvents"
        ]
        Resource = "*"
      }
    ]
  })
}

resource "aws_bedrockagent_agent" "finops" {
  agent_name              = "${var.name_prefix}-finops-governance"
  agent_resource_role_arn  = aws_iam_role.bedrock_agent.arn
  foundation_model        = var.bedrock_model_id
  idle_session_ttl_in_seconds = 600
  description             = "FinOps governance agent — monitors costs, detects anomalies, enforces tagging, recommends optimisations"

  instruction = <<-EOT
    You are an AWS FinOps governance agent. Your role is to:
    1. MONITOR: Analyse AWS Cost Explorer data to identify spending trends.
    2. DETECT: Find cost anomalies using AWS Cost Anomaly Detection.
    3. ENFORCE: Check tag compliance (Team, CostCentre, Environment, Owner).
    4. RECOMMEND: Surface rightsizing and Savings Plan recommendations.
    5. REPORT: Generate human-readable reports requiring no further investigation.

    When generating alerts or reports:
    - Always include WHO is accountable (resource creator/owner).
    - Always include the COST IMPACT (exact USD figures).
    - Always include WHAT TO DO NEXT (prioritised action list).
    - Always include the ESCALATION PATH if action is not taken.
    All actions are audited for governance compliance.
  EOT

  tags = merge(var.tags, {
    Component = "finops-automation-hub"
  })
}

# ---------- Cost Anomaly Detection ----------

resource "aws_ce_anomaly_monitor" "finops" {
  name              = "${var.name_prefix}-finops-anomaly-monitor"
  monitor_type      = "DIMENSIONAL"
  monitor_dimension = "SERVICE"

  tags = merge(var.tags, {
    Component = "finops-automation-hub"
  })
}

resource "aws_ce_anomaly_subscription" "finops" {
  name = "${var.name_prefix}-finops-anomaly-subscription"

  frequency = "DAILY"

  monitor_arn_list = [aws_ce_anomaly_monitor.finops.arn]

  subscriber {
    type    = "SNS"
    address = aws_sns_topic.finops_alerts.arn
  }

  threshold_expression {
    dimension {
      key           = "ANOMALY_TOTAL_IMPACT_ABSOLUTE"
      values        = [tostring(var.anomaly_threshold_usd)]
      match_options = ["GREATER_THAN_OR_EQUAL"]
    }
  }

  tags = merge(var.tags, {
    Component = "finops-automation-hub"
  })
}

# ---------- AWS Budget Alarm ----------

resource "aws_budgets_budget" "monthly_cost" {
  name         = "${var.name_prefix}-monthly-budget"
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = var.alert_email_addresses
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = var.alert_email_addresses
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = var.alert_email_addresses
  }
}

# ---------- OIDC Provider for GitHub Actions (keyless CI/CD) ----------

resource "aws_iam_openid_connect_provider" "github" {
  count = var.enable_github_oidc ? 1 : 0

  url             = "https://token.actions.githubusercontent.com"
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = ["6938fd4d98bab03faadb97b34396831e3780aea1"]

  tags = merge(var.tags, {
    Component = "finops-automation-hub"
    Purpose   = "github-oidc"
  })
}

resource "aws_iam_role" "github_actions" {
  count = var.enable_github_oidc ? 1 : 0

  name = "${var.name_prefix}-github-actions-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Federated = aws_iam_openid_connect_provider.github[0].arn }
      Action    = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = {
          "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
        }
        StringLike = {
          "token.actions.githubusercontent.com:sub" = "repo:${var.github_repository}:*"
        }
      }
    }]
  })

  tags = var.tags
}
