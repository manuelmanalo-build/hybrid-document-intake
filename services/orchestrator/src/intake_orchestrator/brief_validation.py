"""Deterministic brief rules CB-001..CB-014 and the brief-routing-v1 policy (ADR 0009).

Pure functions: callers load SOW, lead-time, duplicate, and segment facts and pass them in. The
same code backs the advisory tool and authoritative validation, so a queue is replayable from
stored inputs and these versions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from intake_orchestrator.validation import RuleResult

BRIEF_RULESET_VERSION = "brief-rules-v1"
BRIEF_ROUTING_VERSION = "brief-routing-v1"
BRIEF_RULE_DEFINITIONS: tuple[tuple[str, str, str], ...] = (
    ("CB-001", "The brand is covered by a tenant statement of work", "ERROR"),
    ("CB-002", "The SOW is active and the launch date is within its term", "ERROR"),
    ("CB-003", "Objective, audience, KPI, launch date, and deliverables are present", "REVIEW"),
    ("CB-004", "The KPI target is measurable", "REVIEW"),
    ("CB-005", "A budget is present", "REVIEW"),
    ("CB-006", "The budget is within the SOW's remaining fees", "REVIEW"),
    ("CB-007", "Every deliverable's service is in SOW scope", "REVIEW"),
    ("CB-008", "Every deliverable has its minimum production lead time", "REVIEW"),
    ("CB-009", "Correspondence does not change the launch date or add deliverables", "REVIEW"),
    ("CB-010", "The SOW is not in a regulated category", "REVIEW"),
    ("CB-011", "No earlier non-rejected brief has the same SOW, campaign, and launch", "REVIEW"),
    ("CB-012", "Received date, launch date, and end date are in order", "ERROR"),
    ("CB-013", "Critical fields are present with sufficient confidence", "REVIEW"),
    ("CB-014", "Every page has a supported segment label", "REVIEW"),
)
BriefQueue = Literal[
    "READY_FOR_PLANNING",
    "NEEDS_CLIENT_INFO",
    "SCOPE_REVIEW",
    "LEGAL_REVIEW",
    "RUSH_REVIEW",
    "TRAFFIC_REVIEW",
    "DUPLICATE_REVIEW",
]


class DeliverableData(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)
    asset_type: str
    quantity: int = 1
    description: str = ""
    due_date: date | None = None


class BriefCandidateData(BaseModel):
    """Business values of a brief candidate or reviewed version; unknown keys are ignored."""

    model_config = ConfigDict(extra="ignore", frozen=True)
    client_name: str | None = None
    brand: str | None = None
    campaign_name: str | None = None
    sow_number_stated: str | None = None
    received_date: date | None = None
    objective: str | None = None
    primary_kpi: str | None = None
    kpi_target: str | None = None
    audience: str | None = None
    budget_total: Decimal | None = None
    launch_date: date | None = None
    end_date: date | None = None
    launch_date_in_correspondence: date | None = None
    deliverables: list[DeliverableData] = Field(default_factory=lambda: list[DeliverableData]())
    deliverables_added_in_correspondence: list[DeliverableData] = Field(
        default_factory=lambda: list[DeliverableData]()
    )
    field_confidence: dict[str, Decimal] = Field(default_factory=lambda: dict[str, Decimal]())


@dataclass(frozen=True, slots=True)
class SowFacts:
    sow_number: str
    status: str
    start_date: date
    end_date: date
    total_fees: Decimal
    committed_fees: Decimal
    services: frozenset[str]
    regulated_category: str

    @property
    def remaining_fees(self) -> Decimal:
        return self.total_fees - self.committed_fees


@dataclass(frozen=True, slots=True)
class LeadTime:
    service: str
    min_business_days: int


@dataclass(frozen=True, slots=True)
class BriefValidationContext:
    business_date: date
    brand_sows: tuple[SowFacts, ...] = ()
    lead_times: dict[str, LeadTime] = field(default_factory=lambda: dict[str, LeadTime]())
    duplicate_of_earlier_brief: bool = False
    segment_labels: tuple[str, ...] = ()
    critical_confidence_threshold: Decimal = Decimal("0.70")
    critical_fields: tuple[str, ...] = ("client_name", "brand", "campaign_name", "launch_date")


@dataclass(frozen=True, slots=True)
class BriefRoute:
    queue: BriefQueue
    reason_code: str
    flags: list[str] = field(default_factory=lambda: list[str]())


def safe_brief_data(value: object) -> BriefCandidateData:
    try:
        return BriefCandidateData.model_validate(value)
    except (ValidationError, InvalidOperation, ValueError):
        return BriefCandidateData()


def business_days(start: date, end: date) -> int:
    """Weekdays after `start` up to and including `end`; holidays are ignored in v1."""
    days = 0
    current = start
    while current < end:
        current += timedelta(days=1)
        if current.weekday() < 5:
            days += 1
    return days


def resolve_sow(data: BriefCandidateData, sows: tuple[SowFacts, ...]) -> SowFacts | None:
    """The stated SOW if it covers the brand, else the SOW in force at launch, else the latest."""
    if data.sow_number_stated:
        stated = [sow for sow in sows if sow.sow_number == data.sow_number_stated.strip()]
        if stated:
            return stated[0]
    if data.launch_date is not None:
        covering = [sow for sow in sows if sow.start_date <= data.launch_date <= sow.end_date]
        if covering:
            return covering[0]
    return max(sows, key=lambda sow: sow.end_date) if sows else None


def validate_brief_rules(
    data: BriefCandidateData, context: BriefValidationContext
) -> list[RuleResult]:
    sow = resolve_sow(data, context.brand_sows)
    deliverables = [*data.deliverables, *data.deliverables_added_in_correspondence]
    return [
        _result("CB-001", "PASS")
        if sow is not None
        else _result("CB-001", "FAIL", "SOW_NOT_FOUND"),
        _sow_active(data, sow),
        _required(data),
        _kpi(data),
        _result("CB-005", "PASS")
        if data.budget_total is not None
        else _result("CB-005", "REVIEW", "BUDGET_MISSING"),
        _budget(data, sow),
        _scope(deliverables, sow, context),
        _lead_time(data, deliverables, context),
        _correspondence(data),
        _regulated(sow),
        _result("CB-011", "REVIEW", "DUPLICATE_BRIEF")
        if context.duplicate_of_earlier_brief
        else _result("CB-011", "PASS"),
        _dates(data),
        _critical(data, context),
        _segments(context.segment_labels),
    ]


def route_brief(results: list[RuleResult]) -> BriefRoute:
    """brief-routing-v1: first matching row of the routing table in briefs-slice-scope.md."""
    outcome = {result.rule_id: result.outcome for result in results}
    flags = list(dict.fromkeys(result.reason_code for result in results if result.reason_code))

    def flagged(*rule_ids: str) -> bool:
        return any(outcome[rule_id] in {"FAIL", "REVIEW"} for rule_id in rule_ids)

    table: tuple[tuple[bool, BriefQueue, str], ...] = (
        (flagged("CB-011"), "DUPLICATE_REVIEW", "DUPLICATE_BRIEF"),
        (flagged("CB-001", "CB-002"), "SCOPE_REVIEW", "NO_ACTIVE_SOW"),
        (flagged("CB-006", "CB-007"), "SCOPE_REVIEW", "CHANGE_ORDER_NEEDED"),
        (flagged("CB-010"), "LEGAL_REVIEW", "REGULATED_CONTENT"),
        (flagged("CB-003", "CB-004", "CB-005"), "NEEDS_CLIENT_INFO", "BRIEF_MISSING_INFORMATION"),
        (flagged("CB-008"), "RUSH_REVIEW", "TIMELINE_AT_RISK"),
        (flagged("CB-009", "CB-012", "CB-013", "CB-014"), "TRAFFIC_REVIEW", "NEEDS_RECONCILING"),
    )
    for matched, queue, reason in table:
        if matched:
            return BriefRoute(queue=queue, reason_code=reason, flags=flags)
    return BriefRoute(queue="READY_FOR_PLANNING", reason_code="COMPLETE_AND_IN_SCOPE", flags=flags)


def _sow_active(data: BriefCandidateData, sow: SowFacts | None) -> RuleResult:
    if sow is None or data.launch_date is None:
        return _result("CB-002", "NOT_APPLICABLE")
    if sow.status == "ACTIVE" and sow.start_date <= data.launch_date <= sow.end_date:
        return _result("CB-002", "PASS")
    return _result("CB-002", "FAIL", "SOW_NOT_ACTIVE_AT_LAUNCH")


def _required(data: BriefCandidateData) -> RuleResult:
    present = (
        data.objective,
        data.audience,
        data.primary_kpi,
        data.launch_date,
        data.deliverables or None,
    )
    if all(value is not None and value != "" for value in present):
        return _result("CB-003", "PASS")
    return _result("CB-003", "REVIEW", "BRIEF_INCOMPLETE")


def _kpi(data: BriefCandidateData) -> RuleResult:
    if data.kpi_target and any(character.isdigit() for character in data.kpi_target):
        return _result("CB-004", "PASS")
    return _result("CB-004", "REVIEW", "KPI_NOT_MEASURABLE")


def _budget(data: BriefCandidateData, sow: SowFacts | None) -> RuleResult:
    if sow is None or data.budget_total is None:
        return _result("CB-006", "NOT_APPLICABLE")
    if data.budget_total <= sow.remaining_fees:
        return _result("CB-006", "PASS")
    return _result("CB-006", "REVIEW", "OVER_SOW_BUDGET")


def _scope(
    deliverables: list[DeliverableData], sow: SowFacts | None, context: BriefValidationContext
) -> RuleResult:
    if sow is None or not deliverables:
        return _result("CB-007", "NOT_APPLICABLE")
    for item in deliverables:
        lead = context.lead_times.get(item.asset_type)
        if lead is None or lead.service not in sow.services:
            return _result("CB-007", "REVIEW", "OUT_OF_SCOPE_SERVICE")
    return _result("CB-007", "PASS")


def _lead_time(
    data: BriefCandidateData, deliverables: list[DeliverableData], context: BriefValidationContext
) -> RuleResult:
    received = data.received_date
    launches = [day for day in (data.launch_date, data.launch_date_in_correspondence) if day]
    if received is None or not launches or not deliverables:
        return _result("CB-008", "NOT_APPLICABLE")
    effective_launch = min(launches)
    for item in deliverables:
        lead = context.lead_times.get(item.asset_type)
        if lead is None:
            continue
        due = min(item.due_date or effective_launch, effective_launch)
        if business_days(received, due) < lead.min_business_days:
            return _result("CB-008", "REVIEW", "TIMELINE_AT_RISK")
    return _result("CB-008", "PASS")


def _correspondence(data: BriefCandidateData) -> RuleResult:
    moved = (
        data.launch_date_in_correspondence is not None
        and data.launch_date_in_correspondence != data.launch_date
    )
    if moved or data.deliverables_added_in_correspondence:
        return _result("CB-009", "REVIEW", "CORRESPONDENCE_CONFLICT")
    return _result("CB-009", "PASS")


def _regulated(sow: SowFacts | None) -> RuleResult:
    if sow is None:
        return _result("CB-010", "NOT_APPLICABLE")
    if sow.regulated_category != "NONE":
        return _result("CB-010", "REVIEW", "REGULATED_CONTENT")
    return _result("CB-010", "PASS")


def _dates(data: BriefCandidateData) -> RuleResult:
    if data.received_date is None or data.launch_date is None or data.end_date is None:
        return _result("CB-012", "NOT_APPLICABLE")
    if data.received_date < data.launch_date < data.end_date:
        return _result("CB-012", "PASS")
    return _result("CB-012", "FAIL", "DATES_INVALID")


def _critical(data: BriefCandidateData, context: BriefValidationContext) -> RuleResult:
    for name in context.critical_fields:
        if getattr(data, name, None) in (None, ""):
            return _result("CB-013", "REVIEW", "CRITICAL_FIELD_MISSING")
        confidence = data.field_confidence.get(name)
        if confidence is not None and confidence < context.critical_confidence_threshold:
            return _result("CB-013", "REVIEW", "CRITICAL_FIELD_LOW_CONFIDENCE")
    return _result("CB-013", "PASS")


def _segments(labels: tuple[str, ...]) -> RuleResult:
    if not labels:
        return _result("CB-014", "NOT_APPLICABLE")
    if "OTHER" in labels:
        return _result("CB-014", "REVIEW", "UNSUPPORTED_SEGMENT")
    return _result("CB-014", "PASS")


def _result(
    rule_id: str,
    outcome: Literal["PASS", "FAIL", "REVIEW", "NOT_APPLICABLE"],
    reason_code: str | None = None,
) -> RuleResult:
    return RuleResult(rule_id=rule_id, outcome=outcome, reason_code=reason_code)
