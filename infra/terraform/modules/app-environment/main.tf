locals {
  application_hostname          = "app.${var.environment}.${var.domain_name}"
  bedrock_model_arn             = "arn:aws:bedrock:${var.aws_region}::foundation-model/openai.gpt-oss-20b-1:0"
  checkpoint_retention_days     = var.environment == "prod" ? 365 : 30
  cognito_groups                = toset(["stockai-procurement-manager", "stockai-procurement-officer"])
  enable_point_in_time_recovery = var.environment == "prod"
  loki_prefix                   = "${var.environment}/"
  secret_names = toset([
    "cron-token",
    "grafana-admin-password",
    "mcp-token",
    "odoo-api-key",
    "odoo-database-password",
    "session-secret",
  ])
  volume_workloads = toset(["odoo", "postgresql", "prometheus"])
}

resource "aws_dynamodb_table" "application" {
  name         = "${var.cluster_name}-${var.environment}-application"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "PK"
  range_key    = "SK"

  attribute {
    name = "PK"
    type = "S"
  }

  attribute {
    name = "SK"
    type = "S"
  }

  point_in_time_recovery {
    enabled = local.enable_point_in_time_recovery
  }

  server_side_encryption {
    enabled = true
  }

  ttl {
    attribute_name = "ttl"
    enabled        = true
  }

  tags = {
    Environment = var.environment
    Name        = "${var.cluster_name}-${var.environment}-application"
    Purpose     = "application-state"
  }
}

resource "aws_dynamodb_table" "checkpoint" {
  name         = "${var.cluster_name}-${var.environment}-checkpoints"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "PK"
  range_key    = "SK"

  attribute {
    name = "PK"
    type = "S"
  }

  attribute {
    name = "SK"
    type = "S"
  }

  point_in_time_recovery {
    enabled = local.enable_point_in_time_recovery
  }

  server_side_encryption {
    enabled = true
  }

  ttl {
    attribute_name = "ttl"
    enabled        = true
  }

  tags = {
    Environment = var.environment
    Name        = "${var.cluster_name}-${var.environment}-checkpoints"
    Purpose     = "langgraph-checkpoints"
    Retention   = "${local.checkpoint_retention_days}-days"
  }
}

resource "aws_secretsmanager_secret" "runtime" {
  for_each = local.secret_names

  name                    = "${var.cluster_name}/${var.environment}/${each.key}"
  description             = "${var.environment} ${replace(each.key, "-", " ")} for StockAI"
  recovery_window_in_days = var.environment == "prod" ? 30 : 7

  tags = {
    Environment = var.environment
    Name        = "${var.cluster_name}-${var.environment}-${each.key}"
    Purpose     = each.key
  }
}

resource "aws_cognito_user_pool" "main" {
  name                     = "${var.cluster_name}-${var.environment}"
  alias_attributes         = ["email"]
  auto_verified_attributes = ["email"]
  deletion_protection      = var.enable_cognito_deletion_protection ? "ACTIVE" : "INACTIVE"
  mfa_configuration        = "OFF"

  account_recovery_setting {
    recovery_mechanism {
      name     = "verified_email"
      priority = 1
    }
  }

  admin_create_user_config {
    allow_admin_create_user_only = true
  }

  password_policy {
    minimum_length                   = 12
    require_lowercase                = true
    require_numbers                  = true
    require_symbols                  = true
    require_uppercase                = true
    temporary_password_validity_days = 7
  }

  schema {
    attribute_data_type = "String"
    mutable             = true
    name                = "email"
    required            = true

    string_attribute_constraints {
      max_length = "320"
      min_length = "3"
    }
  }

  user_attribute_update_settings {
    attributes_require_verification_before_update = ["email"]
  }

  verification_message_template {
    default_email_option = "CONFIRM_WITH_CODE"
  }

  tags = {
    Environment = var.environment
    Name        = "${var.cluster_name}-${var.environment}"
  }
}

resource "aws_cognito_user_pool_client" "web" {
  name         = "${var.cluster_name}-${var.environment}-web"
  user_pool_id = aws_cognito_user_pool.main.id

  access_token_validity                = 60
  allowed_oauth_flows                  = ["code"]
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_scopes                 = ["email", "openid", "profile"]
  auth_session_validity                = 3
  callback_urls                        = ["https://${local.application_hostname}/auth/callback"]
  enable_token_revocation              = true
  generate_secret                      = false
  id_token_validity                    = 60
  logout_urls                          = ["https://${local.application_hostname}/"]
  prevent_user_existence_errors        = "ENABLED"
  refresh_token_validity               = 1
  supported_identity_providers         = ["COGNITO"]

  token_validity_units {
    access_token  = "minutes"
    id_token      = "minutes"
    refresh_token = "days"
  }
}

resource "aws_cognito_user_pool_domain" "main" {
  domain       = "${var.cluster_name}-${var.environment}"
  user_pool_id = aws_cognito_user_pool.main.id
}

resource "aws_cognito_user_group" "procurement" {
  for_each = local.cognito_groups

  description  = "StockAI ${replace(each.key, "stockai-procurement-", "")} role"
  name         = each.key
  user_pool_id = aws_cognito_user_pool.main.id
}

resource "aws_ebs_volume" "data" {
  for_each = local.volume_workloads

  availability_zone = var.worker_availability_zone
  encrypted         = true
  size              = var.data_volume_size_gib
  type              = "gp3"

  tags = {
    Cluster        = var.cluster_name
    Environment    = var.environment
    ManagedBy      = "Terraform"
    Name           = "${var.cluster_name}-${var.environment}-${each.key}-data"
    SnapshotPolicy = var.environment == "prod" && contains(["odoo", "postgresql"], each.key) ? "prod-erp-daily" : "none"
    Workload       = each.key
  }

}

data "aws_iam_policy_document" "worker_application" {
  statement {
    sid = "EnvironmentDynamoDB"

    actions = [
      "dynamodb:BatchGetItem",
      "dynamodb:BatchWriteItem",
      "dynamodb:DeleteItem",
      "dynamodb:DescribeTable",
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:Query",
      "dynamodb:TransactGetItems",
      "dynamodb:TransactWriteItems",
      "dynamodb:UpdateItem",
    ]
    resources = [
      aws_dynamodb_table.application.arn,
      aws_dynamodb_table.checkpoint.arn,
    ]
  }

  statement {
    sid = "EnvironmentSecretRead"

    actions = [
      "secretsmanager:DescribeSecret",
      "secretsmanager:GetSecretValue",
    ]
    resources = [for secret in aws_secretsmanager_secret.runtime : secret.arn]
  }

  statement {
    sid       = "LokiPrefixList"
    actions   = ["s3:ListBucket"]
    resources = [var.loki_bucket_arn]

    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = [local.loki_prefix, "${local.loki_prefix}*"]
    }
  }

  statement {
    sid = "LokiPrefixObjects"

    actions = [
      "s3:DeleteObject",
      "s3:GetObject",
      "s3:PutObject",
    ]
    resources = ["${var.loki_bucket_arn}/${local.loki_prefix}*"]
  }

  statement {
    sid = "ApprovedBedrockModel"

    actions = [
      "bedrock:InvokeModel",
      "bedrock:InvokeModelWithResponseStream",
    ]
    resources = [local.bedrock_model_arn]
  }

  # CloudWatch metric query actions do not support resource-level ARNs.
  statement {
    sid = "CloudWatchMetricsRead"

    actions = [
      "cloudwatch:GetMetricData",
      "cloudwatch:GetMetricStatistics",
      "cloudwatch:GetMetricWidgetImage",
      "cloudwatch:ListMetrics",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "worker_application" {
  name   = "${var.cluster_name}-${var.environment}-application"
  policy = data.aws_iam_policy_document.worker_application.json
  role   = var.worker_role_name
}

data "aws_iam_policy_document" "odoo_bootstrap" {
  statement {
    sid       = "WriteExactOdooApiKey"
    actions   = ["secretsmanager:PutSecretValue"]
    resources = [aws_secretsmanager_secret.runtime["odoo-api-key"].arn]
  }
}

resource "aws_iam_policy" "odoo_bootstrap" {
  count = var.enable_odoo_key_bootstrap ? 1 : 0

  name        = "${var.cluster_name}-${var.environment}-odoo-key-bootstrap"
  description = "Temporary exact-secret write for the finite ${var.environment} Odoo key job"
  policy      = data.aws_iam_policy_document.odoo_bootstrap.json

  tags = {
    Environment = var.environment
    Purpose     = "temporary-odoo-key-bootstrap"
  }
}

resource "aws_iam_role_policy_attachment" "odoo_bootstrap" {
  count = var.enable_odoo_key_bootstrap ? 1 : 0

  policy_arn = aws_iam_policy.odoo_bootstrap[0].arn
  role       = var.worker_role_name
}

data "aws_iam_policy_document" "control_plane_ebs" {
  statement {
    sid = "AttachEnvironmentDataVolumes"

    actions = [
      "ec2:AttachVolume",
      "ec2:DetachVolume",
    ]
    resources = concat(
      [for volume in aws_ebs_volume.data : volume.arn],
      ["arn:aws:ec2:${var.aws_region}:${var.aws_account_id}:instance/*"],
    )

    condition {
      test     = "StringEquals"
      variable = "ec2:ResourceTag/Environment"
      values   = [var.environment]
    }

    condition {
      test     = "StringEquals"
      variable = "ec2:ResourceTag/Owner"
      values   = [var.owner_name]
    }
  }

  statement {
    sid = "DescribeEnvironmentVolumes"

    actions = [
      "ec2:DescribeAvailabilityZones",
      "ec2:DescribeInstances",
      "ec2:DescribeVolumes",
      "ec2:DescribeVolumesModifications",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "control_plane_ebs" {
  name   = "${var.cluster_name}-${var.environment}-ebs-csi"
  policy = data.aws_iam_policy_document.control_plane_ebs.json
  role   = var.control_plane_role_name
}

data "aws_iam_policy_document" "dlm_assume_role" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["dlm.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "dlm" {
  count = var.environment == "prod" ? 1 : 0

  name               = "${var.cluster_name}-prod-dlm"
  description        = "Crash-consistent snapshots of tagged prod ERP volumes"
  assume_role_policy = data.aws_iam_policy_document.dlm_assume_role.json

  tags = {
    Environment = "prod"
    Purpose     = "erp-volume-recovery"
  }
}

data "aws_iam_policy_document" "dlm" {
  statement {
    actions = [
      "ec2:CreateSnapshot",
      "ec2:CreateSnapshots",
      "ec2:CreateTags",
      "ec2:DeleteSnapshot",
      "ec2:DescribeSnapshots",
      "ec2:DescribeTags",
      "ec2:DescribeVolumes",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "dlm" {
  count = var.environment == "prod" ? 1 : 0

  name   = "${var.cluster_name}-prod-dlm"
  policy = data.aws_iam_policy_document.dlm.json
  role   = aws_iam_role.dlm[0].name
}

resource "aws_dlm_lifecycle_policy" "prod_erp" {
  count = var.environment == "prod" ? 1 : 0

  description        = "Seven daily crash-consistent snapshots of prod Odoo and PostgreSQL"
  execution_role_arn = aws_iam_role.dlm[0].arn
  state              = "ENABLED"

  policy_details {
    resource_types = ["VOLUME"]
    target_tags = {
      SnapshotPolicy = "prod-erp-daily"
    }

    schedule {
      name      = "prod-erp-daily"
      copy_tags = true

      create_rule {
        interval      = 24
        interval_unit = "HOURS"
        times         = ["03:00"]
      }

      retain_rule {
        count = 7
      }

      tags_to_add = {
        ManagedBy = "DLM"
        Purpose   = "prod-erp-recovery"
      }
    }
  }

  tags = {
    Environment = "prod"
    Name        = "${var.cluster_name}-prod-erp-daily"
  }
}
