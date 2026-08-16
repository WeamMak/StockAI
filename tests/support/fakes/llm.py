"""Deterministic structured LLM fake for agent tests."""

from dataclasses import dataclass, field

from procurement.ports.llm import (
    RecommendationRequest,
    StructuredLlmPort,
    StructuredRecommendation,
)
from tests.support.recommendations import t27_recommendation


@dataclass(slots=True)
class FakeStructuredLlm(StructuredLlmPort):
    """Return one configured response without calling a model provider."""

    response: StructuredRecommendation
    requests: list[RecommendationRequest] = field(default_factory=list)

    async def recommend(
        self,
        request: RecommendationRequest,
    ) -> StructuredRecommendation:
        self.requests.append(request)
        return self.response


@dataclass(slots=True)
class EvidenceAwareFakeStructuredLlm(StructuredLlmPort):
    """Build a valid T27 response from the exact request evidence."""

    requests: list[RecommendationRequest] = field(default_factory=list)

    async def recommend(
        self,
        request: RecommendationRequest,
    ) -> StructuredRecommendation:
        self.requests.append(request)
        return t27_recommendation(request)
