variable "administrator_cidr" {
  description = "IPv4 CIDR allowed to administer the later self-managed cluster"
  type        = string

  validation {
    condition = (
      can(cidrnetmask(var.administrator_cidr)) &&
      length(split(".", split("/", var.administrator_cidr)[0])) == 4
    )
    error_message = "Administrator CIDR must be a valid IPv4 CIDR. Prefer one trusted /32 address."
  }
}

variable "aws_account_id" {
  description = "Twelve-digit AWS account ID that is allowed to run this bootstrap"
  type        = string

  validation {
    condition     = can(regex("^[0-9]{12}$", var.aws_account_id))
    error_message = "AWS account ID must contain exactly 12 digits."
  }
}

variable "aws_region" {
  description = "AWS region for the Terraform state and locking resources"
  type        = string
  default     = "us-east-1"

  validation {
    condition     = var.aws_region == "us-east-1"
    error_message = "The approved StockAI region is us-east-1."
  }
}

variable "cluster_name" {
  description = "Deterministic StockAI cluster prefix used to scope lifecycle resources"
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,31}$", var.cluster_name))
    error_message = "Cluster name must be 3-32 lowercase letters, digits, or hyphens and start with a letter."
  }
}

variable "github_apply_environments" {
  description = "Protected GitHub environments allowed to assume the Terraform apply role"
  type        = set(string)
  default = [
    "dev",
    "infrastructure-destroy",
    "infrastructure-provision",
    "prod",
  ]

  validation {
    condition = (
      length(var.github_apply_environments) > 0 &&
      alltrue([
        for environment in var.github_apply_environments :
        can(regex("^[A-Za-z0-9._-]+$", environment))
      ])
    )
    error_message = "GitHub apply environments must be non-empty names containing only letters, digits, dots, underscores, or hyphens."
  }
}

variable "github_repository_subject" {
  description = "Immutable GitHub OIDC repository segment in owner@owner-id/repository@repository-id form"
  type        = string

  validation {
    condition = can(regex(
      "^[A-Za-z0-9_.-]+@[0-9]+/[A-Za-z0-9_.-]+@[0-9]+$",
      var.github_repository_subject,
    ))
    error_message = "Repository subject must use immutable GitHub owner and repository IDs, for example owner@123456/repository@789012."
  }
}

variable "project_name" {
  description = "Short lowercase name used to identify StockAI bootstrap IAM resources"
  type        = string
  default     = "stockai"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,31}$", var.project_name))
    error_message = "Project name must be 3-32 lowercase letters, digits, or hyphens and start with a letter."
  }
}

variable "route53_zone_id" {
  description = "Exact existing public hosted zone allowed for StockAI DNS records"
  type        = string

  validation {
    condition     = can(regex("^Z[A-Z0-9]{8,31}$", var.route53_zone_id))
    error_message = "Route 53 hosted-zone ID is invalid."
  }
}

variable "loki_bucket_name" {
  description = "Exact non-bootstrap S3 bucket managed by the edge Terraform root"
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

variable "owner_name" {
  description = "Exact Owner tag required on mutable StockAI lifecycle resources"
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,31}$", var.owner_name))
    error_message = "Owner name must be 2-32 lowercase letters, digits, or hyphens."
  }
}

variable "state_bucket_name" {
  description = "Globally unique S3 bucket name dedicated only to Terraform state"
  type        = string

  validation {
    condition = (
      length(var.state_bucket_name) >= 3 &&
      length(var.state_bucket_name) <= 63 &&
      can(regex("^[a-z0-9][a-z0-9.-]*[a-z0-9]$", var.state_bucket_name))
    )
    error_message = "State bucket name must be a valid 3-63 character lowercase S3 bucket name."
  }
}

variable "state_key_prefix" {
  description = "S3 object prefix reserved for later Terraform root states"
  type        = string
  default     = "stockai"

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9/_-]*[a-z0-9]$", var.state_key_prefix))
    error_message = "State key prefix must contain lowercase letters, digits, slashes, underscores, or hyphens without leading or trailing separators."
  }
}

variable "state_lock_table_name" {
  description = "DynamoDB table name dedicated to Terraform state locks"
  type        = string

  validation {
    condition = (
      length(var.state_lock_table_name) >= 3 &&
      length(var.state_lock_table_name) <= 255 &&
      can(regex("^[A-Za-z0-9_.-]+$", var.state_lock_table_name))
    )
    error_message = "State lock table name must be 3-255 letters, digits, dots, underscores, or hyphens."
  }
}
