from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError

CRITERIA = [
    ("price", "Price"),
    ("delivery", "Delivery"),
    ("reliability", "Reliability"),
]


class StockAIProcurementPreference(models.Model):
    _name = "stockai.procurement.preference"
    _description = "StockAI Procurement Recommendation Preference"
    _inherit = ["mail.thread"]
    _rec_name = "scope"
    _order = "scope, id"
    _check_company_auto = True

    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
        index=True,
        tracking=True,
    )
    scope = fields.Selection(
        [
            ("company", "Company"),
            ("category", "Product Category"),
            ("product", "Product"),
        ],
        required=True,
        index=True,
        tracking=True,
    )
    product_category_id = fields.Many2one(
        "product.category",
        index=True,
        tracking=True,
        ondelete="restrict",
    )
    product_id = fields.Many2one(
        "product.product",
        index=True,
        tracking=True,
        check_company=True,
        ondelete="restrict",
    )
    priority_ids = fields.One2many(
        "stockai.procurement.preference.priority",
        "preference_id",
        string="Ordered priorities",
        copy=True,
    )
    max_price_premium_percent = fields.Float(
        required=True,
        default=0.0,
        digits=(5, 2),
        tracking=True,
    )
    enforcement_mode = fields.Selection(
        [("advisory", "Advisory"), ("hard", "Hard")],
        required=True,
        default="advisory",
        tracking=True,
    )
    revision = fields.Integer(required=True, readonly=True, default=1, tracking=True)
    active = fields.Boolean(default=True, tracking=True)

    _premium_range = models.Constraint(
        "CHECK (max_price_premium_percent >= 0 AND max_price_premium_percent <= 100)",
        "The maximum price premium must be between 0 and 100 percent.",
    )
    _positive_revision = models.Constraint(
        "CHECK (revision > 0)",
        "The preference revision must be positive.",
    )
    _company_current_unique = models.UniqueIndex(
        "(company_id) WHERE active IS TRUE AND scope = 'company'",
        "Only one current company preference is allowed.",
    )
    _category_current_unique = models.UniqueIndex(
        "(company_id, product_category_id) WHERE active IS TRUE AND scope = 'category'",
        "Only one current preference is allowed per product category.",
    )
    _product_current_unique = models.UniqueIndex(
        "(company_id, product_id) WHERE active IS TRUE AND scope = 'product'",
        "Only one current preference is allowed per product.",
    )

    @api.model_create_multi
    def create(self, vals_list):
        for values in vals_list:
            if "revision" in values:
                raise ValidationError(_("The preference revision is server-managed."))
        records = super(
            StockAIProcurementPreference,
            self.with_context(stockai_parent_create=True),
        ).create(vals_list)
        records._validate_complete_priorities()
        return records

    def write(self, vals):
        if "revision" in vals and not self.env.context.get("stockai_revision_bump"):
            raise ValidationError(_("The preference revision is server-managed."))
        if self.env.context.get("stockai_revision_bump"):
            return super().write(vals)
        self._require_configuration_administrator()
        result = super(
            StockAIProcurementPreference,
            self.with_context(stockai_parent_write=True),
        ).write(vals)
        self._validate_complete_priorities()
        self._bump_revision()
        return result

    def unlink(self):
        raise AccessError(_("Preference records must be archived, not deleted."))

    def _require_configuration_administrator(self):
        if not self.env.user.has_group(
            "stockai_procurement.group_stockai_procurement_config_admin"
        ):
            raise AccessError(
                _("Only a configuration administrator may change preferences.")
            )

    def _bump_revision(self):
        for preference in self.sorted("id"):
            self.env.cr.execute(
                "SELECT revision FROM stockai_procurement_preference "
                "WHERE id = %s FOR UPDATE",
                [preference.id],
            )
            current = self.env.cr.fetchone()
            if not current:
                raise ValidationError(_("The preference record no longer exists."))
            super(
                StockAIProcurementPreference,
                preference.with_context(stockai_revision_bump=True),
            ).write({"revision": current[0] + 1})

    @api.constrains("scope", "company_id", "product_category_id", "product_id")
    def _check_scope_fields(self):
        for preference in self:
            if preference.scope == "company" and (
                preference.product_category_id or preference.product_id
            ):
                raise ValidationError(
                    _("A company preference cannot select a category or product.")
                )
            if preference.scope == "category" and (
                not preference.product_category_id or preference.product_id
            ):
                raise ValidationError(
                    _("A category preference requires only a product category.")
                )
            if preference.scope == "product" and (
                not preference.product_id or preference.product_category_id
            ):
                raise ValidationError(
                    _("A product preference requires only a product.")
                )
            if (
                preference.product_id.company_id
                and preference.product_id.company_id != preference.company_id
            ):
                raise ValidationError(
                    _("The product must belong to the preference company.")
                )

    @api.constrains("priority_ids")
    def _check_complete_priorities(self):
        self._validate_complete_priorities()

    def _validate_complete_priorities(self):
        expected = {criterion for criterion, _label in CRITERIA}
        for preference in self:
            criteria = preference.priority_ids.mapped("criterion")
            if len(criteria) != 3 or set(criteria) != expected:
                raise ValidationError(
                    _(
                        "Priorities must order price, delivery, and reliability "
                        "exactly once."
                    )
                )


class StockAIProcurementPreferencePriority(models.Model):
    _name = "stockai.procurement.preference.priority"
    _description = "StockAI Procurement Preference Priority"
    _order = "sequence, id"
    _check_company_auto = True

    preference_id = fields.Many2one(
        "stockai.procurement.preference",
        required=True,
        index=True,
        ondelete="cascade",
    )
    company_id = fields.Many2one(
        related="preference_id.company_id",
        store=True,
        index=True,
    )
    sequence = fields.Integer(required=True, default=10)
    criterion = fields.Selection(CRITERIA, required=True)

    _criterion_unique = models.Constraint(
        "UNIQUE (preference_id, criterion)",
        "Each supported criterion may appear only once.",
    )
    _sequence_unique = models.Constraint(
        "UNIQUE (preference_id, sequence)",
        "Each priority position may appear only once.",
    )

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        if self.env.context.get("stockai_parent_create"):
            return records
        preferences = records.preference_id
        preferences._require_configuration_administrator()
        preferences._validate_complete_priorities()
        preferences._bump_revision()
        return records

    def write(self, vals):
        if self.env.context.get("stockai_parent_write"):
            return super().write(vals)
        preferences = self.preference_id
        preferences._require_configuration_administrator()
        result = super().write(vals)
        (preferences | self.preference_id)._validate_complete_priorities()
        (preferences | self.preference_id)._bump_revision()
        return result

    def unlink(self):
        if self.env.context.get("stockai_parent_write"):
            return super().unlink()
        preferences = self.preference_id
        preferences._require_configuration_administrator()
        result = super().unlink()
        preferences._validate_complete_priorities()
        preferences._bump_revision()
        return result
