"""Deterministic fake for the narrow T04 ERP port."""

from __future__ import annotations

from collections.abc import Sequence

from procurement.bootstrap.mcp import _fictional_evidence, _fictional_preference
from procurement.domain.policy.evidence import ProcurementEvidence
from procurement.domain.policy.preferences import ProcurementPreference
from procurement.ports.erp import (
    CandidatePage,
    ProcurementEvidenceQuery,
    ProcurementPreferenceQuery,
    ReplenishmentCandidatesQuery,
)


class FakeOdooAdapter:
    """Return queued fictional pages or failures without a live Odoo service."""

    def __init__(
        self,
        *,
        page: CandidatePage,
        failures: Sequence[Exception] = (),
    ) -> None:
        self._page = page
        self._failures = list(failures)
        self.queries: list[ReplenishmentCandidatesQuery] = []

    async def list_replenishment_candidates(
        self,
        query: ReplenishmentCandidatesQuery,
    ) -> CandidatePage:
        """Record the query, then return or fail in a deterministic order."""

        self.queries.append(query)
        if self._failures:
            raise self._failures.pop(0)
        return self._page

    async def get_procurement_evidence(
        self, query: ProcurementEvidenceQuery
    ) -> ProcurementEvidence:
        """Return policy-built evidence for the requested fictional product."""

        return _fictional_evidence(query)

    async def get_procurement_preferences(
        self, query: ProcurementPreferenceQuery
    ) -> ProcurementPreference:
        return _fictional_preference(query)
