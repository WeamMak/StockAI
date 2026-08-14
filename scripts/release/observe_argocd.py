"""Observe one exact Argo CD application revision through its authenticated API."""

from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any

import boto3  # type: ignore[import-untyped]

from .verify_manifest import GIT_OBJECT_PATTERN


class ObservationError(RuntimeError):
    """A bounded Argo observation failure without credential content."""


def _control_plane_id(ec2: Any) -> str:
    response = ec2.describe_instances(
        Filters=[
            {"Name": "tag:Name", "Values": ["weam-stockai-control-plane"]},
            {"Name": "instance-state-name", "Values": ["running"]},
        ]
    )
    instances = [
        instance
        for reservation in response.get("Reservations", [])
        for instance in reservation.get("Instances", [])
    ]
    if len(instances) != 1:
        raise ObservationError("exactly one running control plane is required")
    return str(instances[0]["InstanceId"])


def _commands(
    application: str, expected_revision: str, timeout_seconds: int
) -> list[str]:
    observation_budget = max(1, timeout_seconds - 10)
    expected_state = f"{expected_revision}|Synced|Healthy"
    return [
        "set -eu",
        "export KUBECONFIG=/etc/kubernetes/admin.conf",
        (
            'password="$(kubectl -n argocd get secret '
            "argocd-initial-admin-secret -o jsonpath='{.data.password}' "
            '| base64 --decode)"'
        ),
        (
            'payload="$(ARGO_PASSWORD="$password" python3 -c '
            '\'import json,os; print(json.dumps({"username":"admin",'
            '"password":os.environ["ARGO_PASSWORD"]}))\')"'
        ),
        (
            'token="$(curl --fail --silent --show-error --insecure '
            "--request POST https://argocd-server.argocd.svc/api/v1/session "
            "--header 'Content-Type: application/json' --data \"$payload\" "
            '| python3 -c \'import json,sys; print(json.load(sys.stdin)["token"])\')"'
        ),
        "unset password payload ARGO_PASSWORD",
        f'deadline="$(( $(date +%s) + {observation_budget} ))"',
        'while test "$(date +%s)" -lt "$deadline"; do',
        (
            '  result="$(curl --fail --silent --insecure '
            f"https://argocd-server.argocd.svc/api/v1/applications/{application} "
            '--header "Authorization: Bearer $token" | python3 -c '
            '\'import json,sys; d=json.load(sys.stdin); s=d["status"]; '
            'print("|".join((s["sync"]["revision"], '
            's["sync"]["status"], s["health"]["status"])))\' '
            '2>/dev/null)" || result=""'
        ),
        f'  if test "$result" = "{expected_state}"; then',
        '    printf "%s\\n" "$result"',
        "    unset token result deadline",
        "    exit 0",
        "  fi",
        "  sleep 5",
        "done",
        "unset token result deadline",
        "exit 1",
    ]


def observe_application(
    *,
    ec2: Any,
    ssm: Any,
    application: str,
    expected_revision: str,
    timeout_seconds: int = 600,
) -> dict[str, str]:
    """Return only after Argo reports the exact revision Synced and Healthy."""

    if application != "stockai-prod":
        raise ObservationError("only the stockai-prod application may be observed")
    if GIT_OBJECT_PATTERN.fullmatch(expected_revision) is None:
        raise ObservationError("expected revision is invalid")
    control_plane_id = _control_plane_id(ec2)
    sent = ssm.send_command(
        InstanceIds=[control_plane_id],
        DocumentName="AWS-RunShellScript",
        Parameters={
            "commands": _commands(application, expected_revision, timeout_seconds)
        },
        TimeoutSeconds=min(timeout_seconds, 600),
    )
    command_id = str(sent["Command"]["CommandId"])
    deadline = time.monotonic() + timeout_seconds
    invocation: dict[str, Any] = {}
    while time.monotonic() < deadline:
        try:
            invocation = ssm.get_command_invocation(
                CommandId=command_id,
                InstanceId=control_plane_id,
            )
        except ssm.exceptions.InvocationDoesNotExist:
            time.sleep(1)
            continue
        if invocation.get("Status") in {
            "Success",
            "Cancelled",
            "Failed",
            "TimedOut",
        }:
            break
        time.sleep(2)
    if invocation.get("Status") != "Success":
        raise ObservationError("Argo API observation did not complete successfully")
    lines = str(invocation.get("StandardOutputContent", "")).splitlines()
    if len(lines) != 1 or len(lines[0].split("|")) != 3:
        raise ObservationError("Argo API returned an invalid bounded result")
    revision, sync, health = lines[0].split("|")
    if revision != expected_revision:
        raise ObservationError("Argo revision does not match the promoted revision")
    if sync != "Synced" or health != "Healthy":
        raise ObservationError("Argo application is not Synced and Healthy")
    return {"revision": revision, "sync": sync, "health": health}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--application", choices=("stockai-prod",), required=True)
    parser.add_argument("--expected-revision", required=True)
    parser.add_argument("--timeout-seconds", type=int, default=600)
    arguments = parser.parse_args()
    try:
        observed = observe_application(
            ec2=boto3.client("ec2", region_name="us-east-1"),
            ssm=boto3.client("ssm", region_name="us-east-1"),
            application=arguments.application,
            expected_revision=arguments.expected_revision,
            timeout_seconds=arguments.timeout_seconds,
        )
    except ObservationError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(json.dumps(observed, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
