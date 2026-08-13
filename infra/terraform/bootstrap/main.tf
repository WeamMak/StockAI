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
    sid     = "GitHubPullRequestPlan"
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
      values   = ["repo:${var.github_repository_subject}:pull_request"]
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
  description          = "GitHub pull-request role for Terraform state reads and locks"
  assume_role_policy   = data.aws_iam_policy_document.github_plan_trust.json
  max_session_duration = 3600
}

resource "aws_iam_role" "github_apply" {
  name                 = "${var.project_name}-github-terraform-apply"
  description          = "Protected GitHub environment role for Terraform state writes"
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

data "aws_iam_policy_document" "github_apply_lifecycle" {
  statement {
    sid    = "CreateOnlyTaggedStockAIResources"
    effect = "Allow"
    actions = [
      "acm:RequestCertificate",
      "autoscaling:CreateAutoScalingGroup",
      "cloudwatch:PutMetricAlarm",
      "cognito-idp:CreateUserPool",
      "dlm:CreateLifecyclePolicy",
      "dynamodb:CreateTable",
      "ec2:CreateInternetGateway",
      "ec2:CreateLaunchTemplate",
      "ec2:CreateLaunchTemplateVersion",
      "ec2:CreateRouteTable",
      "ec2:CreateSecurityGroup",
      "ec2:CreateSubnet",
      "ec2:CreateTags",
      "ec2:CreateVolume",
      "ec2:CreateVpc",
      "ec2:RunInstances",
      "elasticloadbalancing:CreateLoadBalancer",
      "elasticloadbalancing:CreateTargetGroup",
      "events:PutRule",
      "iam:CreateInstanceProfile",
      "iam:CreatePolicy",
      "iam:CreateRole",
      "lambda:CreateFunction",
      "logs:CreateLogGroup",
      "secretsmanager:CreateSecret",
      "ssm:PutParameter",
    ]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "aws:RequestTag/Owner"
      values   = [var.owner_name]
    }
  }

  statement {
    sid    = "MutateOnlyOwnedStockAIResources"
    effect = "Allow"
    actions = [
      "acm:AddTagsToCertificate",
      "acm:DeleteCertificate",
      "acm:RemoveTagsFromCertificate",
      "autoscaling:AttachLoadBalancerTargetGroups",
      "autoscaling:CompleteLifecycleAction",
      "autoscaling:DeleteAutoScalingGroup",
      "autoscaling:DeleteLifecycleHook",
      "autoscaling:DetachLoadBalancerTargetGroups",
      "autoscaling:PutLifecycleHook",
      "autoscaling:SetDesiredCapacity",
      "autoscaling:StartInstanceRefresh",
      "autoscaling:UpdateAutoScalingGroup",
      "autoscaling:CreateOrUpdateTags",
      "autoscaling:DeleteTags",
      "cloudwatch:DeleteAlarms",
      "cloudwatch:PutMetricAlarm",
      "dlm:DeleteLifecyclePolicy",
      "dlm:UpdateLifecyclePolicy",
      "ec2:AttachInternetGateway",
      "ec2:AttachVolume",
      "ec2:AssociateRouteTable",
      "ec2:AuthorizeSecurityGroupEgress",
      "ec2:AuthorizeSecurityGroupIngress",
      "ec2:CreateRoute",
      "ec2:DeleteInternetGateway",
      "ec2:DeleteLaunchTemplate",
      "ec2:DeleteLaunchTemplateVersions",
      "ec2:DeleteRoute",
      "ec2:DeleteRouteTable",
      "ec2:DeleteSecurityGroup",
      "ec2:DeleteSubnet",
      "ec2:DeleteTags",
      "ec2:DeleteVolume",
      "ec2:DeleteVpc",
      "ec2:DetachInternetGateway",
      "ec2:DetachVolume",
      "ec2:DisassociateRouteTable",
      "ec2:ModifyLaunchTemplate",
      "ec2:ModifyInstanceAttribute",
      "ec2:ModifySubnetAttribute",
      "ec2:ModifyVolume",
      "ec2:ModifyVpcAttribute",
      "ec2:RevokeSecurityGroupEgress",
      "ec2:RevokeSecurityGroupIngress",
      "ec2:TerminateInstances",
      "events:DeleteRule",
      "events:PutTargets",
      "events:RemoveTargets",
      "lambda:AddPermission",
      "lambda:DeleteFunction",
      "lambda:RemovePermission",
      "lambda:UpdateFunctionCode",
      "lambda:UpdateFunctionConfiguration",
      "logs:DeleteLogGroup",
      "logs:PutRetentionPolicy",
      "secretsmanager:DeleteSecret",
      "secretsmanager:RestoreSecret",
      "secretsmanager:TagResource",
      "secretsmanager:UntagResource",
      "secretsmanager:UpdateSecret",
    ]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "aws:ResourceTag/Owner"
      values   = [var.owner_name]
    }
  }

  statement {
    sid    = "ManageNamedStockAIIam"
    effect = "Allow"
    actions = [
      "iam:AddRoleToInstanceProfile",
      "iam:AttachRolePolicy",
      "iam:CreatePolicyVersion",
      "iam:DeleteInstanceProfile",
      "iam:DeletePolicy",
      "iam:DeletePolicyVersion",
      "iam:DeleteRole",
      "iam:DeleteRolePolicy",
      "iam:DetachRolePolicy",
      "iam:PutRolePolicy",
      "iam:RemoveRoleFromInstanceProfile",
      "iam:TagInstanceProfile",
      "iam:TagPolicy",
      "iam:TagRole",
      "iam:UntagInstanceProfile",
      "iam:UntagPolicy",
      "iam:UntagRole",
      "iam:UpdateAssumeRolePolicy",
    ]
    resources = [
      "arn:aws:iam::${var.aws_account_id}:instance-profile/${var.cluster_name}-*",
      "arn:aws:iam::${var.aws_account_id}:policy/${var.cluster_name}-*",
      "arn:aws:iam::${var.aws_account_id}:role/${var.cluster_name}-*",
    ]
  }

  statement {
    sid     = "PassOnlyApprovedStockAIRoles"
    effect  = "Allow"
    actions = ["iam:PassRole"]
    resources = [
      "arn:aws:iam::${var.aws_account_id}:role/${var.cluster_name}-control-plane",
      "arn:aws:iam::${var.aws_account_id}:role/${var.cluster_name}-dev-worker",
      "arn:aws:iam::${var.aws_account_id}:role/${var.cluster_name}-prod-dlm",
      "arn:aws:iam::${var.aws_account_id}:role/${var.cluster_name}-prod-worker",
      "arn:aws:iam::${var.aws_account_id}:role/${var.cluster_name}-worker-lifecycle",
    ]
  }

  statement {
    sid    = "ManageExactApplicationData"
    effect = "Allow"
    actions = [
      "dynamodb:DeleteTable",
      "dynamodb:TagResource",
      "dynamodb:UntagResource",
      "dynamodb:UpdateContinuousBackups",
      "dynamodb:UpdateTable",
      "dynamodb:UpdateTimeToLive",
      "ssm:AddTagsToResource",
      "ssm:DeleteParameter",
      "ssm:RemoveTagsFromResource",
    ]
    resources = [
      "arn:aws:dynamodb:${var.aws_region}:${var.aws_account_id}:table/${var.cluster_name}-dev-application",
      "arn:aws:dynamodb:${var.aws_region}:${var.aws_account_id}:table/${var.cluster_name}-dev-checkpoints",
      "arn:aws:dynamodb:${var.aws_region}:${var.aws_account_id}:table/${var.cluster_name}-prod-application",
      "arn:aws:dynamodb:${var.aws_region}:${var.aws_account_id}:table/${var.cluster_name}-prod-checkpoints",
      "arn:aws:ssm:${var.aws_region}:${var.aws_account_id}:parameter/stockai/${var.cluster_name}/kubeadm/join-command",
    ]
  }

  statement {
    sid    = "ManageExactLokiBucket"
    effect = "Allow"
    actions = [
      "s3:CreateBucket",
      "s3:DeleteBucket",
      "s3:DeleteBucketPolicy",
      "s3:DeleteBucketWebsite",
      "s3:PutBucketLifecycleConfiguration",
      "s3:PutBucketOwnershipControls",
      "s3:PutBucketPolicy",
      "s3:PutBucketPublicAccessBlock",
      "s3:PutBucketTagging",
      "s3:PutBucketVersioning",
      "s3:PutEncryptionConfiguration",
    ]
    resources = ["arn:aws:s3:::${var.loki_bucket_name}"]
  }

  statement {
    sid    = "ManageStockAINetworkEdgeAndIdentity"
    effect = "Allow"
    actions = [
      "cognito-idp:CreateUserPoolClient",
      "cognito-idp:CreateUserPoolDomain",
      "cognito-idp:CreateGroup",
      "cognito-idp:DeleteGroup",
      "cognito-idp:DeleteUserPool",
      "cognito-idp:DeleteUserPoolClient",
      "cognito-idp:DeleteUserPoolDomain",
      "cognito-idp:SetUserPoolMfaConfig",
      "cognito-idp:UpdateGroup",
      "cognito-idp:UpdateUserPool",
      "cognito-idp:UpdateUserPoolClient",
      "elasticloadbalancing:AddTags",
      "elasticloadbalancing:CreateListener",
      "elasticloadbalancing:CreateRule",
      "elasticloadbalancing:DeleteListener",
      "elasticloadbalancing:DeleteLoadBalancer",
      "elasticloadbalancing:DeleteRule",
      "elasticloadbalancing:DeleteTargetGroup",
      "elasticloadbalancing:ModifyListener",
      "elasticloadbalancing:ModifyLoadBalancerAttributes",
      "elasticloadbalancing:ModifyRule",
      "elasticloadbalancing:ModifyTargetGroup",
      "elasticloadbalancing:ModifyTargetGroupAttributes",
      "elasticloadbalancing:RemoveTags",
    ]
    resources = [
      "arn:aws:cognito-idp:${var.aws_region}:${var.aws_account_id}:userpool/*",
      "arn:aws:elasticloadbalancing:${var.aws_region}:${var.aws_account_id}:listener-rule/app/${var.cluster_name}-*/*/*",
      "arn:aws:elasticloadbalancing:${var.aws_region}:${var.aws_account_id}:listener/app/${var.cluster_name}-*/*/*",
      "arn:aws:elasticloadbalancing:${var.aws_region}:${var.aws_account_id}:loadbalancer/app/${var.cluster_name}-*/*",
      "arn:aws:elasticloadbalancing:${var.aws_region}:${var.aws_account_id}:targetgroup/${var.cluster_name}-*/*",
    ]

    condition {
      test     = "StringEquals"
      variable = "aws:ResourceTag/Owner"
      values   = [var.owner_name]
    }
  }

  statement {
    sid       = "ManageOnlyApprovedHostedZone"
    effect    = "Allow"
    actions   = ["route53:ChangeResourceRecordSets"]
    resources = ["arn:aws:route53:::hostedzone/${var.route53_zone_id}"]
  }

  statement {
    sid       = "RunPlatformOnlyOnTaggedControlPlane"
    effect    = "Allow"
    actions   = ["ssm:SendCommand"]
    resources = ["arn:aws:ec2:${var.aws_region}:${var.aws_account_id}:instance/*"]

    condition {
      test     = "StringEquals"
      variable = "ec2:ResourceTag/Role"
      values   = ["control-plane"]
    }

    condition {
      test     = "StringEquals"
      variable = "ec2:ResourceTag/Owner"
      values   = [var.owner_name]
    }
  }

  statement {
    sid       = "UseOnlyRunShellScript"
    effect    = "Allow"
    actions   = ["ssm:SendCommand"]
    resources = ["arn:aws:ssm:${var.aws_region}::document/AWS-RunShellScript"]
  }

  statement {
    sid    = "ReadOnlyDiscoveryAndCommandStatus"
    effect = "Allow"
    actions = [
      "acm:DescribeCertificate",
      "acm:ListTagsForCertificate",
      "autoscaling:Describe*",
      "cloudwatch:DescribeAlarms",
      "cognito-idp:Describe*",
      "cognito-idp:List*",
      "dlm:GetLifecyclePolicy",
      "dlm:ListTagsForResource",
      "dynamodb:Describe*",
      "dynamodb:ListTagsOfResource",
      "ec2:Describe*",
      "events:DescribeRule",
      "events:ListTagsForResource",
      "iam:Get*",
      "iam:List*",
      "lambda:Get*",
      "lambda:ListTags",
      "logs:DescribeLogGroups",
      "route53:Get*",
      "route53:List*",
      "s3:GetBucket*",
      "s3:GetEncryptionConfiguration",
      "s3:ListAllMyBuckets",
      "secretsmanager:DescribeSecret",
      "secretsmanager:ListSecretVersionIds",
      "ssm:DescribeParameters",
      "ssm:GetCommandInvocation",
      "ssm:GetParameter",
      "ssm:ListTagsForResource",
    ]
    resources = ["*"]
  }

  statement {
    sid    = "DenyBootstrapFoundationMutation"
    effect = "Deny"
    actions = [
      "dynamodb:DeleteTable",
      "dynamodb:UpdateTable",
      "iam:DeleteOpenIDConnectProvider",
      "iam:DeleteRole",
      "iam:DeleteRolePolicy",
      "iam:DetachRolePolicy",
      "iam:UpdateAssumeRolePolicy",
      "s3:DeleteBucket",
      "s3:DeleteBucketPolicy",
      "s3:PutBucketPolicy",
      "s3:PutBucketVersioning",
    ]
    resources = [
      "arn:aws:dynamodb:${var.aws_region}:${var.aws_account_id}:table/${var.state_lock_table_name}",
      "arn:aws:iam::${var.aws_account_id}:oidc-provider/token.actions.githubusercontent.com",
      "arn:aws:iam::${var.aws_account_id}:role/${var.project_name}-github-terraform-apply",
      "arn:aws:iam::${var.aws_account_id}:role/${var.project_name}-github-terraform-plan",
      "arn:aws:s3:::${var.state_bucket_name}",
    ]
  }
}

locals {
  github_apply_lifecycle_document = jsondecode(data.aws_iam_policy_document.github_apply_lifecycle.json)
  github_apply_lifecycle_statement_groups = {
    core = [
      "CreateOnlyTaggedStockAIResources",
      "ManageNamedStockAIIam",
      "MutateOnlyOwnedStockAIResources",
      "PassOnlyApprovedStockAIRoles",
    ]
    services = [
      "ManageExactApplicationData",
      "ManageExactLokiBucket",
      "ManageOnlyApprovedHostedZone",
      "ManageStockAINetworkEdgeAndIdentity",
    ]
    operations = [
      "DenyBootstrapFoundationMutation",
      "ReadOnlyDiscoveryAndCommandStatus",
      "RunPlatformOnlyOnTaggedControlPlane",
      "UseOnlyRunShellScript",
    ]
  }
}

resource "aws_iam_policy" "github_apply_lifecycle" {
  for_each = local.github_apply_lifecycle_statement_groups

  name        = "${var.project_name}-github-terraform-lifecycle-${each.key}"
  description = "Protected ${each.key} lifecycle access for StockAI resources"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      for statement in local.github_apply_lifecycle_document.Statement : statement
      if contains(each.value, statement.Sid)
    ]
  })
}

resource "aws_iam_role_policy_attachment" "github_apply_lifecycle" {
  for_each = aws_iam_policy.github_apply_lifecycle

  role       = aws_iam_role.github_apply.name
  policy_arn = each.value.arn
}

resource "aws_iam_policy" "github_plan_discovery" {
  name        = "${var.project_name}-github-terraform-plan-discovery"
  description = "Read-only AWS discovery required for StockAI Terraform plans"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      for statement in local.github_apply_lifecycle_document.Statement : statement
      if statement.Sid == "ReadOnlyDiscoveryAndCommandStatus"
    ]
  })
}

resource "aws_iam_role_policy_attachment" "github_plan_discovery" {
  role       = aws_iam_role.github_plan.name
  policy_arn = aws_iam_policy.github_plan_discovery.arn
}
