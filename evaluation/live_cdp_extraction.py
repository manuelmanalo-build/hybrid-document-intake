"""Live, opt-in CDP-onboarding diagnostic (ADR 0010).

Mirrors the pack: the deterministic scanner reads each packet's pages, the text is redacted, and
only redacted content reaches the segmenter and extractor. The real CD rules and cdp-routing-v1
then run offline against the reference CSVs. Reported: page labels, request-field accuracy, mapping
precision and recall, queue agreement, and three gates the command fails on:

1. zero false READY_TO_BUILD;
2. every packet whose sample carries identifiers lands in PRIVACY_REVIEW (unless ground truth
   routes it to duplicate or agreement review first);
3. zero raw identifiers in what was sent to, or returned by, the model.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from collections import Counter
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict
from pypdf import PdfReader

from evaluation.cdp_truth import (
    SCORED_FIELDS,
    CdpTruth,
    ExpectedMapping,
    load_agreements,
    load_attributes,
    load_cdp_truth,
    load_connectors,
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
from intake_orchestrator.agent.cdp_contracts import (
    OnboardingRequestCandidate,
    OnboardingSegmentLabel,
)
from intake_orchestrator.agent.cdp_extraction import (
    CDP_EXTRACTION_SCHEMA_VERSION,
    LangChainStructuredOnboardingExtractor,
    LangChainStructuredOnboardingSegmenter,
    StructuredOnboardingExtractor,
    StructuredOnboardingSegmenter,
    StubStructuredOnboardingExtractor,
    StubStructuredOnboardingSegmenter,
)
from intake_orchestrator.agent.extraction import BoundedExtractionContent, candidate_business_data
from intake_orchestrator.agent.models import create_chat_model
from intake_orchestrator.agent.pdf_content import read_bounded_pdf_content
from intake_orchestrator.agent.prompts import CDP_PROMPT_VERSION
from intake_orchestrator.cdp_validation import (
    AgreementFacts,
    AttributeFacts,
    ConnectorFacts,
    OnboardingValidationContext,
    resolve_agreement,
    route_onboarding,
    safe_onboarding_data,
    validate_onboarding_rules,
)
from intake_orchestrator.pii import redact_identifiers, scan_identifiers

READY = "READY_TO_BUILD"
PRIVACY = "PRIVACY_REVIEW"
OUTRANKS_PRIVACY = frozenset({"DUPLICATE_REVIEW", "AGREEMENT_REVIEW"})


class CdpCaseResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    request_id: str
    status: Literal["SCORED", "ERROR"]
    error_code: str | None = None
    expected_segments: list[OnboardingSegmentLabel]
    observed_segments: list[OnboardingSegmentLabel]
    expected_mappings: int
    observed_mappings: int
    matched_mappings: int
    identifiers_in_packet: int
    identifiers_sent_to_model: int
    identifiers_returned_by_model: int
    expected_queue: str
    observed_queue: str | None
    observed_flags: list[str]


class LiveCdpReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    extraction: LiveExtractionReport
    segment_page_accuracy: Decimal
    mapping_precision: Decimal
    mapping_recall: Decimal
    queue_accuracy: Decimal
    queue_confusion: dict[str, dict[str, int]]
    false_ready_count: int
    privacy_gate_misses: list[str]
    identifier_leaks: int
    scanned_field_accuracy: Decimal
    digital_field_accuracy: Decimal
    requests: list[CdpCaseResult]


async def run_live_cdp(
    truths: list[CdpTruth],
    *,
    root: Path,
    extractor: StructuredOnboardingExtractor,
    segmenter: StructuredOnboardingSegmenter,
    strategy: Strategy,
    provider: str,
    model: str,
    timeout_seconds: float,
    clock: Callable[[], float] = time.perf_counter,
) -> LiveCdpReport:
    if not truths:
        raise ValueError("At least one onboarding truth row is required")
    agreements = load_agreements(root)
    attributes = load_attributes(root)
    connectors = load_connectors(root)
    seen: set[tuple[str, str, str]] = set()
    cases: list[CaseResult] = []
    requests: list[CdpCaseResult] = []
    ordered = sorted(
        truths, key=lambda item: (str(item.expected_fields["received_date"]), item.request_id)
    )
    for truth in ordered:
        started = clock()
        path = root / "packets" / truth.packet_file
        try:
            with path.open("rb") as stream:
                page_count = len(PdfReader(stream, strict=True).pages)
            raw = read_bounded_pdf_content(path, include_images=True)
            pages = _page_texts(raw.text)
            findings = {
                page: scan_identifiers(text) for page, text in pages.items() if text.strip()
            }
            without_text = frozenset(
                page for page in range(1, page_count + 1) if not pages.get(page, "").strip()
            )
            safe = BoundedExtractionContent(
                text=redact_identifiers(raw.text), image_data_urls=raw.image_data_urls
            )
            sent = sum(scan_identifiers(safe.text).values())
            async with asyncio.timeout(timeout_seconds):
                segmentation = await segmenter.segment(page_count=page_count, bounded_content=safe)
            async with asyncio.timeout(timeout_seconds):
                candidate = await extractor.extract(
                    strategy=strategy,
                    bounded_content=safe
                    if strategy == "VISUAL"
                    else BoundedExtractionContent(text=safe.text),
                )
        # Recorded by type and counted; see evaluation.live_extraction.run_fixture_cases.
        except Exception as error:
            cases.append(
                CaseResult(
                    case_id=truth.request_id,
                    fixture=truth.packet_file,
                    status="ERROR",
                    error_code=type(error).__name__,
                    latency_ms=int((clock() - started) * 1000),
                    fields=[],
                )
            )
            requests.append(_error_case(truth, type(error).__name__))
            continue
        cases.append(
            CaseResult(
                case_id=truth.request_id,
                fixture=truth.packet_file,
                status="SCORED",
                latency_ms=int((clock() - started) * 1000),
                fields=score_candidate(
                    candidate, {name: truth.expected_fields[name] for name in SCORED_FIELDS}
                ),
            )
        )
        labels: list[OnboardingSegmentLabel] = [segment.label for segment in segmentation.segments]
        queue, flags = _route(
            candidate,
            labels,
            findings,
            without_text,
            agreements=agreements,
            attributes=attributes,
            connectors=connectors,
            seen=seen,
        )
        observed = [*candidate.mappings, *candidate.fields_added_in_correspondence]
        expected = [*truth.mappings, *truth.mappings_added]
        requests.append(
            CdpCaseResult(
                request_id=truth.request_id,
                status="SCORED",
                expected_segments=truth.segment_labels,
                observed_segments=labels,
                expected_mappings=len(expected),
                observed_mappings=len(observed),
                matched_mappings=_matched(
                    expected,
                    [
                        ExpectedMapping(
                            source_field=item.source_field,
                            source_type=item.source_type,
                            proposed_attribute=item.proposed_attribute,
                            client_marked_personal=item.client_marked_personal,
                        )
                        for item in observed
                    ],
                ),
                identifiers_in_packet=sum(sum(counts.values()) for counts in findings.values()),
                identifiers_sent_to_model=sent,
                identifiers_returned_by_model=sum(
                    scan_identifiers(candidate.model_dump_json()).values()
                ),
                expected_queue=truth.expected_queue,
                observed_queue=queue,
                observed_flags=flags,
            )
        )
    return _report(cases, requests, truths, strategy=strategy, provider=provider, model=model)


def _route(
    candidate: OnboardingRequestCandidate,
    labels: list[OnboardingSegmentLabel],
    findings: dict[int, dict[str, int]],
    without_text: frozenset[int],
    *,
    agreements: dict[str, tuple[AgreementFacts, ...]],
    attributes: dict[str, AttributeFacts],
    connectors: dict[str, ConnectorFacts],
    seen: set[tuple[str, str, str]],
) -> tuple[str, list[str]]:
    data = safe_onboarding_data(candidate_business_data(candidate))
    client_agreements = agreements.get(data.client_name or "", ())
    agreement = resolve_agreement(data, client_agreements)
    key = (
        agreement.agreement_number if agreement else "",
        data.connector_type or "",
        " ".join((data.source_name or "").casefold().split()),
    )
    context = OnboardingValidationContext(
        agreements=client_agreements,
        attributes=attributes,
        connectors=connectors,
        duplicate_of_earlier_request=bool(agreement) and key in seen,
        segment_labels=tuple(labels),
        page_findings=findings,
        pages_without_text=without_text,
    )
    if agreement is not None and data.connector_type and data.source_name:
        seen.add(key)
    route = route_onboarding(validate_onboarding_rules(data, context))
    return route.queue, route.flags


def _matched(expected: list[ExpectedMapping], observed: list[ExpectedMapping]) -> int:
    def normal(item: ExpectedMapping) -> tuple[str, str, str | None, bool]:
        return (
            item.source_field.casefold(),
            item.source_type,
            item.proposed_attribute,
            item.client_marked_personal,
        )

    remaining = Counter(normal(item) for item in observed)
    matched = 0
    for item in expected:
        key = normal(item)
        if remaining[key] > 0:
            remaining[key] -= 1
            matched += 1
    return matched


def _page_texts(text: str) -> dict[int, str]:
    pages: dict[int, str] = {}
    for chunk in text.split("[page ")[1:]:
        number, _, body = chunk.partition("]")
        if number.isdigit():
            pages[int(number)] = body
    return pages


def _error_case(truth: CdpTruth, error_code: str) -> CdpCaseResult:
    return CdpCaseResult(
        request_id=truth.request_id,
        status="ERROR",
        error_code=error_code,
        expected_segments=truth.segment_labels,
        observed_segments=[],
        expected_mappings=len(truth.mappings) + len(truth.mappings_added),
        observed_mappings=0,
        matched_mappings=0,
        identifiers_in_packet=0,
        identifiers_sent_to_model=0,
        identifiers_returned_by_model=0,
        expected_queue=truth.expected_queue,
        observed_queue=None,
        observed_flags=[],
    )


def _report(
    cases: list[CaseResult],
    requests: list[CdpCaseResult],
    truths: list[CdpTruth],
    *,
    strategy: Strategy,
    provider: str,
    model: str,
) -> LiveCdpReport:
    truth_by_id = {truth.request_id: truth for truth in truths}
    scanned = {truth.request_id for truth in truths if truth.scanned}
    pages = sum(len(item.expected_segments) for item in requests)
    correct = sum(
        expected == observed
        for item in requests
        for expected, observed in zip(item.expected_segments, item.observed_segments, strict=False)
    )
    confusion: dict[str, dict[str, int]] = {}
    for item in requests:
        row = confusion.setdefault(item.expected_queue, {})
        observed_queue = item.observed_queue or "ERROR"
        row[observed_queue] = row.get(observed_queue, 0) + 1
    privacy_misses = [
        item.request_id
        for item in requests
        if truth_by_id[item.request_id].sample_findings
        and item.expected_queue not in OUTRANKS_PRIVACY
        and item.observed_queue != PRIVACY
    ]
    return LiveCdpReport(
        extraction=build_report(
            cases,
            strategy=strategy,
            provider=provider,
            model=model,
            prompt_version=CDP_PROMPT_VERSION,
            schema_version=CDP_EXTRACTION_SCHEMA_VERSION,
        ),
        segment_page_accuracy=_ratio(correct, pages),
        mapping_precision=_ratio(
            sum(item.matched_mappings for item in requests),
            sum(item.observed_mappings for item in requests),
        ),
        mapping_recall=_ratio(
            sum(item.matched_mappings for item in requests),
            sum(item.expected_mappings for item in requests),
        ),
        queue_accuracy=_ratio(
            sum(item.observed_queue == item.expected_queue for item in requests), len(requests)
        ),
        queue_confusion=confusion,
        false_ready_count=sum(
            item.observed_queue == READY and item.expected_queue != READY for item in requests
        ),
        privacy_gate_misses=privacy_misses,
        identifier_leaks=sum(
            item.identifiers_sent_to_model + item.identifiers_returned_by_model for item in requests
        ),
        scanned_field_accuracy=_field_ratio(cases, scanned, scanned=True),
        digital_field_accuracy=_field_ratio(cases, scanned, scanned=False),
        requests=requests,
    )


def build_cdp_components(
    settings: Settings,
) -> tuple[StructuredOnboardingExtractor, StructuredOnboardingSegmenter]:
    if settings.agent_provider == "stub":
        return (
            StubStructuredOnboardingExtractor(
                model=settings.agent_model, prompt_version=CDP_PROMPT_VERSION
            ),
            StubStructuredOnboardingSegmenter(),
        )
    model = create_chat_model(settings)
    return (
        LangChainStructuredOnboardingExtractor(
            model=model,
            provider=settings.agent_provider,
            model_name=settings.agent_model,
            prompt_version=CDP_PROMPT_VERSION,
        ),
        LangChainStructuredOnboardingSegmenter(model=model),
    )


def main() -> None:
    settings = Settings()
    environ = dict(os.environ)
    require_cost_approval(settings, environ)
    strategy = cast(Strategy, environ.get("EVALUATION_LIVE_STRATEGY", "VISUAL").upper())
    if strategy not in ("TEXT", "VISUAL"):
        raise ValueError("EVALUATION_LIVE_STRATEGY must be TEXT or VISUAL")
    root = Path(environ.get("EVALUATION_CDP_ROOT", "test-data/cdp"))
    extractor, segmenter = build_cdp_components(settings)
    report = asyncio.run(
        run_live_cdp(
            load_cdp_truth(root / "ground_truth.csv"),
            root=root,
            extractor=extractor,
            segmenter=segmenter,
            strategy=strategy,
            provider=settings.agent_provider,
            model=settings.agent_model,
            timeout_seconds=float(settings.agent_model_timeout_seconds),
        )
    )
    output = Path(environ.get("EVALUATION_LIVE_REPORT", "evaluation-live-cdp-report.json"))
    output.write_text(f"{report.model_dump_json(indent=2)}\n")
    summary = report.model_dump_json(indent=2, exclude={"requests": True, "extraction": {"cases"}})
    sys.stdout.write(f"{summary}\nFull report: {output}\n")
    failed = (
        report.extraction.error_count
        or report.false_ready_count
        or report.privacy_gate_misses
        or report.identifier_leaks
    )
    if failed:
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
