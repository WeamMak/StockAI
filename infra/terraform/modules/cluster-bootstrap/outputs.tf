output "join_parameter_arn" {
  description = "Exact ARN of the encrypted runtime-owned kubeadm join parameter"
  value       = local.join_parameter_arn

  depends_on = [
    aws_iam_role_policy.control_plane_join,
    aws_iam_role_policy.worker_join,
  ]
}

output "join_parameter_name" {
  description = "Name of the encrypted runtime-owned kubeadm join parameter"
  value       = aws_ssm_parameter.join.name

  depends_on = [
    aws_iam_role_policy.control_plane_join,
    aws_iam_role_policy.worker_join,
  ]
}

