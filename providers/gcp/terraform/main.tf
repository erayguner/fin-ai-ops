################################################################################
# GCP FinOps Automation Hub Infrastructure
#
# Deploys:
# - Cloud Audit Log monitoring via Log Sinks
# - Pub/Sub alerting for resource creation events
# - BigQuery billing export dataset
# - Vertex AI Agent Engine for ADK-based FinOps agent
# - Budget alerts via Cloud Billing Budget API
# - Workload Identity Federation (keyless auth for GitHub Actions)
#
# Authentication: WIF and ADC only. No service account keys permitted.
# Aligned with UK NCSC Secure by Design principles.
#
# Terraform >= 1.14 required. Google provider >= 6.0.
################################################################################

terraform {
  required_version = ">= 1.14.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 6.0"
    }
    google-beta = {
      source  = "hashicorp/google-beta"
      version = ">= 6.0"
    }
  }
}

# ---------- Data Sources ----------

data "google_project" "current" {
  project_id = var.project_id
}

# ---------- Enable Required APIs ----------

resource "google_project_service" "required_apis" {
  for_each = toset([
    "logging.googleapis.com",
    "monitoring.googleapis.com",
    "pubsub.googleapis.com",
    "bigquery.googleapis.com",
    "billingbudgets.googleapis.com",
    "cloudasset.googleapis.com",
    "recommender.googleapis.com",
    "aiplatform.googleapis.com",
    "cloudresourcemanager.googleapis.com",
  ])

  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

# ---------- Pub/Sub Topic for Cost Alerts ----------

resource "google_pubsub_topic" "finops_alerts" {
  name    = "${var.name_prefix}-finops-alerts"
  project = var.project_id

  message_storage_policy {
    allowed_persistence_regions = [var.region]
  }

  labels = merge(var.labels, {
    component = "finops-automation-hub"
    purpose   = "cost-alerts"
  })
}

resource "google_pubsub_subscription" "finops_alerts_pull" {
  name    = "${var.name_prefix}-finops-alerts-pull"
  project = var.project_id
  topic   = google_pubsub_topic.finops_alerts.id

  message_retention_duration = "604800s" # 7 days
  retain_acked_messages      = true
  ack_deadline_seconds       = 60

  expiration_policy {
    ttl = "" # Never expire
  }

  labels = var.labels
}

# ---------- Log Sink for Resource Creation Events ----------

resource "google_logging_project_sink" "resource_creation" {
  name        = "${var.name_prefix}-resource-creation-sink"
  project     = var.project_id
  destination = "pubsub.googleapis.com/${google_pubsub_topic.finops_alerts.id}"

  filter = <<-EOT
    protoPayload.methodName=(
      "v1.compute.instances.insert" OR
      "cloudsql.instances.create" OR
      "google.container.v1.ClusterManager.CreateCluster" OR
      "storage.buckets.create" OR
      "google.cloud.functions.v2.FunctionService.CreateFunction" OR
      "google.cloud.redis.v1.CloudRedis.CreateInstance" OR
      "v1.compute.addresses.insert"
    )
    AND severity>=NOTICE
  EOT

  unique_writer_identity = true
}

resource "google_pubsub_topic_iam_member" "log_sink_publisher" {
  project = var.project_id
  topic   = google_pubsub_topic.finops_alerts.name
  role    = "roles/pubsub.publisher"
  member  = google_logging_project_sink.resource_creation.writer_identity
}

# ---------- BigQuery Dataset for Billing Export ----------

resource "google_bigquery_dataset" "billing_export" {
  dataset_id    = replace("${var.name_prefix}_finops_billing", "-", "_")
  project       = var.project_id
  location      = var.region
  friendly_name = "FinOps Billing Export"
  description   = "Stores billing export data for FinOps cost analysis"

  default_table_expiration_ms = null

  labels = merge(var.labels, {
    component = "finops-automation-hub"
    purpose   = "billing-analysis"
  })

  access {
    role          = "OWNER"
    special_group = "projectOwners"
  }

  access {
    role          = "READER"
    special_group = "projectReaders"
  }
}

# ---------- Service Account for FinOps Hub (Keyless — WIF only) ----------

resource "google_service_account" "finops_hub" {
  account_id   = "${var.name_prefix}-finops-hub"
  project      = var.project_id
  display_name = "FinOps Automation Hub"
  description  = "Service account for FinOps hub. Uses WIF only — no keys permitted."
}

# Least-privilege IAM bindings
resource "google_project_iam_member" "finops_roles" {
  for_each = toset([
    "roles/logging.viewer",
    "roles/monitoring.viewer",
    "roles/bigquery.dataViewer",
    "roles/cloudasset.viewer",
    "roles/recommender.viewer",
    "roles/billing.viewer",
  ])

  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.finops_hub.email}"
}

# ---------- Vertex AI Agent Engine for ADK Agent ----------

resource "google_service_account" "vertex_agent" {
  account_id   = "${var.name_prefix}-vertex-agent"
  project      = var.project_id
  display_name = "FinOps ADK Agent on Vertex AI"
  description  = "Runs the Google ADK FinOps agent on Vertex AI Agent Engine. WIF only."
}

resource "google_project_iam_member" "vertex_agent_roles" {
  for_each = toset([
    "roles/aiplatform.user",
    "roles/logging.viewer",
    "roles/bigquery.dataViewer",
    "roles/bigquery.jobUser",
    "roles/cloudasset.viewer",
    "roles/recommender.viewer",
    "roles/billing.viewer",
  ])

  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.vertex_agent.email}"
}

# ---------- Workload Identity Federation (GitHub Actions) ----------

resource "google_iam_workload_identity_pool" "github" {
  count = var.enable_wif ? 1 : 0

  workload_identity_pool_id = "${var.name_prefix}-github-pool"
  project                   = var.project_id
  display_name              = "GitHub Actions Pool (FinOps)"
}

resource "google_iam_workload_identity_pool_provider" "github" {
  count = var.enable_wif ? 1 : 0

  workload_identity_pool_id          = google_iam_workload_identity_pool.github[0].workload_identity_pool_id
  workload_identity_pool_provider_id = "${var.name_prefix}-github-provider"
  project                            = var.project_id
  display_name                       = "GitHub Provider"

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }

  attribute_condition = "assertion.repository == \"${var.github_repository}\""

  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.actor"      = "assertion.actor"
    "attribute.repository" = "assertion.repository"
  }
}

resource "google_service_account_iam_member" "wif_hub_binding" {
  count = var.enable_wif ? 1 : 0

  service_account_id = google_service_account.finops_hub.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github[0].name}/attribute.repository/${var.github_repository}"
}

resource "google_service_account_iam_member" "wif_vertex_binding" {
  count = var.enable_wif ? 1 : 0

  service_account_id = google_service_account.vertex_agent.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github[0].name}/attribute.repository/${var.github_repository}"
}

# ---------- Budget Alert ----------

resource "google_billing_budget" "monthly" {
  count = var.billing_account_id != "" ? 1 : 0

  billing_account = var.billing_account_id
  display_name    = "${var.name_prefix} Monthly Budget"

  budget_filter {
    projects = ["projects/${data.google_project.current.number}"]
  }

  amount {
    specified_amount {
      currency_code = "USD"
      units         = tostring(var.monthly_budget_usd)
    }
  }

  threshold_rules {
    threshold_percent = 0.5
    spend_basis       = "CURRENT_SPEND"
  }

  threshold_rules {
    threshold_percent = 0.8
    spend_basis       = "CURRENT_SPEND"
  }

  threshold_rules {
    threshold_percent = 1.0
    spend_basis       = "CURRENT_SPEND"
  }

  threshold_rules {
    threshold_percent = 1.0
    spend_basis       = "FORECASTED_SPEND"
  }

  all_updates_rule {
    pubsub_topic = google_pubsub_topic.finops_alerts.id
  }
}
