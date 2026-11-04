"""Campaign-brief extractors and page segmenters (ADR 0009)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal, Protocol

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from intake_orchestrator.agent.brief_contracts import (
    BriefPacketSegmentation,
    CampaignBriefCandidate,
)
from intake_orchestrator.agent.extraction import (
    BoundedExtractionContent,
    _human_content,  # pyright: ignore[reportPrivateUsage]
    _normalized_candidate,  # pyright: ignore[reportPrivateUsage]
)
from intake_orchestrator.agent.prompts import (
    BRIEF_EXTRACTION_SYSTEM_PROMPT,
    BRIEF_SEGMENTATION_SYSTEM_PROMPT,
)

BRIEF_EXTRACTION_SCHEMA_VERSION = "campaign-brief-v1"


class StructuredBriefExtractor(Protocol):
    async def extract(
        self, *, strategy: Literal["TEXT", "VISUAL"], bounded_content: BoundedExtractionContent
    ) -> CampaignBriefCandidate: ...


class StructuredBriefSegmenter(Protocol):
    async def segment(
        self, *, page_count: int, bounded_content: BoundedExtractionContent
    ) -> BriefPacketSegmentation: ...


class StubStructuredBriefExtractor:
    """Deterministic brief double for the ordinary suite; values mirror fixture B01."""

    def __init__(self, *, model: str, prompt_version: str) -> None:
        self._model = model
        self._prompt_version = prompt_version

    async def extract(
        self, *, strategy: Literal["TEXT", "VISUAL"], bounded_content: BoundedExtractionContent
    ) -> CampaignBriefCandidate:
        del bounded_content
        values: dict[str, object] = {
            "client_name": "Harbor & Vine Coffee Co.",
            "brand": "Harbor & Vine",
            "campaign_name": "Winter Warmers",
            "sow_number_stated": "SOW-1001",
            "received_date": date(2026, 11, 2),
            "objective": "Drive café visits for the seasonal menu during December and January.",
            "primary_kpi": "Store visits vs. last year",
            "kpi_target": "+8%",
            "audience": "Adults 25-44 within 3 miles of a café",
            "markets": "New York, Boston, Philadelphia",
            "budget_total": Decimal("85000"),
            "launch_date": date(2026, 12, 1),
            "end_date": date(2027, 1, 31),
            "launch_date_in_correspondence": None,
            "approver": "Elena Ruiz, Brand Director",
            "mandatories": "Use the 2026 winter palette. No price claims.",
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
                "schema_version": BRIEF_EXTRACTION_SCHEMA_VERSION,
                "prompt_version": self._prompt_version,
            }
            for name, value in values.items()
        }
        payload["deliverables"] = [
            _deliverable("SOCIAL_STATIC", 12, "Menu feature posts", date(2026, 11, 20)),
            _deliverable("SOCIAL_VIDEO", 4, "Barista cut-downs", date(2026, 11, 20)),
            _deliverable("DISPLAY_SET", 1, "Geo-targeted banner set, 6 sizes", date(2026, 11, 24)),
        ]
        payload["deliverables_added_in_correspondence"] = []
        return CampaignBriefCandidate.model_validate(payload)


class StubStructuredBriefSegmenter:
    """Labels page 1 as the brief and every later page as correspondence."""

    async def segment(
        self, *, page_count: int, bounded_content: BoundedExtractionContent
    ) -> BriefPacketSegmentation:
        del bounded_content
        return BriefPacketSegmentation.model_validate(
            {
                "segments": [
                    {
                        "page_number": page,
                        "label": "BRIEF" if page == 1 else "CORRESPONDENCE",
                        "confidence": Decimal("0.95"),
                    }
                    for page in range(1, page_count + 1)
                ]
            }
        )


class LangChainStructuredBriefExtractor:
    def __init__(
        self, *, model: BaseChatModel, provider: str, model_name: str, prompt_version: str
    ) -> None:
        self._runnable = model.with_structured_output(CampaignBriefCandidate)
        self._provider = provider
        self._model_name = model_name
        self._prompt_version = prompt_version

    async def extract(
        self, *, strategy: Literal["TEXT", "VISUAL"], bounded_content: BoundedExtractionContent
    ) -> CampaignBriefCandidate:
        raw = await self._runnable.ainvoke(
            [
                SystemMessage(content=BRIEF_EXTRACTION_SYSTEM_PROMPT),
                HumanMessage(content=_human_content(strategy, bounded_content)),
            ]
        )
        return _normalized_candidate(
            CampaignBriefCandidate,
            raw,
            strategy=strategy,
            provider=self._provider,
            model_name=self._model_name,
            schema_version=BRIEF_EXTRACTION_SCHEMA_VERSION,
            prompt_version=self._prompt_version,
        )


class LangChainStructuredBriefSegmenter:
    def __init__(self, *, model: BaseChatModel) -> None:
        self._runnable = model.with_structured_output(BriefPacketSegmentation)

    async def segment(
        self, *, page_count: int, bounded_content: BoundedExtractionContent
    ) -> BriefPacketSegmentation:
        raw = await self._runnable.ainvoke(
            [
                SystemMessage(content=BRIEF_SEGMENTATION_SYSTEM_PROMPT),
                HumanMessage(
                    content=_human_content(
                        "VISUAL",
                        bounded_content,
                        preamble=f"The packet has exactly {page_count} pages.",
                    )
                ),
            ]
        )
        segmentation = BriefPacketSegmentation.model_validate(raw)
        if len(segmentation.segments) != page_count:
            raise ValueError("Segmentation must label every page exactly once")
        return segmentation


def _deliverable(asset_type: str, quantity: int, description: str, due: date) -> dict[str, object]:
    return {
        "asset_type": asset_type,
        "quantity": quantity,
        "description": description,
        "due_date": due,
        "confidence": Decimal("0.93"),
        "source_page": 1,
        "evidence_excerpt": f"{quantity} x {description}",
    }
