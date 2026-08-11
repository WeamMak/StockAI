variable "aws_account_id" {
  description = "Twelve-digit AWS account ID that owns the dev resources"
  type        = string

  validation {
    condition     = can(regex("^[0-9]{12}$", var.aws_account_id))
    error_message = "AWS account ID must contain exactly 12 digits."
  }
}

variable "aws_region" {
  description = "Approved AWS region for the StockAI dev environment"
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

variable "control_plane_role_name" {
  description = "T16 control-plane IAM role name"
  type        = string
}

variable "data_volume_size_gib" {
  description = "Initial GiB size of each retained dev data volume"
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
  description = "Existing user-owned DNS domain used for the dev callback URL"
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$", var.domain_name))
    error_message = "Domain name must be a lowercase fully qualified DNS name without a trailing dot."
  }
}

variable "enable_odoo_key_bootstrap" {
  description = "Temporarily attach exact-secret write permission for the finite dev Odoo key job"
  type        = bool
  default     = false
}

variable "loki_bucket_arn" {
  description = "ARN of the shared encrypted operational-log bucket"
  type        = string
}

variable "owner_name" {
  description = "Owner recorded on dev AWS resources"
  type        = string
  default     = "weam"
}

variable "worker_availability_zone" {
  description = "T16 Availability Zone dedicated to the dev worker ASG"
  type        = string
}

variable "worker_role_name" {
  description = "T16 IAM role name used only by dev workers"
  type        = string
}
