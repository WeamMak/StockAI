"""Deterministic HTTP fake for Task 09 local-stack tests.

This is an internal test contract, not a claim about the unverified Odoo 19
JSON-2 API. Task 10 owns verification of the real Odoo contract.
"""

from __future__ import annotations

import asyncio
import os
from datetime import date
from decimal import Decimal
from typing import Literal, cast

from fastapi import FastAPI, Response
from pydantic import BaseModel, ConfigDict, Field

from procurement.bootstrap.mcp import _fictional_evidence
from procurement.domain.identifiers import Environment
from procurement.ports.erp import ProcurementEvidenceQuery

Scenario = Literal["success", "no_valid_response", "malformed", "timeout"]
_SUPPORTED_SCENARIOS = frozenset(
    {"success", "no_valid_response", "malformed", "timeout"}
)
_STRICT_CONFIG = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)


class HealthResponse(BaseModel):
    """Bounded liveness response."""

    model_config = _STRICT_CONFIG

    status: Literal["live"]


class CandidateQuery(BaseModel):
    """Narrow query accepted from the local MCP adapter."""

    model_config = _STRICT_CONFIG

    horizon_days: int = Field(strict=True, ge=1, le=90)
    limit: int = Field(strict=True, ge=1, le=100)
    cursor: str | None = Field(default=None, max_length=256)


class CandidateRecord(BaseModel):
    """One fictional candidate returned to the local MCP adapter."""

    model_config = _STRICT_CONFIG

    product_id: str
    product_name: str
    category_id: str
    reorder_minimum: Decimal
    reorder_maximum: Decimal
    projected_quantity: Decimal
    projected_trigger_date: date
    skip_reason_code: str | None


class CandidatePage(BaseModel):
    """One deterministic page returned by the fake service."""

    model_config = _STRICT_CONFIG

    items: tuple[CandidateRecord, ...]
    next_cursor: str | None


class EvidenceQuery(BaseModel):
    model_config = _STRICT_CONFIG

    environment: Literal["dev", "prod"]
    product_id: str = Field(min_length=1, max_length=128)
    horizon_days: Literal[14]


def _scenario() -> Scenario:
    value = os.environ.get("PROCUREMENT_FAKE_ODOO_SCENARIO", "success")
    if value not in _SUPPORTED_SCENARIOS:
        raise ValueError("PROCUREMENT_FAKE_ODOO_SCENARIO is invalid")
    return cast(Scenario, value)


def _timeout_seconds() -> float:
    value = float(os.environ.get("PROCUREMENT_FAKE_ODOO_TIMEOUT_SECONDS", "5"))
    if not 0 < value <= 120:
        raise ValueError(
            "PROCUREMENT_FAKE_ODOO_TIMEOUT_SECONDS must be between 0 and 120"
        )
    return value


app = FastAPI(title="StockAI deterministic fake Odoo")


@app.get("/health/live")
def live() -> HealthResponse:
    """Report only fake-process liveness."""

    return HealthResponse(status="live")


@app.post(
    "/test/replenishment-candidates",
    response_model=CandidatePage,
)
async def list_replenishment_candidates(
    query: CandidateQuery,
) -> CandidatePage | Response:
    """Return the configured deterministic read scenario."""

    scenario = _scenario()
    if scenario == "timeout":
        await asyncio.sleep(_timeout_seconds())
    if scenario == "malformed":
        return Response(content='{"items":', media_type="application/json")
    if scenario == "no_valid_response":
        return CandidatePage(items=(), next_cursor=None)
    return CandidatePage(
        items=(
            CandidateRecord(
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


@app.post("/test/procurement-evidence", response_model=None)
async def get_procurement_evidence(
    query: EvidenceQuery,
) -> dict[str, object] | Response:
    """Return deterministic policy evidence for local-stack tests."""

    scenario = _scenario()
    if scenario == "timeout":
        await asyncio.sleep(_timeout_seconds())
    if scenario == "malformed":
        return Response(content='{"environment":', media_type="application/json")
    evidence = _fictional_evidence(
        ProcurementEvidenceQuery(
            environment=Environment(query.environment),
            product_id=query.product_id,
            horizon_days=query.horizon_days,
        )
    )
    if scenario == "no_valid_response":
        payload = evidence.to_dict()
        payload["offers"] = []
        payload["skip_reason_code"] = "NO_VALID_OFFER"
        return payload
    return evidence.to_dict()
