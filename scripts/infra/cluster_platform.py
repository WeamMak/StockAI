"""Install, verify, or quiesce the shared Kubernetes platform through SSM."""

# The embedded shell commands are kept as readable, auditable single lines.
# ruff: noqa: E501

from __future__ import annotations

import argparse
import re
import shlex
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

import boto3  # type: ignore[import-untyped]
from botocore.exceptions import ClientError  # type: ignore[import-untyped]

INSTANCE_PATTERN = re.compile(r"^i-[0-9a-f]{17}$")
REPOSITORY_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
REVISION_PATTERN = re.compile(r"^[0-9a-f]{40}$")
CLUSTER_PATTERN = re.compile(r"^[a-z][a-z0-9-]{2,31}$")
TERMINAL_STATUSES = {"Success", "Cancelled", "Failed", "TimedOut", "Cancelling"}
SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(token|secret|password|authorization)\s*[=:]\s*[^\s]+"
)
KUBEADM_TOKEN = re.compile(r"\b[a-z0-9]{6}\.[a-z0-9]{16}\b")
MAX_EVIDENCE_LENGTH = 4096


class ClusterPlatformError(RuntimeError):
    """A bounded platform operation failure safe to show to an operator."""


class SsmClient(Protocol):
    def get_command_invocation(self, **kwargs: str) -> dict[str, Any]: ...


@dataclass(frozen=True)
class CommandEvidence:
    status: str
    output: str


def _validate_cluster(cluster_name: str) -> None:
    if CLUSTER_PATTERN.fullmatch(cluster_name) is None:
        raise ClusterPlatformError("cluster name is invalid")


def build_install_script(*, repository: str, revision: str, cluster_name: str) -> str:
    """Return the pinned, idempotent control-plane installation script."""
    _validate_cluster(cluster_name)
    if REPOSITORY_PATTERN.fullmatch(repository) is None:
        raise ClusterPlatformError("repository is invalid")
    if REVISION_PATTERN.fullmatch(revision) is None:
        raise ClusterPlatformError("repository revision must be a full commit SHA")

    repository_url = shlex.quote(f"https://github.com/{repository}.git")
    revision_value = shlex.quote(revision)
    return f"""#!/usr/bin/env bash
set -euo pipefail
export KUBECONFIG=/etc/kubernetes/admin.conf
workdir="$(mktemp -d /tmp/stockai-platform.XXXXXX)"
cleanup() {{ rm -rf "$workdir"; }}
trap cleanup EXIT

wait_for_file() {{
  local path="$1"
  for _ in $(seq 1 120); do
    test -s "$path" && return 0
    sleep 5
  done
  echo "required control-plane marker is unavailable" >&2
  return 1
}}

wait_for_file /var/lib/cloud/instance/boot-finished
wait_for_file /etc/kubernetes/admin.conf
wait_for_file /var/lib/stockai/control-plane-init-complete

kubectl wait --for=condition=Ready node --all --timeout=10m >/dev/null
test "$(kubectl get nodes -l node-role.kubernetes.io/control-plane -o name | wc -l)" -eq 1
test "$(kubectl get nodes -l stockai.io/environment=dev -o name | wc -l)" -ge 1
test "$(kubectl get nodes -l stockai.io/environment=prod -o name | wc -l)" -ge 1

git clone --quiet --filter=blob:none --no-checkout {repository_url} "$workdir/repository"
cd "$workdir/repository"
git checkout --detach {revision_value} >/dev/null

kubectl apply --server-side --field-manager=stockai-platform -k deploy/kubernetes/cluster/ingress >/dev/null
kubectl apply --server-side --field-manager=stockai-platform -k deploy/kubernetes/cluster/ebs-csi >/dev/null
kubectl apply --server-side --field-manager=stockai-platform -k deploy/kubernetes/cluster/metrics >/dev/null
kubectl apply --server-side --field-manager=stockai-platform -k deploy/kubernetes/cluster/external-secrets >/dev/null
kubectl wait --for=condition=Established crd/externalsecrets.external-secrets.io crd/secretstores.external-secrets.io --timeout=5m >/dev/null
kubectl apply --server-side --field-manager=stockai-platform -k deploy/kubernetes/cluster/argocd >/dev/null

kubectl rollout status daemonset/ingress-nginx-controller -n ingress-nginx --timeout=10m >/dev/null
kubectl rollout status deployment/ebs-csi-controller -n kube-system --timeout=10m >/dev/null
kubectl rollout status daemonset/ebs-csi-node -n kube-system --timeout=10m >/dev/null
kubectl rollout status deployment/metrics-server -n kube-system --timeout=10m >/dev/null
kubectl rollout status deployment/kube-state-metrics -n kube-system --timeout=10m >/dev/null
kubectl wait --for=condition=Available deployment --all -n argocd --timeout=10m >/dev/null
kubectl rollout status statefulset/argocd-application-controller -n argocd --timeout=10m >/dev/null

ready_nodes="$(kubectl get nodes --no-headers | awk '$2 == "Ready" {{ count++ }} END {{ print count + 0 }}')"
echo "stockai-platform-ready nodes=$ready_nodes controllers=healthy"
"""


def build_quiesce_script(*, cluster_name: str) -> str:
    """Return a bounded script that removes only environment desired state."""
    _validate_cluster(cluster_name)
    return """#!/usr/bin/env bash
set -euo pipefail
export KUBECONFIG=/etc/kubernetes/admin.conf

kubectl delete application.argoproj.io/stockai-prod application.argoproj.io/stockai-dev \
  --namespace argocd --ignore-not-found --wait=true --timeout=5m >/dev/null
kubectl delete namespace/prod namespace/dev \
  --ignore-not-found --wait=true --timeout=10m >/dev/null

for _ in $(seq 1 120); do
  attachments="$(kubectl get volumeattachments.storage.k8s.io --no-headers 2>/dev/null | wc -l)"
  test "$attachments" -eq 0 && break
  sleep 5
done
test "$(kubectl get volumeattachments.storage.k8s.io --no-headers 2>/dev/null | wc -l)" -eq 0
echo "stockai-environments-quiesced volumeAttachments=0"
"""


def redact_output(output: str) -> str:
    """Remove credential-like values and bound operator-visible evidence."""
    redacted = SECRET_ASSIGNMENT.sub(
        lambda match: f"{match.group(1)}=[REDACTED]", output
    )
    redacted = KUBEADM_TOKEN.sub("[REDACTED]", redacted)
    return redacted[:MAX_EVIDENCE_LENGTH].strip()


def wait_for_command(
    ssm: SsmClient,
    *,
    command_id: str,
    instance_id: str,
    timeout_seconds: int = 1200,
    poll_seconds: float = 5,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> CommandEvidence:
    """Poll one SSM invocation until a bounded terminal state."""
    deadline = monotonic() + timeout_seconds
    while True:
        try:
            response = ssm.get_command_invocation(
                CommandId=command_id,
                InstanceId=instance_id,
            )
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") != "InvocationDoesNotExist":
                raise ClusterPlatformError(
                    "SSM command status could not be read"
                ) from error
            response = {"Status": "Pending"}

        status = str(response.get("Status", "Unknown"))
        if status in TERMINAL_STATUSES:
            stdout = redact_output(str(response.get("StandardOutputContent", "")))
            stderr = redact_output(str(response.get("StandardErrorContent", "")))
            evidence = stdout or stderr or f"SSM command completed with status {status}"
            if status != "Success":
                raise ClusterPlatformError(
                    f"SSM command failed with status {status}: {evidence}"
                )
            return CommandEvidence(status=status, output=evidence)
        if monotonic() >= deadline:
            raise ClusterPlatformError("SSM command timed out")
        sleep(poll_seconds)


def _verify_control_plane(ec2: Any, instance_id: str, cluster_name: str) -> None:
    response = ec2.describe_instances(InstanceIds=[instance_id])
    instances = [
        instance
        for reservation in response.get("Reservations", [])
        for instance in reservation.get("Instances", [])
    ]
    if len(instances) != 1:
        raise ClusterPlatformError("control-plane instance was not found")
    tags = {tag["Key"]: tag["Value"] for tag in instances[0].get("Tags", [])}
    if (
        tags.get("Role") != "control-plane"
        or tags.get("Name") != f"{cluster_name}-control-plane"
    ):
        raise ClusterPlatformError("SSM target is not the tagged StockAI control plane")


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instance-id", required=True)
    parser.add_argument("--cluster-name", required=True)
    parser.add_argument("--region", default="us-east-1")
    subparsers = parser.add_subparsers(dest="command", required=True)
    install = subparsers.add_parser("install")
    install.add_argument("--repository", required=True)
    install.add_argument("--revision", required=True)
    subparsers.add_parser("quiesce")
    return parser.parse_args()


def main() -> int:
    arguments = _parse_arguments()
    try:
        _validate_cluster(arguments.cluster_name)
        if INSTANCE_PATTERN.fullmatch(arguments.instance_id) is None:
            raise ClusterPlatformError("control-plane instance ID is invalid")
        session = boto3.session.Session(region_name=arguments.region)
        _verify_control_plane(
            session.client("ec2"), arguments.instance_id, arguments.cluster_name
        )
        if arguments.command == "install":
            script = build_install_script(
                repository=arguments.repository,
                revision=arguments.revision,
                cluster_name=arguments.cluster_name,
            )
        else:
            script = build_quiesce_script(cluster_name=arguments.cluster_name)
        ssm = session.client("ssm")
        response = ssm.send_command(
            InstanceIds=[arguments.instance_id],
            DocumentName="AWS-RunShellScript",
            Parameters={"commands": [script]},
            TimeoutSeconds=1200,
            Comment="StockAI protected shared-platform lifecycle",
        )
        evidence = wait_for_command(
            ssm,
            command_id=str(response["Command"]["CommandId"]),
            instance_id=arguments.instance_id,
        )
        print(evidence.output)
    except (ClientError, ClusterPlatformError, KeyError) as error:
        print(f"error: {redact_output(str(error))}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
