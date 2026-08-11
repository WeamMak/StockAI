#!/usr/bin/env bash
set -euo pipefail

CALICO_VERSION="v3.30.2"
CALICO_MANIFEST_URL="https://raw.githubusercontent.com/projectcalico/calico/${CALICO_VERSION}/manifests/calico.yaml"
IMDS_ENDPOINT="http://169.254.169.254/latest"
CRI_SOCKET="unix:///run/containerd/containerd.sock"

if [[ "${EUID}" -ne 0 ]]; then
  echo "init-control-plane.sh must run as root" >&2
  exit 1
fi

if [[ "$#" -ne 3 ]]; then
  echo "usage: init-control-plane.sh <cluster-name> <aws-region> <parameter-name>" >&2
  exit 2
fi

cluster_name="$1"
aws_region="$2"
join_parameter_name="$3"

if ! [[ "$cluster_name" =~ ^[a-z][a-z0-9-]{2,31}$ ]]; then
  echo "cluster name is invalid" >&2
  exit 2
fi

if ! [[ "$aws_region" =~ ^[a-z]{2}-[a-z]+-[0-9]+$ ]]; then
  echo "AWS region is invalid" >&2
  exit 2
fi

if [[ "$join_parameter_name" != "/stockai/${cluster_name}/kubeadm/join-command" ]]; then
  echo "join parameter does not match the cluster" >&2
  exit 2
fi

metadata_token="$(curl --fail --silent --show-error \
  --connect-timeout 2 \
  --max-time 5 \
  --request PUT \
  --header 'X-aws-ec2-metadata-token-ttl-seconds: 60' \
  "${IMDS_ENDPOINT}/api/token")"
private_ip="$(curl --fail --silent --show-error \
  --connect-timeout 2 \
  --max-time 5 \
  --header "X-aws-ec2-metadata-token: ${metadata_token}" \
  "${IMDS_ENDPOINT}/meta-data/local-ipv4")"
private_dns="$(curl --fail --silent --show-error \
  --connect-timeout 2 \
  --max-time 5 \
  --header "X-aws-ec2-metadata-token: ${metadata_token}" \
  "${IMDS_ENDPOINT}/meta-data/local-hostname")"
unset metadata_token

if ! [[ "$private_ip" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]]; then
  echo "EC2 private IP is invalid" >&2
  exit 1
fi

if ! [[ "$private_dns" =~ ^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$ ]] || \
  [[ "$private_dns" != *.* ]]; then
  echo "EC2 private DNS name is invalid" >&2
  exit 1
fi

if [[ ! -s /etc/kubernetes/admin.conf ]]; then
  if ! timeout 10m kubeadm init \
    --apiserver-advertise-address "$private_ip" \
    --control-plane-endpoint "${private_ip}:6443" \
    --cri-socket "$CRI_SOCKET" \
    --node-name "$private_dns" \
    --pod-network-cidr=192.168.0.0/16 \
    --skip-token-print \
    >/dev/null 2>&1; then
    echo "kubeadm control-plane initialization failed" >&2
    exit 1
  fi
fi

chmod 0600 /etc/kubernetes/admin.conf
install -d -m 0755 /etc/stockai
cat >/etc/stockai/cluster-bootstrap <<EOF
AWS_REGION=${aws_region}
JOIN_PARAMETER_NAME=${join_parameter_name}
EOF
chmod 0600 /etc/stockai/cluster-bootstrap

systemctl daemon-reload
systemctl enable kubeadm-token-rotation.timer
systemctl start kubeadm-token-rotation.service
systemctl start kubeadm-token-rotation.timer

KUBECONFIG=/etc/kubernetes/admin.conf kubectl apply --server-side \
  --field-manager=stockai-bootstrap \
  --filename "$CALICO_MANIFEST_URL" \
  --request-timeout=2m \
  >/dev/null

install -d -m 0755 /var/lib/stockai
printf '%s\n' "$CALICO_VERSION" >/var/lib/stockai/control-plane-init-complete
