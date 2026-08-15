"""Local composition root for the runnable Procurement MCP process."""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation

import httpx
import uvicorn
from starlette.types import ASGIApp, Receive, Scope, Send

from procurement.adapters.odoo.client import (
    OdooErpAdapter,
    OdooJson2Client,
    create_odoo_metrics,
)
from procurement.domain.identifiers import Environment
from procurement.domain.policy.budget import calculate_budget
from procurement.domain.policy.coverage import apply_coverage
from procurement.domain.policy.evidence import (
    ProcurementEvidence,
    procurement_evidence_from_dict,
)
from procurement.domain.policy.forecast import StockMovement, project_shortage
from procurement.domain.policy.offers import VendorOffer, evaluate_offer
from procurement.domain.policy.performance import CompletedOrder, performance_evidence
from procurement.domain.policy.preferences import (
    PreferenceCriterion,
    PreferenceScope,
    PremiumEnforcement,
    ProcurementPreference,
    preference_from_dict,
)
from procurement.mcp_server.observability import create_mcp_metrics
from procurement.mcp_server.server import SERVICE_NAME, create_mcp_server
from procurement.observability.logging import configure_json_logging
from procurement.ports.erp import (
    CandidatePage,
    ErpPort,
    ErpUnavailableError,
    ProcurementEvidenceQuery,
    ProcurementPreferenceQuery,
    ReplenishmentCandidateRecord,
    ReplenishmentCandidatesQuery,
)

_SUPPORTED_MODES = frozenset({"success", "timeout", "odoo"})


@dataclass(frozen=True, slots=True)
class LocalMcpSettings:
    """Validated dependencies for deterministic or real-Odoo MCP operation."""

    environment: Environment
    bearer_token: str = field(repr=False)
    erp_mode: str = "success"
    erp_url: str | None = None
    log_level: int = logging.INFO
    read_timeout_seconds: float = 10.0
    max_retries: int = 2
    retry_delay_seconds: float = 0.05
    odoo_url: str | None = None
    odoo_database: str | None = None
    odoo_api_key: str | None = field(default=None, repr=False)
    odoo_company_id: int | None = None

    def __post_init__(self) -> None:
        if self.erp_mode not in _SUPPORTED_MODES:
            raise ValueError(
                "PROCUREMENT_LOCAL_ERP_MODE must be success, timeout, or odoo"
            )
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
        if self.erp_mode == "odoo":
            if (
                self.odoo_url is None
                or self.odoo_database is None
                or self.odoo_api_key is None
                or self.odoo_company_id is None
            ):
                raise ValueError("real Odoo mode requires all Odoo settings")
            parsed_odoo_url = httpx.URL(self.odoo_url)
            if (
                parsed_odoo_url.scheme not in {"http", "https"}
                or parsed_odoo_url.host is None
            ):
                raise ValueError("PROCUREMENT_ODOO_URL must be an absolute HTTP URL")
            if (
                not 1 <= len(self.odoo_database) <= 128
                or not self.odoo_database.isascii()
                or any(character.isspace() for character in self.odoo_database)
            ):
                raise ValueError("PROCUREMENT_ODOO_DATABASE is invalid")
            if (
                not 32 <= len(self.odoo_api_key) <= 512
                or not self.odoo_api_key.isascii()
                or any(character.isspace() for character in self.odoo_api_key)
            ):
                raise ValueError("PROCUREMENT_ODOO_API_KEY is invalid")
            if type(self.odoo_company_id) is not int or self.odoo_company_id <= 0:
                raise ValueError("PROCUREMENT_ODOO_COMPANY_ID must be positive")

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
        raw_odoo_company_id = os.environ.get("PROCUREMENT_ODOO_COMPANY_ID")
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
            odoo_url=os.environ.get("PROCUREMENT_ODOO_URL"),
            odoo_database=os.environ.get("PROCUREMENT_ODOO_DATABASE"),
            odoo_api_key=os.environ.get("PROCUREMENT_ODOO_API_KEY"),
            odoo_company_id=(
                int(raw_odoo_company_id) if raw_odoo_company_id is not None else None
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

    async def get_procurement_evidence(
        self, query: ProcurementEvidenceQuery
    ) -> ProcurementEvidence:
        if self.mode == "timeout":
            await asyncio.sleep(3_600)
        return _fictional_evidence(query)

    async def get_procurement_preferences(
        self, query: ProcurementPreferenceQuery
    ) -> ProcurementPreference:
        if self.mode == "timeout":
            await asyncio.sleep(3_600)
        return _fictional_preference(query)


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

    async def get_procurement_evidence(
        self, query: ProcurementEvidenceQuery
    ) -> ProcurementEvidence:
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout_seconds,
            ) as client:
                response = await client.post(
                    "/test/procurement-evidence",
                    json={
                        "environment": query.environment.value,
                        "product_id": query.product_id,
                        "horizon_days": query.horizon_days,
                    },
                )
                response.raise_for_status()
                return procurement_evidence_from_dict(response.json())
        except httpx.TimeoutException:
            raise TimeoutError from None
        except (httpx.HTTPError, InvalidOperation, TypeError, ValueError) as error:
            raise ErpUnavailableError from error

    async def get_procurement_preferences(
        self, query: ProcurementPreferenceQuery
    ) -> ProcurementPreference:
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout_seconds,
            ) as client:
                response = await client.post(
                    "/test/procurement-preferences",
                    json={
                        "environment": query.environment.value,
                        "company_id": query.company_id,
                        "category_id": query.category_id,
                        "product_id": query.product_id,
                    },
                )
                response.raise_for_status()
                return preference_from_dict(response.json())
        except httpx.TimeoutException:
            raise TimeoutError from None
        except (httpx.HTTPError, InvalidOperation, TypeError, ValueError) as error:
            raise ErpUnavailableError from error


def _fictional_preference(query: ProcurementPreferenceQuery) -> ProcurementPreference:
    return ProcurementPreference(
        profile_id="preference-local-company",
        company_id=query.company_id,
        category_id=query.category_id,
        product_id=query.product_id,
        scope=PreferenceScope.COMPANY,
        scope_id=query.company_id,
        revision=1,
        ordered_criteria=(
            PreferenceCriterion.RELIABILITY,
            PreferenceCriterion.DELIVERY,
            PreferenceCriterion.PRICE,
        ),
        max_price_premium_percent=Decimal("25.000000"),
        enforcement_mode=PremiumEnforcement.ADVISORY,
        precedence_source=PreferenceScope.COMPANY,
    )


def _fictional_evidence(query: ProcurementEvidenceQuery) -> ProcurementEvidence:
    """Build deterministic local evidence through the real policy functions."""

    as_of = date.today()
    shortage, timeline = project_shortage(
        as_of=as_of,
        on_hand=Decimal("8"),
        reserved=Decimal("0"),
        movements=(StockMovement(as_of + timedelta(days=3), Decimal("-8")),),
        reorder_minimum=Decimal("10"),
        reorder_maximum=Decimal("40"),
    )
    coverage = apply_coverage(shortage=shortage, timeline=timeline, sources=())
    performance = performance_evidence(
        orders=(
            CompletedOrder(as_of - timedelta(days=30), as_of - timedelta(days=30)),
            CompletedOrder(as_of - timedelta(days=20), as_of - timedelta(days=19)),
        ),
        as_of=as_of,
    )
    offer = evaluate_offer(
        offer=VendorOffer(
            offer_id="offer-101",
            vendor_id="vendor-101",
            vendor_name="Fictional Approved Supplies",
            approved=True,
            blocked=False,
            valid_from=None,
            valid_until=None,
            currency="USD",
            company_currency="USD",
            unit_price=Decimal("12.50"),
            exchange_rate=Decimal("1"),
            lead_time_days=2,
            minimum_quantity=Decimal("1"),
            package_multiple=Decimal("5"),
        ),
        order_date=as_of,
        shortage=shortage,
        timeline=timeline,
        performance=performance,
    )
    budget = calculate_budget(
        period_start=as_of.replace(day=1),
        currency="USD",
        budget_amount=Decimal("5000"),
        confirmed_commitment=Decimal("160"),
        proposed_amount=offer.normalized_cost,
    )
    skip_reason = (
        "FULLY_COVERED"
        if coverage.status == "full"
        else None
        if offer.status.value == "eligible"
        else "NO_VALID_OFFER"
    )
    return ProcurementEvidence(
        environment=query.environment,
        evidence_id=f"{query.environment.value}:evidence-{query.product_id}",
        product_id=query.product_id,
        product_name="Fictional Safety Gloves",
        category_id="category-safety",
        captured_at=datetime.now(tz=UTC),
        shortage=shortage,
        coverage=coverage,
        offers=(offer,),
        budget=budget,
        skip_reason_code=skip_reason,
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
    metrics = create_mcp_metrics()
    if resolved.erp_mode == "odoo":
        if (
            resolved.odoo_url is None
            or resolved.odoo_database is None
            or resolved.odoo_api_key is None
            or resolved.odoo_company_id is None
        ):  # pragma: no cover - settings validation guarantees this
            raise RuntimeError("real Odoo settings are incomplete")
        erp: ErpPort = OdooErpAdapter(
            client=OdooJson2Client(
                base_url=resolved.odoo_url,
                database=resolved.odoo_database,
                api_key=resolved.odoo_api_key,
                timeout_seconds=resolved.read_timeout_seconds,
                max_retries=resolved.max_retries,
                retry_delay_seconds=resolved.retry_delay_seconds,
                metrics=create_odoo_metrics(metrics.registry),
                logger=logger,
            ),
            company_id=resolved.odoo_company_id,
        )
        tool_max_retries = 0
    elif resolved.erp_url is not None:
        erp = LocalHttpFictionalErp(
            base_url=resolved.erp_url,
            timeout_seconds=resolved.read_timeout_seconds,
        )
        tool_max_retries = resolved.max_retries
    else:
        erp = LocalFictionalErp(mode=resolved.erp_mode)
        tool_max_retries = resolved.max_retries
    server = create_mcp_server(
        erp=erp,
        environment=resolved.environment,
        bearer_token=resolved.bearer_token,
        host="0.0.0.0",  # noqa: S104 - accept the private container-network host
        logger=logger,
        metrics=metrics,
        read_timeout_seconds=resolved.read_timeout_seconds,
        max_retries=tool_max_retries,
        retry_delay_seconds=resolved.retry_delay_seconds,
    )
    return server.streamable_http_app()


class _LazyLocalMcpApp:
    """Defer environment loading until an ASGI server starts the app."""

    def __init__(self) -> None:
        self._resolved_app: ASGIApp | None = None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if self._resolved_app is None:
            self._resolved_app = create_local_mcp_app()
        await self._resolved_app(scope, receive, send)


app: ASGIApp = _LazyLocalMcpApp()


def run() -> None:
    """Run only the configured MCP composition root."""

    uvicorn.run(
        create_local_mcp_app(),
        host="0.0.0.0",  # noqa: S104 - required inside the container boundary
        port=9000,
        log_level="warning",
        access_log=False,
        server_header=False,
    )
