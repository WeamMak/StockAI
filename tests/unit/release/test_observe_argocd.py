"""Contracts for bounded, read-only prod Argo API observation over SSM."""

from __future__ import annotations

from typing import Any

import pytest
from scripts.release.observe_argocd import ObservationError, observe_application


class FakeEC2:
    def describe_instances(self, **_kwargs: object) -> dict[str, object]:
        return {"Reservations": [{"Instances": [{"InstanceId": "i-control-plane"}]}]}


class FakeSSM:
    class exceptions:
        class InvocationDoesNotExist(Exception):
            pass

    def __init__(self, output: str) -> None:
        self.output = output
        self.parameters: dict[str, Any] = {}

    def send_command(self, **kwargs: Any) -> dict[str, object]:
        self.parameters = kwargs
        return {"Command": {"CommandId": "command-1"}}

    def get_command_invocation(self, **_kwargs: object) -> dict[str, object]:
        return {
            "Status": "Success",
            "StandardOutputContent": self.output,
            "StandardErrorContent": "",
        }


def test_observer_uses_argocd_api_and_accepts_exact_healthy_revision() -> None:
    revision = "a" * 40
    ssm = FakeSSM(f"{revision}|Synced|Healthy\n")

    observed = observe_application(
        ec2=FakeEC2(),
        ssm=ssm,
        application="stockai-prod",
        expected_revision=revision,
        timeout_seconds=1,
    )

    assert observed == {
        "revision": revision,
        "sync": "Synced",
        "health": "Healthy",
    }
    commands = ssm.parameters["Parameters"]["commands"]
    assert isinstance(commands, list)
    source = "\n".join(commands)
    assert "jsonpath='{.spec.clusterIP}'" in source
    assert "https://$server_ip/api/v1/session" in source
    assert "argocd-server.argocd.svc" not in source
    assert "/api/v1/applications/stockai-prod" in source
    assert "/api/v1/session" in source
    assert "while test" in source
    assert f"{revision}|Synced|Healthy" in source
    assert "kubectl apply" not in source


def test_observer_rejects_a_different_argo_revision() -> None:
    with pytest.raises(ObservationError, match="revision"):
        observe_application(
            ec2=FakeEC2(),
            ssm=FakeSSM(f"{'b' * 40}|Synced|Healthy\n"),
            application="stockai-prod",
            expected_revision="a" * 40,
            timeout_seconds=1,
        )
