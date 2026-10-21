"""Adapter from `test-data/claims/ground_truth.csv` to claim-contract expectations."""

from __future__ import annotations

import csv
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from intake_orchestrator.agent.claim_contracts import SegmentLabel

ExpectedQueue = Literal[
    "FAST_TRACK",
    "DESK_ADJUSTER",
    "COVERAGE_REVIEW",
    "SIU_REFERRAL",
    "BODILY_INJURY_ADJUSTER",
    "TOTAL_LOSS_REVIEW",
    "BELOW_DEDUCTIBLE_NOTICE",
    "DUPLICATE_REVIEW",
]

_SEGMENTS: dict[str, SegmentLabel] = {
    "notice": "LOSS_NOTICE",
    "estimate": "REPAIR_ESTIMATE",
    "police": "POLICE_REPORT",
    "email": "CORRESPONDENCE",
}


class ClaimTruth(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    claim_id: str
    packet_file: str
    scanned: bool
    segment_labels: list[SegmentLabel]
    expected_fields: dict[str, object]
    expected_queue: ExpectedQueue
    flags: list[str]


def load_claim_truth(path: Path) -> list[ClaimTruth]:
    with path.open(newline="") as stream:
        return [_truth(row) for row in csv.DictReader(stream)]


def _truth(row: dict[str, str]) -> ClaimTruth:
    year, make, model = row["vehicle"].split(" ", 2)
    injury_source = row["injury_source"]
    expected: dict[str, object] = {
        "policy_number": row["policy_number"],
        "named_insured": row["named_insured"],
        "loss_date": date.fromisoformat(row["loss_date"]),
        "report_date": date.fromisoformat(row["report_date"]),
        "coverage": row["coverage"].upper(),
        "vehicle_year": int(year),
        "vehicle_make": make,
        "vehicle_model": model,
        "vin_reported": row["vin_reported"],
        "vin_on_estimate": row["vin_on_policy"] or row["vin_reported"],
        "injury_indicated_on_form": injury_source == "loss_notice_checkbox",
        "injury_mentioned_in_correspondence": injury_source == "email",
        "third_party_involved": row["third_party_involved"] == "Y",
        "police_report_number": row["police_report_no"] or None,
        "estimate_number": row["estimate_number"],
        "estimate_total": Decimal(row["estimate_total"]),
    }
    return ClaimTruth.model_validate(
        {
            "claim_id": row["claim_id"],
            "packet_file": row["packet_file"],
            "scanned": row["scan_quality"] == "degraded_image_only",
            "segment_labels": [_SEGMENTS[kind] for kind in row["doc_types"].split(";")],
            "expected_fields": expected,
            "expected_queue": row["expected_route"].upper(),
            "flags": [flag for flag in row["flags"].split(";") if flag],
        }
    )
