"""Behavior contracts for protected SSM Kubernetes platform operations."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from scripts.infra.cluster_platform import (
    ClusterPlatformError,
    build_install_script,
    build_quiesce_script,
    redact_output,
    wait_for_command,
)


def test_install_script_is_pinned_idempotent_and_excludes_application_state() -> None:
    script = build_install_script(
        repository="WeamMak/StockAI",
        revision="a" * 40,
        cluster_name="weammak-stockai",
    )

    assert "/var/lib/cloud/instance/boot-finished" in script
    assert "/etc/kubernetes/admin.conf" in script
    assert "/var/lib/stockai/control-plane-init-complete" in script
    assert "stockai.io/environment=dev" in script
    assert "stockai.io/environment=prod" in script
    assert "git checkout --detach" in script
    assert "deploy/kubernetes/cluster/ingress" in script
    assert "deploy/kubernetes/cluster/ebs-csi" in script
    assert "deploy/kubernetes/cluster/metrics" in script
    assert "deploy/kubernetes/cluster/argocd" in script
    assert "kubectl rollout status" in script
    assert "deploy/kubernetes/cluster/network" not in script
    assert "deploy/kubernetes/argocd/dev-application" not in script
    assert "deploy/kubernetes/overlays/dev" not in script
    assert "deploy/kubernetes/overlays/prod" not in script
    assert "trap cleanup EXIT" in script


def test_quiesce_removes_only_environment_state_and_waits_for_detach() -> None:
    script = build_quiesce_script(cluster_name="weammak-stockai")

    assert "application.argoproj.io/stockai-dev" in script
    assert "application.argoproj.io/stockai-prod" in script
    assert "namespace/dev" in script
    assert "namespace/prod" in script
    assert "volumeattachments.storage.k8s.io" in script
    assert "kube-system" not in script
    assert "argocd namespace" not in script.lower()


def test_redaction_removes_tokens_credentials_and_unbounded_output() -> None:
    raw = "token=abcdef.0123456789abcdef secret=top-secret password=hunter2\n" + (
        "x" * 20_000
    )

    safe = redact_output(raw)

    assert "abcdef" not in safe
    assert "top-secret" not in safe
    assert "hunter2" not in safe
    assert len(safe) <= 4096


class FakeSsm:
    def __init__(self, responses: Iterator[dict[str, Any]]) -> None:
        self.responses = responses

    def get_command_invocation(self, **_: str) -> dict[str, Any]:
        return next(self.responses)


def test_wait_for_command_returns_only_sanitized_health_evidence() -> None:
    ssm = FakeSsm(
        iter(
            [
                {"Status": "InProgress"},
                {
                    "Status": "Success",
                    "StandardOutputContent": (
                        "stockai-platform-ready nodes=3 token=hidden"
                    ),
                    "StandardErrorContent": "",
                },
            ]
        )
    )

    evidence = wait_for_command(
        ssm,
        command_id="command-1",
        instance_id="i-0123456789abcdef0",
        timeout_seconds=5,
        poll_seconds=0,
    )

    assert evidence.status == "Success"
    assert evidence.output == "stockai-platform-ready nodes=3 token=[REDACTED]"


def test_wait_for_command_times_out_without_leaking_remote_output() -> None:
    ssm = FakeSsm(iter([{"Status": "InProgress"}] * 10))
    ticks = iter([0.0, 0.0, 2.0])

    with pytest.raises(ClusterPlatformError, match="timed out"):
        wait_for_command(
            ssm,
            command_id="command-1",
            instance_id="i-0123456789abcdef0",
            timeout_seconds=1,
            poll_seconds=0,
            monotonic=lambda: next(ticks),
        )
