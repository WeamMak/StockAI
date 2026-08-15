"""Vendor-offer eligibility and authoritative quantity calculations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_CEILING, Decimal

from procurement.domain.policy.evidence import (
    EvidenceStatus,
    OfferEvidence,
    ProjectedDay,
    ShortageEvidence,
    VendorPerformanceEvidence,
)

_QUANTUM = Decimal("0.000001")


@dataclass(frozen=True, slots=True)
class VendorOffer:
    """Validated source facts needed to evaluate one current offer."""

    offer_id: str
    vendor_id: str
    vendor_name: str
    approved: bool
    blocked: bool
    valid_from: date | None
    valid_until: date | None
    currency: str
    company_currency: str
    unit_price: Decimal
    exchange_rate: Decimal
    lead_time_days: int
    minimum_quantity: Decimal
    package_multiple: Decimal


def _projected_at(timeline: tuple[ProjectedDay, ...], arrival: date) -> Decimal:
    eligible = tuple(day.quantity for day in timeline if day.projection_date <= arrival)
    return eligible[-1] if eligible else timeline[0].quantity


def evaluate_offer(
    *,
    offer: VendorOffer,
    order_date: date,
    shortage: ShortageEvidence,
    timeline: tuple[ProjectedDay, ...],
    performance: VendorPerformanceEvidence,
) -> OfferEvidence:
    """Reject unsafe offers and calculate exact per-offer quantity and cost."""

    if not timeline or type(offer.lead_time_days) is not int:
        raise ValueError("offer inputs are invalid")
    if (
        offer.unit_price < 0
        or offer.exchange_rate <= 0
        or offer.lead_time_days < 0
        or offer.minimum_quantity <= 0
        or offer.package_multiple <= 0
    ):
        raise ValueError("offer numeric inputs are invalid")
    arrival = order_date + timedelta(days=offer.lead_time_days)
    reasons: list[str] = []
    if not offer.approved:
        reasons.append("VENDOR_NOT_APPROVED")
    if offer.blocked:
        reasons.append("VENDOR_BLOCKED")
    if offer.valid_from is not None and order_date < offer.valid_from:
        reasons.append("OFFER_NOT_YET_VALID")
    if offer.valid_until is not None and order_date > offer.valid_until:
        reasons.append("OFFER_EXPIRED")
    if arrival > shortage.need_by_date:
        reasons.append("DELIVERY_AFTER_NEED_BY")

    projected_arrival = _projected_at(timeline, arrival)
    unrounded = max(
        shortage.reorder_maximum - projected_arrival,
        offer.minimum_quantity,
    )
    packages = (unrounded / offer.package_multiple).to_integral_value(
        rounding=ROUND_CEILING
    )
    quantity = (packages * offer.package_multiple).quantize(_QUANTUM)
    normalized_unit = (offer.unit_price * offer.exchange_rate).quantize(_QUANTUM)
    normalized_cost = (quantity * normalized_unit).quantize(_QUANTUM)
    projected_after = (projected_arrival + quantity).quantize(_QUANTUM)
    excess = max(Decimal("0"), projected_after - shortage.reorder_maximum).quantize(
        _QUANTUM
    )
    return OfferEvidence(
        offer_id=offer.offer_id,
        vendor_id=offer.vendor_id,
        vendor_name=offer.vendor_name,
        status=(EvidenceStatus.REJECTED if reasons else EvidenceStatus.ELIGIBLE),
        reason_codes=tuple(reasons),
        currency=offer.currency,
        unit_price=offer.unit_price.quantize(_QUANTUM),
        company_currency=offer.company_currency,
        normalized_unit_price=normalized_unit,
        delivery_date=arrival,
        quantity=quantity,
        normalized_cost=normalized_cost,
        projected_inventory_after_receipt=projected_after,
        excess_inventory=excess,
        performance=performance,
    )
