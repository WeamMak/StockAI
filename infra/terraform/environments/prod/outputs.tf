output "application_table_name" {
  description = "Name of the prod application-state DynamoDB table"
  value       = module.application.application_table_name
}

output "bedrock_model_arn" {
  description = "Exact foundation-model ARN allowed for prod workers"
  value       = module.application.bedrock_model_arn
}

output "checkpoint_retention_days" {
  description = "Application-configured prod checkpoint TTL in days"
  value       = module.application.checkpoint_retention_days
}

output "checkpoint_table_name" {
  description = "Name of the prod LangGraph checkpoint table"
  value       = module.application.checkpoint_table_name
}

output "cognito_client_id" {
  description = "Prod Cognito public PKCE web client ID"
  value       = module.application.cognito_client_id
}

output "cognito_domain" {
  description = "Prod Cognito hosted-login domain prefix"
  value       = module.application.cognito_domain
}

output "cognito_user_pool_id" {
  description = "Prod Cognito user pool ID"
  value       = module.application.cognito_user_pool_id
}

output "data_volumes" {
  description = "Prod retained data-volume IDs and Availability Zones"
  value       = module.application.data_volumes
}

output "loki_prefix" {
  description = "Exact S3 object prefix reserved for prod Loki objects"
  value       = module.application.loki_prefix
}

output "secret_arns" {
  description = "Prod runtime secret ARNs keyed by purpose"
  value       = module.application.secret_arns
}
