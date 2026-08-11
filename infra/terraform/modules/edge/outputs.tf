output "alb_dns_name" {
  description = "Public DNS name assigned to the shared ALB"
  value       = aws_lb.main.dns_name
}

output "alb_zone_id" {
  description = "Route 53 canonical hosted-zone ID of the shared ALB"
  value       = aws_lb.main.zone_id
}

output "certificate_arn" {
  description = "ARN of the DNS-validated ACM certificate covering all six hostnames"
  value       = aws_acm_certificate_validation.edge.certificate_arn
}

output "dev_target_group_arn" {
  description = "ARN of the dev NGINX Ingress target group"
  value       = aws_lb_target_group.ingress["dev"].arn
}

output "hostnames" {
  description = "Six public StockAI hostnames keyed by service and environment"
  value       = local.hostnames
}

output "loki_bucket_arn" {
  description = "ARN of the encrypted operational-log bucket"
  value       = aws_s3_bucket.loki.arn
}

output "prod_target_group_arn" {
  description = "ARN of the prod NGINX Ingress target group"
  value       = aws_lb_target_group.ingress["prod"].arn
}
