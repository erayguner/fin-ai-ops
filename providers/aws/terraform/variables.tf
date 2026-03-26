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
  description = "KMS key ARN for encryption at rest"
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
  description = "Foundation model ID for the Bedrock FinOps agent"
  type        = string
  default     = "anthropic.claude-sonnet-4-20250514"
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
