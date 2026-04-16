################################################################################
# GCP FinOps Automation Hub Infrastructure (2026)
#
# Deploys native Google ADK / Vertex AI Agent Builder capabilities:
#   * Artifact Registry for the ADK agent container image
#   * Cloud Run v2 service as the ADK agent runtime (alternative to Agent Engine)
#   * Vertex AI Vector Search (Index + Index Endpoint) for RAG retrieval
#   * Discovery Engine data store + chat engine (Vertex AI Agent Builder) for
#     conversational grounding against the FinOps corpus
#   * Cloud Storage bucket for ADK session state, artefacts, and RAG corpus
#   * Secret Manager for ADK agent config (non-sensitive settings only —
#     no credentials, WIF covers identity)
#   * Audit log capture: Log Sink -> Pub/Sub -> ADK agent
#   * Billing export dataset, Budget alerts, Workload Identity Federation
#
# Authentication: WIF and ADC only. No service account keys permitted.
# Aligned with UK NCSC Secure by Design and Google Cloud Agent Starter Pack
# (2026) reference architecture.
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

locals {
  common_labels = merge(var.labels, {
    component = "finops-automation-hub"
  })
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
    # 2026 native-agent stack
    "artifactregistry.googleapis.com",
    "run.googleapis.com",
    "secretmanager.googleapis.com",
    "discoveryengine.googleapis.com",
    "storage.googleapis.com",
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
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

  labels = merge(local.common_labels, { purpose = "cost-alerts" })
}

resource "google_pubsub_subscription" "finops_alerts_pull" {
  name    = "${var.name_prefix}-finops-alerts-pull"
  project = var.project_id
  topic   = google_pubsub_topic.finops_alerts.id

  message_retention_duration = "604800s" # 7 days
  retain_acked_messages      = true
  ack_deadline_seconds       = 60

  expiration_policy {
    ttl = ""
  }

  labels = local.common_labels
}

# ---------- Log Sink for Resource Creation Events (2026 services) ----------

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
      "v1.compute.addresses.insert" OR
      "google.cloud.aiplatform.v1.EndpointService.CreateEndpoint" OR
      "google.cloud.aiplatform.v1.PipelineService.CreateTrainingPipeline" OR
      "google.cloud.alloydb.v1.AlloyDBAdmin.CreateCluster" OR
      "google.cloud.run.v2.Services.CreateService" OR
      "google.cloud.bigquery.v2.JobService.InsertJob"
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

  labels = merge(local.common_labels, { purpose = "billing-analysis" })

  access {
    role          = "OWNER"
    special_group = "projectOwners"
  }

  access {
    role          = "READER"
    special_group = "projectReaders"
  }
}

# ---------- Service Accounts (keyless — WIF only) ----------

resource "google_service_account" "finops_hub" {
  account_id   = "${var.name_prefix}-finops-hub"
  project      = var.project_id
  display_name = "FinOps Automation Hub"
  description  = "Service account for FinOps hub. Uses WIF only — no keys permitted."
}

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

################################################################################
# Google ADK Agent — native Vertex AI Agent Builder + Reasoning Engine stack
#
# Two complementary runtimes are provisioned:
#   1. Cloud Run v2 service (ADK agent server, stateful sessions, long tasks)
#   2. Vertex AI Agent Builder (Discovery Engine chat engine for conversational
#      grounding against the FinOps corpus)
#
# Both share:
#   * Vertex AI Vector Search for retrieval
#   * Cloud Storage bucket (corpus + session state)
#   * Artifact Registry (ADK container image)
#   * Dedicated service account with Vertex AI User + Discovery Engine admin
################################################################################

# ---------- Service Account for the ADK Agent Runtime ----------

resource "google_service_account" "adk_agent" {
  account_id   = "${var.name_prefix}-adk-agent"
  project      = var.project_id
  display_name = "FinOps ADK Agent Runtime"
  description  = "Runs the Google ADK FinOps agent on Cloud Run / Agent Engine. WIF only."
}

resource "google_project_iam_member" "adk_agent_roles" {
  for_each = toset([
    "roles/aiplatform.user",        # Vertex AI Reasoning Engine + Gemini
    "roles/discoveryengine.editor", # Agent Builder data stores + engines
    "roles/logging.logWriter",
    "roles/monitoring.metricWriter",
    "roles/cloudtrace.agent",
    "roles/bigquery.dataViewer",
    "roles/bigquery.jobUser",
    "roles/cloudasset.viewer",
    "roles/recommender.viewer",
    "roles/billing.viewer",
    "roles/secretmanager.secretAccessor",
    "roles/storage.objectUser",
  ])

  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.adk_agent.email}"
}

# ---------- Cloud Storage: ADK session state + RAG corpus ----------

resource "google_storage_bucket" "adk_artifacts" {
  name                        = "${var.project_id}-${var.name_prefix}-adk-artifacts"
  project                     = var.project_id
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  versioning {
    enabled = true
  }

  lifecycle_rule {
    condition {
      age = 180
    }
    action {
      type = "Delete"
    }
  }

  labels = merge(local.common_labels, { purpose = "adk-agent-artifacts" })
}

resource "google_storage_bucket_iam_member" "adk_bucket_writer" {
  bucket = google_storage_bucket.adk_artifacts.name
  role   = "roles/storage.objectUser"
  member = "serviceAccount:${google_service_account.adk_agent.email}"
}

# ---------- Artifact Registry: ADK agent container image ----------

resource "google_artifact_registry_repository" "adk_agent" {
  location      = var.region
  project       = var.project_id
  repository_id = "${var.name_prefix}-adk-agent"
  description   = "Container image for the ADK FinOps agent"
  format        = "DOCKER"

  labels = local.common_labels

  docker_config {
    immutable_tags = true
  }

  cleanup_policies {
    id     = "keep-last-10"
    action = "KEEP"
    most_recent_versions {
      keep_count = 10
    }
  }

  depends_on = [google_project_service.required_apis]
}

resource "google_artifact_registry_repository_iam_member" "adk_reader" {
  location   = google_artifact_registry_repository.adk_agent.location
  project    = var.project_id
  repository = google_artifact_registry_repository.adk_agent.name
  role       = "roles/artifactregistry.reader"
  member     = "serviceAccount:${google_service_account.adk_agent.email}"
}

# ---------- Secret Manager: ADK runtime config (non-credential) ----------

resource "google_secret_manager_secret" "adk_config" {
  secret_id = "${var.name_prefix}-adk-config"
  project   = var.project_id

  replication {
    auto {}
  }

  labels = local.common_labels

  depends_on = [google_project_service.required_apis]
}

resource "google_secret_manager_secret_iam_member" "adk_config_accessor" {
  secret_id = google_secret_manager_secret.adk_config.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.adk_agent.email}"
}

# ---------- Vertex AI Vector Search: RAG retrieval for ADK tools ----------

resource "google_vertex_ai_index" "finops_rag" {
  count        = var.enable_vector_search ? 1 : 0
  region       = var.region
  project      = var.project_id
  display_name = "${var.name_prefix}-finops-rag"
  description  = "Vector index for FinOps runbooks, policies, and escalation ownership."

  metadata {
    contents_delta_uri = "gs://${google_storage_bucket.adk_artifacts.name}/rag/"
    config {
      dimensions                  = var.embedding_dimensions
      approximate_neighbors_count = 150
      distance_measure_type       = "DOT_PRODUCT_DISTANCE"
      algorithm_config {
        tree_ah_config {
          leaf_node_embedding_count    = 500
          leaf_nodes_to_search_percent = 7
        }
      }
    }
  }

  index_update_method = "STREAM_UPDATE"

  labels = local.common_labels
}

resource "google_vertex_ai_index_endpoint" "finops_rag" {
  count                   = var.enable_vector_search ? 1 : 0
  region                  = var.region
  project                 = var.project_id
  display_name            = "${var.name_prefix}-finops-rag-endpoint"
  description             = "Endpoint serving the FinOps RAG index for ADK retrieval tools."
  public_endpoint_enabled = true

  labels = local.common_labels
}

# ---------- Vertex AI Agent Builder: Discovery Engine data store + chat engine ----------

resource "google_discovery_engine_data_store" "finops" {
  count                       = var.enable_agent_builder ? 1 : 0
  location                    = "global"
  project                     = var.project_id
  data_store_id               = "${var.name_prefix}-finops-datastore"
  display_name                = "FinOps Runbooks & Policies"
  industry_vertical           = "GENERIC"
  content_config              = "CONTENT_REQUIRED"
  solution_types              = ["SOLUTION_TYPE_CHAT"]
  create_advanced_site_search = false
}

resource "google_discovery_engine_chat_engine" "finops" {
  count             = var.enable_agent_builder ? 1 : 0
  engine_id         = "${var.name_prefix}-finops-agent"
  project           = var.project_id
  collection_id     = "default_collection"
  location          = "global"
  display_name      = "FinOps Governance Agent"
  industry_vertical = "GENERIC"
  data_store_ids    = [google_discovery_engine_data_store.finops[0].data_store_id]

  common_config {
    company_name = var.company_name
  }

  chat_engine_config {
    agent_creation_config {
      business              = var.company_name
      default_language_code = "en"
      time_zone             = "Europe/London"
    }
  }
}

# ---------- Cloud Run v2: ADK agent server runtime ----------

resource "google_cloud_run_v2_service" "adk_agent" {
  count    = var.enable_cloud_run_runtime ? 1 : 0
  name     = "${var.name_prefix}-adk-agent"
  project  = var.project_id
  location = var.region
  ingress  = "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER"

  template {
    service_account = google_service_account.adk_agent.email
    timeout         = "900s" # long-running tool calls (Cost Explorer, BQ)

    scaling {
      min_instance_count = 0
      max_instance_count = 10
    }

    containers {
      image = var.adk_agent_image

      resources {
        limits = {
          cpu    = "2"
          memory = "2Gi"
        }
        cpu_idle          = true
        startup_cpu_boost = true
      }

      env {
        name  = "GOOGLE_CLOUD_PROJECT"
        value = var.project_id
      }
      env {
        name  = "GOOGLE_CLOUD_LOCATION"
        value = var.region
      }
      env {
        name  = "GOOGLE_GENAI_USE_VERTEXAI"
        value = "TRUE"
      }
      env {
        name  = "ADK_MODEL"
        value = var.gemini_model
      }
      env {
        name  = "ADK_SESSION_BUCKET"
        value = google_storage_bucket.adk_artifacts.name
      }
      env {
        name  = "ADK_RAG_INDEX_ENDPOINT"
        value = var.enable_vector_search ? google_vertex_ai_index_endpoint.finops_rag[0].id : ""
      }
      env {
        name = "ADK_CONFIG_SECRET"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.adk_config.secret_id
            version = "latest"
          }
        }
      }

      liveness_probe {
        http_get {
          path = "/healthz"
        }
        initial_delay_seconds = 10
        period_seconds        = 30
      }

      startup_probe {
        http_get {
          path = "/readyz"
        }
        initial_delay_seconds = 5
        timeout_seconds       = 5
        period_seconds        = 10
        failure_threshold     = 6
      }
    }
  }

  traffic {
    type    = "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST"
    percent = 100
  }

  labels = local.common_labels

  depends_on = [
    google_project_iam_member.adk_agent_roles,
    google_secret_manager_secret_iam_member.adk_config_accessor,
    google_artifact_registry_repository_iam_member.adk_reader,
  ]
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

resource "google_service_account_iam_member" "wif_adk_binding" {
  count = var.enable_wif ? 1 : 0

  service_account_id = google_service_account.adk_agent.name
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
