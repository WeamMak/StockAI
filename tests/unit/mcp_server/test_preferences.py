"""Typed preference MCP tool behavior."""

from decimal import Decimal
from io import StringIO

import pytest
from prometheus_client import generate_latest

from procurement.domain.identifiers import Environment
from procurement.domain.policy.preferences import (
    PreferenceCriterion,
    PreferenceScope,
    PremiumEnforcement,
    ProcurementPreference,
)
from procurement.mcp_server.observability import create_mcp_metrics
from procurement.mcp_server.schemas import GetProcurementPreferencesInput
from procurement.mcp_server.tools.preferences import get_procurement_preferences
from procurement.observability.logging import configure_json_logging
from procurement.ports.erp import ProcurementPreferenceQuery


class PreferenceErp:
    def __init__(self, profile: ProcurementPreference) -> None:
        self.profile = profile
        self.queries: list[ProcurementPreferenceQuery] = []

    async def get_procurement_preferences(
        self, query: ProcurementPreferenceQuery
    ) -> ProcurementPreference:
        self.queries.append(query)
        return self.profile


@pytest.mark.anyio
async def test_preference_tool_returns_strict_environment_bound_output() -> None:
    profile = ProcurementPreference(
        profile_id="preference-3",
        company_id="7",
        category_id="8",
        product_id="9",
        scope=PreferenceScope.PRODUCT,
        scope_id="9",
        revision=6,
        ordered_criteria=(
            PreferenceCriterion.PRICE,
            PreferenceCriterion.RELIABILITY,
            PreferenceCriterion.DELIVERY,
        ),
        max_price_premium_percent=Decimal("10.000000"),
        enforcement_mode=PremiumEnforcement.ADVISORY,
        precedence_source=PreferenceScope.PRODUCT,
    )
    erp = PreferenceErp(profile)
    metrics = create_mcp_metrics()

    response = await get_procurement_preferences(
        request=GetProcurementPreferencesInput(
            environment="dev", company_id="7", category_id="8", product_id="9"
        ),
        erp=erp,  # type: ignore[arg-type]
        server_environment=Environment.DEV,
        metrics=metrics,
        logger=configure_json_logging(
            service="procurement-mcp",
            environment="dev",
            stream=StringIO(),
            logger_name="procurement.test.mcp.preferences",
        ),
    )

    assert erp.queries == [ProcurementPreferenceQuery(Environment.DEV, "7", "8", "9")]
    assert response.profile_id == "preference-3"
    assert response.revision == 6
    assert (
        'procurement_mcp_tool_calls_total{status="success",'
        'tool="get_procurement_preferences"} 1.0'
        in generate_latest(metrics.registry).decode()
    )
