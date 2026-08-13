locals {
  environment_hostnames = {
    dev = [
      "app.dev.${var.domain_name}",
      "grafana.dev.${var.domain_name}",
      "odoo.dev.${var.domain_name}",
    ]
    prod = [
      "app.prod.${var.domain_name}",
      "grafana.prod.${var.domain_name}",
      "odoo.prod.${var.domain_name}",
    ]
  }
  hostnames = {
    for hostname in flatten(values(local.environment_hostnames)) :
    replace(hostname, ".${var.domain_name}", "") => hostname
  }
  retention = {
    dev  = 14
    prod = 90
  }
}

resource "aws_security_group" "alb" {
  name        = "${var.cluster_name}-alb"
  description = "Public HTTPS edge for StockAI"
  vpc_id      = var.vpc_id

  tags = {
    Environment = "shared"
    Name        = "${var.cluster_name}-alb"
    Role        = "public-edge"
  }
}

resource "aws_vpc_security_group_ingress_rule" "alb_http" {
  security_group_id = aws_security_group.alb.id
  cidr_ipv4         = "0.0.0.0/0"
  description       = "Public HTTP redirected to HTTPS"
  from_port         = 80
  ip_protocol       = "tcp"
  to_port           = 80
}

resource "aws_vpc_security_group_ingress_rule" "alb_https" {
  security_group_id = aws_security_group.alb.id
  cidr_ipv4         = "0.0.0.0/0"
  description       = "Public HTTPS"
  from_port         = 443
  ip_protocol       = "tcp"
  to_port           = 443
}

resource "aws_vpc_security_group_egress_rule" "alb_ingress" {
  security_group_id            = aws_security_group.alb.id
  description                  = "NGINX HTTP NodePort on StockAI workers"
  from_port                    = var.nginx_http_node_port
  ip_protocol                  = "tcp"
  referenced_security_group_id = var.worker_security_group_id
  to_port                      = var.nginx_http_node_port
}

resource "aws_vpc_security_group_ingress_rule" "worker_ingress" {
  security_group_id            = var.worker_security_group_id
  description                  = "NGINX HTTP NodePort and health checks from the ALB only"
  from_port                    = var.nginx_http_node_port
  ip_protocol                  = "tcp"
  referenced_security_group_id = aws_security_group.alb.id
  to_port                      = var.nginx_http_node_port
}

resource "aws_lb" "main" {
  name               = "${var.cluster_name}-edge"
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = var.alb_subnet_ids

  drop_invalid_header_fields = true
  enable_deletion_protection = false

  tags = {
    Environment = "shared"
    Name        = "${var.cluster_name}-edge"
  }
}

resource "aws_lb_target_group" "ingress" {
  for_each = local.environment_hostnames

  name        = "${var.cluster_name}-${each.key}-ingress"
  port        = var.nginx_http_node_port
  protocol    = "HTTP"
  target_type = "instance"
  vpc_id      = var.vpc_id

  deregistration_delay = 30

  health_check {
    enabled             = true
    healthy_threshold   = 2
    interval            = 30
    matcher             = "200-399"
    path                = "/healthz"
    port                = "traffic-port"
    protocol            = "HTTP"
    timeout             = 5
    unhealthy_threshold = 3
  }

  tags = {
    Environment = each.key
    Name        = "${var.cluster_name}-${each.key}-ingress"
  }
}

resource "aws_autoscaling_attachment" "ingress" {
  for_each = var.worker_asg_names

  autoscaling_group_name = each.value
  lb_target_group_arn    = aws_lb_target_group.ingress[each.key].arn
}

resource "aws_acm_certificate" "edge" {
  domain_name = local.hostnames["app.dev"]
  subject_alternative_names = sort([
    for key, hostname in local.hostnames : hostname if key != "app.dev"
  ])
  validation_method = "DNS"

  lifecycle {
    create_before_destroy = true
  }

  tags = {
    Environment = "shared"
    Name        = "${var.cluster_name}-edge"
  }
}

resource "aws_route53_record" "certificate_validation" {
  for_each = {
    for option in aws_acm_certificate.edge.domain_validation_options :
    option.domain_name => {
      name   = option.resource_record_name
      record = option.resource_record_value
      type   = option.resource_record_type
    }
  }

  allow_overwrite = true
  name            = each.value.name
  records         = [each.value.record]
  ttl             = 60
  type            = each.value.type
  zone_id         = var.route53_zone_id
}

resource "aws_acm_certificate_validation" "edge" {
  certificate_arn         = aws_acm_certificate.edge.arn
  validation_record_fqdns = [for record in aws_route53_record.certificate_validation : record.fqdn]
}

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.main.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type = "redirect"

    redirect {
      port        = "443"
      protocol    = "HTTPS"
      status_code = "HTTP_301"
    }
  }
}

resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.main.arn
  certificate_arn   = aws_acm_certificate_validation.edge.certificate_arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"

  default_action {
    type = "fixed-response"

    fixed_response {
      content_type = "text/plain"
      message_body = "unknown host"
      status_code  = "404"
    }
  }
}

resource "aws_lb_listener_rule" "environment" {
  for_each = local.environment_hostnames

  listener_arn = aws_lb_listener.https.arn
  priority     = each.key == "dev" ? 100 : 200

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.ingress[each.key].arn
  }

  condition {
    host_header {
      values = each.value
    }
  }
}

resource "aws_route53_record" "application" {
  for_each = toset(values(local.hostnames))

  name    = each.value
  type    = "A"
  zone_id = var.route53_zone_id

  alias {
    evaluate_target_health = true
    name                   = aws_lb.main.dns_name
    zone_id                = aws_lb.main.zone_id
  }
}

resource "aws_s3_bucket" "loki" {
  bucket        = var.loki_bucket_name
  force_destroy = false

  tags = {
    Environment = "shared"
    Name        = var.loki_bucket_name
    Purpose     = "loki-operational-logs"
  }
}

resource "aws_s3_bucket_ownership_controls" "loki" {
  bucket = aws_s3_bucket.loki.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_public_access_block" "loki" {
  bucket = aws_s3_bucket.loki.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "loki" {
  bucket = aws_s3_bucket.loki.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_versioning" "loki" {
  bucket = aws_s3_bucket.loki.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "loki" {
  bucket = aws_s3_bucket.loki.id

  dynamic "rule" {
    for_each = local.retention

    content {
      id     = "${rule.key}-retention"
      status = "Enabled"

      expiration {
        days = rule.value
      }

      filter {
        prefix = "${rule.key}/"
      }

      noncurrent_version_expiration {
        noncurrent_days = rule.value
      }
    }
  }

  depends_on = [aws_s3_bucket_versioning.loki]
}

data "aws_iam_policy_document" "loki_bucket" {
  statement {
    sid    = "DenyInsecureTransport"
    effect = "Deny"

    actions = ["s3:*"]
    resources = [
      aws_s3_bucket.loki.arn,
      "${aws_s3_bucket.loki.arn}/*",
    ]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "loki" {
  bucket = aws_s3_bucket.loki.id
  policy = data.aws_iam_policy_document.loki_bucket.json
}
