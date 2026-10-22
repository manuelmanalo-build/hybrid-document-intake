"""Deterministic claim rules CR-001..CR-014 and the claim-routing-v1 queue policy (ADR 0008).

Pure functions only: callers load policy, vehicle, duplicate, and segment facts and pass them in.
The same code backs the advisory `validate_candidate` tool and authoritative parent validation,
so a queue can always be replayed from stored inputs and these versions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from intake_orchestrator.validation import RuleResult

CLAIM_RULESET_VERSION = "claim-rules-v1"
CLAIM_ROUTING_VERSION = "claim-routing-v1"
CLAIM_RULE_DEFINITIONS: tuple[tuple[str, str, str], ...] = (
    ("CR-001", "Policy number resolves to a tenant policy", "ERROR"),
    ("CR-002", "Loss date is present, not future, and not after the report date", "ERROR"),
    ("CR-003", "Loss date is within the policy term", "ERROR"),
    ("CR-004", "Policy was not cancelled on or before the loss date", "ERROR"),
    ("CR-005", "Reported VIN is on the policy", "ERROR"),
    ("CR-006", "No earlier non-rejected claim has the same policy, VIN, and loss date", "REVIEW"),
    ("CR-007", "Report date is within the late-reporting window", "REVIEW"),
    ("CR-008", "Loss is not shortly after policy inception", "REVIEW"),
    ("CR-009", "No injury is indicated anywhere in the packet", "REVIEW"),
    ("CR-010", "Estimate components reconcile to the total", "ERROR"),
    ("CR-011", "Estimate is below the total-loss threshold of actual cash value", "REVIEW"),
    ("CR-012", "Estimate exceeds the applicable deductible", "REVIEW"),
    ("CR-013", "Critical fields are present with sufficient confidence", "REVIEW"),
    ("CR-014", "Every page has a supported segment label", "REVIEW"),
)
ReviewQueueName = Literal[
    "FAST_TRACK",
    "DESK_ADJUSTER",
    "COVERAGE_REVIEW",
    "SIU_REFERRAL",
    "BODILY_INJURY_ADJUSTER",
    "TOTAL_LOSS_REVIEW",
    "BELOW_DEDUCTIBLE_NOTICE",
    "DUPLICATE_REVIEW",
]
VehicleMatch = Literal["EXACT", "NEAR", "NONE"]

_VIN_VALUES = dict(
    zip(
        "ABCDEFGHJKLMNPRSTUVWXYZ0123456789",
        [1, 2, 3, 4, 5, 6, 7, 8, 1, 2, 3, 4, 5, 7, 9, 2, 3, 4, 5, 6, 7, 8, 9, *range(10)],
        strict=True,
    )
)
_VIN_WEIGHTS = (8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2)
_NEAR_VIN_EDITS = 2


class ClaimCandidateData(BaseModel):
    """Business values of a claim candidate or reviewed version; unknown keys are ignored."""

    model_config = ConfigDict(extra="ignore", frozen=True)
    policy_number: str | None = None
    named_insured: str | None = None
    loss_date: date | None = None
    report_date: date | None = None
    coverage: Literal["COLLISION", "COMPREHENSIVE"] | None = None
    vehicle_year: int | None = None
    vehicle_make: str | None = None
    vin_reported: str | None = None
    vin_on_estimate: str | None = None
    injury_indicated_on_form: bool | None = None
    injury_mentioned_in_correspondence: bool | None = None
    third_party_involved: bool | None = None
    estimate_parts: Decimal | None = None
    estimate_labor: Decimal | None = None
    estimate_materials: Decimal | None = None
    estimate_tax: Decimal | None = None
    estimate_total: Decimal | None = None
    field_confidence: dict[str, Decimal] = Field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PolicyFacts:
    policy_number: str
    effective_date: date
    expiration_date: date
    status: str
    cancellation_date: date | None
    collision_deductible: Decimal
    comprehensive_deductible: Decimal


@dataclass(frozen=True, slots=True)
class VehicleFacts:
    vin: str
    model_year: int
    make: str
    actual_cash_value: Decimal


@dataclass(frozen=True, slots=True)
class ClaimValidationContext:
    business_date: date
    policy: PolicyFacts | None = None
    vehicles: tuple[VehicleFacts, ...] = ()
    duplicate_of_earlier_claim: bool = False
    segment_labels: tuple[str, ...] = ()
    late_report_days: int = 30
    inception_window_days: int = 14
    total_loss_ratio: Decimal = Decimal("0.75")
    fast_track_limit: Decimal = Decimal("5000.00")
    amount_tolerance: Decimal = Decimal("0.01")
    critical_confidence_threshold: Decimal = Decimal("0.70")
    critical_fields: tuple[str, ...] = (
        "policy_number",
        "loss_date",
        "vin_reported",
        "estimate_total",
    )


@dataclass(frozen=True, slots=True)
class ClaimRoute:
    queue: ReviewQueueName
    reason_code: str
    flags: list[str] = field(default_factory=lambda: list[str]())


def safe_claim_data(value: object) -> ClaimCandidateData:
    try:
        return ClaimCandidateData.model_validate(value)
    except (ValidationError, InvalidOperation, ValueError):
        return ClaimCandidateData()


def vin_check_digit_valid(vin: str) -> bool:
    if len(vin) != 17 or any(character not in _VIN_VALUES for character in vin):
        return False
    remainder = (
        sum(
            _VIN_VALUES[character] * weight
            for character, weight in zip(vin, _VIN_WEIGHTS, strict=True)
        )
        % 11
    )
    return vin[8] == ("X" if remainder == 10 else str(remainder))


def match_vehicle(
    vin: str | None,
    vehicles: tuple[VehicleFacts, ...],
    *,
    model_year: int | None,
    make: str | None,
) -> tuple[VehicleMatch, VehicleFacts | None]:
    """Exact VIN match, or a NEAR match for a mistyped VIN on the same year and make."""
    if not vin:
        return "NONE", None
    normalized = vin.strip().upper()
    for vehicle in vehicles:
        if vehicle.vin == normalized:
            return "EXACT", vehicle
    if vin_check_digit_valid(normalized):
        return "NONE", None
    for vehicle in vehicles:
        same_vehicle = model_year == vehicle.model_year and (make or "").casefold() == (
            vehicle.make.casefold()
        )
        if same_vehicle and _edit_distance(normalized, vehicle.vin) <= _NEAR_VIN_EDITS:
            return "NEAR", vehicle
    return "NONE", None


def validate_claim_rules(
    candidate: ClaimCandidateData, context: ClaimValidationContext
) -> list[RuleResult]:
    policy = context.policy
    match_kind, vehicle = match_vehicle(
        candidate.vin_reported,
        context.vehicles,
        model_year=candidate.vehicle_year,
        make=candidate.vehicle_make,
    )
    return [
        _policy_found(candidate, policy),
        _loss_date(candidate, context.business_date),
        _in_term(candidate, policy),
        _not_cancelled(candidate, policy),
        _vehicle_on_policy(candidate, policy, match_kind),
        _flag("CR-006", context.duplicate_of_earlier_claim, "DUPLICATE_CLAIM"),
        _late_report(candidate, context.late_report_days),
        _near_inception(candidate, policy, context.inception_window_days),
        _injury(candidate),
        _estimate_math(candidate, context.amount_tolerance),
        _total_loss(candidate, vehicle, context.total_loss_ratio),
        _deductible(candidate, policy),
        _critical_fields(candidate, context),
        _segments(context.segment_labels),
    ]


def route_claim(
    results: list[RuleResult], candidate: ClaimCandidateData, context: ClaimValidationContext
) -> ClaimRoute:
    """claim-routing-v1: first matching row of the routing table in claims-slice-scope.md."""
    outcome = {result.rule_id: result for result in results}
    flags = [result.reason_code for result in results if result.reason_code]
    # The dangerous disagreement: the form says no injury but correspondence describes one.
    if candidate.injury_indicated_on_form is False and candidate.injury_mentioned_in_correspondence:
        flags.append("INJURY_CONFLICT")
    if candidate.third_party_involved:
        flags.append("THIRD_PARTY_INVOLVED")
    flags = list(dict.fromkeys(flags))

    def failed(rule_id: str) -> bool:
        return outcome[rule_id].outcome == "FAIL"

    def review(rule_id: str) -> bool:
        return outcome[rule_id].outcome == "REVIEW"

    vehicle_code = outcome["CR-005"].reason_code
    over_limit = (
        candidate.estimate_total is not None and candidate.estimate_total > context.fast_track_limit
    )
    data_gaps = [
        rule_id
        for rule_id in ("CR-001", "CR-002", "CR-010", "CR-013", "CR-014")
        if outcome[rule_id].outcome in {"FAIL", "REVIEW"}
    ]
    table: tuple[tuple[bool, ReviewQueueName, str], ...] = (
        (review("CR-006"), "DUPLICATE_REVIEW", "DUPLICATE_CLAIM"),
        (failed("CR-003") or failed("CR-004"), "COVERAGE_REVIEW", "POLICY_NOT_IN_FORCE"),
        (vehicle_code == "VEHICLE_NOT_ON_POLICY", "COVERAGE_REVIEW", "VEHICLE_NOT_ON_POLICY"),
        (review("CR-007") or review("CR-008"), "SIU_REFERRAL", "SIU_INDICATOR"),
        (review("CR-009"), "BODILY_INJURY_ADJUSTER", "INJURY_INDICATED"),
        (review("CR-011"), "TOTAL_LOSS_REVIEW", "POSSIBLE_TOTAL_LOSS"),
        (review("CR-012"), "BELOW_DEDUCTIBLE_NOTICE", "AT_OR_BELOW_DEDUCTIBLE"),
        (vehicle_code == "VIN_DISCREPANCY", "DESK_ADJUSTER", "VIN_DISCREPANCY"),
        (bool(candidate.third_party_involved), "DESK_ADJUSTER", "THIRD_PARTY_INVOLVED"),
        (over_limit, "DESK_ADJUSTER", "ESTIMATE_OVER_FAST_TRACK_LIMIT"),
        (bool(data_gaps), "DESK_ADJUSTER", "DATA_REQUIRES_REVIEW"),
    )
    for matched, queue, reason in table:
        if matched:
            return ClaimRoute(queue=queue, reason_code=reason, flags=flags)
    return ClaimRoute(queue="FAST_TRACK", reason_code="CLEAN_WITHIN_FAST_TRACK_LIMIT", flags=flags)


def applicable_deductible(candidate: ClaimCandidateData, policy: PolicyFacts) -> Decimal | None:
    if candidate.coverage == "COLLISION":
        return policy.collision_deductible
    if candidate.coverage == "COMPREHENSIVE":
        return policy.comprehensive_deductible
    return None


def _policy_found(candidate: ClaimCandidateData, policy: PolicyFacts | None) -> RuleResult:
    if not candidate.policy_number:
        return _result("CR-001", "FAIL", "POLICY_NUMBER_REQUIRED")
    if policy is None:
        return _result("CR-001", "FAIL", "POLICY_NOT_FOUND")
    return _result("CR-001", "PASS")


def _loss_date(candidate: ClaimCandidateData, business_date: date) -> RuleResult:
    loss = candidate.loss_date
    if loss is None or loss > business_date:
        return _result("CR-002", "FAIL", "LOSS_DATE_INVALID")
    if candidate.report_date is not None and loss > candidate.report_date:
        return _result("CR-002", "FAIL", "LOSS_DATE_INVALID")
    return _result("CR-002", "PASS")


def _in_term(candidate: ClaimCandidateData, policy: PolicyFacts | None) -> RuleResult:
    if policy is None or candidate.loss_date is None:
        return _result("CR-003", "NOT_APPLICABLE")
    if policy.effective_date <= candidate.loss_date < policy.expiration_date:
        return _result("CR-003", "PASS")
    return _result("CR-003", "FAIL", "LOSS_OUTSIDE_POLICY_TERM")


def _not_cancelled(candidate: ClaimCandidateData, policy: PolicyFacts | None) -> RuleResult:
    if policy is None or candidate.loss_date is None:
        return _result("CR-004", "NOT_APPLICABLE")
    if policy.cancellation_date is not None and candidate.loss_date >= policy.cancellation_date:
        return _result("CR-004", "FAIL", "POLICY_CANCELLED_BEFORE_LOSS")
    return _result("CR-004", "PASS")


def _vehicle_on_policy(
    candidate: ClaimCandidateData, policy: PolicyFacts | None, match_kind: VehicleMatch
) -> RuleResult:
    if policy is None:
        return _result("CR-005", "NOT_APPLICABLE")
    if not candidate.vin_reported:
        return _result("CR-005", "FAIL", "VIN_REQUIRED")
    if match_kind == "EXACT":
        return _result("CR-005", "PASS")
    if match_kind == "NEAR":
        return _result("CR-005", "REVIEW", "VIN_DISCREPANCY")
    return _result("CR-005", "FAIL", "VEHICLE_NOT_ON_POLICY")


def _late_report(candidate: ClaimCandidateData, window_days: int) -> RuleResult:
    if candidate.loss_date is None or candidate.report_date is None:
        return _result("CR-007", "NOT_APPLICABLE")
    if (candidate.report_date - candidate.loss_date).days > window_days:
        return _result("CR-007", "REVIEW", "LATE_REPORTING")
    return _result("CR-007", "PASS")


def _near_inception(
    candidate: ClaimCandidateData, policy: PolicyFacts | None, window_days: int
) -> RuleResult:
    if policy is None or candidate.loss_date is None:
        return _result("CR-008", "NOT_APPLICABLE")
    if 0 <= (candidate.loss_date - policy.effective_date).days <= window_days:
        return _result("CR-008", "REVIEW", "LOSS_NEAR_INCEPTION")
    return _result("CR-008", "PASS")


def _injury(candidate: ClaimCandidateData) -> RuleResult:
    if candidate.injury_indicated_on_form or candidate.injury_mentioned_in_correspondence:
        return _result("CR-009", "REVIEW", "INJURY_INDICATED")
    return _result("CR-009", "PASS")


def _estimate_math(candidate: ClaimCandidateData, tolerance: Decimal) -> RuleResult:
    parts = (
        candidate.estimate_parts,
        candidate.estimate_labor,
        candidate.estimate_materials,
        candidate.estimate_tax,
        candidate.estimate_total,
    )
    if any(value is None for value in parts):
        return _result("CR-010", "NOT_APPLICABLE")
    *components, total = [value for value in parts if value is not None]
    if abs(sum(components, Decimal("0")) - total) <= tolerance:
        return _result("CR-010", "PASS")
    return _result("CR-010", "FAIL", "ESTIMATE_ARITHMETIC_MISMATCH")


def _total_loss(
    candidate: ClaimCandidateData, vehicle: VehicleFacts | None, ratio: Decimal
) -> RuleResult:
    if vehicle is None or candidate.estimate_total is None:
        return _result("CR-011", "NOT_APPLICABLE")
    if candidate.estimate_total >= vehicle.actual_cash_value * ratio:
        return _result("CR-011", "REVIEW", "POSSIBLE_TOTAL_LOSS")
    return _result("CR-011", "PASS")


def _deductible(candidate: ClaimCandidateData, policy: PolicyFacts | None) -> RuleResult:
    if policy is None or candidate.estimate_total is None:
        return _result("CR-012", "NOT_APPLICABLE")
    deductible = applicable_deductible(candidate, policy)
    if deductible is None:
        return _result("CR-012", "NOT_APPLICABLE")
    if candidate.estimate_total <= deductible:
        return _result("CR-012", "REVIEW", "AT_OR_BELOW_DEDUCTIBLE")
    return _result("CR-012", "PASS")


def _critical_fields(candidate: ClaimCandidateData, context: ClaimValidationContext) -> RuleResult:
    for name in context.critical_fields:
        if getattr(candidate, name, None) is None:
            return _result("CR-013", "REVIEW", "CRITICAL_FIELD_MISSING")
        confidence = candidate.field_confidence.get(name)
        if confidence is not None and confidence < context.critical_confidence_threshold:
            return _result("CR-013", "REVIEW", "CRITICAL_FIELD_LOW_CONFIDENCE")
    return _result("CR-013", "PASS")


def _segments(labels: tuple[str, ...]) -> RuleResult:
    if not labels:
        return _result("CR-014", "NOT_APPLICABLE")
    if any(label == "OTHER" for label in labels):
        return _result("CR-014", "REVIEW", "UNSUPPORTED_SEGMENT")
    return _result("CR-014", "PASS")


def _flag(rule_id: str, raised: bool, reason: str) -> RuleResult:
    return _result(rule_id, "REVIEW", reason) if raised else _result(rule_id, "PASS")


def _result(
    rule_id: str,
    outcome: Literal["PASS", "FAIL", "REVIEW", "NOT_APPLICABLE"],
    reason_code: str | None = None,
) -> RuleResult:
    return RuleResult(rule_id=rule_id, outcome=outcome, reason_code=reason_code)


def _edit_distance(left: str, right: str) -> int:
    previous = list(range(len(right) + 1))
    for row, left_char in enumerate(left, start=1):
        current = [row]
        for column, right_char in enumerate(right, start=1):
            current.append(
                min(
                    previous[column] + 1,
                    current[column - 1] + 1,
                    previous[column - 1] + (left_char != right_char),
                )
            )
        previous = current
    return previous[-1]
