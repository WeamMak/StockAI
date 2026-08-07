"""Local composition root for the runnable Procurement MCP process."""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation

import httpx
import uvicorn
from starlette.types import ASGIApp

from procurement.domain.identifiers import Environment
from procurement.mcp_server.server import SERVICE_NAME, create_mcp_server
from procurement.observability.logging import configure_json_logging
from procurement.ports.erp import (
    CandidatePage,
    ErpPort,
    ErpUnavailableError,
    ReplenishmentCandidateRecord,
    ReplenishmentCandidatesQuery,
)

_SUPPORTED_MODES = frozenset({"success", "timeout"})


@dataclass(frozen=True, slots=True)
class LocalMcpSettings:
    """Validated process settings for the local deterministic MCP service."""

    environment: Environment
    bearer_token: str = field(repr=False)
    erp_mode: str = "success"
    erp_url: str | None = None
    log_level: int = logging.INFO
    read_timeout_seconds: float = 10.0
    max_retries: int = 2
    retry_delay_seconds: float = 0.05

    def __post_init__(self) -> None:
        if self.erp_mode not in _SUPPORTED_MODES:
            raise ValueError("PROCUREMENT_LOCAL_ERP_MODE must be success or timeout")
        if self.erp_url is not None:
            parsed_url = httpx.URL(self.erp_url)
            if parsed_url.scheme not in {"http", "https"} or parsed_url.host is None:
                raise ValueError(
                    "PROCUREMENT_LOCAL_ERP_URL must be an absolute HTTP URL"
                )
        if not 0 < self.read_timeout_seconds <= 120:
            raise ValueError(
                "PROCUREMENT_MCP_READ_TIMEOUT_SECONDS must be between 0 and 120"
            )
        if not 0 <= self.max_retries <= 2:
            raise ValueError("PROCUREMENT_MCP_MAX_RETRIES must be between 0 and 2")
        if not 0 <= self.retry_delay_seconds <= 10:
            raise ValueError(
                "PROCUREMENT_MCP_RETRY_DELAY_SECONDS must be between 0 and 10"
            )

    @classmethod
    def from_environment(cls) -> LocalMcpSettings:
        """Load local MCP settings without providing a credential default."""

        bearer_token = os.environ.get("PROCUREMENT_MCP_TOKEN")
        if bearer_token is None:
            raise ValueError("PROCUREMENT_MCP_TOKEN is required")
        raw_log_level = os.environ.get("PROCUREMENT_LOG_LEVEL", "INFO").upper()
        log_levels = {
            "DEBUG": logging.DEBUG,
            "INFO": logging.INFO,
            "WARNING": logging.WARNING,
            "ERROR": logging.ERROR,
            "CRITICAL": logging.CRITICAL,
        }
        try:
            environment = Environment(
                os.environ.get("PROCUREMENT_ENVIRONMENT", Environment.DEV.value)
            )
        except ValueError as error:
            raise ValueError("PROCUREMENT_ENVIRONMENT must be dev or prod") from error
        try:
            log_level = log_levels[raw_log_level]
        except KeyError as error:
            raise ValueError("PROCUREMENT_LOG_LEVEL is invalid") from error
        return cls(
            environment=environment,
            bearer_token=bearer_token,
            erp_mode=os.environ.get("PROCUREMENT_LOCAL_ERP_MODE", "success"),
            erp_url=os.environ.get("PROCUREMENT_LOCAL_ERP_URL"),
            log_level=log_level,
            read_timeout_seconds=float(
                os.environ.get("PROCUREMENT_MCP_READ_TIMEOUT_SECONDS", "10")
            ),
            max_retries=int(os.environ.get("PROCUREMENT_MCP_MAX_RETRIES", "2")),
            retry_delay_seconds=float(
                os.environ.get("PROCUREMENT_MCP_RETRY_DELAY_SECONDS", "0.05")
            ),
        )


@dataclass(frozen=True, slots=True)
class LocalFictionalErp(ErpPort):
    """One bounded fictional ERP read used only by the local skeleton."""

    mode: str

    async def list_replenishment_candidates(
        self,
        query: ReplenishmentCandidatesQuery,
    ) -> CandidatePage:
        del query
        if self.mode == "timeout":
            await asyncio.sleep(3_600)
        return CandidatePage(
            items=(
                ReplenishmentCandidateRecord(
                    product_id="product-101",
                    product_name="Fictional Safety Gloves",
                    category_id="category-safety",
                    reorder_minimum=Decimal("10.000000"),
                    reorder_maximum=Decimal("40.000000"),
                    projected_quantity=Decimal("8.000000"),
                    projected_trigger_date=date(2026, 8, 8),
                    skip_reason_code=None,
                ),
            ),
            next_cursor=None,
        )


def _candidate_record(raw: object) -> ReplenishmentCandidateRecord:
    if not isinstance(raw, Mapping) or set(raw) != {
        "product_id",
        "product_name",
        "category_id",
        "reorder_minimum",
        "reorder_maximum",
        "projected_quantity",
        "projected_trigger_date",
        "skip_reason_code",
    }:
        raise ValueError("fake ERP candidate payload is invalid")
    string_fields = (
        "product_id",
        "product_name",
        "category_id",
        "reorder_minimum",
        "reorder_maximum",
        "projected_quantity",
        "projected_trigger_date",
    )
    if not all(isinstance(raw[field], str) for field in string_fields):
        raise ValueError("fake ERP candidate fields are invalid")
    skip_reason_code = raw["skip_reason_code"]
    if skip_reason_code is not None and not isinstance(skip_reason_code, str):
        raise ValueError("fake ERP skip reason is invalid")
    return ReplenishmentCandidateRecord(
        product_id=raw["product_id"],
        product_name=raw["product_name"],
        category_id=raw["category_id"],
        reorder_minimum=Decimal(raw["reorder_minimum"]),
        reorder_maximum=Decimal(raw["reorder_maximum"]),
        projected_quantity=Decimal(raw["projected_quantity"]),
        projected_trigger_date=date.fromisoformat(raw["projected_trigger_date"]),
        skip_reason_code=skip_reason_code,
    )


def _candidate_page(raw: object) -> CandidatePage:
    if not isinstance(raw, Mapping) or set(raw) != {"items", "next_cursor"}:
        raise ValueError("fake ERP page payload is invalid")
    items = raw["items"]
    next_cursor = raw["next_cursor"]
    if not isinstance(items, list):
        raise ValueError("fake ERP page items are invalid")
    if next_cursor is not None and not isinstance(next_cursor, str):
        raise ValueError("fake ERP cursor is invalid")
    return CandidatePage(
        items=tuple(_candidate_record(item) for item in items),
        next_cursor=next_cursor,
    )


@dataclass(frozen=True, slots=True)
class LocalHttpFictionalErp(ErpPort):
    """Call the separate deterministic fake Odoo service used by Compose."""

    base_url: str
    timeout_seconds: float

    async def list_replenishment_candidates(
        self,
        query: ReplenishmentCandidatesQuery,
    ) -> CandidatePage:
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout_seconds,
            ) as client:
                response = await client.post(
                    "/test/replenishment-candidates",
                    json={
                        "horizon_days": query.horizon_days,
                        "limit": query.limit,
                        "cursor": query.cursor,
                    },
                )
                response.raise_for_status()
                return _candidate_page(response.json())
        except httpx.TimeoutException:
            raise TimeoutError from None
        except (httpx.HTTPError, InvalidOperation, TypeError, ValueError) as error:
            raise ErpUnavailableError from error


def create_local_mcp_app(
    settings: LocalMcpSettings | None = None,
) -> ASGIApp:
    """Build the local MCP process with only MCP-owned dependencies."""

    resolved = settings or LocalMcpSettings.from_environment()
    logger = configure_json_logging(
        service=SERVICE_NAME,
        environment=resolved.environment.value,
        level=resolved.log_level,
    )
    erp: ErpPort = (
        LocalHttpFictionalErp(
            base_url=resolved.erp_url,
            timeout_seconds=resolved.read_timeout_seconds,
        )
        if resolved.erp_url is not None
        else LocalFictionalErp(mode=resolved.erp_mode)
    )
    server = create_mcp_server(
        erp=erp,
        environment=resolved.environment,
        bearer_token=resolved.bearer_token,
        host="0.0.0.0",  # noqa: S104 - accept the private container-network host
        logger=logger,
        read_timeout_seconds=resolved.read_timeout_seconds,
        max_retries=resolved.max_retries,
        retry_delay_seconds=resolved.retry_delay_seconds,
    )
    return server.streamable_http_app()


app = create_local_mcp_app()


def run() -> None:
    """Run only the configured MCP composition root."""

    uvicorn.run(
        app,
        host="0.0.0.0",  # noqa: S104 - required inside the container boundary
        port=9000,
        log_level="warning",
        access_log=False,
        server_header=False,
    )
