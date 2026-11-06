import csv
import json
from datetime import date
from pathlib import Path

from intake_orchestrator.cdp_validation import (
    CDP_RULE_DEFINITIONS,
    AgreementFacts,
    AttributeFacts,
    ConnectorFacts,
    OnboardingCandidateData,
    OnboardingValidationContext,
    route_onboarding,
    validate_onboarding_rules,
)

ROOT = Path("test-data/cdp")


def _rows(name: str) -> list[dict[str, str]]:
    with (ROOT / name).open(newline="") as stream:
        return list(csv.DictReader(stream))


def _split(value: str) -> frozenset[str]:
    return frozenset(filter(None, value.split(";")))


def _agreements_by_client() -> dict[str, tuple[AgreementFacts, ...]]:
    grouped: dict[str, list[AgreementFacts]] = {}
    for row in _rows("data_agreements.csv"):
        grouped.setdefault(row["client"], []).append(
            AgreementFacts(
                agreement_number=row["agreement_number"],
                status=row["status"],
                start_date=date.fromisoformat(row["start_date"]),
                end_date=date.fromisoformat(row["end_date"]),
                regions=_split(row["regions"]),
                lawful_bases=_split(row["lawful_bases"]),
                sensitive_categories=_split(row["sensitive_categories"]),
                max_retention_days=int(row["max_retention_days"]),
            )
        )
    return {client: tuple(items) for client, items in grouped.items()}


def _context(row: dict[str, str], *, duplicate: bool) -> OnboardingValidationContext:
    labels = tuple(row["segments"].split(";"))
    findings = json.loads(row["sample_identifier_findings"])
    sample_pages = [page for page, label in enumerate(labels, start=1) if label == "SAMPLE_EXTRACT"]
    return OnboardingValidationContext(
        agreements=_agreements_by_client().get(row["client_name"], ()),
        attributes={
            item["attribute"]: AttributeFacts(
                item["data_type"], item["personal_class"], item["identity_key"] == "true"
            )
            for item in _rows("canonical_attributes.csv")
        },
        connectors={
            item["connector_type"]: ConnectorFacts(
                _split(item["cadences"]), int(item["min_business_days"])
            )
            for item in _rows("connectors.csv")
        },
        duplicate_of_earlier_request=duplicate,
        segment_labels=labels,
        page_findings={sample_pages[0]: findings} if findings and sample_pages else {},
        pages_without_text=frozenset(range(1, len(labels) + 1))
        if row["scan_quality"] == "degraded_image_only"
        else frozenset(),
    )


def _candidate(row: dict[str, str]) -> OnboardingCandidateData:
    def opt(name: str) -> str | None:
        return row[name] or None

    return OnboardingCandidateData.model_validate(
        {
            "client_name": row["client_name"],
            "agreement_number_stated": opt("agreement_number_stated"),
            "received_date": row["received_date"],
            "source_name": row["source_name"],
            "connector_type": row["connector_type"],
            "refresh_cadence": opt("refresh_cadence"),
            "data_region": row["data_region"],
            "lawful_basis": opt("lawful_basis"),
            "consent_mechanism": opt("consent_mechanism"),
            "declares_no_personal_data": row["declares_no_personal_data"] == "true",
            "declares_sensitive_data": row["declares_sensitive_data"] == "true",
            "retention_days": int(row["retention_days"]),
            "requested_go_live": opt("requested_go_live"),
            "go_live_in_correspondence": opt("go_live_in_correspondence"),
            "mappings": json.loads(row["mappings"]),
            "fields_added_in_correspondence": json.loads(row["fields_added_in_correspondence"]),
        }
    )


def _route_all() -> dict[str, tuple[str, list[str]]]:
    seen: set[tuple[str, str, str]] = set()
    routed: dict[str, tuple[str, list[str]]] = {}
    rows = sorted(_rows("ground_truth.csv"), key=lambda r: (r["received_date"], r["request_id"]))
    for row in rows:
        key = (row["agreement_resolved"], row["connector_type"], row["source_name"].casefold())
        duplicate = bool(row["agreement_resolved"]) and key in seen
        if row["agreement_resolved"]:
            seen.add(key)
        route = route_onboarding(
            validate_onboarding_rules(_candidate(row), _context(row, duplicate=duplicate))
        )
        routed[row["request_id"]] = (route.queue, route.flags)
    return routed


def test_rules_reproduce_every_ground_truth_queue() -> None:
    expected = {row["request_id"]: row["expected_queue"] for row in _rows("ground_truth.csv")}

    assert {key: queue for key, (queue, _) in _route_all().items()} == expected


def test_rules_raise_every_ground_truth_flag() -> None:
    expected = {row["request_id"]: _split(row["flags"]) for row in _rows("ground_truth.csv")}

    for key, (_, flags) in _route_all().items():
        assert expected[key] <= set(flags), key


def test_both_hard_gates_hold_on_ground_truth() -> None:
    truth = {row["request_id"]: row for row in _rows("ground_truth.csv")}
    routed = _route_all()

    assert not [
        key
        for key, (queue, _) in routed.items()
        if queue == "READY_TO_BUILD" and truth[key]["expected_queue"] != "READY_TO_BUILD"
    ]
    with_findings = [
        key for key, row in truth.items() if json.loads(row["sample_identifier_findings"])
    ]
    assert with_findings == ["D11"]
    assert all(routed[key][0] == "PRIVACY_REVIEW" for key in with_findings)


def test_a_scanned_sample_extract_is_never_ready() -> None:
    row = next(item for item in _rows("ground_truth.csv") if item["request_id"] == "D01")
    context = _context(row, duplicate=False)
    unscannable = OnboardingValidationContext(
        agreements=context.agreements,
        attributes=context.attributes,
        connectors=context.connectors,
        segment_labels=context.segment_labels,
        pages_without_text=frozenset({3}),
    )

    route = route_onboarding(validate_onboarding_rules(_candidate(row), unscannable))

    assert route.queue == "PRIVACY_REVIEW"
    assert "SAMPLE_NOT_SCANNABLE" in route.flags


def test_every_rule_runs_once() -> None:
    results = validate_onboarding_rules(OnboardingCandidateData(), OnboardingValidationContext())

    assert [result.rule_id for result in results] == [rule for rule, _, _ in CDP_RULE_DEFINITIONS]


def test_a_reviewer_exclusion_clears_an_unmappable_field() -> None:
    row = next(item for item in _rows("ground_truth.csv") if item["request_id"] == "D05")
    candidate = _candidate(row)
    assert (
        route_onboarding(validate_onboarding_rules(candidate, _context(row, duplicate=False))).queue
        == "MAPPING_REVIEW"
    )
    mappings = [
        m.model_copy(update={"excluded": m.proposed_attribute is None}) for m in candidate.mappings
    ]

    excluded = candidate.model_copy(update={"mappings": mappings})

    assert (
        route_onboarding(validate_onboarding_rules(excluded, _context(row, duplicate=False))).queue
        == "READY_TO_BUILD"
    )
