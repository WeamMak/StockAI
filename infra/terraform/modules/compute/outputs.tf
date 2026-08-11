output "control_plane_instance_id" {
  description = "EC2 instance ID of the fixed control plane"
  value       = aws_instance.control_plane.id
}

output "control_plane_private_ip" {
  description = "Private IPv4 address of the fixed control plane"
  value       = aws_instance.control_plane.private_ip
}

output "dev_worker_asg_name" {
  description = "Name of the dev worker Auto Scaling Group"
  value       = aws_autoscaling_group.worker["dev"].name
}

output "dev_worker_az" {
  description = "Availability Zone dedicated to dev workers"
  value       = local.workers["dev"].availability_zone
}

output "prod_worker_asg_name" {
  description = "Name of the prod worker Auto Scaling Group"
  value       = aws_autoscaling_group.worker["prod"].name
}

output "prod_worker_az" {
  description = "Availability Zone dedicated to prod workers"
  value       = local.workers["prod"].availability_zone
}
