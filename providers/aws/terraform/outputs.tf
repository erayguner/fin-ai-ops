output "cloudtrail_arn" {
  description = "ARN of the FinOps CloudTrail"
  value       = aws_cloudtrail.finops_trail.arn
}

output "trail_bucket_name" {
  description = "S3 bucket storing CloudTrail logs"
  value       = aws_s3_bucket.trail_bucket.id
}

output "sns_topic_arn" {
  description = "SNS topic ARN for FinOps alerts"
  value       = aws_sns_topic.finops_alerts.arn
}

output "cloudwatch_log_group" {
  description = "CloudWatch Log Group for CloudTrail"
  value       = aws_cloudwatch_log_group.trail_logs.name
}

output "eventbridge_rule_arn" {
  description = "EventBridge rule ARN for resource creation monitoring"
  value       = aws_cloudwatch_event_rule.costly_resource_creation.arn
}

output "bedrock_agent_id" {
  description = "Bedrock FinOps governance agent ID"
  value       = aws_bedrockagent_agent.finops.agent_id
}

output "bedrock_agent_arn" {
  description = "Bedrock FinOps governance agent ARN"
  value       = aws_bedrockagent_agent.finops.agent_arn
}

output "anomaly_monitor_arn" {
  description = "Cost Anomaly Detection monitor ARN"
  value       = aws_ce_anomaly_monitor.finops.arn
}

output "github_actions_role_arn" {
  description = "IAM role ARN for GitHub Actions OIDC (if enabled)"
  value       = var.enable_github_oidc ? aws_iam_role.github_actions[0].arn : null
}
