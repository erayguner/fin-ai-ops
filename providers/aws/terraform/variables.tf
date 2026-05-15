variable "name_prefix" {
  description = "Prefix for all resource names"
  type        = string
  default     = "finops"
}

variable "multi_region" {
  description = "Enable multi-region CloudTrail"
  type        = bool
  default     = true
}

variable "kms_key_arn" {
  description = "KMS key ARN for encryption at rest (CloudTrail, SNS, CloudWatch Logs, KB bucket)."
  type        = string
}

variable "log_retention_days" {
  description = "Number of days to retain logs"
  type        = number
  default     = 365
}

variable "alert_email_addresses" {
  description = "Email addresses for cost alert notifications"
  type        = list(string)
  default     = []
}

variable "monthly_budget_usd" {
  description = "Monthly budget limit in USD"
  type        = number
  default     = 10000
}

variable "bedrock_model_id" {
  description = <<-EOT
    Bedrock Agent foundation model. Claude Sonnet 4 / 4.5 are only served via
    cross-region inference profiles (see
    docs.aws.amazon.com/bedrock/latest/userguide/models-supported.html), so
    this must be an inference-profile identifier — e.g.
    `eu.anthropic.claude-sonnet-4-5-20250929-v1:0` or
    `us.anthropic.claude-sonnet-4-5-20250929-v1:0`. Match the prefix to your
    deployment region's geography.
  EOT
  type        = string
  default     = "eu.anthropic.claude-sonnet-4-5-20250929-v1:0"
}

variable "bedrock_embedding_model_id" {
  description = "Embedding model for the Knowledge Base (Titan v2 by default)."
  type        = string
  default     = "amazon.titan-embed-text-v2:0"
}

variable "enable_knowledge_base" {
  description = "Provision Bedrock Knowledge Base (OpenSearch Serverless + S3 corpus)."
  type        = bool
  default     = true
}

variable "enable_guardrails" {
  description = "Provision Bedrock Guardrail (PII + content filters) and attach to agent."
  type        = bool
  default     = true
}

variable "enable_prompt_management" {
  description = <<-EOT
    Provision the agent instruction via aws_bedrockagent_prompt +
    aws_bedrockagent_prompt_version (framework §11.7 + §16.1). Requires
    AWS provider >= 6.5. The agent's inline `instruction` attribute is
    kept in sync with the prompt source so the agent works either way.
  EOT
  type        = bool
  default     = true
}

variable "anomaly_threshold_usd" {
  description = "Minimum anomaly impact (USD) to trigger alerts"
  type        = number
  default     = 100
}

variable "enable_github_oidc" {
  description = "Enable GitHub Actions OIDC provider for keyless CI/CD"
  type        = bool
  default     = false
}

variable "github_repository" {
  description = "GitHub repository for OIDC federation (e.g. org/repo)"
  type        = string
  default     = ""
}

variable "tags" {
  description = "Common tags for all resources"
  type        = map(string)
  default = {
    ManagedBy = "terraform"
    Project   = "finops-automation-hub"
  }
}
