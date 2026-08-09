"""Read-only replenishment-candidate tool behavior."""

from __future__ import annotations

import asyncio
import json
import logging
import random
from time import perf_counter

from pydantic import ValidationError

from procurement.domain.errors import ErrorCode
from procurement.domain.identifiers import Environment
from procurement.mcp_server.observability import McpMetrics
from procurement.mcp_server.schemas import (
    CandidateSkipMetadata,
    ListReplenishmentCandidatesInput,
    ListReplenishmentCandidatesOutput,
    ReplenishmentCandidate,
)
from procurement.observability.logging import log_event
from procurement.ports.erp import (
    CandidatePage,
    ErpPort,
    ErpUnavailableError,
    ReplenishmentCandidateRecord,
    ReplenishmentCandidatesQuery,
)

TOOL_NAME = "list_replenishment_candidates"
_RETRYABLE_CODES = frozenset({ErrorCode.MCP_TIMEOUT, ErrorCode.ODOO_UNAVAILABLE})


class SafeMcpToolError(Exception):
    """Stable MCP tool failure containing no raw request or upstream data."""

    def __init__(
        self,
        error_code: ErrorCode,
        safe_message: str,
        retry_count: int,
    ) -> None:
        super().__init__(safe_message)
        self.error_code = error_code
        self.safe_message = safe_message
        self.retry_count = retry_count

    @property
    def retryable(self) -> bool:
        """Whether a caller may safely retry this read after backoff."""

        return self.error_code in _RETRYABLE_CODES

    def __str__(self) -> str:
        return json.dumps(
            {
                "error_code": self.error_code.value,
                "message": self.safe_message,
                "retry_count": self.retry_count,
                "retryable": self.retryable,
            },
            separators=(",", ":"),
            sort_keys=True,
        )


def _candidate_from_record(
    record: ReplenishmentCandidateRecord,
) -> ReplenishmentCandidate:
    skip_metadata = (
        CandidateSkipMetadata(reason_code=record.skip_reason_code)
        if record.skip_reason_code is not None
        else None
    )
    return ReplenishmentCandidate(
        product_id=record.product_id,
        product_name=record.product_name,
        category_id=record.category_id,
        reorder_minimum=record.reorder_minimum,
        reorder_maximum=record.reorder_maximum,
        projected_quantity=record.projected_quantity,
        projected_trigger_date=record.projected_trigger_date,
        skip_metadata=skip_metadata,
    )


def _validated_output(
    *,
    page: CandidatePage,
    request: ListReplenishmentCandidatesInput,
) -> ListReplenishmentCandidatesOutput:
    if not isinstance(page, CandidatePage):
        raise TypeError("ERP response must be a CandidatePage")
    if not isinstance(page.items, tuple) or not all(
        isinstance(item, ReplenishmentCandidateRecord) for item in page.items
    ):
        raise TypeError("ERP response items are invalid")
    if len(page.items) > request.limit:
        raise ValueError("ERP response exceeds the requested limit")
    return ListReplenishmentCandidatesOutput(
        environment=request.environment,
        candidates=tuple(_candidate_from_record(record) for record in page.items),
        next_cursor=page.next_cursor,
    )


def _record_completion(
    *,
    metrics: McpMetrics,
    logger: logging.Logger,
    started_at: float,
    status: str,
    retry_count: int,
    error_code: ErrorCode | None = None,
) -> None:
    duration_seconds = perf_counter() - started_at
    metrics.observe_call(
        tool=TOOL_NAME,
        status=status,
        duration_seconds=duration_seconds,
        error_code=error_code,
    )
    fields: dict[str, object] = {
        "tool_name": TOOL_NAME,
        "duration_ms": round(duration_seconds * 1000, 3),
        "status": status,
        "retry_count": retry_count,
    }
    if error_code is not None:
        fields["error_code"] = error_code.value
    log_event(
        logger,
        "mcp_tool_completed",
        level=logging.ERROR if error_code is not None else logging.INFO,
        **fields,
    )


def _raise_safe_error(
    *,
    error_code: ErrorCode,
    safe_message: str,
    metrics: McpMetrics,
    logger: logging.Logger,
    started_at: float,
    retry_count: int,
) -> None:
    _record_completion(
        metrics=metrics,
        logger=logger,
        started_at=started_at,
        status="error",
        retry_count=retry_count,
        error_code=error_code,
    )
    raise SafeMcpToolError(error_code, safe_message, retry_count) from None


async def list_replenishment_candidates(
    *,
    request: ListReplenishmentCandidatesInput,
    erp: ErpPort,
    server_environment: Environment,
    metrics: McpMetrics,
    logger: logging.Logger,
    read_timeout_seconds: float = 10,
    max_retries: int = 2,
    retry_delay_seconds: float = 0.05,
) -> ListReplenishmentCandidatesOutput:
    """Read, validate, observe, and safely expose one candidate page."""

    if read_timeout_seconds <= 0:
        raise ValueError("read_timeout_seconds must be positive")
    if not 0 <= max_retries <= 2:
        raise ValueError("max_retries must be between zero and two")
    if retry_delay_seconds < 0:
        raise ValueError("retry_delay_seconds cannot be negative")

    started_at = perf_counter()
    retry_count = 0
    if request.environment != server_environment.value:
        _raise_safe_error(
            error_code=ErrorCode.FORBIDDEN,
            safe_message="The requested environment is not allowed.",
            metrics=metrics,
            logger=logger,
            started_at=started_at,
            retry_count=retry_count,
        )

    query = ReplenishmentCandidatesQuery(
        horizon_days=request.horizon_days,
        limit=request.limit,
        cursor=request.cursor,
    )
    while True:
        try:
            async with asyncio.timeout(read_timeout_seconds):
                page = await erp.list_replenishment_candidates(query)
            break
        except (TimeoutError, ErpUnavailableError) as error:
            is_timeout = isinstance(error, TimeoutError)
            adapter_retry_count = getattr(error, "retry_count", 0)
            if type(adapter_retry_count) is int and 0 <= adapter_retry_count <= 2:
                retry_count = max(retry_count, adapter_retry_count)
            if is_timeout:
                metrics.record_timeout(tool=TOOL_NAME)
            if retry_count >= max_retries:
                _raise_safe_error(
                    error_code=(
                        ErrorCode.MCP_TIMEOUT
                        if is_timeout
                        else ErrorCode.ODOO_UNAVAILABLE
                    ),
                    safe_message=(
                        "The procurement source timed out."
                        if is_timeout
                        else "The procurement source is unavailable."
                    ),
                    metrics=metrics,
                    logger=logger,
                    started_at=started_at,
                    retry_count=retry_count,
                )
            retry_count += 1
            metrics.record_retry(tool=TOOL_NAME)
            backoff_ceiling = retry_delay_seconds * (2 ** (retry_count - 1))
            await asyncio.sleep(
                random.uniform(0, backoff_ceiling)  # noqa: S311
            )
        except Exception:
            _raise_safe_error(
                error_code=ErrorCode.ODOO_UNAVAILABLE,
                safe_message="The procurement source is unavailable.",
                metrics=metrics,
                logger=logger,
                started_at=started_at,
                retry_count=retry_count,
            )

    try:
        response = _validated_output(page=page, request=request)
    except (TypeError, ValueError, ValidationError):
        _raise_safe_error(
            error_code=ErrorCode.ODOO_UNAVAILABLE,
            safe_message="The procurement source returned an invalid response.",
            metrics=metrics,
            logger=logger,
            started_at=started_at,
            retry_count=retry_count,
        )

    _record_completion(
        metrics=metrics,
        logger=logger,
        started_at=started_at,
        status="success",
        retry_count=retry_count,
    )
    return response
