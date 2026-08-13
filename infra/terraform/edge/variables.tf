variable "alb_subnet_ids" {
  description = "Two T16 public subnet IDs used by the shared internet-facing ALB"
  type        = list(string)

  validation {
    condition = (
      length(var.alb_subnet_ids) == 2 &&
      length(distinct(var.alb_subnet_ids)) == 2 &&
      alltrue([for subnet_id in var.alb_subnet_ids : can(regex("^subnet-[0-9a-f]{8,17}$", subnet_id))])
    )
    error_message = "Exactly two distinct EC2 subnet IDs are required."
  }
}

variable "aws_region" {
  description = "Approved AWS region for the StockAI edge"
  type        = string
  default     = "us-east-1"

  validation {
    condition     = var.aws_region == "us-east-1"
    error_message = "The approved StockAI region is us-east-1."
  }
}

variable "cluster_name" {
  description = "Owner-prefixed name of the self-managed Kubernetes cluster"
  type        = string
  default     = "weam-stockai"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,31}$", var.cluster_name))
    error_message = "Cluster name must be 3-32 lowercase letters, digits, or hyphens and start with a letter."
  }
}

variable "domain_name" {
  description = "Existing user-owned Route 53 domain used for all six public hostnames"
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$", var.domain_name))
    error_message = "Domain name must be a lowercase fully qualified DNS name without a trailing dot."
  }
}

variable "loki_bucket_name" {
  description = "Globally unique S3 bucket name for encrypted dev and prod Loki objects"
  type        = string

  validation {
    condition = (
      length(var.loki_bucket_name) >= 3 &&
      length(var.loki_bucket_name) <= 63 &&
      can(regex("^[a-z0-9][a-z0-9.-]*[a-z0-9]$", var.loki_bucket_name))
    )
    error_message = "Loki bucket name must be a valid 3-63 character lowercase S3 bucket name."
  }
}

variable "nginx_http_node_port" {
  description = "Fixed HTTP NodePort exposed by the later NGINX Ingress installation"
  type        = number
  default     = 32080

  validation {
    condition = (
      var.nginx_http_node_port == floor(var.nginx_http_node_port) &&
      var.nginx_http_node_port >= 30000 &&
      var.nginx_http_node_port <= 32767
    )
    error_message = "NGINX HTTP NodePort must be a whole number from 30000 through 32767."
  }
}

variable "owner_name" {
  description = "Owner recorded on shared edge AWS resources"
  type        = string
  default     = "weam"
}

variable "route53_zone_id" {
  description = "ID of the pre-existing user-owned public Route 53 hosted zone"
  type        = string

  validation {
    condition     = can(regex("^Z[A-Z0-9]{8,31}$", var.route53_zone_id))
    error_message = "Route 53 zone ID must start with Z and contain 9-32 uppercase letters or digits."
  }
}

variable "vpc_id" {
  description = "T16 VPC ID used by the shared ALB and target groups"
  type        = string
}

variable "worker_asg_names" {
  description = "T16 worker Auto Scaling Group names keyed exactly by dev and prod"
  type        = map(string)

  validation {
    condition     = toset(keys(var.worker_asg_names)) == toset(["dev", "prod"])
    error_message = "Worker ASG names must contain exactly dev and prod."
  }
}

variable "worker_security_group_id" {
  description = "T16 security group ID shared by environment workers"
  type        = string
}
