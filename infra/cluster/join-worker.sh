#!/usr/bin/env bash
set -euo pipefail

MAX_ATTEMPTS=40
MAX_BACKOFF_SECONDS=30
IMDS_ENDPOINT="http://169.254.169.254/latest"
CRI_SOCKET="unix:///run/containerd/containerd.sock"

if [[ "${EUID}" -ne 0 ]]; then
  echo "join-worker.sh must run as root" >&2
  exit 1
fi

if [[ "$#" -ne 4 ]]; then
  echo "usage: join-worker.sh <dev|prod> <aws-region> <parameter-name> <api-endpoint>" >&2
  exit 2
fi

environment="$1"
aws_region="$2"
parameter_name="$3"
expected_api_endpoint="$4"

if ! [[ "$environment" =~ ^(dev|prod)$ ]]; then
  echo "worker environment must be dev or prod" >&2
  exit 2
fi

if ! [[ "$aws_region" =~ ^[a-z]{2}-[a-z]+-[0-9]+$ ]]; then
  echo "AWS region is invalid" >&2
  exit 2
fi

if ! [[ "$parameter_name" =~ ^/stockai/[a-z0-9-]+/kubeadm/join-command$ ]]; then
  echo "join parameter name is invalid" >&2
  exit 2
fi

if ! [[ "$expected_api_endpoint" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}:6443$ ]]; then
  echo "expected Kubernetes API endpoint is invalid" >&2
  exit 2
fi

metadata_token="$(curl --fail --silent --show-error \
  --connect-timeout 2 \
  --max-time 5 \
  --request PUT \
  --header 'X-aws-ec2-metadata-token-ttl-seconds: 60' \
  "${IMDS_ENDPOINT}/api/token")"
private_dns="$(curl --fail --silent --show-error \
  --connect-timeout 2 \
  --max-time 5 \
  --header "X-aws-ec2-metadata-token: ${metadata_token}" \
  "${IMDS_ENDPOINT}/meta-data/local-hostname")"
unset metadata_token

if ! [[ "$private_dns" =~ ^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$ ]] || \
  [[ "$private_dns" != *.* ]]; then
  echo "EC2 private DNS name is invalid" >&2
  exit 1
fi

install -d -m 0755 /etc/default
printf '%s\n' \
  "KUBELET_EXTRA_ARGS=--node-labels=stockai.io/environment=${environment} --register-with-taints=stockai.io/environment=${environment}:NoSchedule" \
  >/etc/default/kubelet

if [[ -s /etc/kubernetes/kubelet.conf ]]; then
  exit 0
fi

escaped_endpoint="${expected_api_endpoint//./\\.}"
join_pattern="^kubeadm join ${escaped_endpoint} --token [a-z0-9]{6}\\.[a-z0-9]{16} --discovery-token-ca-cert-hash sha256:[[:xdigit:]]{64}$"
backoff_seconds=2

for ((attempt = 1; attempt <= MAX_ATTEMPTS; attempt++)); do
  join_command="$(aws ssm get-parameter \
    --cli-connect-timeout 5 \
    --cli-read-timeout 10 \
    --region "$aws_region" \
    --name "$parameter_name" \
    --with-decryption \
    --query Parameter.Value \
    --output text 2>/dev/null || true)"

  if [[ "$join_command" =~ $join_pattern ]]; then
    read -r -a join_args <<<"$join_command"
    if timeout 5m "${join_args[@]}" \
      --node-name "$private_dns" \
      --cri-socket "$CRI_SOCKET" \
      >/dev/null 2>&1; then
      unset join_command join_args
      exit 0
    fi

    timeout 2m kubeadm reset --force --cri-socket "$CRI_SOCKET" \
      >/dev/null 2>&1 || true
  fi

  unset join_command
  if ((attempt < MAX_ATTEMPTS)); then
    sleep "$backoff_seconds"
    if ((backoff_seconds < MAX_BACKOFF_SECONDS)); then
      backoff_seconds=$((backoff_seconds * 2))
      if ((backoff_seconds > MAX_BACKOFF_SECONDS)); then
        backoff_seconds=$MAX_BACKOFF_SECONDS
      fi
    fi
  fi
done

echo "worker could not obtain and use a valid kubeadm join command" >&2
exit 1
