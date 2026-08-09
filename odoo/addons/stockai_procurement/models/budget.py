from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class StockAIProcurementBudget(models.Model):
    _name = "stockai.procurement.budget"
    _description = "StockAI Monthly Procurement Budget"
    _inherit = ["mail.thread"]
    _rec_name = "period_start"
    _order = "period_start desc, product_category_id, id"
    _check_company_auto = True

    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
        index=True,
        tracking=True,
    )
    product_category_id = fields.Many2one(
        "product.category",
        required=True,
        index=True,
        tracking=True,
        ondelete="restrict",
    )
    analytic_account_id = fields.Many2one(
        "account.analytic.account",
        required=True,
        index=True,
        tracking=True,
        check_company=True,
        ondelete="restrict",
    )
    period_start = fields.Date(
        required=True,
        index=True,
        tracking=True,
        default=lambda self: fields.Date.today().replace(day=1),
    )
    currency_id = fields.Many2one(
        "res.currency",
        related="company_id.currency_id",
        store=True,
        readonly=True,
    )
    amount = fields.Monetary(
        required=True,
        currency_field="currency_id",
        tracking=True,
        default=0.0,
    )
    active = fields.Boolean(default=True, tracking=True)

    _amount_non_negative = models.Constraint(
        "CHECK (amount >= 0)",
        "The monthly procurement budget cannot be negative.",
    )
    _period_first_day = models.Constraint(
        "CHECK (EXTRACT(DAY FROM period_start) = 1)",
        "The procurement budget period must be the first day of a month.",
    )
    _active_company_category_month_unique = models.UniqueIndex(
        "(company_id, product_category_id, period_start) WHERE active IS TRUE",
        "Only one active procurement budget is allowed per company, category, "
        "and month.",
    )

    @api.constrains("company_id", "analytic_account_id")
    def _check_analytic_account_company(self):
        for budget in self:
            analytic_company = budget.analytic_account_id.company_id
            if analytic_company and analytic_company != budget.company_id:
                raise ValidationError(
                    _("The analytic account must belong to the budget company.")
                )
