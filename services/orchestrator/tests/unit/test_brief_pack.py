import asyncio
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from langchain_core.runnables import RunnableLambda
from pydantic import ValidationError

from intake_orchestrator.agent.backend import InMemoryAgentBackend
from intake_orchestrator.agent.brief_contracts import (
    BriefPacketSegmentation,
    CampaignBriefCandidate,
)
from intake_orchestrator.agent.brief_extraction import (
    BRIEF_EXTRACTION_SCHEMA_VERSION,
    LangChainStructuredBriefExtractor,
    StubStructuredBriefExtractor,
)
from intake_orchestrator.agent.extraction import (
    BoundedExtractionContent,
    candidate_business_data,
    candidate_completeness,
)
from intake_orchestrator.agent.graph import compile_agent_subgraph, run_agent
from intake_orchestrator.agent.models import StubChatModel
from intake_orchestrator.agent.policy import AgentLimits

CONTENT = BoundedExtractionContent(text="[page 1]\nbounded brief text")


class FakeStructuredModel:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def with_structured_output(self, schema: object) -> RunnableLambda[object, dict[str, Any]]:
        del schema
        return RunnableLambda(lambda _messages: self._payload)


def _stub() -> CampaignBriefCandidate:
    return asyncio.run(
        StubStructuredBriefExtractor(model="m", prompt_version="p").extract(
            strategy="TEXT", bounded_content=CONTENT
        )
    )


def test_brief_business_data_keeps_deliverables_as_plain_items() -> None:
    data = candidate_business_data(_stub())

    assert data["budget_total"] == "85000"
    assert data["launch_date"] == "2026-12-01"
    assert data["deliverables"][0] == {  # type: ignore[index]
        "asset_type": "SOCIAL_STATIC",
        "quantity": 12,
        "description": "Menu feature posts",
        "due_date": "2026-11-20",
    }
    assert data["deliverables_added_in_correspondence"] == []
    assert candidate_completeness(_stub()) == Decimal("0.9375")


def test_unknown_asset_types_and_decisions_are_rejected() -> None:
    payload = _stub().model_dump()
    payload["deliverables"][0]["asset_type"] = "BILLBOARD_ON_THE_MOON"
    with pytest.raises(ValidationError):
        CampaignBriefCandidate.model_validate(payload)
    with pytest.raises(ValidationError):
        CampaignBriefCandidate.model_validate({**_stub().model_dump(), "queue": "READY"})


def test_segmentation_requires_ordered_pages() -> None:
    with pytest.raises(ValidationError):
        BriefPacketSegmentation.model_validate(
            {"segments": [{"page_number": 2, "label": "BRIEF", "confidence": "0.9"}]}
        )


@pytest.mark.asyncio
async def test_live_extractor_stamps_provenance_and_keeps_deliverable_evidence() -> None:
    stub = StubStructuredBriefExtractor(model="m", prompt_version="p")
    payload = (await stub.extract(strategy="TEXT", bounded_content=CONTENT)).model_dump()
    for value in payload.values():
        if isinstance(value, dict):
            value.update(provider="attacker", schema_version="x")
    payload["deliverables"][0]["evidence_excerpt"] = "12 x menu posts (p.1)"
    extractor = LangChainStructuredBriefExtractor(
        model=FakeStructuredModel(payload),  # type: ignore[arg-type]
        provider="anthropic",
        model_name="configured-model",
        prompt_version="brief-intake-agent-v1",
    )

    brief = await extractor.extract(strategy="VISUAL", bounded_content=CONTENT)

    assert brief.brand.provider == "anthropic"
    assert brief.brand.schema_version == BRIEF_EXTRACTION_SCHEMA_VERSION
    assert brief.deliverables[0].evidence_excerpt == "12 x menu posts (p.1)"


@pytest.mark.asyncio
async def test_campaign_brief_follows_the_brief_tool_path() -> None:
    backend = InMemoryAgentBackend(document_type="CAMPAIGN_BRIEF")
    graph = compile_agent_subgraph(
        model=StubChatModel(model_name="unit-stub", decision_plan=[]),
        backend=backend,
        limits=AgentLimits(),
        model_timeout_seconds=2,
        tool_timeout_seconds=2,
        total_timeout_seconds=30,
    )

    outcome = await run_agent(
        graph,
        graph_run_id=uuid4(),
        tenant_id=uuid4(),
        document_id=uuid4(),
        correlation_id="briefs-unit",
        agent_run_id=uuid4(),
    )

    assert outcome["incomplete"] is False
    assert [name for name, _ in backend.calls] == [
        "inspect_document",
        "classify_document",
        "segment_brief_packet",
        "extract_brief_from_text",
        "validate_candidate",
        "submit_recommendation",
    ]


@pytest.mark.asyncio
async def test_brief_tools_are_refused_for_claims_and_lookups_are_limited() -> None:
    claims = InMemoryAgentBackend(document_type="CLAIM_PACKET")
    briefs = InMemoryAgentBackend(document_type="CAMPAIGN_BRIEF")
    for backend, plan in (
        (claims, ["inspect_document", "classify_document", "segment_brief_packet"]),
        (
            briefs,
            ["inspect_document", "classify_document"] + ["lookup_client_sow"] * 3,
        ),
    ):
        graph = compile_agent_subgraph(
            model=StubChatModel(model_name="unit-stub", decision_plan=plan),
            backend=backend,
            limits=AgentLimits(),
            model_timeout_seconds=2,
            tool_timeout_seconds=2,
            total_timeout_seconds=30,
        )
        outcome = await run_agent(
            graph,
            graph_run_id=uuid4(),
            tenant_id=uuid4(),
            document_id=uuid4(),
            correlation_id="briefs-unit",
            agent_run_id=uuid4(),
        )
        assert outcome["incomplete"] is True
    assert "segment_brief_packet" not in [name for name, _ in claims.calls]
    assert [name for name, _ in briefs.calls].count("lookup_client_sow") == 2
