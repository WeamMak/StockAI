"""Strict MCP input and output schemas for procurement tools."""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

EnvironmentValue = Literal["dev", "prod"]
HorizonDays = Annotated[int, Field(strict=True, ge=1, le=90)]
CandidateLimit = Annotated[int, Field(strict=True, ge=1, le=100)]
CandidateCursor = Annotated[
    str,
    Field(strict=True, min_length=1, max_length=256, pattern=r"^[A-Za-z0-9._:-]+$"),
]

_IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]*$"
_SKIP_CODE_PATTERN = r"^[A-Z][A-Z0-9_]*$"
_CONTROL_CHARACTER_PATTERN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_STRICT_MODEL_CONFIG = ConfigDict(
    extra="forbid",
    strict=True,
    hide_input_in_errors=True,
)


class ListReplenishmentCandidatesInput(BaseModel):
    """Arguments for bounded candidate discovery in one environment."""

    model_config = _STRICT_MODEL_CONFIG

    environment: EnvironmentValue
    horizon_days: HorizonDays
    limit: CandidateLimit = 25
    cursor: CandidateCursor | None = None


class CandidateSkipMetadata(BaseModel):
    """Stable reason metadata when later policy marks a candidate to skip."""

    model_config = _STRICT_MODEL_CONFIG

    reason_code: str = Field(
        min_length=1,
        max_length=64,
        pattern=_SKIP_CODE_PATTERN,
    )


class ReplenishmentCandidate(BaseModel):
    """Validated candidate fields exposed to MCP clients."""

    model_config = _STRICT_MODEL_CONFIG

    product_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=_IDENTIFIER_PATTERN,
    )
    product_name: str = Field(min_length=1, max_length=200)
    category_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=_IDENTIFIER_PATTERN,
    )
    reorder_minimum: Decimal = Field(
        ge=Decimal("0"),
        le=Decimal("999999999.999999"),
        max_digits=15,
        decimal_places=6,
        allow_inf_nan=False,
    )
    reorder_maximum: Decimal = Field(
        ge=Decimal("0"),
        le=Decimal("999999999.999999"),
        max_digits=15,
        decimal_places=6,
        allow_inf_nan=False,
    )
    projected_quantity: Decimal = Field(
        ge=Decimal("-999999999.999999"),
        le=Decimal("999999999.999999"),
        max_digits=15,
        decimal_places=6,
        allow_inf_nan=False,
    )
    projected_trigger_date: date
    skip_metadata: CandidateSkipMetadata | None

    @field_validator("product_name")
    @classmethod
    def validate_product_name(cls, value: str) -> str:
        """Reject blank names and unsafe control characters without rewriting data."""

        if not value.strip() or _CONTROL_CHARACTER_PATTERN.search(value) is not None:
            raise ValueError("product_name must be bounded normal text")
        return value

    @model_validator(mode="after")
    def validate_reorder_range(self) -> ReplenishmentCandidate:
        """Require the configured reorder maximum to include the minimum."""

        if self.reorder_maximum < self.reorder_minimum:
            raise ValueError("reorder_maximum must be at least reorder_minimum")
        return self


class ListReplenishmentCandidatesOutput(BaseModel):
    """Bounded, environment-bound page returned by candidate discovery."""

    model_config = _STRICT_MODEL_CONFIG

    environment: EnvironmentValue
    candidates: tuple[ReplenishmentCandidate, ...] = Field(max_length=100)
    next_cursor: CandidateCursor | None


class GetProcurementEvidenceInput(BaseModel):
    """Arguments for one environment-bound product evidence read."""

    model_config = _STRICT_MODEL_CONFIG

    environment: EnvironmentValue
    product_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=_IDENTIFIER_PATTERN,
    )
    horizon_days: Literal[14] = 14


class ProjectedDayOutput(BaseModel):
    model_config = _STRICT_MODEL_CONFIG

    projection_date: date
    quantity: Decimal


class ShortageEvidenceOutput(BaseModel):
    model_config = _STRICT_MODEL_CONFIG

    horizon_start: date
    horizon_end: date
    reorder_trigger_date: date | None
    need_by_date: date
    reorder_minimum: Decimal
    reorder_maximum: Decimal
    minimum_projected_quantity: Decimal
    timeline: tuple[ProjectedDayOutput, ...] = Field(min_length=15, max_length=15)


class CoverageEvidenceOutput(BaseModel):
    model_config = _STRICT_MODEL_CONFIG

    status: Literal["none", "partial", "full"]
    covered_quantity: Decimal
    residual_quantity: Decimal
    source_count: int = Field(strict=True, ge=0, le=100)


class PerformanceEvidenceOutput(BaseModel):
    model_config = _STRICT_MODEL_CONFIG

    completed_order_count: int = Field(strict=True, ge=0, le=100_000)
    on_time_rate: Decimal | None
    history_status: Literal["limited", "sufficient"]


class OfferEvidenceOutput(BaseModel):
    model_config = _STRICT_MODEL_CONFIG

    offer_id: str = Field(min_length=1, max_length=128, pattern=_IDENTIFIER_PATTERN)
    vendor_id: str = Field(min_length=1, max_length=128, pattern=_IDENTIFIER_PATTERN)
    vendor_name: str = Field(min_length=1, max_length=200)
    status: Literal["eligible", "rejected"]
    reason_codes: tuple[str, ...] = Field(max_length=16)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    unit_price: Decimal
    company_currency: str = Field(pattern=r"^[A-Z]{3}$")
    normalized_unit_price: Decimal
    delivery_date: date
    quantity: Decimal
    normalized_cost: Decimal
    projected_inventory_after_receipt: Decimal
    excess_inventory: Decimal
    performance: PerformanceEvidenceOutput


class BudgetEvidenceOutput(BaseModel):
    model_config = _STRICT_MODEL_CONFIG

    period_start: date
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    budget_amount: Decimal
    confirmed_commitment: Decimal
    proposed_amount: Decimal
    remaining_before: Decimal
    remaining_after: Decimal
    overage: Decimal
    exception_required: bool


class ProcurementEvidenceOutput(BaseModel):
    """Strict authoritative evidence exposed over MCP transport."""

    model_config = _STRICT_MODEL_CONFIG

    environment: EnvironmentValue
    evidence_id: str = Field(min_length=1, max_length=128, pattern=_IDENTIFIER_PATTERN)
    product_id: str = Field(min_length=1, max_length=128, pattern=_IDENTIFIER_PATTERN)
    product_name: str = Field(min_length=1, max_length=200)
    category_id: str = Field(min_length=1, max_length=128, pattern=_IDENTIFIER_PATTERN)
    captured_at: datetime
    shortage: ShortageEvidenceOutput
    coverage: CoverageEvidenceOutput
    offers: tuple[OfferEvidenceOutput, ...] = Field(max_length=50)
    budget: BudgetEvidenceOutput | None
    skip_reason_code: str | None = Field(
        default=None, max_length=64, pattern=_SKIP_CODE_PATTERN
    )
    preferences: dict[str, object] | None = None


class CreateDraftInput(BaseModel):
    """Arguments for one idempotent draft purchase-order creation."""

    model_config = _STRICT_MODEL_CONFIG

    environment: EnvironmentValue
    origin: str = Field(min_length=1, max_length=128, pattern=_IDENTIFIER_PATTERN)
    vendor_id: str = Field(min_length=1, max_length=128, pattern=_IDENTIFIER_PATTERN)
    currency_code: str = Field(pattern=r"^[A-Z]{3}$")
    product_id: str = Field(min_length=1, max_length=128, pattern=_IDENTIFIER_PATTERN)
    product_name: str = Field(min_length=1, max_length=200)
    # Decimal/date fields cannot be satisfied by wire JSON under strict mode
    # (only an actual Decimal/date instance passes, never a str/int/float) --
    # so quantity, unit_price, and need_by_date cross the MCP boundary as
    # bounded strings and are parsed to their real types by the tool.
    quantity: str = Field(pattern=r"^\d{1,15}(\.\d{1,6})?$")
    unit_price: str = Field(pattern=r"^\d{1,15}(\.\d{1,6})?$")
    need_by_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")

    @field_validator("product_name")
    @classmethod
    def validate_product_name(cls, value: str) -> str:
        """Reject blank names and unsafe control characters without rewriting data."""

        if not value.strip() or _CONTROL_CHARACTER_PATTERN.search(value) is not None:
            raise ValueError("product_name must be bounded normal text")
        return value


class PurchaseOrderDraftOutput(BaseModel):
    """Odoo purchase-order identity and optimistic-concurrency snapshot."""

    model_config = _STRICT_MODEL_CONFIG

    po_id: int = Field(strict=True, gt=0)
    write_date: str = Field(min_length=1, max_length=32)
    state: str = Field(min_length=1, max_length=32)
    partner_id: int = Field(strict=True, gt=0)
    currency_id: int = Field(strict=True, gt=0)
    amount_total: Decimal = Field(
        ge=Decimal("0"),
        le=Decimal("999999999.999999"),
        max_digits=15,
        decimal_places=6,
        allow_inf_nan=False,
    )


class GetProcurementPreferencesInput(BaseModel):
    """Identifiers required to resolve one environment-bound profile."""

    model_config = _STRICT_MODEL_CONFIG

    environment: EnvironmentValue
    company_id: str = Field(min_length=1, max_length=128, pattern=_IDENTIFIER_PATTERN)
    category_id: str = Field(min_length=1, max_length=128, pattern=_IDENTIFIER_PATTERN)
    product_id: str = Field(min_length=1, max_length=128, pattern=_IDENTIFIER_PATTERN)


class ProcurementPreferenceOutput(BaseModel):
    """Strict typed preference profile returned over MCP."""

    model_config = _STRICT_MODEL_CONFIG

    profile_id: str = Field(min_length=1, max_length=128, pattern=_IDENTIFIER_PATTERN)
    company_id: str = Field(min_length=1, max_length=128, pattern=_IDENTIFIER_PATTERN)
    category_id: str = Field(min_length=1, max_length=128, pattern=_IDENTIFIER_PATTERN)
    product_id: str = Field(min_length=1, max_length=128, pattern=_IDENTIFIER_PATTERN)
    scope: Literal["company", "category", "product"]
    scope_id: str = Field(min_length=1, max_length=128, pattern=_IDENTIFIER_PATTERN)
    revision: int = Field(strict=True, ge=1, le=2_147_483_647)
    ordered_criteria: tuple[Literal["price", "delivery", "reliability"], ...] = Field(
        min_length=3, max_length=3
    )
    max_price_premium_percent: Decimal = Field(
        ge=Decimal("0"), le=Decimal("100"), decimal_places=6, allow_inf_nan=False
    )
    enforcement_mode: Literal["advisory", "hard"]
    precedence_source: Literal["company", "category", "product"]
