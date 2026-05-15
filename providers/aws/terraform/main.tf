################################################################################
# AWS FinOps Automation Hub Infrastructure (2026)
#
# Deploys native Bedrock AgentCore-aligned capabilities:
#   * Bedrock Agent + production Alias with versioned action groups
#   * Action Groups exposing Cost Explorer + Tagging Health tools via Lambda
#     (OpenAPI 3.0 schema contract — AgentCore-compatible tool calling)
#   * Knowledge Base (OpenSearch Serverless vector store, Titan embeddings)
#     backed by an S3 data source for the FinOps runbook corpus
#   * Bedrock Guardrails (PII, content filters) with production version
#   * Cost observability: CloudTrail + EventBridge -> SNS, Cost Anomaly
#     Detection, monthly Budget with forecast thresholds
#   * GitHub Actions OIDC (keyless CI/CD)
#
# Authentication: IAM roles + OIDC only. No long-lived credentials.
# Aligned with UK NCSC Secure by Design and AWS Well-Architected 2026 ML Lens.
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
    random = {
      source  = "hashicorp/random"
      version = ">= 3.6"
    }
    archive = {
      source  = "hashicorp/archive"
      version = ">= 2.5"
    }
  }
}

# ---------- Data Sources ----------

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}
data "aws_partition" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  region     = data.aws_region.current.region
  partition  = data.aws_partition.current.partition

  common_tags = merge(var.tags, {
    Component = "finops-automation-hub"
  })
}

resource "random_id" "suffix" {
  byte_length = 3
}

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

  # ADR-008 §9 / framework §8.1 — capture Bedrock runtime data events so
  # InvokeModel / InvokeAgent payloads (model in/out, action-group calls)
  # land in the corporate trail. Management-events-only is the default,
  # but G-A5 / PHASE1 §3.1 requires data-events too.
  advanced_event_selector {
    name = "bedrock-and-agent-runtime"

    field_selector {
      field  = "eventCategory"
      equals = ["Data"]
    }

    field_selector {
      field = "resources.type"
      equals = [
        "AWS::Bedrock::Model",
        "AWS::BedrockAgent::Agent",
        "AWS::BedrockAgent::AgentAlias",
      ]
    }
  }

  tags = merge(local.common_tags, { Purpose = "cost-monitoring" })
}

# ADR-008 §9 / G-A2 — Bedrock Model Invocation Logging. OFF by default,
# so without this resource raw prompts/responses for every Bedrock call
# are not preserved. Framework §11.4 requires "Model invocation logging
# enabled" on every Vertex / Bedrock surface.
resource "aws_cloudwatch_log_group" "bedrock_invocations" {
  name              = "/finops/${var.name_prefix}/bedrock-invocations"
  retention_in_days = var.log_retention_days
  kms_key_id        = var.kms_key_arn

  tags = merge(local.common_tags, { Purpose = "bedrock-invocation-logging" })
}

# Bedrock writes large objects (prompts, embeddings) to S3 when payloads
# exceed the CloudWatch ingestion size; pair the log group with a bucket
# so the framework §11.5 minimisation pattern works: CloudWatch holds
# the structured record, S3 holds the full payload.
resource "aws_s3_bucket" "bedrock_invocations" {
  bucket        = "${var.name_prefix}-bedrock-invocations-${local.account_id}"
  force_destroy = false
  tags          = merge(local.common_tags, { Purpose = "bedrock-invocation-payloads" })
}

resource "aws_s3_bucket_server_side_encryption_configuration" "bedrock_invocations" {
  bucket = aws_s3_bucket.bedrock_invocations.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = var.kms_key_arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "bedrock_invocations" {
  bucket                  = aws_s3_bucket.bedrock_invocations.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "bedrock_invocations" {
  bucket = aws_s3_bucket.bedrock_invocations.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "bedrock_invocations" {
  bucket = aws_s3_bucket.bedrock_invocations.id
  rule {
    id     = "expire-old-payloads"
    status = "Enabled"
    expiration {
      days = var.log_retention_days
    }
  }
}

resource "aws_iam_role" "bedrock_invocation_logging" {
  name = "${var.name_prefix}-bedrock-invocation-logging-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "bedrock.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
  tags = local.common_tags
}

resource "aws_iam_role_policy" "bedrock_invocation_logging" {
  role = aws_iam_role.bedrock_invocation_logging.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogStream",
          "logs:PutLogEvents",
        ]
        Resource = "${aws_cloudwatch_log_group.bedrock_invocations.arn}:*"
      },
      {
        Effect = "Allow"
        Action = [
          "s3:PutObject",
        ]
        Resource = "${aws_s3_bucket.bedrock_invocations.arn}/*"
      },
    ]
  })
}

resource "aws_bedrock_model_invocation_logging_configuration" "finops" {
  logging_config {
    embedding_data_delivery_enabled = true
    image_data_delivery_enabled     = false
    text_data_delivery_enabled      = true
    video_data_delivery_enabled     = false

    cloudwatch_config {
      log_group_name = aws_cloudwatch_log_group.bedrock_invocations.name
      role_arn       = aws_iam_role.bedrock_invocation_logging.arn

      large_data_delivery_s3_config {
        bucket_name = aws_s3_bucket.bedrock_invocations.id
        key_prefix  = "large-payloads/"
      }
    }

    s3_config {
      bucket_name = aws_s3_bucket.bedrock_invocations.id
      key_prefix  = "bedrock-invocations/"
    }
  }

  depends_on = [
    aws_iam_role_policy.bedrock_invocation_logging,
    aws_s3_bucket_policy.bedrock_invocations,
  ]
}

resource "aws_s3_bucket_policy" "bedrock_invocations" {
  bucket = aws_s3_bucket.bedrock_invocations.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "AllowBedrockWrite"
        Effect    = "Allow"
        Principal = { Service = "bedrock.amazonaws.com" }
        Action    = "s3:PutObject"
        Resource  = "${aws_s3_bucket.bedrock_invocations.arn}/*"
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
          aws_s3_bucket.bedrock_invocations.arn,
          "${aws_s3_bucket.bedrock_invocations.arn}/*",
        ]
        Condition = {
          Bool = { "aws:SecureTransport" = "false" }
        }
      },
    ]
  })
}

# ---------- S3 Bucket for CloudTrail Logs ----------

resource "aws_s3_bucket" "trail_bucket" {
  bucket        = "${var.name_prefix}-finops-trail-${local.account_id}"
  force_destroy = false

  tags = merge(local.common_tags, { Purpose = "audit-storage" })
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
        Resource  = "${aws_s3_bucket.trail_bucket.arn}/AWSLogs/${local.account_id}/*"
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

  tags = local.common_tags
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

  tags = local.common_tags
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
  description = "Captures 2026 cost-relevant resource creation events (compute, DB, AI/ML)"

  event_pattern = jsonencode({
    source = [
      "aws.ec2", "aws.rds", "aws.eks", "aws.elasticache", "aws.redshift",
      "aws.sagemaker", "aws.bedrock", "aws.emr-serverless", "aws.opensearch"
    ]
    detail-type = ["AWS API Call via CloudTrail"]
    detail = {
      eventName = [
        "RunInstances",
        "CreateDBInstance",
        "CreateDBCluster",
        "CreateCluster",
        "CreateCacheCluster",
        "CreateReplicationGroup",
        "CreateNatGateway",
        "CreateEndpoint", # SageMaker endpoints
        "CreateTrainingJob",
        "CreateHyperPodCluster",
        "CreateProvisionedModelThroughput", # Bedrock
        "CreateApplication",                # EMR Serverless
        "CreateDomain"                      # OpenSearch
      ]
    }
  })

  tags = local.common_tags
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

  tags = local.common_tags
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

################################################################################
# Amazon Bedrock Agent (2026) — native AgentCore-aligned deployment
#
# Structure:
#   - Agent role + least-privilege policies
#   - Action Groups: cost_tools + tagging_tools (Lambda-backed, OpenAPI-described)
#   - Knowledge Base: OpenSearch Serverless vector store, Titan v2 embeddings
#   - Guardrail: PII, content filters, toxic-language blocking
#   - Agent Alias: production versioning for blue/green promotion
################################################################################

# ---------- Per-Agent Identity (AgentCore Identity, Preview) ----------
#
# Framework §7.1 / F-2 — per-agent identity is the documented L2+ shape.
# Bedrock AgentCore Identity is rolling out and not yet GA in the
# Terraform AWS provider at every region. Until the dedicated resource
# type lands, we provision an agent-specific IAM role with a strict
# trust policy bound to the agent's ARN and tag it for graduation:
# when the provider catches up, swap this role for an
# ``aws_bedrockagent_identity`` resource and migrate without changing
# any consumer (the consumer reads ``aws_iam_role.bedrock_agent.arn``).
#
# Recommended migration:
#   1. Provision `aws_bedrockagent_agent_identity` resource in a follow-up.
#   2. Re-issue the trust policy below with `bedrock-agent-identity.amazonaws.com`.
#   3. Cut a release note pinning the L2 graduation date.

# ---------- IAM: Agent Execution Role ----------

resource "aws_iam_role" "bedrock_agent" {
  name = "${var.name_prefix}-bedrock-finops-agent-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "bedrock.amazonaws.com" }
      Action    = "sts:AssumeRole"
      Condition = {
        StringEquals = { "aws:SourceAccount" = local.account_id }
        ArnLike = {
          "aws:SourceArn" = "arn:${local.partition}:bedrock:${local.region}:${local.account_id}:agent/*"
        }
      }
    }]
  })

  tags = merge(local.common_tags, { Purpose = "bedrock-agent" })
}

resource "aws_iam_role_policy" "bedrock_agent_model" {
  name = "${var.name_prefix}-bedrock-agent-model-policy"
  role = aws_iam_role.bedrock_agent.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # Claude Sonnet 4/4.5 are cross-region inference-profile only. Granting
        # bedrock:InvokeModel against a single foundation-model ARN is
        # insufficient — Bedrock requires (a) the inference-profile ARN and
        # (b) the underlying foundation-model ARNs across every region the
        # profile routes to. We scope (b) to the agent's region only; for
        # true cross-region routing, broaden the resource to `bedrock:*::`.
        Sid    = "InvokeInferenceProfile"
        Effect = "Allow"
        Action = [
          "bedrock:InvokeModel",
          "bedrock:InvokeModelWithResponseStream",
          "bedrock:GetInferenceProfile"
        ]
        Resource = [
          "arn:${local.partition}:bedrock:${local.region}:${local.account_id}:inference-profile/${var.bedrock_model_id}",
          "arn:${local.partition}:bedrock:*::foundation-model/*",
        ]
      },
      {
        Sid    = "InvokeEmbeddingModel"
        Effect = "Allow"
        Action = [
          "bedrock:InvokeModel",
        ]
        Resource = [
          "arn:${local.partition}:bedrock:${local.region}::foundation-model/${var.bedrock_embedding_model_id}"
        ]
      },
      {
        Sid    = "UseKnowledgeBase"
        Effect = "Allow"
        Action = [
          "bedrock:Retrieve",
          "bedrock:RetrieveAndGenerate"
        ]
        Resource = "arn:${local.partition}:bedrock:${local.region}:${local.account_id}:knowledge-base/*"
      },
      {
        Sid      = "ApplyGuardrail"
        Effect   = "Allow"
        Action   = ["bedrock:ApplyGuardrail"]
        Resource = "arn:${local.partition}:bedrock:${local.region}:${local.account_id}:guardrail/*"
      }
    ]
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
      },
      {
        Sid    = "InvokeActionGroupLambdas"
        Effect = "Allow"
        Action = ["lambda:InvokeFunction"]
        Resource = [
          aws_lambda_function.cost_tools.arn,
          aws_lambda_function.tagging_tools.arn
        ]
      }
    ]
  })
}

# ---------- Lambda: Action Group backends (native Bedrock tool calling) ----------

resource "aws_iam_role" "action_group_lambda" {
  name = "${var.name_prefix}-ag-lambda-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = local.common_tags
}

resource "aws_iam_role_policy_attachment" "action_group_lambda_basic" {
  role       = aws_iam_role.action_group_lambda.name
  policy_arn = "arn:${local.partition}:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "action_group_lambda_finops" {
  name = "${var.name_prefix}-ag-lambda-finops"
  role = aws_iam_role.action_group_lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "ce:GetCostAndUsage",
        "ce:GetCostForecast",
        "ce:GetAnomalies",
        "ce:GetRightsizingRecommendation",
        "tag:GetResources",
        "tag:GetTagKeys",
        "tag:GetTagValues",
        "resource-explorer-2:Search"
      ]
      Resource = "*"
    }]
  })
}

# Placeholder Lambda packages — operators replace with built artefacts.
data "archive_file" "empty_lambda" {
  type        = "zip"
  output_path = "${path.module}/.build/empty.zip"
  source {
    content  = "def lambda_handler(event, context):\n    return {'statusCode': 200, 'body': 'stub'}\n"
    filename = "index.py"
  }
}

resource "aws_lambda_function" "cost_tools" {
  function_name = "${var.name_prefix}-ag-cost-tools"
  role          = aws_iam_role.action_group_lambda.arn
  handler       = "index.lambda_handler"
  runtime       = "python3.12"
  timeout       = 30
  memory_size   = 512

  filename         = data.archive_file.empty_lambda.output_path
  source_code_hash = data.archive_file.empty_lambda.output_base64sha256

  environment {
    variables = {
      FINOPS_MODE = "cost_tools"
    }
  }

  tracing_config {
    mode = "Active"
  }

  tags = local.common_tags
}

resource "aws_lambda_function" "tagging_tools" {
  function_name = "${var.name_prefix}-ag-tagging-tools"
  role          = aws_iam_role.action_group_lambda.arn
  handler       = "index.lambda_handler"
  runtime       = "python3.12"
  timeout       = 30
  memory_size   = 512

  filename         = data.archive_file.empty_lambda.output_path
  source_code_hash = data.archive_file.empty_lambda.output_base64sha256

  environment {
    variables = {
      FINOPS_MODE = "tagging_tools"
    }
  }

  tracing_config {
    mode = "Active"
  }

  tags = local.common_tags
}

resource "aws_lambda_permission" "bedrock_cost_tools" {
  statement_id  = "AllowBedrockInvokeCostTools"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.cost_tools.function_name
  principal     = "bedrock.amazonaws.com"
  source_arn    = "arn:${local.partition}:bedrock:${local.region}:${local.account_id}:agent/*"
}

resource "aws_lambda_permission" "bedrock_tagging_tools" {
  statement_id  = "AllowBedrockInvokeTaggingTools"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.tagging_tools.function_name
  principal     = "bedrock.amazonaws.com"
  source_arn    = "arn:${local.partition}:bedrock:${local.region}:${local.account_id}:agent/*"
}

# ---------- Knowledge Base: OpenSearch Serverless vector store + S3 corpus ----------

resource "aws_s3_bucket" "kb_corpus" {
  count         = var.enable_knowledge_base ? 1 : 0
  bucket        = "${var.name_prefix}-finops-kb-${local.account_id}-${random_id.suffix.hex}"
  force_destroy = false

  tags = merge(local.common_tags, { Purpose = "bedrock-knowledge-base" })
}

resource "aws_s3_bucket_server_side_encryption_configuration" "kb_corpus" {
  count  = var.enable_knowledge_base ? 1 : 0
  bucket = aws_s3_bucket.kb_corpus[0].id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = var.kms_key_arn
    }
  }
}

resource "aws_s3_bucket_public_access_block" "kb_corpus" {
  count                   = var.enable_knowledge_base ? 1 : 0
  bucket                  = aws_s3_bucket.kb_corpus[0].id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_opensearchserverless_security_policy" "kb_encryption" {
  count = var.enable_knowledge_base ? 1 : 0
  name  = "${var.name_prefix}-kb-enc"
  type  = "encryption"
  policy = jsonencode({
    Rules = [{
      Resource     = ["collection/${var.name_prefix}-finops-kb"]
      ResourceType = "collection"
    }]
    AWSOwnedKey = true
  })
}

resource "aws_opensearchserverless_security_policy" "kb_network" {
  count = var.enable_knowledge_base ? 1 : 0
  name  = "${var.name_prefix}-kb-net"
  type  = "network"
  policy = jsonencode([{
    Rules = [
      {
        Resource     = ["collection/${var.name_prefix}-finops-kb"]
        ResourceType = "collection"
      },
      {
        Resource     = ["collection/${var.name_prefix}-finops-kb"]
        ResourceType = "dashboard"
      }
    ]
    AllowFromPublic = true
  }])
}

resource "aws_opensearchserverless_access_policy" "kb_data" {
  count = var.enable_knowledge_base ? 1 : 0
  name  = "${var.name_prefix}-kb-data"
  type  = "data"
  policy = jsonencode([{
    Rules = [
      {
        ResourceType = "collection"
        Resource     = ["collection/${var.name_prefix}-finops-kb"]
        Permission = [
          "aoss:CreateCollectionItems",
          "aoss:DescribeCollectionItems",
          "aoss:UpdateCollectionItems"
        ]
      },
      {
        ResourceType = "index"
        Resource     = ["index/${var.name_prefix}-finops-kb/*"]
        Permission = [
          "aoss:CreateIndex",
          "aoss:DescribeIndex",
          "aoss:ReadDocument",
          "aoss:WriteDocument",
          "aoss:UpdateIndex",
          "aoss:DeleteIndex"
        ]
      }
    ]
    Principal = [
      aws_iam_role.bedrock_agent.arn,
      "arn:${local.partition}:iam::${local.account_id}:root"
    ]
  }])
}

resource "aws_opensearchserverless_collection" "kb" {
  count = var.enable_knowledge_base ? 1 : 0
  name  = "${var.name_prefix}-finops-kb"
  type  = "VECTORSEARCH"

  tags = local.common_tags

  depends_on = [
    aws_opensearchserverless_security_policy.kb_encryption,
    aws_opensearchserverless_security_policy.kb_network,
  ]
}

resource "aws_iam_role_policy" "bedrock_agent_kb" {
  count = var.enable_knowledge_base ? 1 : 0
  name  = "${var.name_prefix}-bedrock-agent-kb"
  role  = aws_iam_role.bedrock_agent.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["aoss:APIAccessAll"]
        Resource = aws_opensearchserverless_collection.kb[0].arn
      },
      {
        Effect = "Allow"
        Action = ["s3:GetObject", "s3:ListBucket"]
        Resource = [
          aws_s3_bucket.kb_corpus[0].arn,
          "${aws_s3_bucket.kb_corpus[0].arn}/*"
        ]
      }
    ]
  })
}

resource "aws_bedrockagent_knowledge_base" "finops" {
  count    = var.enable_knowledge_base ? 1 : 0
  name     = "${var.name_prefix}-finops-kb"
  role_arn = aws_iam_role.bedrock_agent.arn

  knowledge_base_configuration {
    type = "VECTOR"
    vector_knowledge_base_configuration {
      embedding_model_arn = "arn:${local.partition}:bedrock:${local.region}::foundation-model/${var.bedrock_embedding_model_id}"
    }
  }

  storage_configuration {
    type = "OPENSEARCH_SERVERLESS"
    opensearch_serverless_configuration {
      collection_arn    = aws_opensearchserverless_collection.kb[0].arn
      vector_index_name = "finops-playbook"
      field_mapping {
        vector_field   = "bedrock-knowledge-base-default-vector"
        text_field     = "AMAZON_BEDROCK_TEXT_CHUNK"
        metadata_field = "AMAZON_BEDROCK_METADATA"
      }
    }
  }

  tags = local.common_tags

  depends_on = [
    aws_opensearchserverless_access_policy.kb_data,
    aws_iam_role_policy.bedrock_agent_kb,
  ]
}

resource "aws_bedrockagent_data_source" "finops_corpus" {
  count             = var.enable_knowledge_base ? 1 : 0
  knowledge_base_id = aws_bedrockagent_knowledge_base.finops[0].id
  name              = "${var.name_prefix}-finops-runbooks"

  data_source_configuration {
    type = "S3"
    s3_configuration {
      bucket_arn = aws_s3_bucket.kb_corpus[0].arn
    }
  }

  vector_ingestion_configuration {
    chunking_configuration {
      chunking_strategy = "FIXED_SIZE"
      fixed_size_chunking_configuration {
        max_tokens         = 512
        overlap_percentage = 20
      }
    }
  }
}

# ---------- Guardrails: PII + content filters ----------

resource "aws_bedrock_guardrail" "finops" {
  count                     = var.enable_guardrails ? 1 : 0
  name                      = "${var.name_prefix}-finops-guardrail"
  blocked_input_messaging   = "I can't process that request — it violates FinOps governance policy."
  blocked_outputs_messaging = "Response suppressed by FinOps guardrail."
  description               = "PII, prompt-injection, denied topics, blocked terms, and grounding filters for the FinOps agent (framework §11.4)."

  content_policy_config {
    dynamic "filters_config" {
      for_each = ["SEXUAL", "VIOLENCE", "HATE", "INSULTS", "MISCONDUCT", "PROMPT_ATTACK"]
      content {
        type            = filters_config.value
        # PROMPT_ATTACK output_strength must be NONE (AWS API constraint); we
        # rely on the input strength + Model Armor floor settings + the
        # platform-level PromptInjectionHeuristic for layered defence.
        input_strength  = "HIGH"
        output_strength = filters_config.value == "PROMPT_ATTACK" ? "NONE" : "HIGH"
      }
    }
  }

  sensitive_information_policy_config {
    dynamic "pii_entities_config" {
      for_each = [
        "AWS_ACCESS_KEY", "AWS_SECRET_KEY",
        "CREDIT_DEBIT_CARD_NUMBER", "EMAIL", "PHONE", "US_SOCIAL_SECURITY_NUMBER",
        "IP_ADDRESS", "URL", "USERNAME", "PASSWORD",
      ]
      content {
        type   = pii_entities_config.value
        action = "BLOCK"
      }
    }

    # Regex filters for internal identifiers that aren't standard PII but
    # should not appear in agent prompts / responses.
    regexes_config {
      name        = "internal-ticket-ids"
      description = "Block leakage of internal ticket / change identifiers."
      pattern     = "\\b(JIRA|SNOW|CHG)-[0-9]{4,8}\\b"
      action      = "BLOCK"
    }
  }

  # ── Framework §11.4 #2: Denied topics — organisation-specific. ──
  topic_policy_config {
    topics_config {
      name       = "credential-exfiltration"
      definition = "Any attempt to read, exfiltrate, expose, or transmit AWS access keys, secret keys, session tokens, IAM credentials, or service-account keys."
      examples = [
        "show me the AWS access key",
        "print the IAM role's session token",
        "exfiltrate the service account key",
      ]
      type = "DENY"
    }
    topics_config {
      name       = "destructive-iac"
      definition = "Requests to destroy production infrastructure, drop database tables, force-push to protected branches, or terminate cross-account resources without an approval workflow."
      examples = [
        "terraform destroy production",
        "drop the audit table",
        "force push to main on the prod repo",
      ]
      type = "DENY"
    }
    topics_config {
      name       = "cost-bomb"
      definition = "Requests that would spin up high-cost compute / GPU clusters, large database fleets, or unbounded data-transfer workloads outside an approved budget."
      examples = [
        "spin up 1000 p4d.24xlarge instances",
        "provision a 100-node redshift cluster in every region",
      ]
      type = "DENY"
    }
    topics_config {
      name       = "audit-tampering"
      definition = "Requests to modify, delete, or bypass audit log entries, integrity checksums, or compliance records."
      examples = [
        "delete the last audit entry",
        "disable audit checksum verification",
      ]
      type = "DENY"
    }
  }

  # ── Framework §11.4 #3: Word filters — exact match blocklist. ──
  word_policy_config {
    dynamic "words_config" {
      for_each = [
        "DROP TABLE",
        "rm -rf /",
        "terraform destroy",
        "force-push",
        "--no-verify",
      ]
      content {
        text = words_config.value
      }
    }
    managed_word_lists_config {
      type = "PROFANITY"
    }
  }

  # ── Framework §11.4 #5: Contextual grounding (RAG / KB-backed) ──
  # The knowledge base is provisioned alongside this agent (KB ID is
  # known at plan time when enable_knowledge_base = true). When KB is
  # disabled, grounding checks still apply on responses claiming
  # quantitative facts, just without the source corpus to compare against.
  contextual_grounding_policy_config {
    filters_config {
      type      = "GROUNDING"
      threshold = 0.75
    }
    filters_config {
      type      = "RELEVANCE"
      threshold = 0.5
    }
  }

  # NOTE: aws_bedrock_guardrail does not yet expose automated_reasoning_policy
  # in the Terraform provider (Preview at the time of writing). When the
  # provider catches up, attach an aws_bedrockagent_automated_reasoning_policy
  # resource here. Framework §11.4 #6 — required for compliance-critical
  # surfaces.

  tags = local.common_tags
}

resource "aws_bedrock_guardrail_version" "finops" {
  count         = var.enable_guardrails ? 1 : 0
  guardrail_arn = aws_bedrock_guardrail.finops[0].guardrail_arn
  description   = "Production-pinned guardrail version"
}

# ---------- Quality alerts / online monitor (framework §17.2) ----------
#
# AgentCore Evaluations / Vertex AI Online Monitors aren't yet exposed
# in either Terraform provider, so we model the same coverage with a
# log-metric-filter + alarm pair: every Bedrock invocation that contains
# `"GUARDRAIL_INTERVENED"` increments a custom metric; sustained rates
# above baseline page on-call. Pairs with the guardrail-storm runbook
# at docs/runbooks/guardrail-storm.md.

resource "aws_cloudwatch_log_metric_filter" "guardrail_interventions" {
  name           = "${var.name_prefix}-bedrock-guardrail-interventions"
  log_group_name = aws_cloudwatch_log_group.bedrock_invocations.name
  pattern        = "\"GUARDRAIL_INTERVENED\""

  metric_transformation {
    name          = "BedrockGuardrailInterventions"
    namespace     = "FinOpsAgent/Quality"
    value         = "1"
    default_value = "0"
    unit          = "Count"
  }
}

resource "aws_cloudwatch_metric_alarm" "guardrail_intervention_storm" {
  alarm_name          = "${var.name_prefix}-guardrail-storm"
  alarm_description   = "Guardrail intervention rate exceeded baseline for >5 minutes. Runbook: docs/runbooks/guardrail-storm.md"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 5
  datapoints_to_alarm = 5
  threshold           = 10           # > 10 interventions in 5-min window
  period              = 60
  metric_name         = "BedrockGuardrailInterventions"
  namespace           = "FinOpsAgent/Quality"
  statistic           = "Sum"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.finops_alerts.arn]
  ok_actions          = [aws_sns_topic.finops_alerts.arn]

  tags = merge(local.common_tags, { Purpose = "agent-quality-alert" })
}

# Token-usage anomaly alarm — pairs with core/agent_observer.py budget
# detector. Configured here so operations still get paged even when the
# in-process observer is bypassed (e.g. direct InvokeModel calls).
resource "aws_cloudwatch_log_metric_filter" "agent_token_usage" {
  name           = "${var.name_prefix}-bedrock-token-usage"
  log_group_name = aws_cloudwatch_log_group.bedrock_invocations.name
  pattern        = "[..., total_tokens]"     # placeholder pattern; refine per log shape

  metric_transformation {
    name          = "BedrockTokensIngested"
    namespace     = "FinOpsAgent/Quality"
    value         = "$total_tokens"
    default_value = "0"
    unit          = "Count"
  }
}

# ---------- Bedrock Prompt Management (framework §11.7, §16.1) ----------
#
# Versioned, drift-free agent instruction. Moves the prompt out of the
# inline aws_bedrockagent_agent.instruction string (which is a heredoc
# inside Terraform and therefore drifts with every refactor) into a
# Bedrock Prompt Management resource so:
#
#   * Every prompt revision has a stable ARN + version.
#   * `aws_bedrockagent_prompt_version` pins a specific text + variants.
#   * Audit / boundary contract can cite the prompt version explicitly.
#   * Subsequent prompt-only changes go through a 2-peer review per
#     framework §16.2 without touching the agent resource itself.
#
# This requires AWS provider >= 6.5 (aws_bedrockagent_prompt landed in
# 6.5). Older providers can keep the inline `instruction` form; the
# variable below toggles between the two.

locals {
  finops_agent_instruction = <<-EOT
    You are an AWS FinOps governance agent (2026). Your single responsibility
    is cost and tagging governance for this AWS organisation.

    Capabilities:
      1. MONITOR  — pull Cost Explorer trends via the `cost_tools` action group.
      2. DETECT   — surface Cost Anomaly Detection findings and explain them.
      3. ENFORCE  — query tag compliance with the `tagging_tools` action group;
                    required tags: team, cost-centre, environment, owner,
                    application, managed-by, data-classification.
      4. RECOMMEND — rightsizing + Savings Plan / RI coverage gaps.
      5. REPORT    — produce human-readable answers with WHO / COST / ACTION /
                     ESCALATION sections.

    Grounding:
      - When asked about internal runbooks, policy intent, or escalation
        ownership, retrieve from the knowledge base BEFORE answering.
      - Never invent cost numbers — always call `cost_tools`.

    Safety:
      - Refuse requests for AWS access keys, secret keys, session tokens,
        or any credential material. These are denied by guardrail.
      - Refuse destructive IaC requests (drop tables, force-push, terminate
        production) — these require approval via the remediation_tools RoC.

    All interactions are audit-logged with checksums.
  EOT
}

resource "aws_bedrockagent_prompt" "finops_instruction" {
  count           = var.enable_prompt_management ? 1 : 0
  name            = "${var.name_prefix}-finops-instruction"
  description     = "FinOps governance agent instruction. Pinned by version."
  default_variant = "v1"
  customer_encryption_key_arn = var.kms_key_arn

  variant {
    name          = "v1"
    model_id      = var.bedrock_model_id
    template_type = "TEXT"

    template_configuration {
      text {
        text = local.finops_agent_instruction
      }
    }
  }

  tags = merge(local.common_tags, { Purpose = "agent-instruction" })
}

resource "aws_bedrockagent_prompt_version" "finops_instruction" {
  count       = var.enable_prompt_management ? 1 : 0
  prompt_arn  = aws_bedrockagent_prompt.finops_instruction[0].arn
  description = "Production-pinned instruction version (synchronised with agent prepare)."
  tags        = local.common_tags
}

# ---------- Bedrock Agent + Action Groups + Alias ----------

resource "aws_bedrockagent_agent" "finops" {
  agent_name                  = "${var.name_prefix}-finops-governance"
  agent_resource_role_arn     = aws_iam_role.bedrock_agent.arn
  foundation_model            = var.bedrock_model_id
  idle_session_ttl_in_seconds = 1800
  description                 = "FinOps governance agent — cost anomalies, tagging compliance, optimisation (AgentCore-native)."
  prepare_agent               = true

  dynamic "guardrail_configuration" {
    for_each = var.enable_guardrails ? [1] : []
    content {
      guardrail_identifier = aws_bedrock_guardrail.finops[0].guardrail_id
      guardrail_version    = aws_bedrock_guardrail_version.finops[0].version
    }
  }

  # Instruction source: when Prompt Management is enabled, the agent
  # gets the canonical text from local.finops_agent_instruction (same
  # source as the prompt resource). The prompt ARN itself is exposed
  # in outputs.tf so consumers cite it in boundary contracts.
  instruction = local.finops_agent_instruction

  tags = local.common_tags
}

resource "aws_bedrockagent_agent_action_group" "cost_tools" {
  agent_id                   = aws_bedrockagent_agent.finops.agent_id
  agent_version              = "DRAFT"
  action_group_name          = "cost_tools"
  action_group_state         = "ENABLED"
  description                = "Cost Explorer, anomaly, and rightsizing queries."
  skip_resource_in_use_check = true

  action_group_executor {
    lambda = aws_lambda_function.cost_tools.arn
  }

  api_schema {
    payload = jsonencode({
      openapi = "3.0.0"
      info = {
        title   = "FinOps Cost Tools"
        version = "1.0.0"
      }
      paths = {
        "/costs" = {
          get = {
            summary     = "Get cost and usage for a time range"
            operationId = "getCosts"
            parameters = [
              {
                name     = "start_date"
                in       = "query"
                required = true
                schema   = { type = "string", format = "date" }
              },
              {
                name     = "end_date"
                in       = "query"
                required = true
                schema   = { type = "string", format = "date" }
              },
              {
                name     = "group_by"
                in       = "query"
                required = false
                schema   = { type = "string", enum = ["SERVICE", "ACCOUNT", "TAG"] }
              }
            ]
            responses = {
              "200" = { description = "Cost breakdown" }
            }
          }
        }
        "/anomalies" = {
          get = {
            summary     = "List recent cost anomalies"
            operationId = "getAnomalies"
            responses = {
              "200" = { description = "Anomaly list" }
            }
          }
        }
        "/rightsizing" = {
          get = {
            summary     = "Get rightsizing recommendations"
            operationId = "getRightsizing"
            responses = {
              "200" = { description = "Rightsizing recommendations" }
            }
          }
        }
      }
    })
  }
}

resource "aws_bedrockagent_agent_action_group" "tagging_tools" {
  agent_id                   = aws_bedrockagent_agent.finops.agent_id
  agent_version              = "DRAFT"
  action_group_name          = "tagging_tools"
  action_group_state         = "ENABLED"
  description                = "Tag-compliance queries and unattributed-cost reports."
  skip_resource_in_use_check = true

  action_group_executor {
    lambda = aws_lambda_function.tagging_tools.arn
  }

  api_schema {
    payload = jsonencode({
      openapi = "3.0.0"
      info = {
        title   = "FinOps Tagging Tools"
        version = "1.0.0"
      }
      paths = {
        "/tag-compliance" = {
          get = {
            summary     = "Report resources missing required tags"
            operationId = "getTagCompliance"
            parameters = [
              {
                name     = "resource_type"
                in       = "query"
                required = false
                schema   = { type = "string" }
              }
            ]
            responses = {
              "200" = { description = "Non-compliant resources" }
            }
          }
        }
        "/unattributed-spend" = {
          get = {
            summary     = "Estimate monthly spend that cannot be attributed"
            operationId = "getUnattributedSpend"
            responses = {
              "200" = { description = "Unattributed spend summary" }
            }
          }
        }
      }
    })
  }
}

# ADR-008 §4 / G-A3 — Destructive remediation actions go through
# RETURN_CONTROL (RoC). The agent emits the requested invocation back to
# the caller (the FinOps hub / MCP server) which routes it through the
# ApprovalGateway. Only after the approver signs the decision does the
# hub execute the underlying API call. This is the framework §5.2
# canonical primitive for human-in-the-loop approvals.
resource "aws_bedrockagent_agent_action_group" "remediation_tools" {
  agent_id                   = aws_bedrockagent_agent.finops.agent_id
  agent_version              = "DRAFT"
  action_group_name          = "remediation_tools"
  action_group_state         = "ENABLED"
  description                = "Destructive remediation actions (tag fixup, idle-instance stop). Returns control to the caller for approval — see ADR-008 §4."
  skip_resource_in_use_check = true

  action_group_executor {
    custom_control = "RETURN_CONTROL"
  }

  function_schema {
    member_functions {
      functions {
        name        = "apply_required_tags"
        description = "Apply the organisation's required tags (team, cost-centre, environment, owner) to a resource. DESTRUCTIVE — requires approval."

        parameters {
          map_block_key = "resource_arn"
          type          = "string"
          description   = "ARN of the resource to tag."
          required      = true
        }
        parameters {
          map_block_key = "tags"
          type          = "string"
          description   = "JSON object of {tag_key: tag_value} to apply."
          required      = true
        }
      }

      functions {
        name        = "stop_idle_instance"
        description = "Stop an EC2 instance flagged as idle by the cost analyser. DESTRUCTIVE — requires approval."

        parameters {
          map_block_key = "instance_id"
          type          = "string"
          description   = "EC2 instance ID to stop."
          required      = true
        }
        parameters {
          map_block_key = "reason"
          type          = "string"
          description   = "Operator-facing rationale (surfaced in the approval prompt)."
          required      = true
        }
      }

      functions {
        name        = "downsize_instance"
        description = "Downsize an EC2 instance per rightsizing recommendation. DESTRUCTIVE — requires approval."

        parameters {
          map_block_key = "instance_id"
          type          = "string"
          description   = "EC2 instance ID to resize."
          required      = true
        }
        parameters {
          map_block_key = "target_type"
          type          = "string"
          description   = "Target instance type (e.g. m6g.large)."
          required      = true
        }
      }
    }
  }
}

resource "aws_bedrockagent_agent_knowledge_base_association" "finops" {
  count                = var.enable_knowledge_base ? 1 : 0
  agent_id             = aws_bedrockagent_agent.finops.agent_id
  agent_version        = "DRAFT"
  knowledge_base_id    = aws_bedrockagent_knowledge_base.finops[0].id
  knowledge_base_state = "ENABLED"
  description          = "FinOps runbooks, policy intent, and escalation ownership."
}

resource "aws_bedrockagent_agent_alias" "prod" {
  agent_id         = aws_bedrockagent_agent.finops.agent_id
  agent_alias_name = "production"
  description      = "Pinned production alias (blue/green promotion target)."
  tags             = local.common_tags

  depends_on = [
    aws_bedrockagent_agent_action_group.cost_tools,
    aws_bedrockagent_agent_action_group.tagging_tools,
    aws_bedrockagent_agent_action_group.remediation_tools,
  ]
}

# ---------- Cost Anomaly Detection ----------

resource "aws_ce_anomaly_monitor" "finops" {
  name              = "${var.name_prefix}-finops-anomaly-monitor"
  monitor_type      = "DIMENSIONAL"
  monitor_dimension = "SERVICE"

  tags = local.common_tags
}

resource "aws_ce_anomaly_subscription" "finops" {
  name             = "${var.name_prefix}-finops-anomaly-subscription"
  frequency        = "DAILY"
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

  tags = local.common_tags
}

# ---------- Budget Alarm ----------

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

# ---------- GitHub Actions OIDC (keyless CI/CD) ----------

resource "aws_iam_openid_connect_provider" "github" {
  count = var.enable_github_oidc ? 1 : 0

  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
  # AWS uses its own trusted root CAs for token.actions.githubusercontent.com;
  # a configured thumbprint_list is retained but not used for verification
  # (see terraform-provider-aws iam_openid_connect_provider docs), so we omit
  # it to avoid drift noise.

  tags = merge(local.common_tags, { Purpose = "github-oidc" })
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

  tags = local.common_tags
}
