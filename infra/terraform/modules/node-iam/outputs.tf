output "control_plane_instance_profile_name" {
  description = "Instance profile name for the fixed control plane"
  value       = aws_iam_instance_profile.node["control_plane"].name
}

output "control_plane_role_name" {
  description = "Infrastructure-only IAM role name for the fixed control plane"
  value       = aws_iam_role.node["control_plane"].name
}

output "dev_worker_instance_profile_name" {
  description = "Instance profile name for dev workers"
  value       = aws_iam_instance_profile.node["dev_worker"].name
}

output "dev_worker_role_name" {
  description = "Environment-specific IAM role name for dev workers"
  value       = aws_iam_role.node["dev_worker"].name
}

output "prod_worker_instance_profile_name" {
  description = "Instance profile name for prod workers"
  value       = aws_iam_instance_profile.node["prod_worker"].name
}

output "prod_worker_role_name" {
  description = "Environment-specific IAM role name for prod workers"
  value       = aws_iam_role.node["prod_worker"].name
}
