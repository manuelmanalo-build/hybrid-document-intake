"""Typed contracts for the campaign-brief document type (ADR 0009)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from intake_orchestrator.agent.contracts import (
    ExtractedField,
    ReasonCode,
    StrictContract,
    ToolIdentity,
)

BriefSegmentLabel = Literal["BRIEF", "CORRESPONDENCE", "MEDIA_PLAN", "OTHER"]
AssetType = Literal[
    "SOCIAL_STATIC",
    "SOCIAL_VIDEO",
    "DISPLAY_SET",
    "VIDEO_30",
    "VIDEO_15",
    "EMAIL",
    "LANDING_PAGE",
    "OOH_BOARD",
    "AUDIO_30",
    "SEARCH_ADS",
    "INFLUENCER_KIT",
    "PRINT_AD",
]
MAX_BRIEF_PAGES = 20
MAX_DELIVERABLES = 50


class BriefPageSegment(StrictContract):
    page_number: Annotated[int, Field(ge=1, le=MAX_BRIEF_PAGES)]
    label: BriefSegmentLabel
    confidence: Annotated[Decimal, Field(ge=0, le=1)]


class BriefPacketSegmentation(StrictContract):
    segments: Annotated[list[BriefPageSegment], Field(min_length=1, max_length=MAX_BRIEF_PAGES)]

    @model_validator(mode="after")
    def validate_page_sequence(self) -> Self:
        pages = [segment.page_number for segment in self.segments]
        if pages != list(range(1, len(pages) + 1)):
            raise ValueError("Segments must label pages 1..n exactly once, in order")
        return self


class ExtractedDeliverable(StrictContract):
    """One requested deliverable with its own evidence."""

    asset_type: AssetType
    quantity: Annotated[int, Field(ge=1, le=10_000)]
    description: Annotated[str, Field(max_length=200)]
    due_date: date | None
    confidence: Annotated[Decimal, Field(ge=0, le=1)]
    source_page: Annotated[int | None, Field(ge=1, le=MAX_BRIEF_PAGES)]
    evidence_excerpt: Annotated[str, Field(max_length=500)]


class CampaignBriefCandidate(StrictContract):
    """A work request extracted from a brief packet. Conflicting sources stay separate fields."""

    client_name: ExtractedField[str]
    brand: ExtractedField[str]
    campaign_name: ExtractedField[str]
    sow_number_stated: ExtractedField[str]
    received_date: ExtractedField[date]
    objective: ExtractedField[str]
    primary_kpi: ExtractedField[str]
    kpi_target: ExtractedField[str]
    audience: ExtractedField[str]
    markets: ExtractedField[str]
    budget_total: ExtractedField[Decimal]
    launch_date: ExtractedField[date]
    end_date: ExtractedField[date]
    launch_date_in_correspondence: ExtractedField[date]
    approver: ExtractedField[str]
    mandatories: ExtractedField[str]
    deliverables: Annotated[list[ExtractedDeliverable], Field(max_length=MAX_DELIVERABLES)]
    deliverables_added_in_correspondence: Annotated[
        list[ExtractedDeliverable], Field(max_length=MAX_DELIVERABLES)
    ]


class SegmentBriefPacketInput(ToolIdentity):
    inspection_completed: Literal[True]


class SegmentBriefPacketOutput(StrictContract):
    page_count: Annotated[int, Field(ge=1, le=MAX_BRIEF_PAGES)]
    labels: Annotated[list[BriefSegmentLabel], Field(min_length=1, max_length=MAX_BRIEF_PAGES)]
    reason_codes: Annotated[list[ReasonCode], Field(max_length=20)] = []

    @model_validator(mode="after")
    def validate_label_count(self) -> Self:
        if len(self.labels) != self.page_count:
            raise ValueError("Exactly one segment label is required per page")
        return self


class LookupClientSowInput(ToolIdentity):
    brand: Annotated[str, Field(min_length=1, max_length=120)]
    sow_number: Annotated[str | None, Field(max_length=40)] = None


class LookupClientSowOutput(StrictContract):
    matched: bool
    sow_id: UUID | None
    sow_number: str | None
    status: Literal["ACTIVE", "EXPIRED", "SUSPENDED"] | None

    @model_validator(mode="after")
    def validate_match_consistency(self) -> Self:
        if self.matched != (self.sow_id is not None):
            raise ValueError("A matched SOW requires an ID; no match has none")
        return self
