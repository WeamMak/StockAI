"""Authoritative deterministic procurement-evidence MCP tool."""

from __future__ import annotations

import asyncio
import logging
from time import perf_counter

from pydantic import ValidationError

from procurement.domain.errors import ErrorCode
from procurement.domain.identifiers import Environment
from procurement.mcp_server.observability import McpMetrics
from procurement.mcp_server.schemas import (
    GetProcurementEvidenceInput,
    ProcurementEvidenceOutput,
)
from procurement.mcp_server.tools.candidates import SafeMcpToolError
from procurement.observability.logging import log_event
from procurement.ports.erp import ErpPort, ErpUnavailableError, ProcurementEvidenceQuery

TOOL_NAME = "get_procurement_evidence"


async def get_procurement_evidence(
    *,
    request: GetProcurementEvidenceInput,
    erp: ErpPort,
    server_environment: Environment,
    metrics: McpMetrics,
    logger: logging.Logger,
    read_timeout_seconds: float = 10,
) -> ProcurementEvidenceOutput:
    """Read, strictly validate, observe, and return one evidence record."""

    started_at = perf_counter()
    error_code: ErrorCode | None = None
    try:
        if request.environment != server_environment.value:
            raise SafeMcpToolError(
                ErrorCode.FORBIDDEN,
                "The requested environment is not allowed.",
                0,
            )
        async with asyncio.timeout(read_timeout_seconds):
            evidence = await erp.get_procurement_evidence(
                ProcurementEvidenceQuery(
                    environment=server_environment,
                    product_id=request.product_id,
                    horizon_days=request.horizon_days,
                )
            )
        if evidence.environment is not server_environment:
            raise ValueError("cross-environment evidence")
        response = ProcurementEvidenceOutput.model_validate(
            evidence.to_dict(), strict=False
        )
    except SafeMcpToolError:
        error_code = ErrorCode.FORBIDDEN
        raise
    except TimeoutError:
        error_code = ErrorCode.MCP_TIMEOUT
        metrics.record_timeout(tool=TOOL_NAME)
        raise SafeMcpToolError(
            error_code, "The procurement source timed out.", 0
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
            "The procurement source returned invalid evidence.",
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
