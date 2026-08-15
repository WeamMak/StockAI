"""Authoritative procurement-evidence MCP behavior."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from io import StringIO

import pytest
from prometheus_client import generate_latest

from procurement.domain.identifiers import Environment
from procurement.domain.policy.evidence import (
    CoverageEvidence,
    ProcurementEvidence,
    ProjectedDay,
    ShortageEvidence,
)
from procurement.mcp_server.observability import create_mcp_metrics
from procurement.mcp_server.schemas import GetProcurementEvidenceInput
from procurement.mcp_server.tools.evidence import get_procurement_evidence
from procurement.observability.logging import configure_json_logging
from procurement.ports.erp import ProcurementEvidenceQuery


def _evidence(environment: Environment = Environment.DEV) -> ProcurementEvidence:
    return ProcurementEvidence(
        environment=environment,
        evidence_id=f"{environment.value}:evidence-product-1",
        product_id="product-1",
        product_name="Fictional component",
        category_id="category-1",
        captured_at=datetime(2026, 8, 15, 8, tzinfo=UTC),
        shortage=ShortageEvidence(
            horizon_start=date(2026, 8, 15),
            horizon_end=date(2026, 8, 29),
            reorder_trigger_date=date(2026, 8, 16),
            need_by_date=date(2026, 8, 20),
            reorder_minimum=Decimal("5.000000"),
            reorder_maximum=Decimal("20.000000"),
            minimum_projected_quantity=Decimal("0.000000"),
            timeline=tuple(
                ProjectedDay(
                    projection_date=date(2026, 8, 15) + timedelta(days=offset),
                    quantity=(
                        Decimal("0.000000") if offset >= 5 else Decimal("8.000000")
                    ),
                )
                for offset in range(15)
            ),
        ),
        coverage=CoverageEvidence(
            status="none",
            covered_quantity=Decimal("0.000000"),
            residual_quantity=Decimal("20.000000"),
            source_count=0,
        ),
        offers=(),
        budget=None,
        skip_reason_code="NO_VALID_OFFER",
    )


class EvidenceErp:
    def __init__(self, evidence: ProcurementEvidence) -> None:
        self.evidence = evidence
        self.queries: list[ProcurementEvidenceQuery] = []

    async def get_procurement_evidence(
        self, query: ProcurementEvidenceQuery
    ) -> ProcurementEvidence:
        self.queries.append(query)
        return self.evidence


@pytest.mark.anyio
async def test_evidence_tool_returns_strict_environment_bound_output() -> None:
    erp = EvidenceErp(_evidence())
    metrics = create_mcp_metrics()
    response = await get_procurement_evidence(
        request=GetProcurementEvidenceInput(
            environment="dev", product_id="product-1", horizon_days=14
        ),
        erp=erp,  # type: ignore[arg-type]
        server_environment=Environment.DEV,
        metrics=metrics,
        logger=configure_json_logging(
            service="procurement-mcp",
            environment="dev",
            stream=StringIO(),
            logger_name="procurement.test.mcp.evidence",
        ),
    )

    assert erp.queries == [ProcurementEvidenceQuery(Environment.DEV, "product-1", 14)]
    assert response.environment == "dev"
    assert response.coverage.residual_quantity == Decimal("20.000000")
    assert len(response.shortage.timeline) == 15
    assert response.shortage.timeline[-1].projection_date == date(2026, 8, 29)
    assert response.skip_reason_code == "NO_VALID_OFFER"
    assert (
        'procurement_mcp_tool_calls_total{status="success",'
        'tool="get_procurement_evidence"} 1.0'
        in generate_latest(metrics.registry).decode()
    )
