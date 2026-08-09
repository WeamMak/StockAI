"""Construction boundary for the course-supported DynamoDB checkpointer."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, overload

from langgraph_checkpoint_aws import DynamoDBSaver  # type: ignore[import-untyped]

from procurement.domain.identifiers import Environment

CHECKPOINT_TTL_SECONDS = {
    Environment.DEV: 30 * 24 * 60 * 60,
    Environment.PROD: 365 * 24 * 60 * 60,
}


@dataclass(frozen=True, slots=True)
class DynamoCheckpointSettings:
    """Validated runtime coordinates for one checkpoint table."""

    environment: Environment
    table_name: str
    region_name: str
    endpoint_url: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.environment, Environment):
            raise ValueError("environment must be dev or prod")
        if not self.table_name.strip():
            raise ValueError("checkpoint table name is required")
        if not self.region_name.strip():
            raise ValueError("AWS region is required")
        if self.endpoint_url is not None and not self.endpoint_url.startswith(
            ("http://", "https://")
        ):
            raise ValueError("DynamoDB endpoint must be an absolute HTTP URL")


@overload
def create_dynamodb_checkpointer(
    settings: DynamoCheckpointSettings,
) -> DynamoDBSaver: ...


@overload
def create_dynamodb_checkpointer[SaverT](
    settings: DynamoCheckpointSettings,
    *,
    saver_type: Callable[..., SaverT],
) -> SaverT: ...


def create_dynamodb_checkpointer(
    settings: DynamoCheckpointSettings,
    *,
    saver_type: Callable[..., Any] = DynamoDBSaver,
) -> Any:
    """Create only the approved saver with environment retention applied."""

    return saver_type(
        table_name=settings.table_name,
        region_name=settings.region_name,
        endpoint_url=settings.endpoint_url,
        ttl_seconds=CHECKPOINT_TTL_SECONDS[settings.environment],
    )
