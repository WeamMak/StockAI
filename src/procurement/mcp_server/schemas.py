"""Strict MCP input and output schemas for procurement tools."""

from __future__ import annotations

import re
from datetime import date
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
