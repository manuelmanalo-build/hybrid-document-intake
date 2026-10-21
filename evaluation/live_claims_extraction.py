"""Live, opt-in claim-packet diagnostic: page segmentation and field extraction.

Scores the production claim segmenter and extractor against `test-data/claims/ground_truth.csv`.
Diagnostic only, with the same cost-approval gate and stub labeling as `evaluation.live_extraction`.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict
from pypdf import PdfReader

from evaluation.claims_truth import ClaimTruth, load_claim_truth
from evaluation.live_extraction import (
    CaseResult,
    FixtureCase,
    LiveExtractionReport,
    Strategy,
    build_report,
    require_cost_approval,
    run_fixture_cases,
)
from intake_api.infrastructure.config import Settings
from intake_orchestrator.agent.claim_contracts import SegmentLabel
from intake_orchestrator.agent.extraction import (
    CLAIM_EXTRACTION_SCHEMA_VERSION,
    LangChainStructuredClaimExtractor,
    LangChainStructuredClaimSegmenter,
    StructuredClaimExtractor,
    StructuredClaimSegmenter,
    StubStructuredClaimExtractor,
    StubStructuredClaimSegmenter,
)
from intake_orchestrator.agent.models import create_chat_model
from intake_orchestrator.agent.pdf_content import read_bounded_pdf_content
from intake_orchestrator.agent.prompts import CLAIM_PROMPT_VERSION


class SegmentCaseResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    claim_id: str
    status: Literal["SCORED", "ERROR"]
    error_code: str | None = None
    expected: list[SegmentLabel]
    observed: list[SegmentLabel]


class LiveClaimsReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    extraction: LiveExtractionReport
    segment_page_accuracy: Decimal
    segment_error_count: int
    scanned_field_accuracy: Decimal
    digital_field_accuracy: Decimal
    segments: list[SegmentCaseResult]


async def run_live_claims(
    truths: list[ClaimTruth],
    *,
    packet_root: Path,
    extractor: StructuredClaimExtractor,
    segmenter: StructuredClaimSegmenter,
    strategy: Strategy,
    provider: str,
    model: str,
    timeout_seconds: float,
    clock: Callable[[], float] = time.perf_counter,
) -> LiveClaimsReport:
    if not truths:
        raise ValueError("At least one claim truth row is required")
    segments = [
        await _segment_case(truth, packet_root, segmenter, timeout_seconds) for truth in truths
    ]
    results = await run_fixture_cases(
        [
            FixtureCase(
                case_id=truth.claim_id,
                fixture=truth.packet_file,
                path=packet_root / truth.packet_file,
                expected_fields=truth.expected_fields,
            )
            for truth in truths
        ],
        extractor=extractor,
        strategy=strategy,
        timeout_seconds=timeout_seconds,
        clock=clock,
    )
    scanned_ids = {truth.claim_id for truth in truths if truth.scanned}
    pages = sum(len(segment.expected) for segment in segments)
    correct_pages = sum(
        expected == observed
        for segment in segments
        for expected, observed in zip(segment.expected, segment.observed, strict=False)
    )
    return LiveClaimsReport(
        extraction=build_report(
            results,
            strategy=strategy,
            provider=provider,
            model=model,
            prompt_version=CLAIM_PROMPT_VERSION,
            schema_version=CLAIM_EXTRACTION_SCHEMA_VERSION,
        ),
        segment_page_accuracy=_ratio(correct_pages, pages),
        segment_error_count=sum(segment.status == "ERROR" for segment in segments),
        scanned_field_accuracy=_field_ratio(results, scanned_ids, scanned=True),
        digital_field_accuracy=_field_ratio(results, scanned_ids, scanned=False),
        segments=segments,
    )


async def _segment_case(
    truth: ClaimTruth,
    packet_root: Path,
    segmenter: StructuredClaimSegmenter,
    timeout_seconds: float,
) -> SegmentCaseResult:
    path = packet_root / truth.packet_file
    try:
        with path.open("rb") as stream:
            page_count = len(PdfReader(stream, strict=True).pages)
        content = read_bounded_pdf_content(path, include_images=True)
        async with asyncio.timeout(timeout_seconds):
            segmentation = await segmenter.segment(page_count=page_count, bounded_content=content)
    # Recorded by type and counted; see evaluation.live_extraction.run_fixture_cases.
    except Exception as error:
        return SegmentCaseResult(
            claim_id=truth.claim_id,
            status="ERROR",
            error_code=type(error).__name__,
            expected=truth.segment_labels,
            observed=[],
        )
    return SegmentCaseResult(
        claim_id=truth.claim_id,
        status="SCORED",
        expected=truth.segment_labels,
        observed=[segment.label for segment in segmentation.segments],
    )


def build_claim_components(
    settings: Settings,
) -> tuple[StructuredClaimExtractor, StructuredClaimSegmenter]:
    if settings.agent_provider == "stub":
        return (
            StubStructuredClaimExtractor(
                model=settings.agent_model, prompt_version=CLAIM_PROMPT_VERSION
            ),
            StubStructuredClaimSegmenter(),
        )
    model = create_chat_model(settings)
    return (
        LangChainStructuredClaimExtractor(
            model=model,
            provider=settings.agent_provider,
            model_name=settings.agent_model,
            prompt_version=CLAIM_PROMPT_VERSION,
        ),
        LangChainStructuredClaimSegmenter(model=model),
    )


def main() -> None:
    settings = Settings()
    environ = dict(os.environ)
    require_cost_approval(settings, environ)
    strategy = cast(Strategy, environ.get("EVALUATION_LIVE_STRATEGY", "VISUAL").upper())
    if strategy not in ("TEXT", "VISUAL"):
        raise ValueError("EVALUATION_LIVE_STRATEGY must be TEXT or VISUAL")
    root = Path(environ.get("EVALUATION_CLAIMS_ROOT", "test-data/claims"))
    extractor, segmenter = build_claim_components(settings)
    report = asyncio.run(
        run_live_claims(
            load_claim_truth(root / "ground_truth.csv"),
            packet_root=root / "packets",
            extractor=extractor,
            segmenter=segmenter,
            strategy=strategy,
            provider=settings.agent_provider,
            model=settings.agent_model,
            timeout_seconds=float(settings.agent_model_timeout_seconds),
        )
    )
    output = Path(environ.get("EVALUATION_LIVE_REPORT", "evaluation-live-claims-report.json"))
    output.write_text(f"{report.model_dump_json(indent=2)}\n")
    summary = report.model_dump_json(indent=2, exclude={"segments": True, "extraction": {"cases"}})
    sys.stdout.write(f"{summary}\nFull report: {output}\n")
    if report.extraction.error_count or report.segment_error_count:
        raise SystemExit(1)


def _field_ratio(results: list[CaseResult], scanned_ids: set[str], *, scanned: bool) -> Decimal:
    fields = [
        field.match
        for result in results
        if result.status == "SCORED" and (result.case_id in scanned_ids) == scanned
        for field in result.fields
    ]
    return _ratio(sum(fields), len(fields))


def _ratio(numerator: int, denominator: int) -> Decimal:
    if denominator == 0:
        return Decimal("0.0000")
    return (Decimal(numerator) / Decimal(denominator)).quantize(Decimal("0.0001"))


if __name__ == "__main__":
    main()
