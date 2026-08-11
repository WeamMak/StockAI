variable "administrator_cidr" {
  description = "Trusted IPv4 CIDR allowed to administer the cluster nodes and API"
  type        = string

  validation {
    condition = (
      can(cidrnetmask(var.administrator_cidr)) &&
      length(split(".", split("/", var.administrator_cidr)[0])) == 4
    )
    error_message = "Administrator CIDR must be a valid IPv4 CIDR. Prefer one trusted /32 address."
  }
}

variable "ami_id" {
  description = "Approved Ubuntu AMI ID used by every cluster node"
  type        = string

  validation {
    condition     = can(regex("^ami-[0-9a-f]{8,17}$", var.ami_id))
    error_message = "AMI ID must be a valid lowercase EC2 AMI identifier."
  }
}

variable "availability_zones" {
  description = "Two distinct us-east-1 Availability Zones for dev and prod"
  type        = list(string)
  default     = ["us-east-1a", "us-east-1b"]

  validation {
    condition = (
      length(var.availability_zones) == 2 &&
      length(distinct(var.availability_zones)) == 2 &&
      alltrue([for zone in var.availability_zones : startswith(zone, "us-east-1")])
    )
    error_message = "Exactly two distinct us-east-1 Availability Zones are required."
  }
}

variable "aws_region" {
  description = "Approved AWS region for the StockAI platform"
  type        = string
  default     = "us-east-1"

  validation {
    condition     = var.aws_region == "us-east-1"
    error_message = "The approved StockAI region is us-east-1."
  }
}

variable "cluster_name" {
  description = "Owner-prefixed name used to identify the self-managed Kubernetes cluster"
  type        = string
  default     = "weam-stockai"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,31}$", var.cluster_name))
    error_message = "Cluster name must be 3-32 lowercase letters, digits, or hyphens and start with a letter."
  }
}

variable "owner_name" {
  description = "Owner recorded on StockAI AWS resources"
  type        = string
  default     = "weam"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,31}$", var.owner_name))
    error_message = "Owner name must be 2-32 lowercase letters, digits, or hyphens and start with a letter."
  }
}

variable "public_subnet_cidrs" {
  description = "Two non-overlapping public subnet CIDRs ordered like availability_zones"
  type        = list(string)
  default     = ["10.0.1.0/24", "10.0.2.0/24"]

  validation {
    condition = (
      length(var.public_subnet_cidrs) == 2 &&
      length(distinct(var.public_subnet_cidrs)) == 2 &&
      alltrue([for cidr in var.public_subnet_cidrs : can(cidrnetmask(cidr))])
    )
    error_message = "Exactly two distinct valid IPv4 subnet CIDRs are required."
  }
}

variable "vpc_cidr" {
  description = "IPv4 CIDR for the StockAI VPC"
  type        = string
  default     = "10.0.0.0/16"

  validation {
    condition     = can(cidrnetmask(var.vpc_cidr))
    error_message = "VPC CIDR must be a valid IPv4 CIDR."
  }
}

variable "worker_capacity" {
  description = "Baseline explicit capacity applied to each environment worker ASG"
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
