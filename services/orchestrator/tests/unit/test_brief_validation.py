import csv
import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from intake_orchestrator.brief_validation import (
    BRIEF_RULE_DEFINITIONS,
    BriefCandidateData,
    BriefValidationContext,
    LeadTime,
    SowFacts,
    route_brief,
    validate_brief_rules,
)

ROOT = Path("test-data/briefs")


def _rows(name: str) -> list[dict[str, str]]:
    with (ROOT / name).open(newline="") as stream:
        return list(csv.DictReader(stream))


def _sows_by_brand() -> dict[str, tuple[SowFacts, ...]]:
    sows = {
        row["sow_number"]: SowFacts(
            sow_number=row["sow_number"],
            status=row["status"],
            start_date=date.fromisoformat(row["start_date"]),
            end_date=date.fromisoformat(row["end_date"]),
            total_fees=Decimal(row["total_fees"]),
            committed_fees=Decimal(row["committed_fees"]),
            services=frozenset(row["services_in_scope"].split(";")),
            regulated_category=row["regulated_category"],
        )
        for row in _rows("statements_of_work.csv")
    }
    grouped: dict[str, list[SowFacts]] = {}
    for row in _rows("sow_brands.csv"):
        grouped.setdefault(row["brand"], []).append(sows[row["sow_number"]])
    return {brand: tuple(items) for brand, items in grouped.items()}


def _lead_times() -> dict[str, LeadTime]:
    return {
        row["asset_type"]: LeadTime(row["service"], int(row["min_business_days"]))
        for row in _rows("lead_times.csv")
    }


def _candidate(row: dict[str, str]) -> BriefCandidateData:
    return BriefCandidateData.model_validate(
        {
            "client_name": row["client_name"],
            "brand": row["brand"],
            "campaign_name": row["campaign_name"],
            "sow_number_stated": row["sow_number_stated"] or None,
            "received_date": row["received_date"],
            "objective": row["objective"],
            "primary_kpi": row["primary_kpi"] or None,
            "kpi_target": row["kpi_target"] or None,
            "audience": row["audience"],
            "budget_total": row["budget_total"] or None,
            "launch_date": row["launch_date"],
            "end_date": row["end_date"],
            "launch_date_in_correspondence": row["launch_date_in_correspondence"] or None,
            "deliverables": json.loads(row["deliverables"]),
            "deliverables_added_in_correspondence": json.loads(
                row["deliverables_added_in_correspondence"]
            ),
        }
    )


def _route_all() -> dict[str, tuple[str, list[str]]]:
    sows, leads = _sows_by_brand(), _lead_times()
    seen: set[tuple[str, str, str]] = set()
    routed: dict[str, tuple[str, list[str]]] = {}
    rows = sorted(_rows("ground_truth.csv"), key=lambda r: (r["received_date"], r["brief_id"]))
    for row in rows:
        key = (row["sow_number_resolved"], row["campaign_name"].casefold(), row["launch_date"])
        context = BriefValidationContext(
            business_date=date(2026, 11, 20),
            brand_sows=sows.get(row["brand"], ()),
            lead_times=leads,
            duplicate_of_earlier_brief=key in seen,
            segment_labels=tuple(row["segments"].split(";")),
        )
        seen.add(key)
        route = route_brief(validate_brief_rules(_candidate(row), context))
        routed[row["brief_id"]] = (route.queue, route.flags)
    return routed


def test_rules_reproduce_every_ground_truth_queue() -> None:
    expected = {row["brief_id"]: row["expected_queue"] for row in _rows("ground_truth.csv")}

    assert {brief: queue for brief, (queue, _) in _route_all().items()} == expected


def test_rules_raise_the_ground_truth_flags() -> None:
    expected = {
        row["brief_id"]: set(filter(None, row["flags"].split(";")))
        for row in _rows("ground_truth.csv")
    }

    for brief, (_queue, flags) in _route_all().items():
        assert expected[brief] <= set(flags), brief


def test_zero_false_ready_for_planning() -> None:
    expected = {row["brief_id"]: row["expected_queue"] for row in _rows("ground_truth.csv")}
    false_ready = [
        brief
        for brief, (queue, _) in _route_all().items()
        if queue == "READY_FOR_PLANNING" and expected[brief] != "READY_FOR_PLANNING"
    ]

    assert false_ready == []


def test_every_rule_runs_once() -> None:
    results = validate_brief_rules(
        BriefCandidateData(), BriefValidationContext(business_date=date(2026, 11, 20))
    )

    assert [result.rule_id for result in results] == [rule for rule, _, _ in BRIEF_RULE_DEFINITIONS]
    assert route_brief(results).queue == "SCOPE_REVIEW"
