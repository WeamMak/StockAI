locals {
  join_parameter_arn  = "arn:aws:ssm:${var.aws_region}:${var.aws_account_id}:parameter${local.join_parameter_name}"
  join_parameter_name = "/stockai/${var.cluster_name}/kubeadm/join-command"
}

resource "aws_ssm_parameter" "join" {
  name        = local.join_parameter_name
  description = "Runtime-owned finite kubeadm join command for ${var.cluster_name} workers"
  type        = "SecureString"
  value       = "pending-control-plane-initialization"

  tags = {
    Environment = "shared"
    Purpose     = "kubeadm-worker-join"
  }

  lifecycle {
    ignore_changes = [value]
  }
}

data "aws_iam_policy_document" "control_plane_join" {
  statement {
    sid       = "WriteExactKubeadmJoinParameter"
    actions   = ["ssm:PutParameter"]
    resources = [local.join_parameter_arn]
  }
}

resource "aws_iam_role_policy" "control_plane_join" {
  name   = "${var.cluster_name}-control-plane-kubeadm-join-write"
  policy = data.aws_iam_policy_document.control_plane_join.json
  role   = var.control_plane_role_name
}

data "aws_iam_policy_document" "worker_join" {
  statement {
    sid       = "ReadExactKubeadmJoinParameter"
    actions   = ["ssm:GetParameter"]
    resources = [local.join_parameter_arn]
  }
}

resource "aws_iam_role_policy" "worker_join" {
  for_each = var.worker_role_names

  name   = "${var.cluster_name}-${each.key}-worker-kubeadm-join-read"
  policy = data.aws_iam_policy_document.worker_join.json
  role   = each.value
}

