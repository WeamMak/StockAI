"""Contracts for generated Terraform inputs and guided provisioning state."""

from __future__ import annotations

import copy
import json
import subprocess
from pathlib import Path

import pytest
import scripts.infra.provision as provision_module
from scripts.infra.discovery import RepositoryIdentity
from scripts.infra.provision import (
    DEFAULT_DESCRIPTOR,
    ProvisionError,
    atomic_write_json,
    configure_github,
    generated_metadata,
    load_descriptor,
    provision,
    root_inputs,
    validate_descriptor,
)


def test_committed_descriptor_has_only_two_operator_inputs() -> None:
    descriptor = load_descriptor(DEFAULT_DESCRIPTOR)

    assert set(descriptor["inputs"]) == {"domain_name", "route53_zone_id"}
    assert descriptor["generated"]["aws_region"] == "us-east-1"
    assert descriptor["generated"]["administrator_cidr"].endswith("/32")


@pytest.mark.parametrize(
    ("key", "value", "message"),
    (
        ("domain_name", "Example.COM", "lowercase public DNS"),
        ("route53_zone_id", "zone-secret-token", "zone_id is invalid"),
    ),
)
def test_rejects_invalid_or_secret_like_operator_values(
    key: str, value: str, message: str
) -> None:
    descriptor = load_descriptor(DEFAULT_DESCRIPTOR)
    descriptor["inputs"][key] = value

    with pytest.raises(ProvisionError, match=message):
        validate_descriptor(descriptor)


def test_rejects_extra_operator_input_and_non_32_cidr() -> None:
    descriptor = load_descriptor(DEFAULT_DESCRIPTOR)
    descriptor["inputs"]["budget_notification_email"] = "operator@example.com"
    with pytest.raises(ProvisionError, match="exactly"):
        validate_descriptor(descriptor)

    descriptor = load_descriptor(DEFAULT_DESCRIPTOR)
    descriptor["generated"]["administrator_cidr"] = "203.0.113.0/24"
    with pytest.raises(ProvisionError, match="IPv4 /32"):
        validate_descriptor(descriptor)


def test_atomic_write_keeps_complete_valid_json(tmp_path: Path) -> None:
    path = tmp_path / "deployment.json"
    payload = load_descriptor(DEFAULT_DESCRIPTOR)

    atomic_write_json(path, payload)

    assert json.loads(path.read_text(encoding="utf-8")) == payload
    assert not list(tmp_path.glob(".deployment.json.*"))


def test_names_are_stable_and_compatible_with_current_convention() -> None:
    repository = RepositoryIdentity(
        full_name="WeamMak/StockAI",
        owner="WeamMak",
        owner_id=155534656,
        repository="StockAI",
        repository_id=1311929978,
    )

    first = generated_metadata(
        "228281126655",
        repository,
        "147.235.223.133/32",
        "ami-0b6d9d3d33ba97d99",
        ["us-east-1a", "us-east-1b"],
    )
    second = generated_metadata(
        "228281126655",
        repository,
        "147.235.223.133/32",
        "ami-0b6d9d3d33ba97d99",
        ["us-east-1a", "us-east-1b"],
    )

    assert first == second
    assert first["cluster_name"] == "weammak-stockai"
    assert len(first["state_bucket_name"]) <= 63
    assert len(first["loki_bucket_name"]) <= 63


def test_renders_all_root_inputs_without_github_json_blobs() -> None:
    descriptor = load_descriptor(DEFAULT_DESCRIPTOR)

    bootstrap = root_inputs(descriptor, "bootstrap")
    platform = root_inputs(descriptor, "platform")
    edge = root_inputs(descriptor, "edge")
    dev = root_inputs(descriptor, "dev")
    prod = root_inputs(descriptor, "prod")

    assert bootstrap["github_repository_subject"].endswith("StockAI@1311929978")
    assert platform["ami_id"] == descriptor["generated"]["ami_id"]
    assert edge["vpc_id"] == descriptor["outputs"]["platform"]["vpc_id"]
    assert edge["domain_name"] == descriptor["inputs"]["domain_name"]
    assert dev["worker_availability_zone"] == "us-east-1a"
    assert prod["worker_availability_zone"] == "us-east-1b"
    assert "budget_notification_email" not in edge


def test_missing_dependency_output_stops_before_terraform() -> None:
    descriptor = copy.deepcopy(load_descriptor(DEFAULT_DESCRIPTOR))
    del descriptor["outputs"]["platform"]["vpc_id"]

    with pytest.raises(ProvisionError, match="vpc_id is required"):
        root_inputs(descriptor, "edge")


def test_duplicate_json_key_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "deployment.json"
    path.write_text('{"schemaVersion":1,"schemaVersion":1}', encoding="utf-8")

    with pytest.raises(ProvisionError, match="duplicate"):
        load_descriptor(path)


def test_configures_five_variables_and_removes_only_obsolete_json_blobs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    descriptor = load_descriptor(DEFAULT_DESCRIPTOR)
    commands: list[list[str]] = []

    def record(command: list[str], *, cwd: Path | None = None) -> None:
        assert cwd is None
        commands.append(command)

    def list_variables(
        *args: object, **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        assert kwargs["capture_output"] is True
        return subprocess.CompletedProcess(
            args=["gh", "variable", "list"],
            returncode=0,
            stdout=json.dumps(
                [
                    {"name": "DO_NOT_DELETE"},
                    {"name": "TERRAFORM_DEV_TFVARS_JSON"},
                    {"name": "TERRAFORM_PROD_TFVARS_JSON"},
                ]
            ),
        )

    monkeypatch.setattr("scripts.infra.provision._run", record)
    monkeypatch.setattr("scripts.infra.provision.subprocess.run", list_variables)

    configure_github(descriptor)

    assert [command[-1] for command in commands[:2]] == [
        "repos/WeamMak/StockAI/environments/dev",
        "repos/WeamMak/StockAI/environments/prod",
    ]
    sets = [command for command in commands if command[:3] == ["gh", "variable", "set"]]
    assert {command[5] for command in sets} == {
        "AWS_TERRAFORM_APPLY_ROLE_ARN",
        "AWS_TERRAFORM_PLAN_ROLE_ARN",
        "TERRAFORM_LOCK_TABLE",
        "TERRAFORM_STATE_BUCKET",
        "TERRAFORM_STATE_KEY_PREFIX",
    }
    deletes = [
        command for command in commands if command[:3] == ["gh", "variable", "delete"]
    ]
    assert {command[-1] for command in deletes} == {
        "TERRAFORM_DEV_TFVARS_JSON",
        "TERRAFORM_PROD_TFVARS_JSON",
    }


def test_rejected_saved_plan_never_runs_terraform_apply(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    descriptor_path = tmp_path / "deployment.json"
    atomic_write_json(descriptor_path, load_descriptor(DEFAULT_DESCRIPTOR))
    root_paths = {root: tmp_path / root for root in provision_module.ROOTS}
    for path in root_paths.values():
        path.mkdir()
    commands: list[list[str]] = []
    answers = iter(["use 147.235.223.133/32", "reject"])

    monkeypatch.setattr(provision_module, "ROOT_PATHS", root_paths)
    monkeypatch.setattr(provision_module, "CHECKPOINT", tmp_path / "checkpoint.json")
    monkeypatch.setattr(provision_module, "discover_account_id", lambda: "228281126655")
    monkeypatch.setattr(
        provision_module,
        "discover_repository",
        lambda: RepositoryIdentity(
            "WeamMak/StockAI", "WeamMak", 155534656, "StockAI", 1311929978
        ),
    )
    monkeypatch.setattr(
        provision_module, "discover_public_cidr", lambda: "147.235.223.133/32"
    )
    monkeypatch.setattr(provision_module, "verify_route53_zone", lambda *args: None)
    monkeypatch.setattr(provision_module, "verify_bedrock_and_quota", lambda: None)
    monkeypatch.setattr("builtins.input", lambda _: next(answers))
    monkeypatch.setattr(
        provision_module,
        "_run",
        lambda command, cwd=None: commands.append(list(command)),
    )

    with pytest.raises(ProvisionError, match="plan was not approved"):
        provision(descriptor_path)

    assert any(command[:2] == ["terraform", "plan"] for command in commands)
    assert not any(command[:2] == ["terraform", "apply"] for command in commands)
