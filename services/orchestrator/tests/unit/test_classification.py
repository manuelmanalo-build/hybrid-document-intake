import csv
from decimal import Decimal
from pathlib import Path

import pytest

from intake_orchestrator.agent.classification import KeywordDocumentClassifier
from intake_orchestrator.agent.pdf_content import read_bounded_pdf_content
from intake_orchestrator.confidence import CLAIM_WEIGHTS, WEIGHTS, decision_confidence

CLAIMS = Path("test-data/claims")


def _truth() -> list[dict[str, str]]:
    with (CLAIMS / "ground_truth.csv").open(newline="") as stream:
        return list(csv.DictReader(stream))


@pytest.mark.asyncio
async def test_keyword_classifier_labels_every_digital_claim_packet() -> None:
    classifier = KeywordDocumentClassifier()

    for row in _truth():
        content = read_bounded_pdf_content(
            CLAIMS / "packets" / row["packet_file"], include_images=False
        )
        result = await classifier.classify(bounded_content=content)
        expected = "UNKNOWN" if row["scan_quality"] == "degraded_image_only" else "CLAIM_PACKET"
        assert result.value == expected, row["claim_id"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fixture", ["test-data/minimal.pdf", "test-data/evaluation/valid-invoice.pdf"]
)
async def test_keyword_classifier_keeps_invoices_as_invoices(fixture: str) -> None:
    content = read_bounded_pdf_content(Path(fixture), include_images=False)

    result = await KeywordDocumentClassifier().classify(bounded_content=content)

    assert result.value == "AP_INVOICE"
    assert result.confidence == Decimal("0.90")


def test_claim_confidence_uses_policy_and_vehicle_weights() -> None:
    components: dict[str, Decimal | None] = {
        "classification_confidence": Decimal("1"),
        "required_field_completeness": Decimal("1"),
        "critical_field_confidence": Decimal("1"),
        "evidence_coverage": Decimal("1"),
        "policy_match_quality": Decimal("0"),
        "vehicle_match_quality": Decimal("0"),
        "deterministic_rule_score": Decimal("1"),
    }

    claim_score = decision_confidence(
        components, rule_based_risk=Decimal("0"), weights=CLAIM_WEIGHTS
    )
    invoice_score = decision_confidence(components, rule_based_risk=Decimal("0"), weights=WEIGHTS)

    assert sum(CLAIM_WEIGHTS.values()) == Decimal("1.00")
    assert claim_score == Decimal("0.8000")
    assert invoice_score == Decimal("1.0000")
