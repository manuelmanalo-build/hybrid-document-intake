"""Live, opt-in campaign-brief diagnostic (ADR 0009).

Runs the production brief segmenter and extractor over `test-data/briefs`, then applies the real
CB rules and brief-routing-v1 to what was extracted, using the reference CSVs offline. It reports
page-label accuracy, structured-field accuracy, deliverable precision and recall, queue agreement
with ground truth, and the hard gate: zero false READY_FOR_PLANNING assignments.

Diagnostic only, with the same cost-approval gate and stub labeling as the other live runners.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from collections import Counter
from collections.abc import Callable
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict
from pypdf import PdfReader

from evaluation.briefs_truth import (
    FREE_TEXT_FIELDS,
    SCORED_FIELDS,
    BriefTruth,
    ExpectedDeliverable,
    load_brief_truth,
    load_lead_times,
    load_sows_by_brand,
)
from evaluation.live_extraction import (
    CaseResult,
    LiveExtractionReport,
    Strategy,
    build_report,
    require_cost_approval,
    score_candidate,
)
from intake_api.infrastructure.config import Settings
from intake_orchestrator.agent.brief_contracts import BriefSegmentLabel, CampaignBriefCandidate
from intake_orchestrator.agent.brief_extraction import (
    BRIEF_EXTRACTION_SCHEMA_VERSION,
    LangChainStructuredBriefExtractor,
    LangChainStructuredBriefSegmenter,
    StructuredBriefExtractor,
    StructuredBriefSegmenter,
    StubStructuredBriefExtractor,
    StubStructuredBriefSegmenter,
)
from intake_orchestrator.agent.extraction import candidate_business_data
from intake_orchestrator.agent.models import create_chat_model
from intake_orchestrator.agent.pdf_content import read_bounded_pdf_content
from intake_orchestrator.agent.prompts import BRIEF_PROMPT_VERSION
from intake_orchestrator.brief_validation import (
    BriefValidationContext,
    LeadTime,
    SowFacts,
    resolve_sow,
    route_brief,
    safe_brief_data,
    validate_brief_rules,
)

READY = "READY_FOR_PLANNING"


class BriefCaseResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    brief_id: str
    status: Literal["SCORED", "ERROR"]
    error_code: str | None = None
    expected_segments: list[BriefSegmentLabel]
    observed_segments: list[BriefSegmentLabel]
    expected_deliverables: int
    observed_deliverables: int
    matched_deliverables: int
    free_text_present: int
    expected_queue: str
    observed_queue: str | None
    observed_flags: list[str]


class LiveBriefsReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    extraction: LiveExtractionReport
    segment_page_accuracy: Decimal
    deliverable_precision: Decimal
    deliverable_recall: Decimal
    free_text_presence: Decimal
    queue_accuracy: Decimal
    false_ready_count: int
    queue_confusion: dict[str, dict[str, int]]
    scanned_field_accuracy: Decimal
    digital_field_accuracy: Decimal
    briefs: list[BriefCaseResult]


async def run_live_briefs(
    truths: list[BriefTruth],
    *,
    root: Path,
    extractor: StructuredBriefExtractor,
    segmenter: StructuredBriefSegmenter,
    strategy: Strategy,
    provider: str,
    model: str,
    timeout_seconds: float,
    clock: Callable[[], float] = time.perf_counter,
) -> LiveBriefsReport:
    if not truths:
        raise ValueError("At least one brief truth row is required")
    sows = load_sows_by_brand(root)
    lead_times = load_lead_times(root)
    seen: set[tuple[str, str, date]] = set()
    cases: list[CaseResult] = []
    briefs: list[BriefCaseResult] = []
    expected_received = {
        truth.brief_id: cast(date, truth.expected_fields["received_date"]) for truth in truths
    }
    for truth in sorted(truths, key=lambda item: (expected_received[item.brief_id], item.brief_id)):
        started = clock()
        path = root / "packets" / truth.packet_file
        try:
            with path.open("rb") as stream:
                page_count = len(PdfReader(stream, strict=True).pages)
            async with asyncio.timeout(timeout_seconds):
                segmentation = await segmenter.segment(
                    page_count=page_count,
                    bounded_content=read_bounded_pdf_content(path, include_images=True),
                )
            async with asyncio.timeout(timeout_seconds):
                candidate = await extractor.extract(
                    strategy=strategy,
                    bounded_content=read_bounded_pdf_content(
                        path, include_images=strategy == "VISUAL"
                    ),
                )
        # Recorded by type and counted; see evaluation.live_extraction.run_fixture_cases.
        except Exception as error:
            cases.append(
                CaseResult(
                    case_id=truth.brief_id,
                    fixture=truth.packet_file,
                    status="ERROR",
                    error_code=type(error).__name__,
                    latency_ms=int((clock() - started) * 1000),
                    fields=[],
                )
            )
            briefs.append(_error_case(truth, type(error).__name__))
            continue
        cases.append(
            CaseResult(
                case_id=truth.brief_id,
                fixture=truth.packet_file,
                status="SCORED",
                latency_ms=int((clock() - started) * 1000),
                fields=score_candidate(
                    candidate, {name: truth.expected_fields[name] for name in SCORED_FIELDS}
                ),
            )
        )
        labels: list[BriefSegmentLabel] = [segment.label for segment in segmentation.segments]
        queue, flags = _route(candidate, labels, sows, lead_times, seen)
        observed = [*candidate.deliverables, *candidate.deliverables_added_in_correspondence]
        expected = [*truth.deliverables, *truth.deliverables_added]
        briefs.append(
            BriefCaseResult(
                brief_id=truth.brief_id,
                status="SCORED",
                expected_segments=truth.segment_labels,
                observed_segments=labels,
                expected_deliverables=len(expected),
                observed_deliverables=len(observed),
                matched_deliverables=_matched(
                    expected,
                    [
                        ExpectedDeliverable(
                            asset_type=item.asset_type,
                            quantity=item.quantity,
                            due_date=item.due_date,
                        )
                        for item in observed
                    ],
                ),
                free_text_present=sum(
                    bool(getattr(candidate, name).value) for name in FREE_TEXT_FIELDS
                ),
                expected_queue=truth.expected_queue,
                observed_queue=queue,
                observed_flags=flags,
            )
        )
    return _report(cases, briefs, truths, strategy=strategy, provider=provider, model=model)


def _route(
    candidate: CampaignBriefCandidate,
    labels: list[BriefSegmentLabel],
    sows: dict[str, tuple[SowFacts, ...]],
    lead_times: dict[str, LeadTime],
    seen: set[tuple[str, str, date]],
) -> tuple[str, list[str]]:
    data = safe_brief_data(candidate_business_data(candidate))
    brand_sows = sows.get(data.brand or "", ())
    sow = resolve_sow(data, brand_sows)
    key = (
        sow.sow_number if sow else "",
        " ".join((data.campaign_name or "").casefold().split()),
        data.launch_date or date.min,
    )
    context = BriefValidationContext(
        business_date=data.received_date or date.min,
        brand_sows=brand_sows,
        lead_times=lead_times,
        duplicate_of_earlier_brief=bool(sow) and key in seen,
        segment_labels=tuple(labels),
    )
    if sow is not None and data.campaign_name and data.launch_date:
        seen.add(key)
    route = route_brief(validate_brief_rules(data, context))
    return route.queue, route.flags


def _matched(expected: list[ExpectedDeliverable], observed: list[ExpectedDeliverable]) -> int:
    remaining = Counter(observed)
    matched = 0
    for item in expected:
        if remaining[item] > 0:
            remaining[item] -= 1
            matched += 1
    return matched


def _error_case(truth: BriefTruth, error_code: str) -> BriefCaseResult:
    return BriefCaseResult(
        brief_id=truth.brief_id,
        status="ERROR",
        error_code=error_code,
        expected_segments=truth.segment_labels,
        observed_segments=[],
        expected_deliverables=len(truth.deliverables) + len(truth.deliverables_added),
        observed_deliverables=0,
        matched_deliverables=0,
        free_text_present=0,
        expected_queue=truth.expected_queue,
        observed_queue=None,
        observed_flags=[],
    )


def _report(
    cases: list[CaseResult],
    briefs: list[BriefCaseResult],
    truths: list[BriefTruth],
    *,
    strategy: Strategy,
    provider: str,
    model: str,
) -> LiveBriefsReport:
    scanned = {truth.brief_id for truth in truths if truth.scanned}
    pages = sum(len(brief.expected_segments) for brief in briefs)
    correct_pages = sum(
        expected == observed
        for brief in briefs
        for expected, observed in zip(
            brief.expected_segments, brief.observed_segments, strict=False
        )
    )
    confusion: dict[str, dict[str, int]] = {}
    for brief in briefs:
        row = confusion.setdefault(brief.expected_queue, {})
        observed = brief.observed_queue or "ERROR"
        row[observed] = row.get(observed, 0) + 1
    scored = [brief for brief in briefs if brief.status == "SCORED"]
    return LiveBriefsReport(
        extraction=build_report(
            cases,
            strategy=strategy,
            provider=provider,
            model=model,
            prompt_version=BRIEF_PROMPT_VERSION,
            schema_version=BRIEF_EXTRACTION_SCHEMA_VERSION,
        ),
        segment_page_accuracy=_ratio(correct_pages, pages),
        deliverable_precision=_ratio(
            sum(brief.matched_deliverables for brief in briefs),
            sum(brief.observed_deliverables for brief in briefs),
        ),
        deliverable_recall=_ratio(
            sum(brief.matched_deliverables for brief in briefs),
            sum(brief.expected_deliverables for brief in briefs),
        ),
        free_text_presence=_ratio(
            sum(brief.free_text_present for brief in scored),
            len(scored) * len(FREE_TEXT_FIELDS),
        ),
        queue_accuracy=_ratio(
            sum(brief.observed_queue == brief.expected_queue for brief in briefs), len(briefs)
        ),
        false_ready_count=sum(
            brief.observed_queue == READY and brief.expected_queue != READY for brief in briefs
        ),
        queue_confusion=confusion,
        scanned_field_accuracy=_field_ratio(cases, scanned, scanned=True),
        digital_field_accuracy=_field_ratio(cases, scanned, scanned=False),
        briefs=briefs,
    )


def build_brief_components(
    settings: Settings,
) -> tuple[StructuredBriefExtractor, StructuredBriefSegmenter]:
    if settings.agent_provider == "stub":
        return (
            StubStructuredBriefExtractor(
                model=settings.agent_model, prompt_version=BRIEF_PROMPT_VERSION
            ),
            StubStructuredBriefSegmenter(),
        )
    model = create_chat_model(settings)
    return (
        LangChainStructuredBriefExtractor(
            model=model,
            provider=settings.agent_provider,
            model_name=settings.agent_model,
            prompt_version=BRIEF_PROMPT_VERSION,
        ),
        LangChainStructuredBriefSegmenter(model=model),
    )


def main() -> None:
    settings = Settings()
    environ = dict(os.environ)
    require_cost_approval(settings, environ)
    strategy = cast(Strategy, environ.get("EVALUATION_LIVE_STRATEGY", "VISUAL").upper())
    if strategy not in ("TEXT", "VISUAL"):
        raise ValueError("EVALUATION_LIVE_STRATEGY must be TEXT or VISUAL")
    root = Path(environ.get("EVALUATION_BRIEFS_ROOT", "test-data/briefs"))
    extractor, segmenter = build_brief_components(settings)
    report = asyncio.run(
        run_live_briefs(
            load_brief_truth(root / "ground_truth.csv"),
            root=root,
            extractor=extractor,
            segmenter=segmenter,
            strategy=strategy,
            provider=settings.agent_provider,
            model=settings.agent_model,
            timeout_seconds=float(settings.agent_model_timeout_seconds),
        )
    )
    output = Path(environ.get("EVALUATION_LIVE_REPORT", "evaluation-live-briefs-report.json"))
    output.write_text(f"{report.model_dump_json(indent=2)}\n")
    summary = report.model_dump_json(indent=2, exclude={"briefs": True, "extraction": {"cases"}})
    sys.stdout.write(f"{summary}\nFull report: {output}\n")
    if report.extraction.error_count or report.false_ready_count:
        raise SystemExit(1)


def _field_ratio(cases: list[CaseResult], scanned_ids: set[str], *, scanned: bool) -> Decimal:
    matches = [
        field.match
        for case in cases
        if case.status == "SCORED" and (case.case_id in scanned_ids) == scanned
        for field in case.fields
    ]
    return _ratio(sum(matches), len(matches))


def _ratio(numerator: int, denominator: int) -> Decimal:
    if denominator == 0:
        return Decimal("0.0000")
    return (Decimal(numerator) / Decimal(denominator)).quantize(Decimal("0.0001"))


if __name__ == "__main__":
    main()
