# ruff: noqa: F821
"""Verify the four bounded fictional StockAI Odoo demo scenarios."""

from __future__ import annotations

import json
import os

environment = os.environ.get("STOCKAI_ODOO_SEED_ENVIRONMENT", "dev").strip().lower()
if environment not in {"dev", "prod"}:
    raise RuntimeError("seed environment must be dev or prod")

prefix = f"STOCKAI-{environment.upper()}"
company = env.company  # noqa: F821 - supplied by odoo shell
references = {
    "no-replenishment": f"{prefix}-NO-NEED",
    "three-eligible": f"{prefix}-CHOICE-3",
    "two-eligible": f"{prefix}-CHOICE-2",
    "no-valid-offer": f"{prefix}-NO-OFFER",
}
expected_outcomes = {
    "no-replenishment": "skipped",
    "three-eligible": "llm_safe_set_3",
    "two-eligible": "llm_safe_set_2",
    "no-valid-offer": "no_valid_offer",
}

templates = (
    env["product.template"]
    .sudo()
    .search(  # noqa: F821
        [
            ("default_code", "in", list(references.values())),
            ("active", "=", True),
        ]
    )
)
if len(templates) != 4:
    raise RuntimeError("seed verification expected four active demo products")

active_prefixed = (
    env["product.template"]
    .sudo()
    .search_count(  # noqa: F821
        [("default_code", "like", f"{prefix}-%"), ("active", "=", True)]
    )
)
if active_prefixed != 4:
    raise RuntimeError("seed verification found obsolete active demo products")

approved_tag = (
    env["res.partner.category"]
    .sudo()
    .search([("name", "=", "Approved Procurement Vendor")], limit=1)  # noqa: F821
)
if not approved_tag:
    raise RuntimeError("seed verification found no approved vendor tag")

offer_counts = {}
for template in templates:
    offers = (
        env["product.supplierinfo"]
        .sudo()
        .search(  # noqa: F821
            [
                ("product_tmpl_id", "=", template.id),
                ("company_id", "=", company.id),
            ]
        )
    )
    if len(offers) != 3:
        raise RuntimeError("seed verification expected three offers per product")
    if any(approved_tag not in offer.partner_id.category_id for offer in offers):
        raise RuntimeError("seed verification found an unapproved demo vendor")
    offer_counts[template.default_code] = len(offers)
    product = template.product_variant_id.ensure_one()
    orderpoints = (
        env["stock.warehouse.orderpoint"]
        .sudo()
        .search(  # noqa: F821
            [("product_id", "=", product.id), ("company_id", "=", company.id)]
        )
    )
    if len(orderpoints) != 1:
        raise RuntimeError("seed verification expected one reordering rule per product")

for scenario, code in references.items():
    template = templates.filtered(lambda item, value=code: item.default_code == value)
    if len(template) != 1:
        raise RuntimeError("seed verification found a missing stable product reference")
    product = template.product_variant_id.ensure_one()
    demand_count = (
        env["stock.move"]
        .sudo()
        .search_count(  # noqa: F821
            [
                ("origin", "=", f"{prefix} Forecast Demand {code}"),
                ("product_id", "=", product.id),
                ("company_id", "=", company.id),
                ("state", "not in", ["cancel", "done"]),
            ]
        )
    )
    expected_demands = 0 if scenario == "no-replenishment" else 1
    if demand_count != expected_demands:
        raise RuntimeError("seed verification found an incorrect demand scenario")

budget_count = (
    env["stockai.procurement.budget"]
    .sudo()
    .search_count(  # noqa: F821
        [
            ("company_id", "=", company.id),
            ("product_category_id", "=", templates[0].categ_id.id),
            ("active", "=", True),
        ]
    )
)
preference_count = (
    env["stockai.procurement.preference"]
    .sudo()
    .search_count(  # noqa: F821
        [
            ("company_id", "=", company.id),
            ("scope", "=", "company"),
            ("active", "=", True),
        ]
    )
)
if budget_count != 1 or preference_count != 1:
    raise RuntimeError("seed verification found incomplete policy configuration")

vendor_ids = {
    offer.partner_id.id
    for template in templates
    for offer in template.seller_ids.filtered(lambda item: item.company_id == company)
}
counts = {
    "active_products": active_prefixed,
    "offers": sum(offer_counts.values()),
    "approved_vendors": len(vendor_ids),
}
if counts["approved_vendors"] != 3:
    raise RuntimeError("seed verification expected exactly three demo vendors")

print(
    json.dumps(
        {
            "counts": counts,
            "environment": environment,
            "offers_per_product": 3,
            "references": references,
            "scenario_outcomes": expected_outcomes,
            "scenarios": sorted(references),
            "status": "ok",
        },
        sort_keys=True,
    )
)
