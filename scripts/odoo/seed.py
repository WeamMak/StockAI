# ruff: noqa: F821
"""Seed the fictional, idempotent StockAI Odoo dev/prod demonstration data."""

from __future__ import annotations

import datetime
import json
import os

from odoo.fields import Command

environment = os.environ.get("STOCKAI_ODOO_SEED_ENVIRONMENT", "dev").strip().lower()
if environment not in {"dev", "prod"}:
    raise RuntimeError("seed environment must be dev or prod")

label = environment.upper()
prefix = f"STOCKAI-{label}"
company = env.company  # noqa: F821 - supplied by odoo shell
unit_uom = env.ref("uom.product_uom_unit")  # noqa: F821
warehouse = (
    env["stock.warehouse"]
    .sudo()
    .search(  # noqa: F821
        [("company_id", "=", company.id)], limit=1
    )
)
if not warehouse:
    raise RuntimeError("seed requires one warehouse for the current company")


def _one_or_create(model_name, domain, values):
    records = env[model_name].sudo().search(domain)  # noqa: F821
    if len(records) > 1:
        raise RuntimeError(f"seed found duplicate {model_name} records")
    if records:
        return records.ensure_one()
    return env[model_name].sudo().create(values)  # noqa: F821


def _vendor(ref, name, tag):
    vendor = _one_or_create(
        "res.partner",
        [("ref", "=", ref)],
        {
            "name": name,
            "ref": ref,
            "is_company": True,
            "supplier_rank": 1,
            "email": f"{ref.lower()}@example.invalid",
            "company_id": False,
        },
    )
    vendor.write(
        {
            "name": name,
            "supplier_rank": max(vendor.supplier_rank, 1),
            "category_id": [Command.set(tag.ids)],
        }
    )
    return vendor


def _product(code, name, category):
    template = _one_or_create(
        "product.template",
        [("default_code", "=", code)],
        {
            "name": name,
            "default_code": code,
            "is_storable": True,
            "purchase_ok": True,
            "sale_ok": False,
            "categ_id": category.id,
            "uom_id": unit_uom.id,
        },
    )
    template.write(
        {
            "name": name,
            "is_storable": True,
            "purchase_ok": True,
            "categ_id": category.id,
        }
    )
    product = template.product_variant_id.ensure_one()
    _one_or_create(
        "stock.warehouse.orderpoint",
        [
            ("product_id", "=", product.id),
            ("location_id", "=", warehouse.lot_stock_id.id),
            ("company_id", "=", company.id),
        ],
        {
            "name": f"{code} Reordering Rule",
            "trigger": "manual",
            "product_id": product.id,
            "location_id": warehouse.lot_stock_id.id,
            "product_min_qty": 5.0,
            "product_max_qty": 20.0,
            "replenishment_uom_id": unit_uom.id,
            "company_id": company.id,
        },
    )
    return template, product


def _offer(template, vendor, *, price, delay, sequence):
    offer = _one_or_create(
        "product.supplierinfo",
        [
            ("partner_id", "=", vendor.id),
            ("product_tmpl_id", "=", template.id),
            ("company_id", "=", company.id),
        ],
        {
            "partner_id": vendor.id,
            "product_tmpl_id": template.id,
            "product_uom_id": unit_uom.id,
            "min_qty": 1.0,
            "price": price,
            "currency_id": company.currency_id.id,
            "delay": delay,
            "sequence": sequence,
            "company_id": company.id,
        },
    )
    offer.write({"price": price, "delay": delay, "sequence": sequence})
    return offer


def _budget(category, analytic_account, amount):
    month = datetime.date.today().replace(day=1)
    budget = _one_or_create(
        "stockai.procurement.budget",
        [
            ("company_id", "=", company.id),
            ("product_category_id", "=", category.id),
            ("period_start", "=", month),
            ("active", "=", True),
        ],
        {
            "company_id": company.id,
            "product_category_id": category.id,
            "analytic_account_id": analytic_account.id,
            "period_start": month,
            "amount": amount,
        },
    )
    budget.write({"analytic_account_id": analytic_account.id, "amount": amount})
    return budget


def _draft_order(origin, vendor, product, analytic_account, *, quantity, price):
    orders = (
        env["purchase.order"]
        .sudo()
        .search(  # noqa: F821
            [("origin", "=", origin), ("company_id", "=", company.id)]
        )
    )
    if len(orders) > 1:
        raise RuntimeError("seed found duplicate purchase orders")
    if orders:
        order = orders.ensure_one()
        if order.state not in {"draft", "sent"}:
            raise RuntimeError("seed open purchase order is no longer a draft")
        return order
    planned_date = datetime.datetime.now() + datetime.timedelta(days=7)
    return (
        env["purchase.order"]
        .sudo()
        .create(  # noqa: F821
            {
                "partner_id": vendor.id,
                "origin": origin,
                "partner_ref": f"FICTIONAL-{origin}",
                "company_id": company.id,
                "order_line": [
                    Command.create(
                        {
                            "product_id": product.id,
                            "product_qty": quantity,
                            "product_uom_id": unit_uom.id,
                            "date_planned": planned_date,
                            "price_unit": price,
                            "tax_ids": [Command.clear()],
                            "analytic_distribution": {str(analytic_account.id): 100.0},
                        }
                    )
                ],
            }
        )
    )


def _receipt_and_return(origin, vendor, product, analytic_account):
    orders = (
        env["purchase.order"]
        .sudo()
        .search(  # noqa: F821
            [("origin", "=", origin), ("company_id", "=", company.id)]
        )
    )
    if len(orders) > 1:
        raise RuntimeError("seed found duplicate receipt purchase orders")
    if orders:
        return orders.ensure_one()

    order = _draft_order(
        origin,
        vendor,
        product,
        analytic_account,
        quantity=4.0,
        price=18.0,
    )
    order.button_confirm()
    receipt = order.picking_ids.filtered(
        lambda picking: not picking.return_id
    ).ensure_one()
    receipt.move_ids.write({"quantity": 4.0})
    result = receipt.button_validate()
    if isinstance(result, dict):
        raise RuntimeError("seed receipt unexpectedly required an interactive wizard")

    wizard = (
        env["stock.return.picking"]
        .sudo()
        .create(  # noqa: F821
            {"picking_id": receipt.id}
        )
    )
    return_action = wizard.action_create_returns_all()
    return_picking = (
        env["stock.picking"]
        .sudo()
        .browse(  # noqa: F821
            return_action["res_id"]
        )
    )
    return_picking.move_ids.write({"quantity": 4.0})
    result = return_picking.button_validate()
    if isinstance(result, dict):
        raise RuntimeError("seed return unexpectedly required an interactive wizard")
    return order


def _inventory_scenario(product):
    """Keep one idempotent on-hand balance and one dated confirmed demand."""
    quant = env["stock.quant"].sudo()  # noqa: F821
    current = quant._get_available_quantity(product, warehouse.lot_stock_id)
    difference = 8.0 - current
    if difference:
        quant._update_available_quantity(
            product,
            warehouse.lot_stock_id,
            difference,
        )
    move_origin = f"{prefix} Forecast Demand {product.default_code}"
    moves = (
        env["stock.move"]
        .sudo()
        .search(  # noqa: F821
            [
                ("origin", "=", move_origin),
                ("product_id", "=", product.id),
                ("company_id", "=", company.id),
                ("state", "!=", "cancel"),
            ]
        )
    )
    if len(moves) > 1:
        raise RuntimeError("seed found duplicate forecast demand moves")
    demand_date = datetime.datetime.now() + datetime.timedelta(days=3)
    if not moves:
        customer_location = env.ref("stock.stock_location_customers")  # noqa: F821
        move = (
            env["stock.move"]
            .sudo()
            .create(  # noqa: F821
                {
                    "origin": move_origin,
                    "product_id": product.id,
                    "product_uom_qty": 8.0,
                    "product_uom": unit_uom.id,
                    "location_id": warehouse.lot_stock_id.id,
                    "location_dest_id": customer_location.id,
                    "date": demand_date,
                    "company_id": company.id,
                }
            )
        )
        move._action_confirm()
    else:
        moves.ensure_one().write({"date": demand_date})


approved_tag = _one_or_create(
    "res.partner.category",
    [("name", "=", "Approved Procurement Vendor")],
    {"name": "Approved Procurement Vendor"},
)
blocked_tag = _one_or_create(
    "res.partner.category",
    [("name", "=", "Blocked Procurement Vendor")],
    {"name": "Blocked Procurement Vendor"},
)
fast_vendor = _vendor(
    f"{prefix}-VENDOR-FAST", f"{label} Fictional Fast Supplies", approved_tag
)
cheap_vendor = _vendor(
    f"{prefix}-VENDOR-CHEAP", f"{label} Fictional Value Supplies", approved_tag
)
blocked_vendor = _vendor(
    f"{prefix}-VENDOR-BLOCKED", f"{label} Fictional Blocked Supplies", blocked_tag
)

happy_category = _one_or_create(
    "product.category",
    [("name", "=", f"{prefix} Happy Components")],
    {"name": f"{prefix} Happy Components"},
)
over_category = _one_or_create(
    "product.category",
    [("name", "=", f"{prefix} Over Budget Components")],
    {"name": f"{prefix} Over Budget Components"},
)
no_offer_category = _one_or_create(
    "product.category",
    [("name", "=", f"{prefix} No Valid Offer Components")],
    {"name": f"{prefix} No Valid Offer Components"},
)
analytic_plan = _one_or_create(
    "account.analytic.plan",
    [("name", "=", f"{prefix} Procurement")],
    {"name": f"{prefix} Procurement"},
)
analytic_account = _one_or_create(
    "account.analytic.account",
    [("code", "=", f"{prefix}-PROC")],
    {
        "name": f"{label} Fictional Procurement",
        "code": f"{prefix}-PROC",
        "plan_id": analytic_plan.id,
        "company_id": company.id,
    },
)

happy_template, happy_product = _product(
    f"{prefix}-HAPPY", f"{label} Fictional Happy-Path Component", happy_category
)
over_template, over_product = _product(
    f"{prefix}-OVER", f"{label} Fictional Over-Budget Component", over_category
)
no_offer_template, _no_offer_product = _product(
    f"{prefix}-NO-OFFER",
    f"{label} Fictional No-Valid-Offer Component",
    no_offer_category,
)
_offer(happy_template, fast_vendor, price=19.0, delay=2, sequence=1)
_offer(happy_template, cheap_vendor, price=16.0, delay=6, sequence=2)
_offer(happy_template, blocked_vendor, price=8.0, delay=1, sequence=3)
_offer(over_template, fast_vendor, price=80.0, delay=2, sequence=1)
_offer(no_offer_template, blocked_vendor, price=5.0, delay=2, sequence=1)

_budget(happy_category, analytic_account, 5000.0)
_budget(over_category, analytic_account, 100.0)
_draft_order(
    f"{prefix}-CASE-HAPPY",
    cheap_vendor,
    happy_product,
    analytic_account,
    quantity=2.0,
    price=16.0,
)
_draft_order(
    f"{prefix}-CASE-OVER-BUDGET",
    fast_vendor,
    over_product,
    analytic_account,
    quantity=5.0,
    price=80.0,
)
_receipt_and_return(
    f"{prefix}-CASE-RECEIPT-RETURN",
    fast_vendor,
    happy_product,
    analytic_account,
)
_inventory_scenario(happy_product)
_inventory_scenario(over_product)
_inventory_scenario(_no_offer_product)

env.cr.commit()  # noqa: F821
print(
    json.dumps(
        {
            "environment": environment,
            "prefix": prefix,
            "status": "ok",
        },
        sort_keys=True,
    )
)
