"""Local composition root for the runnable Procurement MCP process."""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

import uvicorn
from starlette.types import ASGIApp

from procurement.domain.identifiers import Environment
from procurement.mcp_server.server import SERVICE_NAME, create_mcp_server
from procurement.observability.logging import configure_json_logging
from procurement.ports.erp import (
    CandidatePage,
    ErpPort,
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
    log_level: int = logging.INFO
    read_timeout_seconds: float = 10.0
    max_retries: int = 2
    retry_delay_seconds: float = 0.05

    def __post_init__(self) -> None:
        if self.erp_mode not in _SUPPORTED_MODES:
            raise ValueError("PROCUREMENT_LOCAL_ERP_MODE must be success or timeout")
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
    server = create_mcp_server(
        erp=LocalFictionalErp(mode=resolved.erp_mode),
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
