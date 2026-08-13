"""Contracts for bounded, read-only infrastructure discovery."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence

import pytest
from scripts.infra.discovery import (
    AMI_PARAMETER,
    BEDROCK_MODEL_ID,
    DiscoveryError,
    discover_account_id,
    discover_ami,
    discover_availability_zones,
    discover_repository,
    parse_public_cidr,
    verify_bedrock_and_quota,
    verify_route53_zone,
)


def _runner(
    responses: dict[tuple[str, ...], object],
) -> Callable[[Sequence[str]], str]:
    def run(command: Sequence[str]) -> str:
        value = responses[tuple(command)]
        return value if isinstance(value, str) else json.dumps(value)

    return run


def test_discovers_exact_sts_and_immutable_github_identity() -> None:
    runner = _runner(
        {
            ("aws", "sts", "get-caller-identity", "--output", "json"): {
                "Account": "123456789012",
                "Arn": "arn:aws:iam::123456789012:user/operator",
                "UserId": "ignored",
            },
            ("gh", "repo", "view", "--json", "nameWithOwner"): {
                "nameWithOwner": "Example/StockAI"
            },
            ("gh", "api", "repos/Example/StockAI"): {
                "id": 44,
                "name": "StockAI",
                "full_name": "Example/StockAI",
                "owner": {"id": 22, "login": "Example"},
            },
        }
    )

    assert discover_account_id(runner) == "123456789012"
    repository = discover_repository(runner)
    assert repository.full_name == "Example/StockAI"
    assert repository.subject == "Example@22/StockAI@44"


@pytest.mark.parametrize("raw", ("", "2001:db8::1", "not-an-address"))
def test_public_address_must_be_ipv4(raw: str) -> None:
    with pytest.raises(DiscoveryError, match="invalid IPv4"):
        parse_public_cidr(raw)


def test_public_address_is_restricted_to_one_caller() -> None:
    assert parse_public_cidr("203.0.113.10\n") == "203.0.113.10/32"


def test_ami_comes_from_controlled_canonical_parameter_and_owner() -> None:
    ami_id = "ami-0123456789abcdef0"
    runner = _runner(
        {
            (
                "aws",
                "ssm",
                "get-parameter",
                "--region",
                "us-east-1",
                "--name",
                AMI_PARAMETER,
                "--output",
                "json",
            ): {"Parameter": {"Value": ami_id}},
            (
                "aws",
                "ec2",
                "describe-images",
                "--region",
                "us-east-1",
                "--image-ids",
                ami_id,
                "--owners",
                "099720109477",
                "--output",
                "json",
            ): {
                "Images": [
                    {
                        "ImageId": ami_id,
                        "State": "available",
                        "Architecture": "x86_64",
                        "RootDeviceType": "ebs",
                    }
                ]
            },
        }
    )

    assert discover_ami(runner) == ami_id


def test_selects_first_two_distinct_available_zones() -> None:
    runner = _runner(
        {
            (
                "aws",
                "ec2",
                "describe-availability-zones",
                "--region",
                "us-east-1",
                "--filters",
                "Name=state,Values=available",
                "--output",
                "json",
            ): {
                "AvailabilityZones": [
                    {"ZoneName": "us-east-1c"},
                    {"ZoneName": "us-east-1a"},
                    {"ZoneName": "us-east-1b"},
                ]
            }
        }
    )

    assert discover_availability_zones(runner) == ["us-east-1a", "us-east-1b"]


def test_preflight_verifies_public_zone_bedrock_and_six_vcpu_quota() -> None:
    runner = _runner(
        {
            (
                "aws",
                "route53",
                "get-hosted-zone",
                "--id",
                "Z123456789",
                "--output",
                "json",
            ): {
                "HostedZone": {
                    "Name": "example.com.",
                    "Config": {"PrivateZone": False},
                }
            },
            (
                "aws",
                "bedrock",
                "get-foundation-model-availability",
                "--region",
                "us-east-1",
                "--model-id",
                BEDROCK_MODEL_ID,
                "--output",
                "json",
            ): {
                "modelId": BEDROCK_MODEL_ID,
                "agreementAvailability": {"status": "AVAILABLE"},
                "authorizationStatus": "AUTHORIZED",
                "entitlementAvailability": "AVAILABLE",
                "regionAvailability": "AVAILABLE",
            },
            (
                "aws",
                "service-quotas",
                "get-service-quota",
                "--region",
                "us-east-1",
                "--service-code",
                "ec2",
                "--quota-code",
                "L-1216C47A",
                "--output",
                "json",
            ): {"Quota": {"Value": 8.0}},
        }
    )

    verify_route53_zone("Z123456789", "example.com", runner)
    verify_bedrock_and_quota(runner)


def test_malformed_discovery_never_echoes_provider_output() -> None:
    secret_like_output = '{"Account":"token-do-not-print"}'

    with pytest.raises(DiscoveryError) as failure:
        discover_account_id(lambda _: secret_like_output)

    assert "token-do-not-print" not in str(failure.value)
