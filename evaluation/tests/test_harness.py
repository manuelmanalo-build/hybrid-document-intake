from decimal import Decimal
from pathlib import Path

import pytest
from evaluation.harness import evaluate, load_cases
from pypdf import PdfReader
from pypdf.errors import PdfReadError


def test_sanitized_regression_dataset_meets_first_slice_gates() -> None:
    report = evaluate(load_cases(Path("test-data/evaluation/cases.json")))

    assert report.case_count == 9
    assert report.document_classification_accuracy == Decimal("1.0000")
    assert report.field_extraction_accuracy == Decimal("1.0000")
    assert report.recommendation_format_validity == Decimal("1.0000")
    assert report.unauthorized_tool_rate == Decimal("0.0000")
    assert report.rule_agreement == Decimal("1.0000")
    assert report.average_tool_count > Decimal("0")
    assert report.extraction_retry_rate > Decimal("0")


def test_evaluation_pdf_fixtures_are_readable_and_contain_synthetic_values() -> None:
    fixture_root = Path("test-data/evaluation")
    cases = load_cases(fixture_root / "cases.json")

    for case in cases:
        fixture = fixture_root / case.fixture
        if case.fixture == "malformed.pdf":
            with pytest.raises(PdfReadError):
                PdfReader(fixture)
            continue

        reader = PdfReader(fixture)
        assert len(reader.pages) == 1
        text = reader.pages[0].extract_text()
        assert "SYNTHETIC TEST FIXTURE" in text
        assert case.case_id in text
        for expected_value in case.expected.fields.values():
            if expected_value is not None:
                assert str(expected_value) in text
