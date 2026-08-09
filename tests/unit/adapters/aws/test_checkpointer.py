"""Construction contract for the approved DynamoDB LangGraph checkpointer."""

from __future__ import annotations

from typing import Any

from procurement.adapters.aws.checkpointer import (
    CHECKPOINT_TTL_SECONDS,
    DynamoCheckpointSettings,
    create_dynamodb_checkpointer,
)
from procurement.domain.identifiers import Environment


class RecordingSaver:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs


def test_dev_checkpointer_uses_the_separate_table_endpoint_and_30_day_ttl() -> None:
    settings = DynamoCheckpointSettings(
        environment=Environment.DEV,
        table_name="stockai-dev-checkpoints",
        region_name="us-east-1",
        endpoint_url="http://dynamodb-local:8000",
    )

    saver = create_dynamodb_checkpointer(settings, saver_type=RecordingSaver)

    assert CHECKPOINT_TTL_SECONDS[Environment.DEV] == 30 * 24 * 60 * 60
    assert saver.kwargs == {
        "table_name": "stockai-dev-checkpoints",
        "region_name": "us-east-1",
        "endpoint_url": "http://dynamodb-local:8000",
        "ttl_seconds": 30 * 24 * 60 * 60,
    }


def test_prod_checkpointer_uses_one_year_ttl_and_no_local_endpoint() -> None:
    settings = DynamoCheckpointSettings(
        environment=Environment.PROD,
        table_name="stockai-prod-checkpoints",
        region_name="us-east-1",
    )

    saver = create_dynamodb_checkpointer(settings, saver_type=RecordingSaver)

    assert saver.kwargs["ttl_seconds"] == 365 * 24 * 60 * 60
    assert saver.kwargs["endpoint_url"] is None
