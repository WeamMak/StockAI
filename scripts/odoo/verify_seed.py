# ruff: noqa: F821
"""Verify stable references and counts for the fictional StockAI Odoo seed."""

from __future__ import annotations

import json
import os

environment = os.environ.get("STOCKAI_ODOO_SEED_ENVIRONMENT", "dev").strip().lower()
if environment not in {"dev", "prod"}:
    raise RuntimeError("seed environment must be dev or prod")

prefix = f"STOCKAI-{environment.upper()}"
company = env.company  # noqa: F821 - supplied by odoo shell
references = {
    "happy": f"{prefix}-CASE-HAPPY",
    "no-valid-offer": f"{prefix}-NO-OFFER",
    "over-budget": f"{prefix}-CASE-OVER-BUDGET",
    "receipt-return": f"{prefix}-CASE-RECEIPT-RETURN",
}


def _count(model_name, domain):
    return env[model_name].sudo().search_count(domain)  # noqa: F821


missing = []
for origin in (
    references["happy"],
    references["over-budget"],
    references["receipt-return"],
):
    if (
        _count(
            "purchase.order", [("origin", "=", origin), ("company_id", "=", company.id)]
        )
        != 1
    ):
        missing.append(origin)
if (
    _count("product.template", [("default_code", "=", references["no-valid-offer"])])
    != 1
):
    missing.append(references["no-valid-offer"])

receipt_order = (
    env["purchase.order"]
    .sudo()
    .search(  # noqa: F821
        [
            ("origin", "=", references["receipt-return"]),
            ("company_id", "=", company.id),
        ],
        limit=1,
    )
)
environment_preferences = (
    env["stockai.procurement.preference"]
    .sudo()
    .search(  # noqa: F821
        [("company_id", "=", company.id), ("active", "=", True)]
    )
    .filtered(
        lambda preference: (
            preference.scope == "company"
            or (
                preference.scope == "category"
                and preference.product_category_id.name.startswith(prefix)
            )
            or (
                preference.scope == "product"
                and preference.product_id.default_code.startswith(prefix)
            )
        )
    )
)
counts = {
    "budgets": _count(
        "stockai.procurement.budget",
        [
            ("company_id", "=", company.id),
            ("product_category_id.name", "like", f"{prefix}%"),
            ("active", "=", True),
        ],
    ),
    "completed_receipts": _count(
        "stock.picking",
        [
            ("id", "in", receipt_order.picking_ids.ids),
            ("return_id", "=", False),
            ("state", "=", "done"),
        ],
    ),
    "open_purchase_orders": _count(
        "purchase.order",
        [
            ("origin", "like", f"{prefix}-CASE-%"),
            ("state", "in", ["draft", "sent"]),
            ("company_id", "=", company.id),
        ],
    ),
    "products": _count("product.template", [("default_code", "like", f"{prefix}-%")]),
    "preferences": len(environment_preferences),
    "returns": _count(
        "stock.picking",
        [
            ("id", "in", receipt_order.picking_ids.ids),
            ("return_id", "!=", False),
            ("state", "=", "done"),
        ],
    ),
    "vendors": _count("res.partner", [("ref", "like", f"{prefix}-VENDOR-%")]),
}
if missing:
    raise RuntimeError("seed verification found missing stable references")
if (
    counts["budgets"] < 2
    or counts["preferences"] != 3
    or counts["products"] < 3
    or counts["vendors"] < 3
):
    raise RuntimeError("seed verification found incomplete configuration records")
if (
    counts["open_purchase_orders"] < 2
    or counts["completed_receipts"] < 1
    or counts["returns"] < 1
):
    raise RuntimeError(
        "seed verification found incomplete purchase or inventory records"
    )

print(
    json.dumps(
        {
            "counts": counts,
            "environment": environment,
            "references": references,
            "scenarios": sorted(references),
            "status": "ok",
        },
        sort_keys=True,
    )
)
