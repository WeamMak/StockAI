"""Typed boundary for authoritative deterministic procurement evidence."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, fields
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Any, cast

from procurement.domain.identifiers import Environment

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$", re.ASCII)
_REASON_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$", re.ASCII)
_CURRENCY = re.compile(r"^[A-Z]{3}$", re.ASCII)
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_QUANTUM = Decimal("0.000001")
_MAX_NUMBER = Decimal("999999999999.999999")
_MAX_SERIALIZED_BYTES = 65_536


class EvidenceStatus(StrEnum):
    """Whether one offer may proceed to later reasoning."""

    ELIGIBLE = "eligible"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class ProjectedDay:
    """One exact end-of-day point in the authoritative shortage timeline."""

    projection_date: date
    quantity: Decimal

    def __post_init__(self) -> None:
        _date(self.projection_date, field="projection_date")
        _decimal(self.quantity, field="quantity", allow_negative=True)


def _decimal(value: object, *, field: str, allow_negative: bool = False) -> None:
    minimum = -_MAX_NUMBER if allow_negative else Decimal("0")
    if (
        not isinstance(value, Decimal)
        or not value.is_finite()
        or not minimum <= value <= _MAX_NUMBER
        or value.quantize(_QUANTUM) != value
    ):
        raise ValueError(f"{field} must be an exact bounded Decimal")


def _identifier(value: object, *, field: str) -> None:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise ValueError(f"{field} must be a bounded identifier")


def _normal_text(value: object, *, field: str, maximum: int = 200) -> None:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > maximum
        or _CONTROL.search(value) is not None
    ):
        raise ValueError(f"{field} must be bounded normal text")


def _currency(value: object) -> None:
    if not isinstance(value, str) or _CURRENCY.fullmatch(value) is None:
        raise ValueError("currency must be three uppercase letters")


def _date(value: object, *, field: str) -> None:
    if type(value) is not date:
        raise ValueError(f"{field} must be a date")


@dataclass(frozen=True, slots=True)
class ShortageEvidence:
    """The bounded 14-day shortage timeline calculated from known movements."""

    horizon_start: date
    horizon_end: date
    reorder_trigger_date: date | None
    need_by_date: date
    reorder_minimum: Decimal
    reorder_maximum: Decimal
    minimum_projected_quantity: Decimal
    timeline: tuple[ProjectedDay, ...]

    def __post_init__(self) -> None:
        _date(self.horizon_start, field="horizon_start")
        _date(self.horizon_end, field="horizon_end")
        _date(self.need_by_date, field="need_by_date")
        if self.reorder_trigger_date is not None:
            _date(self.reorder_trigger_date, field="reorder_trigger_date")
        if self.horizon_end != self.horizon_start + timedelta(days=14):
            raise ValueError("shortage horizon must contain exactly 14 days")
        if not self.horizon_start <= self.need_by_date <= self.horizon_end:
            raise ValueError("need_by_date must be inside the shortage horizon")
        if self.reorder_trigger_date is not None and not (
            self.horizon_start <= self.reorder_trigger_date <= self.horizon_end
        ):
            raise ValueError("reorder_trigger_date must be inside the horizon")
        _decimal(self.reorder_minimum, field="reorder_minimum")
        _decimal(self.reorder_maximum, field="reorder_maximum")
        _decimal(
            self.minimum_projected_quantity,
            field="minimum_projected_quantity",
            allow_negative=True,
        )
        if self.reorder_maximum < self.reorder_minimum:
            raise ValueError("reorder maximum must include the minimum")
        if (
            not isinstance(self.timeline, tuple)
            or len(self.timeline) != 15
            or any(
                not isinstance(item, ProjectedDay)
                or item.projection_date != self.horizon_start + timedelta(days=index)
                for index, item in enumerate(self.timeline)
            )
        ):
            raise ValueError("shortage timeline must contain each day in the horizon")
        if self.minimum_projected_quantity != min(
            item.quantity for item in self.timeline
        ):
            raise ValueError("minimum projected quantity must match the timeline")


@dataclass(frozen=True, slots=True)
class CoverageEvidence:
    """Existing pending-case and open-PO coverage of one shortage."""

    status: str
    covered_quantity: Decimal
    residual_quantity: Decimal
    source_count: int

    def __post_init__(self) -> None:
        if self.status not in {"none", "partial", "full"}:
            raise ValueError("coverage status is invalid")
        _decimal(self.covered_quantity, field="covered_quantity")
        _decimal(self.residual_quantity, field="residual_quantity")
        if type(self.source_count) is not int or not 0 <= self.source_count <= 100:
            raise ValueError("coverage source_count is invalid")


@dataclass(frozen=True, slots=True)
class VendorPerformanceEvidence:
    """Bounded 365-day completed-order reliability evidence."""

    completed_order_count: int
    on_time_rate: Decimal | None
    history_status: str

    def __post_init__(self) -> None:
        if (
            type(self.completed_order_count) is not int
            or not 0 <= self.completed_order_count <= 100_000
        ):
            raise ValueError("completed_order_count is invalid")
        if self.on_time_rate is not None:
            _decimal(self.on_time_rate, field="on_time_rate")
            if self.on_time_rate > Decimal("1"):
                raise ValueError("on_time_rate must not exceed one")
        if self.history_status not in {"limited", "sufficient"}:
            raise ValueError("history_status is invalid")
        expected = "limited" if self.completed_order_count < 3 else "sufficient"
        if self.history_status != expected:
            raise ValueError("history_status does not match the evidence count")
        if (self.completed_order_count == 0) != (self.on_time_rate is None):
            raise ValueError("on_time_rate requires completed order evidence")


@dataclass(frozen=True, slots=True)
class OfferEvidence:
    """One deterministic current vendor-offer decision and calculation."""

    offer_id: str
    vendor_id: str
    vendor_name: str
    status: EvidenceStatus
    reason_codes: tuple[str, ...]
    currency: str
    unit_price: Decimal
    company_currency: str
    normalized_unit_price: Decimal
    delivery_date: date
    quantity: Decimal
    normalized_cost: Decimal
    projected_inventory_after_receipt: Decimal
    excess_inventory: Decimal
    performance: VendorPerformanceEvidence

    def __post_init__(self) -> None:
        _identifier(self.offer_id, field="offer_id")
        _identifier(self.vendor_id, field="vendor_id")
        _normal_text(self.vendor_name, field="vendor_name")
        if not isinstance(self.status, EvidenceStatus):
            raise ValueError("offer status is invalid")
        if (
            not isinstance(self.reason_codes, tuple)
            or len(self.reason_codes) > 16
            or any(_REASON_CODE.fullmatch(code) is None for code in self.reason_codes)
        ):
            raise ValueError("offer reason codes are invalid")
        if self.status is EvidenceStatus.ELIGIBLE and self.reason_codes:
            raise ValueError("eligible offers cannot have rejection reasons")
        if self.status is EvidenceStatus.REJECTED and not self.reason_codes:
            raise ValueError("rejected offers require a reason")
        _currency(self.currency)
        _currency(self.company_currency)
        for field in (
            "unit_price",
            "normalized_unit_price",
            "quantity",
            "normalized_cost",
            "excess_inventory",
        ):
            _decimal(getattr(self, field), field=field)
        _decimal(
            self.projected_inventory_after_receipt,
            field="projected_inventory_after_receipt",
            allow_negative=True,
        )
        _date(self.delivery_date, field="delivery_date")
        if not isinstance(self.performance, VendorPerformanceEvidence):
            raise ValueError("performance evidence is invalid")


@dataclass(frozen=True, slots=True)
class BudgetEvidence:
    """Authoritative calendar-month category budget calculation."""

    period_start: date
    currency: str
    budget_amount: Decimal
    confirmed_commitment: Decimal
    proposed_amount: Decimal
    remaining_before: Decimal
    remaining_after: Decimal
    overage: Decimal
    exception_required: bool

    def __post_init__(self) -> None:
        _date(self.period_start, field="period_start")
        if self.period_start.day != 1:
            raise ValueError("budget period must start on the first day")
        _currency(self.currency)
        for field in (
            "budget_amount",
            "confirmed_commitment",
            "proposed_amount",
            "overage",
        ):
            _decimal(getattr(self, field), field=field)
        _decimal(self.remaining_before, field="remaining_before", allow_negative=True)
        _decimal(self.remaining_after, field="remaining_after", allow_negative=True)
        if type(self.exception_required) is not bool:
            raise ValueError("exception_required must be boolean")


@dataclass(frozen=True, slots=True)
class ProcurementEvidence:
    """One immutable authoritative evidence record created before LLM reasoning."""

    environment: Environment
    evidence_id: str
    product_id: str
    product_name: str
    category_id: str
    captured_at: datetime
    shortage: ShortageEvidence
    coverage: CoverageEvidence
    offers: tuple[OfferEvidence, ...]
    budget: BudgetEvidence | None
    skip_reason_code: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.environment, Environment):
            raise ValueError("environment is invalid")
        _identifier(self.evidence_id, field="evidence_id")
        if not self.evidence_id.startswith(f"{self.environment.value}:"):
            raise ValueError("evidence_id must match its environment")
        _identifier(self.product_id, field="product_id")
        _normal_text(self.product_name, field="product_name")
        _identifier(self.category_id, field="category_id")
        if (
            not isinstance(self.captured_at, datetime)
            or self.captured_at.tzinfo is None
            or self.captured_at.utcoffset() != timedelta(0)
        ):
            raise ValueError("captured_at must be timezone-aware UTC")
        if not isinstance(self.shortage, ShortageEvidence):
            raise ValueError("shortage evidence is invalid")
        if not isinstance(self.coverage, CoverageEvidence):
            raise ValueError("coverage evidence is invalid")
        if (
            not isinstance(self.offers, tuple)
            or len(self.offers) > 50
            or not all(isinstance(offer, OfferEvidence) for offer in self.offers)
        ):
            raise ValueError("offers must contain at most 50 typed offers")
        if self.budget is not None and not isinstance(self.budget, BudgetEvidence):
            raise ValueError("budget evidence is invalid")
        if self.skip_reason_code is not None and (
            _REASON_CODE.fullmatch(self.skip_reason_code) is None
        ):
            raise ValueError("skip_reason_code is invalid")
        if len(self.canonical_json()) > _MAX_SERIALIZED_BYTES:
            raise ValueError("procurement evidence exceeds the serialization limit")

    def to_dict(self) -> dict[str, Any]:
        """Return the strict JSON-compatible public representation."""

        return cast(dict[str, Any], _json_value(asdict(self)))

    def canonical_json(self) -> bytes:
        """Return stable bounded bytes suitable for hashing and persistence."""

        return json.dumps(
            self.to_dict(),
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")


def _json_value(value: Any) -> Any:
    if isinstance(value, Environment):
        return value.value
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def procurement_evidence_from_dict(raw: object) -> ProcurementEvidence:
    """Strictly reconstruct authoritative evidence from an untrusted mapping."""

    if not isinstance(raw, dict) or set(raw) != {
        "environment",
        "evidence_id",
        "product_id",
        "product_name",
        "category_id",
        "captured_at",
        "shortage",
        "coverage",
        "offers",
        "budget",
        "skip_reason_code",
    }:
        raise ValueError("procurement evidence payload is invalid")
    shortage_raw = _mapping(raw["shortage"], ShortageEvidence)
    timeline_raw = shortage_raw.pop("timeline")
    if not isinstance(timeline_raw, list):
        raise ValueError("shortage timeline is invalid")
    timeline = tuple(
        ProjectedDay(
            projection_date=date.fromisoformat(
                str(_mapping(item, ProjectedDay)["projection_date"])
            ),
            quantity=Decimal(str(_mapping(item, ProjectedDay)["quantity"])),
        )
        for item in timeline_raw
    )
    coverage_raw = _mapping(raw["coverage"], CoverageEvidence)
    offers_raw = raw["offers"]
    if not isinstance(offers_raw, list):
        raise ValueError("procurement evidence offers are invalid")
    offers: list[OfferEvidence] = []
    for item in offers_raw:
        offer_raw = _mapping(item, OfferEvidence)
        performance_raw = _mapping(
            offer_raw.pop("performance"), VendorPerformanceEvidence
        )
        offers.append(
            OfferEvidence(
                offer_id=str(offer_raw["offer_id"]),
                vendor_id=str(offer_raw["vendor_id"]),
                vendor_name=str(offer_raw["vendor_name"]),
                status=EvidenceStatus(str(offer_raw["status"])),
                reason_codes=tuple(offer_raw["reason_codes"]),
                currency=str(offer_raw["currency"]),
                unit_price=Decimal(str(offer_raw["unit_price"])),
                company_currency=str(offer_raw["company_currency"]),
                normalized_unit_price=Decimal(str(offer_raw["normalized_unit_price"])),
                delivery_date=date.fromisoformat(str(offer_raw["delivery_date"])),
                quantity=Decimal(str(offer_raw["quantity"])),
                normalized_cost=Decimal(str(offer_raw["normalized_cost"])),
                projected_inventory_after_receipt=Decimal(
                    str(offer_raw["projected_inventory_after_receipt"])
                ),
                excess_inventory=Decimal(str(offer_raw["excess_inventory"])),
                performance=VendorPerformanceEvidence(
                    completed_order_count=int(performance_raw["completed_order_count"]),
                    on_time_rate=(
                        Decimal(str(performance_raw["on_time_rate"]))
                        if performance_raw["on_time_rate"] is not None
                        else None
                    ),
                    history_status=str(performance_raw["history_status"]),
                ),
            )
        )
    budget_raw = raw["budget"]
    budget = None
    if budget_raw is not None:
        values = _mapping(budget_raw, BudgetEvidence)
        exception_required = values.pop("exception_required")
        if type(exception_required) is not bool:
            raise ValueError("budget exception_required is invalid")
        budget = BudgetEvidence(
            period_start=date.fromisoformat(str(values.pop("period_start"))),
            currency=str(values.pop("currency")),
            exception_required=exception_required,
            **{key: Decimal(str(value)) for key, value in values.items()},
        )
    return ProcurementEvidence(
        environment=Environment(str(raw["environment"])),
        evidence_id=str(raw["evidence_id"]),
        product_id=str(raw["product_id"]),
        product_name=str(raw["product_name"]),
        category_id=str(raw["category_id"]),
        captured_at=datetime.fromisoformat(str(raw["captured_at"])),
        shortage=ShortageEvidence(
            horizon_start=date.fromisoformat(str(shortage_raw["horizon_start"])),
            horizon_end=date.fromisoformat(str(shortage_raw["horizon_end"])),
            reorder_trigger_date=(
                date.fromisoformat(str(shortage_raw["reorder_trigger_date"]))
                if shortage_raw["reorder_trigger_date"] is not None
                else None
            ),
            need_by_date=date.fromisoformat(str(shortage_raw["need_by_date"])),
            reorder_minimum=Decimal(str(shortage_raw["reorder_minimum"])),
            reorder_maximum=Decimal(str(shortage_raw["reorder_maximum"])),
            minimum_projected_quantity=Decimal(
                str(shortage_raw["minimum_projected_quantity"])
            ),
            timeline=timeline,
        ),
        coverage=CoverageEvidence(
            status=str(coverage_raw["status"]),
            covered_quantity=Decimal(str(coverage_raw["covered_quantity"])),
            residual_quantity=Decimal(str(coverage_raw["residual_quantity"])),
            source_count=int(coverage_raw["source_count"]),
        ),
        offers=tuple(offers),
        budget=budget,
        skip_reason_code=(
            str(raw["skip_reason_code"])
            if raw["skip_reason_code"] is not None
            else None
        ),
    )


def _mapping(raw: object, model: type[object]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("nested procurement evidence payload is invalid")
    expected = {field.name for field in fields(cast(Any, model))}
    if set(raw) != expected:
        raise ValueError("nested procurement evidence fields are invalid")
    return dict(raw)
