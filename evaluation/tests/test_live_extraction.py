import asyncio
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Literal

import pytest
from evaluation.harness import EvaluationCase, load_cases
from evaluation.live_extraction import (
    LiveExtractionReport,
    normalize_value,
    require_cost_approval,
    run_live_extraction,
)
from pydantic import SecretStr

from intake_api.infrastructure.config import Settings
from intake_orchestrator.agent.contracts import APInvoiceCandidate
from intake_orchestrator.agent.extraction import (
    BoundedExtractionContent,
    StructuredInvoiceExtractor,
)

FIXTURES = Path("test-data/evaluation")


def _candidate(values: dict[str, object]) -> APInvoiceCandidate:
    names = APInvoiceCandidate.model_fields
    return APInvoiceCandidate.model_validate(
        {
            name: {
                "value": values.get(name),
                "confidence": Decimal("0.9"),
                "source_page": 1,
                "evidence_excerpt": "synthetic",
                "extraction_strategy": "TEXT",
                "provider": "fake",
                "model": "fake",
                "schema_version": "ap-invoice-v1",
                "prompt_version": "test",
            }
            for name in names
        }
    )


class EchoExtractor:
    """Returns each case's expected values in order, optionally corrupting one field."""

    def __init__(self, cases: list[EvaluationCase], corrupt: str | None = None) -> None:
        self._queue = [c for c in cases if c.expected.classification == "AP_INVOICE"]
        self._corrupt = corrupt

    async def extract(
        self, *, strategy: Literal["TEXT", "VISUAL"], bounded_content: BoundedExtractionContent
    ) -> APInvoiceCandidate:
        assert strategy == "TEXT"
        assert "[page 1]" in bounded_content.text
        values = dict(self._queue.pop(0).expected.fields)
        if self._corrupt and self._corrupt in values:
            values[self._corrupt] = "WRONG"
        return _candidate(values)


class FailingExtractor:
    async def extract(
        self, *, strategy: Literal["TEXT", "VISUAL"], bounded_content: BoundedExtractionContent
    ) -> APInvoiceCandidate:
        del strategy, bounded_content
        raise TimeoutError


def _run(
    extractor: StructuredInvoiceExtractor, cases: list[EvaluationCase]
) -> LiveExtractionReport:
    return asyncio.run(
        run_live_extraction(
            cases,
            fixture_root=FIXTURES,
            extractor=extractor,
            strategy="TEXT",
            provider="fake",
            model="fake-model",
            timeout_seconds=5,
            clock=iter(float(i) / 10 for i in range(1000)).__next__,
        )
    )


def test_live_runner_scores_real_pdf_reads_and_reports_perfect_echo() -> None:
    cases = load_cases(FIXTURES / "cases.json")
    report = _run(EchoExtractor(cases), cases)

    assert report.measurement == "LIVE"
    assert report.case_count == 7
    assert report.error_count == 0
    assert report.field_accuracy == Decimal("1.0000")
    assert all(value == Decimal("1.0000") for value in report.per_field_accuracy.values())


def test_live_runner_reports_field_level_mismatches() -> None:
    cases = load_cases(FIXTURES / "cases.json")
    report = _run(EchoExtractor(cases, corrupt="supplier_name"), cases)

    assert report.error_count == 0
    assert report.per_field_accuracy["supplier_name"] == Decimal("0.0000")
    assert report.per_field_accuracy["total"] == Decimal("1.0000")
    assert report.field_accuracy < Decimal("1.0000")
    mismatch = next(f for f in report.cases[0].fields if f.field == "supplier_name")
    assert mismatch.match is False
    assert mismatch.observed == "wrong"


def test_live_runner_records_errors_instead_of_hiding_them() -> None:
    cases = load_cases(FIXTURES / "cases.json")
    report = _run(FailingExtractor(), cases)

    assert report.error_count == report.case_count
    assert {case.error_code for case in report.cases} == {"TimeoutError"}
    assert report.field_accuracy == Decimal("0.0000")


@pytest.mark.parametrize(
    ("expected", "observed"),
    [
        ("108.00", Decimal("108")),
        ("2026-07-01", date(2026, 7, 1)),
        ("Synthetic  Office Supply", "synthetic office supply"),
        (None, ""),
    ],
)
def test_normalization_treats_equivalent_values_as_equal(
    expected: object, observed: object
) -> None:
    assert normalize_value(expected) == normalize_value(observed)


def test_live_provider_requires_explicit_cost_approval() -> None:
    settings = Settings(
        agent_provider="openai",
        agent_model="configured-model",
        openai_api_key=SecretStr("test-only-key"),
    )
    with pytest.raises(PermissionError):
        require_cost_approval(settings, {})
    require_cost_approval(settings, {"EVALUATION_LIVE_ENABLED": "true"})
    require_cost_approval(Settings(agent_provider="stub"), {})
