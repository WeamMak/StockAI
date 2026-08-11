output "alb_subnet_ids" {
  description = "Public subnet IDs available to the later shared ALB"
  value       = [for zone in var.availability_zones : aws_subnet.public[zone].id]
}

output "control_plane_security_group_id" {
  description = "Security group ID for the fixed control-plane instance"
  value       = aws_security_group.control_plane.id
}

output "public_subnet_ids_by_az" {
  description = "Public subnet IDs keyed by Availability Zone"
  value       = { for zone in var.availability_zones : zone => aws_subnet.public[zone].id }
}

output "vpc_id" {
  description = "ID of the StockAI VPC"
  value       = aws_vpc.main.id
}

output "worker_security_group_id" {
  description = "Security group ID shared by the environment worker groups"
  value       = aws_security_group.worker.id
}
