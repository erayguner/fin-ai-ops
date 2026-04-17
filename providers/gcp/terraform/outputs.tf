output "pubsub_topic" {
  description = "Pub/Sub topic for FinOps alerts"
  value       = google_pubsub_topic.finops_alerts.id
}

output "pubsub_subscription" {
  description = "Pub/Sub subscription for pulling alerts"
  value       = google_pubsub_subscription.finops_alerts_pull.id
}

output "log_sink_name" {
  description = "Log sink capturing resource creation events"
  value       = google_logging_project_sink.resource_creation.name
}

output "bigquery_dataset" {
  description = "BigQuery dataset for billing export"
  value       = google_bigquery_dataset.billing_export.dataset_id
}

output "finops_hub_service_account" {
  description = "FinOps hub service account email"
  value       = google_service_account.finops_hub.email
}

output "adk_agent_service_account" {
  description = "Service account email used by the ADK agent runtime."
  value       = google_service_account.adk_agent.email
}

output "adk_artifact_registry_repo" {
  description = "Artifact Registry repository URI for pushing the ADK container image."
  value       = "${google_artifact_registry_repository.adk_agent.location}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.adk_agent.repository_id}"
}

output "adk_artifacts_bucket" {
  description = "GCS bucket holding ADK session state and RAG corpus."
  value       = google_storage_bucket.adk_artifacts.name
}

output "adk_config_secret" {
  description = "Secret Manager secret ID for ADK runtime config."
  value       = google_secret_manager_secret.adk_config.secret_id
}

output "vector_search_index_id" {
  description = "Vertex AI Vector Search Index ID (null if disabled)."
  value       = var.enable_vector_search ? google_vertex_ai_index.finops_rag[0].id : null
}

output "vector_search_endpoint_id" {
  description = "Vertex AI Vector Search Index Endpoint ID (null if disabled)."
  value       = var.enable_vector_search ? google_vertex_ai_index_endpoint.finops_rag[0].id : null
}

output "agent_builder_engine_id" {
  description = "Vertex AI Agent Builder (Discovery Engine) chat engine ID."
  value       = var.enable_agent_builder ? google_discovery_engine_chat_engine.finops[0].engine_id : null
}

output "agent_builder_datastore_id" {
  description = "Discovery Engine data store ID backing the chat engine."
  value       = var.enable_agent_builder ? google_discovery_engine_data_store.finops[0].data_store_id : null
}

output "adk_cloud_run_url" {
  description = "Cloud Run URL for the ADK agent runtime (null if Cloud Run disabled)."
  value       = var.enable_cloud_run_runtime ? google_cloud_run_v2_service.adk_agent[0].uri : null
}

output "workload_identity_pool" {
  description = "Workload Identity Pool ID (if enabled)"
  value       = var.enable_wif ? google_iam_workload_identity_pool.github[0].name : null
}
