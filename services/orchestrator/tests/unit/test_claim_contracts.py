import asyncio
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from langchain_core.runnables import RunnableLambda
from pydantic import ValidationError

from intake_api.domain.enums import ClaimSegmentLabel
from intake_orchestrator.agent.claim_contracts import (
    ClaimPacketCandidate,
    ClaimPacketSegmentation,
    LookupPolicyOutput,
    LookupPolicyVehicleOutput,
    SegmentClaimPacketOutput,
    SegmentLabel,
)
from intake_orchestrator.agent.extraction import (
    CLAIM_EXTRACTION_SCHEMA_VERSION,
    BoundedExtractionContent,
    LangChainStructuredClaimExtractor,
    LangChainStructuredClaimSegmenter,
    StubStructuredClaimExtractor,
    StubStructuredClaimSegmenter,
    candidate_business_data,
    candidate_completeness,
)
from intake_orchestrator.agent.prompts import PROMPT_CONTENT_SHA256
from intake_orchestrator.agent.tools import TOOL_OUTPUT_SCHEMAS

CONTENT = BoundedExtractionContent(text="[page 1]\nbounded claim text")


class FakeStructuredModel:
    """Minimal chat-model double: structured output returns a fixed payload."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def with_structured_output(self, schema: object) -> RunnableLambda[object, dict[str, Any]]:
        del schema
        return RunnableLambda(lambda _messages: self._payload)


def _stub_payload() -> dict[str, Any]:
    return {
        "segments": [
            {"page_number": 1, "label": "LOSS_NOTICE", "confidence": "0.9"},
            {"page_number": 2, "label": "REPAIR_ESTIMATE", "confidence": "0.8"},
        ]
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("strategy", ["TEXT", "VISUAL"])
async def test_stub_claim_extraction_is_typed_with_page_evidence(strategy: str) -> None:
    extractor = StubStructuredClaimExtractor(model="configured", prompt_version="claims-v-test")

    candidate = await extractor.extract(strategy=strategy, bounded_content=CONTENT)  # type: ignore[arg-type]

    assert candidate.estimate_total.value == Decimal("3241.87")
    assert candidate.estimate_total.source_page == 2
    assert candidate.coverage.value == "COMPREHENSIVE"
    assert candidate.injury_indicated_on_form.value is False
    assert candidate.loss_date.schema_version == CLAIM_EXTRACTION_SCHEMA_VERSION
    assert candidate.loss_date.extraction_strategy == strategy
    assert candidate_completeness(candidate) == Decimal("0.9524")
    assert candidate_business_data(candidate)["estimate_total"] == "3241.87"


def test_claim_candidate_rejects_decisions_and_unknown_coverage() -> None:
    with pytest.raises(ValidationError):
        ClaimPacketCandidate.model_validate({"decision": "APPROVE", "queue": "FAST_TRACK"})

    stub = StubStructuredClaimExtractor(model="m", prompt_version="p")
    candidate = asyncio.run(stub.extract(strategy="TEXT", bounded_content=CONTENT))
    payload = candidate.model_dump()
    payload["coverage"]["value"] = "LIABILITY"
    with pytest.raises(ValidationError):
        ClaimPacketCandidate.model_validate(payload)


@pytest.mark.parametrize(
    "pages",
    [[2, 1], [1, 3], [1, 1], list(range(1, 22))],
)
def test_segmentation_requires_each_page_once_in_order(pages: list[int]) -> None:
    with pytest.raises(ValidationError):
        ClaimPacketSegmentation.model_validate(
            {
                "segments": [
                    {"page_number": page, "label": "OTHER", "confidence": "0.5"} for page in pages
                ]
            }
        )


def test_segment_tool_output_requires_one_label_per_page() -> None:
    SegmentClaimPacketOutput(page_count=2, labels=["LOSS_NOTICE", "REPAIR_ESTIMATE"])
    with pytest.raises(ValidationError):
        SegmentClaimPacketOutput(page_count=3, labels=["LOSS_NOTICE", "REPAIR_ESTIMATE"])


def test_lookup_outputs_are_internally_consistent() -> None:
    LookupPolicyOutput(matched=True, policy_id=uuid4(), status="ACTIVE")
    LookupPolicyOutput(matched=False, policy_id=None, status=None)
    with pytest.raises(ValidationError):
        LookupPolicyOutput(matched=True, policy_id=None, status="ACTIVE")
    LookupPolicyVehicleOutput(match_kind="NEAR", vehicle_id=uuid4())
    with pytest.raises(ValidationError):
        LookupPolicyVehicleOutput(match_kind="NONE", vehicle_id=uuid4())
    with pytest.raises(ValidationError):
        LookupPolicyVehicleOutput(match_kind="EXACT", vehicle_id=None)


def test_segment_labels_match_the_persisted_enum() -> None:
    assert set(SegmentLabel.__args__) == {label.value for label in ClaimSegmentLabel}


@pytest.mark.asyncio
async def test_langchain_claim_extractor_overrides_model_chosen_provenance() -> None:
    stub = StubStructuredClaimExtractor(model="m", prompt_version="p")
    payload = (await stub.extract(strategy="TEXT", bounded_content=CONTENT)).model_dump()
    for field in payload.values():
        field.update(provider="attacker", model="attacker", schema_version="x", prompt_version="x")
        field["evidence_excerpt"] = "e" * 500
    extractor = LangChainStructuredClaimExtractor(
        model=FakeStructuredModel(payload),  # type: ignore[arg-type]
        provider="openai",
        model_name="configured-model",
        prompt_version="claims-intake-agent-v1",
    )

    candidate = await extractor.extract(strategy="VISUAL", bounded_content=CONTENT)

    assert candidate.policy_number.provider == "openai"
    assert candidate.policy_number.model == "configured-model"
    assert candidate.policy_number.schema_version == CLAIM_EXTRACTION_SCHEMA_VERSION
    assert candidate.policy_number.extraction_strategy == "VISUAL"


@pytest.mark.asyncio
async def test_segmenters_label_every_page() -> None:
    stub = await StubStructuredClaimSegmenter().segment(page_count=3, bounded_content=CONTENT)
    assert [s.label for s in stub.segments] == ["LOSS_NOTICE", "REPAIR_ESTIMATE", "REPAIR_ESTIMATE"]

    live = LangChainStructuredClaimSegmenter(model=FakeStructuredModel(_stub_payload()))  # type: ignore[arg-type]
    assert len((await live.segment(page_count=2, bounded_content=CONTENT)).segments) == 2
    with pytest.raises(ValueError, match="every page"):
        await live.segment(page_count=3, bounded_content=CONTENT)


def test_claim_contracts_do_not_change_the_invoice_agent_surface() -> None:
    assert PROMPT_CONTENT_SHA256 == (
        "d1c22e1af7034655f36ee84f8358eefb5c2bedc5210ae9b187411a60450dbd41"
    )
    assert len(TOOL_OUTPUT_SCHEMAS) == 9
    assert not any("claim" in name or "policy" in name for name in TOOL_OUTPUT_SCHEMAS)
