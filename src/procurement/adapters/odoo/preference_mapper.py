"""Strict Odoo mapping for resolved typed procurement preferences."""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal, InvalidOperation

from procurement.domain.policy.preferences import (
    PreferenceCriterion,
    PreferenceScope,
    PremiumEnforcement,
    ProcurementPreference,
)

_PROFILE_FIELDS = {
    "id",
    "company_id",
    "scope",
    "product_category_id",
    "product_id",
    "revision",
    "max_price_premium_percent",
    "enforcement_mode",
    "active",
    "priority_ids",
}
_PRIORITY_FIELDS = {"id", "preference_id", "sequence", "criterion"}


def map_effective_preference(
    *,
    profiles: object,
    priorities: object,
    company_id: int,
    category_id: int,
    product_id: int,
) -> ProcurementPreference:
    """Validate current records and resolve product, category, then company."""

    try:
        rows = _rows(profiles, _PROFILE_FIELDS, maximum=100)
        priority_rows = _rows(priorities, _PRIORITY_FIELDS, maximum=300)
        if any(_many2one(row["company_id"]) != company_id for row in rows):
            raise ValueError("cross-company preference")
        selected: Mapping[str, object] | None = None
        selected_scope: PreferenceScope | None = None
        for scope in (
            PreferenceScope.PRODUCT,
            PreferenceScope.CATEGORY,
            PreferenceScope.COMPANY,
        ):
            matching = [
                row
                for row in rows
                if row["active"] is True
                and row["scope"] == scope.value
                and _scope_matches(
                    row,
                    scope=scope,
                    category_id=category_id,
                    product_id=product_id,
                )
            ]
            if len(matching) > 1:
                raise ValueError("duplicate current preference")
            if matching:
                selected = matching[0]
                selected_scope = scope
                break
        if selected is None or selected_scope is None:
            raise ValueError("missing company preference")
        profile_id = _integer(selected["id"])
        raw_priority_ids = selected["priority_ids"]
        if not isinstance(raw_priority_ids, list) or any(
            type(item) is not int or item <= 0 for item in raw_priority_ids
        ):
            raise ValueError("invalid preference priorities")
        selected_priorities = [
            row
            for row in priority_rows
            if _many2one(row["preference_id"]) == profile_id
        ]
        if {_integer(row["id"]) for row in selected_priorities} != set(
            raw_priority_ids
        ):
            raise ValueError("preference priority identity mismatch")
        selected_priorities.sort(key=lambda row: _integer(row["sequence"]))
        criteria = tuple(
            PreferenceCriterion(str(row["criterion"])) for row in selected_priorities
        )
        scope_id = {
            PreferenceScope.COMPANY: str(company_id),
            PreferenceScope.CATEGORY: str(category_id),
            PreferenceScope.PRODUCT: str(product_id),
        }[selected_scope]
        return ProcurementPreference(
            profile_id=f"preference-{profile_id}",
            company_id=str(company_id),
            category_id=str(category_id),
            product_id=str(product_id),
            scope=selected_scope,
            scope_id=scope_id,
            revision=_integer(selected["revision"]),
            ordered_criteria=criteria,
            max_price_premium_percent=Decimal(
                str(selected["max_price_premium_percent"])
            ).quantize(Decimal("0.000001")),
            enforcement_mode=PremiumEnforcement(str(selected["enforcement_mode"])),
            precedence_source=selected_scope,
        )
    except (InvalidOperation, KeyError, TypeError, ValueError) as error:
        raise ValueError("Odoo preference response is invalid") from error


def _scope_matches(
    row: Mapping[str, object],
    *,
    scope: PreferenceScope,
    category_id: int,
    product_id: int,
) -> bool:
    category = _optional_many2one(row["product_category_id"])
    product = _optional_many2one(row["product_id"])
    if scope is PreferenceScope.COMPANY:
        return category is None and product is None
    if scope is PreferenceScope.CATEGORY:
        return category == category_id and product is None
    return category is None and product == product_id


def _rows(
    raw: object, expected: set[str], *, maximum: int
) -> list[Mapping[str, object]]:
    if not isinstance(raw, list) or len(raw) > maximum:
        raise ValueError("expected bounded Odoo preference records")
    if any(not isinstance(row, Mapping) or set(row) != expected for row in raw):
        raise ValueError("unexpected Odoo preference fields")
    return list(raw)


def _integer(raw: object) -> int:
    if type(raw) is not int or raw <= 0:
        raise ValueError("invalid Odoo preference integer")
    return raw


def _many2one(raw: object) -> int:
    if (
        not isinstance(raw, list)
        or len(raw) != 2
        or type(raw[0]) is not int
        or raw[0] <= 0
        or not isinstance(raw[1], str)
    ):
        raise ValueError("invalid Odoo preference relationship")
    return raw[0]


def _optional_many2one(raw: object) -> int | None:
    return None if raw is False else _many2one(raw)
