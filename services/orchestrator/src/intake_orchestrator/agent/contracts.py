from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

DocumentType = Literal["AP_INVOICE", "UNSUPPORTED", "UNKNOWN"]
Disposition = Literal["HUMAN_REVIEW", "REQUEST_INFORMATION", "REJECT_UNSUPPORTED"]
ReasonCode = Annotated[
    str,
    Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_.:-]+$"),
]
HumanAction = Annotated[str, Field(min_length=1, max_length=200)]
ToolName = Annotated[str, Field(min_length=1, max_length=100)]


class StrictContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ExtractedField[FieldValue](StrictContract):
    value: FieldValue | None
    confidence: Annotated[Decimal, Field(ge=0, le=1)]
    source_page: Annotated[int | None, Field(ge=1)] = None
    evidence_excerpt: Annotated[str, Field(max_length=500)]
    extraction_strategy: Literal["TEXT", "VISUAL"]
    provider: Annotated[str, Field(min_length=1, max_length=32)]
    model: Annotated[str, Field(min_length=1, max_length=200)]
    schema_version: Annotated[str, Field(min_length=1, max_length=64)]
    prompt_version: Annotated[str, Field(min_length=1, max_length=64)]


class APInvoiceCandidate(StrictContract):
    invoice_number: ExtractedField[str]
    invoice_date: ExtractedField[date]
    due_date: ExtractedField[date]
    supplier_name: ExtractedField[str]
    supplier_tax_id: ExtractedField[str]
    purchase_order_number: ExtractedField[str]
    currency: ExtractedField[str]
    subtotal: ExtractedField[Decimal]
    tax: ExtractedField[Decimal]
    total: ExtractedField[Decimal]


class ToolIdentity(StrictContract):
    tenant_id: UUID
    document_id: UUID
    agent_run_id: UUID


class InspectDocumentInput(ToolIdentity):
    """Tenant-scoped identity is the complete inspection request contract."""


class InspectDocumentOutput(StrictContract):
    document_id: UUID
    page_count: Annotated[int, Field(ge=1, le=1000)]
    text_available: bool
    visual_content_present: bool
    media_type: Literal["application/pdf"] = "application/pdf"


class ClassifyDocumentInput(ToolIdentity):
    inspection_completed: Literal[True]


class ClassifyDocumentOutput(StrictContract):
    value: DocumentType
    confidence: Annotated[float, Field(ge=0, le=1)]
    reason_codes: Annotated[list[ReasonCode], Field(max_length=20)]


class ExtractInvoiceInput(ToolIdentity):
    attempt_number: Annotated[int, Field(ge=1, le=3)]


class ExtractInvoiceOutput(StrictContract):
    extraction_run_id: UUID
    extraction_candidate_id: UUID
    strategy: Literal["TEXT", "VISUAL"]
    completeness_score: Annotated[float, Field(ge=0, le=1)]
    reason_codes: Annotated[list[ReasonCode], Field(max_length=20)] = []


class LookupSupplierInput(ToolIdentity):
    supplier_name: Annotated[str, Field(min_length=1, max_length=200)]
    supplier_tax_id: Annotated[str | None, Field(max_length=100)] = None


class LookupSupplierOutput(StrictContract):
    matched: bool
    supplier_id: UUID | None
    active: bool | None
    match_quality: Annotated[float, Field(ge=0, le=1)]


class LookupPurchaseOrderInput(ToolIdentity):
    purchase_order_number: Annotated[str, Field(min_length=1, max_length=100)]


class LookupPurchaseOrderOutput(StrictContract):
    matched: bool
    purchase_order_id: UUID | None
    active: bool | None
    match_quality: Annotated[float, Field(ge=0, le=1)]


class ValidateCandidateInput(ToolIdentity):
    extraction_candidate_id: UUID


class ValidateCandidateOutput(StrictContract):
    valid: bool
    retry_recommended: bool
    reason_codes: Annotated[list[ReasonCode], Field(max_length=20)]


class RetryExtractionInput(ToolIdentity):
    extraction_candidate_id: UUID
    next_strategy: Literal["TEXT", "VISUAL"]
    reason_codes: Annotated[list[ReasonCode], Field(min_length=1, max_length=10)]


class RetryExtractionOutput(StrictContract):
    authorized: Literal[True]
    next_attempt_number: Annotated[int, Field(ge=2, le=3)]
    next_strategy: Literal["TEXT", "VISUAL"]


class SubmitRecommendationInput(ToolIdentity):
    extraction_candidate_id: UUID | None = None
    document_type: DocumentType
    document_type_confidence: Annotated[float, Field(ge=0, le=1)]
    recommended_disposition: Disposition
    reason_codes: Annotated[list[ReasonCode], Field(max_length=20)]
    summary: Annotated[str, Field(min_length=1, max_length=1000)]
    requested_human_actions: Annotated[list[HumanAction], Field(max_length=20)]
    tools_used: Annotated[list[ToolName], Field(max_length=10)]


class Recommendation(StrictContract):
    id: UUID
    document_id: UUID
    document_type: ClassifyDocumentOutput
    recommended_disposition: Disposition
    reason_codes: Annotated[list[ReasonCode], Field(max_length=20)]
    summary: Annotated[str, Field(min_length=1, max_length=1000)]
    requested_human_actions: Annotated[list[HumanAction], Field(max_length=20)]
    tools_used: Annotated[list[ToolName], Field(max_length=10)]
    prompt_version: Annotated[str, Field(min_length=1, max_length=64)]
    agent_policy_version: Literal["1.0"] = "1.0"
    incomplete: bool = False


class SubmitRecommendationOutput(StrictContract):
    recommendation_id: UUID


ToolInput = (
    InspectDocumentInput
    | ClassifyDocumentInput
    | ExtractInvoiceInput
    | LookupSupplierInput
    | LookupPurchaseOrderInput
    | ValidateCandidateInput
    | RetryExtractionInput
    | SubmitRecommendationInput
)
