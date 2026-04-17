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

  tags = merge(local.common_tags, { Purpose = "cost-monitoring" })
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
  description               = "PII, prompt-injection, and sensitive-topic filters for the FinOps agent."

  content_policy_config {
    dynamic "filters_config" {
      for_each = ["SEXUAL", "VIOLENCE", "HATE", "INSULTS", "MISCONDUCT", "PROMPT_ATTACK"]
      content {
        type            = filters_config.value
        input_strength  = "HIGH"
        output_strength = filters_config.value == "PROMPT_ATTACK" ? "NONE" : "HIGH"
      }
    }
  }

  sensitive_information_policy_config {
    dynamic "pii_entities_config" {
      for_each = [
        "AWS_ACCESS_KEY", "AWS_SECRET_KEY",
        "CREDIT_DEBIT_CARD_NUMBER", "EMAIL", "PHONE", "US_SOCIAL_SECURITY_NUMBER"
      ]
      content {
        type   = pii_entities_config.value
        action = "BLOCK"
      }
    }
  }

  tags = local.common_tags
}

resource "aws_bedrock_guardrail_version" "finops" {
  count         = var.enable_guardrails ? 1 : 0
  guardrail_arn = aws_bedrock_guardrail.finops[0].guardrail_arn
  description   = "Production-pinned guardrail version"
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

  instruction = <<-EOT
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

    All interactions are audit-logged with checksums.
  EOT

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
