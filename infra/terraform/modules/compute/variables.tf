variable "ami_id" {
  description = "Approved Ubuntu AMI ID used by every cluster node"
  type        = string

  validation {
    condition     = can(regex("^ami-[0-9a-f]{8,17}$", var.ami_id))
    error_message = "AMI ID must be a valid lowercase EC2 AMI identifier."
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

variable "control_plane_instance_profile_name" {
  description = "IAM instance profile name for the control plane"
  type        = string
}

variable "control_plane_security_group_id" {
  description = "Security group ID for the control plane"
  type        = string
}

variable "control_plane_subnet_id" {
  description = "Public subnet ID for the fixed control plane"
  type        = string
}

variable "owner_name" {
  description = "Owner tag propagated to cluster instances, volumes, and worker ASGs"
  type        = string
}

variable "worker_availability_zones" {
  description = "Worker Availability Zones keyed by dev and prod environment"
  type        = map(string)

  validation {
    condition     = toset(keys(var.worker_availability_zones)) == toset(["dev", "prod"])
    error_message = "Worker Availability Zones must contain exactly dev and prod."
  }
}

variable "worker_capacity" {
  description = "Baseline explicit capacity for each environment worker ASG"
  type        = object({ min = number, desired = number, max = number })
  default     = { min = 1, desired = 1, max = 3 }

  validation {
    condition = (
      (var.worker_capacity.min == 0 && var.worker_capacity.desired == 0 && var.worker_capacity.max == 3) ||
      (var.worker_capacity.min == 1 && var.worker_capacity.desired >= 1 && var.worker_capacity.desired <= 3 && var.worker_capacity.max == 3)
    )
    error_message = "use inactive 0/0/3 or active 1/<1..3>/3 capacity"
  }

  validation {
    condition = alltrue([
      for value in [var.worker_capacity.min, var.worker_capacity.desired, var.worker_capacity.max] :
      value == floor(value)
    ])
    error_message = "Worker capacity values must be whole numbers."
  }
}

variable "worker_capacity_overrides" {
  description = "Optional explicit capacity overrides keyed by dev or prod"
  type        = map(object({ min = number, desired = number, max = number }))
  default     = {}

  validation {
    condition = alltrue([
      for environment, capacity in var.worker_capacity_overrides :
      contains(["dev", "prod"], environment) &&
      (
        (capacity.min == 0 && capacity.desired == 0 && capacity.max == 3) ||
        (capacity.min == 1 && capacity.desired >= 1 && capacity.desired <= 3 && capacity.max == 3)
      ) &&
      alltrue([for value in [capacity.min, capacity.desired, capacity.max] : value == floor(value)])
    ])
    error_message = "Capacity overrides must target dev or prod and use inactive 0/0/3 or active 1/<1..3>/3 whole-number capacity."
  }
}

variable "worker_instance_profile_names" {
  description = "Worker IAM instance profile names keyed by dev and prod environment"
  type        = map(string)

  validation {
    condition     = toset(keys(var.worker_instance_profile_names)) == toset(["dev", "prod"])
    error_message = "Worker instance profile names must contain exactly dev and prod."
  }
}

variable "worker_security_group_id" {
  description = "Security group ID shared by the environment worker groups"
  type        = string
}

variable "worker_subnet_ids" {
  description = "Single worker subnet IDs keyed by dev and prod environment"
  type        = map(string)

  validation {
    condition     = toset(keys(var.worker_subnet_ids)) == toset(["dev", "prod"])
    error_message = "Worker subnet IDs must contain exactly dev and prod."
  }
}
