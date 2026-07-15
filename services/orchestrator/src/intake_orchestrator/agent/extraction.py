from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Literal, Protocol, cast

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from intake_orchestrator.agent.contracts import APInvoiceCandidate, ExtractedField
from intake_orchestrator.agent.prompts import EXTRACTION_SYSTEM_PROMPT

EXTRACTION_SCHEMA_VERSION = "ap-invoice-v1"
MAX_EXTRACTION_INPUT_CHARS = 20_000


@dataclass(frozen=True, slots=True)
class BoundedExtractionContent:
    text: str
    image_data_urls: tuple[str, ...] = ()


class StructuredInvoiceExtractor(Protocol):
    async def extract(
        self,
        *,
        strategy: Literal["TEXT", "VISUAL"],
        bounded_content: BoundedExtractionContent,
    ) -> APInvoiceCandidate: ...


class StubStructuredInvoiceExtractor:
    """Deterministic structured-output double used by the ordinary test suite."""

    def __init__(self, *, model: str, prompt_version: str) -> None:
        self._model = model
        self._prompt_version = prompt_version

    async def extract(
        self,
        *,
        strategy: Literal["TEXT", "VISUAL"],
        bounded_content: BoundedExtractionContent,
    ) -> APInvoiceCandidate:
        del bounded_content
        values: dict[str, object] = {
            "invoice_number": "INV-DEMO-001",
            "invoice_date": date(2026, 1, 15),
            "due_date": date(2026, 2, 14),
            "supplier_name": "Demo Supplier",
            "supplier_tax_id": "TAX-DEMO",
            "purchase_order_number": "PO-DEMO",
            "currency": "USD",
            "subtotal": Decimal("1250.00"),
            "tax": Decimal("110.00"),
            "total": Decimal("1360.00"),
        }
        payload = {
            name: {
                "value": value,
                "confidence": Decimal("0.95"),
                "source_page": 1,
                "evidence_excerpt": f"Bounded evidence for {name}",
                "extraction_strategy": strategy,
                "provider": "stub",
                "model": self._model,
                "schema_version": EXTRACTION_SCHEMA_VERSION,
                "prompt_version": self._prompt_version,
            }
            for name, value in values.items()
        }
        return APInvoiceCandidate.model_validate(payload)


class LangChainStructuredInvoiceExtractor:
    def __init__(
        self, *, model: BaseChatModel, provider: str, model_name: str, prompt_version: str
    ) -> None:
        self._runnable = model.with_structured_output(APInvoiceCandidate)
        self._provider = provider
        self._model_name = model_name
        self._prompt_version = prompt_version

    async def extract(
        self,
        *,
        strategy: Literal["TEXT", "VISUAL"],
        bounded_content: BoundedExtractionContent,
    ) -> APInvoiceCandidate:
        content = bounded_content.text[:MAX_EXTRACTION_INPUT_CHARS]
        human_content: str | list[str | dict[Any, Any]] = (  # noqa: ANN401
            [
                {
                    "type": "text",
                    "text": f"Extraction strategy: {strategy}\nDocument content:\n{content}",
                },
                *[
                    {"type": "image_url", "image_url": {"url": image_url}}
                    for image_url in bounded_content.image_data_urls
                ],
            ]
            if strategy == "VISUAL" and bounded_content.image_data_urls
            else f"Extraction strategy: {strategy}\nDocument content:\n{content}"
        )
        raw = await self._runnable.ainvoke(
            [
                SystemMessage(content=EXTRACTION_SYSTEM_PROMPT),
                HumanMessage(content=human_content),
            ]
        )
        validated = APInvoiceCandidate.model_validate(raw)
        normalized: dict[str, ExtractedField[object]] = {}
        for name in APInvoiceCandidate.model_fields:
            field = cast(ExtractedField[object], getattr(validated, name))
            normalized[name] = field.model_copy(
                update={
                    "extraction_strategy": strategy,
                    "provider": self._provider,
                    "model": self._model_name,
                    "schema_version": EXTRACTION_SCHEMA_VERSION,
                    "prompt_version": self._prompt_version,
                    "evidence_excerpt": field.evidence_excerpt[:500],
                }
            )
        return APInvoiceCandidate.model_validate(normalized)


def candidate_business_data(candidate: APInvoiceCandidate) -> dict[str, object]:
    result: dict[str, object] = {}
    for name in APInvoiceCandidate.model_fields:
        value = cast(ExtractedField[object], getattr(candidate, name)).value
        if isinstance(value, Decimal):
            result[name] = str(value)
        elif isinstance(value, date):
            result[name] = value.isoformat()
        else:
            result[name] = value
    return result


def candidate_completeness(candidate: APInvoiceCandidate) -> Decimal:
    populated = sum(
        cast(ExtractedField[object], getattr(candidate, name)).value is not None
        for name in APInvoiceCandidate.model_fields
    )
    return (Decimal(populated) / Decimal(len(APInvoiceCandidate.model_fields))).quantize(
        Decimal("0.0001")
    )
