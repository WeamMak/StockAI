from __future__ import annotations

from collections.abc import Mapping

from odoo import _, fields, models
from odoo.exceptions import AccessError, MissingError, UserError, ValidationError
from odoo.tools import SQL

_INTEGRATION_GROUP = "stockai_procurement.group_stockai_procurement_integration"
_EXPECTED_FIELDS = {
    "write_date",
    "state",
    "partner_id",
    "currency_id",
    "amount_total",
}
_DRAFT_STATES = {"draft", "sent"}
_ALLOWED_ORDER_FIELDS = {
    "partner_id",
    "partner_ref",
    "date_order",
    "currency_id",
    "payment_term_id",
    "order_line",
}
_ALLOWED_LINE_FIELDS = {
    "name",
    "product_id",
    "product_qty",
    "product_uom_id",
    "date_planned",
    "price_unit",
    "discount",
    "tax_ids",
    "analytic_distribution",
}


class PurchaseOrder(models.Model):
    _inherit = "purchase.order"

    def action_stockai_update_draft(self, expected, changes):
        order = self._stockai_lock_and_validate(expected)
        order._stockai_require_draft()
        order._stockai_validate_changes(changes)
        order.write(dict(changes))
        return order._stockai_snapshot()

    def action_stockai_cancel_draft(self, expected):
        order = self._stockai_lock_and_validate(expected)
        order._stockai_require_draft()
        order.button_cancel()
        return order._stockai_snapshot()

    def action_stockai_confirm(self, expected):
        order = self._stockai_lock_and_validate(expected)
        order._stockai_require_draft()
        order.button_confirm()
        return order._stockai_snapshot()

    def _stockai_lock_and_validate(self, expected):
        if len(self) != 1:
            raise ValidationError(
                _("StockAI purchase-order actions require exactly one record.")
            )
        if not self.env.user.has_group(_INTEGRATION_GROUP):
            raise AccessError(_("This user cannot run StockAI purchase-order actions."))
        if not isinstance(expected, Mapping) or set(expected) != _EXPECTED_FIELDS:
            raise ValidationError(_("The StockAI expected revision is invalid."))

        self.env.cr.execute(
            SQL("SELECT id FROM purchase_order WHERE id = %s FOR UPDATE", self.id)
        )
        if not self.env.cr.fetchone():
            raise MissingError(_("The purchase order no longer exists."))

        self.invalidate_recordset(list(_EXPECTED_FIELDS))
        self._stockai_compare_expected(expected)
        return self

    def _stockai_compare_expected(self, expected):
        try:
            expected_write_date = fields.Datetime.to_string(
                fields.Datetime.to_datetime(expected["write_date"])
            )
            expected_partner_id = int(expected["partner_id"])
            expected_currency_id = int(expected["currency_id"])
            expected_total = float(expected["amount_total"])
        except (TypeError, ValueError) as exc:
            raise ValidationError(
                _("The StockAI expected revision is invalid.")
            ) from exc

        mismatch = (
            expected_write_date != fields.Datetime.to_string(self.write_date)
            or expected["state"] != self.state
            or expected_partner_id != self.partner_id.id
            or expected_currency_id != self.currency_id.id
            or self.currency_id.compare_amounts(expected_total, self.amount_total) != 0
        )
        if mismatch:
            raise UserError(_("The StockAI purchase-order revision is stale."))

    def _stockai_require_draft(self):
        if self.state not in _DRAFT_STATES:
            raise UserError(
                _("The StockAI purchase-order action is invalid for the current state.")
            )

    def _stockai_validate_changes(self, changes):
        if not isinstance(changes, Mapping) or not changes:
            raise ValidationError(_("StockAI purchase-order changes are invalid."))
        if set(changes) - _ALLOWED_ORDER_FIELDS:
            raise ValidationError(_("A requested purchase-order field is not allowed."))
        if "order_line" in changes:
            self._stockai_validate_line_commands(changes["order_line"])

    def _stockai_validate_line_commands(self, commands):
        if not isinstance(commands, list):
            raise ValidationError(_("StockAI purchase-order line changes are invalid."))

        own_line_ids = set(self.order_line.ids)
        for command in commands:
            if not isinstance(command, (list, tuple)) or not command:
                raise ValidationError(
                    _("StockAI purchase-order line changes are invalid.")
                )
            operation = command[0]
            if type(operation) is not int:
                raise ValidationError(
                    _("StockAI purchase-order line changes are invalid.")
                )
            if operation == 0:
                if len(command) != 3 or type(command[1]) is not int or command[1] != 0:
                    raise ValidationError(
                        _("StockAI purchase-order line changes are invalid.")
                    )
                self._stockai_validate_line_values(command, expected_length=3)
            elif operation == 1:
                self._stockai_validate_owned_line(
                    command, own_line_ids, with_values=True
                )
            elif operation == 2:
                self._stockai_validate_owned_line(
                    command, own_line_ids, with_values=False
                )
            elif (
                operation == 5
                and len(command) == 3
                and type(command[1]) is int
                and command[1] == 0
                and type(command[2]) is int
                and command[2] == 0
            ):
                continue
            else:
                raise ValidationError(
                    _("A requested purchase-order line operation is not allowed.")
                )

    def _stockai_validate_owned_line(self, command, own_line_ids, *, with_values):
        expected_length = 3
        if (
            len(command) != expected_length
            or type(command[1]) is not int
            or command[1] not in own_line_ids
        ):
            raise ValidationError(_("StockAI purchase-order line changes are invalid."))
        if with_values:
            self._stockai_validate_line_values(command, expected_length=expected_length)
        elif type(command[2]) is not int or command[2] != 0:
            raise ValidationError(_("StockAI purchase-order line changes are invalid."))

    def _stockai_validate_line_values(self, command, *, expected_length):
        if len(command) != expected_length or not isinstance(command[2], Mapping):
            raise ValidationError(_("StockAI purchase-order line changes are invalid."))
        if set(command[2]) - _ALLOWED_LINE_FIELDS:
            raise ValidationError(
                _("A requested purchase-order line field is not allowed.")
            )

    def _stockai_snapshot(self):
        self.invalidate_recordset(list(_EXPECTED_FIELDS))
        return {
            "id": self.id,
            "name": self.name,
            "write_date": fields.Datetime.to_string(self.write_date),
            "state": self.state,
            "partner_id": self.partner_id.id,
            "currency_id": self.currency_id.id,
            "amount_total": self.amount_total,
        }
