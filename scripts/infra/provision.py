"""Guided, resumable provisioning for the fixed StockAI AWS architecture."""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from scripts.infra.discovery import (
    AWS_REGION,
    DiscoveryError,
    RepositoryIdentity,
    discover_account_id,
    discover_ami,
    discover_availability_zones,
    discover_public_cidr,
    discover_repository,
    verify_bedrock_and_quota,
    verify_route53_zone,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DESCRIPTOR = PROJECT_ROOT / "deploy" / "config" / "deployment.json"
CHECKPOINT = PROJECT_ROOT / ".stockai-provision-checkpoint.json"
MAX_DESCRIPTOR_BYTES = 256 * 1024
ROOTS = ("bootstrap", "platform", "edge", "dev", "prod")
LIFECYCLE_ROOTS = ("platform", "edge", "dev", "prod")
ROOT_PATHS = {
    "bootstrap": PROJECT_ROOT / "infra" / "terraform" / "bootstrap",
    "platform": PROJECT_ROOT / "infra" / "terraform" / "platform",
    "edge": PROJECT_ROOT / "infra" / "terraform" / "edge",
    "dev": PROJECT_ROOT / "infra" / "terraform" / "environments" / "dev",
    "prod": PROJECT_ROOT / "infra" / "terraform" / "environments" / "prod",
}
STATE_KEYS = {
    "platform": "platform/terraform.tfstate",
    "edge": "edge/terraform.tfstate",
    "dev": "environments/dev/terraform.tfstate",
    "prod": "environments/prod/terraform.tfstate",
}
TFVARS_FILE = ".stockai.auto.tfvars.json"
REMOVED_GITHUB_VARIABLES = (
    "TERRAFORM_PLATFORM_TFVARS_JSON",
    "TERRAFORM_EDGE_TFVARS_JSON",
    "TERRAFORM_DEV_TFVARS_JSON",
    "TERRAFORM_PROD_TFVARS_JSON",
)
DOMAIN_PATTERN = re.compile(
    r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$"
)
ZONE_PATTERN = re.compile(r"^Z[A-Z0-9]{8,31}$")


class ProvisionError(ValueError):
    """A bounded provisioning failure without sensitive process output."""


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ProvisionError("deployment descriptor contains duplicate keys")
        result[key] = value
    return result


def load_descriptor(path: Path = DEFAULT_DESCRIPTOR) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as error:
        raise ProvisionError("deployment descriptor is unavailable") from error
    if len(raw.encode("utf-8")) > MAX_DESCRIPTOR_BYTES:
        raise ProvisionError("deployment descriptor exceeds 256 KiB")
    try:
        payload = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
    except json.JSONDecodeError as error:
        raise ProvisionError("deployment descriptor is malformed") from error
    validate_descriptor(payload)
    return dict(payload)


def validate_descriptor(payload: Any) -> None:
    if not isinstance(payload, dict) or set(payload) != {
        "schemaVersion",
        "inputs",
        "generated",
        "outputs",
    }:
        raise ProvisionError("deployment descriptor has unexpected top-level keys")
    if payload["schemaVersion"] != 1:
        raise ProvisionError("deployment descriptor schema version is unsupported")
    inputs = payload["inputs"]
    if not isinstance(inputs, dict) or set(inputs) != {
        "domain_name",
        "route53_zone_id",
    }:
        raise ProvisionError(
            "operator inputs must be exactly domain_name and route53_zone_id"
        )
    domain = inputs["domain_name"]
    zone = inputs["route53_zone_id"]
    if not isinstance(domain, str) or DOMAIN_PATTERN.fullmatch(domain) is None:
        raise ProvisionError("domain_name must be a lowercase public DNS name")
    if not isinstance(zone, str) or ZONE_PATTERN.fullmatch(zone) is None:
        raise ProvisionError("route53_zone_id is invalid")
    generated = payload["generated"]
    required = {
        "administrator_cidr",
        "ami_id",
        "availability_zones",
        "aws_account_id",
        "aws_region",
        "cluster_name",
        "github_repository",
        "github_repository_subject",
        "loki_bucket_name",
        "owner_name",
        "project_name",
        "state_bucket_name",
        "state_key_prefix",
        "state_lock_table_name",
    }
    if not isinstance(generated, dict) or set(generated) != required:
        raise ProvisionError("generated deployment metadata has unexpected keys")
    try:
        cidr = ipaddress.ip_network(generated["administrator_cidr"], strict=True)
    except (TypeError, ValueError) as error:
        raise ProvisionError("administrator_cidr is invalid") from error
    if cidr.version != 4 or cidr.prefixlen != 32:
        raise ProvisionError("administrator_cidr must be an IPv4 /32")
    if generated["aws_region"] != AWS_REGION:
        raise ProvisionError("deployment region must be us-east-1")
    if not re.fullmatch(r"[0-9]{12}", generated["aws_account_id"]):
        raise ProvisionError("generated AWS account ID is invalid")
    zones = generated["availability_zones"]
    if (
        not isinstance(zones, list)
        or len(zones) != 2
        or len(set(zones)) != 2
        or not all(
            isinstance(zone_name, str) and zone_name.startswith(AWS_REGION)
            for zone_name in zones
        )
    ):
        raise ProvisionError("generated Availability Zones are invalid")
    if not isinstance(payload["outputs"], dict) or not set(payload["outputs"]).issubset(
        ROOTS
    ):
        raise ProvisionError("Terraform outputs have unexpected roots")


def atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(payload, indent=2, sort_keys=False) + "\n"
    descriptor = None
    try:
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{path.name}.", dir=path.parent
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            descriptor = None
            stream.write(rendered)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except OSError as error:
        raise ProvisionError(
            "could not atomically write generated configuration"
        ) from error
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _slug(value: str, maximum: int) -> str:
    slug = re.sub(r"[^a-z0-9-]+", "-", value.lower()).strip("-")
    slug = re.sub(r"-+", "-", slug)
    return slug[:maximum].rstrip("-")


def generated_metadata(
    account_id: str,
    repository: RepositoryIdentity,
    administrator_cidr: str,
    ami_id: str,
    zones: list[str],
) -> dict[str, Any]:
    owner = _slug(repository.owner, 16)
    cluster = _slug(f"{owner}-stockai", 32)
    return {
        "administrator_cidr": administrator_cidr,
        "ami_id": ami_id,
        "availability_zones": zones,
        "aws_account_id": account_id,
        "aws_region": AWS_REGION,
        "cluster_name": cluster,
        "github_repository": repository.full_name,
        "github_repository_subject": repository.subject,
        "loki_bucket_name": _slug(f"{cluster}-loki-{account_id}-{AWS_REGION}", 63),
        "owner_name": owner,
        "project_name": cluster,
        "state_bucket_name": _slug(
            f"{cluster}-terraform-state-{account_id}-{AWS_REGION}", 63
        ),
        "state_key_prefix": "stockai",
        "state_lock_table_name": _slug(f"{cluster}-terraform-locks", 255),
    }


def create_descriptor(
    domain_name: str,
    route53_zone_id: str,
    *,
    administrator_cidr: str | None = None,
    confirm: Callable[[str], str] = input,
) -> dict[str, Any]:
    if (
        DOMAIN_PATTERN.fullmatch(domain_name) is None
        or ZONE_PATTERN.fullmatch(route53_zone_id) is None
    ):
        raise ProvisionError("domain or Route 53 zone ID is invalid")
    account_id = discover_account_id()
    repository = discover_repository()
    cidr = administrator_cidr or discover_public_cidr()
    try:
        network = ipaddress.ip_network(cidr, strict=True)
    except ValueError as error:
        raise ProvisionError("administrator CIDR override is invalid") from error
    if network.version != 4 or network.prefixlen != 32:
        raise ProvisionError("administrator CIDR override must be an IPv4 /32")
    answer = confirm(
        f"Detected administrator CIDR {cidr}. Type 'use {cidr}' to confirm: "
    )
    if answer != f"use {cidr}":
        raise ProvisionError("administrator CIDR was not confirmed")
    verify_route53_zone(route53_zone_id, domain_name)
    verify_bedrock_and_quota()
    metadata = generated_metadata(
        account_id,
        repository,
        cidr,
        discover_ami(),
        discover_availability_zones(),
    )
    return {
        "schemaVersion": 1,
        "inputs": {
            "domain_name": domain_name,
            "route53_zone_id": route53_zone_id,
        },
        "generated": metadata,
        "outputs": {},
    }


def _required_output(outputs: Mapping[str, Any], root: str, name: str) -> Any:
    root_outputs = outputs.get(root)
    if not isinstance(root_outputs, Mapping) or name not in root_outputs:
        raise ProvisionError(f"{root} output {name} is required before this root")
    return root_outputs[name]


def root_inputs(descriptor: Mapping[str, Any], root: str) -> dict[str, Any]:
    if root not in ROOTS:
        raise ProvisionError("unknown Terraform root")
    generated = descriptor["generated"]
    inputs = descriptor["inputs"]
    outputs = descriptor["outputs"]
    common = {
        "aws_account_id": generated["aws_account_id"],
        "aws_region": AWS_REGION,
    }
    if root == "bootstrap":
        return {
            **common,
            "administrator_cidr": generated["administrator_cidr"],
            "cluster_name": generated["cluster_name"],
            "github_apply_environments": [
                "dev",
                "infrastructure-destroy",
                "infrastructure-provision",
                "prod",
            ],
            "github_repository_subject": generated["github_repository_subject"],
            "loki_bucket_name": generated["loki_bucket_name"],
            "owner_name": generated["owner_name"],
            "project_name": generated["project_name"],
            "route53_zone_id": inputs["route53_zone_id"],
            "state_bucket_name": generated["state_bucket_name"],
            "state_key_prefix": generated["state_key_prefix"],
            "state_lock_table_name": generated["state_lock_table_name"],
        }
    if root == "platform":
        return {
            **common,
            "administrator_cidr": generated["administrator_cidr"],
            "ami_id": generated["ami_id"],
            "availability_zones": generated["availability_zones"],
            "cluster_name": generated["cluster_name"],
            "owner_name": generated["owner_name"],
            "worker_capacity": {"min": 1, "desired": 1, "max": 3},
        }
    if root == "edge":
        return {
            "alb_subnet_ids": _required_output(outputs, "platform", "alb_subnet_ids"),
            "aws_region": AWS_REGION,
            "cluster_name": generated["cluster_name"],
            "domain_name": inputs["domain_name"],
            "loki_bucket_name": generated["loki_bucket_name"],
            "nginx_http_node_port": 32080,
            "owner_name": generated["owner_name"],
            "route53_zone_id": inputs["route53_zone_id"],
            "vpc_id": _required_output(outputs, "platform", "vpc_id"),
            "worker_asg_names": {
                "dev": _required_output(outputs, "platform", "dev_worker_asg_name"),
                "prod": _required_output(outputs, "platform", "prod_worker_asg_name"),
            },
            "worker_security_group_id": _required_output(
                outputs, "platform", "worker_security_group_id"
            ),
        }
    return {
        **common,
        "cluster_name": generated["cluster_name"],
        "control_plane_role_name": _required_output(
            outputs, "platform", "control_plane_role_name"
        ),
        "domain_name": inputs["domain_name"],
        "enable_odoo_key_bootstrap": False,
        "loki_bucket_arn": _required_output(outputs, "edge", "loki_bucket_arn"),
        "owner_name": generated["owner_name"],
        "worker_availability_zone": _required_output(
            outputs, "platform", f"{root}_worker_az"
        ),
        "worker_role_name": _required_output(
            outputs, "platform", f"{root}_worker_role_name"
        ),
    }


def render_input(descriptor: Mapping[str, Any], root: str, path: Path) -> None:
    atomic_write_json(path, root_inputs(descriptor, root))


def verify_runner(descriptor: Mapping[str, Any]) -> None:
    """Verify that a non-interactive runner belongs to this deployment."""
    validate_descriptor(descriptor)
    if discover_account_id() != descriptor["generated"]["aws_account_id"]:
        raise ProvisionError("authenticated AWS account does not match the deployment")
    if discover_repository().full_name != descriptor["generated"]["github_repository"]:
        raise ProvisionError(
            "authenticated GitHub repository does not match the deployment"
        )


def capture_output(
    descriptor_path: Path,
    root: str,
    *,
    root_path: Path | None = None,
) -> None:
    """Atomically record typed Terraform outputs for one applied lifecycle root."""
    if root not in LIFECYCLE_ROOTS:
        raise ProvisionError("output capture is limited to lifecycle roots")
    descriptor = load_descriptor(descriptor_path)
    descriptor["outputs"][root] = _terraform_output(root_path or ROOT_PATHS[root])
    atomic_write_json(descriptor_path, descriptor)


def sync_outputs(descriptor_path: Path) -> None:
    """Synchronize reviewed non-secret Terraform outputs into desired state."""
    from scripts.config.sync_terraform_outputs import sync_from_deployment

    sync_from_deployment(load_descriptor(descriptor_path))


def _terraform_output(root_path: Path) -> dict[str, Any]:
    try:
        result = subprocess.run(
            ["terraform", f"-chdir={root_path}", "output", "-json"],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        raw = json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as error:
        raise ProvisionError("Terraform output could not be read") from error
    if not isinstance(raw, dict):
        raise ProvisionError("Terraform output has an unexpected shape")
    values: dict[str, Any] = {}
    for name, wrapper in raw.items():
        if (
            not isinstance(name, str)
            or not isinstance(wrapper, dict)
            or "value" not in wrapper
        ):
            raise ProvisionError("Terraform output has an unexpected shape")
        values[name] = wrapper["value"]
    return values


def _run(command: Sequence[str], *, cwd: Path | None = None) -> None:
    try:
        subprocess.run(list(command), cwd=cwd, check=True)
    except (OSError, subprocess.SubprocessError) as error:
        raise ProvisionError(f"{command[0]} command failed") from error


def _backend_args(descriptor: Mapping[str, Any], root: str) -> list[str]:
    generated = descriptor["generated"]
    return [
        f"-backend-config=bucket={generated['state_bucket_name']}",
        f"-backend-config=key={generated['state_key_prefix']}/{STATE_KEYS[root]}",
        f"-backend-config=region={AWS_REGION}",
        f"-backend-config=dynamodb_table={generated['state_lock_table_name']}",
        "-backend-config=encrypt=true",
    ]


def _load_checkpoint() -> list[str]:
    if not CHECKPOINT.exists():
        return []
    try:
        payload = json.loads(CHECKPOINT.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ProvisionError("provisioning checkpoint is malformed") from error
    if not isinstance(payload, dict) or not isinstance(
        payload.get("completedRoots"), list
    ):
        raise ProvisionError("provisioning checkpoint is malformed")
    completed = payload["completedRoots"]
    if not all(root in ROOTS for root in completed):
        raise ProvisionError("provisioning checkpoint is malformed")
    return [str(root) for root in completed]


def configure_github(descriptor: Mapping[str, Any]) -> None:
    repository = descriptor["generated"]["github_repository"]
    for environment in ("dev", "prod"):
        _run(
            [
                "gh",
                "api",
                "--method",
                "PUT",
                f"repos/{repository}/environments/{environment}",
            ]
        )
    values = {
        "AWS_TERRAFORM_APPLY_ROLE_ARN": _required_output(
            descriptor["outputs"], "bootstrap", "github_apply_role_arn"
        ),
        "AWS_TERRAFORM_PLAN_ROLE_ARN": _required_output(
            descriptor["outputs"], "bootstrap", "github_plan_role_arn"
        ),
        "TERRAFORM_LOCK_TABLE": descriptor["generated"]["state_lock_table_name"],
        "TERRAFORM_STATE_BUCKET": descriptor["generated"]["state_bucket_name"],
        "TERRAFORM_STATE_KEY_PREFIX": descriptor["generated"]["state_key_prefix"],
    }
    for name, value in values.items():
        _run(["gh", "variable", "set", "-R", repository, name, "--body", str(value)])
    existing_raw = subprocess.run(
        ["gh", "variable", "list", "-R", repository, "--json", "name"],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    ).stdout
    try:
        existing = {item["name"] for item in json.loads(existing_raw)}
    except (json.JSONDecodeError, KeyError, TypeError) as error:
        raise ProvisionError("GitHub variable inventory was malformed") from error
    for name in REMOVED_GITHUB_VARIABLES:
        if name in existing:
            _run(["gh", "variable", "delete", "-R", repository, name])


def provision(descriptor_path: Path = DEFAULT_DESCRIPTOR) -> None:
    account_id = discover_account_id()
    descriptor = load_descriptor(descriptor_path)
    if account_id != descriptor["generated"]["aws_account_id"]:
        print("Initializing an independent deployment for this AWS account.")
        descriptor = create_descriptor(
            input("Route 53 domain name: ").strip(),
            input("Route 53 public hosted-zone ID: ").strip(),
        )
        atomic_write_json(descriptor_path, descriptor)
    if discover_repository().full_name != descriptor["generated"]["github_repository"]:
        raise ProvisionError(
            "authenticated GitHub repository does not match the deployment"
        )
    verify_route53_zone(
        descriptor["inputs"]["route53_zone_id"], descriptor["inputs"]["domain_name"]
    )
    verify_bedrock_and_quota()
    detected_cidr = discover_public_cidr()
    configured_cidr = descriptor["generated"]["administrator_cidr"]
    if detected_cidr != configured_cidr:
        raise ProvisionError(
            "detected administrator CIDR changed; run init with an explicit "
            "reviewed CIDR update"
        )
    if input(f"Type 'use {detected_cidr}' to confirm administrator access: ") != (
        f"use {detected_cidr}"
    ):
        raise ProvisionError("administrator CIDR was not confirmed")
    completed = _load_checkpoint()
    for root in ROOTS:
        if root in completed:
            continue
        root_path = ROOT_PATHS[root]
        render_input(descriptor, root, root_path / TFVARS_FILE)
        init = ["terraform", "init", "-input=false"]
        if root != "bootstrap":
            init.extend(_backend_args(descriptor, root))
        _run(init, cwd=root_path)
        plan_path = root_path / "stockai.tfplan"
        _run(
            ["terraform", "plan", "-input=false", "-no-color", f"-out={plan_path}"],
            cwd=root_path,
        )
        _run(["terraform", "show", "-no-color", str(plan_path)], cwd=root_path)
        if (
            input(f"Type 'apply {root}' to apply this exact saved plan: ")
            != f"apply {root}"
        ):
            raise ProvisionError(f"{root} plan was not approved")
        _run(["terraform", "apply", "-no-color", str(plan_path)], cwd=root_path)
        descriptor["outputs"][root] = _terraform_output(root_path)
        atomic_write_json(descriptor_path, descriptor)
        if root == "bootstrap":
            configure_github(descriptor)
        completed.append(root)
        atomic_write_json(CHECKPOINT, {"completedRoots": completed})
    from scripts.config.sync_terraform_outputs import sync_from_deployment

    sync_from_deployment(descriptor)
    CHECKPOINT.unlink(missing_ok=True)


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--descriptor", type=Path, default=DEFAULT_DESCRIPTOR)
    subparsers = parser.add_subparsers(dest="command", required=True)
    initialize = subparsers.add_parser("init")
    initialize.add_argument("--domain-name", required=True)
    initialize.add_argument("--route53-zone-id", required=True)
    initialize.add_argument("--administrator-cidr")
    render = subparsers.add_parser("render-input")
    render.add_argument("--root", choices=ROOTS, required=True)
    render.add_argument("--output", type=Path, required=True)
    capture = subparsers.add_parser("capture-output")
    capture.add_argument("--root", choices=LIFECYCLE_ROOTS, required=True)
    subparsers.add_parser("verify-runner")
    subparsers.add_parser("sync-outputs")
    subparsers.add_parser("configure-github")
    subparsers.add_parser("provision")
    return parser.parse_args()


def main() -> int:
    arguments = _parse_arguments()
    try:
        if arguments.command == "init":
            descriptor = create_descriptor(
                arguments.domain_name,
                arguments.route53_zone_id,
                administrator_cidr=arguments.administrator_cidr,
            )
            if arguments.descriptor.exists():
                current = load_descriptor(arguments.descriptor)
                same_account = (
                    current["generated"]["aws_account_id"]
                    == descriptor["generated"]["aws_account_id"]
                )
                if same_account:
                    if (
                        current["generated"]["administrator_cidr"]
                        != descriptor["generated"]["administrator_cidr"]
                    ):
                        raise ProvisionError(
                            "administrator CIDR changed; use an explicit "
                            "reviewed update"
                        )
                    if (
                        current["generated"]["github_repository_subject"]
                        != descriptor["generated"]["github_repository_subject"]
                    ):
                        raise ProvisionError(
                            "existing deployment belongs to another repository"
                        )
                    descriptor["generated"] = current["generated"]
                    descriptor["outputs"] = current["outputs"]
            atomic_write_json(arguments.descriptor, descriptor)
        elif arguments.command == "render-input":
            render_input(
                load_descriptor(arguments.descriptor), arguments.root, arguments.output
            )
        elif arguments.command == "configure-github":
            configure_github(load_descriptor(arguments.descriptor))
        elif arguments.command == "verify-runner":
            verify_runner(load_descriptor(arguments.descriptor))
        elif arguments.command == "capture-output":
            capture_output(arguments.descriptor, arguments.root)
        elif arguments.command == "sync-outputs":
            sync_outputs(arguments.descriptor)
        else:
            provision(arguments.descriptor)
    except (
        DiscoveryError,
        OSError,
        ProvisionError,
        subprocess.SubprocessError,
    ) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
