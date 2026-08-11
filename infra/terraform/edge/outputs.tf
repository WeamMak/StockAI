output "alb_dns_name" {
  description = "Public DNS name assigned to the shared ALB"
  value       = module.edge.alb_dns_name
}

output "alb_zone_id" {
  description = "Route 53 canonical hosted-zone ID of the shared ALB"
  value       = module.edge.alb_zone_id
}

output "certificate_arn" {
  description = "ARN of the ACM certificate covering all six hostnames"
  value       = module.edge.certificate_arn
}

output "dev_target_group_arn" {
  description = "ARN of the dev NGINX Ingress target group"
  value       = module.edge.dev_target_group_arn
}

output "hostnames" {
  description = "Six public StockAI hostnames keyed by service and environment"
  value       = module.edge.hostnames
}

output "loki_bucket_arn" {
  description = "ARN of the encrypted operational-log bucket"
  value       = module.edge.loki_bucket_arn
}

output "prod_target_group_arn" {
  description = "ARN of the prod NGINX Ingress target group"
  value       = module.edge.prod_target_group_arn
}
