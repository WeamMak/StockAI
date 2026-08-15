"""Known-movement inventory projection for the approved 14-day horizon."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from procurement.domain.policy.evidence import ProjectedDay, ShortageEvidence

_QUANTUM = Decimal("0.000001")


def _quantity(value: Decimal) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError("forecast quantities must be finite Decimals")
    return value.quantize(_QUANTUM)


@dataclass(frozen=True, slots=True)
class StockMovement:
    """One confirmed dated inventory change; outgoing values are negative."""

    movement_date: date
    quantity_change: Decimal

    def __post_init__(self) -> None:
        if type(self.movement_date) is not date:
            raise ValueError("movement_date must be a date")
        _quantity(self.quantity_change)


def project_shortage(
    *,
    as_of: date,
    on_hand: Decimal,
    reserved: Decimal,
    movements: tuple[StockMovement, ...],
    reorder_minimum: Decimal,
    reorder_maximum: Decimal,
) -> tuple[ShortageEvidence, tuple[ProjectedDay, ...]]:
    """Project known inventory and identify trigger and stockout dates."""

    if type(as_of) is not date:
        raise ValueError("as_of must be a date")
    available = _quantity(on_hand) - _quantity(reserved)
    minimum = _quantity(reorder_minimum)
    maximum = _quantity(reorder_maximum)
    if reserved < 0 or minimum < 0 or maximum < minimum:
        raise ValueError("forecast configuration is invalid")
    horizon_end = as_of + timedelta(days=14)
    changes: dict[date, Decimal] = {}
    for movement in movements:
        if as_of <= movement.movement_date <= horizon_end:
            changes[movement.movement_date] = (
                changes.get(movement.movement_date, Decimal("0"))
                + movement.quantity_change
            )

    timeline: list[ProjectedDay] = []
    trigger: date | None = None
    stockout: date | None = None
    for offset in range(15):
        day = as_of + timedelta(days=offset)
        available = _quantity(available + changes.get(day, Decimal("0")))
        timeline.append(ProjectedDay(day, available))
        if trigger is None and available < minimum:
            trigger = day
        if stockout is None and available <= 0:
            stockout = day

    evidence = ShortageEvidence(
        horizon_start=as_of,
        horizon_end=horizon_end,
        reorder_trigger_date=trigger,
        need_by_date=stockout or horizon_end,
        reorder_minimum=minimum,
        reorder_maximum=maximum,
        minimum_projected_quantity=min(day.quantity for day in timeline),
        timeline=tuple(timeline),
    )
    return evidence, tuple(timeline)
