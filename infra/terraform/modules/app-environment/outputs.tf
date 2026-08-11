output "application_table_name" {
  description = "Name of the environment application-state DynamoDB table"
  value       = aws_dynamodb_table.application.name
}

output "bedrock_model_arn" {
  description = "Exact foundation-model ARN allowed for environment workers"
  value       = local.bedrock_model_arn
}

output "checkpoint_retention_days" {
  description = "Application-configured checkpoint TTL in days for this environment"
  value       = local.checkpoint_retention_days
}

output "checkpoint_table_name" {
  description = "Name of the environment LangGraph checkpoint DynamoDB table"
  value       = aws_dynamodb_table.checkpoint.name
}

output "cognito_client_id" {
  description = "Public PKCE web client ID for this environment"
  value       = aws_cognito_user_pool_client.web.id
}

output "cognito_domain" {
  description = "Cognito hosted-login domain prefix for this environment"
  value       = aws_cognito_user_pool_domain.main.domain
}

output "cognito_user_pool_id" {
  description = "Cognito user pool ID for this environment"
  value       = aws_cognito_user_pool.main.id
}

output "data_volumes" {
  description = "Retained data-volume coordinates keyed by environment and workload"
  value = {
    (var.environment) = {
      for workload, volume in aws_ebs_volume.data : workload => {
        availability_zone = volume.availability_zone
        volume_id         = volume.id
      }
    }
  }
}

output "loki_prefix" {
  description = "Exact shared-bucket object prefix assigned to this environment"
  value       = local.loki_prefix
}

output "secret_arns" {
  description = "Runtime secret ARNs keyed by purpose for External Secrets"
  value       = { for name, secret in aws_secretsmanager_secret.runtime : name => secret.arn }
}
