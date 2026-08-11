variable "aws_account_id" {
  description = "Twelve-digit AWS account ID that owns the environment resources"
  type        = string

  validation {
    condition     = can(regex("^[0-9]{12}$", var.aws_account_id))
    error_message = "AWS account ID must contain exactly 12 digits."
  }
}

variable "aws_region" {
  description = "Approved AWS region for the StockAI environment"
  type        = string

  validation {
    condition     = var.aws_region == "us-east-1"
    error_message = "The approved StockAI region is us-east-1."
  }
}

variable "cluster_name" {
  description = "Owner-prefixed name of the self-managed Kubernetes cluster"
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,31}$", var.cluster_name))
    error_message = "Cluster name must be 3-32 lowercase letters, digits, or hyphens and start with a letter."
  }
}

variable "control_plane_role_name" {
  description = "T16 control-plane IAM role that runs the EBS CSI controller"
  type        = string

  validation {
    condition     = length(trimspace(var.control_plane_role_name)) > 0
    error_message = "Control-plane role name must not be empty."
  }
}

variable "data_volume_size_gib" {
  description = "Initial GiB size of each retained environment data volume"
  type        = number
  default     = 5

  validation {
    condition = (
      var.data_volume_size_gib == floor(var.data_volume_size_gib) &&
      var.data_volume_size_gib >= 5 &&
      var.data_volume_size_gib <= 100
    )
    error_message = "Data volume size must be a whole number from 5 through 100 GiB."
  }
}

variable "domain_name" {
  description = "Existing user-owned DNS domain used for the environment callback URL"
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$", var.domain_name))
    error_message = "Domain name must be a lowercase fully qualified DNS name without a trailing dot."
  }
}

variable "enable_odoo_key_bootstrap" {
  description = "Temporarily attach exact-secret write permission for the finite Odoo key bootstrap job"
  type        = bool
  default     = false
}

variable "environment" {
  description = "Isolated StockAI application environment"
  type        = string

  validation {
    condition     = contains(["dev", "prod"], var.environment)
    error_message = "Environment must be dev or prod."
  }
}

variable "loki_bucket_arn" {
  description = "ARN of the shared encrypted operational-log bucket created by the edge root"
  type        = string

  validation {
    condition     = can(regex("^arn:aws:s3:::[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$", var.loki_bucket_arn))
    error_message = "Loki bucket ARN must be a valid S3 bucket ARN without an object suffix."
  }
}

variable "owner_name" {
  description = "Owner recorded on StockAI AWS resources"
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,31}$", var.owner_name))
    error_message = "Owner name must be 2-32 lowercase letters, digits, or hyphens and start with a letter."
  }
}

variable "worker_availability_zone" {
  description = "T16 Availability Zone dedicated to this environment worker ASG"
  type        = string

  validation {
    condition     = startswith(var.worker_availability_zone, "us-east-1")
    error_message = "Worker Availability Zone must be in us-east-1."
  }
}

variable "worker_role_name" {
  description = "T16 IAM role used only by this environment's workers"
  type        = string

  validation {
    condition     = length(trimspace(var.worker_role_name)) > 0
    error_message = "Worker role name must not be empty."
  }
}
