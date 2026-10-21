import asyncio
from decimal import Decimal
from pathlib import Path
from typing import Literal

from evaluation.claims_truth import ClaimTruth, ExpectedQueue, load_claim_truth
from evaluation.live_claims_extraction import LiveClaimsReport, run_live_claims

from intake_api.domain.enums import ReviewQueue
from intake_orchestrator.agent.claim_contracts import (
    ClaimPacketCandidate,
    ClaimPacketSegmentation,
)
from intake_orchestrator.agent.extraction import (
    BoundedExtractionContent,
    StructuredClaimExtractor,
    StructuredClaimSegmenter,
    StubStructuredClaimExtractor,
    StubStructuredClaimSegmenter,
)

ROOT = Path("test-data/claims")


def _truths() -> list[ClaimTruth]:
    return load_claim_truth(ROOT / "ground_truth.csv")


class EchoClaimExtractor:
    def __init__(self, truths: list[ClaimTruth]) -> None:
        self._queue = list(truths)
        self._stub = StubStructuredClaimExtractor(model="echo", prompt_version="echo")

    async def extract(
        self, *, strategy: Literal["TEXT", "VISUAL"], bounded_content: BoundedExtractionContent
    ) -> ClaimPacketCandidate:
        payload = (
            await self._stub.extract(strategy=strategy, bounded_content=bounded_content)
        ).model_dump()
        for name, value in self._queue.pop(0).expected_fields.items():
            payload[name]["value"] = value
        return ClaimPacketCandidate.model_validate(payload)


class EchoSegmenter:
    def __init__(self, truths: list[ClaimTruth]) -> None:
        self._queue = list(truths)

    async def segment(
        self, *, page_count: int, bounded_content: BoundedExtractionContent
    ) -> ClaimPacketSegmentation:
        del bounded_content
        labels = self._queue.pop(0).segment_labels
        assert len(labels) == page_count
        return ClaimPacketSegmentation.model_validate(
            {
                "segments": [
                    {"page_number": i, "label": label, "confidence": "0.9"}
                    for i, label in enumerate(labels, start=1)
                ]
            }
        )


def _run(
    extractor: StructuredClaimExtractor,
    segmenter: StructuredClaimSegmenter,
    provider: str = "fake",
) -> LiveClaimsReport:
    return asyncio.run(
        run_live_claims(
            _truths(),
            packet_root=ROOT / "packets",
            extractor=extractor,
            segmenter=segmenter,
            strategy="VISUAL",
            provider=provider,
            model="fake-model",
            timeout_seconds=5,
            clock=iter(float(i) for i in range(1000)).__next__,
        )
    )


def test_ground_truth_adapter_maps_conflicts_and_segments() -> None:
    truths = {truth.claim_id: truth for truth in _truths()}

    assert len(truths) == 20
    c09 = truths["C09"].expected_fields
    assert c09["vin_reported"] != c09["vin_on_estimate"]
    assert truths["C10"].expected_fields["injury_mentioned_in_correspondence"] is True
    assert truths["C10"].expected_fields["injury_indicated_on_form"] is False
    assert truths["C14"].segment_labels == ["CORRESPONDENCE", "REPAIR_ESTIMATE"]
    assert truths["C05"].expected_fields["estimate_total"] == Decimal("7420.60")
    assert {truth.scanned for truth in truths.values()} == {True, False}


def test_expected_queues_match_the_persisted_enum() -> None:
    assert set(ExpectedQueue.__args__) == {queue.value for queue in ReviewQueue}


def test_perfect_components_score_perfectly() -> None:
    truths = _truths()
    report = _run(EchoClaimExtractor(truths), EchoSegmenter(truths))

    assert report.extraction.measurement == "LIVE"
    assert report.extraction.case_count == 20
    assert report.extraction.field_accuracy == Decimal("1.0000")
    assert report.segment_page_accuracy == Decimal("1.0000")
    assert report.scanned_field_accuracy == Decimal("1.0000")
    assert report.segment_error_count == 0


def test_stub_run_is_labeled_and_matches_only_its_mirrored_fixture() -> None:
    report = _run(
        StubStructuredClaimExtractor(model="stub", prompt_version="p"),
        StubStructuredClaimSegmenter(),
        provider="stub",
    )

    assert report.extraction.measurement == "STUB_NOT_A_MEASUREMENT"
    c01 = next(case for case in report.extraction.cases if case.case_id == "C01")
    assert all(field.match for field in c01.fields)
    assert report.extraction.field_accuracy < Decimal("1.0000")
