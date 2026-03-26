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

output "vertex_agent_service_account" {
  description = "Vertex AI ADK agent service account email"
  value       = google_service_account.vertex_agent.email
}

output "workload_identity_pool" {
  description = "Workload Identity Pool ID (if enabled)"
  value       = var.enable_wif ? google_iam_workload_identity_pool.github[0].name : null
}
