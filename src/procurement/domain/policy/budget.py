"""Authoritative monthly procurement budget calculation."""

from datetime import date
from decimal import Decimal

from procurement.domain.policy.evidence import BudgetEvidence

_QUANTUM = Decimal("0.000001")


def calculate_budget(
    *,
    period_start: date,
    currency: str,
    budget_amount: Decimal,
    confirmed_commitment: Decimal,
    proposed_amount: Decimal,
) -> BudgetEvidence:
    """Calculate remaining funds and an explicit manager-exception flag."""

    budget = budget_amount.quantize(_QUANTUM)
    committed = confirmed_commitment.quantize(_QUANTUM)
    proposed = proposed_amount.quantize(_QUANTUM)
    before = (budget - committed).quantize(_QUANTUM)
    after = (before - proposed).quantize(_QUANTUM)
    overage = max(Decimal("0"), -after).quantize(_QUANTUM)
    return BudgetEvidence(
        period_start=period_start,
        currency=currency,
        budget_amount=budget,
        confirmed_commitment=committed,
        proposed_amount=proposed,
        remaining_before=before,
        remaining_after=after,
        overage=overage,
        exception_required=overage > 0,
    )
