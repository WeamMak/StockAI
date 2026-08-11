locals {
  control_plane_arn   = "arn:aws:ec2:${var.aws_region}:${var.aws_account_id}:instance/${var.control_plane_instance_id}"
  lambda_name         = "${var.cluster_name}-worker-lifecycle"
  lifecycle_hook_name = "${var.cluster_name}-worker-terminate"
  log_group_arn       = "arn:aws:logs:${var.aws_region}:${var.aws_account_id}:log-group:/aws/lambda/${local.lambda_name}:*"
  metric_namespace    = "StockAI/WorkerLifecycle"
  worker_asg_arns = {
    for environment, name in var.worker_asg_names : environment =>
    "arn:aws:autoscaling:${var.aws_region}:${var.aws_account_id}:autoScalingGroup:*:autoScalingGroupName/${name}"
  }
}

data "archive_file" "node_cleanup" {
  type        = "zip"
  source_dir  = "${path.module}/lambda"
  output_path = "${path.root}/.terraform/node-cleanup.zip"
  excludes    = ["__pycache__", "__pycache__/*", "*.pyc"]
}

data "aws_iam_policy_document" "lambda_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "node_cleanup" {
  name               = local.lambda_name
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json

  tags = {
    Environment = "shared"
    Owner       = var.owner_name
    Purpose     = "worker-termination-cleanup"
  }
}

data "aws_iam_policy_document" "node_cleanup" {
  statement {
    sid       = "DescribeTerminatingInstance"
    actions   = ["ec2:DescribeInstances"]
    resources = ["*"]
  }

  statement {
    sid     = "RunCleanupOnlyOnControlPlane"
    actions = ["ssm:SendCommand"]
    resources = [
      local.control_plane_arn,
      "arn:aws:ssm:${var.aws_region}::document/AWS-RunShellScript",
    ]
  }

  statement {
    sid       = "ReadCleanupCommandStatus"
    actions   = ["ssm:GetCommandInvocation"]
    resources = ["*"]
  }

  statement {
    sid = "ReleaseOnlyWorkerLifecycleActions"
    actions = [
      "autoscaling:CompleteLifecycleAction",
      "autoscaling:RecordLifecycleActionHeartbeat",
    ]
    resources = values(local.worker_asg_arns)
  }

  statement {
    sid       = "PublishOnlyWorkerLifecycleMetrics"
    actions   = ["cloudwatch:PutMetricData"]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "cloudwatch:namespace"
      values   = [local.metric_namespace]
    }
  }

  statement {
    sid = "WriteOnlyPrecreatedLifecycleLogGroup"
    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = [local.log_group_arn]
  }
}

resource "aws_iam_role_policy" "node_cleanup" {
  name   = local.lambda_name
  policy = data.aws_iam_policy_document.node_cleanup.json
  role   = aws_iam_role.node_cleanup.id
}

resource "aws_cloudwatch_log_group" "node_cleanup" {
  name              = "/aws/lambda/${local.lambda_name}"
  retention_in_days = 14

  tags = {
    Environment = "shared"
    Owner       = var.owner_name
    Purpose     = "worker-termination-cleanup"
  }
}

resource "aws_lambda_function" "node_cleanup" {
  filename         = data.archive_file.node_cleanup.output_path
  function_name    = local.lambda_name
  handler          = "node_cleanup.handler"
  memory_size      = 128
  role             = aws_iam_role.node_cleanup.arn
  runtime          = "python3.12"
  source_code_hash = data.archive_file.node_cleanup.output_base64sha256
  timeout          = 240

  environment {
    variables = {
      CONTROL_PLANE_INSTANCE_ID = var.control_plane_instance_id
      DEV_WORKER_ASG_NAME       = var.worker_asg_names["dev"]
      LIFECYCLE_HOOK_NAME       = local.lifecycle_hook_name
      PROD_WORKER_ASG_NAME      = var.worker_asg_names["prod"]
    }
  }

  tags = {
    Environment = "shared"
    Owner       = var.owner_name
    Purpose     = "worker-termination-cleanup"
  }

  depends_on = [
    aws_cloudwatch_log_group.node_cleanup,
    aws_iam_role_policy.node_cleanup,
  ]
}

resource "aws_autoscaling_lifecycle_hook" "worker_termination" {
  for_each = var.worker_asg_names

  name                   = local.lifecycle_hook_name
  autoscaling_group_name = each.value
  default_result         = "CONTINUE"
  heartbeat_timeout      = 300
  lifecycle_transition   = "autoscaling:EC2_INSTANCE_TERMINATING"
}

resource "aws_cloudwatch_event_rule" "worker_termination" {
  name        = "${var.cluster_name}-worker-termination"
  description = "Routes only StockAI worker ASG termination lifecycle actions"
  event_pattern = jsonencode({
    source      = ["aws.autoscaling"]
    detail-type = ["EC2 Instance-terminate Lifecycle Action"]
    detail = {
      AutoScalingGroupName = values(var.worker_asg_names)
      LifecycleTransition  = ["autoscaling:EC2_INSTANCE_TERMINATING"]
    }
  })

  tags = {
    Environment = "shared"
    Owner       = var.owner_name
    Purpose     = "worker-termination-cleanup"
  }
}

resource "aws_cloudwatch_event_target" "worker_termination" {
  arn  = aws_lambda_function.node_cleanup.arn
  rule = aws_cloudwatch_event_rule.worker_termination.name
}

resource "aws_lambda_permission" "eventbridge" {
  statement_id  = "AllowWorkerTerminationEvents"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.node_cleanup.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.worker_termination.arn
}

resource "aws_cloudwatch_metric_alarm" "lambda_errors" {
  alarm_name          = "${local.lambda_name}-errors"
  alarm_description   = "Worker cleanup Lambda invocation failed; inspect the termination runbook"
  comparison_operator = "GreaterThanOrEqualToThreshold"
  evaluation_periods  = 1
  metric_name         = "Errors"
  namespace           = "AWS/Lambda"
  period              = 300
  statistic           = "Sum"
  threshold           = 1
  treat_missing_data  = "notBreaching"

  dimensions = {
    FunctionName = aws_lambda_function.node_cleanup.function_name
  }

  tags = {
    Environment = "shared"
    Owner       = var.owner_name
    Purpose     = "worker-termination-cleanup"
  }
}

resource "aws_cloudwatch_metric_alarm" "non_clean" {
  for_each = var.worker_asg_names

  alarm_name          = "${local.lambda_name}-${each.key}-non-clean"
  alarm_description   = "${each.key} worker cleanup was forced or failed; follow the termination runbook"
  comparison_operator = "GreaterThanOrEqualToThreshold"
  evaluation_periods  = 1
  metric_name         = "WorkerCleanupOutcome"
  namespace           = local.metric_namespace
  period              = 300
  statistic           = "Maximum"
  threshold           = 1
  treat_missing_data  = "notBreaching"

  dimensions = {
    Environment = each.key
  }

  tags = {
    Environment = each.key
    Owner       = var.owner_name
    Purpose     = "worker-termination-cleanup"
  }
}
