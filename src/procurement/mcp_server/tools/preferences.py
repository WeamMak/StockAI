"""Resolved typed procurement-preference MCP tool."""

from __future__ import annotations

import asyncio
import logging
from time import perf_counter

from pydantic import ValidationError

from procurement.domain.errors import ErrorCode
from procurement.domain.identifiers import Environment
from procurement.mcp_server.observability import McpMetrics
from procurement.mcp_server.schemas import (
    GetProcurementPreferencesInput,
    ProcurementPreferenceOutput,
)
from procurement.mcp_server.tools.candidates import SafeMcpToolError
from procurement.observability.logging import log_event
from procurement.ports.erp import (
    ErpPort,
    ErpUnavailableError,
    ProcurementPreferenceQuery,
)

TOOL_NAME = "get_procurement_preferences"


async def get_procurement_preferences(
    *,
    request: GetProcurementPreferencesInput,
    erp: ErpPort,
    server_environment: Environment,
    metrics: McpMetrics,
    logger: logging.Logger,
    read_timeout_seconds: float = 10,
) -> ProcurementPreferenceOutput:
    """Resolve, strictly validate, observe, and return one preference."""

    started_at = perf_counter()
    error_code: ErrorCode | None = None
    try:
        if request.environment != server_environment.value:
            raise SafeMcpToolError(
                ErrorCode.FORBIDDEN, "The requested environment is not allowed.", 0
            )
        async with asyncio.timeout(read_timeout_seconds):
            profile = await erp.get_procurement_preferences(
                ProcurementPreferenceQuery(
                    environment=server_environment,
                    company_id=request.company_id,
                    category_id=request.category_id,
                    product_id=request.product_id,
                )
            )
        response = ProcurementPreferenceOutput.model_validate(
            profile.to_dict(), strict=False
        )
        if (
            response.company_id != request.company_id
            or response.category_id != request.category_id
            or response.product_id != request.product_id
        ):
            raise ValueError("preference identity mismatch")
    except SafeMcpToolError:
        error_code = ErrorCode.FORBIDDEN
        raise
    except TimeoutError:
        error_code = ErrorCode.MCP_TIMEOUT
        metrics.record_timeout(tool=TOOL_NAME)
        raise SafeMcpToolError(
            error_code, "The procurement preference source timed out.", 0
        ) from None
    except (
        ErpUnavailableError,
        AttributeError,
        TypeError,
        ValueError,
        ValidationError,
    ):
        error_code = ErrorCode.ODOO_UNAVAILABLE
        raise SafeMcpToolError(
            error_code,
            "The procurement preferences require configuration review.",
            0,
        ) from None
    finally:
        duration = perf_counter() - started_at
        metrics.observe_call(
            tool=TOOL_NAME,
            status="error" if error_code is not None else "success",
            duration_seconds=duration,
            error_code=error_code,
        )
        log_event(
            logger,
            "mcp_tool_completed",
            level=logging.ERROR if error_code is not None else logging.INFO,
            tool_name=TOOL_NAME,
            status="error" if error_code is not None else "success",
            duration_ms=round(duration * 1000, 3),
            retry_count=0,
            **({"error_code": error_code.value} if error_code is not None else {}),
        )
    return response
