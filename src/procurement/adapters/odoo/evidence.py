"""Strict Odoo evidence mapping into deterministic procurement policy."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation

from procurement.domain.identifiers import Environment
from procurement.domain.policy.budget import calculate_budget
from procurement.domain.policy.coverage import (
    CoverageSource,
    apply_coverage,
    timeline_with_coverage,
)
from procurement.domain.policy.evidence import EvidenceStatus, ProcurementEvidence
from procurement.domain.policy.forecast import StockMovement, project_shortage
from procurement.domain.policy.offers import VendorOffer, evaluate_offer
from procurement.domain.policy.performance import CompletedOrder, performance_evidence


def build_odoo_evidence(  # noqa: PLR0913 - explicit untrusted boundaries
    *,
    environment: Environment,
    company_id: int,
    product_id: int,
    captured_at: datetime,
    product: object,
    orderpoint: object,
    offers: object,
    partners: object,
    tags: object,
    orders: object,
    order_lines: object,
    budgets: object,
    moves: object,
    locations: object,
    company: object,
    currencies: object,
    uoms: object,
) -> ProcurementEvidence:
    """Validate independent Odoo reads and calculate one evidence record."""

    try:
        today = captured_at.astimezone(UTC).date()
        product_row = _one(
            product,
            {"id", "name", "categ_id", "product_tmpl_id", "qty_available"},
        )
        if _integer(product_row["id"]) != product_id:
            raise ValueError("product mismatch")
        category_id, _ = _many2one(product_row["categ_id"])
        template_id, _ = _many2one(product_row["product_tmpl_id"])
        product_name = _text(product_row["name"])
        point = _one(
            orderpoint,
            {
                "id",
                "product_min_qty",
                "product_max_qty",
                "replenishment_uom_id",
                "company_id",
            },
        )
        point_company, _ = _many2one(point["company_id"])
        if point_company != company_id:
            raise ValueError("cross-company orderpoint")
        company_row = _one(company, {"id", "currency_id"})
        if _integer(company_row["id"]) != company_id:
            raise ValueError("company mismatch")
        company_currency_id, company_currency = _many2one(company_row["currency_id"])
        currency_rows = _rows(currencies, {"id", "name", "rate"})
        currency_rates = {
            _integer(row["id"]): (_text(row["name"]), _decimal(row["rate"]))
            for row in currency_rows
        }
        company_rate = currency_rates[company_currency_id]
        if company_rate[0] != company_currency or company_rate[1] <= 0:
            raise ValueError("invalid company currency")
        uom_factors = {
            _integer(row["id"]): _decimal(row["factor"])
            for row in _rows(uoms, {"id", "factor"})
        }
        if any(factor <= 0 for factor in uom_factors.values()):
            raise ValueError("invalid UoM factor")
        _many2one(point["replenishment_uom_id"])

        location_usage = {
            _integer(row["id"]): _text(row["usage"])
            for row in _rows(locations, {"id", "usage"})
        }
        stock_movements: list[StockMovement] = []
        for move in _rows(
            moves,
            {
                "id",
                "date",
                "product_uom_qty",
                "location_id",
                "location_dest_id",
                "purchase_line_id",
            },
        ):
            source, _ = _many2one(move["location_id"])
            destination, _ = _many2one(move["location_dest_id"])
            source_internal = location_usage.get(source) == "internal"
            destination_internal = location_usage.get(destination) == "internal"
            if source_internal == destination_internal:
                continue
            if destination_internal and move["purchase_line_id"] is not False:
                _many2one(move["purchase_line_id"])
                continue
            quantity = _decimal(move["product_uom_qty"])
            stock_movements.append(
                StockMovement(
                    _datetime(move["date"]).date(),
                    quantity if destination_internal else -quantity,
                )
            )
        shortage, timeline = project_shortage(
            as_of=today,
            on_hand=_decimal(product_row["qty_available"]),
            reserved=Decimal("0"),
            movements=tuple(stock_movements),
            reorder_minimum=_decimal(point["product_min_qty"]),
            reorder_maximum=_decimal(point["product_max_qty"]),
        )

        order_rows = _rows(
            orders,
            {
                "id",
                "state",
                "partner_id",
                "date_order",
                "effective_date",
                "currency_id",
                "company_id",
            },
        )
        orders_by_id = {_integer(row["id"]): row for row in order_rows}
        line_rows = _rows(
            order_lines,
            {
                "id",
                "order_id",
                "product_qty",
                "qty_received",
                "date_planned",
                "price_subtotal",
                "currency_id",
                "analytic_distribution",
            },
        )
        coverage_sources: list[CoverageSource] = []
        for line in line_rows:
            order_id, _ = _many2one(line["order_id"])
            order = orders_by_id[order_id]
            if order["state"] in {"draft", "sent", "purchase"}:
                remaining = _decimal(line["product_qty"]) - _decimal(
                    line["qty_received"]
                )
                if remaining > 0:
                    coverage_sources.append(
                        CoverageSource(
                            f"po-line-{_integer(line['id'])}",
                            _datetime(line["date_planned"]).date(),
                            remaining,
                        )
                    )
        coverage = apply_coverage(
            shortage=shortage,
            timeline=timeline,
            sources=tuple(coverage_sources),
        )
        covered_timeline = timeline_with_coverage(
            timeline=timeline,
            sources=tuple(coverage_sources),
        )

        tag_names = {
            _integer(row["id"]): _text(row["name"])
            for row in _rows(tags, {"id", "name"})
        }
        partner_rows = _rows(partners, {"id", "name", "category_id"})
        partner_by_id = {_integer(row["id"]): row for row in partner_rows}
        completed_by_vendor: dict[int, list[CompletedOrder]] = {}
        for order in order_rows:
            effective = order["effective_date"]
            if order["state"] not in {"purchase", "done"} or not effective:
                continue
            order_id = _integer(order["id"])
            vendor_id, _ = _many2one(order["partner_id"])
            planned_dates = [
                _datetime(line["date_planned"]).date()
                for line in line_rows
                if _many2one(line["order_id"])[0] == order_id
                and _decimal(line["qty_received"]) > 0
            ]
            if planned_dates:
                completed_by_vendor.setdefault(vendor_id, []).append(
                    CompletedOrder(max(planned_dates), _datetime(effective).date())
                )

        mapped_offers = []
        for raw_offer in _rows(
            offers,
            {
                "id",
                "partner_id",
                "product_tmpl_id",
                "product_uom_id",
                "currency_id",
                "price",
                "delay",
                "min_qty",
                "date_start",
                "date_end",
            },
        ):
            raw_template_id, _ = _many2one(raw_offer["product_tmpl_id"])
            if raw_template_id != template_id:
                raise ValueError("offer template mismatch")
            vendor_id, _ = _many2one(raw_offer["partner_id"])
            partner = partner_by_id[vendor_id]
            category_ids = _integer_list(partner["category_id"])
            names = {tag_names[tag_id] for tag_id in category_ids}
            currency_id, currency_name = _many2one(raw_offer["currency_id"])
            source_currency, source_rate = currency_rates[currency_id]
            if source_currency != currency_name or source_rate <= 0:
                raise ValueError("invalid offer currency")
            uom_id, _ = _many2one(raw_offer["product_uom_id"])
            uom_factor = uom_factors[uom_id]
            performance = performance_evidence(
                orders=tuple(completed_by_vendor.get(vendor_id, [])),
                as_of=today,
            )
            mapped = evaluate_offer(
                offer=VendorOffer(
                    offer_id=f"offer-{_integer(raw_offer['id'])}",
                    vendor_id=f"vendor-{vendor_id}",
                    vendor_name=_text(partner["name"]),
                    approved="Approved Procurement Vendor" in names,
                    blocked="Blocked Procurement Vendor" in names,
                    valid_from=_optional_date(raw_offer["date_start"]),
                    valid_until=_optional_date(raw_offer["date_end"]),
                    currency=currency_name,
                    company_currency=company_currency,
                    unit_price=_decimal(raw_offer["price"]) / uom_factor,
                    exchange_rate=company_rate[1] / source_rate,
                    lead_time_days=_integer(raw_offer["delay"], allow_zero=True),
                    minimum_quantity=max(
                        Decimal("0.000001"),
                        _decimal(raw_offer["min_qty"]) * uom_factor,
                    ),
                    package_multiple=uom_factor,
                ),
                order_date=today,
                shortage=shortage,
                timeline=covered_timeline,
                performance=performance,
            )
            mapped_offers.append(mapped)

        eligible = tuple(
            offer for offer in mapped_offers if offer.status is EvidenceStatus.ELIGIBLE
        )
        budget = None
        budget_rows = _rows(
            budgets,
            {
                "id",
                "product_category_id",
                "analytic_account_id",
                "period_start",
                "currency_id",
                "amount",
                "company_id",
            },
        )
        matching = [
            row
            for row in budget_rows
            if _many2one(row["product_category_id"])[0] == category_id
            and _date(row["period_start"]) == today.replace(day=1)
            and _many2one(row["company_id"])[0] == company_id
        ]
        if len(matching) == 1 and eligible:
            budget_row = matching[0]
            analytic_id, _ = _many2one(budget_row["analytic_account_id"])
            currency = _many2one(budget_row["currency_id"])[1]
            committed = sum(
                (
                    _decimal(line["price_subtotal"])
                    for line in line_rows
                    if _line_is_committed(
                        line,
                        orders_by_id=orders_by_id,
                        analytic_id=analytic_id,
                        month=today.replace(day=1),
                        currency=currency,
                    )
                ),
                Decimal("0"),
            )
            budget = calculate_budget(
                period_start=today.replace(day=1),
                currency=currency,
                budget_amount=_decimal(budget_row["amount"]),
                confirmed_commitment=committed,
                proposed_amount=min(offer.normalized_cost for offer in eligible),
            )

        skip_reason = None
        if shortage.reorder_trigger_date is None:
            skip_reason = "NO_SHORTAGE"
        elif coverage.status == "full":
            skip_reason = "FULLY_COVERED"
        elif not eligible:
            skip_reason = "NO_VALID_OFFER"
        elif budget is None:
            skip_reason = "BUDGET_UNAVAILABLE"
        evidence_suffix = captured_at.strftime("%Y%m%dT%H%M%S%fZ")
        return ProcurementEvidence(
            environment=environment,
            evidence_id=f"{environment.value}:evidence-{product_id}-{evidence_suffix}",
            product_id=str(product_id),
            product_name=product_name,
            category_id=str(category_id),
            captured_at=captured_at,
            shortage=shortage,
            coverage=coverage,
            offers=tuple(mapped_offers),
            budget=budget,
            skip_reason_code=skip_reason,
        )
    except (InvalidOperation, KeyError, TypeError, ValueError) as error:
        raise ValueError("Odoo evidence response is invalid") from error


def _rows(raw: object, expected: set[str]) -> list[Mapping[str, object]]:
    if not isinstance(raw, list) or len(raw) > 1000:
        raise ValueError("expected bounded Odoo records")
    if any(not isinstance(row, Mapping) or set(row) != expected for row in raw):
        raise ValueError("unexpected Odoo evidence fields")
    return list(raw)


def _one(raw: object, expected: set[str]) -> Mapping[str, object]:
    rows = _rows(raw, expected)
    if len(rows) != 1:
        raise ValueError("expected exactly one Odoo record")
    return rows[0]


def _integer(raw: object, *, allow_zero: bool = False) -> int:
    if type(raw) is not int or raw < (0 if allow_zero else 1):
        raise ValueError("invalid Odoo integer")
    return raw


def _decimal(raw: object) -> Decimal:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise ValueError("invalid Odoo decimal")
    value = Decimal(str(raw)).quantize(Decimal("0.000001"))
    if not value.is_finite() or abs(value) > Decimal("999999999999.999999"):
        raise ValueError("Odoo decimal is out of range")
    return value


def _text(raw: object) -> str:
    if not isinstance(raw, str) or not raw.strip() or len(raw) > 200:
        raise ValueError("invalid Odoo text")
    return raw


def _many2one(raw: object) -> tuple[int, str]:
    if not isinstance(raw, list) or len(raw) != 2:
        raise ValueError("invalid Odoo relationship")
    return _integer(raw[0]), _text(raw[1])


def _integer_list(raw: object) -> tuple[int, ...]:
    if not isinstance(raw, list) or len(raw) > 100:
        raise ValueError("invalid Odoo relationship list")
    return tuple(_integer(value) for value in raw)


def _datetime(raw: object) -> datetime:
    if not isinstance(raw, str):
        raise ValueError("invalid Odoo datetime")
    return datetime.strptime(raw, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)


def _date(raw: object) -> date:
    if not isinstance(raw, str):
        raise ValueError("invalid Odoo date")
    return date.fromisoformat(raw)


def _optional_date(raw: object) -> date | None:
    return None if raw is False else _date(raw)


def _line_is_committed(
    line: Mapping[str, object],
    *,
    orders_by_id: Mapping[int, Mapping[str, object]],
    analytic_id: int,
    month: date,
    currency: str,
) -> bool:
    order_id, _ = _many2one(line["order_id"])
    order = orders_by_id[order_id]
    distribution = line["analytic_distribution"]
    return (
        order["state"] in {"purchase", "done"}
        and _datetime(order["date_order"]).date().replace(day=1) == month
        and _many2one(order["currency_id"])[1] == currency
        and isinstance(distribution, Mapping)
        and any(str(analytic_id) in str(key).split(",") for key in distribution)
    )
