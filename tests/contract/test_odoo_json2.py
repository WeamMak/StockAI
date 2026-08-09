"""Task 10 contracts for the pinned Odoo 19 JSON-2 runtime."""

from __future__ import annotations

import json
import os
import subprocess
import time
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from tests.contract.conftest import OdooContractStack

PROJECT_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = PROJECT_ROOT / "compose.odoo.yaml"
ODOO_IMAGE = "stockai-odoo:t11a-local"
POSTGRES_IMAGE = (
    "postgres@sha256:e8db9bd3e9e1751eb639fb17be53cc6d1b62a322adf75b99e791767a7a16ce69"
)
CONTRACT_DATABASE = "stockai_t10_contract"
FICTIONAL_DATABASE_PASSWORD = "fictional-t10-postgres-password"
REQUIRED_MODULES = {
    "purchase",
    "stock",
    "purchase_stock",
    "contacts",
    "account",
    "analytic",
    "stockai_procurement",
}
REQUIRED_MODEL_FIELDS = {
    "product.product": {
        "active",
        "is_storable",
        "categ_id",
        "purchase_ok",
        "uom_id",
        "uom_ids",
        "seller_ids",
    },
    "stock.warehouse.orderpoint": {
        "active",
        "trigger",
        "warehouse_id",
        "location_id",
        "product_id",
        "product_min_qty",
        "product_max_qty",
        "replenishment_uom_id",
        "route_id",
        "company_id",
        "qty_on_hand",
        "qty_forecast",
        "qty_to_order",
    },
    "stock.quant": {
        "product_id",
        "location_id",
        "lot_id",
        "package_id",
        "owner_id",
        "quantity",
        "reserved_quantity",
        "available_quantity",
        "company_id",
    },
    "stock.move": {
        "product_id",
        "product_uom_qty",
        "quantity",
        "product_uom",
        "date",
        "date_deadline",
        "location_id",
        "location_dest_id",
        "state",
        "picking_id",
        "reservation_date",
        "origin_returned_move_id",
        "returned_move_ids",
        "orderpoint_id",
    },
    "res.partner": {
        "name",
        "ref",
        "active",
        "is_company",
        "parent_id",
        "company_id",
        "category_id",
        "street",
        "street2",
        "zip",
        "city",
        "state_id",
        "country_id",
        "email",
        "phone",
        "vat",
        "supplier_rank",
        "property_supplier_payment_term_id",
    },
    "res.partner.category": {"name", "active"},
    "product.supplierinfo": {
        "partner_id",
        "product_tmpl_id",
        "product_id",
        "product_uom_id",
        "min_qty",
        "price",
        "discount",
        "currency_id",
        "date_start",
        "date_end",
        "company_id",
        "delay",
        "sequence",
    },
    "purchase.order": {
        "name",
        "origin",
        "partner_ref",
        "partner_id",
        "date_order",
        "date_approve",
        "date_planned",
        "state",
        "order_line",
        "currency_id",
        "payment_term_id",
        "company_id",
        "amount_untaxed",
        "amount_tax",
        "amount_total",
        "picking_ids",
        "picking_type_id",
        "receipt_status",
        "effective_date",
        "write_date",
    },
    "purchase.order.line": {
        "order_id",
        "product_id",
        "product_qty",
        "product_uom_id",
        "date_planned",
        "price_unit",
        "discount",
        "tax_ids",
        "analytic_distribution",
        "qty_received",
        "qty_invoiced",
        "qty_to_invoice",
        "move_ids",
    },
    "stock.picking": {
        "name",
        "origin",
        "state",
        "picking_type_id",
        "picking_type_code",
        "scheduled_date",
        "date_done",
        "location_id",
        "location_dest_id",
        "move_ids",
        "move_line_ids",
        "backorder_id",
        "return_id",
    },
    "stock.return.picking": {"picking_id", "product_return_moves"},
    "stock.return.picking.line": {
        "product_id",
        "quantity",
        "uom_id",
        "move_id",
        "wizard_id",
    },
    "account.analytic.plan": {"name"},
    "account.analytic.account": {
        "name",
        "code",
        "active",
        "plan_id",
        "company_id",
        "partner_id",
        "balance",
        "debit",
        "credit",
    },
    "account.analytic.line": {
        "name",
        "date",
        "amount",
        "unit_amount",
        "product_uom_id",
        "partner_id",
        "company_id",
        "currency_id",
    },
}
REQUIRED_MODEL_METHODS = {
    "purchase.order": {
        "button_confirm",
        "button_approve",
        "button_draft",
        "button_cancel",
    },
    "stock.picking": {
        "action_confirm",
        "action_assign",
        "action_cancel",
        "button_validate",
    },
    "stock.return.picking": {
        "action_create_returns",
        "action_create_returns_all",
    },
    "stock.warehouse.orderpoint": {
        "action_replenish",
        "action_replenish_auto",
    },
}


def _environment() -> dict[str, str]:
    return {
        **os.environ,
        "ODOO_CONTRACT_DATABASE": CONTRACT_DATABASE,
        "ODOO_CONTRACT_PORT": "18069",
        "ODOO_CONTRACT_POSTGRES_PASSWORD": FICTIONAL_DATABASE_PASSWORD,
    }


def _run(
    command: list[str],
    *,
    environment: Mapping[str, str],
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        env=environment,
        check=check,
        capture_output=True,
        text=True,
        timeout=60,
    )


def _rendered_compose() -> dict[str, object]:
    completed = _run(
        [
            "docker",
            "compose",
            "--file",
            str(COMPOSE_FILE),
            "config",
            "--format",
            "json",
        ],
        environment=_environment(),
    )
    rendered = json.loads(completed.stdout)
    assert isinstance(rendered, dict)
    return rendered


def test_odoo_compose_is_pinned_private_bounded_and_disposable() -> None:
    rendered = _rendered_compose()
    services = rendered["services"]
    networks = rendered["networks"]
    volumes = rendered["volumes"]

    assert isinstance(services, dict)
    assert set(services) == {"odoo", "postgres"}
    assert services["odoo"]["image"] == ODOO_IMAGE
    assert services["odoo"]["build"]["context"] == str(PROJECT_ROOT)
    assert services["odoo"]["build"]["dockerfile"] == "docker/odoo.Dockerfile"
    assert services["postgres"]["image"] == POSTGRES_IMAGE

    assert isinstance(networks, dict)
    assert set(networks) == {"odoo-contract-db", "odoo-contract-edge"}
    assert networks["odoo-contract-db"]["internal"] is True
    assert networks["odoo-contract-edge"].get("internal", False) is False
    assert set(services["postgres"]["networks"]) == {"odoo-contract-db"}
    assert set(services["odoo"]["networks"]) == {
        "odoo-contract-db",
        "odoo-contract-edge",
    }
    assert isinstance(volumes, dict)
    assert set(volumes) == {"odoo-contract-filestore", "odoo-contract-postgres"}

    for service in services.values():
        assert service["read_only"] is True
        assert service["healthcheck"]["test"]
        assert service["deploy"]["resources"]["limits"]["cpus"]
        assert service["deploy"]["resources"]["limits"]["memory"]
        assert service["cap_drop"] == ["ALL"]
        assert service["security_opt"] == ["no-new-privileges:true"]

    assert set(services["postgres"]["cap_add"]) == {
        "CHOWN",
        "DAC_OVERRIDE",
        "FOWNER",
        "SETGID",
        "SETUID",
    }
    assert "cap_add" not in services["odoo"]

    assert "ports" not in services["postgres"]
    assert services["postgres"]["environment"]["POSTGRES_PASSWORD"] == (
        FICTIONAL_DATABASE_PASSWORD
    )
    assert services["odoo"]["environment"]["PASSWORD"] == (FICTIONAL_DATABASE_PASSWORD)
    assert services["odoo"]["ports"] == [
        {
            "mode": "ingress",
            "target": 8069,
            "published": "18069",
            "host_ip": "127.0.0.1",
            "protocol": "tcp",
        }
    ]
    assert services["odoo"]["depends_on"]["postgres"]["condition"] == (
        "service_healthy"
    )
    assert services["odoo"]["environment"]["ODOO_CONTRACT_LOGIN"] == (
        "stockai-contract@example.invalid"
    )
    assert any(
        mount["type"] == "bind"
        and mount["target"] == "/opt/stockai/probe_bootstrap.py"
        and mount["read_only"] is True
        for mount in services["odoo"]["volumes"]
    )

    command = services["odoo"]["command"]
    assert "--no-database-list" in command
    assert f"--database={CONTRACT_DATABASE}" in command
    installed_modules = next(
        argument.removeprefix("--init=")
        for argument in command
        if argument.startswith("--init=")
    )
    assert set(installed_modules.split(",")) == REQUIRED_MODULES
    assert any(
        mount.startswith("/run/stockai-contract:")
        and "mode=0700" in mount
        and "uid=100" in mount
        and "gid=101" in mount
        for mount in services["odoo"]["tmpfs"]
    )


def test_orm_bootstrap_is_idempotent_and_keeps_the_key_private(
    running_odoo_contract: OdooContractStack,
) -> None:
    assert running_odoo_contract.first_bootstrap["status"] == "created"
    assert running_odoo_contract.second_bootstrap["status"] == "existing"
    assert (
        running_odoo_contract.first_bootstrap["user_id"]
        == (running_odoo_contract.second_bootstrap["user_id"])
    )
    assert running_odoo_contract.first_bootstrap["active_named_key_count"] == 1
    assert running_odoo_contract.second_bootstrap["active_named_key_count"] == 1
    assert running_odoo_contract.first_bootstrap["configuration_status"] == "created"
    assert running_odoo_contract.second_bootstrap["configuration_status"] == "existing"
    assert (
        running_odoo_contract.first_bootstrap["configuration_user_id"]
        == running_odoo_contract.second_bootstrap["configuration_user_id"]
    )
    assert (
        running_odoo_contract.first_bootstrap["configuration_active_named_key_count"]
        == 1
    )
    assert running_odoo_contract.key_mode == "600"
    assert running_odoo_contract.configuration_key_mode == "600"
    assert len(running_odoo_contract.api_key) >= 32


def test_json2_probe_never_exposes_remote_debug_details() -> None:
    from scripts.odoo.probe_contract import Json2Client, ProbeError

    secret_debug = "traceback includes secret-contract-value"

    def remote_error(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            500,
            json={
                "name": "builtins.RuntimeError",
                "message": "unsafe internal message",
                "debug": secret_debug,
            },
        )

    with Json2Client(
        base_url="http://odoo.invalid",
        database=CONTRACT_DATABASE,
        api_key="fictional-key",
        transport=httpx.MockTransport(remote_error),
    ) as client:
        with pytest.raises(ProbeError) as caught:
            client.call("res.users", "context_get", {})

    assert caught.value.status_code == 500
    assert str(caught.value) == "Odoo JSON-2 request failed (HTTP 500)"
    assert secret_debug not in str(caught.value)
    assert "unsafe internal message" not in str(caught.value)


def test_json2_auth_database_selection_and_version_are_real(
    running_odoo_contract: OdooContractStack,
) -> None:
    from scripts.odoo.probe_contract import Json2Client, ProbeError

    with Json2Client(
        base_url=running_odoo_contract.base_url,
        database=running_odoo_contract.database,
        api_key=running_odoo_contract.api_key,
    ) as client:
        context = client.call("res.users", "context_get", {})

    assert isinstance(context, dict)
    assert context["uid"] == running_odoo_contract.first_bootstrap["user_id"]

    version = httpx.get(f"{running_odoo_contract.base_url}/web/version", timeout=10)
    assert version.status_code == 200
    assert version.json()["version"].startswith("19.0")

    endpoint = f"{running_odoo_contract.base_url}/json/2/res.users/context_get"
    missing = httpx.post(
        endpoint,
        headers={"X-Odoo-Database": running_odoo_contract.database},
        json={},
        timeout=10,
    )
    wrong = httpx.post(
        endpoint,
        headers={
            "Authorization": "bearer definitely-wrong",
            "X-Odoo-Database": running_odoo_contract.database,
        },
        json={},
        timeout=10,
    )
    assert missing.status_code == 401
    assert wrong.status_code == 401

    with Json2Client(
        base_url=running_odoo_contract.base_url,
        database="wrong_database",
        api_key=running_odoo_contract.api_key,
    ) as wrong_database_client:
        with pytest.raises(ProbeError) as caught:
            wrong_database_client.call("res.users", "context_get", {})
    assert caught.value.status_code == 404


def test_documented_models_fields_methods_and_acls_match_the_runtime(
    running_odoo_contract: OdooContractStack,
) -> None:
    from scripts.odoo.probe_contract import Json2Client

    with Json2Client(
        base_url=running_odoo_contract.base_url,
        database=running_odoo_contract.database,
        api_key=running_odoo_contract.api_key,
    ) as client:
        missing_doc_fields: dict[str, set[str]] = {}
        missing_fields_get_fields: dict[str, set[str]] = {}
        for model, required_fields in REQUIRED_MODEL_FIELDS.items():
            model_doc = client.doc(model)
            assert model_doc["model"] == model
            if missing := required_fields - set(model_doc["fields"]):
                missing_doc_fields[model] = missing

            fields_get = client.call(
                model,
                "fields_get",
                {
                    "allfields": sorted(required_fields),
                    "attributes": [
                        "type",
                        "required",
                        "readonly",
                        "selection",
                        "relation",
                    ],
                },
            )
            assert all(
                isinstance(metadata.get("type"), str)
                for metadata in fields_get.values()
            )
            if missing := required_fields - set(fields_get):
                missing_fields_get_fields[model] = missing

        missing_methods: dict[str, set[str]] = {}
        for model, required_methods in REQUIRED_MODEL_METHODS.items():
            model_doc = client.doc(model)
            if missing := required_methods - set(model_doc["methods"]):
                missing_methods[model] = missing

    assert not missing_doc_fields
    assert not missing_fields_get_fields
    assert not missing_methods


def test_custom_budget_and_atomic_actions_extend_the_standard_contract(
    running_odoo_contract: OdooContractStack,
) -> None:
    from scripts.odoo.probe_contract import Json2Client

    with Json2Client(
        base_url=running_odoo_contract.base_url,
        database=running_odoo_contract.database,
        api_key=running_odoo_contract.api_key,
        timeout=30,
    ) as client:
        index = client.doc()
        purchase_order_doc = client.doc("purchase.order")
        budget_doc = client.doc("stockai.procurement.budget")

    assert REQUIRED_MODULES <= set(index["modules"])
    assert "account_budget" not in index["modules"]
    assert "stockai.procurement.budget" in {model["model"] for model in index["models"]}
    assert budget_doc["model"] == "stockai.procurement.budget"

    for method_name in (
        "action_stockai_update_draft",
        "action_stockai_cancel_draft",
        "action_stockai_confirm",
    ):
        method = purchase_order_doc["methods"][method_name]
        assert "expected" in method["parameters"]


def test_fictional_reordering_supplier_and_analytic_records_are_readable(
    running_odoo_contract: OdooContractStack,
) -> None:
    from scripts.odoo.probe_contract import Json2Client

    fixture = running_odoo_contract.first_bootstrap["fixture"]
    assert isinstance(fixture, dict)
    assert fixture == running_odoo_contract.second_bootstrap["fixture"]

    with Json2Client(
        base_url=running_odoo_contract.base_url,
        database=running_odoo_contract.database,
        api_key=running_odoo_contract.api_key,
    ) as client:
        product = client.call(
            "product.product",
            "read",
            {
                "ids": [fixture["product_id"]],
                "fields": [
                    "default_code",
                    "is_storable",
                    "purchase_ok",
                    "seller_ids",
                ],
            },
        )[0]
        supplier = client.call(
            "product.supplierinfo",
            "read",
            {
                "ids": [fixture["supplierinfo_id"]],
                "fields": [
                    "partner_id",
                    "min_qty",
                    "price",
                    "discount",
                    "currency_id",
                    "delay",
                ],
            },
        )[0]
        orderpoint = client.call(
            "stock.warehouse.orderpoint",
            "read",
            {
                "ids": [fixture["orderpoint_id"]],
                "fields": [
                    "trigger",
                    "product_min_qty",
                    "product_max_qty",
                    "replenishment_uom_id",
                    "qty_on_hand",
                    "qty_forecast",
                    "qty_to_order",
                ],
            },
        )[0]
        analytic_account = client.call(
            "account.analytic.account",
            "read",
            {
                "ids": [fixture["analytic_account_id"]],
                "fields": ["name", "code", "plan_id", "company_id"],
            },
        )[0]

    assert product["default_code"] == "STOCKAI-T10-PRODUCT"
    assert product["is_storable"] is True
    assert product["purchase_ok"] is True
    assert product["seller_ids"] == [fixture["supplierinfo_id"]]
    assert supplier["partner_id"][0] == fixture["vendor_id"]
    assert supplier["min_qty"] == 1.0
    assert supplier["price"] == 12.5
    assert supplier["discount"] == 5.0
    assert supplier["delay"] == 3
    assert orderpoint["trigger"] == "manual"
    assert orderpoint["product_min_qty"] == 5.0
    assert orderpoint["product_max_qty"] == 20.0
    assert orderpoint["qty_on_hand"] == 0.0
    assert orderpoint["qty_forecast"] == 0.0
    assert orderpoint["qty_to_order"] == 20.0
    assert analytic_account["name"] == "StockAI T10 Procurement"
    assert analytic_account["code"] == "STOCKAI-T10"
    assert analytic_account["plan_id"][0] == fixture["analytic_plan_id"]
    assert analytic_account["company_id"][0] == fixture["company_id"]


def test_po_receipt_backorder_return_and_revision_behaviors_are_real(
    running_odoo_contract: OdooContractStack,
) -> None:
    from scripts.odoo.probe_contract import Json2Client

    fixture = running_odoo_contract.first_bootstrap["fixture"]
    assert isinstance(fixture, dict)
    planned_date = (datetime.now(UTC) + timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")

    with Json2Client(
        base_url=running_odoo_contract.base_url,
        database=running_odoo_contract.database,
        api_key=running_odoo_contract.api_key,
    ) as client:

        def create_po(origin: str, quantity: float) -> int:
            created = client.call(
                "purchase.order",
                "create",
                {
                    "vals_list": [
                        {
                            "partner_id": fixture["vendor_id"],
                            "origin": origin,
                            "partner_ref": f"VENDOR-{origin}",
                            "company_id": fixture["company_id"],
                            "order_line": [
                                [
                                    0,
                                    0,
                                    {
                                        "product_id": fixture["product_id"],
                                        "product_qty": quantity,
                                        "product_uom_id": fixture["unit_uom_id"],
                                        "date_planned": planned_date,
                                        "price_unit": 12.5,
                                        "discount": 5.0,
                                        "tax_ids": [[6, 0, []]],
                                        "analytic_distribution": {
                                            str(fixture["analytic_account_id"]): 100.0
                                        },
                                    },
                                ]
                            ],
                        }
                    ]
                },
            )
            assert isinstance(created, list) and len(created) == 1
            return int(created[0])

        po_id = create_po("stockai-case-t10-receipt", 8.0)
        purchase_order = client.call(
            "purchase.order",
            "read",
            {
                "ids": [po_id],
                "fields": [
                    "origin",
                    "partner_ref",
                    "state",
                    "write_date",
                    "order_line",
                    "amount_untaxed",
                    "amount_total",
                ],
            },
        )[0]
        line_id = purchase_order["order_line"][0]
        line = client.call(
            "purchase.order.line",
            "read",
            {
                "ids": [line_id],
                "fields": [
                    "product_id",
                    "product_qty",
                    "product_uom_id",
                    "price_unit",
                    "discount",
                    "analytic_distribution",
                ],
            },
        )[0]

        assert purchase_order["origin"] == "stockai-case-t10-receipt"
        assert purchase_order["partner_ref"] == ("VENDOR-stockai-case-t10-receipt")
        assert purchase_order["state"] == "draft"
        assert purchase_order["amount_untaxed"] == 95.0
        assert purchase_order["amount_total"] == 95.0
        assert line["product_id"][0] == fixture["product_id"]
        assert line["product_qty"] == 8.0
        assert line["product_uom_id"][0] == fixture["unit_uom_id"]
        assert line["price_unit"] == 12.5
        assert line["discount"] == 5.0
        assert line["analytic_distribution"] == {
            str(fixture["analytic_account_id"]): 100.0
        }

        first_revision = purchase_order["write_date"]
        time.sleep(1.05)
        assert (
            client.call(
                "purchase.order",
                "write",
                {
                    "ids": [po_id],
                    "vals": {"partner_ref": "VENDOR-REVISION-2"},
                },
            )
            is True
        )
        changed_order = client.call(
            "purchase.order",
            "read",
            {"ids": [po_id], "fields": ["partner_ref", "write_date"]},
        )[0]
        assert changed_order["partner_ref"] == "VENDOR-REVISION-2"
        assert changed_order["write_date"] != first_revision

        client.call("purchase.order", "button_confirm", {"ids": [po_id]})
        confirmed_order = client.call(
            "purchase.order",
            "read",
            {"ids": [po_id], "fields": ["name", "state", "picking_ids"]},
        )[0]
        assert confirmed_order["state"] == "purchase"
        assert len(confirmed_order["picking_ids"]) == 1
        receipt_id = confirmed_order["picking_ids"][0]

        receipt = client.call(
            "stock.picking",
            "read",
            {
                "ids": [receipt_id],
                "fields": ["origin", "state", "move_ids", "backorder_id"],
            },
        )[0]
        assert receipt["origin"] == confirmed_order["name"]
        assert len(receipt["move_ids"]) == 1
        received_move_id = receipt["move_ids"][0]
        assert (
            client.call(
                "stock.move",
                "write",
                {"ids": [received_move_id], "vals": {"quantity": 3.0}},
            )
            is True
        )

        backorder_action = client.call(
            "stock.picking", "button_validate", {"ids": [receipt_id]}
        )
        assert backorder_action["res_model"] == "stock.backorder.confirmation"
        backorder_context = backorder_action["context"]
        wizard_defaults = client.call(
            "stock.backorder.confirmation",
            "default_get",
            {
                "fields": [
                    "pick_ids",
                    "show_transfers",
                    "backorder_confirmation_line_ids",
                ],
                "context": backorder_context,
            },
        )
        created_wizard = client.call(
            "stock.backorder.confirmation",
            "create",
            {"vals_list": [wizard_defaults], "context": backorder_context},
        )
        client.call(
            "stock.backorder.confirmation",
            "process",
            {"ids": created_wizard, "context": backorder_context},
        )
        completed_receipt = client.call(
            "stock.picking",
            "read",
            {"ids": [receipt_id], "fields": ["state", "date_done"]},
        )[0]
        assert completed_receipt["state"] == "done"
        assert completed_receipt["date_done"]

        backorders = client.call(
            "stock.picking",
            "search_read",
            {
                "domain": [["backorder_id", "=", receipt_id]],
                "fields": ["id", "state", "backorder_id", "move_ids"],
                "limit": 2,
            },
        )
        assert len(backorders) == 1
        assert backorders[0]["backorder_id"][0] == receipt_id
        remaining_move = client.call(
            "stock.move",
            "read",
            {
                "ids": backorders[0]["move_ids"],
                "fields": ["product_uom_qty", "quantity"],
            },
        )[0]
        assert remaining_move["product_uom_qty"] == 5.0

        return_wizard = client.call(
            "stock.return.picking",
            "create",
            {"vals_list": [{"picking_id": receipt_id}]},
        )
        return_action = client.call(
            "stock.return.picking",
            "action_create_returns_all",
            {"ids": return_wizard},
        )
        return_picking_id = return_action["res_id"]
        return_picking = client.call(
            "stock.picking",
            "read",
            {
                "ids": [return_picking_id],
                "fields": ["return_id", "state", "move_ids"],
            },
        )[0]
        assert return_picking["return_id"][0] == receipt_id
        return_move = client.call(
            "stock.move",
            "read",
            {
                "ids": return_picking["move_ids"],
                "fields": ["origin_returned_move_id", "product_uom_qty"],
            },
        )[0]
        assert return_move["origin_returned_move_id"][0] == received_move_id
        assert return_move["product_uom_qty"] == 3.0

        cancelled_po_id = create_po("stockai-case-t10-cancel", 2.0)
        client.call("purchase.order", "button_cancel", {"ids": [cancelled_po_id]})
        cancelled = client.call(
            "purchase.order",
            "read",
            {"ids": [cancelled_po_id], "fields": ["state"]},
        )[0]
        assert cancelled["state"] == "cancel"
        client.call("purchase.order", "button_draft", {"ids": [cancelled_po_id]})
        reset = client.call(
            "purchase.order",
            "read",
            {"ids": [cancelled_po_id], "fields": ["state"]},
        )[0]
        assert reset["state"] == "draft"


def test_integration_user_configuration_mutations_are_denied(
    running_odoo_contract: OdooContractStack,
) -> None:
    from scripts.odoo.probe_contract import Json2Client, ProbeError

    fixture = running_odoo_contract.first_bootstrap["fixture"]
    assert isinstance(fixture, dict)
    with Json2Client(
        base_url=running_odoo_contract.base_url,
        database=running_odoo_contract.database,
        api_key=running_odoo_contract.api_key,
    ) as client:
        denied_calls: tuple[tuple[str, str, dict[str, object]], ...] = (
            (
                "res.partner",
                "write",
                {
                    "ids": [fixture["vendor_id"]],
                    "vals": {"name": "Forbidden vendor change"},
                },
            ),
            (
                "product.supplierinfo",
                "write",
                {
                    "ids": [fixture["supplierinfo_id"]],
                    "vals": {"price": 1.0},
                },
            ),
            (
                "stock.warehouse.orderpoint",
                "write",
                {
                    "ids": [fixture["orderpoint_id"]],
                    "vals": {"product_max_qty": 999.0},
                },
            ),
            (
                "res.users",
                "create",
                {
                    "vals_list": [
                        {
                            "name": "Forbidden User",
                            "login": "forbidden@example.invalid",
                        }
                    ]
                },
            ),
        )
        for model, method, payload in denied_calls:
            with pytest.raises(ProbeError) as denied:
                client.call(model, method, payload)
            assert denied.value.status_code == 403
