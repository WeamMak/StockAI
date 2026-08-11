output "administrator_cidr" {
  description = "Validated administrator IPv4 CIDR for the later platform root"
  value       = var.administrator_cidr
}

output "github_apply_role_arn" {
  description = "ARN of the protected-environment Terraform apply role"
  value       = aws_iam_role.github_apply.arn
}

output "github_oidc_provider_arn" {
  description = "ARN of the GitHub Actions OIDC identity provider"
  value       = aws_iam_openid_connect_provider.github_actions.arn
}

output "github_plan_role_arn" {
  description = "ARN of the pull-request Terraform plan role"
  value       = aws_iam_role.github_plan.arn
}

output "state_bucket_name" {
  description = "Dedicated encrypted and versioned Terraform state bucket name"
  value       = aws_s3_bucket.terraform_state.id
}

output "state_key_prefix" {
  description = "Reserved object prefix for later Terraform root states"
  value       = var.state_key_prefix
}

output "state_lock_table_name" {
  description = "Encrypted DynamoDB Terraform lock table name"
  value       = aws_dynamodb_table.terraform_lock.name
}
