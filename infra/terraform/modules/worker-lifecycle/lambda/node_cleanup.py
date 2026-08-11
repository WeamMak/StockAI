"""Bounded, fail-open cleanup for terminating self-managed Kubernetes workers."""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Callable, Mapping
from enum import StrEnum
from ipaddress import IPv4Address
from typing import Any, NamedTuple, Protocol

import boto3  # type: ignore[import-untyped]

EVENT_TYPE = "EC2 Instance-terminate Lifecycle Action"
LIFECYCLE_TRANSITION = "autoscaling:EC2_INSTANCE_TERMINATING"
METRIC_NAMESPACE = "StockAI/WorkerLifecycle"
POLL_SECONDS = 5
MAX_POLLS = 42
NODE_NAME_PATTERN = re.compile(r"^[a-z0-9.-]+$")
INSTANCE_ID_PATTERN = re.compile(r"^i-[0-9a-f]{8,17}$")


class CleanupOutcome(StrEnum):
    """Stable worker cleanup outcomes used by metrics and runbooks."""

    CLEAN = "clean"
    FORCED = "forced"
    FAILED = "failed"


class TerminationEvent(NamedTuple):
    """Validated subset of an ASG termination lifecycle event."""

    event_id: str
    environment: str
    asg_name: str
    hook_name: str
    instance_id: str


class CleanupResult(NamedTuple):
    """Sanitized result returned by the lifecycle cleanup boundary."""

    outcome: CleanupOutcome
    environment: str
    instance_id: str
    node_name: str | None
    duration_seconds: float
    heartbeat_count: int
    ssm_status: str | None
    error_code: str | None


class LambdaContext(Protocol):
    """The Lambda context surface retained for the public handler signature."""

    def get_remaining_time_in_millis(self) -> int: ...


class AwsClients(NamedTuple):
    """Small injectable boundary around the AWS clients and clock."""

    autoscaling: Any
    cloudwatch: Any
    ec2: Any
    ssm: Any
    monotonic: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep


def parse_event(event: Mapping[str, Any]) -> TerminationEvent:
    """Validate and reduce an EventBridge ASG termination event."""

    if (
        event.get("source") != "aws.autoscaling"
        or event.get("detail-type") != EVENT_TYPE
    ):
        raise ValueError("invalid lifecycle event")

    detail = event.get("detail")
    if not isinstance(detail, Mapping):
        raise ValueError("invalid lifecycle detail")

    event_id = event.get("id")
    hook_name = detail.get("LifecycleHookName")
    asg_name = detail.get("AutoScalingGroupName")
    transition = detail.get("LifecycleTransition")
    instance_id = detail.get("EC2InstanceId")
    required_values = (event_id, hook_name, asg_name, transition, instance_id)
    if not all(isinstance(value, str) and value for value in required_values):
        raise ValueError("invalid lifecycle detail")
    if transition != LIFECYCLE_TRANSITION:
        raise ValueError("invalid lifecycle detail")
    expected_hook = os.environ["LIFECYCLE_HOOK_NAME"]
    if hook_name != expected_hook:
        raise ValueError("invalid lifecycle detail")
    if not INSTANCE_ID_PATTERN.fullmatch(instance_id):
        raise ValueError("invalid lifecycle detail")

    allowed_asgs = {
        os.environ["DEV_WORKER_ASG_NAME"]: "dev",
        os.environ["PROD_WORKER_ASG_NAME"]: "prod",
    }
    environment = allowed_asgs.get(asg_name)
    if environment is None:
        raise ValueError("unknown worker ASG")

    return TerminationEvent(
        event_id=event_id,
        environment=environment,
        asg_name=asg_name,
        hook_name=hook_name,
        instance_id=instance_id,
    )


def cleanup_node(event: TerminationEvent, clients: AwsClients) -> CleanupResult:
    """Run bounded node cleanup and always attempt fail-open lifecycle completion."""

    started = clients.monotonic()
    node_name: str | None = None
    private_ip: str | None = None
    heartbeat_count = 0
    ssm_status: str | None = None
    outcome = CleanupOutcome.FAILED
    error_code: str | None = "cleanup_failed"

    try:
        instance = _describe_instance(event, clients)
        if instance is None:
            outcome = CleanupOutcome.CLEAN
            error_code = "instance_already_absent"
        else:
            node_name, private_ip, identity_error = _validate_instance_identity(
                event, instance
            )
            if identity_error is not None:
                error_code = identity_error
            else:
                command_id = _send_cleanup(event, node_name, private_ip, clients)
                outcome, ssm_status, heartbeat_count, error_code = _poll_cleanup(
                    event, command_id, clients
                )
    except Exception:
        error_code = (
            "ssm_unavailable" if node_name is not None else "instance_lookup_failed"
        )
    finally:
        clients.autoscaling.complete_lifecycle_action(
            AutoScalingGroupName=event.asg_name,
            LifecycleHookName=event.hook_name,
            LifecycleActionResult="CONTINUE",
            InstanceId=event.instance_id,
        )

    return CleanupResult(
        outcome=outcome,
        environment=event.environment,
        instance_id=event.instance_id,
        node_name=node_name,
        duration_seconds=max(0.0, clients.monotonic() - started),
        heartbeat_count=heartbeat_count,
        ssm_status=ssm_status,
        error_code=error_code,
    )


def _describe_instance(
    event: TerminationEvent, clients: AwsClients
) -> Mapping[str, Any] | None:
    try:
        response = clients.ec2.describe_instances(InstanceIds=[event.instance_id])
    except Exception as error:
        error_response = getattr(error, "response", {})
        if error_response.get("Error", {}).get("Code") == "InvalidInstanceID.NotFound":
            return None
        raise
    instances = [
        instance
        for reservation in response.get("Reservations", [])
        for instance in reservation.get("Instances", [])
    ]
    if not instances:
        return None
    if len(instances) != 1 or instances[0].get("InstanceId") != event.instance_id:
        raise ValueError("instance identity mismatch")
    return instances[0]


def _validate_instance_identity(
    event: TerminationEvent, instance: Mapping[str, Any]
) -> tuple[str | None, str | None, str | None]:
    tags = {
        tag.get("Key"): tag.get("Value")
        for tag in instance.get("Tags", [])
        if isinstance(tag, Mapping)
    }
    if (
        tags.get("aws:autoscaling:groupName") != event.asg_name
        or tags.get("Environment") != event.environment
    ):
        return None, None, "instance_identity_mismatch"

    node_name = instance.get("PrivateDnsName")
    if not isinstance(node_name, str) or not NODE_NAME_PATTERN.fullmatch(node_name):
        return None, None, "invalid_node_name"

    private_ip = instance.get("PrivateIpAddress")
    if not isinstance(private_ip, str):
        return None, None, "invalid_private_ip"
    try:
        private_ip = str(IPv4Address(private_ip))
    except ValueError:
        return None, None, "invalid_private_ip"
    return node_name, private_ip, None


def _send_cleanup(
    event: TerminationEvent,
    node_name: str,
    private_ip: str,
    clients: AwsClients,
) -> str:
    response = clients.ssm.send_command(
        InstanceIds=[os.environ["CONTROL_PLANE_INSTANCE_ID"]],
        DocumentName="AWS-RunShellScript",
        TimeoutSeconds=180,
        Parameters={"commands": [_cleanup_script(event, node_name, private_ip)]},
    )
    command_id = response.get("Command", {}).get("CommandId")
    if not isinstance(command_id, str) or not command_id:
        raise ValueError("missing SSM command ID")
    return command_id


def _cleanup_script(event: TerminationEvent, node_name: str, private_ip: str) -> str:
    return f"""set -u
export KUBECONFIG=/etc/kubernetes/admin.conf
node_name='{node_name}'
expected_private_ip='{private_ip}'
expected_environment='{event.environment}'
if ! kubectl get node "$node_name" >/dev/null 2>&1; then
  printf 'CLEANUP_OUTCOME=clean\\n'
  exit 0
fi
internal_ip="$(kubectl get node "$node_name" \
  -o jsonpath='{{.status.addresses[?(@.type=="InternalIP")].address}}')"
environment="$(kubectl get node "$node_name" \
  -o jsonpath='{{.metadata.labels.stockai\\.io/environment}}')"
[ "$internal_ip" = "$expected_private_ip" ] || exit 42
[ "$environment" = "$expected_environment" ] || exit 43
drain_rc=0
kubectl cordon "$node_name" >/dev/null 2>&1 || true
kubectl drain "$node_name" --ignore-daemonsets --delete-emptydir-data \
  --force --timeout=120s >/dev/null 2>&1 || drain_rc=$?
kubectl delete node "$node_name" --ignore-not-found=true >/dev/null 2>&1 || exit 44
if [ "$drain_rc" -eq 0 ]; then
  printf 'CLEANUP_OUTCOME=clean\\n'
else
  printf 'CLEANUP_OUTCOME=forced\\n'
fi
"""


def _poll_cleanup(
    event: TerminationEvent, command_id: str, clients: AwsClients
) -> tuple[CleanupOutcome, str, int, str | None]:
    heartbeat_count = 0
    terminal_failure_statuses = {
        "Cancelled",
        "Cancelling",
        "Failed",
        "TimedOut",
        "Undeliverable",
        "Terminated",
    }
    for _ in range(MAX_POLLS):
        try:
            response = clients.ssm.get_command_invocation(
                CommandId=command_id,
                InstanceId=os.environ["CONTROL_PLANE_INSTANCE_ID"],
            )
            status = str(response.get("Status", "Unknown"))
        except Exception as error:
            error_response = getattr(error, "response", {})
            if error_response.get("Error", {}).get("Code") != "InvocationDoesNotExist":
                raise
            status = "Pending"
        if status == "Success":
            marker = str(response.get("StandardOutputContent", ""))
            if "CLEANUP_OUTCOME=clean" in marker:
                return CleanupOutcome.CLEAN, status, heartbeat_count, None
            if "CLEANUP_OUTCOME=forced" in marker:
                return CleanupOutcome.FORCED, status, heartbeat_count, "drain_failed"
            return (
                CleanupOutcome.FAILED,
                status,
                heartbeat_count,
                "malformed_ssm_output",
            )
        if status in terminal_failure_statuses:
            return CleanupOutcome.FAILED, status, heartbeat_count, "ssm_command_failed"

        clients.autoscaling.record_lifecycle_action_heartbeat(
            AutoScalingGroupName=event.asg_name,
            LifecycleHookName=event.hook_name,
            InstanceId=event.instance_id,
        )
        heartbeat_count += 1
        clients.sleep(POLL_SECONDS)

    return CleanupOutcome.FAILED, "TimedOut", heartbeat_count, "ssm_poll_timeout"


def _aws_clients() -> AwsClients:
    region = os.environ["AWS_REGION"]
    return AwsClients(
        autoscaling=boto3.client("autoscaling", region_name=region),
        cloudwatch=boto3.client("cloudwatch", region_name=region),
        ec2=boto3.client("ec2", region_name=region),
        ssm=boto3.client("ssm", region_name=region),
    )


def _emit_metrics(result: CleanupResult, clients: AwsClients) -> None:
    outcome_value = {
        CleanupOutcome.CLEAN: 0.0,
        CleanupOutcome.FORCED: 1.0,
        CleanupOutcome.FAILED: 2.0,
    }[result.outcome]
    dimensions = [{"Name": "Environment", "Value": result.environment}]
    clients.cloudwatch.put_metric_data(
        Namespace=METRIC_NAMESPACE,
        MetricData=[
            {
                "MetricName": "WorkerCleanupOutcome",
                "Dimensions": dimensions,
                "Unit": "Count",
                "Value": outcome_value,
            },
            {
                "MetricName": "WorkerCleanupDuration",
                "Dimensions": dimensions,
                "Unit": "Seconds",
                "Value": result.duration_seconds,
            },
        ],
    )


def handler(event: Mapping[str, Any], context: LambdaContext) -> dict[str, str]:  # noqa: ARG001
    """Handle one validated worker-termination lifecycle event."""

    termination = parse_event(event)
    clients = _aws_clients()
    result = cleanup_node(termination, clients)
    _emit_metrics(result, clients)
    print(
        json.dumps(
            {
                "event": "worker_cleanup_completed",
                "event_id": termination.event_id,
                "environment": result.environment,
                "asg_name": termination.asg_name,
                "instance_id": result.instance_id,
                "node_name": result.node_name,
                "duration_seconds": round(result.duration_seconds, 3),
                "heartbeat_count": result.heartbeat_count,
                "ssm_status": result.ssm_status,
                "outcome": result.outcome.value,
                "error_code": result.error_code,
            },
            sort_keys=True,
        )
    )
    return {
        "outcome": result.outcome.value,
        "environment": result.environment,
        "instance_id": result.instance_id,
    }
