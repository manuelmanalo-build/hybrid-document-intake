from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Literal, Protocol, cast

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from intake_orchestrator.agent.claim_contracts import (
    ClaimPacketCandidate,
    ClaimPacketSegmentation,
)
from intake_orchestrator.agent.contracts import APInvoiceCandidate, ExtractedField
from intake_orchestrator.agent.prompts import (
    CLAIM_EXTRACTION_SYSTEM_PROMPT,
    CLAIM_SEGMENTATION_SYSTEM_PROMPT,
    EXTRACTION_SYSTEM_PROMPT,
)

EXTRACTION_SCHEMA_VERSION = "ap-invoice-v1"
CLAIM_EXTRACTION_SCHEMA_VERSION = "claim-packet-v1"
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
        raw = await self._runnable.ainvoke(
            [
                SystemMessage(content=EXTRACTION_SYSTEM_PROMPT),
                HumanMessage(content=_human_content(strategy, bounded_content)),
            ]
        )
        return _normalized_candidate(
            APInvoiceCandidate,
            raw,
            strategy=strategy,
            provider=self._provider,
            model_name=self._model_name,
            schema_version=EXTRACTION_SCHEMA_VERSION,
            prompt_version=self._prompt_version,
        )


def candidate_business_data(
    candidate: APInvoiceCandidate | ClaimPacketCandidate,
) -> dict[str, object]:
    result: dict[str, object] = {}
    for name in type(candidate).model_fields:
        value = cast(ExtractedField[object], getattr(candidate, name)).value
        if isinstance(value, Decimal):
            result[name] = str(value)
        elif isinstance(value, date):
            result[name] = value.isoformat()
        else:
            result[name] = value
    return result


def candidate_completeness(candidate: APInvoiceCandidate | ClaimPacketCandidate) -> Decimal:
    fields = type(candidate).model_fields
    populated = sum(
        cast(ExtractedField[object], getattr(candidate, name)).value is not None for name in fields
    )
    return (Decimal(populated) / Decimal(len(fields))).quantize(Decimal("0.0001"))


class StructuredClaimExtractor(Protocol):
    async def extract(
        self,
        *,
        strategy: Literal["TEXT", "VISUAL"],
        bounded_content: BoundedExtractionContent,
    ) -> ClaimPacketCandidate: ...


class StructuredClaimSegmenter(Protocol):
    async def segment(
        self, *, page_count: int, bounded_content: BoundedExtractionContent
    ) -> ClaimPacketSegmentation: ...


class StubStructuredClaimExtractor:
    """Deterministic claim double for the ordinary suite; values mirror fixture C01."""

    def __init__(self, *, model: str, prompt_version: str) -> None:
        self._model = model
        self._prompt_version = prompt_version

    async def extract(
        self,
        *,
        strategy: Literal["TEXT", "VISUAL"],
        bounded_content: BoundedExtractionContent,
    ) -> ClaimPacketCandidate:
        del bounded_content
        values: dict[str, tuple[object, int]] = {
            "policy_number": ("NFM-PA-4471200", 1),
            "named_insured": ("Marisol Vega", 1),
            "loss_date": (date(2026, 9, 3), 1),
            "report_date": (date(2026, 9, 4), 1),
            "coverage": ("COMPREHENSIVE", 1),
            "cause_of_loss": ("Hail", 1),
            "vehicle_year": (2021, 1),
            "vehicle_make": ("Honda", 1),
            "vehicle_model": ("CR-V EX", 1),
            "vin_reported": ("1HGHBUSR5MC181960", 1),
            "vin_on_estimate": ("1HGHBUSR5MC181960", 2),
            "injury_indicated_on_form": (False, 1),
            "injury_mentioned_in_correspondence": (False, 1),
            "third_party_involved": (False, 1),
            "police_report_number": (None, 1),
            "estimate_number": ("E-560137", 2),
            "estimate_parts": (Decimal("462.27"), 2),
            "estimate_labor": (Decimal("2742.62"), 2),
            "estimate_materials": (Decimal("0.00"), 2),
            "estimate_tax": (Decimal("36.98"), 2),
            "estimate_total": (Decimal("3241.87"), 2),
        }
        payload = {
            name: {
                "value": value,
                "confidence": Decimal("0.95"),
                "source_page": page,
                "evidence_excerpt": f"Bounded evidence for {name}",
                "extraction_strategy": strategy,
                "provider": "stub",
                "model": self._model,
                "schema_version": CLAIM_EXTRACTION_SCHEMA_VERSION,
                "prompt_version": self._prompt_version,
            }
            for name, (value, page) in values.items()
        }
        return ClaimPacketCandidate.model_validate(payload)


class StubStructuredClaimSegmenter:
    """Labels page 1 as the loss notice and every later page as the repair estimate."""

    async def segment(
        self, *, page_count: int, bounded_content: BoundedExtractionContent
    ) -> ClaimPacketSegmentation:
        del bounded_content
        return ClaimPacketSegmentation.model_validate(
            {
                "segments": [
                    {
                        "page_number": page,
                        "label": "LOSS_NOTICE" if page == 1 else "REPAIR_ESTIMATE",
                        "confidence": Decimal("0.95"),
                    }
                    for page in range(1, page_count + 1)
                ]
            }
        )


class LangChainStructuredClaimExtractor:
    def __init__(
        self, *, model: BaseChatModel, provider: str, model_name: str, prompt_version: str
    ) -> None:
        self._runnable = model.with_structured_output(ClaimPacketCandidate)
        self._provider = provider
        self._model_name = model_name
        self._prompt_version = prompt_version

    async def extract(
        self,
        *,
        strategy: Literal["TEXT", "VISUAL"],
        bounded_content: BoundedExtractionContent,
    ) -> ClaimPacketCandidate:
        raw = await self._runnable.ainvoke(
            [
                SystemMessage(content=CLAIM_EXTRACTION_SYSTEM_PROMPT),
                HumanMessage(content=_human_content(strategy, bounded_content)),
            ]
        )
        return _normalized_candidate(
            ClaimPacketCandidate,
            raw,
            strategy=strategy,
            provider=self._provider,
            model_name=self._model_name,
            schema_version=CLAIM_EXTRACTION_SCHEMA_VERSION,
            prompt_version=self._prompt_version,
        )


class LangChainStructuredClaimSegmenter:
    def __init__(self, *, model: BaseChatModel) -> None:
        self._runnable = model.with_structured_output(ClaimPacketSegmentation)

    async def segment(
        self, *, page_count: int, bounded_content: BoundedExtractionContent
    ) -> ClaimPacketSegmentation:
        raw = await self._runnable.ainvoke(
            [
                SystemMessage(content=CLAIM_SEGMENTATION_SYSTEM_PROMPT),
                HumanMessage(
                    content=_human_content(
                        "VISUAL",
                        bounded_content,
                        preamble=f"The packet has exactly {page_count} pages.",
                    )
                ),
            ]
        )
        segmentation = ClaimPacketSegmentation.model_validate(raw)
        if len(segmentation.segments) != page_count:
            raise ValueError("Segmentation must label every page exactly once")
        return segmentation


def _human_content(
    strategy: Literal["TEXT", "VISUAL"],
    bounded_content: BoundedExtractionContent,
    *,
    preamble: str | None = None,
) -> str | list[str | dict[Any, Any]]:  # noqa: ANN401
    content = bounded_content.text[:MAX_EXTRACTION_INPUT_CHARS]
    heading = f"{preamble}\n" if preamble else ""
    text = f"{heading}Extraction strategy: {strategy}\nDocument content:\n{content}"
    if strategy == "VISUAL" and bounded_content.image_data_urls:
        return [
            {"type": "text", "text": text},
            *[
                {"type": "image_url", "image_url": {"url": image_url}}
                for image_url in bounded_content.image_data_urls
            ],
        ]
    return text


def _normalized_candidate[CandidateT: APInvoiceCandidate | ClaimPacketCandidate](
    candidate_type: type[CandidateT],
    raw: object,
    *,
    strategy: Literal["TEXT", "VISUAL"],
    provider: str,
    model_name: str,
    schema_version: str,
    prompt_version: str,
) -> CandidateT:
    """Validate model output, then stamp provenance the model is not allowed to choose."""
    validated = candidate_type.model_validate(raw)
    normalized: dict[str, ExtractedField[object]] = {}
    for name in candidate_type.model_fields:
        field = cast(ExtractedField[object], getattr(validated, name))
        normalized[name] = field.model_copy(
            update={
                "extraction_strategy": strategy,
                "provider": provider,
                "model": model_name,
                "schema_version": schema_version,
                "prompt_version": prompt_version,
                "evidence_excerpt": field.evidence_excerpt[:500],
            }
        )
    return candidate_type.model_validate(normalized)
