variable "cluster_name" {
  description = "Short name used to identify the self-managed Kubernetes cluster"
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,31}$", var.cluster_name))
    error_message = "Cluster name must be 3-32 lowercase letters, digits, or hyphens and start with a letter."
  }
}
