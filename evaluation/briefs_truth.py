"""Adapters from `test-data/briefs` to brief-contract expectations and offline rule context."""

from __future__ import annotations

import csv
import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from intake_orchestrator.agent.brief_contracts import BriefSegmentLabel
from intake_orchestrator.brief_validation import LeadTime, SowFacts

# Structured fields scored by exact match. Free-text fields (objective, audience, markets,
# approver, mandatories) vary legitimately in wording and are reported as presence only.
SCORED_FIELDS = (
    "client_name",
    "brand",
    "campaign_name",
    "sow_number_stated",
    "received_date",
    "primary_kpi",
    "kpi_target",
    "budget_total",
    "launch_date",
    "end_date",
    "launch_date_in_correspondence",
)
FREE_TEXT_FIELDS = ("objective", "audience", "markets", "approver", "mandatories")


class ExpectedDeliverable(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    asset_type: str
    quantity: int
    due_date: date | None


class BriefTruth(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    brief_id: str
    packet_file: str
    scanned: bool
    segment_labels: list[BriefSegmentLabel]
    expected_fields: dict[str, object]
    deliverables: list[ExpectedDeliverable]
    deliverables_added: list[ExpectedDeliverable]
    expected_queue: str
    flags: list[str]


def load_brief_truth(path: Path) -> list[BriefTruth]:
    with path.open(newline="") as stream:
        return [_truth(row) for row in csv.DictReader(stream)]


def load_sows_by_brand(root: Path) -> dict[str, tuple[SowFacts, ...]]:
    with (root / "statements_of_work.csv").open(newline="") as stream:
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
            for row in csv.DictReader(stream)
        }
    grouped: dict[str, list[SowFacts]] = {}
    with (root / "sow_brands.csv").open(newline="") as stream:
        for row in csv.DictReader(stream):
            grouped.setdefault(row["brand"], []).append(sows[row["sow_number"]])
    return {brand: tuple(items) for brand, items in grouped.items()}


def load_lead_times(root: Path) -> dict[str, LeadTime]:
    with (root / "lead_times.csv").open(newline="") as stream:
        return {
            row["asset_type"]: LeadTime(row["service"], int(row["min_business_days"]))
            for row in csv.DictReader(stream)
        }


def _deliverables(raw: str) -> list[ExpectedDeliverable]:
    return [
        ExpectedDeliverable(
            asset_type=item["asset_type"],
            quantity=int(item["quantity"]),
            due_date=date.fromisoformat(item["due_date"]) if item["due_date"] else None,
        )
        for item in json.loads(raw)
    ]


def _truth(row: dict[str, str]) -> BriefTruth:
    def optional(name: str) -> str | None:
        return row[name] or None

    expected: dict[str, object] = {
        "client_name": row["client_name"],
        "brand": row["brand"],
        "campaign_name": row["campaign_name"],
        "sow_number_stated": optional("sow_number_stated"),
        "received_date": date.fromisoformat(row["received_date"]),
        "primary_kpi": optional("primary_kpi"),
        "kpi_target": optional("kpi_target"),
        "budget_total": Decimal(row["budget_total"]) if row["budget_total"] else None,
        "launch_date": date.fromisoformat(row["launch_date"]),
        "end_date": date.fromisoformat(row["end_date"]),
        "launch_date_in_correspondence": (
            date.fromisoformat(row["launch_date_in_correspondence"])
            if row["launch_date_in_correspondence"]
            else None
        ),
    }
    return BriefTruth.model_validate(
        {
            "brief_id": row["brief_id"],
            "packet_file": row["packet_file"],
            "scanned": row["scan_quality"] == "degraded_image_only",
            "segment_labels": row["segments"].split(";"),
            "expected_fields": expected,
            "deliverables": _deliverables(row["deliverables"]),
            "deliverables_added": _deliverables(row["deliverables_added_in_correspondence"]),
            "expected_queue": row["expected_queue"],
            "flags": [flag for flag in row["flags"].split(";") if flag],
        }
    )
