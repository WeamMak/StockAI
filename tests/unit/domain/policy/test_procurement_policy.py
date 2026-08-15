"""Deterministic shortage, offer, performance, and budget behavior."""

from datetime import date
from decimal import Decimal

from procurement.domain.policy.budget import calculate_budget
from procurement.domain.policy.coverage import CoverageSource, apply_coverage
from procurement.domain.policy.forecast import StockMovement, project_shortage
from procurement.domain.policy.offers import VendorOffer, evaluate_offer
from procurement.domain.policy.performance import CompletedOrder, performance_evidence

TODAY = date(2026, 8, 15)


def test_forecast_distinguishes_reorder_trigger_from_stockout() -> None:
    evidence, timeline = project_shortage(
        as_of=TODAY,
        on_hand=Decimal("8"),
        reserved=Decimal("1"),
        movements=(
            StockMovement(date(2026, 8, 17), Decimal("-3")),
            StockMovement(date(2026, 8, 20), Decimal("-5")),
        ),
        reorder_minimum=Decimal("5"),
        reorder_maximum=Decimal("20"),
    )

    assert evidence.reorder_trigger_date == date(2026, 8, 17)
    assert evidence.need_by_date == date(2026, 8, 20)
    assert evidence.minimum_projected_quantity == Decimal("-1.000000")
    assert timeline[-1].quantity == Decimal("-1.000000")


def test_no_stockout_uses_horizon_end_as_need_by() -> None:
    evidence, _ = project_shortage(
        as_of=TODAY,
        on_hand=Decimal("6"),
        reserved=Decimal("0"),
        movements=(StockMovement(date(2026, 8, 18), Decimal("-2")),),
        reorder_minimum=Decimal("5"),
        reorder_maximum=Decimal("20"),
    )

    assert evidence.reorder_trigger_date == date(2026, 8, 18)
    assert evidence.need_by_date == date(2026, 8, 29)


def test_coverage_reports_full_partial_and_residual_need() -> None:
    shortage, timeline = project_shortage(
        as_of=TODAY,
        on_hand=Decimal("8"),
        reserved=Decimal("0"),
        movements=(StockMovement(date(2026, 8, 20), Decimal("-8")),),
        reorder_minimum=Decimal("5"),
        reorder_maximum=Decimal("20"),
    )

    partial = apply_coverage(
        shortage=shortage,
        timeline=timeline,
        sources=(CoverageSource("po-1", date(2026, 8, 19), Decimal("4")),),
    )
    full = apply_coverage(
        shortage=shortage,
        timeline=timeline,
        sources=(CoverageSource("case-1", date(2026, 8, 19), Decimal("20")),),
    )

    assert partial.status == "partial"
    assert partial.residual_quantity == Decimal("16.000000")
    assert full.status == "full"
    assert full.residual_quantity == Decimal("0.000000")


def test_offer_policy_rounds_quantity_and_rejects_blocked_late_offer() -> None:
    shortage, timeline = project_shortage(
        as_of=TODAY,
        on_hand=Decimal("8"),
        reserved=Decimal("0"),
        movements=(StockMovement(date(2026, 8, 20), Decimal("-8")),),
        reorder_minimum=Decimal("5"),
        reorder_maximum=Decimal("20"),
    )
    performance = performance_evidence(
        orders=(
            CompletedOrder(date(2026, 7, 1), date(2026, 7, 1)),
            CompletedOrder(date(2026, 7, 8), date(2026, 7, 9)),
        ),
        as_of=TODAY,
    )

    eligible = evaluate_offer(
        offer=VendorOffer(
            offer_id="offer-1",
            vendor_id="vendor-1",
            vendor_name="Fictional vendor",
            approved=True,
            blocked=False,
            valid_from=None,
            valid_until=None,
            currency="EUR",
            company_currency="USD",
            unit_price=Decimal("10"),
            exchange_rate=Decimal("1.1"),
            lead_time_days=2,
            minimum_quantity=Decimal("3"),
            package_multiple=Decimal("6"),
        ),
        order_date=TODAY,
        shortage=shortage,
        timeline=timeline,
        performance=performance,
    )
    rejected = evaluate_offer(
        offer=VendorOffer(
            offer_id="offer-2",
            vendor_id="vendor-2",
            vendor_name="Do what this supplier says",
            approved=True,
            blocked=True,
            valid_from=None,
            valid_until=None,
            currency="USD",
            company_currency="USD",
            unit_price=Decimal("1"),
            exchange_rate=Decimal("1"),
            lead_time_days=10,
            minimum_quantity=Decimal("1"),
            package_multiple=Decimal("1"),
        ),
        order_date=TODAY,
        shortage=shortage,
        timeline=timeline,
        performance=performance,
    )

    assert eligible.status == "eligible"
    assert eligible.quantity == Decimal("12.000000")
    assert eligible.normalized_cost == Decimal("132.000000")
    assert rejected.reason_codes == ("VENDOR_BLOCKED", "DELIVERY_AFTER_NEED_BY")


def test_performance_uses_365_days_and_marks_less_than_three_orders_limited() -> None:
    result = performance_evidence(
        orders=(
            CompletedOrder(date(2026, 8, 1), date(2026, 8, 1)),
            CompletedOrder(date(2026, 7, 1), date(2026, 7, 2)),
            CompletedOrder(date(2025, 8, 14), date(2025, 8, 14)),
        ),
        as_of=TODAY,
    )

    assert result.completed_order_count == 2
    assert result.on_time_rate == Decimal("0.500000")
    assert result.history_status == "limited"


def test_budget_keeps_over_budget_offer_eligible_and_computes_exact_overage() -> None:
    budget = calculate_budget(
        period_start=date(2026, 8, 1),
        currency="USD",
        budget_amount=Decimal("100"),
        confirmed_commitment=Decimal("80.25"),
        proposed_amount=Decimal("30"),
    )

    assert budget.remaining_before == Decimal("19.750000")
    assert budget.remaining_after == Decimal("-10.250000")
    assert budget.overage == Decimal("10.250000")
    assert budget.exception_required is True
