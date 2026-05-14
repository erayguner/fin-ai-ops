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

variable "company_name" {
  description = "Company/org name surfaced by Vertex AI Agent Builder (Discovery Engine chat engine)."
  type        = string
  default     = "FinOps Automation Hub"
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

variable "gemini_model" {
  description = "Gemini model used by the ADK agent (Gemini 2.5 family)."
  type        = string
  default     = "gemini-2.5-pro"
}

variable "embedding_dimensions" {
  description = "Embedding dimensions for the Vector Search index (text-embedding-005 = 768)."
  type        = number
  default     = 768
}

variable "adk_agent_image" {
  description = "Full Artifact Registry image URI for the ADK agent (built and pushed out of band)."
  type        = string
  default     = "us-docker.pkg.dev/google-samples/containers/gke/hello-app:1.0"
}

variable "enable_cloud_run_runtime" {
  description = "Deploy ADK agent on Cloud Run v2 as the primary runtime."
  type        = bool
  default     = true
}

variable "enable_vector_search" {
  description = "Provision Vertex AI Vector Search (Index + Endpoint) for RAG retrieval."
  type        = bool
  default     = true
}

variable "enable_agent_builder" {
  description = "Provision Vertex AI Agent Builder (Discovery Engine data store + chat engine)."
  type        = bool
  default     = true
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

# ADR-008 §9 — Model Armor floor settings are Preview at the time of
# writing. Setting this to true requires google-beta provider + Org Admin
# privileges; default false so plan/apply works in fresh projects.
variable "enable_model_armor_floor_settings" {
  description = "Provision Model Armor floor settings (Preview) for Google-managed MCP servers."
  type        = bool
  default     = false
}
