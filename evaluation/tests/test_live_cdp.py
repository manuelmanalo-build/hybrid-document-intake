import asyncio
from decimal import Decimal
from pathlib import Path
from typing import Literal

from evaluation.cdp_truth import CdpTruth, ExpectedMapping, load_cdp_truth
from evaluation.live_cdp_extraction import LiveCdpReport, run_live_cdp

from intake_orchestrator.agent.cdp_contracts import (
    OnboardingPacketSegmentation,
    OnboardingRequestCandidate,
)
from intake_orchestrator.agent.cdp_extraction import (
    StructuredOnboardingExtractor,
    StructuredOnboardingSegmenter,
    StubStructuredOnboardingExtractor,
    StubStructuredOnboardingSegmenter,
)
from intake_orchestrator.agent.extraction import BoundedExtractionContent

ROOT = Path("test-data/cdp")


def _truths() -> list[CdpTruth]:
    return load_cdp_truth(ROOT / "ground_truth.csv")


def _ordered(truths: list[CdpTruth]) -> list[CdpTruth]:
    return sorted(
        truths, key=lambda item: (str(item.expected_fields["received_date"]), item.request_id)
    )


def _items(items: list[ExpectedMapping]) -> list[dict[str, object]]:
    return [
        {
            "source_field": item.source_field,
            "source_type": item.source_type,
            "proposed_attribute": item.proposed_attribute,
            "client_marked_personal": item.client_marked_personal,
            "confidence": "0.9",
            "source_page": 2,
            "evidence_excerpt": "echo",
        }
        for item in items
    ]


class EchoExtractor:
    """Returns ground truth in processing order; can inject a leak into the returned evidence."""

    def __init__(self, truths: list[CdpTruth], *, leak: bool = False) -> None:
        self._queue = _ordered(truths)
        self._leak = leak
        self.seen: list[str] = []
        self._stub = StubStructuredOnboardingExtractor(model="echo", prompt_version="echo")

    async def extract(
        self, *, strategy: Literal["TEXT", "VISUAL"], bounded_content: BoundedExtractionContent
    ) -> OnboardingRequestCandidate:
        self.seen.append(bounded_content.text)
        truth = self._queue.pop(0)
        payload = (
            await self._stub.extract(strategy=strategy, bounded_content=bounded_content)
        ).model_dump()
        for name, value in truth.expected_fields.items():
            payload[name]["value"] = value
        payload["consent_mechanism"]["value"] = truth.consent_mechanism
        payload["mappings"] = _items(truth.mappings)
        payload["fields_added_in_correspondence"] = _items(truth.mappings_added)
        if self._leak and truth.request_id == "D11":
            payload["data_owner"]["evidence_excerpt"] = "maya.chen@example.com"
        return OnboardingRequestCandidate.model_validate(payload)


class EchoSegmenter:
    def __init__(self, truths: list[CdpTruth]) -> None:
        self._queue = [truth.segment_labels for truth in _ordered(truths)]

    async def segment(
        self, *, page_count: int, bounded_content: BoundedExtractionContent
    ) -> OnboardingPacketSegmentation:
        del bounded_content
        labels = self._queue.pop(0)
        assert len(labels) == page_count
        return OnboardingPacketSegmentation.model_validate(
            {
                "segments": [
                    {"page_number": index, "label": label, "confidence": "0.9"}
                    for index, label in enumerate(labels, start=1)
                ]
            }
        )


def _run(
    extractor: StructuredOnboardingExtractor,
    segmenter: StructuredOnboardingSegmenter,
    provider: str = "fake",
) -> LiveCdpReport:
    return asyncio.run(
        run_live_cdp(
            _truths(),
            root=ROOT,
            extractor=extractor,
            segmenter=segmenter,
            strategy="TEXT",
            provider=provider,
            model="fake-model",
            timeout_seconds=5,
            clock=iter(float(i) for i in range(1000)).__next__,
        )
    )


def test_perfect_extraction_passes_every_gate() -> None:
    truths = _truths()
    extractor = EchoExtractor(truths)
    report = _run(extractor, EchoSegmenter(truths))

    assert report.extraction.field_accuracy == Decimal("1.0000")
    assert report.mapping_precision == Decimal("1.0000")
    assert report.mapping_recall == Decimal("1.0000")
    assert report.queue_accuracy == Decimal("1.0000")
    assert report.false_ready_count == 0
    assert report.privacy_gate_misses == []
    assert report.identifier_leaks == 0
    d11 = next(item for item in report.requests if item.request_id == "D11")
    assert d11.identifiers_in_packet >= 12
    assert d11.identifiers_sent_to_model == 0
    assert not any("maya.chen@example.com" in text for text in extractor.seen)


def test_an_identifier_returned_by_the_model_fails_the_leak_gate() -> None:
    truths = _truths()

    report = _run(EchoExtractor(truths, leak=True), EchoSegmenter(truths))

    assert report.identifier_leaks == 1
    d11 = next(item for item in report.requests if item.request_id == "D11")
    assert d11.identifiers_returned_by_model == 1


def test_stub_run_is_labeled_and_its_privacy_miss_is_reported() -> None:
    report = _run(
        StubStructuredOnboardingExtractor(model="stub", prompt_version="p"),
        StubStructuredOnboardingSegmenter(),
        provider="stub",
    )

    assert report.extraction.measurement == "STUB_NOT_A_MEASUREMENT"
    # Every stub packet extracts as D01, so D11 is routed as D01's duplicate instead of privacy.
    assert report.privacy_gate_misses == ["D11"]
    assert report.identifier_leaks == 0
