"""Executable T11A contracts for the StockAI Odoo add-on."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from scripts.odoo.probe_contract import Json2Client, ProbeError

from tests.contract.conftest import OdooContractStack

EXPECTED_FIELDS = [
    "write_date",
    "state",
    "partner_id",
    "currency_id",
    "amount_total",
]


def _client(stack: OdooContractStack, api_key: str) -> Json2Client:
    return Json2Client(
        base_url=stack.base_url,
        database=stack.database,
        api_key=api_key,
        timeout=30,
    )


def _expected(order: dict[str, object]) -> dict[str, object]:
    partner = order["partner_id"]
    currency = order["currency_id"]
    assert isinstance(partner, list)
    assert isinstance(currency, list)
    return {
        "write_date": order["write_date"],
        "state": order["state"],
        "partner_id": partner[0],
        "currency_id": currency[0],
        "amount_total": order["amount_total"],
    }


def _read_order(client: Json2Client, order_id: int) -> dict[str, object]:
    result = client.call(
        "purchase.order",
        "read",
        {"ids": [order_id], "fields": EXPECTED_FIELDS + ["partner_ref", "picking_ids"]},
    )
    assert isinstance(result, list) and len(result) == 1
    order = result[0]
    assert isinstance(order, dict)
    return order


def _create_order(
    client: Json2Client,
    fixture: dict[str, object],
    *,
    origin: str,
    with_product: bool = True,
) -> int:
    planned_date = (datetime.now(UTC) + timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
    line = {
        "name": "Fictional StockAI contract line",
        "product_qty": 2.0,
        "product_uom_id": fixture["unit_uom_id"],
        "date_planned": planned_date,
        "price_unit": 12.5,
        "tax_ids": [[6, 0, []]],
    }
    if with_product:
        line["product_id"] = fixture["product_id"]
    result = client.call(
        "purchase.order",
        "create",
        {
            "vals_list": [
                {
                    "partner_id": fixture["vendor_id"],
                    "origin": origin,
                    "partner_ref": f"VENDOR-{origin}",
                    "company_id": fixture["company_id"],
                    "order_line": [[0, 0, line]],
                }
            ]
        },
    )
    assert isinstance(result, list) and len(result) == 1
    return int(result[0])


def test_budget_shape_constraints_tracking_and_role_permissions(
    running_odoo_contract: OdooContractStack,
) -> None:
    fixture = running_odoo_contract.first_bootstrap["fixture"]
    assert isinstance(fixture, dict)
    month = datetime.now(UTC).date().replace(day=1).isoformat()
    values = {
        "company_id": fixture["company_id"],
        "product_category_id": fixture["product_category_id"],
        "analytic_account_id": fixture["analytic_account_id"],
        "period_start": month,
        "amount": 2500.0,
    }

    with _client(
        running_odoo_contract, running_odoo_contract.configuration_api_key
    ) as administrator:
        metadata = administrator.call(
            "stockai.procurement.budget",
            "fields_get",
            {
                "allfields": [
                    "company_id",
                    "product_category_id",
                    "analytic_account_id",
                    "period_start",
                    "currency_id",
                    "amount",
                    "active",
                ],
                "attributes": ["type", "required", "readonly"],
            },
        )
        assert metadata["company_id"]["required"] is True
        assert metadata["product_category_id"]["required"] is True
        assert metadata["analytic_account_id"]["required"] is True
        assert metadata["period_start"]["required"] is True
        assert metadata["amount"]["required"] is True
        assert metadata["currency_id"]["readonly"] is True
        budget_id = administrator.call(
            "stockai.procurement.budget", "create", {"vals_list": [values]}
        )[0]
        budget = administrator.call(
            "stockai.procurement.budget",
            "read",
            {
                "ids": [budget_id],
                "fields": [
                    "company_id",
                    "product_category_id",
                    "analytic_account_id",
                    "period_start",
                    "currency_id",
                    "amount",
                    "active",
                    "message_ids",
                ],
            },
        )[0]
        assert budget["company_id"][0] == fixture["company_id"]
        assert budget["currency_id"][0] == fixture["currency_id"]
        assert budget["period_start"] == month
        assert budget["amount"] == 2500.0
        assert budget["active"] is True
        initial_messages = budget["message_ids"]
        assert isinstance(initial_messages, list)

        for invalid_values in (
            values | {"amount": -1.0},
            values | {"period_start": month[:-2] + "02"},
            values,
        ):
            with pytest.raises(ProbeError) as invalid:
                administrator.call(
                    "stockai.procurement.budget",
                    "create",
                    {"vals_list": [invalid_values]},
                )
            assert invalid.value.status_code == 422

        assert administrator.call(
            "stockai.procurement.budget",
            "write",
            {"ids": [budget_id], "vals": {"amount": 2750.0}},
        )
        tracked = administrator.call(
            "stockai.procurement.budget",
            "read",
            {"ids": [budget_id], "fields": ["amount", "message_ids"]},
        )[0]
        assert tracked["amount"] == 2750.0
        assert len(tracked["message_ids"]) > len(initial_messages)
        assert administrator.call(
            "stockai.procurement.budget",
            "write",
            {"ids": [budget_id], "vals": {"active": False}},
        )
        replacement_id = administrator.call(
            "stockai.procurement.budget", "create", {"vals_list": [values]}
        )[0]

    with _client(running_odoo_contract, running_odoo_contract.api_key) as integration:
        visible = integration.call(
            "stockai.procurement.budget",
            "read",
            {"ids": [replacement_id], "fields": ["amount", "active"]},
        )[0]
        assert visible == {"id": replacement_id, "amount": 2500.0, "active": True}
        denied_mutations: tuple[tuple[str, dict[str, object]], ...] = (
            ("create", {"vals_list": [values | {"period_start": "2030-01-01"}]}),
            ("write", {"ids": [replacement_id], "vals": {"amount": 1.0}}),
            ("write", {"ids": [replacement_id], "vals": {"active": False}}),
        )
        for method, payload in denied_mutations:
            with pytest.raises(ProbeError) as denied:
                integration.call("stockai.procurement.budget", method, payload)
            assert denied.value.status_code == 403


def test_atomic_update_rejects_stale_unauthorized_multi_and_forbidden_changes(
    running_odoo_contract: OdooContractStack,
) -> None:
    fixture = running_odoo_contract.first_bootstrap["fixture"]
    assert isinstance(fixture, dict)
    with _client(running_odoo_contract, running_odoo_contract.api_key) as integration:
        first_id = _create_order(
            integration, fixture, origin="stockai-t11a-update-first"
        )
        second_id = _create_order(
            integration, fixture, origin="stockai-t11a-update-second"
        )
        snapshot = _read_order(integration, first_id)

        with pytest.raises(ProbeError) as multi:
            integration.call(
                "purchase.order",
                "action_stockai_update_draft",
                {
                    "ids": [first_id, second_id],
                    "expected": _expected(snapshot),
                    "changes": {"partner_ref": "MULTI-DENIED"},
                },
            )
        assert multi.value.status_code == 422

        with pytest.raises(ProbeError) as forbidden:
            integration.call(
                "purchase.order",
                "action_stockai_update_draft",
                {
                    "ids": [first_id],
                    "expected": _expected(snapshot),
                    "changes": {"state": "purchase"},
                },
            )
        assert forbidden.value.status_code == 422

        with pytest.raises(ProbeError) as forbidden_line:
            integration.call(
                "purchase.order",
                "action_stockai_update_draft",
                {
                    "ids": [first_id],
                    "expected": _expected(snapshot),
                    "changes": {"order_line": [[0, 0, {"state": "purchase"}]]},
                },
            )
        assert forbidden_line.value.status_code == 422

        time.sleep(1.05)
        assert integration.call(
            "purchase.order",
            "write",
            {"ids": [first_id], "vals": {"partner_ref": "CONCURRENT-WRITE"}},
        )
        with pytest.raises(ProbeError) as stale:
            integration.call(
                "purchase.order",
                "action_stockai_update_draft",
                {
                    "ids": [first_id],
                    "expected": _expected(snapshot),
                    "changes": {"partner_ref": "STALE-WRITE"},
                },
            )
        assert stale.value.status_code == 422
        assert _read_order(integration, first_id)["partner_ref"] == "CONCURRENT-WRITE"

        current = _read_order(integration, first_id)
        changed = integration.call(
            "purchase.order",
            "action_stockai_update_draft",
            {
                "ids": [first_id],
                "expected": _expected(current),
                "changes": {"partner_ref": "ATOMIC-UPDATE"},
            },
        )
        assert changed["id"] == first_id
        assert _read_order(integration, first_id)["partner_ref"] == "ATOMIC-UPDATE"
        current_after_update = _read_order(integration, first_id)

    with _client(
        running_odoo_contract, running_odoo_contract.configuration_api_key
    ) as unauthorized:
        with pytest.raises(ProbeError) as denied:
            unauthorized.call(
                "purchase.order",
                "action_stockai_cancel_draft",
                {"ids": [first_id], "expected": _expected(current_after_update)},
            )
        assert denied.value.status_code == 403


def test_atomic_confirm_serializes_competing_actions_and_never_duplicates_receipts(
    running_odoo_contract: OdooContractStack,
) -> None:
    fixture = running_odoo_contract.first_bootstrap["fixture"]
    assert isinstance(fixture, dict)
    with _client(running_odoo_contract, running_odoo_contract.api_key) as client:
        order_id = _create_order(client, fixture, origin="stockai-t11a-confirm-race")
        expected = _expected(_read_order(client, order_id))

    def confirm_once() -> tuple[str, int | None]:
        try:
            with _client(
                running_odoo_contract, running_odoo_contract.api_key
            ) as client:
                result = client.call(
                    "purchase.order",
                    "action_stockai_confirm",
                    {"ids": [order_id], "expected": expected},
                )
            return "ok", result["id"]
        except ProbeError as exc:
            return "conflict", exc.status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _index: confirm_once(), range(2)))

    assert sorted(status for status, _value in results) == ["conflict", "ok"]
    assert next(value for status, value in results if status == "conflict") == 422
    with _client(running_odoo_contract, running_odoo_contract.api_key) as client:
        confirmed = _read_order(client, order_id)
        assert confirmed["state"] == "purchase"
        confirmed_pickings = confirmed["picking_ids"]
        assert isinstance(confirmed_pickings, list)
        assert len(confirmed_pickings) == 1
        with pytest.raises(ProbeError) as repeated:
            client.call(
                "purchase.order",
                "action_stockai_confirm",
                {"ids": [order_id], "expected": _expected(confirmed)},
            )
        assert repeated.value.status_code == 422
        final_pickings = _read_order(client, order_id)["picking_ids"]
        assert isinstance(final_pickings, list)
        assert len(final_pickings) == 1


def test_atomic_cancel_is_one_time_and_standard_confirmation_failures_roll_back(
    running_odoo_contract: OdooContractStack,
) -> None:
    fixture = running_odoo_contract.first_bootstrap["fixture"]
    assert isinstance(fixture, dict)
    with _client(running_odoo_contract, running_odoo_contract.api_key) as client:
        cancel_id = _create_order(client, fixture, origin="stockai-t11a-cancel-once")
        cancel_expected = _expected(_read_order(client, cancel_id))
        cancelled = client.call(
            "purchase.order",
            "action_stockai_cancel_draft",
            {"ids": [cancel_id], "expected": cancel_expected},
        )
        assert cancelled["state"] == "cancel"
        with pytest.raises(ProbeError) as repeated:
            client.call(
                "purchase.order",
                "action_stockai_cancel_draft",
                {"ids": [cancel_id], "expected": cancel_expected},
            )
        assert repeated.value.status_code == 422
        assert _read_order(client, cancel_id)["state"] == "cancel"

        invalid_id = _create_order(
            client,
            fixture,
            origin="stockai-t11a-standard-failure",
            with_product=False,
        )
        with pytest.raises(ProbeError) as standard_failure:
            client.call(
                "purchase.order",
                "action_stockai_confirm",
                {
                    "ids": [invalid_id],
                    "expected": _expected(_read_order(client, invalid_id)),
                },
            )
        assert standard_failure.value.status_code == 422
        failed = _read_order(client, invalid_id)
        assert failed["state"] == "draft"
        assert failed["picking_ids"] == []
