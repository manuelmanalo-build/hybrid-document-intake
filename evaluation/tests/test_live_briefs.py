import asyncio
from decimal import Decimal
from pathlib import Path
from typing import Literal

from evaluation.briefs_truth import BriefTruth, ExpectedDeliverable, load_brief_truth
from evaluation.live_briefs_extraction import LiveBriefsReport, run_live_briefs

from intake_orchestrator.agent.brief_contracts import (
    BriefPacketSegmentation,
    CampaignBriefCandidate,
)
from intake_orchestrator.agent.brief_extraction import (
    StructuredBriefExtractor,
    StructuredBriefSegmenter,
    StubStructuredBriefExtractor,
    StubStructuredBriefSegmenter,
)
from intake_orchestrator.agent.extraction import BoundedExtractionContent

ROOT = Path("test-data/briefs")


def _truths() -> list[BriefTruth]:
    return load_brief_truth(ROOT / "ground_truth.csv")


def _items(items: list[ExpectedDeliverable]) -> list[dict[str, object]]:
    return [
        {
            "asset_type": item.asset_type,
            "quantity": item.quantity,
            "description": "echo",
            "due_date": item.due_date,
            "confidence": "0.9",
            "source_page": 1,
            "evidence_excerpt": "echo",
        }
        for item in items
    ]


class EchoExtractor:
    """Returns each brief's ground truth, in received order, optionally dropping one value."""

    def __init__(self, truths: list[BriefTruth], drop: tuple[str, str] | None = None) -> None:
        self._by_file = {truth.packet_file: truth for truth in truths}
        self._order = [
            truth.packet_file
            for truth in sorted(
                truths, key=lambda item: (str(item.expected_fields["received_date"]), item.brief_id)
            )
        ]
        self._drop = drop
        self._stub = StubStructuredBriefExtractor(model="echo", prompt_version="echo")

    async def extract(
        self, *, strategy: Literal["TEXT", "VISUAL"], bounded_content: BoundedExtractionContent
    ) -> CampaignBriefCandidate:
        truth = self._by_file[self._order.pop(0)]
        payload = (
            await self._stub.extract(strategy=strategy, bounded_content=bounded_content)
        ).model_dump()
        for name, value in truth.expected_fields.items():
            payload[name]["value"] = value
            if self._drop == (truth.brief_id, name):
                payload[name]["value"] = None
        payload["deliverables"] = _items(truth.deliverables)
        payload["deliverables_added_in_correspondence"] = _items(truth.deliverables_added)
        return CampaignBriefCandidate.model_validate(payload)


class EchoSegmenter:
    def __init__(self, truths: list[BriefTruth]) -> None:
        self._queue = [
            truth.segment_labels
            for truth in sorted(
                truths, key=lambda item: (str(item.expected_fields["received_date"]), item.brief_id)
            )
        ]

    async def segment(
        self, *, page_count: int, bounded_content: BoundedExtractionContent
    ) -> BriefPacketSegmentation:
        del bounded_content
        labels = self._queue.pop(0)
        assert len(labels) == page_count
        return BriefPacketSegmentation.model_validate(
            {
                "segments": [
                    {"page_number": index, "label": label, "confidence": "0.9"}
                    for index, label in enumerate(labels, start=1)
                ]
            }
        )


def _run(
    extractor: StructuredBriefExtractor, segmenter: StructuredBriefSegmenter, provider: str = "fake"
) -> LiveBriefsReport:
    return asyncio.run(
        run_live_briefs(
            _truths(),
            root=ROOT,
            extractor=extractor,
            segmenter=segmenter,
            strategy="VISUAL",
            provider=provider,
            model="fake-model",
            timeout_seconds=5,
            clock=iter(float(i) for i in range(1000)).__next__,
        )
    )


def test_perfect_extraction_scores_perfectly_end_to_end() -> None:
    truths = _truths()
    report = _run(EchoExtractor(truths), EchoSegmenter(truths))

    assert report.extraction.measurement == "LIVE"
    assert report.extraction.field_accuracy == Decimal("1.0000")
    assert report.segment_page_accuracy == Decimal("1.0000")
    assert report.deliverable_precision == Decimal("1.0000")
    assert report.deliverable_recall == Decimal("1.0000")
    assert report.queue_accuracy == Decimal("1.0000")
    assert report.false_ready_count == 0


def test_missing_the_email_launch_date_is_caught_as_a_false_ready() -> None:
    truths = _truths()
    report = _run(
        EchoExtractor(truths, drop=("B12", "launch_date_in_correspondence")), EchoSegmenter(truths)
    )

    b12 = next(brief for brief in report.briefs if brief.brief_id == "B12")
    assert b12.observed_queue == "READY_FOR_PLANNING"
    assert report.false_ready_count == 1
    assert report.queue_confusion["RUSH_REVIEW"]["READY_FOR_PLANNING"] == 1


def test_stub_run_is_labeled_and_matches_only_its_mirrored_brief() -> None:
    report = _run(
        StubStructuredBriefExtractor(model="stub", prompt_version="p"),
        StubStructuredBriefSegmenter(),
        provider="stub",
    )

    assert report.extraction.measurement == "STUB_NOT_A_MEASUREMENT"
    b01 = next(case for case in report.extraction.cases if case.case_id == "B01")
    assert all(field.match for field in b01.fields)
    assert report.extraction.field_accuracy < Decimal("1.0000")
