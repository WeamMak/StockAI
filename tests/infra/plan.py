"""Helpers for asserting Terraform plans through their public JSON format."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any, cast

TerraformPlan = dict[str, Any]


def _modules(module: Mapping[str, Any]) -> Iterator[Mapping[str, Any]]:
    """Yield a planned module and every nested child module."""

    yield module
    for child in module.get("child_modules", []):
        yield from _modules(child)


def resources(plan: TerraformPlan, resource_type: str) -> list[dict[str, Any]]:
    """Return all managed resources of ``resource_type`` from a plan."""

    root_module = plan.get("planned_values", {}).get("root_module", {})
    return [
        resource
        for module in _modules(root_module)
        for resource in module.get("resources", [])
        if resource.get("mode", "managed") == "managed"
        and resource.get("type") == resource_type
    ]


def resource_configurations(
    plan: TerraformPlan, resource_type: str
) -> list[dict[str, Any]]:
    """Return resource configurations of ``resource_type`` from every module."""

    root_module = plan.get("configuration", {}).get("root_module", {})
    return [
        resource
        for module in _configuration_modules(root_module)
        for resource in module.get("resources", [])
        if resource.get("mode", "managed") == "managed"
        and resource.get("type") == resource_type
    ]


def _configuration_modules(module: Mapping[str, Any]) -> Iterator[Mapping[str, Any]]:
    yield module
    for module_call in module.get("module_calls", {}).values():
        child = module_call.get("module")
        if child is not None:
            yield from _configuration_modules(child)


def create_plan(
    root: Path,
    plan_path: Path,
    variables: Mapping[str, str],
    *,
    init_args: Sequence[str] = (),
) -> TerraformPlan:
    """Create and decode an offline Terraform plan for deterministic tests."""

    working_root = _local_backend_copy(root, plan_path.parent)
    environment = os.environ.copy()
    environment.update(
        {
            "AWS_ACCESS_KEY_ID": "test-access-key",
            "AWS_EC2_METADATA_DISABLED": "true",
            "AWS_SECRET_ACCESS_KEY": "test-secret-key",
            "AWS_SKIP_CREDENTIALS_VALIDATION": "true",
            "AWS_SKIP_REQUESTING_ACCOUNT_ID": "true",
        }
    )

    provider_directory = root / ".terraform" / "providers"
    if not provider_directory.is_dir():
        provider_directory = next(
            (
                ancestor / "platform" / ".terraform" / "providers"
                for ancestor in root.parents
                if (ancestor / "platform" / ".terraform" / "providers").is_dir()
            ),
            provider_directory,
        )
    local_provider_args = (
        (f"-plugin-dir={provider_directory}",)
        if provider_directory.is_dir()
        and any(provider_directory.rglob("terraform-provider-*"))
        else ()
    )
    _run(
        [
            "terraform",
            "init",
            "-backend=false",
            "-input=false",
            "-no-color",
            *local_provider_args,
            *init_args,
        ],
        working_root,
        environment,
    )
    _run(
        [
            "terraform",
            "plan",
            "-input=false",
            "-lock=false",
            "-refresh=false",
            "-no-color",
            f"-out={plan_path}",
            *(f"-var={name}={value}" for name, value in variables.items()),
        ],
        working_root,
        environment,
    )
    result = _run(
        ["terraform", "show", "-json", str(plan_path)],
        working_root,
        environment,
    )
    return cast(TerraformPlan, json.loads(result.stdout))


def _local_backend_copy(root: Path, destination: Path) -> Path:
    """Copy a root and its local modules without its production S3 backend."""

    source_root = root.parents[1] if root.parent.name == "environments" else root.parent
    terraform_root = destination / "terraform"
    working_root = terraform_root / root.relative_to(source_root)
    working_root.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        root,
        working_root,
        ignore=shutil.ignore_patterns(
            ".terraform",
            "*.tfplan",
            "*.tfstate",
            "*.tfstate.*",
            "terraform.tfvars",
        ),
    )
    shutil.copytree(source_root / "modules", terraform_root / "modules")
    cluster_assets = source_root.parent / "cluster"
    if cluster_assets.is_dir():
        shutil.copytree(cluster_assets, destination / "cluster")

    versions_path = working_root / "versions.tf"
    versions = versions_path.read_text(encoding="utf-8")
    offline_versions = versions.replace('  backend "s3" {}\n\n', "").replace(
        'provider "aws" {\n  region = var.aws_region\n',
        'provider "aws" {\n'
        "  region                      = var.aws_region\n"
        "  skip_credentials_validation = true\n"
        "  skip_metadata_api_check     = true\n"
        "  skip_requesting_account_id  = true\n",
    )
    versions_path.write_text(
        offline_versions,
        encoding="utf-8",
    )
    return working_root


def _run(
    command: Sequence[str], cwd: Path, environment: Mapping[str, str]
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            cwd=cwd,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as error:
        message = error.stderr.strip() or error.stdout.strip()
        raise RuntimeError(
            f"Terraform command failed: {' '.join(command)}\n{message}"
        ) from error
