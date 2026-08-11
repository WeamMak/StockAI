variable "aws_account_id" {
  description = "Twelve-digit AWS account ID used for exact EBS CSI resource scopes"
  type        = string

  validation {
    condition     = can(regex("^[0-9]{12}$", var.aws_account_id))
    error_message = "AWS account ID must contain exactly 12 digits."
  }
}

variable "aws_region" {
  description = "AWS region containing the cluster data volumes and worker instances"
  type        = string

  validation {
    condition     = var.aws_region == "us-east-1"
    error_message = "The approved StockAI region is us-east-1."
  }
}

variable "cluster_name" {
  description = "Short name used to identify the self-managed Kubernetes cluster"
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,31}$", var.cluster_name))
    error_message = "Cluster name must be 3-32 lowercase letters, digits, or hyphens and start with a letter."
  }
}

variable "owner_name" {
  description = "Owner tag required on volumes and workers managed by the EBS CSI controller"
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,31}$", var.owner_name))
    error_message = "Owner name must be 2-32 lowercase letters, digits, or hyphens and start with a letter."
  }
}
