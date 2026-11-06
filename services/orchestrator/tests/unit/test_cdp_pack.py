import asyncio
from collections.abc import Mapping
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

import pytest
from pydantic import BaseModel

from intake_orchestrator.agent.backend import InMemoryAgentBackend
from intake_orchestrator.agent.cdp_contracts import OnboardingRequestCandidate
from intake_orchestrator.agent.cdp_extraction import (
    StubStructuredOnboardingExtractor,
    StubStructuredOnboardingSegmenter,
)
from intake_orchestrator.agent.claim_contracts import ExtractClaimInput
from intake_orchestrator.agent.extraction import BoundedExtractionContent
from intake_orchestrator.agent.graph import compile_agent_subgraph, run_agent
from intake_orchestrator.agent.models import StubChatModel
from intake_orchestrator.agent.pdf_content import read_bounded_pdf_content
from intake_orchestrator.agent.policy import AgentLimits
from intake_orchestrator.packs.base import ToolEffectContext
from intake_orchestrator.packs.cdp import CdpAgentEffects, redact_candidate

D11 = Path("test-data/cdp/packets/D11_onboarding.pdf")


class FakeSession:
    def __init__(self) -> None:
        self.added: list[object] = []

    def add(self, item: object) -> None:
        self.added.append(item)


class FakeServices:
    provider = "anthropic"
    graph_run_id = uuid4()

    def __init__(self) -> None:
        self.persisted: list[BaseModel] = []

    async def stored_document_path(
        self, session: object, *, tenant_id: UUID, document_id: UUID
    ) -> Path:
        del session, tenant_id, document_id
        return D11

    async def bounded_document_content(
        self, session: object, *, tenant_id: UUID, document_id: UUID, include_images: bool
    ) -> BoundedExtractionContent:
        del session, tenant_id, document_id
        return read_bounded_pdf_content(D11, include_images=include_images)

    async def persist_candidate(
        self, session: object, *, candidate: BaseModel, **_: object
    ) -> None:
        del session
        self.persisted.append(candidate)


class CapturingExtractor:
    def __init__(self) -> None:
        self.seen: list[str] = []
        self._stub = StubStructuredOnboardingExtractor(model="m", prompt_version="p")

    async def extract(
        self, *, strategy: Literal["TEXT", "VISUAL"], bounded_content: BoundedExtractionContent
    ) -> OnboardingRequestCandidate:
        self.seen.append(bounded_content.text)
        candidate = await self._stub.extract(strategy=strategy, bounded_content=bounded_content)
        payload = candidate.model_dump()
        payload["data_owner"]["evidence_excerpt"] = "owner maya.chen@example.com, (212) 555-0155"
        return OnboardingRequestCandidate.model_validate(payload)


def _context(session: FakeSession) -> ToolEffectContext:
    return ToolEffectContext(session, uuid4(), uuid4(), uuid4(), "op-key")  # type: ignore[arg-type]


def test_scan_records_identifier_counts_per_page_and_never_values() -> None:
    session = FakeSession()
    effects = CdpAgentEffects(
        extractor=StubStructuredOnboardingExtractor(model="m", prompt_version="p"),
        segmenter=StubStructuredOnboardingSegmenter(),
    )

    result = asyncio.run(
        effects.execute(
            FakeServices(),  # type: ignore[arg-type]
            _context(session),
            "scan_onboarding_packet",
            ExtractClaimInput(
                tenant_id=uuid4(), document_id=uuid4(), agent_run_id=uuid4(), attempt_number=1
            ),
        )
    )

    pages: list[Mapping[str, object]] = result["pages"]  # type: ignore[assignment]
    # Page 1 carries business contact emails (the form header and submitter); rules count only
    # sample-extract and data-dictionary pages, but every page is scanned and redacted.
    assert pages[0]["findings"] == {"EMAIL": 2}
    assert pages[2]["findings"] == {"EMAIL": 6, "PHONE": 6}
    assert result["total_identifiers"] == 14
    stored = {(row.page_number, row.identifier_type, row.occurrences) for row in session.added}  # type: ignore[attr-defined]
    assert stored == {(1, "EMAIL", 2), (3, "EMAIL", 6), (3, "PHONE", 6)}
    assert "example.com" not in repr(result)


def test_the_model_only_sees_redacted_text_and_evidence_is_redacted_before_storage() -> None:
    session = FakeSession()
    services = FakeServices()
    extractor = CapturingExtractor()
    effects = CdpAgentEffects(extractor=extractor, segmenter=StubStructuredOnboardingSegmenter())

    asyncio.run(
        effects.execute(
            services,  # type: ignore[arg-type]
            _context(session),
            "extract_onboarding_from_text",
            ExtractClaimInput(
                tenant_id=uuid4(), document_id=uuid4(), agent_run_id=uuid4(), attempt_number=1
            ),
        )
    )

    raw = read_bounded_pdf_content(D11, include_images=False).text
    assert "maya.chen@example.com" in raw
    assert "maya.chen@example.com" not in extractor.seen[0]
    assert "[EMAIL]" in extractor.seen[0]
    assert "[PHONE]" in extractor.seen[0]
    stored = services.persisted[0]
    assert isinstance(stored, OnboardingRequestCandidate)
    assert stored.data_owner.evidence_excerpt == "owner [EMAIL], [PHONE]"


def test_redact_candidate_scrubs_values_and_mapping_evidence() -> None:
    stub = asyncio.run(
        StubStructuredOnboardingExtractor(model="m", prompt_version="p").extract(
            strategy="VISUAL", bounded_content=BoundedExtractionContent(text="")
        )
    )
    payload = stub.model_dump()
    payload["consent_mechanism"]["value"] = "Ask sam.okafor@example.com"
    payload["mappings"][0]["evidence_excerpt"] = "e.g. ava.martin@example.com"

    cleaned = redact_candidate(OnboardingRequestCandidate.model_validate(payload))

    assert cleaned.consent_mechanism.value == "Ask [EMAIL]"
    assert cleaned.mappings[0].evidence_excerpt == "e.g. [EMAIL]"


async def _run(plan: list[str]) -> tuple[dict[str, object], list[str]]:
    backend = InMemoryAgentBackend(document_type="CDP_ONBOARDING")
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
        correlation_id="cdp-unit",
        agent_run_id=uuid4(),
    )
    return dict(outcome), [name for name, _ in backend.calls]


@pytest.mark.asyncio
async def test_onboarding_scans_before_anything_reaches_the_model() -> None:
    outcome, calls = await _run([])

    assert outcome["incomplete"] is False
    assert calls == [
        "inspect_document",
        "classify_document",
        "scan_onboarding_packet",
        "segment_onboarding_packet",
        "extract_onboarding_from_text",
        "validate_candidate",
        "submit_recommendation",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "plan",
    [
        ["inspect_document", "classify_document", "segment_onboarding_packet"],
        [
            "inspect_document",
            "classify_document",
            "scan_onboarding_packet",
            "scan_onboarding_packet",
        ],
        ["inspect_document", "classify_document", *["lookup_data_agreement"] * 3],
    ],
)
async def test_order_and_limits_are_enforced_by_the_guard(plan: list[str]) -> None:
    outcome, calls = await _run(plan)

    assert outcome["incomplete"] is True
    assert calls.count("segment_onboarding_packet") == 0 or "scan_onboarding_packet" in calls
    assert calls.count("scan_onboarding_packet") <= 1
    assert calls.count("lookup_data_agreement") <= 2
