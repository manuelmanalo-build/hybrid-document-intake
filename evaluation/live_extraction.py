"""Live, opt-in extraction diagnostic.

Runs the production structured extractor against the evaluation PDF fixtures and scores real
output against expected fields. Unlike `evaluation.run`, which scores recorded observations, this
measures a configured provider. It is diagnostic only: it never runs in ordinary CI, it needs
explicit cost approval for a live provider, and stub output is labeled as not a measurement.
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Literal, Protocol, cast

from pydantic import BaseModel, ConfigDict

from evaluation.harness import EvaluationCase, load_cases
from intake_api.infrastructure.config import Settings
from intake_orchestrator.agent.contracts import ExtractedField
from intake_orchestrator.agent.extraction import (
    EXTRACTION_SCHEMA_VERSION,
    BoundedExtractionContent,
    LangChainStructuredInvoiceExtractor,
    StructuredInvoiceExtractor,
    StubStructuredInvoiceExtractor,
)
from intake_orchestrator.agent.models import create_chat_model
from intake_orchestrator.agent.pdf_content import read_bounded_pdf_content
from intake_orchestrator.agent.prompts import PROMPT_VERSION

Strategy = Literal["TEXT", "VISUAL"]
_NUMBER = re.compile(r"^-?\d+(\.\d+)?$")
_WHITESPACE = re.compile(r"\s+")


class FieldResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    field: str
    expected: str
    observed: str
    match: bool


class CaseResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    case_id: str
    fixture: str
    status: Literal["SCORED", "ERROR"]
    error_code: str | None = None
    latency_ms: int
    fields: list[FieldResult]


class LiveExtractionReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    measurement: Literal["LIVE", "STUB_NOT_A_MEASUREMENT"]
    provider: str
    model: str
    strategy: Strategy
    prompt_version: str
    schema_version: str
    case_count: int
    scored_count: int
    error_count: int
    field_accuracy: Decimal
    per_field_accuracy: dict[str, Decimal]
    mean_latency_ms: int
    cases: list[CaseResult]


def normalize_value(value: object) -> str:
    """Normalize expected and observed values to one comparable string form."""
    if value is None:
        return ""
    if isinstance(value, date):
        return value.isoformat()
    text = _WHITESPACE.sub(" ", str(value)).strip()
    if _NUMBER.match(text):
        try:
            return format(Decimal(text).normalize(), "f")
        except InvalidOperation:
            return text.casefold()
    return text.casefold()


def score_candidate(candidate: BaseModel, expected_fields: dict[str, object]) -> list[FieldResult]:
    results: list[FieldResult] = []
    for name, expected_value in sorted(expected_fields.items()):
        observed_value = (
            cast(ExtractedField[object], getattr(candidate, name)).value
            if name in type(candidate).model_fields
            else None
        )
        expected_text = normalize_value(expected_value)
        observed_text = normalize_value(observed_value)
        results.append(
            FieldResult(
                field=name,
                expected=expected_text,
                observed=observed_text,
                match=expected_text == observed_text,
            )
        )
    return results


@dataclass(frozen=True, slots=True)
class FixtureCase:
    case_id: str
    fixture: str
    path: Path
    expected_fields: dict[str, object]


class CandidateExtractor(Protocol):
    async def extract(
        self, *, strategy: Strategy, bounded_content: BoundedExtractionContent
    ) -> BaseModel: ...


async def run_fixture_cases(
    cases: list[FixtureCase],
    *,
    extractor: CandidateExtractor,
    strategy: Strategy,
    timeout_seconds: float,
    clock: Callable[[], float] = time.perf_counter,
) -> list[CaseResult]:
    results: list[CaseResult] = []
    for case in cases:
        started = clock()
        try:
            content = read_bounded_pdf_content(case.path, include_images=strategy == "VISUAL")
            async with asyncio.timeout(timeout_seconds):
                candidate = await extractor.extract(strategy=strategy, bounded_content=content)
        # A diagnostic run records every per-case failure by type and keeps going; the error is
        # counted in the report and makes the command exit non-zero, so it is never hidden.
        except Exception as error:
            results.append(
                CaseResult(
                    case_id=case.case_id,
                    fixture=case.fixture,
                    status="ERROR",
                    error_code=type(error).__name__,
                    latency_ms=_elapsed_ms(started, clock),
                    fields=[],
                )
            )
            continue
        results.append(
            CaseResult(
                case_id=case.case_id,
                fixture=case.fixture,
                status="SCORED",
                latency_ms=_elapsed_ms(started, clock),
                fields=score_candidate(candidate, case.expected_fields),
            )
        )
    return results


async def run_live_extraction(
    cases: list[EvaluationCase],
    *,
    fixture_root: Path,
    extractor: StructuredInvoiceExtractor,
    strategy: Strategy,
    provider: str,
    model: str,
    timeout_seconds: float,
    clock: Callable[[], float] = time.perf_counter,
) -> LiveExtractionReport:
    invoice_cases = [case for case in cases if case.expected.classification == "AP_INVOICE"]
    if not invoice_cases:
        raise ValueError("At least one AP_INVOICE evaluation case is required")
    results = await run_fixture_cases(
        [
            FixtureCase(
                case_id=case.case_id,
                fixture=case.fixture,
                path=fixture_root / case.fixture,
                expected_fields=dict(case.expected.fields),
            )
            for case in invoice_cases
        ],
        extractor=extractor,
        strategy=strategy,
        timeout_seconds=timeout_seconds,
        clock=clock,
    )
    return build_report(
        results,
        strategy=strategy,
        provider=provider,
        model=model,
        prompt_version=PROMPT_VERSION,
        schema_version=EXTRACTION_SCHEMA_VERSION,
    )


def build_extractor(settings: Settings) -> StructuredInvoiceExtractor:
    if settings.agent_provider == "stub":
        return StubStructuredInvoiceExtractor(
            model=settings.agent_model, prompt_version=PROMPT_VERSION
        )
    return LangChainStructuredInvoiceExtractor(
        model=create_chat_model(settings),
        provider=settings.agent_provider,
        model_name=settings.agent_model,
        prompt_version=PROMPT_VERSION,
    )


def require_cost_approval(settings: Settings, environ: dict[str, str]) -> None:
    if settings.agent_provider == "stub":
        return
    if environ.get("EVALUATION_LIVE_ENABLED", "false").casefold() != "true":
        raise PermissionError(
            "Live extraction calls a paid provider; set EVALUATION_LIVE_ENABLED=true to approve"
        )


def main() -> None:
    settings = Settings()
    environ = dict(os.environ)
    require_cost_approval(settings, environ)
    strategy = cast(Strategy, environ.get("EVALUATION_LIVE_STRATEGY", "TEXT").upper())
    if strategy not in ("TEXT", "VISUAL"):
        raise ValueError("EVALUATION_LIVE_STRATEGY must be TEXT or VISUAL")
    dataset = Path(environ.get("EVALUATION_DATASET", "test-data/evaluation/cases.json"))
    report = asyncio.run(
        run_live_extraction(
            load_cases(dataset),
            fixture_root=dataset.parent,
            extractor=build_extractor(settings),
            strategy=strategy,
            provider=settings.agent_provider,
            model=settings.agent_model,
            timeout_seconds=float(settings.agent_model_timeout_seconds),
        )
    )
    output = Path(environ.get("EVALUATION_LIVE_REPORT", "evaluation-live-report.json"))
    output.write_text(f"{report.model_dump_json(indent=2)}\n")
    summary = report.model_dump_json(indent=2, exclude={"cases"})
    sys.stdout.write(f"{summary}\nFull report: {output}\n")
    if report.error_count:
        raise SystemExit(1)


def build_report(
    results: list[CaseResult],
    *,
    strategy: Strategy,
    provider: str,
    model: str,
    prompt_version: str,
    schema_version: str,
) -> LiveExtractionReport:
    scored = [result for result in results if result.status == "SCORED"]
    matches = sum(field.match for result in scored for field in result.fields)
    total = sum(len(result.fields) for result in scored)
    per_field: dict[str, list[bool]] = {}
    for result in scored:
        for field in result.fields:
            per_field.setdefault(field.field, []).append(field.match)
    latencies = [result.latency_ms for result in results]
    return LiveExtractionReport(
        measurement="STUB_NOT_A_MEASUREMENT" if provider == "stub" else "LIVE",
        provider=provider,
        model=model,
        strategy=strategy,
        prompt_version=prompt_version,
        schema_version=schema_version,
        case_count=len(results),
        scored_count=len(scored),
        error_count=len(results) - len(scored),
        field_accuracy=_ratio(matches, total),
        per_field_accuracy={
            name: _ratio(sum(values), len(values)) for name, values in sorted(per_field.items())
        },
        mean_latency_ms=sum(latencies) // len(latencies) if latencies else 0,
        cases=results,
    )


def _ratio(numerator: int, denominator: int) -> Decimal:
    if denominator == 0:
        return Decimal("0.0000")
    return (Decimal(numerator) / Decimal(denominator)).quantize(Decimal("0.0001"))


def _elapsed_ms(started: float, clock: Callable[[], float]) -> int:
    return int((clock() - started) * 1000)


if __name__ == "__main__":
    main()
