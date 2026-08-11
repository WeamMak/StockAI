#!/usr/bin/env bash
set -euo pipefail

KUBERNETES_MINOR="v1.35"
KUBERNETES_VERSION="1.35.5-1.1"
CONTAINERD_VERSION="2.3.1"
CONTAINERD_SHA256="628448bd973610c656c1cbea8e88b32fafd85b23cc1aa4a3372eb7198478c054"
RUNC_VERSION="1.5.1"
RUNC_SHA256="177df879d50c913eb205e898d5c1c05a18f574053c0ce5524c471208eaf06f6f"
CNI_PLUGINS_VERSION="v1.9.1"
CNI_PLUGINS_SHA256="b98f74a0f8522f0a83867178729c1aa70f2158f90c45a2ca8fa791db1c76b303"
AWS_CLI_VERSION="2.35.23"
SSM_AGENT_DEB_SERVICE="amazon-ssm-agent.service"
SSM_AGENT_SNAP_SERVICE="snap.amazon-ssm-agent.amazon-ssm-agent.service"

if [[ "${EUID}" -ne 0 ]]; then
  echo "install-node.sh must run as root" >&2
  exit 1
fi

if [[ "$(uname -m)" != "x86_64" ]]; then
  echo "the approved StockAI node image must use x86_64" >&2
  exit 1
fi

download_dir="$(mktemp -d /tmp/stockai-node-install.XXXXXX)"
cleanup() {
  rm -rf -- "$download_dir"
}
trap cleanup EXIT

download_and_verify() {
  local url="$1"
  local destination="$2"
  local expected_sha256="$3"

  curl \
    --connect-timeout 10 \
    --fail \
    --location \
    --max-time 300 \
    --retry 3 \
    --retry-all-errors \
    --silent \
    --show-error \
    "$url" \
    --output "$destination"
  printf '%s  %s\n' "$expected_sha256" "$destination" | sha256sum --check --status
}

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install --yes \
  apt-transport-https \
  ca-certificates \
  conntrack \
  curl \
  ebtables \
  ethtool \
  gnupg \
  socat \
  unzip

install -d -m 0755 /etc/modules-load.d /etc/sysctl.d
cat >/etc/modules-load.d/stockai-kubernetes.conf <<'EOF'
overlay
br_netfilter
EOF
modprobe overlay
modprobe br_netfilter

cat >/etc/sysctl.d/99-stockai-kubernetes.conf <<'EOF'
net.bridge.bridge-nf-call-iptables = 1
net.bridge.bridge-nf-call-ip6tables = 1
net.ipv4.ip_forward = 1
EOF
sysctl --system >/dev/null

swapoff --all
sed -ri '/[[:space:]]swap[[:space:]]/s/^#?/#/' /etc/fstab

containerd_archive="$download_dir/containerd.tar.gz"
download_and_verify \
  "https://github.com/containerd/containerd/releases/download/v${CONTAINERD_VERSION}/containerd-${CONTAINERD_VERSION}-linux-amd64.tar.gz" \
  "$containerd_archive" \
  "$CONTAINERD_SHA256"
tar --extract --gzip --file "$containerd_archive" --directory /usr/local

runc_binary="$download_dir/runc.amd64"
download_and_verify \
  "https://github.com/opencontainers/runc/releases/download/v${RUNC_VERSION}/runc.amd64" \
  "$runc_binary" \
  "$RUNC_SHA256"
install -m 0755 "$runc_binary" /usr/local/sbin/runc

cni_archive="$download_dir/cni-plugins.tgz"
download_and_verify \
  "https://github.com/containernetworking/plugins/releases/download/${CNI_PLUGINS_VERSION}/cni-plugins-linux-amd64-${CNI_PLUGINS_VERSION}.tgz" \
  "$cni_archive" \
  "$CNI_PLUGINS_SHA256"
install -d -m 0755 /opt/cni/bin
tar --extract --gzip --file "$cni_archive" --directory /opt/cni/bin

install -d -m 0755 /etc/containerd /usr/local/lib/systemd/system
cat >/etc/containerd/config.toml <<'EOF'
version = 3

[plugins.'io.containerd.cri.v1.runtime'.containerd]
  default_runtime_name = 'runc'

  [plugins.'io.containerd.cri.v1.runtime'.containerd.runtimes.runc]
    runtime_type = 'io.containerd.runc.v2'

    [plugins.'io.containerd.cri.v1.runtime'.containerd.runtimes.runc.options]
      SystemdCgroup = true
EOF

cat >/usr/local/lib/systemd/system/containerd.service <<'EOF'
[Unit]
Description=containerd container runtime
Documentation=https://containerd.io
After=network.target local-fs.target

[Service]
ExecStartPre=-/sbin/modprobe overlay
ExecStart=/usr/local/bin/containerd
Type=notify
Delegate=yes
KillMode=process
Restart=always
RestartSec=5
LimitNPROC=infinity
LimitCORE=infinity
LimitNOFILE=infinity
TasksMax=infinity
OOMScoreAdjust=-999

[Install]
WantedBy=multi-user.target
EOF

install -d -m 0755 /etc/apt/keyrings
curl --connect-timeout 10 --fail --location --max-time 60 --retry 3 \
  --retry-all-errors --silent --show-error \
  "https://pkgs.k8s.io/core:/stable:/${KUBERNETES_MINOR}/deb/Release.key" \
  | gpg --dearmor --yes --output /etc/apt/keyrings/kubernetes-apt-keyring.gpg
chmod 0644 /etc/apt/keyrings/kubernetes-apt-keyring.gpg
printf '%s\n' \
  "deb [signed-by=/etc/apt/keyrings/kubernetes-apt-keyring.gpg] https://pkgs.k8s.io/core:/stable:/${KUBERNETES_MINOR}/deb/ /" \
  >/etc/apt/sources.list.d/kubernetes.list

apt-get update
apt-get install --yes \
  "kubeadm=${KUBERNETES_VERSION}" \
  "kubectl=${KUBERNETES_VERSION}" \
  "kubelet=${KUBERNETES_VERSION}"
apt-mark hold kubelet kubeadm kubectl

if ! command -v aws >/dev/null 2>&1 || \
  [[ "$(aws --version 2>&1)" != aws-cli/${AWS_CLI_VERSION}* ]]; then
  aws_archive="$download_dir/awscliv2.zip"
  curl --connect-timeout 10 --fail --location --max-time 300 --retry 3 \
    --retry-all-errors --silent --show-error \
    "https://awscli.amazonaws.com/awscli-exe-linux-x86_64-${AWS_CLI_VERSION}.zip" \
    --output "$aws_archive"
  unzip -q "$aws_archive" -d "$download_dir"
  aws_install_args=(
    --bin-dir /usr/local/bin
    --install-dir /usr/local/aws-cli
  )
  if [[ -d /usr/local/aws-cli ]]; then
    aws_install_args+=(--update)
  fi
  "$download_dir/aws/install" "${aws_install_args[@]}"
fi

systemctl daemon-reload
systemctl enable --now containerd
systemctl enable kubelet

ssm_agent_service=""
if systemctl cat "$SSM_AGENT_DEB_SERVICE" >/dev/null 2>&1; then
  ssm_agent_service="$SSM_AGENT_DEB_SERVICE"
  if ! systemctl enable --now "$SSM_AGENT_DEB_SERVICE" >/dev/null; then
    echo "the approved Ubuntu AMI must include an active amazon-ssm-agent" >&2
    exit 1
  fi
elif command -v snap >/dev/null 2>&1 && \
  snap list amazon-ssm-agent >/dev/null 2>&1 && \
  systemctl cat "$SSM_AGENT_SNAP_SERVICE" >/dev/null 2>&1; then
  ssm_agent_service="$SSM_AGENT_SNAP_SERVICE"
  if ! snap start --enable amazon-ssm-agent >/dev/null; then
    echo "the approved Ubuntu AMI must include an active amazon-ssm-agent" >&2
    exit 1
  fi
else
  echo "the approved Ubuntu AMI must include an active amazon-ssm-agent" >&2
  exit 1
fi

if ! systemctl is-active --quiet "$ssm_agent_service"; then
  echo "the approved Ubuntu AMI must include an active amazon-ssm-agent" >&2
  exit 1
fi

install -d -m 0755 /var/lib/stockai
printf '%s\n' "${KUBERNETES_VERSION}" >/var/lib/stockai/node-install-complete
