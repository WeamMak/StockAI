"""Strict mapping of untrusted Odoo candidate data into the ERP port."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from procurement.ports.erp import (
    CandidatePage,
    ErpUnavailableError,
    ReplenishmentCandidateRecord,
)

_ORDERPOINT_FIELDS = frozenset(
    {
        "id",
        "active",
        "trigger",
        "product_id",
        "product_min_qty",
        "product_max_qty",
        "company_id",
        "qty_forecast",
        "qty_to_order",
        "write_date",
    }
)
_PRODUCT_FIELDS = frozenset(
    {"id", "name", "categ_id", "active", "is_storable", "purchase_ok"}
)
_CURSOR_PATTERN = re.compile(r"^orderpoint:([1-9][0-9]*)$", re.ASCII)
_ODOO_DATETIME_PATTERN = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}$",
    re.ASCII,
)
_CONTROL_CHARACTER_PATTERN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_DECIMAL_QUANTUM = Decimal("0.000001")
_MAX_DECIMAL = Decimal("999999999.999999")


class OdooMappingError(ErpUnavailableError):
    """Safe signal that Odoo returned unusable candidate data."""

    safe_message = "The procurement source returned invalid data."


def parse_candidate_cursor(cursor: str | None) -> int:
    """Decode the only cursor format accepted by this Odoo adapter."""

    if cursor is None:
        return 0
    if (
        not isinstance(cursor, str)
        or (match := _CURSOR_PATTERN.fullmatch(cursor)) is None
    ):
        raise OdooMappingError(cursor)
    return int(match.group(1))


def candidate_product_ids(orderpoints: object) -> tuple[int, ...]:
    """Extract stable product IDs without trusting any other response field."""

    records = _records(orderpoints, expected_fields=_ORDERPOINT_FIELDS)
    product_ids: list[int] = []
    for record in records:
        product_id, _display_name = _many2one(record["product_id"])
        if product_id not in product_ids:
            product_ids.append(product_id)
    return tuple(product_ids)


def map_candidate_page(
    *,
    orderpoints: object,
    products: object,
    expected_company_id: int,
    requested_limit: int,
    trigger_date: date,
) -> CandidatePage:
    """Map one strictly bounded Odoo result page into ERP-neutral records."""

    try:
        if type(expected_company_id) is not int or expected_company_id <= 0:
            raise ValueError("invalid expected company")
        if type(requested_limit) is not int or not 1 <= requested_limit <= 100:
            raise ValueError("invalid requested limit")
        if type(trigger_date) is not date:
            raise ValueError("invalid trigger date")

        orderpoint_records = _records(
            orderpoints,
            expected_fields=_ORDERPOINT_FIELDS,
        )
        if len(orderpoint_records) > requested_limit + 1:
            raise ValueError("orderpoint page is oversized")
        product_records = _records(products, expected_fields=_PRODUCT_FIELDS)
        products_by_id = _product_index(product_records)

        expected_product_ids = set(candidate_product_ids(orderpoint_records))
        if set(products_by_id) != expected_product_ids:
            raise ValueError("product response does not match orderpoints")

        consumed = orderpoint_records[:requested_limit]
        previous_orderpoint_id = 0
        mapped: list[ReplenishmentCandidateRecord] = []
        for orderpoint in consumed:
            orderpoint_id = _positive_integer(orderpoint["id"])
            if orderpoint_id <= previous_orderpoint_id:
                raise ValueError("orderpoint IDs are not strictly increasing")
            previous_orderpoint_id = orderpoint_id
            if orderpoint["active"] is not True:
                raise ValueError("inactive orderpoint returned")
            if orderpoint["trigger"] not in {"auto", "manual"}:
                raise ValueError("unknown orderpoint trigger")
            product_id, _product_display_name = _many2one(orderpoint["product_id"])
            company_id, _company_display_name = _many2one(orderpoint["company_id"])
            if company_id != expected_company_id:
                raise ValueError("cross-company orderpoint returned")

            minimum = _decimal(orderpoint["product_min_qty"], allow_negative=False)
            maximum = _decimal(orderpoint["product_max_qty"], allow_negative=False)
            projected = _decimal(orderpoint["qty_forecast"], allow_negative=True)
            quantity_to_order = _decimal(
                orderpoint["qty_to_order"],
                allow_negative=False,
            )
            _odoo_datetime(orderpoint["write_date"])
            if maximum < minimum:
                raise ValueError("invalid reorder range")
            if quantity_to_order == 0:
                continue

            product = products_by_id[product_id]
            if (
                product["active"] is not True
                or product["is_storable"] is not True
                or product["purchase_ok"] is not True
            ):
                raise ValueError("ineligible product returned")
            category_id, _category_name = _many2one(product["categ_id"])
            product_name = _normal_text(product["name"], maximum_length=200)
            mapped.append(
                ReplenishmentCandidateRecord(
                    product_id=str(product_id),
                    product_name=product_name,
                    category_id=str(category_id),
                    reorder_minimum=minimum,
                    reorder_maximum=maximum,
                    projected_quantity=projected,
                    projected_trigger_date=trigger_date,
                    skip_reason_code=None,
                )
            )

        next_cursor = (
            f"orderpoint:{_positive_integer(consumed[-1]['id'])}"
            if len(orderpoint_records) > requested_limit and consumed
            else None
        )
        return CandidatePage(items=tuple(mapped), next_cursor=next_cursor)
    except OdooMappingError:
        raise
    except (InvalidOperation, KeyError, TypeError, ValueError) as error:
        raise OdooMappingError(error) from None


def _records(
    raw: object,
    *,
    expected_fields: frozenset[str],
) -> list[Mapping[str, object]]:
    if not isinstance(raw, list):
        raise OdooMappingError(raw)
    records: list[Mapping[str, object]] = []
    for item in raw:
        if not isinstance(item, Mapping) or set(item) != expected_fields:
            raise OdooMappingError(item)
        records.append(item)
    return records


def _product_index(
    products: list[Mapping[str, object]],
) -> dict[int, Mapping[str, object]]:
    indexed: dict[int, Mapping[str, object]] = {}
    for product in products:
        product_id = _positive_integer(product["id"])
        if product_id in indexed:
            raise ValueError("duplicate product returned")
        indexed[product_id] = product
    return indexed


def _positive_integer(raw: object) -> int:
    if type(raw) is not int or raw <= 0:
        raise ValueError("expected a positive integer")
    return raw


def _many2one(raw: object) -> tuple[int, str]:
    if (
        not isinstance(raw, list)
        or len(raw) != 2
        or type(raw[0]) is not int
        or raw[0] <= 0
        or not isinstance(raw[1], str)
        or not raw[1]
        or len(raw[1]) > 200
    ):
        raise ValueError("invalid many-to-one value")
    return raw[0], raw[1]


def _decimal(raw: object, *, allow_negative: bool) -> Decimal:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise ValueError("invalid decimal value")
    value = Decimal(str(raw))
    minimum = -_MAX_DECIMAL if allow_negative else Decimal("0")
    if not value.is_finite() or not minimum <= value <= _MAX_DECIMAL:
        raise ValueError("decimal value is out of range")
    quantized = value.quantize(_DECIMAL_QUANTUM)
    if value != quantized:
        raise ValueError("decimal value has too many places")
    return quantized


def _odoo_datetime(raw: object) -> datetime:
    if not isinstance(raw, str) or _ODOO_DATETIME_PATTERN.fullmatch(raw) is None:
        raise ValueError("invalid Odoo datetime")
    return datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")


def _normal_text(raw: object, *, maximum_length: int) -> str:
    if (
        not isinstance(raw, str)
        or not raw.strip()
        or len(raw) > maximum_length
        or _CONTROL_CHARACTER_PATTERN.search(raw) is not None
    ):
        raise ValueError("invalid text value")
    return raw
