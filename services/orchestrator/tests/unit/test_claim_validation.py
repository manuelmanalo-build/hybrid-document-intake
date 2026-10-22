import csv
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from intake_orchestrator.claim_validation import (
    CLAIM_RULE_DEFINITIONS,
    ClaimCandidateData,
    ClaimValidationContext,
    PolicyFacts,
    VehicleFacts,
    match_vehicle,
    route_claim,
    validate_claim_rules,
    vin_check_digit_valid,
)

ROOT = Path("test-data/claims")
SEGMENTS = {
    "notice": "LOSS_NOTICE",
    "estimate": "REPAIR_ESTIMATE",
    "police": "POLICE_REPORT",
    "email": "CORRESPONDENCE",
}


def _rows(name: str) -> list[dict[str, str]]:
    with (ROOT / name).open(newline="") as stream:
        return list(csv.DictReader(stream))


def _policies() -> dict[str, PolicyFacts]:
    return {
        row["policy_number"]: PolicyFacts(
            policy_number=row["policy_number"],
            effective_date=date.fromisoformat(row["effective_date"]),
            expiration_date=date.fromisoformat(row["expiration_date"]),
            status=row["status"].upper(),
            cancellation_date=(
                date.fromisoformat(row["cancellation_date"]) if row["cancellation_date"] else None
            ),
            collision_deductible=Decimal(row["collision_deductible"]),
            comprehensive_deductible=Decimal(row["comprehensive_deductible"]),
        )
        for row in _rows("policies.csv")
    }


def _vehicles() -> dict[str, tuple[VehicleFacts, ...]]:
    grouped: dict[str, list[VehicleFacts]] = {}
    for row in _rows("policy_vehicles.csv"):
        grouped.setdefault(row["policy_number"], []).append(
            VehicleFacts(
                vin=row["vin"],
                model_year=int(row["year"]),
                make=row["make"],
                actual_cash_value=Decimal(row["acv_valuation"]),
            )
        )
    return {key: tuple(value) for key, value in grouped.items()}


def _candidate(row: dict[str, str]) -> ClaimCandidateData:
    year, make, _model = row["vehicle"].split(" ", 2)
    return ClaimCandidateData(
        policy_number=row["policy_number"],
        loss_date=date.fromisoformat(row["loss_date"]),
        report_date=date.fromisoformat(row["report_date"]),
        coverage=row["coverage"].upper(),  # type: ignore[arg-type]
        vehicle_year=int(year),
        vehicle_make=make,
        vin_reported=row["vin_reported"],
        injury_indicated_on_form=row["injury_source"] == "loss_notice_checkbox",
        injury_mentioned_in_correspondence=row["injury_source"] == "email",
        third_party_involved=row["third_party_involved"] == "Y",
        estimate_total=Decimal(row["estimate_total"]),
    )


def _route_all() -> dict[str, tuple[str, list[str]]]:
    policies = _policies()
    vehicles = _vehicles()
    seen: set[tuple[str, str, str]] = set()
    routed: dict[str, tuple[str, list[str]]] = {}
    for row in sorted(_rows("ground_truth.csv"), key=lambda r: (r["report_date"], r["claim_id"])):
        key = (row["policy_number"], row["vin_reported"], row["loss_date"])
        context = ClaimValidationContext(
            business_date=date(2026, 10, 22),
            policy=policies.get(row["policy_number"]),
            vehicles=vehicles.get(row["policy_number"], ()),
            duplicate_of_earlier_claim=key in seen,
            segment_labels=tuple(SEGMENTS[kind] for kind in row["doc_types"].split(";")),
        )
        seen.add(key)
        candidate = _candidate(row)
        decision = route_claim(validate_claim_rules(candidate, context), candidate, context)
        routed[row["claim_id"]] = (decision.queue, decision.flags)
    return routed


def test_rules_reproduce_every_ground_truth_queue() -> None:
    expected = {row["claim_id"]: row["expected_route"].upper() for row in _rows("ground_truth.csv")}
    routed = _route_all()

    assert {claim: queue for claim, (queue, _flags) in routed.items()} == expected


def test_zero_false_fast_tracks_on_ground_truth() -> None:
    expected = {row["claim_id"]: row["expected_route"].upper() for row in _rows("ground_truth.csv")}
    false_fast_tracks = [
        claim
        for claim, (queue, _flags) in _route_all().items()
        if queue == "FAST_TRACK" and expected[claim] != "FAST_TRACK"
    ]

    assert false_fast_tracks == []


def test_flags_explain_the_hard_cases() -> None:
    routed = _route_all()

    assert "INJURY_CONFLICT" in routed["C10"][1]
    assert "INJURY_CONFLICT" not in routed["C11"][1]
    assert "THIRD_PARTY_INVOLVED" in routed["C10"][1]
    assert "VIN_DISCREPANCY" in routed["C09"][1]
    assert "DUPLICATE_CLAIM" in routed["C14"][1]
    assert "LATE_REPORTING" in routed["C12"][1]


def test_every_rule_runs_once_with_a_defined_id() -> None:
    results = validate_claim_rules(
        ClaimCandidateData(), ClaimValidationContext(business_date=date(2026, 10, 22))
    )

    assert [result.rule_id for result in results] == [rule for rule, _, _ in CLAIM_RULE_DEFINITIONS]


def test_missing_or_low_confidence_data_can_never_fast_track() -> None:
    policy = _policies()["NFM-PA-4471200"]
    vehicles = _vehicles()["NFM-PA-4471200"]
    base = {
        "policy_number": "NFM-PA-4471200",
        "loss_date": date(2026, 9, 3),
        "report_date": date(2026, 9, 4),
        "coverage": "COMPREHENSIVE",
        "vehicle_year": 2021,
        "vehicle_make": "Honda",
        "vin_reported": vehicles[0].vin,
        "injury_indicated_on_form": False,
        "injury_mentioned_in_correspondence": False,
        "third_party_involved": False,
        "estimate_total": Decimal("3241.87"),
    }
    context = ClaimValidationContext(
        business_date=date(2026, 10, 22), policy=policy, vehicles=vehicles
    )
    clean = ClaimCandidateData.model_validate(base)
    assert route_claim(validate_claim_rules(clean, context), clean, context).queue == "FAST_TRACK"

    for variant in (
        {**base, "estimate_total": None},
        {**base, "field_confidence": {"estimate_total": Decimal("0.40")}},
        {
            **base,
            "estimate_parts": Decimal("1"),
            "estimate_labor": Decimal("1"),
            "estimate_materials": Decimal("0"),
            "estimate_tax": Decimal("0"),
        },
    ):
        candidate = ClaimCandidateData.model_validate(variant)
        route = route_claim(validate_claim_rules(candidate, context), candidate, context)
        assert route.queue == "DESK_ADJUSTER"
        assert route.reason_code == "DATA_REQUIRES_REVIEW"

    other_page = ClaimValidationContext(
        business_date=date(2026, 10, 22),
        policy=policy,
        vehicles=vehicles,
        segment_labels=("LOSS_NOTICE", "OTHER"),
    )
    assert route_claim(validate_claim_rules(clean, other_page), clean, other_page).queue == (
        "DESK_ADJUSTER"
    )


@pytest.mark.parametrize(
    ("vin", "expected"),
    [("1HGHBUSR5MC181960", "EXACT"), ("1HGHBUSR5MC118960", "NEAR"), ("5YJ3E1EA7PF000001", "NONE")],
)
def test_vehicle_matching_distinguishes_typos_from_other_vehicles(vin: str, expected: str) -> None:
    vehicles = _vehicles()["NFM-PA-4471200"]

    kind, _vehicle = match_vehicle(vin, vehicles, model_year=2021, make="Honda")

    assert kind == expected


def test_a_different_valid_vin_is_another_vehicle_not_a_typo() -> None:
    vehicles = _vehicles()["NFM-PA-4471200"]
    original = vehicles[0].vin
    changed = original[:16] + ("1" if original[16] != "1" else "2")
    other = next(
        candidate
        for digit in "0123456789X"
        if vin_check_digit_valid(candidate := changed[:8] + digit + changed[9:])
    )

    kind, _vehicle = match_vehicle(other, vehicles, model_year=2021, make="Honda")

    assert other != original
    assert kind == "NONE"
