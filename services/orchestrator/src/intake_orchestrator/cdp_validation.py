"""Deterministic CDP-onboarding rules CD-001..CD-015 and `cdp-routing-v1` (ADR 0010).

Pure functions: callers load agreements, the canonical schema, connectors, duplicates, segments, and
`pii-scanner-v1` findings and pass them in. Scanner findings are facts the model cannot change.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from intake_orchestrator.brief_validation import business_days
from intake_orchestrator.validation import RuleResult

CDP_RULESET_VERSION = "cdp-rules-v1"
CDP_ROUTING_VERSION = "cdp-routing-v1"
CDP_RULE_DEFINITIONS: tuple[tuple[str, str, str], ...] = (
    ("CD-001", "The client has a data agreement", "ERROR"),
    ("CD-002", "The agreement is active at go-live and permits the data region", "ERROR"),
    ("CD-003", "Source, connector, cadence, region, go-live, and mappings are present", "REVIEW"),
    ("CD-004", "The connector exists and supports the cadence", "REVIEW"),
    ("CD-005", "At least one mapping targets an identity key", "REVIEW"),
    ("CD-006", "Every mapping targets a known, type-compatible attribute", "REVIEW"),
    ("CD-007", "Personal-data mappings are declared personal by the client", "REVIEW"),
    (
        "CD-008",
        "No unmasked identifiers in sample or dictionary pages; samples are scannable",
        "REVIEW",
    ),
    (
        "CD-009",
        "Personal data has a permitted lawful basis and, for consent, a mechanism",
        "REVIEW",
    ),
    ("CD-010", "Sensitive data is permitted by the agreement", "REVIEW"),
    ("CD-011", "Retention is within the agreement maximum", "REVIEW"),
    ("CD-012", "The connector's build lead time fits before go-live", "REVIEW"),
    ("CD-013", "Correspondence does not change go-live or add fields", "REVIEW"),
    (
        "CD-014",
        "No earlier non-rejected request for the same agreement, connector, and source",
        "REVIEW",
    ),
    ("CD-015", "Critical fields are confident and every page is labeled", "REVIEW"),
)
CdpQueue = Literal[
    "READY_TO_BUILD",
    "NEEDS_CLIENT_INFO",
    "AGREEMENT_REVIEW",
    "PRIVACY_REVIEW",
    "MAPPING_REVIEW",
    "RUSH_REVIEW",
    "ONBOARDING_REVIEW",
    "DUPLICATE_REVIEW",
]
COMPATIBLE_TYPES: dict[str, frozenset[str]] = {
    "STRING": frozenset({"STRING", "EMAIL", "PHONE"}),
    "INTEGER": frozenset({"NUMBER"}),
    "DECIMAL": frozenset({"NUMBER"}),
    "DATE": frozenset({"DATE"}),
    "TIMESTAMP": frozenset({"TIMESTAMP", "DATE"}),
    "BOOLEAN": frozenset({"BOOLEAN"}),
}
SCANNED_SEGMENTS = frozenset({"SAMPLE_EXTRACT", "DATA_DICTIONARY"})


class MappingData(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)
    source_field: str
    source_type: str
    proposed_attribute: str | None = None
    client_marked_personal: bool = False


class OnboardingCandidateData(BaseModel):
    """Business values of an onboarding candidate or reviewed version; unknown keys are ignored."""

    model_config = ConfigDict(extra="ignore", frozen=True)
    client_name: str | None = None
    agreement_number_stated: str | None = None
    received_date: date | None = None
    source_name: str | None = None
    connector_type: str | None = None
    refresh_cadence: str | None = None
    data_region: str | None = None
    lawful_basis: str | None = None
    consent_mechanism: str | None = None
    declares_no_personal_data: bool | None = None
    declares_sensitive_data: bool | None = None
    retention_days: int | None = None
    requested_go_live: date | None = None
    go_live_in_correspondence: date | None = None
    mappings: list[MappingData] = Field(default_factory=lambda: list[MappingData]())
    fields_added_in_correspondence: list[MappingData] = Field(
        default_factory=lambda: list[MappingData]()
    )
    field_confidence: dict[str, Decimal] = Field(default_factory=lambda: dict[str, Decimal]())


@dataclass(frozen=True, slots=True)
class AgreementFacts:
    agreement_number: str
    status: str
    start_date: date
    end_date: date
    regions: frozenset[str]
    lawful_bases: frozenset[str]
    sensitive_categories: frozenset[str]
    max_retention_days: int


@dataclass(frozen=True, slots=True)
class AttributeFacts:
    data_type: str
    personal_class: str
    identity_key: bool


@dataclass(frozen=True, slots=True)
class ConnectorFacts:
    cadences: frozenset[str]
    min_business_days: int


@dataclass(frozen=True, slots=True)
class OnboardingValidationContext:
    agreements: tuple[AgreementFacts, ...] = ()
    attributes: dict[str, AttributeFacts] = field(
        default_factory=lambda: dict[str, AttributeFacts]()
    )
    connectors: dict[str, ConnectorFacts] = field(
        default_factory=lambda: dict[str, ConnectorFacts]()
    )
    duplicate_of_earlier_request: bool = False
    segment_labels: tuple[str, ...] = ()
    # Scanner facts per page number: identifier counts, and pages without a text layer.
    page_findings: dict[int, dict[str, int]] = field(
        default_factory=lambda: dict[int, dict[str, int]]()
    )
    pages_without_text: frozenset[int] = frozenset()
    critical_confidence_threshold: Decimal = Decimal("0.70")
    critical_fields: tuple[str, ...] = (
        "client_name",
        "connector_type",
        "source_name",
        "requested_go_live",
    )


@dataclass(frozen=True, slots=True)
class OnboardingRoute:
    queue: CdpQueue
    reason_code: str
    flags: list[str] = field(default_factory=lambda: list[str]())


def safe_onboarding_data(value: object) -> OnboardingCandidateData:
    try:
        return OnboardingCandidateData.model_validate(value)
    except (ValidationError, InvalidOperation, ValueError):
        return OnboardingCandidateData()


def resolve_agreement(
    data: OnboardingCandidateData, agreements: tuple[AgreementFacts, ...]
) -> AgreementFacts | None:
    if data.agreement_number_stated:
        stated = [
            a for a in agreements if a.agreement_number == data.agreement_number_stated.strip()
        ]
        if stated:
            return stated[0]
    if data.requested_go_live is not None:
        covering = [a for a in agreements if a.start_date <= data.requested_go_live <= a.end_date]
        if covering:
            return covering[0]
    return max(agreements, key=lambda a: a.end_date) if agreements else None


def validate_onboarding_rules(
    data: OnboardingCandidateData, context: OnboardingValidationContext
) -> list[RuleResult]:
    agreement = resolve_agreement(data, context.agreements)
    mappings = [*data.mappings, *data.fields_added_in_correspondence]
    attributes = context.attributes
    personal = [
        item
        for item in mappings
        if item.proposed_attribute in attributes
        and attributes[item.proposed_attribute].personal_class != "NONE"
    ]
    return [
        _result("CD-001", "PASS")
        if agreement is not None
        else _result("CD-001", "FAIL", "AGREEMENT_NOT_FOUND"),
        _agreement_active(data, agreement),
        _required(data, mappings),
        _connector(data, context),
        _identity(mappings, attributes),
        _mapping_valid(mappings, attributes),
        _declared(data, personal),
        _scanner(context),
        _lawful_basis(data, personal, agreement),
        _sensitive(data, mappings, attributes, agreement),
        _retention(data, agreement),
        _lead_time(data, context),
        _correspondence(data),
        _result("CD-014", "REVIEW", "DUPLICATE_REQUEST")
        if context.duplicate_of_earlier_request
        else _result("CD-014", "PASS"),
        _quality(data, context),
    ]


def route_onboarding(results: list[RuleResult]) -> OnboardingRoute:
    """cdp-routing-v1: first matching row of the routing table in cdp-onboarding-scope.md."""
    outcome = {result.rule_id: result.outcome for result in results}
    flags = list(dict.fromkeys(result.reason_code for result in results if result.reason_code))

    def flagged(*rule_ids: str) -> bool:
        return any(outcome[rule_id] in {"FAIL", "REVIEW"} for rule_id in rule_ids)

    table: tuple[tuple[bool, CdpQueue, str], ...] = (
        (flagged("CD-014"), "DUPLICATE_REVIEW", "DUPLICATE_REQUEST"),
        (flagged("CD-001", "CD-002"), "AGREEMENT_REVIEW", "NO_VALID_AGREEMENT"),
        (
            flagged("CD-007", "CD-008", "CD-009", "CD-010", "CD-011"),
            "PRIVACY_REVIEW",
            "PERSONAL_DATA_FINDINGS",
        ),
        (flagged("CD-003"), "NEEDS_CLIENT_INFO", "REQUEST_INCOMPLETE"),
        (flagged("CD-004", "CD-005", "CD-006"), "MAPPING_REVIEW", "MAPPING_NEEDS_ENGINEER"),
        (flagged("CD-012"), "RUSH_REVIEW", "TIMELINE_AT_RISK"),
        (flagged("CD-013", "CD-015"), "ONBOARDING_REVIEW", "NEEDS_RECONCILING"),
    )
    for matched, queue, reason in table:
        if matched:
            return OnboardingRoute(queue=queue, reason_code=reason, flags=flags)
    return OnboardingRoute(queue="READY_TO_BUILD", reason_code="MAPPED_AND_CLEARED", flags=flags)


def _agreement_active(
    data: OnboardingCandidateData, agreement: AgreementFacts | None
) -> RuleResult:
    if agreement is None:
        return _result("CD-002", "NOT_APPLICABLE")
    go_live = data.requested_go_live
    if agreement.status != "ACTIVE" or (
        go_live is not None and not agreement.start_date <= go_live <= agreement.end_date
    ):
        return _result("CD-002", "FAIL", "AGREEMENT_NOT_ACTIVE")
    if data.data_region and data.data_region not in agreement.regions:
        return _result("CD-002", "FAIL", "REGION_NOT_PERMITTED")
    return _result("CD-002", "PASS")


def _required(data: OnboardingCandidateData, mappings: list[MappingData]) -> RuleResult:
    present = (
        data.source_name,
        data.connector_type,
        data.refresh_cadence,
        data.data_region,
        data.requested_go_live,
    )
    if all(value not in (None, "") for value in present) and mappings:
        return _result("CD-003", "PASS")
    return _result("CD-003", "REVIEW", "REQUEST_INCOMPLETE")


def _connector(data: OnboardingCandidateData, context: OnboardingValidationContext) -> RuleResult:
    if not data.connector_type:
        return _result("CD-004", "NOT_APPLICABLE")
    connector = context.connectors.get(data.connector_type)
    if connector is None or (
        data.refresh_cadence and data.refresh_cadence not in connector.cadences
    ):
        return _result("CD-004", "REVIEW", "CONNECTOR_UNSUPPORTED")
    return _result("CD-004", "PASS")


def _identity(mappings: list[MappingData], attributes: dict[str, AttributeFacts]) -> RuleResult:
    if not mappings:
        return _result("CD-005", "NOT_APPLICABLE")
    if any(
        item.proposed_attribute in attributes and attributes[item.proposed_attribute].identity_key
        for item in mappings
    ):
        return _result("CD-005", "PASS")
    return _result("CD-005", "REVIEW", "NO_IDENTITY_KEY")


def _mapping_valid(
    mappings: list[MappingData], attributes: dict[str, AttributeFacts]
) -> RuleResult:
    if not mappings:
        return _result("CD-006", "NOT_APPLICABLE")
    for item in mappings:
        attribute = attributes.get(item.proposed_attribute or "")
        if attribute is None or attribute.data_type not in COMPATIBLE_TYPES.get(
            item.source_type, frozenset()
        ):
            return _result("CD-006", "REVIEW", "MAPPING_INVALID")
    return _result("CD-006", "PASS")


def _declared(data: OnboardingCandidateData, personal: list[MappingData]) -> RuleResult:
    undeclared = any(not item.client_marked_personal for item in personal)
    if undeclared or (data.declares_no_personal_data and personal):
        return _result("CD-007", "REVIEW", "UNDECLARED_PERSONAL_DATA")
    return _result("CD-007", "PASS")


def _scanner(context: OnboardingValidationContext) -> RuleResult:
    pages = list(enumerate(context.segment_labels, start=1))
    if any(
        label in SCANNED_SEGMENTS and sum(context.page_findings.get(page, {}).values()) > 0
        for page, label in pages
    ):
        return _result("CD-008", "REVIEW", "UNMASKED_IDENTIFIERS_IN_PACKET")
    if any(
        label == "SAMPLE_EXTRACT" and page in context.pages_without_text for page, label in pages
    ):
        return _result("CD-008", "REVIEW", "SAMPLE_NOT_SCANNABLE")
    return _result("CD-008", "PASS")


def _lawful_basis(
    data: OnboardingCandidateData, personal: list[MappingData], agreement: AgreementFacts | None
) -> RuleResult:
    if not personal:
        return _result("CD-009", "NOT_APPLICABLE")
    basis = data.lawful_basis
    if (
        basis is None
        or (agreement is not None and basis not in agreement.lawful_bases)
        or (basis == "CONSENT" and not data.consent_mechanism)
    ):
        return _result("CD-009", "REVIEW", "LAWFUL_BASIS_MISSING")
    return _result("CD-009", "PASS")


def _sensitive(
    data: OnboardingCandidateData,
    mappings: list[MappingData],
    attributes: dict[str, AttributeFacts],
    agreement: AgreementFacts | None,
) -> RuleResult:
    sensitive = any(
        item.proposed_attribute in attributes
        and attributes[item.proposed_attribute].personal_class == "SENSITIVE"
        for item in mappings
    )
    if not sensitive and not data.declares_sensitive_data:
        return _result("CD-010", "PASS")
    if agreement is not None and agreement.sensitive_categories:
        return _result("CD-010", "PASS")
    return _result("CD-010", "REVIEW", "SENSITIVE_DATA_NOT_PERMITTED")


def _retention(data: OnboardingCandidateData, agreement: AgreementFacts | None) -> RuleResult:
    if agreement is None or data.retention_days is None:
        return _result("CD-011", "NOT_APPLICABLE")
    if data.retention_days > agreement.max_retention_days:
        return _result("CD-011", "REVIEW", "RETENTION_EXCEEDS_AGREEMENT")
    return _result("CD-011", "PASS")


def _lead_time(data: OnboardingCandidateData, context: OnboardingValidationContext) -> RuleResult:
    connector = context.connectors.get(data.connector_type or "")
    dates = [day for day in (data.requested_go_live, data.go_live_in_correspondence) if day]
    if connector is None or data.received_date is None or not dates:
        return _result("CD-012", "NOT_APPLICABLE")
    if business_days(data.received_date, min(dates)) < connector.min_business_days:
        return _result("CD-012", "REVIEW", "TIMELINE_AT_RISK")
    return _result("CD-012", "PASS")


def _correspondence(data: OnboardingCandidateData) -> RuleResult:
    moved = (
        data.go_live_in_correspondence is not None
        and data.go_live_in_correspondence != data.requested_go_live
    )
    if moved or data.fields_added_in_correspondence:
        return _result("CD-013", "REVIEW", "CORRESPONDENCE_CONFLICT")
    return _result("CD-013", "PASS")


def _quality(data: OnboardingCandidateData, context: OnboardingValidationContext) -> RuleResult:
    for name in context.critical_fields:
        confidence = data.field_confidence.get(name)
        if confidence is not None and confidence < context.critical_confidence_threshold:
            return _result("CD-015", "REVIEW", "CRITICAL_FIELD_LOW_CONFIDENCE")
    if "OTHER" in context.segment_labels:
        return _result("CD-015", "REVIEW", "UNSUPPORTED_SEGMENT")
    return _result("CD-015", "PASS")


def _result(
    rule_id: str,
    outcome: Literal["PASS", "FAIL", "REVIEW", "NOT_APPLICABLE"],
    reason_code: str | None = None,
) -> RuleResult:
    return RuleResult(rule_id=rule_id, outcome=outcome, reason_code=reason_code)
