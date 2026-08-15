"""Duplicate and open-purchase-order coverage policy."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from procurement.domain.policy.evidence import (
    CoverageEvidence,
    ProjectedDay,
    ShortageEvidence,
)

_QUANTUM = Decimal("0.000001")


@dataclass(frozen=True, slots=True)
class CoverageSource:
    """One pending case or draft/confirmed incoming purchase order."""

    source_id: str
    expected_date: date
    quantity: Decimal

    def __post_init__(self) -> None:
        if not self.source_id or len(self.source_id) > 128:
            raise ValueError("coverage source_id is invalid")
        if type(self.expected_date) is not date:
            raise ValueError("coverage expected_date is invalid")
        if (
            not isinstance(self.quantity, Decimal)
            or not self.quantity.is_finite()
            or self.quantity <= 0
        ):
            raise ValueError("coverage quantity must be positive")


def apply_coverage(
    *,
    shortage: ShortageEvidence,
    timeline: tuple[ProjectedDay, ...],
    sources: tuple[CoverageSource, ...],
) -> CoverageEvidence:
    """Apply bounded existing coverage and return only the residual need."""

    if not timeline or len(sources) > 100:
        raise ValueError("coverage inputs are invalid")
    bounded = tuple(
        source
        for source in sources
        if shortage.horizon_start <= source.expected_date <= shortage.horizon_end
    )
    covered = sum((source.quantity for source in bounded), Decimal("0"))
    covered_timeline = timeline_with_coverage(timeline=timeline, sources=bounded)
    projected = [day.quantity for day in covered_timeline]
    residual = max(
        Decimal("0"),
        shortage.reorder_maximum - min(projected),
    ).quantize(_QUANTUM)
    covered = covered.quantize(_QUANTUM)
    if shortage.reorder_trigger_date is None or all(
        quantity >= shortage.reorder_minimum for quantity in projected
    ):
        status = "full"
        residual = Decimal("0.000000")
    elif covered > 0:
        status = "partial"
    else:
        status = "none"
    return CoverageEvidence(
        status=status,
        covered_quantity=covered,
        residual_quantity=residual,
        source_count=len(bounded),
    )


def timeline_with_coverage(
    *,
    timeline: tuple[ProjectedDay, ...],
    sources: tuple[CoverageSource, ...],
) -> tuple[ProjectedDay, ...]:
    """Add existing dated coverage once to a known-movement projection."""

    if not timeline or len(sources) > 100:
        raise ValueError("coverage timeline inputs are invalid")
    bounded = tuple(
        source
        for source in sources
        if timeline[0].projection_date
        <= source.expected_date
        <= timeline[-1].projection_date
    )
    return tuple(
        ProjectedDay(
            projection_date=day.projection_date,
            quantity=(
                day.quantity
                + sum(
                    (
                        source.quantity
                        for source in bounded
                        if source.expected_date <= day.projection_date
                    ),
                    Decimal("0"),
                )
            ).quantize(_QUANTUM),
        )
        for day in timeline
    )
