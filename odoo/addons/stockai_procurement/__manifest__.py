# ruff: noqa: B018

{
    "name": "StockAI Procurement",
    "summary": "Typed procurement policy and atomic purchase-order contracts",
    "version": "19.0.1.0.0",
    "license": "LGPL-3",
    "depends": ["purchase_stock", "account", "analytic", "mail"],
    "data": [
        "security/groups.xml",
        "security/ir.model.access.csv",
        "security/rules.xml",
        "views/preferences.xml",
    ],
    "application": False,
    "installable": True,
}
