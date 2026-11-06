"""Typed contracts for the CDP-onboarding document type (ADR 0010)."""

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

OnboardingSegmentLabel = Literal[
    "ONBOARDING_FORM",
    "DATA_DICTIONARY",
    "SAMPLE_EXTRACT",
    "PRIVACY_ADDENDUM",
    "CORRESPONDENCE",
    "OTHER",
]
ConnectorType = Literal[
    "SHOPIFY", "SALESFORCE_CRM", "SFTP_CSV", "SNOWFLAKE_SHARE", "GA4", "BRAZE", "REST_WEBHOOK"
]
Cadence = Literal["REALTIME", "HOURLY", "DAILY", "WEEKLY", "ONE_TIME"]
DataRegion = Literal["US", "EU", "UK", "CA", "APAC"]
LawfulBasis = Literal["CONSENT", "CONTRACT", "LEGITIMATE_INTEREST"]
SourceType = Literal["STRING", "INTEGER", "DECIMAL", "BOOLEAN", "DATE", "TIMESTAMP"]
IdentifierType = Literal["EMAIL", "PHONE", "SSN", "CARD"]
MAX_ONBOARDING_PAGES = 20
MAX_MAPPINGS = 200


class OnboardingPageSegment(StrictContract):
    page_number: Annotated[int, Field(ge=1, le=MAX_ONBOARDING_PAGES)]
    label: OnboardingSegmentLabel
    confidence: Annotated[Decimal, Field(ge=0, le=1)]


class OnboardingPacketSegmentation(StrictContract):
    segments: Annotated[
        list[OnboardingPageSegment], Field(min_length=1, max_length=MAX_ONBOARDING_PAGES)
    ]

    @model_validator(mode="after")
    def validate_page_sequence(self) -> Self:
        pages = [segment.page_number for segment in self.segments]
        if pages != list(range(1, len(pages) + 1)):
            raise ValueError("Segments must label pages 1..n exactly once, in order")
        return self


class ExtractedMapping(StrictContract):
    """One source field and the canonical attribute proposed for it (a proposal, not a decision)."""

    source_field: Annotated[str, Field(min_length=1, max_length=120)]
    source_type: SourceType
    proposed_attribute: Annotated[str | None, Field(max_length=80)]
    client_marked_personal: bool
    confidence: Annotated[Decimal, Field(ge=0, le=1)]
    source_page: Annotated[int | None, Field(ge=1, le=MAX_ONBOARDING_PAGES)]
    evidence_excerpt: Annotated[str, Field(max_length=500)]


class OnboardingRequestCandidate(StrictContract):
    client_name: ExtractedField[str]
    agreement_number_stated: ExtractedField[str]
    received_date: ExtractedField[date]
    source_name: ExtractedField[str]
    connector_type: ExtractedField[ConnectorType]
    refresh_cadence: ExtractedField[Cadence]
    data_region: ExtractedField[DataRegion]
    lawful_basis: ExtractedField[LawfulBasis]
    consent_mechanism: ExtractedField[str]
    declares_no_personal_data: ExtractedField[bool]
    declares_sensitive_data: ExtractedField[bool]
    retention_days: ExtractedField[int]
    estimated_records: ExtractedField[int]
    requested_go_live: ExtractedField[date]
    go_live_in_correspondence: ExtractedField[date]
    data_owner: ExtractedField[str]
    mappings: Annotated[list[ExtractedMapping], Field(max_length=MAX_MAPPINGS)]
    fields_added_in_correspondence: Annotated[
        list[ExtractedMapping], Field(max_length=MAX_MAPPINGS)
    ]


class ScanOnboardingPacketInput(ToolIdentity):
    inspection_completed: Literal[True]


class PageScanFindings(StrictContract):
    page_number: Annotated[int, Field(ge=1, le=MAX_ONBOARDING_PAGES)]
    text_layer: bool
    findings: dict[IdentifierType, Annotated[int, Field(ge=0)]]


class ScanOnboardingPacketOutput(StrictContract):
    scanner_version: str
    pages: Annotated[list[PageScanFindings], Field(min_length=1, max_length=MAX_ONBOARDING_PAGES)]
    total_identifiers: Annotated[int, Field(ge=0)]
    reason_codes: Annotated[list[ReasonCode], Field(max_length=20)] = []


class SegmentOnboardingPacketInput(ToolIdentity):
    inspection_completed: Literal[True]


class SegmentOnboardingPacketOutput(StrictContract):
    page_count: Annotated[int, Field(ge=1, le=MAX_ONBOARDING_PAGES)]
    labels: Annotated[
        list[OnboardingSegmentLabel], Field(min_length=1, max_length=MAX_ONBOARDING_PAGES)
    ]
    reason_codes: Annotated[list[ReasonCode], Field(max_length=20)] = []

    @model_validator(mode="after")
    def validate_label_count(self) -> Self:
        if len(self.labels) != self.page_count:
            raise ValueError("Exactly one segment label is required per page")
        return self


class LookupDataAgreementInput(ToolIdentity):
    client_name: Annotated[str, Field(min_length=1, max_length=200)]
    agreement_number: Annotated[str | None, Field(max_length=40)] = None


class LookupDataAgreementOutput(StrictContract):
    matched: bool
    agreement_number: str | None
    status: Literal["ACTIVE", "EXPIRED", "SUSPENDED"] | None
    agreement_reference: UUID | None

    @model_validator(mode="after")
    def validate_match_consistency(self) -> Self:
        if self.matched != (self.agreement_number is not None):
            raise ValueError("A matched agreement requires its number; no match has none")
        return self
