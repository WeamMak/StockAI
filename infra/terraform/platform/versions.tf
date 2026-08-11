terraform {
  required_version = ">= 1.15.0, < 2.0.0"

  backend "s3" {}

  required_providers {
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.7"
    }

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
      Owner     = var.owner_name
      Project   = var.cluster_name
    }
  }
}
