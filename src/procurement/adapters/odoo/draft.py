"""Mapping helpers for one idempotent draft purchase order."""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from procurement.ports.erp import PurchaseOrderDraft

DRAFT_SNAPSHOT_FIELDS = (
    "id",
    "write_date",
    "state",
    "partner_id",
    "currency_id",
    "amount_total",
)


class OdooDraftMappingError(Exception):
    """A purchase-order row could not be safely mapped to a draft snapshot."""


def many2one(raw: object) -> tuple[int, str]:
    if (
        not isinstance(raw, list)
        or len(raw) != 2
        or type(raw[0]) is not int
        or raw[0] <= 0
        or not isinstance(raw[1], str)
        or not raw[1]
    ):
        raise ValueError("invalid many-to-one value")
    return raw[0], raw[1]


def _amount(raw: object) -> Decimal:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise ValueError("invalid amount")
    value = Decimal(str(raw))
    if not value.is_finite() or value < 0:
        raise ValueError("amount is out of range")
    return value


def purchase_order_draft_from_row(row: Mapping[str, Any]) -> PurchaseOrderDraft:
    """Map one `purchase.order` read row to its stable draft snapshot."""

    try:
        po_id = row["id"]
        if type(po_id) is not int or po_id <= 0:
            raise ValueError("invalid purchase order id")
        write_date = row["write_date"]
        if not isinstance(write_date, str) or not write_date:
            raise ValueError("invalid write_date")
        state = row["state"]
        if not isinstance(state, str) or not state:
            raise ValueError("invalid state")
        partner_id, _partner_name = many2one(row["partner_id"])
        currency_id, _currency_name = many2one(row["currency_id"])
        amount_total = _amount(row["amount_total"])
    except (KeyError, TypeError, ValueError) as error:
        raise OdooDraftMappingError(error) from None
    return PurchaseOrderDraft(
        po_id=po_id,
        write_date=write_date,
        state=state,
        partner_id=partner_id,
        currency_id=currency_id,
        amount_total=amount_total,
    )
