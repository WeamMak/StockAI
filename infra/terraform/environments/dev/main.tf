module "application" {
  source = "../../modules/app-environment"

  aws_account_id            = var.aws_account_id
  aws_region                = var.aws_region
  cluster_name              = var.cluster_name
  control_plane_role_name   = var.control_plane_role_name
  data_volume_size_gib      = var.data_volume_size_gib
  domain_name               = var.domain_name
  enable_odoo_key_bootstrap = var.enable_odoo_key_bootstrap
  environment               = "dev"
  loki_bucket_arn           = var.loki_bucket_arn
  owner_name                = var.owner_name
  worker_availability_zone  = var.worker_availability_zone
  worker_role_name          = var.worker_role_name
}
