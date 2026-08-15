"""Strict deterministic evidence mapping from independent Odoo reads."""

from copy import deepcopy
from datetime import UTC, datetime
from decimal import Decimal
from typing import cast

import pytest

from procurement.adapters.odoo.evidence import build_odoo_evidence
from procurement.domain.identifiers import Environment

CAPTURED_AT = datetime(2026, 8, 15, 8, tzinfo=UTC)


def _sources() -> dict[str, object]:
    return {
        "product": [
            {
                "id": 31,
                "name": "DEV Fictional Component",
                "categ_id": [41, "DEV Components"],
                "product_tmpl_id": [51, "DEV Fictional Component"],
                "qty_available": 8.0,
            }
        ],
        "orderpoint": [
            {
                "id": 11,
                "product_min_qty": 5.0,
                "product_max_qty": 20.0,
                "replenishment_uom_id": [1, "Units"],
                "company_id": [7, "Fictional Dev Company"],
            }
        ],
        "offers": [
            {
                "id": 61,
                "partner_id": [71, "Safe supplier"],
                "product_tmpl_id": [51, "DEV Fictional Component"],
                "product_uom_id": [1, "Units"],
                "currency_id": [1, "USD"],
                "price": 12.0,
                "delay": 2,
                "min_qty": 6.0,
                "date_start": False,
                "date_end": False,
            }
        ],
        "partners": [
            {
                "id": 71,
                "name": "Safe supplier",
                "category_id": [81],
            }
        ],
        "tags": [{"id": 81, "name": "Approved Procurement Vendor"}],
        "orders": [
            {
                "id": 91,
                "state": "done",
                "partner_id": [71, "Safe supplier"],
                "date_order": "2026-07-01 08:00:00",
                "effective_date": "2026-07-04 08:00:00",
                "currency_id": [1, "USD"],
                "company_id": [7, "Fictional Dev Company"],
            }
        ],
        "order_lines": [
            {
                "id": 101,
                "order_id": [91, "PO00091"],
                "product_qty": 10.0,
                "qty_received": 10.0,
                "date_planned": "2026-07-05 08:00:00",
                "price_subtotal": 100.0,
                "currency_id": [1, "USD"],
                "analytic_distribution": {"901": 100.0},
            }
        ],
        "budgets": [
            {
                "id": 111,
                "product_category_id": [41, "DEV Components"],
                "analytic_account_id": [901, "DEV Components"],
                "period_start": "2026-08-01",
                "currency_id": [1, "USD"],
                "amount": 500.0,
                "company_id": [7, "Fictional Dev Company"],
            }
        ],
        "moves": [
            {
                "id": 121,
                "date": "2026-08-18 08:00:00",
                "product_uom_qty": 8.0,
                "location_id": [131, "Stock"],
                "location_dest_id": [132, "Customer"],
                "purchase_line_id": False,
            }
        ],
        "locations": [
            {"id": 131, "usage": "internal"},
            {"id": 132, "usage": "customer"},
        ],
        "company": [{"id": 7, "currency_id": [1, "USD"]}],
        "currencies": [{"id": 1, "name": "USD", "rate": 1.0}],
        "uoms": [{"id": 1, "factor": 1.0}],
    }


def test_odoo_evidence_maps_forecast_offer_performance_and_budget() -> None:
    evidence = build_odoo_evidence(
        environment=Environment.DEV,
        company_id=7,
        product_id=31,
        captured_at=CAPTURED_AT,
        **_sources(),
    )

    assert evidence.shortage.reorder_trigger_date is not None
    assert evidence.shortage.reorder_trigger_date.isoformat() == "2026-08-18"
    assert evidence.shortage.need_by_date.isoformat() == "2026-08-18"
    assert evidence.coverage.status == "none"
    assert evidence.offers[0].quantity == Decimal("12.000000")
    assert evidence.offers[0].normalized_cost == Decimal("144.000000")
    assert evidence.offers[0].performance.completed_order_count == 1
    assert evidence.offers[0].performance.history_status == "limited"
    assert evidence.budget is not None
    assert evidence.budget.confirmed_commitment == Decimal("0")
    assert evidence.budget.remaining_after == Decimal("356.000000")
    assert evidence.skip_reason_code is None


def test_odoo_evidence_rejects_cross_company_or_malformed_source_data() -> None:
    sources = deepcopy(_sources())
    orderpoints = cast(list[dict[str, object]], sources["orderpoint"])
    orderpoints[0]["company_id"] = [8, "Other company"]

    with pytest.raises(ValueError, match="Odoo evidence response is invalid"):
        build_odoo_evidence(
            environment=Environment.DEV,
            company_id=7,
            product_id=31,
            captured_at=CAPTURED_AT,
            **sources,
        )


def test_odoo_evidence_applies_configured_currency_and_uom_conversion() -> None:
    sources = deepcopy(_sources())
    offers = cast(list[dict[str, object]], sources["offers"])
    offers[0]["currency_id"] = [2, "EUR"]
    offers[0]["product_uom_id"] = [2, "Pack of 6"]
    offers[0]["price"] = 54.0
    offers[0]["min_qty"] = 2.0
    sources["currencies"] = [
        {"id": 1, "name": "USD", "rate": 1.0},
        {"id": 2, "name": "EUR", "rate": 0.9},
    ]
    sources["uoms"] = [
        {"id": 1, "factor": 1.0},
        {"id": 2, "factor": 6.0},
    ]

    evidence = build_odoo_evidence(
        environment=Environment.DEV,
        company_id=7,
        product_id=31,
        captured_at=CAPTURED_AT,
        **sources,
    )

    offer = evidence.offers[0]
    assert offer.quantity == Decimal("12.000000")
    assert offer.normalized_unit_price == Decimal("10.000000")
    assert offer.normalized_cost == Decimal("120.000000")
    assert offer.excess_inventory == Decimal("0.000000")
