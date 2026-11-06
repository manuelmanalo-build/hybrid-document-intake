"""CDP-onboarding extractors and page segmenters (ADR 0010).

Callers pass content that `pii-scanner-v1` has already redacted; these classes never see raw
identifiers from a packet's text layer.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal, Protocol

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from intake_orchestrator.agent.cdp_contracts import (
    OnboardingPacketSegmentation,
    OnboardingRequestCandidate,
    OnboardingSegmentLabel,
)
from intake_orchestrator.agent.extraction import (
    BoundedExtractionContent,
    _human_content,  # pyright: ignore[reportPrivateUsage]
    _normalized_candidate,  # pyright: ignore[reportPrivateUsage]
)
from intake_orchestrator.agent.prompts import (
    CDP_EXTRACTION_SYSTEM_PROMPT,
    CDP_SEGMENTATION_SYSTEM_PROMPT,
)

CDP_EXTRACTION_SCHEMA_VERSION = "cdp-onboarding-v1"


class StructuredOnboardingExtractor(Protocol):
    async def extract(
        self, *, strategy: Literal["TEXT", "VISUAL"], bounded_content: BoundedExtractionContent
    ) -> OnboardingRequestCandidate: ...


class StructuredOnboardingSegmenter(Protocol):
    async def segment(
        self, *, page_count: int, bounded_content: BoundedExtractionContent
    ) -> OnboardingPacketSegmentation: ...


class StubStructuredOnboardingExtractor:
    """Deterministic double for the ordinary suite; values mirror fixture D01."""

    def __init__(self, *, model: str, prompt_version: str) -> None:
        self._model = model
        self._prompt_version = prompt_version

    async def extract(
        self, *, strategy: Literal["TEXT", "VISUAL"], bounded_content: BoundedExtractionContent
    ) -> OnboardingRequestCandidate:
        del bounded_content
        values: dict[str, object] = {
            "client_name": "Harbor & Vine Coffee Co.",
            "agreement_number_stated": "DPA-2001",
            "received_date": date(2026, 11, 2),
            "source_name": "Shopify orders (US store)",
            "connector_type": "SHOPIFY",
            "refresh_cadence": "DAILY",
            "data_region": "US",
            "lawful_basis": "CONTRACT",
            "consent_mechanism": None,
            "declares_no_personal_data": False,
            "declares_sensitive_data": False,
            "retention_days": 730,
            "estimated_records": 1_200_000,
            "requested_go_live": date(2026, 11, 30),
            "go_live_in_correspondence": None,
            "data_owner": "Elena Ruiz, Brand Director",
        }
        payload: dict[str, object] = {
            name: {
                "value": value,
                "confidence": Decimal("0.95"),
                "source_page": 1,
                "evidence_excerpt": f"Bounded evidence for {name}",
                "extraction_strategy": strategy,
                "provider": "stub",
                "model": self._model,
                "schema_version": CDP_EXTRACTION_SCHEMA_VERSION,
                "prompt_version": self._prompt_version,
            }
            for name, value in values.items()
        }
        payload["mappings"] = [
            _mapping("customer_email", "STRING", "email_address", personal=True),
            _mapping("order_number", "STRING", "order_id", personal=False),
            _mapping("total_price", "DECIMAL", "order_total", personal=False),
            _mapping("created_at", "TIMESTAMP", "order_timestamp", personal=False),
            _mapping("sku", "STRING", "product_sku", personal=False),
            _mapping("qty", "INTEGER", "quantity", personal=False),
        ]
        payload["fields_added_in_correspondence"] = []
        return OnboardingRequestCandidate.model_validate(payload)


class StubStructuredOnboardingSegmenter:
    """Form, dictionary, and sample extract in that order; later pages are correspondence."""

    _ORDER: tuple[OnboardingSegmentLabel, ...] = (
        "ONBOARDING_FORM",
        "DATA_DICTIONARY",
        "SAMPLE_EXTRACT",
    )

    async def segment(
        self, *, page_count: int, bounded_content: BoundedExtractionContent
    ) -> OnboardingPacketSegmentation:
        del bounded_content
        return OnboardingPacketSegmentation.model_validate(
            {
                "segments": [
                    {
                        "page_number": page,
                        "label": self._ORDER[page - 1]
                        if page <= len(self._ORDER)
                        else "CORRESPONDENCE",
                        "confidence": Decimal("0.95"),
                    }
                    for page in range(1, page_count + 1)
                ]
            }
        )


class LangChainStructuredOnboardingExtractor:
    def __init__(
        self, *, model: BaseChatModel, provider: str, model_name: str, prompt_version: str
    ) -> None:
        self._runnable = model.with_structured_output(OnboardingRequestCandidate)
        self._provider = provider
        self._model_name = model_name
        self._prompt_version = prompt_version

    async def extract(
        self, *, strategy: Literal["TEXT", "VISUAL"], bounded_content: BoundedExtractionContent
    ) -> OnboardingRequestCandidate:
        raw = await self._runnable.ainvoke(
            [
                SystemMessage(content=CDP_EXTRACTION_SYSTEM_PROMPT),
                HumanMessage(content=_human_content(strategy, bounded_content)),
            ]
        )
        return _normalized_candidate(
            OnboardingRequestCandidate,
            raw,
            strategy=strategy,
            provider=self._provider,
            model_name=self._model_name,
            schema_version=CDP_EXTRACTION_SCHEMA_VERSION,
            prompt_version=self._prompt_version,
        )


class LangChainStructuredOnboardingSegmenter:
    def __init__(self, *, model: BaseChatModel) -> None:
        self._runnable = model.with_structured_output(OnboardingPacketSegmentation)

    async def segment(
        self, *, page_count: int, bounded_content: BoundedExtractionContent
    ) -> OnboardingPacketSegmentation:
        raw = await self._runnable.ainvoke(
            [
                SystemMessage(content=CDP_SEGMENTATION_SYSTEM_PROMPT),
                HumanMessage(
                    content=_human_content(
                        "VISUAL",
                        bounded_content,
                        preamble=f"The packet has exactly {page_count} pages.",
                    )
                ),
            ]
        )
        segmentation = OnboardingPacketSegmentation.model_validate(raw)
        if len(segmentation.segments) != page_count:
            raise ValueError("Segmentation must label every page exactly once")
        return segmentation


def _mapping(field: str, source_type: str, attribute: str, *, personal: bool) -> dict[str, object]:
    return {
        "source_field": field,
        "source_type": source_type,
        "proposed_attribute": attribute,
        "client_marked_personal": personal,
        "confidence": Decimal("0.93"),
        "source_page": 2,
        "evidence_excerpt": f"{field} -> {attribute}",
    }
