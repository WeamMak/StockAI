"""Narrow asynchronous client for the approved Odoo 19 JSON-2 reads."""

from __future__ import annotations

import asyncio
import json
import logging
import random
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from time import perf_counter
from typing import Any

import httpx
from prometheus_client import CollectorRegistry, Counter, Histogram

from procurement.adapters.odoo.draft import (
    DRAFT_SNAPSHOT_FIELDS,
    OdooDraftMappingError,
    many2one,
    purchase_order_draft_from_row,
)
from procurement.adapters.odoo.evidence import build_odoo_evidence
from procurement.adapters.odoo.mappers import (
    OdooMappingError,
    candidate_product_ids,
    map_candidate_page,
    parse_candidate_cursor,
)
from procurement.adapters.odoo.preference_mapper import map_effective_preference
from procurement.domain.identifiers import Environment
from procurement.domain.policy.evidence import ProcurementEvidence
from procurement.domain.policy.preferences import ProcurementPreference
from procurement.observability.logging import log_event
from procurement.ports.erp import (
    CandidatePage,
    DraftWriteAmbiguousError,
    ErpPort,
    ErpUnavailableError,
    ProcurementEvidenceQuery,
    ProcurementPreferenceQuery,
    PurchaseOrderDraft,
    PurchaseOrderDraftCommand,
    ReplenishmentCandidatesQuery,
)

_ORDERPOINT_FIELDS = [
    "id",
    "active",
    "trigger",
    "product_id",
    "product_min_qty",
    "product_max_qty",
    "company_id",
    "qty_forecast",
    "qty_to_order",
    "write_date",
]
_PRODUCT_FIELDS = ["id", "name", "categ_id", "active", "is_storable", "purchase_ok"]
_TRANSIENT_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504})
_DEFAULT_MAX_RESPONSE_BYTES = 1024 * 1024
_EVIDENCE_OPERATIONS = frozenset(
    {
        "read_evidence_product",
        "read_evidence_orderpoint",
        "read_evidence_offers",
        "read_evidence_partners",
        "read_evidence_orders",
        "read_evidence_order_lines",
        "read_evidence_budget",
        "read_evidence_moves",
        "read_evidence_locations",
        "read_evidence_company",
        "read_evidence_currencies",
        "read_evidence_uoms",
        "read_preferences",
        "read_preference_priorities",
    }
)
_DRAFT_OPERATIONS = frozenset(
    {
        "search_purchase_order_by_origin",
        "search_currency_by_code",
        "search_replenishment_uom",
        "read_purchase_order",
        "create_purchase_order",
    }
)

Sleep = Callable[[float], Awaitable[None]]
Jitter = Callable[[float, float], float]
Today = Callable[[], date]


class OdooReadTimeoutError(TimeoutError):
    """Safe signal that an Odoo read exhausted its bounded attempts."""

    safe_message = "The procurement source timed out."

    def __init__(self, *, retry_count: int, private_detail: object = None) -> None:
        del private_detail
        if type(retry_count) is not int or not 0 <= retry_count <= 2:
            raise ValueError("retry_count must be between zero and two")
        super().__init__(self.safe_message)
        self.retry_count = retry_count


@dataclass(frozen=True, slots=True)
class OdooMetrics:
    """Low-cardinality metrics for the two approved Odoo read operations."""

    registry: CollectorRegistry
    calls: Counter
    retries: Counter
    timeouts: Counter
    duration: Histogram

    def observe_call(
        self,
        *,
        operation: str,
        status: str,
        duration_seconds: float,
    ) -> None:
        safe_operation = _safe_operation(operation)
        safe_status = status if status in {"success", "error", "timeout"} else "error"
        self.calls.labels(operation=safe_operation, status=safe_status).inc()
        self.duration.labels(operation=safe_operation).observe(duration_seconds)

    def record_retry(self, *, operation: str) -> None:
        self.retries.labels(operation=_safe_operation(operation)).inc()

    def record_timeout(self, *, operation: str) -> None:
        self.timeouts.labels(operation=_safe_operation(operation)).inc()


def create_odoo_metrics(registry: CollectorRegistry | None = None) -> OdooMetrics:
    """Create adapter collectors, optionally in the MCP process registry."""

    resolved_registry = registry or CollectorRegistry(auto_describe=True)
    return OdooMetrics(
        registry=resolved_registry,
        calls=Counter(
            "procurement_odoo_calls",
            "Completed Odoo JSON-2 calls.",
            ("operation", "status"),
            registry=resolved_registry,
        ),
        retries=Counter(
            "procurement_odoo_retries",
            "Retried Odoo JSON-2 read attempts.",
            ("operation",),
            registry=resolved_registry,
        ),
        timeouts=Counter(
            "procurement_odoo_timeouts",
            "Timed-out Odoo JSON-2 read attempts.",
            ("operation",),
            registry=resolved_registry,
        ),
        duration=Histogram(
            "procurement_odoo_call_duration_seconds",
            "Odoo JSON-2 call duration in seconds.",
            ("operation",),
            registry=resolved_registry,
        ),
    )


def _safe_operation(operation: str) -> str:
    return (
        operation
        if operation
        in {
            "search_candidate_orderpoints",
            "read_candidate_products",
            *_EVIDENCE_OPERATIONS,
            *_DRAFT_OPERATIONS,
        }
        else "unknown"
    )


@dataclass(frozen=True, slots=True)
class OdooJson2Client:
    """Expose only the two JSON-2 reads needed for candidate discovery."""

    base_url: str
    database: str
    api_key: str = field(repr=False)
    timeout_seconds: float = 10.0
    max_retries: int = 2
    retry_delay_seconds: float = 0.05
    max_response_bytes: int = _DEFAULT_MAX_RESPONSE_BYTES
    sleep: Sleep = field(default=asyncio.sleep, repr=False)
    jitter: Jitter = field(default=random.uniform, repr=False)
    transport: httpx.AsyncBaseTransport | None = field(default=None, repr=False)
    metrics: OdooMetrics | None = field(default=None, repr=False)
    logger: logging.Logger = field(
        default_factory=lambda: logging.getLogger(__name__),
        repr=False,
    )

    def __post_init__(self) -> None:
        parsed_url = httpx.URL(self.base_url)
        if parsed_url.scheme not in {"http", "https"} or parsed_url.host is None:
            raise ValueError("Odoo base URL must be an absolute HTTP URL")
        if not self.database or not self.api_key:
            raise ValueError("Odoo database and API key are required")
        if not 0 < self.timeout_seconds <= 120:
            raise ValueError("Odoo timeout must be between 0 and 120 seconds")
        if not 0 <= self.max_retries <= 2:
            raise ValueError("Odoo maximum retries must be between zero and two")
        if not 0 <= self.retry_delay_seconds <= 10:
            raise ValueError("Odoo retry delay must be between 0 and 10 seconds")
        if not 1 <= self.max_response_bytes <= 4 * 1024 * 1024:
            raise ValueError("Odoo response limit must be between 1 byte and 4 MiB")

    async def search_candidate_orderpoints(
        self,
        *,
        company_id: int,
        after_id: int,
        limit: int,
    ) -> Any:
        """Read one ordered page of company-bound reordering rules."""

        if (
            type(company_id) is not int
            or company_id <= 0
            or type(after_id) is not int
            or after_id < 0
            or type(limit) is not int
            or not 1 <= limit <= 101
        ):
            raise ValueError("Odoo candidate query is invalid")

        return await self._post(
            operation="search_candidate_orderpoints",
            model="stock.warehouse.orderpoint",
            method="search_read",
            payload={
                "domain": [
                    ["active", "=", True],
                    ["company_id", "=", company_id],
                    ["product_id.active", "=", True],
                    ["product_id.is_storable", "=", True],
                    ["product_id.purchase_ok", "=", True],
                    ["id", ">", after_id],
                ],
                "fields": _ORDERPOINT_FIELDS,
                "limit": limit,
                "order": "id asc",
            },
        )

    async def read_candidate_products(self, *, product_ids: tuple[int, ...]) -> Any:
        """Read only the product fields required by candidate mapping."""

        if (
            not isinstance(product_ids, tuple)
            or not 1 <= len(product_ids) <= 101
            or len(set(product_ids)) != len(product_ids)
            or any(
                type(product_id) is not int or product_id <= 0
                for product_id in product_ids
            )
        ):
            raise ValueError("Odoo product IDs are invalid")

        return await self._post(
            operation="read_candidate_products",
            model="product.product",
            method="read",
            payload={"ids": list(product_ids), "fields": _PRODUCT_FIELDS},
        )

    async def search_read_evidence(
        self,
        *,
        operation: str,
        model: str,
        domain: list[list[object]],
        fields: list[str],
        limit: int = 1000,
        order: str = "id asc",
    ) -> Any:
        """Perform one fixed, bounded evidence read selected by the adapter."""

        if operation not in _EVIDENCE_OPERATIONS:
            raise ValueError("Odoo evidence operation is not allowed")
        if not model or not fields or not 1 <= limit <= 1000:
            raise ValueError("Odoo evidence query is invalid")
        return await self._post(
            operation=operation,
            model=model,
            method="search_read",
            payload={
                "domain": domain,
                "fields": fields,
                "limit": limit,
                "order": order,
            },
        )

    async def search_purchase_order_by_origin(self, *, origin: str) -> Any:
        """Find at most one existing draft bound to this stable case origin."""

        if not origin or len(origin) > 128:
            raise ValueError("Odoo purchase-order origin is invalid")
        return await self._post(
            operation="search_purchase_order_by_origin",
            model="purchase.order",
            method="search_read",
            payload={
                "domain": [["origin", "=", origin]],
                "fields": list(DRAFT_SNAPSHOT_FIELDS),
                "limit": 2,
                "order": "id asc",
            },
        )

    async def search_currency_by_code(self, *, code: str) -> Any:
        """Resolve one ISO currency code to its Odoo `res.currency` ID."""

        if not code:
            raise ValueError("Odoo currency code is invalid")
        return await self._post(
            operation="search_currency_by_code",
            model="res.currency",
            method="search_read",
            payload={
                "domain": [["name", "=", code]],
                "fields": ["id", "name"],
                "limit": 2,
                "order": "id asc",
            },
        )

    async def search_replenishment_uom(
        self, *, product_id: int, company_id: int
    ) -> Any:
        """Resolve the same replenishment UoM the evidence quantity used."""

        if product_id <= 0 or company_id <= 0:
            raise ValueError("Odoo replenishment UoM query is invalid")
        return await self._post(
            operation="search_replenishment_uom",
            model="stock.warehouse.orderpoint",
            method="search_read",
            payload={
                "domain": [
                    ["product_id", "=", product_id],
                    ["company_id", "=", company_id],
                ],
                "fields": ["id", "replenishment_uom_id"],
                "limit": 2,
                "order": "id asc",
            },
        )

    async def read_purchase_order(self, *, po_id: int) -> Any:
        """Read one purchase order's stable optimistic-concurrency snapshot."""

        if type(po_id) is not int or po_id <= 0:
            raise ValueError("Odoo purchase-order ID is invalid")
        return await self._post(
            operation="read_purchase_order",
            model="purchase.order",
            method="read",
            payload={"ids": [po_id], "fields": list(DRAFT_SNAPSHOT_FIELDS)},
        )

    async def create_purchase_order_once(self, *, vals: Mapping[str, object]) -> Any:
        """Attempt exactly one non-retried `purchase.order` creation.

        A write is never safe to blindly retry, so any failure here --
        transient status, timeout, or a malformed response -- raises
        `DraftWriteAmbiguousError` instead of the normal safely-retryable
        errors: the caller must search for the order before deciding what to
        do next, never resend this same create.
        """

        started_at = perf_counter()
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url.rstrip("/"),
                headers={
                    "Authorization": f"bearer {self.api_key}",
                    "X-Odoo-Database": self.database,
                },
                timeout=self.timeout_seconds,
                transport=self.transport,
            ) as client:
                async with client.stream(
                    "POST",
                    "/json/2/purchase.order/create",
                    json={"vals_list": [dict(vals)]},
                ) as response:
                    if not 200 <= response.status_code < 300:
                        raise DraftWriteAmbiguousError(retry_count=0)
                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(content) + len(chunk) > self.max_response_bytes:
                            raise DraftWriteAmbiguousError(retry_count=0)
                        content.extend(chunk)
            try:
                result = json.loads(content)
            except (UnicodeDecodeError, ValueError) as error:
                raise DraftWriteAmbiguousError(error, retry_count=0) from None
        except DraftWriteAmbiguousError:
            self._record_completion(
                operation="create_purchase_order",
                status="error",
                started_at=started_at,
                retry_count=0,
            )
            raise
        except (httpx.TimeoutException, httpx.RequestError) as error:
            self._record_completion(
                operation="create_purchase_order",
                status="timeout"
                if isinstance(error, httpx.TimeoutException)
                else "error",
                started_at=started_at,
                retry_count=0,
            )
            raise DraftWriteAmbiguousError(error, retry_count=0) from None
        self._record_completion(
            operation="create_purchase_order",
            status="success",
            started_at=started_at,
            retry_count=0,
        )
        return result

    async def _post(
        self,
        *,
        operation: str,
        model: str,
        method: str,
        payload: dict[str, object],
    ) -> Any:
        started_at = perf_counter()
        try:
            result, retry_count = await self._post_with_retries(
                operation=operation,
                model=model,
                method=method,
                payload=payload,
            )
        except OdooReadTimeoutError as error:
            self._record_completion(
                operation=operation,
                status="timeout",
                started_at=started_at,
                retry_count=error.retry_count,
            )
            raise
        except ErpUnavailableError as error:
            self._record_completion(
                operation=operation,
                status="error",
                started_at=started_at,
                retry_count=error.retry_count,
            )
            raise
        self._record_completion(
            operation=operation,
            status="success",
            started_at=started_at,
            retry_count=retry_count,
        )
        return result

    async def _post_with_retries(
        self,
        *,
        operation: str,
        model: str,
        method: str,
        payload: dict[str, object],
    ) -> tuple[Any, int]:
        async with httpx.AsyncClient(
            base_url=self.base_url.rstrip("/"),
            headers={
                "Authorization": f"bearer {self.api_key}",
                "X-Odoo-Database": self.database,
            },
            timeout=self.timeout_seconds,
            transport=self.transport,
        ) as client:
            for retry_count in range(self.max_retries + 1):
                try:
                    async with client.stream(
                        "POST",
                        f"/json/2/{model}/{method}",
                        json=payload,
                    ) as response:
                        if not 200 <= response.status_code < 300:
                            if response.status_code in _TRANSIENT_STATUS_CODES:
                                if retry_count < self.max_retries:
                                    await self._sleep_before_retry(
                                        operation,
                                        retry_count,
                                    )
                                    continue
                            raise ErpUnavailableError(retry_count=retry_count)

                        content = bytearray()
                        async for chunk in response.aiter_bytes():
                            if len(content) + len(chunk) > self.max_response_bytes:
                                raise ErpUnavailableError(retry_count=retry_count)
                            content.extend(chunk)
                except httpx.TimeoutException as error:
                    if self.metrics is not None:
                        self.metrics.record_timeout(operation=operation)
                    if retry_count < self.max_retries:
                        await self._sleep_before_retry(operation, retry_count)
                        continue
                    raise OdooReadTimeoutError(
                        retry_count=retry_count,
                        private_detail=error,
                    ) from None
                except httpx.RequestError as error:
                    if retry_count < self.max_retries:
                        await self._sleep_before_retry(operation, retry_count)
                        continue
                    raise ErpUnavailableError(
                        error,
                        retry_count=retry_count,
                    ) from None

                try:
                    return json.loads(content), retry_count
                except (UnicodeDecodeError, ValueError) as error:
                    raise ErpUnavailableError(
                        error,
                        retry_count=retry_count,
                    ) from None

        raise RuntimeError("unreachable Odoo retry state")

    async def _sleep_before_retry(self, operation: str, retry_count: int) -> None:
        if self.metrics is not None:
            self.metrics.record_retry(operation=operation)
        ceiling = self.retry_delay_seconds * (2**retry_count)
        await self.sleep(self.jitter(0, ceiling))

    def _record_completion(
        self,
        *,
        operation: str,
        status: str,
        started_at: float,
        retry_count: int,
    ) -> None:
        duration_seconds = perf_counter() - started_at
        if self.metrics is not None:
            self.metrics.observe_call(
                operation=operation,
                status=status,
                duration_seconds=duration_seconds,
            )
        log_event(
            self.logger,
            "odoo_call_completed",
            level=logging.ERROR if status != "success" else logging.INFO,
            operation=_safe_operation(operation),
            duration_ms=round(duration_seconds * 1000, 3),
            status=status,
            retry_count=retry_count,
        )


@dataclass(frozen=True, slots=True)
class OdooErpAdapter(ErpPort):
    """Implement the ERP port with only the approved candidate JSON-2 reads."""

    client: OdooJson2Client
    company_id: int
    today: Today = field(default=date.today, repr=False)

    def __post_init__(self) -> None:
        if type(self.company_id) is not int or self.company_id <= 0:
            raise ValueError("Odoo company ID must be a positive integer")

    async def list_replenishment_candidates(
        self,
        query: ReplenishmentCandidatesQuery,
    ) -> CandidatePage:
        """Read and strictly map one company-bound candidate page."""

        try:
            after_id = parse_candidate_cursor(query.cursor)
            raw_orderpoints = await self.client.search_candidate_orderpoints(
                company_id=self.company_id,
                after_id=after_id,
                limit=query.limit + 1,
            )
            product_ids = candidate_product_ids(raw_orderpoints)
            raw_products: object = (
                await self.client.read_candidate_products(product_ids=product_ids)
                if product_ids
                else []
            )
            return map_candidate_page(
                orderpoints=raw_orderpoints,
                products=raw_products,
                expected_company_id=self.company_id,
                requested_limit=query.limit,
                trigger_date=self.today(),
            )
        except (OdooMappingError, OdooReadTimeoutError, ErpUnavailableError):
            raise
        except (AttributeError, TypeError, ValueError) as error:
            raise OdooMappingError(error) from None

    async def get_procurement_evidence(
        self, query: ProcurementEvidenceQuery
    ) -> ProcurementEvidence:
        """Read fixed Odoo source facts and apply deterministic policy."""

        try:
            if query.horizon_days != 14 or not query.product_id.isascii():
                raise ValueError("invalid evidence query")
            product_id = int(query.product_id)
            captured_at = datetime.now(tz=UTC)
            horizon_end = captured_at + timedelta(days=14)
            history_start = captured_at - timedelta(days=365)
            product = await self.client.search_read_evidence(
                operation="read_evidence_product",
                model="product.product",
                domain=[["id", "=", product_id]],
                fields=[
                    "id",
                    "name",
                    "categ_id",
                    "product_tmpl_id",
                    "qty_available",
                ],
                limit=2,
            )
            product_row = _single_mapping(product)
            template_id = _relationship_id(product_row["product_tmpl_id"])
            orderpoint = await self.client.search_read_evidence(
                operation="read_evidence_orderpoint",
                model="stock.warehouse.orderpoint",
                domain=[
                    ["product_id", "=", product_id],
                    ["company_id", "=", self.company_id],
                    ["active", "=", True],
                ],
                fields=[
                    "id",
                    "product_min_qty",
                    "product_max_qty",
                    "replenishment_uom_id",
                    "company_id",
                ],
                limit=2,
            )
            offers = await self.client.search_read_evidence(
                operation="read_evidence_offers",
                model="product.supplierinfo",
                domain=[
                    ["product_tmpl_id", "=", template_id],
                    ["company_id", "in", [False, self.company_id]],
                ],
                fields=[
                    "id",
                    "partner_id",
                    "product_tmpl_id",
                    "product_uom_id",
                    "currency_id",
                    "price",
                    "delay",
                    "min_qty",
                    "date_start",
                    "date_end",
                ],
            )
            partner_ids = _relationship_ids(offers, "partner_id")
            partners = await self.client.search_read_evidence(
                operation="read_evidence_partners",
                model="res.partner",
                domain=[["id", "in", list(partner_ids)]],
                fields=["id", "name", "category_id"],
            )
            tag_ids = _flat_integer_ids(partners, "category_id")
            tags = await self.client.search_read_evidence(
                operation="read_evidence_partners",
                model="res.partner.category",
                domain=[["id", "in", list(tag_ids)]],
                fields=["id", "name"],
            )
            order_lines = await self.client.search_read_evidence(
                operation="read_evidence_order_lines",
                model="purchase.order.line",
                domain=[
                    ["product_id", "=", product_id],
                    ["company_id", "=", self.company_id],
                    ["order_id.state", "in", ["draft", "sent", "purchase", "done"]],
                    [
                        "order_id.date_order",
                        ">=",
                        history_start.strftime("%Y-%m-%d %H:%M:%S"),
                    ],
                ],
                fields=[
                    "id",
                    "order_id",
                    "product_qty",
                    "qty_received",
                    "date_planned",
                    "price_subtotal",
                    "currency_id",
                    "analytic_distribution",
                ],
            )
            order_ids = _relationship_ids(order_lines, "order_id")
            orders = await self.client.search_read_evidence(
                operation="read_evidence_orders",
                model="purchase.order",
                domain=[["id", "in", list(order_ids)]],
                fields=[
                    "id",
                    "state",
                    "partner_id",
                    "date_order",
                    "effective_date",
                    "currency_id",
                    "company_id",
                ],
            )
            budgets = await self.client.search_read_evidence(
                operation="read_evidence_budget",
                model="stockai.procurement.budget",
                domain=[
                    ["company_id", "=", self.company_id],
                    [
                        "product_category_id",
                        "=",
                        _relationship_id(product_row["categ_id"]),
                    ],
                    [
                        "period_start",
                        "=",
                        captured_at.date().replace(day=1).isoformat(),
                    ],
                    ["active", "=", True],
                ],
                fields=[
                    "id",
                    "product_category_id",
                    "analytic_account_id",
                    "period_start",
                    "currency_id",
                    "amount",
                    "company_id",
                ],
                limit=2,
            )
            company = await self.client.search_read_evidence(
                operation="read_evidence_company",
                model="res.company",
                domain=[["id", "=", self.company_id]],
                fields=["id", "currency_id"],
                limit=2,
            )
            company_row = _single_mapping(company)
            currency_ids = set(_relationship_ids(offers, "currency_id"))
            currency_ids.update(_relationship_ids(budgets, "currency_id"))
            currency_ids.add(_relationship_id(company_row["currency_id"]))
            currencies = await self.client.search_read_evidence(
                operation="read_evidence_currencies",
                model="res.currency",
                domain=[["id", "in", sorted(currency_ids)]],
                fields=["id", "name", "rate"],
            )
            uom_ids = set(_relationship_ids(offers, "product_uom_id"))
            uom_ids.add(
                _relationship_id(_single_mapping(orderpoint)["replenishment_uom_id"])
            )
            uoms = await self.client.search_read_evidence(
                operation="read_evidence_uoms",
                model="uom.uom",
                domain=[["id", "in", sorted(uom_ids)]],
                fields=["id", "factor"],
            )
            moves = await self.client.search_read_evidence(
                operation="read_evidence_moves",
                model="stock.move",
                domain=[
                    ["product_id", "=", product_id],
                    ["company_id", "=", self.company_id],
                    ["state", "in", ["waiting", "confirmed", "assigned"]],
                    ["date", ">=", captured_at.strftime("%Y-%m-%d %H:%M:%S")],
                    ["date", "<=", horizon_end.strftime("%Y-%m-%d %H:%M:%S")],
                ],
                fields=[
                    "id",
                    "date",
                    "product_uom_qty",
                    "location_id",
                    "location_dest_id",
                    "purchase_line_id",
                ],
            )
            location_ids = set(_relationship_ids(moves, "location_id")) | set(
                _relationship_ids(moves, "location_dest_id")
            )
            locations = await self.client.search_read_evidence(
                operation="read_evidence_locations",
                model="stock.location",
                domain=[["id", "in", sorted(location_ids)]],
                fields=["id", "usage"],
            )
            return build_odoo_evidence(
                environment=query.environment,
                company_id=self.company_id,
                product_id=product_id,
                captured_at=captured_at,
                product=product,
                orderpoint=orderpoint,
                offers=offers,
                partners=partners,
                tags=tags,
                orders=orders,
                order_lines=order_lines,
                budgets=budgets,
                moves=moves,
                locations=locations,
                company=company,
                currencies=currencies,
                uoms=uoms,
            )
        except (OdooMappingError, OdooReadTimeoutError, ErpUnavailableError):
            raise
        except (AttributeError, TypeError, ValueError) as error:
            raise OdooMappingError(error) from None

    async def get_procurement_preferences(
        self,
        query: ProcurementPreferenceQuery,
    ) -> ProcurementPreference:
        """Resolve and strictly map one current company-bound profile."""

        try:
            if query.environment not in {Environment.DEV, Environment.PROD}:
                raise ValueError("invalid preference environment")
            company_id = int(query.company_id)
            category_id = int(query.category_id)
            product_id = int(query.product_id)
            if company_id != self.company_id or min(category_id, product_id) <= 0:
                raise ValueError("invalid preference query")
            profiles = await self.client.search_read_evidence(
                operation="read_preferences",
                model="stockai.procurement.preference",
                domain=[
                    ["company_id", "=", self.company_id],
                    ["active", "=", True],
                ],
                fields=[
                    "id",
                    "company_id",
                    "scope",
                    "product_category_id",
                    "product_id",
                    "revision",
                    "max_price_premium_percent",
                    "enforcement_mode",
                    "active",
                    "priority_ids",
                ],
                limit=100,
            )
            priority_ids = _flat_integer_ids(profiles, "priority_ids")
            priorities = await self.client.search_read_evidence(
                operation="read_preference_priorities",
                model="stockai.procurement.preference.priority",
                domain=[["id", "in", list(priority_ids)]],
                fields=["id", "preference_id", "sequence", "criterion"],
                limit=300,
            )
            return map_effective_preference(
                profiles=profiles,
                priorities=priorities,
                company_id=company_id,
                category_id=category_id,
                product_id=product_id,
            )
        except (OdooMappingError, OdooReadTimeoutError, ErpUnavailableError):
            raise
        except (AttributeError, TypeError, ValueError) as error:
            raise OdooMappingError(error) from None

    async def find_purchase_order_draft(
        self, *, origin: str
    ) -> PurchaseOrderDraft | None:
        """Return the existing draft bound to this origin, if any."""

        try:
            rows = await self.client.search_purchase_order_by_origin(origin=origin)
            if not isinstance(rows, list):
                raise ValueError("invalid purchase-order search response")
            if not rows:
                return None
            if len(rows) > 1:
                raise ValueError("more than one purchase order shares this origin")
            row = rows[0]
            if not isinstance(row, Mapping):
                raise ValueError("invalid purchase-order row")
            return purchase_order_draft_from_row(row)
        except (OdooDraftMappingError, OdooReadTimeoutError, ErpUnavailableError):
            raise
        except (AttributeError, TypeError, ValueError) as error:
            raise OdooDraftMappingError(error) from None

    async def create_purchase_order_draft(
        self, command: PurchaseOrderDraftCommand
    ) -> PurchaseOrderDraft:
        """Resolve Odoo identifiers, then attempt one non-retried creation.

        Everything before the actual `create` call is a safe, retryable read
        -- its failures propagate with their normal retryable semantics.
        Everything from the `create` call onward is treated as ambiguous on
        any failure, including a follow-up confirmation-read failure after a
        create that may have already committed, so the caller always
        resolves through a fresh search rather than risking a duplicate.
        """

        try:
            vendor_id = int(command.vendor_id)
            product_id = int(command.product_id)
            if vendor_id <= 0 or product_id <= 0:
                raise ValueError("invalid draft vendor or product identifier")

            currency_rows = await self.client.search_currency_by_code(
                code=command.currency_code
            )
            if not isinstance(currency_rows, list) or not currency_rows:
                raise ValueError("unknown Odoo currency code")
            currency_row = currency_rows[0]
            if not isinstance(currency_row, Mapping):
                raise ValueError("invalid currency row")
            currency_id = currency_row["id"]
            if type(currency_id) is not int or currency_id <= 0:
                raise ValueError("invalid Odoo currency id")

            uom_rows = await self.client.search_replenishment_uom(
                product_id=product_id, company_id=self.company_id
            )
            if not isinstance(uom_rows, list) or not uom_rows:
                raise ValueError("no replenishment rule for this product")
            uom_row = uom_rows[0]
            if not isinstance(uom_row, Mapping):
                raise ValueError("invalid replenishment-UoM row")
            uom_id, _uom_name = many2one(uom_row["replenishment_uom_id"])
        except (OdooReadTimeoutError, ErpUnavailableError):
            raise
        except (AttributeError, TypeError, ValueError) as error:
            raise OdooMappingError(error) from None

        vals: dict[str, object] = {
            "partner_id": vendor_id,
            "origin": command.origin,
            "currency_id": currency_id,
            "company_id": self.company_id,
            "order_line": [
                (
                    0,
                    0,
                    {
                        "name": command.product_name,
                        "product_id": product_id,
                        "product_qty": float(command.quantity),
                        "product_uom_id": uom_id,
                        "date_planned": datetime.combine(
                            command.need_by_date, datetime.min.time()
                        ).strftime("%Y-%m-%d %H:%M:%S"),
                        "price_unit": float(command.unit_price),
                    },
                )
            ],
        }
        created = await self.client.create_purchase_order_once(vals=vals)
        if (
            not isinstance(created, list)
            or len(created) != 1
            or type(created[0]) is not int
        ):
            raise DraftWriteAmbiguousError()
        po_id = created[0]
        try:
            rows = await self.client.read_purchase_order(po_id=po_id)
            row = _single_mapping(rows)
            return purchase_order_draft_from_row(row)
        except Exception as error:
            raise DraftWriteAmbiguousError(error) from None


def _single_mapping(raw: object) -> Mapping[str, object]:
    if not isinstance(raw, list) or len(raw) != 1 or not isinstance(raw[0], Mapping):
        raise ValueError("expected one Odoo record")
    return raw[0]


def _relationship_id(raw: object) -> int:
    if not isinstance(raw, list) or len(raw) != 2 or type(raw[0]) is not int:
        raise ValueError("invalid Odoo relationship")
    return raw[0]


def _relationship_ids(raw: object, field_name: str) -> tuple[int, ...]:
    if not isinstance(raw, list):
        raise ValueError("invalid Odoo records")
    return tuple(
        dict.fromkeys(
            _relationship_id(row[field_name]) for row in raw if isinstance(row, Mapping)
        )
    )


def _flat_integer_ids(raw: object, field_name: str) -> tuple[int, ...]:
    if not isinstance(raw, list):
        raise ValueError("invalid Odoo records")
    values: list[int] = []
    for row in raw:
        if not isinstance(row, Mapping) or not isinstance(row[field_name], list):
            raise ValueError("invalid Odoo relationship list")
        for value in row[field_name]:
            if type(value) is not int:
                raise ValueError("invalid Odoo relationship identifier")
            if value not in values:
                values.append(value)
    return tuple(values)
