"""Typed contracts for the claim-packet document type (ADR 0008).

These are boundary contracts only. Nothing here is executable until the corresponding tools are
registered, guarded, and tested; the registry and `DocumentType` are extended in that increment.
"""

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

SegmentLabel = Literal["LOSS_NOTICE", "REPAIR_ESTIMATE", "POLICE_REPORT", "CORRESPONDENCE", "OTHER"]
ClaimCoverage = Literal["COLLISION", "COMPREHENSIVE"]
VehicleMatchKind = Literal["EXACT", "NEAR", "NONE"]
PolicyLookupStatus = Literal["ACTIVE", "CANCELLED", "EXPIRED"]
MAX_PACKET_PAGES = 20

PolicyNumberText = Annotated[str, Field(min_length=1, max_length=40)]
VinText = Annotated[str, Field(min_length=1, max_length=20)]


class PageSegment(StrictContract):
    page_number: Annotated[int, Field(ge=1, le=MAX_PACKET_PAGES)]
    label: SegmentLabel
    confidence: Annotated[Decimal, Field(ge=0, le=1)]


class ClaimPacketSegmentation(StrictContract):
    """Structured model output: exactly one label per page, pages 1..n in order."""

    segments: Annotated[list[PageSegment], Field(min_length=1, max_length=MAX_PACKET_PAGES)]

    @model_validator(mode="after")
    def validate_page_sequence(self) -> Self:
        pages = [segment.page_number for segment in self.segments]
        if pages != list(range(1, len(pages) + 1)):
            raise ValueError("Segments must label pages 1..n exactly once, in order")
        return self


class ClaimPacketCandidate(StrictContract):
    """One claim extracted from a mixed packet. Conflicting sources stay separate fields."""

    policy_number: ExtractedField[str]
    named_insured: ExtractedField[str]
    loss_date: ExtractedField[date]
    report_date: ExtractedField[date]
    coverage: ExtractedField[ClaimCoverage]
    cause_of_loss: ExtractedField[str]
    vehicle_year: ExtractedField[int]
    vehicle_make: ExtractedField[str]
    vehicle_model: ExtractedField[str]
    vin_reported: ExtractedField[str]
    vin_on_estimate: ExtractedField[str]
    injury_indicated_on_form: ExtractedField[bool]
    injury_mentioned_in_correspondence: ExtractedField[bool]
    third_party_involved: ExtractedField[bool]
    police_report_number: ExtractedField[str]
    estimate_number: ExtractedField[str]
    estimate_parts: ExtractedField[Decimal]
    estimate_labor: ExtractedField[Decimal]
    estimate_materials: ExtractedField[Decimal]
    estimate_tax: ExtractedField[Decimal]
    estimate_total: ExtractedField[Decimal]


class SegmentClaimPacketInput(ToolIdentity):
    inspection_completed: Literal[True]


class SegmentClaimPacketOutput(StrictContract):
    page_count: Annotated[int, Field(ge=1, le=MAX_PACKET_PAGES)]
    labels: Annotated[list[SegmentLabel], Field(min_length=1, max_length=MAX_PACKET_PAGES)]
    reason_codes: Annotated[list[ReasonCode], Field(max_length=20)] = []

    @model_validator(mode="after")
    def validate_label_count(self) -> Self:
        if len(self.labels) != self.page_count:
            raise ValueError("Exactly one segment label is required per page")
        return self


class ExtractClaimInput(ToolIdentity):
    attempt_number: Annotated[int, Field(ge=1, le=3)]


class ExtractClaimOutput(StrictContract):
    extraction_run_id: UUID
    extraction_candidate_id: UUID
    strategy: Literal["TEXT", "VISUAL"]
    completeness_score: Annotated[float, Field(ge=0, le=1)]
    reason_codes: Annotated[list[ReasonCode], Field(max_length=20)] = []


class LookupPolicyInput(ToolIdentity):
    policy_number: PolicyNumberText


class LookupPolicyOutput(StrictContract):
    matched: bool
    policy_id: UUID | None
    status: PolicyLookupStatus | None

    @model_validator(mode="after")
    def validate_match_consistency(self) -> Self:
        if self.matched != (self.policy_id is not None) or self.matched != (
            self.status is not None
        ):
            raise ValueError("A matched policy requires an ID and status; no match has neither")
        return self


class LookupPolicyVehicleInput(ToolIdentity):
    policy_number: PolicyNumberText
    vin: VinText


class LookupPolicyVehicleOutput(StrictContract):
    match_kind: VehicleMatchKind
    vehicle_id: UUID | None

    @model_validator(mode="after")
    def validate_match_consistency(self) -> Self:
        if (self.match_kind == "NONE") != (self.vehicle_id is None):
            raise ValueError("EXACT and NEAR matches require a vehicle ID; NONE has none")
        return self
