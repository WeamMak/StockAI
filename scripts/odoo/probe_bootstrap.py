"""Create the disposable T10 identity and fixtures inside ``odoo shell``.

This file is intentionally finite and idempotent. The raw key is written once
to a container tmpfs file and is never printed.
"""

from __future__ import annotations

import datetime
import json
import os
from pathlib import Path

from odoo.fields import Command

LOGIN = os.environ.get("ODOO_CONTRACT_LOGIN", "stockai-contract@example.invalid")
KEY_FILE = Path(
    os.environ.get("ODOO_CONTRACT_KEY_FILE", "/run/stockai-contract/api-key")
)
KEY_NAME = "stockai-t10-contract"
CONFIGURATION_LOGIN = "stockai-contract-config@example.invalid"
CONFIGURATION_KEY_FILE = Path("/run/stockai-contract/config-api-key")
CONFIGURATION_KEY_NAME = "stockai-t11a-config-contract"
INTEGRATION_GROUP_XML_ID = "stockai_procurement.group_stockai_procurement_integration"
CONFIGURATION_GROUP_XML_ID = (
    "stockai_procurement.group_stockai_procurement_config_admin"
)


def _active_named_keys(user_id: int, key_name: str):
    return env["res.users.apikeys"].search(  # noqa: F821 - supplied by odoo shell
        [
            ("user_id", "=", user_id),
            ("name", "=", key_name),
            "|",
            ("expiration_date", "=", False),
            ("expiration_date", ">=", datetime.datetime.now()),
        ]
    )


def _identity(*, login, name, group_xml_id, key_name, key_file):
    users = env["res.users"].sudo()  # noqa: F821 - supplied by odoo shell
    matching_users = users.search([("login", "=", login)])
    if len(matching_users) > 1:
        raise RuntimeError("contract bootstrap found duplicate users")

    status = "existing"
    group = env.ref(group_xml_id)  # noqa: F821 - supplied by odoo shell
    if not matching_users:
        matching_users = users.create(
            {
                "name": name,
                "login": login,
                "email": login,
                "active": True,
                "group_ids": [Command.set([group.id])],
            }
        )
        status = "created"

    user = matching_users.ensure_one()
    if set(user.group_ids.ids) != {group.id}:
        user.write({"group_ids": [Command.set([group.id])]})
    active_keys = _active_named_keys(user.id, key_name)

    if key_file.exists():
        raw_key = key_file.read_text(encoding="utf-8").strip()
    else:
        if active_keys:
            raise RuntimeError(
                "active contract key exists but its raw value is unavailable"
            )
        expiration = datetime.datetime.now() + datetime.timedelta(hours=12)
        raw_key = (
            env["res.users.apikeys"]  # noqa: F821 - supplied by odoo shell
            .with_user(user)
            ._generate(scope="rpc", name=key_name, expiration_date=expiration)
        )
        descriptor = os.open(key_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as key_stream:
            key_stream.write(raw_key)
        active_keys = _active_named_keys(user.id, key_name)

    authenticated_user_id = env[  # noqa: F821
        "res.users.apikeys"
    ]._check_credentials(scope="rpc", key=raw_key)
    if authenticated_user_id != user.id:
        raise RuntimeError("contract key file does not authenticate its user")
    if len(active_keys) != 1:
        raise RuntimeError("contract bootstrap expected one active named key")
    if key_file.stat().st_mode & 0o777 != 0o600:
        raise RuntimeError("contract key file permissions are not 0600")
    return status, user, active_keys


status, user, active_keys = _identity(
    login=LOGIN,
    name="StockAI Contract Integration",
    group_xml_id=INTEGRATION_GROUP_XML_ID,
    key_name=KEY_NAME,
    key_file=KEY_FILE,
)
configuration_status, configuration_user, configuration_keys = _identity(
    login=CONFIGURATION_LOGIN,
    name="StockAI Contract Configuration Administrator",
    group_xml_id=CONFIGURATION_GROUP_XML_ID,
    key_name=CONFIGURATION_KEY_NAME,
    key_file=CONFIGURATION_KEY_FILE,
)


def _one_or_create(model_name, domain, values):
    records = env[model_name].sudo().search(domain)  # noqa: F821
    if len(records) > 1:
        raise RuntimeError(f"contract bootstrap found duplicate {model_name} records")
    return records or env[model_name].sudo().create(values)  # noqa: F821


company = env.company  # noqa: F821
unit_uom = env.ref("uom.product_uom_unit")  # noqa: F821
vendor_tag = _one_or_create(
    "res.partner.category",
    [("name", "=", "StockAI T10 Approved Vendor")],
    {"name": "StockAI T10 Approved Vendor"},
)
vendor = _one_or_create(
    "res.partner",
    [("ref", "=", "STOCKAI-T10-VENDOR")],
    {
        "name": "StockAI T10 Fictional Supplies",
        "ref": "STOCKAI-T10-VENDOR",
        "is_company": True,
        "email": "orders@example.invalid",
        "supplier_rank": 1,
        "category_id": [Command.set(vendor_tag.ids)],
        "company_id": False,
    },
)
product_category = _one_or_create(
    "product.category",
    [("name", "=", "StockAI T10 Components")],
    {"name": "StockAI T10 Components"},
)
product_template = _one_or_create(
    "product.template",
    [("default_code", "=", "STOCKAI-T10-PRODUCT")],
    {
        "name": "StockAI T10 Fictional Component",
        "default_code": "STOCKAI-T10-PRODUCT",
        "is_storable": True,
        "purchase_ok": True,
        "sale_ok": False,
        "categ_id": product_category.id,
        "uom_id": unit_uom.id,
    },
)
product = product_template.product_variant_id.ensure_one()
supplierinfo = _one_or_create(
    "product.supplierinfo",
    [
        ("partner_id", "=", vendor.id),
        ("product_tmpl_id", "=", product_template.id),
    ],
    {
        "partner_id": vendor.id,
        "product_tmpl_id": product_template.id,
        "product_uom_id": unit_uom.id,
        "min_qty": 1.0,
        "price": 12.5,
        "discount": 5.0,
        "currency_id": company.currency_id.id,
        "delay": 3,
        "sequence": 1,
        "company_id": company.id,
    },
)
warehouse = (
    env["stock.warehouse"]  # noqa: F821 - supplied by odoo shell
    .sudo()
    .search(  # noqa: F821
        [("company_id", "=", company.id)], limit=1
    )
)
if not warehouse:
    raise RuntimeError("contract bootstrap found no warehouse")
orderpoint = _one_or_create(
    "stock.warehouse.orderpoint",
    [
        ("product_id", "=", product.id),
        ("location_id", "=", warehouse.lot_stock_id.id),
        ("company_id", "=", company.id),
    ],
    {
        "name": "StockAI T10 Reordering Rule",
        "trigger": "manual",
        "product_id": product.id,
        "location_id": warehouse.lot_stock_id.id,
        "product_min_qty": 5.0,
        "product_max_qty": 20.0,
        "replenishment_uom_id": unit_uom.id,
        "company_id": company.id,
    },
)
analytic_plan = _one_or_create(
    "account.analytic.plan",
    [("name", "=", "StockAI T10 Procurement")],
    {"name": "StockAI T10 Procurement"},
)
analytic_account = _one_or_create(
    "account.analytic.account",
    [("code", "=", "STOCKAI-T10")],
    {
        "name": "StockAI T10 Procurement",
        "code": "STOCKAI-T10",
        "plan_id": analytic_plan.id,
        "company_id": company.id,
    },
)

fixture = {
    "analytic_account_id": analytic_account.id,
    "analytic_plan_id": analytic_plan.id,
    "company_id": company.id,
    "currency_id": company.currency_id.id,
    "orderpoint_id": orderpoint.id,
    "product_id": product.id,
    "product_category_id": product_category.id,
    "product_template_id": product_template.id,
    "supplierinfo_id": supplierinfo.id,
    "unit_uom_id": unit_uom.id,
    "vendor_id": vendor.id,
    "warehouse_id": warehouse.id,
}

env.cr.commit()  # noqa: F821 - make the finite bootstrap durable before exit
print(
    json.dumps(
        {
            "active_named_key_count": len(active_keys),
            "configuration_active_named_key_count": len(configuration_keys),
            "configuration_status": configuration_status,
            "configuration_user_id": configuration_user.id,
            "fixture": fixture,
            "status": status,
            "user_id": user.id,
        },
        sort_keys=True,
    )
)
