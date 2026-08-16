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
    vendors = (
        env["res.partner"]
        .sudo()
        .with_context(active_test=False)
        .search([("ref", "=", ref)])  # noqa: F821
    )
    if len(vendors) > 1:
        raise RuntimeError("seed found duplicate res.partner records")
    vendor = (
        vendors.ensure_one()
        if vendors
        else env["res.partner"]
        .sudo()
        .create(  # noqa: F821
            {
                "name": name,
                "ref": ref,
                "is_company": True,
                "supplier_rank": 1,
                "email": f"{ref.lower()}@example.invalid",
                "company_id": False,
            }
        )
    )
    vendor.write(
        {
            "name": name,
            "active": True,
            "supplier_rank": max(vendor.supplier_rank, 1),
            "category_id": [Command.set(tag.ids)],
        }
    )
    return vendor


def _product(code, name, category):
    templates = (
        env["product.template"]
        .sudo()
        .with_context(active_test=False)
        .search([("default_code", "=", code)])  # noqa: F821
    )
    if len(templates) > 1:
        raise RuntimeError("seed found duplicate product.template records")
    template = (
        templates.ensure_one()
        if templates
        else env["product.template"]
        .sudo()
        .create(  # noqa: F821
            {
                "name": name,
                "default_code": code,
                "is_storable": True,
                "purchase_ok": True,
                "sale_ok": False,
                "categ_id": category.id,
                "uom_id": unit_uom.id,
            }
        )
    )
    template.write(
        {
            "active": True,
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


def _remove_obsolete_offers(template, allowed_vendors):
    """Remove superseded supplier configuration without touching order history."""
    obsolete = (
        env["product.supplierinfo"]
        .sudo()
        .search(  # noqa: F821
            [
                ("product_tmpl_id", "=", template.id),
                ("company_id", "=", company.id),
                ("partner_id", "not in", allowed_vendors.ids),
            ]
        )
    )
    if obsolete:
        obsolete.unlink()


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


def _preference(scope, priorities, premium, mode, *, category=None, product=None):
    domain = [
        ("company_id", "=", company.id),
        ("scope", "=", scope),
        ("active", "=", True),
    ]
    if scope == "category":
        domain.append(("product_category_id", "=", category.id))
    if scope == "product":
        domain.append(("product_id", "=", product.id))
    records = env["stockai.procurement.preference"].sudo().search(domain)  # noqa: F821
    if len(records) > 1:
        raise RuntimeError("seed found duplicate preference records")
    values = {
        "company_id": company.id,
        "scope": scope,
        "product_category_id": category.id if category else False,
        "product_id": product.id if product else False,
        "max_price_premium_percent": premium,
        "enforcement_mode": mode,
        "active": True,
    }
    ordered = (
        list(records.priority_ids.sorted("sequence").mapped("criterion"))
        if records
        else []
    )
    if records:
        preference = records.ensure_one()
        relationship_fields = {"product_category_id", "product_id", "company_id"}
        changed = {
            name: value
            for name, value in values.items()
            if name in relationship_fields and preference[name].id != value
        }
        changed.update(
            {
                name: value
                for name, value in values.items()
                if name not in relationship_fields and preference[name] != value
            }
        )
        if ordered != priorities:
            changed["priority_ids"] = [
                Command.clear(),
                *[
                    Command.create({"sequence": index * 10, "criterion": criterion})
                    for index, criterion in enumerate(priorities, start=1)
                ],
            ]
        if changed:
            preference.write(changed)
        return preference
    values["priority_ids"] = [
        Command.create({"sequence": index * 10, "criterion": criterion})
        for index, criterion in enumerate(priorities, start=1)
    ]
    return env["stockai.procurement.preference"].sudo().create(values)  # noqa: F821


def _draft_order(origin, vendor, product, analytic_account, *, quantity, price):
    planned_date = datetime.datetime.now() + datetime.timedelta(days=7)
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
        order.order_line.ensure_one().write(
            {
                "product_qty": quantity,
                "date_planned": planned_date,
                "price_unit": price,
            }
        )
        return order
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


def _inventory_scenario(product, *, demand_quantity):
    """Keep one idempotent on-hand balance and optional confirmed demand."""
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
    if demand_quantity == 0:
        if moves:
            moves.ensure_one()._action_cancel()
        return
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
                    "product_uom_qty": demand_quantity,
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
        moves.ensure_one().write(
            {"date": demand_date, "product_uom_qty": demand_quantity}
        )


approved_tag = _one_or_create(
    "res.partner.category",
    [("name", "=", "Approved Procurement Vendor")],
    {"name": "Approved Procurement Vendor"},
)
vendor_refs = {
    f"{prefix}-VENDOR-FAST",
    f"{prefix}-VENDOR-CHEAP",
    f"{prefix}-VENDOR-STEADY",
}
obsolete_vendors = (
    env["res.partner"]
    .sudo()
    .with_context(active_test=False)
    .search(  # noqa: F821
        [
            ("ref", "like", f"{prefix}-VENDOR-%"),
            ("ref", "not in", sorted(vendor_refs)),
            ("active", "=", True),
        ]
    )
)
if obsolete_vendors:
    obsolete_vendors.write({"active": False})
fast_vendor = _vendor(
    f"{prefix}-VENDOR-FAST", f"{label} Fictional Fast Supplies", approved_tag
)
cheap_vendor = _vendor(
    f"{prefix}-VENDOR-CHEAP", f"{label} Fictional Value Supplies", approved_tag
)
steady_vendor = _vendor(
    f"{prefix}-VENDOR-STEADY", f"{label} Fictional Steady Supplies", approved_tag
)

demo_category = _one_or_create(
    "product.category",
    [("name", "=", f"{prefix} Demo Components")],
    {"name": f"{prefix} Demo Components"},
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

scenario_codes = {
    f"{prefix}-NO-NEED",
    f"{prefix}-CHOICE-3",
    f"{prefix}-CHOICE-2",
    f"{prefix}-NO-OFFER",
}
obsolete_templates = (
    env["product.template"]
    .sudo()
    .with_context(active_test=False)
    .search(  # noqa: F821
        [
            ("default_code", "like", f"{prefix}-%"),
            ("default_code", "not in", sorted(scenario_codes)),
            ("active", "=", True),
        ]
    )
)
if obsolete_templates:
    obsolete_templates.write({"active": False})

no_need_template, no_need_product = _product(
    f"{prefix}-NO-NEED",
    f"{label} Fictional No Replenishment Component",
    demo_category,
)
choice_three_template, choice_three_product = _product(
    f"{prefix}-CHOICE-3",
    f"{label} Fictional Three Eligible Offers Component",
    demo_category,
)
choice_two_template, choice_two_product = _product(
    f"{prefix}-CHOICE-2",
    f"{label} Fictional Two Eligible Offers Component",
    demo_category,
)
no_offer_template, no_offer_product = _product(
    f"{prefix}-NO-OFFER",
    f"{label} Fictional No-Valid-Offer Component",
    demo_category,
)
_preference("company", ["reliability", "delivery", "price"], 25.0, "advisory")
for template, delays in (
    (no_need_template, (1, 2, 3)),
    (choice_three_template, (1, 2, 3)),
    (choice_two_template, (1, 2, 6)),
    (no_offer_template, (5, 6, 7)),
):
    _remove_obsolete_offers(
        template,
        fast_vendor | cheap_vendor | steady_vendor,
    )
    for sequence, (vendor, price, delay) in enumerate(
        zip(
            (fast_vendor, cheap_vendor, steady_vendor),
            (19.0, 16.0, 18.0),
            delays,
            strict=True,
        ),
        start=1,
    ):
        _offer(template, vendor, price=price, delay=delay, sequence=sequence)

_budget(demo_category, analytic_account, 5000.0)
_receipt_and_return(
    f"{prefix}-CASE-RECEIPT-RETURN",
    fast_vendor,
    choice_three_product,
    analytic_account,
)
_inventory_scenario(no_need_product, demand_quantity=0.0)
_inventory_scenario(choice_three_product, demand_quantity=8.0)
_inventory_scenario(choice_two_product, demand_quantity=8.0)
_inventory_scenario(no_offer_product, demand_quantity=8.0)

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
