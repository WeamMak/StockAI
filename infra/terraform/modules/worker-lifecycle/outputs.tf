output "cleanup_alarm_names" {
  description = "CloudWatch alarms for Lambda errors and non-clean worker cleanup outcomes"
  value = concat(
    [aws_cloudwatch_metric_alarm.lambda_errors.alarm_name],
    [for alarm in aws_cloudwatch_metric_alarm.non_clean : alarm.alarm_name],
  )
}

output "lambda_function_name" {
  description = "Name of the bounded worker cleanup Lambda function"
  value       = aws_lambda_function.node_cleanup.function_name
}

output "lifecycle_hook_name" {
  description = "Shared termination lifecycle hook name installed on both worker ASGs"
  value       = local.lifecycle_hook_name
}
