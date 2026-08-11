variable "aws_account_id" {
  description = "Twelve-digit AWS account ID used for exact lifecycle IAM scopes"
  type        = string

  validation {
    condition     = can(regex("^[0-9]{12}$", var.aws_account_id))
    error_message = "AWS account ID must contain exactly 12 digits."
  }
}

variable "aws_region" {
  description = "AWS region containing the cluster and lifecycle resources"
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

variable "control_plane_instance_id" {
  description = "Fixed control-plane EC2 instance that receives the bounded SSM cleanup command"
  type        = string

  validation {
    condition     = can(regex("^i-[0-9a-f]{8,17}$", var.control_plane_instance_id))
    error_message = "Control-plane instance ID must be a valid lowercase EC2 instance identifier."
  }
}

variable "owner_name" {
  description = "Owner recorded on StockAI lifecycle resources"
  type        = string
}

variable "worker_asg_names" {
  description = "Exact dev and prod worker Auto Scaling Group names"
  type        = map(string)

  validation {
    condition = (
      toset(keys(var.worker_asg_names)) == toset(["dev", "prod"]) &&
      alltrue([for name in values(var.worker_asg_names) : can(regex("^[a-z][a-z0-9-]{2,127}$", name))])
    )
    error_message = "Worker ASG names must contain exactly valid dev and prod names."
  }
}
