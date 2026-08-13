module "edge" {
  source = "../modules/edge"

  alb_subnet_ids           = var.alb_subnet_ids
  cluster_name             = var.cluster_name
  domain_name              = var.domain_name
  loki_bucket_name         = var.loki_bucket_name
  nginx_http_node_port     = var.nginx_http_node_port
  owner_name               = var.owner_name
  route53_zone_id          = var.route53_zone_id
  vpc_id                   = var.vpc_id
  worker_asg_names         = var.worker_asg_names
  worker_security_group_id = var.worker_security_group_id
}
