"""Document classification port (AP_INVOICE, CLAIM_PACKET, UNSUPPORTED, UNKNOWN).

Live providers classify with a structured model call over bounded text, plus page images when a
document has no text layer. The stub provider uses a deterministic keyword classifier over the
same bounded text so the offline suite and demos can route claim packets without a model.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Protocol

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import Field

from intake_orchestrator.agent.contracts import DocumentType, StrictContract
from intake_orchestrator.agent.extraction import (
    MAX_EXTRACTION_INPUT_CHARS,
    BoundedExtractionContent,
)
from intake_orchestrator.agent.prompts import CLASSIFICATION_SYSTEM_PROMPT

_CLAIM_PHRASES = ("loss notice", "first notice of loss", "notice of loss", "fnol")
_INVOICE_PHRASES = ("invoice",)


class DocumentClassification(StrictContract):
    value: DocumentType
    confidence: Annotated[Decimal, Field(ge=0, le=1)]


class DocumentClassifier(Protocol):
    async def classify(
        self, *, bounded_content: BoundedExtractionContent
    ) -> DocumentClassification: ...


class KeywordDocumentClassifier:
    """Deterministic classifier for the stub provider; never calls a model."""

    async def classify(
        self, *, bounded_content: BoundedExtractionContent
    ) -> DocumentClassification:
        text = " ".join(
            line for line in bounded_content.text.splitlines() if not line.startswith("[page")
        )
        lowered = text.casefold()
        if not lowered.strip():
            return DocumentClassification(value="UNKNOWN", confidence=Decimal("0"))
        estimate_with_vin = "estimate" in lowered and "vin" in lowered
        if any(phrase in lowered for phrase in _CLAIM_PHRASES) or estimate_with_vin:
            return DocumentClassification(value="CLAIM_PACKET", confidence=Decimal("0.85"))
        if any(phrase in lowered for phrase in _INVOICE_PHRASES):
            return DocumentClassification(value="AP_INVOICE", confidence=Decimal("0.90"))
        return DocumentClassification(value="UNSUPPORTED", confidence=Decimal("0.60"))


class LangChainDocumentClassifier:
    def __init__(self, *, model: BaseChatModel) -> None:
        self._runnable = model.with_structured_output(DocumentClassification)

    async def classify(
        self, *, bounded_content: BoundedExtractionContent
    ) -> DocumentClassification:
        text = bounded_content.text[:MAX_EXTRACTION_INPUT_CHARS]
        content: str | list[str | dict[object, object]] = f"Document content:\n{text}"
        if bounded_content.image_data_urls:
            content = [
                {"type": "text", "text": f"Document content:\n{text}"},
                *[
                    {"type": "image_url", "image_url": {"url": image_url}}
                    for image_url in bounded_content.image_data_urls
                ],
            ]
        raw = await self._runnable.ainvoke(
            [SystemMessage(content=CLASSIFICATION_SYSTEM_PROMPT), HumanMessage(content=content)]
        )
        return DocumentClassification.model_validate(raw)
