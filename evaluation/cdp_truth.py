"""Adapters from `test-data/cdp` to onboarding-contract expectations and offline rule context."""

from __future__ import annotations

import csv
import json
from datetime import date
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from intake_orchestrator.agent.cdp_contracts import OnboardingSegmentLabel
from intake_orchestrator.cdp_validation import AgreementFacts, AttributeFacts, ConnectorFacts

SCORED_FIELDS = (
    "client_name",
    "agreement_number_stated",
    "received_date",
    "source_name",
    "connector_type",
    "refresh_cadence",
    "data_region",
    "lawful_basis",
    "declares_no_personal_data",
    "declares_sensitive_data",
    "retention_days",
    "estimated_records",
    "requested_go_live",
    "go_live_in_correspondence",
)


class ExpectedMapping(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    source_field: str
    source_type: str
    proposed_attribute: str | None
    client_marked_personal: bool


class CdpTruth(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    request_id: str
    packet_file: str
    scanned: bool
    client_name: str
    consent_mechanism: str | None
    segment_labels: list[OnboardingSegmentLabel]
    expected_fields: dict[str, object]
    mappings: list[ExpectedMapping]
    mappings_added: list[ExpectedMapping]
    sample_findings: dict[str, int]
    expected_queue: str
    flags: list[str]


def _split(value: str) -> frozenset[str]:
    return frozenset(filter(None, value.split(";")))


def _mappings(raw: str) -> list[ExpectedMapping]:
    return [
        ExpectedMapping(
            source_field=item["source_field"],
            source_type=item["source_type"],
            proposed_attribute=item["proposed_attribute"],
            client_marked_personal=item["client_marked_personal"],
        )
        for item in json.loads(raw)
    ]


def load_cdp_truth(path: Path) -> list[CdpTruth]:
    truths: list[CdpTruth] = []
    with path.open(newline="") as stream:
        for row in csv.DictReader(stream):

            def optional(name: str, row: dict[str, str] = row) -> str | None:
                return row[name] or None

            def day(name: str, row: dict[str, str] = row) -> date | None:
                return date.fromisoformat(row[name]) if row[name] else None

            truths.append(
                CdpTruth.model_validate(
                    {
                        "request_id": row["request_id"],
                        "packet_file": row["packet_file"],
                        "scanned": row["scan_quality"] == "degraded_image_only",
                        "client_name": row["client_name"],
                        "consent_mechanism": optional("consent_mechanism"),
                        "segment_labels": row["segments"].split(";"),
                        "expected_fields": {
                            "client_name": row["client_name"],
                            "agreement_number_stated": optional("agreement_number_stated"),
                            "received_date": day("received_date"),
                            "source_name": row["source_name"],
                            "connector_type": row["connector_type"],
                            "refresh_cadence": optional("refresh_cadence"),
                            "data_region": row["data_region"],
                            "lawful_basis": optional("lawful_basis"),
                            "declares_no_personal_data": row["declares_no_personal_data"] == "true",
                            "declares_sensitive_data": row["declares_sensitive_data"] == "true",
                            "retention_days": int(row["retention_days"]),
                            "estimated_records": int(row["estimated_records"]),
                            "requested_go_live": day("requested_go_live"),
                            "go_live_in_correspondence": day("go_live_in_correspondence"),
                        },
                        "mappings": _mappings(row["mappings"]),
                        "mappings_added": _mappings(row["fields_added_in_correspondence"]),
                        "sample_findings": json.loads(row["sample_identifier_findings"]),
                        "expected_queue": row["expected_queue"],
                        "flags": sorted(_split(row["flags"])),
                    }
                )
            )
    return truths


def load_agreements(root: Path) -> dict[str, tuple[AgreementFacts, ...]]:
    grouped: dict[str, list[AgreementFacts]] = {}
    with (root / "data_agreements.csv").open(newline="") as stream:
        for row in csv.DictReader(stream):
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


def load_attributes(root: Path) -> dict[str, AttributeFacts]:
    with (root / "canonical_attributes.csv").open(newline="") as stream:
        return {
            row["attribute"]: AttributeFacts(
                row["data_type"], row["personal_class"], row["identity_key"] == "true"
            )
            for row in csv.DictReader(stream)
        }


def load_connectors(root: Path) -> dict[str, ConnectorFacts]:
    with (root / "connectors.csv").open(newline="") as stream:
        return {
            row["connector_type"]: ConnectorFacts(
                _split(row["cadences"]), int(row["min_business_days"])
            )
            for row in csv.DictReader(stream)
        }
