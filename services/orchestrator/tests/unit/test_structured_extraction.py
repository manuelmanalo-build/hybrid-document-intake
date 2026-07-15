from decimal import Decimal

import pytest
from pydantic import ValidationError

from intake_orchestrator.agent.contracts import APInvoiceCandidate
from intake_orchestrator.agent.extraction import (
    BoundedExtractionContent,
    StubStructuredInvoiceExtractor,
    candidate_business_data,
    candidate_completeness,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("strategy", ["TEXT", "VISUAL"])
async def test_stub_extraction_is_typed_and_preserves_field_evidence(strategy: str) -> None:
    extractor = StubStructuredInvoiceExtractor(
        model="configured-not-hardcoded", prompt_version="invoice-extraction-v1"
    )

    candidate = await extractor.extract(
        strategy=strategy,  # type: ignore[arg-type]
        bounded_content=BoundedExtractionContent(text="bounded PDF text"),
    )

    assert isinstance(candidate.total.value, Decimal)
    assert candidate.total.value == Decimal("1360.00")
    assert candidate.total.extraction_strategy == strategy
    assert candidate.total.source_page == 1
    assert len(candidate.total.evidence_excerpt) <= 500
    assert candidate.total.provider == "stub"
    assert candidate_completeness(candidate) == Decimal("1.0000")
    assert candidate_business_data(candidate)["total"] == "1360.00"


def test_extraction_contract_rejects_operational_or_free_form_provider_output() -> None:
    with pytest.raises(ValidationError):
        APInvoiceCandidate.model_validate(
            {"decision": "APPROVE", "content": "I think this invoice looks fine."}
        )
