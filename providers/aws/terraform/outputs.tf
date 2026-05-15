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

output "bedrock_agent_alias_arn" {
  description = "Production alias ARN — invoke this from applications (blue/green promotion target)."
  value       = aws_bedrockagent_agent_alias.prod.agent_alias_arn
}

output "bedrock_agent_alias_id" {
  description = "Production alias ID for InvokeAgent calls."
  value       = aws_bedrockagent_agent_alias.prod.agent_alias_id
}

output "action_group_cost_tools_lambda" {
  description = "Lambda ARN for the cost_tools Bedrock action group."
  value       = aws_lambda_function.cost_tools.arn
}

output "action_group_tagging_tools_lambda" {
  description = "Lambda ARN for the tagging_tools Bedrock action group."
  value       = aws_lambda_function.tagging_tools.arn
}

output "knowledge_base_id" {
  description = "Bedrock Knowledge Base ID (null if disabled)."
  value       = var.enable_knowledge_base ? aws_bedrockagent_knowledge_base.finops[0].id : null
}

output "knowledge_base_corpus_bucket" {
  description = "S3 bucket for knowledge-base documents (drop runbooks here)."
  value       = var.enable_knowledge_base ? aws_s3_bucket.kb_corpus[0].id : null
}

output "opensearch_collection_endpoint" {
  description = "OpenSearch Serverless collection endpoint for the KB vector store."
  value       = var.enable_knowledge_base ? aws_opensearchserverless_collection.kb[0].collection_endpoint : null
}

output "prompt_arn" {
  description = "Bedrock Prompt Management ARN for the agent instruction (null if disabled). Cite in boundary contracts."
  value       = var.enable_prompt_management ? aws_bedrockagent_prompt.finops_instruction[0].arn : null
}

output "prompt_version" {
  description = "Pinned Bedrock prompt version (framework §16.1)."
  value       = var.enable_prompt_management ? aws_bedrockagent_prompt_version.finops_instruction[0].version : null
}

output "bedrock_invocation_log_group" {
  description = "CloudWatch Log Group capturing every Bedrock model invocation."
  value       = aws_cloudwatch_log_group.bedrock_invocations.name
}

output "guardrail_id" {
  description = "Bedrock Guardrail ID (null if disabled)."
  value       = var.enable_guardrails ? aws_bedrock_guardrail.finops[0].guardrail_id : null
}

output "guardrail_version" {
  description = "Bedrock Guardrail published version."
  value       = var.enable_guardrails ? aws_bedrock_guardrail_version.finops[0].version : null
}

output "anomaly_monitor_arn" {
  description = "Cost Anomaly Detection monitor ARN"
  value       = aws_ce_anomaly_monitor.finops.arn
}

output "github_actions_role_arn" {
  description = "IAM role ARN for GitHub Actions OIDC (if enabled)"
  value       = var.enable_github_oidc ? aws_iam_role.github_actions[0].arn : null
}
