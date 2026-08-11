variable "aws_account_id" {
  description = "Twelve-digit AWS account ID used to scope the join parameter ARN"
  type        = string

  validation {
    condition     = can(regex("^[0-9]{12}$", var.aws_account_id))
    error_message = "AWS account ID must contain exactly 12 digits."
  }
}

variable "aws_region" {
  description = "AWS region containing the encrypted join parameter"
  type        = string

  validation {
    condition     = can(regex("^[a-z]{2}-[a-z]+-[0-9]+$", var.aws_region))
    error_message = "AWS region must be a valid region identifier."
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

variable "control_plane_role_name" {
  description = "Infrastructure-only IAM role allowed to rotate the join parameter"
  type        = string
}

variable "worker_role_names" {
  description = "Environment worker IAM role names allowed to read the join parameter"
  type        = map(string)

  validation {
    condition     = toset(keys(var.worker_role_names)) == toset(["dev", "prod"])
    error_message = "Worker role names must contain exactly dev and prod."
  }
}

