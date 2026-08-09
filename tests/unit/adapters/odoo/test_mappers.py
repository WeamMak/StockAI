"""Strict mapping tests for untrusted Odoo candidate responses."""

from __future__ import annotations

from copy import deepcopy
from datetime import date
from decimal import Decimal

import pytest

from procurement.adapters.odoo.mappers import (
    OdooMappingError,
    candidate_product_ids,
    map_candidate_page,
    parse_candidate_cursor,
)
from procurement.ports.erp import ReplenishmentCandidateRecord


def _orderpoint() -> dict[str, object]:
    return {
        "id": 11,
        "active": True,
        "trigger": "manual",
        "product_id": [31, "DEV Fictional Component"],
        "product_min_qty": 5.0,
        "product_max_qty": 20.0,
        "company_id": [7, "Fictional Dev Company"],
        "qty_forecast": -2.25,
        "qty_to_order": 22.25,
        "write_date": "2026-08-09 10:30:00",
    }


def _product() -> dict[str, object]:
    return {
        "id": 31,
        "name": "DEV Fictional Component",
        "categ_id": [41, "DEV Components"],
        "active": True,
        "is_storable": True,
        "purchase_ok": True,
    }


def test_candidate_page_maps_exact_company_bound_odoo_records() -> None:
    page = map_candidate_page(
        orderpoints=[_orderpoint()],
        products=[_product()],
        expected_company_id=7,
        requested_limit=25,
        trigger_date=date(2026, 8, 9),
    )

    assert page.next_cursor is None
    assert page.items == (
        ReplenishmentCandidateRecord(
            product_id="31",
            product_name="DEV Fictional Component",
            category_id="41",
            reorder_minimum=Decimal("5.000000"),
            reorder_maximum=Decimal("20.000000"),
            projected_quantity=Decimal("-2.250000"),
            projected_trigger_date=date(2026, 8, 9),
            skip_reason_code=None,
        ),
    )
    assert candidate_product_ids([_orderpoint()]) == (31,)
    assert parse_candidate_cursor(None) == 0
    assert parse_candidate_cursor("orderpoint:11") == 11


def test_candidate_page_uses_an_opaque_cursor_for_the_next_orderpoint_page() -> None:
    first = _orderpoint()
    second = _orderpoint() | {"id": 12, "product_id": [32, "Second"]}
    products = [_product(), _product() | {"id": 32, "name": "Second"}]

    page = map_candidate_page(
        orderpoints=[first, second],
        products=products,
        expected_company_id=7,
        requested_limit=1,
        trigger_date=date(2026, 8, 9),
    )

    assert [item.product_id for item in page.items] == ["31"]
    assert page.next_cursor == "orderpoint:11"


@pytest.mark.parametrize(
    ("target", "field", "value"),
    [
        ("orderpoint", "id", "11"),
        ("orderpoint", "company_id", [8, "Other Company"]),
        ("orderpoint", "product_min_qty", "not-a-decimal"),
        ("orderpoint", "write_date", "not-a-datetime"),
        ("orderpoint", "write_date", "2026-08-09"),
        ("orderpoint", "trigger", "unexpected"),
        ("product", "active", False),
        ("product", "categ_id", ["41", "DEV Components"]),
    ],
)
def test_candidate_mapping_rejects_mistyped_cross_company_or_unexpected_data(
    target: str,
    field: str,
    value: object,
) -> None:
    orderpoint = deepcopy(_orderpoint())
    product = deepcopy(_product())
    (orderpoint if target == "orderpoint" else product)[field] = value

    with pytest.raises(OdooMappingError) as raised:
        map_candidate_page(
            orderpoints=[orderpoint],
            products=[product],
            expected_company_id=7,
            requested_limit=25,
            trigger_date=date(2026, 8, 9),
        )

    assert str(raised.value) == "The procurement source returned invalid data."
    assert str(value) not in str(raised.value)


def test_candidate_mapping_rejects_missing_or_duplicate_records() -> None:
    missing = _orderpoint()
    del missing["write_date"]

    for orderpoints, products in (
        ([missing], [_product()]),
        ([_orderpoint()], []),
        ([_orderpoint()], [_product(), _product()]),
    ):
        with pytest.raises(OdooMappingError):
            map_candidate_page(
                orderpoints=orderpoints,
                products=products,
                expected_company_id=7,
                requested_limit=25,
                trigger_date=date(2026, 8, 9),
            )


@pytest.mark.parametrize("cursor", ["11", "orderpoint:0", "orderpoint:-1", "bad:11"])
def test_candidate_cursor_rejects_unknown_or_nonpositive_values(cursor: str) -> None:
    with pytest.raises(OdooMappingError):
        parse_candidate_cursor(cursor)
