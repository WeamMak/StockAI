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

variable "availability_zones" {
  description = "Two distinct Availability Zones used by the public subnets"
  type        = list(string)

  validation {
    condition = (
      length(var.availability_zones) == 2 &&
      length(distinct(var.availability_zones)) == 2
    )
    error_message = "Exactly two distinct Availability Zones are required."
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

variable "public_subnet_cidrs" {
  description = "Two non-overlapping IPv4 CIDRs for the public subnets"
  type        = list(string)

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

  validation {
    condition     = can(cidrnetmask(var.vpc_cidr))
    error_message = "VPC CIDR must be a valid IPv4 CIDR."
  }
}
