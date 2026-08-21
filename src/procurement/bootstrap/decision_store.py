"""Shared composition helper for the manager-decision read boundary."""

from procurement.adapters.aws.dynamodb import (
    DynamoApplicationRepository,
    create_dynamodb_client,
)
from procurement.domain.identifiers import Environment
from procurement.ports.decisions import DecisionReader
from procurement.ports.repositories import InMemoryApplicationRepository


def create_decision_reader(
    *,
    environment: Environment,
    aws_region: str,
    application_table: str,
    dynamodb_endpoint_url: str | None,
    use_dynamodb: bool,
) -> DecisionReader:
    """Construct the configured store without leaking AWS into MCP bootstrap."""

    if not use_dynamodb:
        return InMemoryApplicationRepository(environment=environment)
    return DynamoApplicationRepository(
        client=create_dynamodb_client(
            region_name=aws_region,
            endpoint_url=dynamodb_endpoint_url,
        ),
        table_name=application_table,
        environment=environment,
    )
