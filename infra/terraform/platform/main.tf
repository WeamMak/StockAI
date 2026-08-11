locals {
  dev_availability_zone  = var.availability_zones[0]
  prod_availability_zone = var.availability_zones[1]
}

module "network" {
  source = "../modules/network"

  administrator_cidr  = var.administrator_cidr
  availability_zones  = var.availability_zones
  cluster_name        = var.cluster_name
  public_subnet_cidrs = var.public_subnet_cidrs
  vpc_cidr            = var.vpc_cidr
}

module "node_iam" {
  source = "../modules/node-iam"

  cluster_name = var.cluster_name
}

module "compute" {
  source = "../modules/compute"

  ami_id                              = var.ami_id
  cluster_name                        = var.cluster_name
  control_plane_instance_profile_name = module.node_iam.control_plane_instance_profile_name
  control_plane_security_group_id     = module.network.control_plane_security_group_id
  control_plane_subnet_id             = module.network.public_subnet_ids_by_az[local.dev_availability_zone]
  owner_name                          = var.owner_name
  worker_availability_zones = {
    dev  = local.dev_availability_zone
    prod = local.prod_availability_zone
  }
  worker_capacity           = var.worker_capacity
  worker_capacity_overrides = var.worker_capacity_overrides
  worker_instance_profile_names = {
    dev  = module.node_iam.dev_worker_instance_profile_name
    prod = module.node_iam.prod_worker_instance_profile_name
  }
  worker_security_group_id = module.network.worker_security_group_id
  worker_subnet_ids = {
    dev  = module.network.public_subnet_ids_by_az[local.dev_availability_zone]
    prod = module.network.public_subnet_ids_by_az[local.prod_availability_zone]
  }
}
