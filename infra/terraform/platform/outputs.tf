output "alb_subnet_ids" {
  description = "Public subnet IDs reserved for the later shared ALB"
  value       = module.network.alb_subnet_ids
}

output "control_plane_instance_id" {
  description = "EC2 instance ID of the fixed control plane"
  value       = module.compute.control_plane_instance_id
}

output "control_plane_private_ip" {
  description = "Private IPv4 address of the fixed control plane"
  value       = module.compute.control_plane_private_ip
}

output "control_plane_role_name" {
  description = "Infrastructure-only IAM role name for the fixed control plane"
  value       = module.node_iam.control_plane_role_name
}

output "dev_worker_asg_name" {
  description = "Name of the dev worker Auto Scaling Group"
  value       = module.compute.dev_worker_asg_name
}

output "dev_worker_az" {
  description = "Availability Zone dedicated to dev workers"
  value       = module.compute.dev_worker_az
}

output "dev_worker_role_name" {
  description = "Environment-specific IAM role name for dev workers"
  value       = module.node_iam.dev_worker_role_name
}

output "prod_worker_asg_name" {
  description = "Name of the prod worker Auto Scaling Group"
  value       = module.compute.prod_worker_asg_name
}

output "prod_worker_az" {
  description = "Availability Zone dedicated to prod workers"
  value       = module.compute.prod_worker_az
}

output "prod_worker_role_name" {
  description = "Environment-specific IAM role name for prod workers"
  value       = module.node_iam.prod_worker_role_name
}

output "vpc_id" {
  description = "VPC ID consumed by the shared edge root"
  value       = module.network.vpc_id
}

output "worker_security_group_id" {
  description = "Security group ID used by both isolated worker groups"
  value       = module.network.worker_security_group_id
}
