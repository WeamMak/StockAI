locals {
  state_list_prefixes = [
    var.state_key_prefix,
    "${var.state_key_prefix}/*",
  ]
  state_object_arn = "${aws_s3_bucket.terraform_state.arn}/${var.state_key_prefix}/*"
}

resource "aws_s3_bucket" "terraform_state" {
  bucket        = var.state_bucket_name
  force_destroy = false

  lifecycle {
    prevent_destroy = true
  }

  tags = {
    Name    = var.state_bucket_name
    Purpose = "TerraformState"
  }
}

resource "aws_s3_bucket_ownership_controls" "terraform_state" {
  bucket = aws_s3_bucket.terraform_state.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_public_access_block" "terraform_state" {
  bucket = aws_s3_bucket.terraform_state.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "terraform_state" {
  bucket = aws_s3_bucket.terraform_state.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_versioning" "terraform_state" {
  bucket = aws_s3_bucket.terraform_state.id

  versioning_configuration {
    status = "Enabled"
  }
}

data "aws_iam_policy_document" "terraform_state_bucket" {
  statement {
    sid     = "DenyInsecureTransport"
    effect  = "Deny"
    actions = ["s3:*"]
    resources = [
      aws_s3_bucket.terraform_state.arn,
      "${aws_s3_bucket.terraform_state.arn}/*",
    ]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "terraform_state" {
  bucket = aws_s3_bucket.terraform_state.id
  policy = data.aws_iam_policy_document.terraform_state_bucket.json

  depends_on = [aws_s3_bucket_public_access_block.terraform_state]
}

resource "aws_dynamodb_table" "terraform_lock" {
  name         = var.state_lock_table_name
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "LockID"

  attribute {
    name = "LockID"
    type = "S"
  }

  server_side_encryption {
    enabled = true
  }

  lifecycle {
    prevent_destroy = true
  }

  tags = {
    Name    = var.state_lock_table_name
    Purpose = "TerraformStateLock"
  }
}

resource "aws_iam_openid_connect_provider" "github_actions" {
  url = "https://token.actions.githubusercontent.com"

  client_id_list = ["sts.amazonaws.com"]

  tags = {
    Name = "${var.project_name}-github-actions"
  }
}

data "aws_iam_policy_document" "github_plan_trust" {
  statement {
    sid     = "GitHubReadOnlyPlan"
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github_actions.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values = [
        "repo:${var.github_repository_subject}:pull_request",
        "repo:${var.github_repository_subject}:ref:refs/heads/main",
      ]
    }
  }
}

data "aws_iam_policy_document" "github_apply_trust" {
  statement {
    sid     = "GitHubProtectedEnvironmentApply"
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github_actions.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values = [
        for environment in sort(tolist(var.github_apply_environments)) :
        "repo:${var.github_repository_subject}:environment:${environment}"
      ]
    }
  }
}

resource "aws_iam_role" "github_plan" {
  name                 = "${var.project_name}-github-terraform-plan"
  description          = "GitHub pull-request and manual-main role for read-only Terraform plans"
  assume_role_policy   = data.aws_iam_policy_document.github_plan_trust.json
  max_session_duration = 3600
}

resource "aws_iam_role" "github_apply" {
  name                 = "${var.project_name}-github-terraform-apply"
  description          = "Protected GitHub environment role for approved Terraform applies"
  assume_role_policy   = data.aws_iam_policy_document.github_apply_trust.json
  max_session_duration = 3600
}

data "aws_iam_policy_document" "github_plan_state_access" {
  statement {
    sid       = "ListStatePrefix"
    effect    = "Allow"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.terraform_state.arn]

    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = local.state_list_prefixes
    }
  }

  statement {
    sid       = "ReadStateObjects"
    effect    = "Allow"
    actions   = ["s3:GetObject"]
    resources = [local.state_object_arn]
  }

  statement {
    sid    = "LockState"
    effect = "Allow"
    actions = [
      "dynamodb:DeleteItem",
      "dynamodb:DescribeTable",
      "dynamodb:GetItem",
      "dynamodb:PutItem",
    ]
    resources = [aws_dynamodb_table.terraform_lock.arn]
  }
}

data "aws_iam_policy_document" "github_apply_state_access" {
  statement {
    sid       = "ListStatePrefix"
    effect    = "Allow"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.terraform_state.arn]

    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = local.state_list_prefixes
    }
  }

  statement {
    sid     = "ReadWriteStateObjects"
    effect  = "Allow"
    actions = ["s3:GetObject", "s3:PutObject"]
    resources = [
      local.state_object_arn,
    ]
  }

  statement {
    sid    = "LockState"
    effect = "Allow"
    actions = [
      "dynamodb:DeleteItem",
      "dynamodb:DescribeTable",
      "dynamodb:GetItem",
      "dynamodb:PutItem",
    ]
    resources = [aws_dynamodb_table.terraform_lock.arn]
  }
}

resource "aws_iam_policy" "github_plan_state_access" {
  name        = "${var.project_name}-github-terraform-plan-state"
  description = "Read and lock only StockAI Terraform state"
  policy      = data.aws_iam_policy_document.github_plan_state_access.json
}

resource "aws_iam_policy" "github_apply_state_access" {
  name        = "${var.project_name}-github-terraform-apply-state"
  description = "Read, write, and lock only StockAI Terraform state"
  policy      = data.aws_iam_policy_document.github_apply_state_access.json
}

resource "aws_iam_role_policy_attachment" "github_plan_state_access" {
  role       = aws_iam_role.github_plan.name
  policy_arn = aws_iam_policy.github_plan_state_access.arn
}

resource "aws_iam_role_policy_attachment" "github_apply_state_access" {
  role       = aws_iam_role.github_apply.name
  policy_arn = aws_iam_policy.github_apply_state_access.arn
}

resource "aws_iam_role_policy_attachment" "github_plan_read_only" {
  role       = aws_iam_role.github_plan.name
  policy_arn = "arn:aws:iam::aws:policy/ReadOnlyAccess"
}

resource "aws_iam_role_policy_attachment" "github_apply_administrator" {
  role       = aws_iam_role.github_apply.name
  policy_arn = "arn:aws:iam::aws:policy/AdministratorAccess"
}

data "aws_iam_policy_document" "github_apply_foundation_protection" {
  statement {
    sid    = "DenyStateBucketMutation"
    effect = "Deny"
    actions = [
      "s3:DeleteBucket*",
      "s3:PutBucket*",
      "s3:PutEncryptionConfiguration",
      "s3:PutLifecycleConfiguration",
      "s3:PutReplicationConfiguration",
    ]
    resources = [aws_s3_bucket.terraform_state.arn]
  }

  statement {
    sid     = "DenyStateObjectDeletion"
    effect  = "Deny"
    actions = ["s3:DeleteObject", "s3:DeleteObjectVersion"]
    resources = [
      local.state_object_arn,
    ]
  }

  statement {
    sid    = "DenyLockTableMutation"
    effect = "Deny"
    actions = [
      "dynamodb:DeleteTable",
      "dynamodb:TagResource",
      "dynamodb:UntagResource",
      "dynamodb:UpdateContinuousBackups",
      "dynamodb:UpdateContributorInsights",
      "dynamodb:UpdateTable",
      "dynamodb:UpdateTableReplicaAutoScaling",
      "dynamodb:UpdateTimeToLive",
    ]
    resources = [aws_dynamodb_table.terraform_lock.arn]
  }

  statement {
    sid    = "DenyOIDCProviderMutation"
    effect = "Deny"
    actions = [
      "iam:AddClientIDToOpenIDConnectProvider",
      "iam:DeleteOpenIDConnectProvider",
      "iam:RemoveClientIDFromOpenIDConnectProvider",
      "iam:TagOpenIDConnectProvider",
      "iam:UntagOpenIDConnectProvider",
      "iam:UpdateOpenIDConnectProviderThumbprint",
    ]
    resources = [aws_iam_openid_connect_provider.github_actions.arn]
  }

  statement {
    sid    = "DenyBootstrapRoleMutation"
    effect = "Deny"
    actions = [
      "iam:AttachRolePolicy",
      "iam:DeleteRole",
      "iam:DeleteRolePermissionsBoundary",
      "iam:DeleteRolePolicy",
      "iam:DetachRolePolicy",
      "iam:PutRolePermissionsBoundary",
      "iam:PutRolePolicy",
      "iam:TagRole",
      "iam:UntagRole",
      "iam:UpdateAssumeRolePolicy",
      "iam:UpdateRole",
      "iam:UpdateRoleDescription",
    ]
    resources = [
      aws_iam_role.github_plan.arn,
      aws_iam_role.github_apply.arn,
    ]
  }

  statement {
    sid    = "DenyBootstrapPolicyMutation"
    effect = "Deny"
    actions = [
      "iam:CreatePolicyVersion",
      "iam:DeletePolicy",
      "iam:DeletePolicyVersion",
      "iam:SetDefaultPolicyVersion",
      "iam:TagPolicy",
      "iam:UntagPolicy",
    ]
    resources = [
      "arn:aws:iam::${var.aws_account_id}:policy/${var.project_name}-github-terraform-plan-state",
      "arn:aws:iam::${var.aws_account_id}:policy/${var.project_name}-github-terraform-apply-state",
      "arn:aws:iam::${var.aws_account_id}:policy/${var.project_name}-github-terraform-foundation-protection",
    ]
  }
}

resource "aws_iam_policy" "github_apply_foundation_protection" {
  name        = "${var.project_name}-github-terraform-foundation-protection"
  description = "Explicitly deny protected Terraform apply sessions from mutating bootstrap"
  policy      = data.aws_iam_policy_document.github_apply_foundation_protection.json
}

resource "aws_iam_role_policy_attachment" "github_apply_foundation_protection" {
  role       = aws_iam_role.github_apply.name
  policy_arn = aws_iam_policy.github_apply_foundation_protection.arn
}
