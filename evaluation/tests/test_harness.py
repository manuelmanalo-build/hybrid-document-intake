from decimal import Decimal
from pathlib import Path

from evaluation.harness import evaluate, load_cases


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
