"""Narrow asynchronous client for the approved Odoo 19 JSON-2 reads."""

from __future__ import annotations

import asyncio
import json
import logging
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date
from time import perf_counter
from typing import Any

import httpx
from prometheus_client import CollectorRegistry, Counter, Histogram

from procurement.adapters.odoo.mappers import (
    OdooMappingError,
    candidate_product_ids,
    map_candidate_page,
    parse_candidate_cursor,
)
from procurement.observability.logging import log_event
from procurement.ports.erp import (
    CandidatePage,
    ErpPort,
    ErpUnavailableError,
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
        if operation in {"search_candidate_orderpoints", "read_candidate_products"}
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
