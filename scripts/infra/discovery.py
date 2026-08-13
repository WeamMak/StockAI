"""Read-only discovery for the fixed StockAI AWS deployment."""

from __future__ import annotations

import ipaddress
import json
import subprocess
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

AWS_REGION = "us-east-1"
BEDROCK_MODEL_ID = "openai.gpt-oss-20b-1:0"
CANONICAL_OWNER_ID = "099720109477"
AMI_PARAMETER = (
    "/aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id"
)
COMMAND_TIMEOUT_SECONDS = 30
MAX_JSON_BYTES = 128 * 1024


class DiscoveryError(ValueError):
    """A safe preflight failure that never includes command output."""


@dataclass(frozen=True)
class RepositoryIdentity:
    """Immutable GitHub repository coordinates used by OIDC trust."""

    full_name: str
    owner: str
    owner_id: int
    repository: str
    repository_id: int

    @property
    def subject(self) -> str:
        return f"{self.owner}@{self.owner_id}/{self.repository}@{self.repository_id}"


Runner = Callable[[Sequence[str]], str]


def run_command(command: Sequence[str]) -> str:
    """Run one bounded read-only command and return stdout."""

    try:
        result = subprocess.run(
            list(command),
            check=True,
            capture_output=True,
            text=True,
            timeout=COMMAND_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise DiscoveryError(f"{command[0]} discovery command failed") from error
    if len(result.stdout.encode("utf-8")) > MAX_JSON_BYTES:
        raise DiscoveryError(f"{command[0]} discovery response is too large")
    return result.stdout


def _json_object(raw: str, source: str) -> dict[str, Any]:
    if len(raw.encode("utf-8")) > MAX_JSON_BYTES:
        raise DiscoveryError(f"{source} response is too large")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise DiscoveryError(f"{source} returned malformed JSON") from error
    if not isinstance(value, dict):
        raise DiscoveryError(f"{source} returned an unexpected JSON shape")
    return value


def discover_account_id(runner: Runner = run_command) -> str:
    payload = _json_object(
        runner(["aws", "sts", "get-caller-identity", "--output", "json"]),
        "AWS STS",
    )
    account_id = payload.get("Account")
    if (
        not isinstance(account_id, str)
        or not account_id.isdigit()
        or len(account_id) != 12
    ):
        raise DiscoveryError("AWS STS returned an invalid account identity")
    return account_id


def discover_repository(runner: Runner = run_command) -> RepositoryIdentity:
    view = _json_object(
        runner(["gh", "repo", "view", "--json", "nameWithOwner"]),
        "GitHub repository",
    )
    full_name = view.get("nameWithOwner")
    if not isinstance(full_name, str) or full_name.count("/") != 1:
        raise DiscoveryError("GitHub returned an invalid repository name")
    payload = _json_object(
        runner(["gh", "api", f"repos/{full_name}"]), "GitHub repository"
    )
    owner = payload.get("owner")
    repository_id = payload.get("id")
    name = payload.get("name")
    if (
        not isinstance(owner, dict)
        or not isinstance(owner.get("login"), str)
        or not isinstance(owner.get("id"), int)
        or not isinstance(repository_id, int)
        or not isinstance(name, str)
        or payload.get("full_name") != full_name
    ):
        raise DiscoveryError("GitHub returned an invalid immutable repository identity")
    return RepositoryIdentity(
        full_name=full_name,
        owner=owner["login"],
        owner_id=owner["id"],
        repository=name,
        repository_id=repository_id,
    )


def parse_public_cidr(raw: str) -> str:
    try:
        address = ipaddress.IPv4Address(raw.strip())
    except ipaddress.AddressValueError as error:
        raise DiscoveryError(
            "public address detection returned invalid IPv4"
        ) from error
    return f"{address}/32"


def discover_public_cidr() -> str:
    try:
        with urllib.request.urlopen(  # noqa: S310 - fixed HTTPS endpoint
            "https://checkip.amazonaws.com", timeout=10
        ) as response:
            raw = response.read(64).decode("ascii")
    except (OSError, UnicodeError) as error:
        raise DiscoveryError("public IPv4 detection failed") from error
    return parse_public_cidr(raw)


def discover_ami(runner: Runner = run_command) -> str:
    parameter = _json_object(
        runner(
            [
                "aws",
                "ssm",
                "get-parameter",
                "--region",
                AWS_REGION,
                "--name",
                AMI_PARAMETER,
                "--output",
                "json",
            ]
        ),
        "AWS SSM AMI",
    )
    parameter_value = parameter.get("Parameter")
    ami_id = parameter_value.get("Value") if isinstance(parameter_value, dict) else None
    if not isinstance(ami_id, str) or not ami_id.startswith("ami-"):
        raise DiscoveryError("AWS SSM returned an invalid AMI identifier")
    image = _json_object(
        runner(
            [
                "aws",
                "ec2",
                "describe-images",
                "--region",
                AWS_REGION,
                "--image-ids",
                ami_id,
                "--owners",
                CANONICAL_OWNER_ID,
                "--output",
                "json",
            ]
        ),
        "AWS EC2 AMI",
    )
    images = image.get("Images")
    if (
        not isinstance(images, list)
        or len(images) != 1
        or not isinstance(images[0], dict)
        or images[0].get("ImageId") != ami_id
        or images[0].get("State") != "available"
        or images[0].get("Architecture") != "x86_64"
        or images[0].get("RootDeviceType") != "ebs"
    ):
        raise DiscoveryError("the controlled Ubuntu AMI is not usable")
    return ami_id


def discover_availability_zones(runner: Runner = run_command) -> list[str]:
    payload = _json_object(
        runner(
            [
                "aws",
                "ec2",
                "describe-availability-zones",
                "--region",
                AWS_REGION,
                "--filters",
                "Name=state,Values=available",
                "--output",
                "json",
            ]
        ),
        "AWS Availability Zones",
    )
    zones = payload.get("AvailabilityZones")
    names: set[str] = set()
    if isinstance(zones, list):
        for zone in zones:
            if not isinstance(zone, dict):
                continue
            name = zone.get("ZoneName")
            if isinstance(name, str) and name.startswith(AWS_REGION):
                names.add(name)
    if len(names) < 2:
        raise DiscoveryError("AWS account needs two available us-east-1 zones")
    return sorted(names)[:2]


def verify_route53_zone(
    zone_id: str, domain_name: str, runner: Runner = run_command
) -> None:
    payload = _json_object(
        runner(
            [
                "aws",
                "route53",
                "get-hosted-zone",
                "--id",
                zone_id,
                "--output",
                "json",
            ]
        ),
        "Route 53",
    )
    zone = payload.get("HostedZone")
    config = zone.get("Config") if isinstance(zone, dict) else None
    if (
        not isinstance(zone, dict)
        or zone.get("Name") != f"{domain_name}."
        or not isinstance(config, dict)
        or config.get("PrivateZone") is not False
    ):
        raise DiscoveryError("Route 53 zone is not the requested public domain")


def verify_bedrock_and_quota(runner: Runner = run_command) -> None:
    availability = _json_object(
        runner(
            [
                "aws",
                "bedrock",
                "get-foundation-model-availability",
                "--region",
                AWS_REGION,
                "--model-id",
                BEDROCK_MODEL_ID,
                "--output",
                "json",
            ]
        ),
        "Amazon Bedrock",
    )
    if (
        availability.get("modelId") != BEDROCK_MODEL_ID
        or availability.get("agreementAvailability", {}).get("status") != "AVAILABLE"
        or availability.get("authorizationStatus") != "AUTHORIZED"
        or availability.get("entitlementAvailability") != "AVAILABLE"
        or availability.get("regionAvailability") != "AVAILABLE"
    ):
        raise DiscoveryError("the approved Bedrock model is not available")
    quota = _json_object(
        runner(
            [
                "aws",
                "service-quotas",
                "get-service-quota",
                "--region",
                AWS_REGION,
                "--service-code",
                "ec2",
                "--quota-code",
                "L-1216C47A",
                "--output",
                "json",
            ]
        ),
        "EC2 quota",
    )
    value = quota.get("Quota", {}).get("Value")
    if not isinstance(value, (int, float)) or value < 6:
        raise DiscoveryError("EC2 standard-instance quota is below six vCPUs")
