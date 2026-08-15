"""Deterministic 365-day vendor reliability evidence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from procurement.domain.policy.evidence import VendorPerformanceEvidence


@dataclass(frozen=True, slots=True)
class CompletedOrder:
    """One completed order's promised and actual receipt dates."""

    scheduled_receipt_date: date
    completed_receipt_date: date


def performance_evidence(
    *,
    orders: tuple[CompletedOrder, ...],
    as_of: date,
) -> VendorPerformanceEvidence:
    """Calculate the approved order count, on-time rate, and history status."""

    if type(as_of) is not date or len(orders) > 100_000:
        raise ValueError("performance inputs are invalid")
    start = as_of - timedelta(days=365)
    included = tuple(
        order for order in orders if start < order.completed_receipt_date <= as_of
    )
    count = len(included)
    on_time = sum(
        order.completed_receipt_date <= order.scheduled_receipt_date
        for order in included
    )
    rate = (
        (Decimal(on_time) / Decimal(count)).quantize(
            Decimal("0.000001"), rounding=ROUND_HALF_UP
        )
        if count
        else None
    )
    return VendorPerformanceEvidence(
        completed_order_count=count,
        on_time_rate=rate,
        history_status="limited" if count < 3 else "sufficient",
    )
