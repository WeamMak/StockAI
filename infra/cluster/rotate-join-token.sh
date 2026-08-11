#!/usr/bin/env bash
set -euo pipefail

: "${AWS_REGION:?AWS_REGION is required}"
: "${JOIN_PARAMETER_NAME:?JOIN_PARAMETER_NAME is required}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "rotate-join-token.sh must run as root" >&2
  exit 1
fi

if [[ ! -s /etc/kubernetes/admin.conf ]]; then
  echo "control plane is not initialized" >&2
  exit 1
fi

join_command="$(kubeadm token create --ttl 24h --print-join-command)"
aws ssm put-parameter \
  --cli-connect-timeout 5 \
  --cli-read-timeout 10 \
  --region "$AWS_REGION" \
  --name "$JOIN_PARAMETER_NAME" \
  --type SecureString \
  --overwrite \
  --value "$join_command" \
  >/dev/null
unset join_command
