variable "project_id" {
  description = "GCP project ID"
  type        = string
}

variable "region" {
  description = "GCP region for resources"
  type        = string
  default     = "europe-west2"
}

variable "name_prefix" {
  description = "Prefix for all resource names"
  type        = string
  default     = "finops"
}

variable "billing_account_id" {
  description = "GCP Billing Account ID for budget alerts (optional)"
  type        = string
  default     = ""
}

variable "monthly_budget_usd" {
  description = "Monthly budget limit in USD"
  type        = number
  default     = 10000
}

variable "enable_wif" {
  description = "Enable Workload Identity Federation for GitHub Actions"
  type        = bool
  default     = false
}

variable "github_repository" {
  description = "GitHub repository for WIF (e.g. org/repo)"
  type        = string
  default     = ""
}

variable "labels" {
  description = "Common labels for all resources"
  type        = map(string)
  default = {
    managed-by = "terraform"
    project    = "finops-automation-hub"
  }
}
