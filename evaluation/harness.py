from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

ALLOWED_TOOLS = frozenset(
    {
        "inspect_document",
        "classify_document",
        "extract_invoice_from_text",
        "extract_invoice_from_visual_content",
        "lookup_supplier",
        "lookup_purchase_order",
        "validate_candidate",
        "retry_extraction",
        "submit_recommendation",
    }
)
REQUIRED_FIELDS = frozenset(
    {"invoice_number", "invoice_date", "supplier_name", "currency", "total"}
)


class RecommendedDocumentType(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: Literal["AP_INVOICE", "UNSUPPORTED", "UNKNOWN"]
    confidence: Decimal = Field(ge=0, le=1)


class Recommendation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_id: UUID
    document_type: RecommendedDocumentType
    recommended_disposition: Literal["HUMAN_REVIEW", "REQUEST_INFORMATION", "REJECT_UNSUPPORTED"]
    reason_codes: list[str]
    summary: str = Field(min_length=1, max_length=1000)
    requested_human_actions: list[str]
    tools_used: list[str]
    prompt_version: str
    agent_policy_version: Literal["1.0"]


class Expected(BaseModel):
    classification: Literal["AP_INVOICE", "UNSUPPORTED", "UNKNOWN"]
    fields: dict[str, object]
    rule_outcomes: dict[str, str]


class Observation(BaseModel):
    classification: Literal["AP_INVOICE", "UNSUPPORTED", "UNKNOWN"]
    fields: dict[str, object]
    evidence_fields: list[str]
    executed_tools: list[str]
    extraction_attempts: int = Field(ge=0, le=3)
    rule_outcomes: dict[str, str]
    recommendation: dict[str, object]
    corrected_fields: list[str] = Field(default_factory=list)


class EvaluationCase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_id: str
    fixture: str
    expected: Expected
    observation: Observation


class EvaluationReport(BaseModel):
    case_count: int
    document_classification_accuracy: Decimal
    field_extraction_accuracy: Decimal
    required_field_completeness: Decimal
    evidence_coverage: Decimal
    recommendation_format_validity: Decimal
    unauthorized_tool_rate: Decimal
    average_tool_count: Decimal
    extraction_retry_rate: Decimal
    rule_agreement: Decimal
    human_correction_rate: Decimal


def load_cases(path: Path) -> list[EvaluationCase]:
    return [EvaluationCase.model_validate(item) for item in json.loads(path.read_text())]


def evaluate(cases: list[EvaluationCase]) -> EvaluationReport:
    if not cases:
        raise ValueError("At least one evaluation case is required")
    classifications = 0
    field_matches = 0
    field_total = 0
    required_present = 0
    required_total = 0
    evidence_present = 0
    evidence_total = 0
    valid_recommendations = 0
    unauthorized = 0
    executed = 0
    tool_count = 0
    retried = 0
    rule_matches = 0
    rule_total = 0
    corrections = 0
    extracted_fields = 0
    for case in cases:
        expected = case.expected
        observed = case.observation
        classifications += observed.classification == expected.classification
        for name, expected_value in expected.fields.items():
            field_total += 1
            field_matches += _normalized(observed.fields.get(name)) == _normalized(expected_value)
        if expected.classification == "AP_INVOICE":
            required_total += len(REQUIRED_FIELDS)
            required_present += sum(
                observed.fields.get(name) not in (None, "") for name in REQUIRED_FIELDS
            )
            evidence_total += len(observed.fields)
            evidence_present += sum(name in observed.evidence_fields for name in observed.fields)
        try:
            Recommendation.model_validate(observed.recommendation)
            valid_recommendations += 1
        except ValueError:
            pass
        tool_count += len(observed.executed_tools)
        executed += len(observed.executed_tools)
        unauthorized += sum(tool not in ALLOWED_TOOLS for tool in observed.executed_tools)
        retried += observed.extraction_attempts > 1
        for rule_id, expected_outcome in expected.rule_outcomes.items():
            rule_total += 1
            rule_matches += observed.rule_outcomes.get(rule_id) == expected_outcome
        corrections += len(observed.corrected_fields)
        extracted_fields += len(observed.fields)
    count = len(cases)
    return EvaluationReport(
        case_count=count,
        document_classification_accuracy=_ratio(classifications, count),
        field_extraction_accuracy=_ratio(field_matches, field_total),
        required_field_completeness=_ratio(required_present, required_total),
        evidence_coverage=_ratio(evidence_present, evidence_total),
        recommendation_format_validity=_ratio(valid_recommendations, count),
        unauthorized_tool_rate=_ratio(unauthorized, executed),
        average_tool_count=(Decimal(tool_count) / Decimal(count)).quantize(Decimal("0.0001")),
        extraction_retry_rate=_ratio(retried, count),
        rule_agreement=_ratio(rule_matches, rule_total),
        human_correction_rate=_ratio(corrections, extracted_fields),
    )


def _ratio(numerator: int, denominator: int) -> Decimal:
    if denominator == 0:
        return Decimal("0.0000")
    return (Decimal(numerator) / Decimal(denominator)).quantize(Decimal("0.0001"))


def _normalized(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip().casefold()
