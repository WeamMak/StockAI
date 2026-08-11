terraform {
  required_version = ">= 1.15.0, < 2.0.0"

  backend "s3" {}

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Component = "platform"
      ManagedBy = "Terraform"
      Project   = var.cluster_name
    }
  }
}
