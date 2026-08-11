"""Unit contracts for bounded worker-node termination cleanup."""

from __future__ import annotations

import importlib.util
import itertools
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest.mock import MagicMock

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LAMBDA_PATH = (
    PROJECT_ROOT
    / "infra"
    / "terraform"
    / "modules"
    / "worker-lifecycle"
    / "lambda"
    / "node_cleanup.py"
)


class _AwsError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


@pytest.fixture(scope="module")
def cleanup_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("node_cleanup", LAMBDA_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError("could not load the worker cleanup Lambda")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def lifecycle_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEV_WORKER_ASG_NAME", "stockai-dev-workers")
    monkeypatch.setenv("PROD_WORKER_ASG_NAME", "stockai-prod-workers")
    monkeypatch.setenv("CONTROL_PLANE_INSTANCE_ID", "i-0controlplane12345")
    monkeypatch.setenv("LIFECYCLE_HOOK_NAME", "stockai-worker-terminate")
    monkeypatch.setenv("AWS_REGION", "us-east-1")


def _event(*, environment: str = "dev") -> dict[str, Any]:
    return {
        "id": "7b5d4c52-fc64-4fd9-a1c7-55d4fef0ab12",
        "source": "aws.autoscaling",
        "detail-type": "EC2 Instance-terminate Lifecycle Action",
        "detail": {
            "LifecycleActionToken": "opaque-lifecycle-token",
            "LifecycleHookName": "stockai-worker-terminate",
            "AutoScalingGroupName": f"stockai-{environment}-workers",
            "LifecycleTransition": "autoscaling:EC2_INSTANCE_TERMINATING",
            "EC2InstanceId": "i-0123456789abcdef0",
        },
    }


def _clients(
    *,
    statuses: Iterator[dict[str, str]] | None = None,
    instance_present: bool = True,
) -> MagicMock:
    clients = MagicMock()
    clients.monotonic.side_effect = [10.0, 12.5]
    clients.sleep.return_value = None
    if instance_present:
        clients.ec2.describe_instances.return_value = {
            "Reservations": [
                {
                    "Instances": [
                        {
                            "InstanceId": "i-0123456789abcdef0",
                            "PrivateDnsName": "ip-10-0-1-59.ec2.internal",
                            "Tags": [
                                {
                                    "Key": "aws:autoscaling:groupName",
                                    "Value": "stockai-dev-workers",
                                },
                                {"Key": "Environment", "Value": "dev"},
                            ],
                        }
                    ]
                }
            ]
        }
    else:
        clients.ec2.describe_instances.return_value = {"Reservations": []}
    clients.ssm.send_command.return_value = {"Command": {"CommandId": "cmd-1"}}
    clients.ssm.get_command_invocation.side_effect = statuses or iter(
        [
            {
                "Status": "Success",
                "StandardOutputContent": "CLEANUP_OUTCOME=clean\n",
                "StandardErrorContent": "",
            }
        ]
    )
    return clients


def test_parse_event_accepts_exact_dev_and_prod_contract(
    cleanup_module: ModuleType,
) -> None:
    dev = cleanup_module.parse_event(_event())
    prod = cleanup_module.parse_event(_event(environment="prod"))

    assert (dev.environment, dev.asg_name) == ("dev", "stockai-dev-workers")
    assert (prod.environment, prod.asg_name) == ("prod", "stockai-prod-workers")
    assert dev.instance_id == "i-0123456789abcdef0"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source", "aws.ec2"),
        ("detail-type", "EC2 Instance Launch Successful"),
    ],
)
def test_parse_event_rejects_wrong_envelope(
    cleanup_module: ModuleType, field: str, value: str
) -> None:
    event = _event()
    event[field] = value

    with pytest.raises(ValueError, match="invalid lifecycle event"):
        cleanup_module.parse_event(event)


def test_parse_event_rejects_unknown_asg_and_malformed_detail(
    cleanup_module: ModuleType,
) -> None:
    unknown = _event()
    unknown["detail"]["AutoScalingGroupName"] = "untrusted-workers"
    malformed = _event()
    del malformed["detail"]["EC2InstanceId"]

    with pytest.raises(ValueError, match="unknown worker ASG"):
        cleanup_module.parse_event(unknown)
    with pytest.raises(ValueError, match="invalid lifecycle detail"):
        cleanup_module.parse_event(malformed)


def test_clean_cleanup_maps_private_dns_heartbeats_and_completes(
    cleanup_module: ModuleType,
) -> None:
    event = cleanup_module.parse_event(_event())
    clients = _clients(
        statuses=iter(
            [
                {"Status": "InProgress"},
                {
                    "Status": "Success",
                    "StandardOutputContent": "CLEANUP_OUTCOME=clean\n",
                    "StandardErrorContent": "",
                },
            ]
        )
    )

    result = cleanup_module.cleanup_node(event, clients)

    assert result.outcome is cleanup_module.CleanupOutcome.CLEAN
    assert result.node_name == "ip-10-0-1-59.ec2.internal"
    assert result.heartbeat_count == 1
    send = clients.ssm.send_command.call_args.kwargs
    assert send["InstanceIds"] == ["i-0controlplane12345"]
    assert send["DocumentName"] == "AWS-RunShellScript"
    script = send["Parameters"]["commands"][0]
    assert "kubectl drain" in script
    assert "--timeout=120s" in script
    assert "kubectl delete node" in script
    assert "|| exit 44" in script
    assert "ip-10-0-1-59.ec2.internal" in script
    clients.autoscaling.record_lifecycle_action_heartbeat.assert_called_once()
    clients.autoscaling.complete_lifecycle_action.assert_called_once_with(
        AutoScalingGroupName="stockai-dev-workers",
        LifecycleHookName="stockai-worker-terminate",
        LifecycleActionResult="CONTINUE",
        InstanceId="i-0123456789abcdef0",
    )


def test_forced_and_failed_outcomes_still_continue_lifecycle(
    cleanup_module: ModuleType,
) -> None:
    event = cleanup_module.parse_event(_event())
    forced_clients = _clients(
        statuses=iter(
            [
                {
                    "Status": "Success",
                    "StandardOutputContent": "CLEANUP_OUTCOME=forced\n",
                    "StandardErrorContent": "drain detail must not be returned",
                }
            ]
        )
    )
    failed_clients = _clients()
    failed_clients.ssm.send_command.side_effect = RuntimeError("secret raw failure")

    forced = cleanup_module.cleanup_node(event, forced_clients)
    failed = cleanup_module.cleanup_node(event, failed_clients)

    assert forced.outcome is cleanup_module.CleanupOutcome.FORCED
    assert forced.error_code == "drain_failed"
    assert failed.outcome is cleanup_module.CleanupOutcome.FAILED
    assert failed.error_code == "ssm_unavailable"
    forced_clients.autoscaling.complete_lifecycle_action.assert_called_once()
    failed_clients.autoscaling.complete_lifecycle_action.assert_called_once()


def test_missing_instance_is_idempotently_clean_without_ssm(
    cleanup_module: ModuleType,
) -> None:
    event = cleanup_module.parse_event(_event())
    clients = _clients(instance_present=False)

    result = cleanup_module.cleanup_node(event, clients)

    assert result.outcome is cleanup_module.CleanupOutcome.CLEAN
    assert result.error_code == "instance_already_absent"
    clients.ssm.send_command.assert_not_called()
    clients.autoscaling.complete_lifecycle_action.assert_called_once()


def test_already_terminated_instance_error_is_idempotently_clean(
    cleanup_module: ModuleType,
) -> None:
    event = cleanup_module.parse_event(_event())
    clients = _clients()
    clients.ec2.describe_instances.side_effect = _AwsError("InvalidInstanceID.NotFound")

    result = cleanup_module.cleanup_node(event, clients)

    assert result.outcome is cleanup_module.CleanupOutcome.CLEAN
    assert result.error_code == "instance_already_absent"
    clients.ssm.send_command.assert_not_called()
    clients.autoscaling.complete_lifecycle_action.assert_called_once()


def test_identity_mismatch_fails_without_sending_ssm(
    cleanup_module: ModuleType,
) -> None:
    event = cleanup_module.parse_event(_event())
    clients = _clients()
    clients.ec2.describe_instances.return_value["Reservations"][0]["Instances"][0][
        "Tags"
    ][1]["Value"] = "prod"

    result = cleanup_module.cleanup_node(event, clients)

    assert result.outcome is cleanup_module.CleanupOutcome.FAILED
    assert result.error_code == "instance_identity_mismatch"
    clients.ssm.send_command.assert_not_called()
    clients.autoscaling.complete_lifecycle_action.assert_called_once()


def test_invalid_private_dns_name_fails_closed(cleanup_module: ModuleType) -> None:
    event = cleanup_module.parse_event(_event())
    clients = _clients()
    clients.ec2.describe_instances.return_value["Reservations"][0]["Instances"][0][
        "PrivateDnsName"
    ] = "node;echo unsafe"

    result = cleanup_module.cleanup_node(event, clients)

    assert result.outcome is cleanup_module.CleanupOutcome.FAILED
    assert result.error_code == "invalid_node_name"
    clients.ssm.send_command.assert_not_called()


def test_ssm_poll_timeout_is_bounded_and_continues(cleanup_module: ModuleType) -> None:
    event = cleanup_module.parse_event(_event())
    clients = _clients(statuses=itertools.repeat({"Status": "InProgress"}))

    result = cleanup_module.cleanup_node(event, clients)

    assert result.outcome is cleanup_module.CleanupOutcome.FAILED
    assert result.error_code == "ssm_poll_timeout"
    assert result.heartbeat_count == 42
    assert clients.sleep.call_count == 42
    assert clients.autoscaling.record_lifecycle_action_heartbeat.call_count == 42
    clients.autoscaling.complete_lifecycle_action.assert_called_once()


def test_handler_emits_sanitized_metrics(cleanup_module: ModuleType) -> None:
    clients = _clients()
    cleanup_module.__dict__["_aws_clients"] = MagicMock(return_value=clients)

    response = cleanup_module.handler(_event(), MagicMock())

    assert response == {
        "outcome": "clean",
        "environment": "dev",
        "instance_id": "i-0123456789abcdef0",
    }
    metrics = clients.cloudwatch.put_metric_data.call_args.kwargs
    assert metrics["Namespace"] == "StockAI/WorkerLifecycle"
    assert {item["MetricName"] for item in metrics["MetricData"]} == {
        "WorkerCleanupDuration",
        "WorkerCleanupOutcome",
    }
    serialized = repr(metrics)
    assert "secret raw failure" not in serialized
