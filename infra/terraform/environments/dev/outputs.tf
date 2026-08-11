output "application_table_name" {
  description = "Name of the dev application-state DynamoDB table"
  value       = module.application.application_table_name
}

output "bedrock_model_arn" {
  description = "Exact foundation-model ARN allowed for dev workers"
  value       = module.application.bedrock_model_arn
}

output "checkpoint_retention_days" {
  description = "Application-configured dev checkpoint TTL in days"
  value       = module.application.checkpoint_retention_days
}

output "checkpoint_table_name" {
  description = "Name of the dev LangGraph checkpoint table"
  value       = module.application.checkpoint_table_name
}

output "cognito_client_id" {
  description = "Dev Cognito public PKCE web client ID"
  value       = module.application.cognito_client_id
}

output "cognito_domain" {
  description = "Dev Cognito hosted-login domain prefix"
  value       = module.application.cognito_domain
}

output "cognito_user_pool_id" {
  description = "Dev Cognito user pool ID"
  value       = module.application.cognito_user_pool_id
}

output "data_volumes" {
  description = "Dev retained data-volume IDs and Availability Zones"
  value       = module.application.data_volumes
}

output "loki_prefix" {
  description = "Exact S3 object prefix reserved for dev Loki objects"
  value       = module.application.loki_prefix
}

output "secret_arns" {
  description = "Dev runtime secret ARNs keyed by purpose"
  value       = module.application.secret_arns
}
