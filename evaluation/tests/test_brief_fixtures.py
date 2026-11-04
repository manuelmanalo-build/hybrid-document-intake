import csv
import json
from collections import Counter
from datetime import date
from pathlib import Path

from pypdf import PdfReader
from scripts.brief_fixtures.data import ASSET_TYPES, BRIEFS
from scripts.brief_fixtures.expected import business_days, expect

from intake_orchestrator.agent.pdf_content import read_bounded_pdf_content

ROOT = Path("test-data/briefs")
QUEUES = {
    "READY_FOR_PLANNING",
    "NEEDS_CLIENT_INFO",
    "SCOPE_REVIEW",
    "LEGAL_REVIEW",
    "RUSH_REVIEW",
    "TRAFFIC_REVIEW",
    "DUPLICATE_REVIEW",
}


def _rows(name: str) -> list[dict[str, str]]:
    with (ROOT / name).open(newline="") as stream:
        return list(csv.DictReader(stream))


def test_ground_truth_covers_twenty_packets_with_matching_pages() -> None:
    truth = _rows("ground_truth.csv")

    assert [row["brief_id"] for row in truth] == [f"B{n:02d}" for n in range(1, 21)]
    for row in truth:
        reader = PdfReader(ROOT / "packets" / row["packet_file"], strict=True)
        assert len(reader.pages) == int(row["page_count"])
        assert len(row["segments"].split(";")) == int(row["page_count"])


def test_queue_mix_matches_the_brief_scope() -> None:
    queues = Counter(row["expected_queue"] for row in _rows("ground_truth.csv"))

    assert set(queues) <= QUEUES
    assert queues == Counter(
        READY_FOR_PLANNING=7,
        SCOPE_REVIEW=4,
        NEEDS_CLIENT_INFO=2,
        LEGAL_REVIEW=2,
        RUSH_REVIEW=2,
        TRAFFIC_REVIEW=2,
        DUPLICATE_REVIEW=1,
    )


def test_ground_truth_matches_the_reference_rules_and_data() -> None:
    truth = {row["brief_id"]: row for row in _rows("ground_truth.csv")}
    seen: set[tuple[str, str, date]] = set()

    for brief in sorted(BRIEFS, key=lambda item: (item.received, item.brief_id)):
        expected = expect(brief, seen)
        row = truth[brief.brief_id]
        assert row["expected_queue"] == expected.queue, brief.brief_id
        assert row["flags"] == ";".join(expected.flags), brief.brief_id


def test_references_resolve_and_deliverables_are_known_asset_types() -> None:
    sows = {row["sow_number"]: row for row in _rows("statements_of_work.csv")}
    brands = {(row["sow_number"], row["brand"]) for row in _rows("sow_brands.csv")}
    lead_times = {row["asset_type"] for row in _rows("lead_times.csv")}

    assert lead_times == set(ASSET_TYPES)
    for row in _rows("ground_truth.csv"):
        if row["sow_number_resolved"]:
            assert row["sow_number_resolved"] in sows
            assert (row["sow_number_resolved"], row["brand"]) in brands
        for item in json.loads(row["deliverables"]) + json.loads(
            row["deliverables_added_in_correspondence"]
        ):
            assert item["asset_type"] in lead_times
            assert item["quantity"] >= 1


def test_business_days_and_the_lead_time_boundary() -> None:
    assert business_days(date(2026, 11, 6), date(2026, 11, 9)) == 1
    assert business_days(date(2026, 11, 9), date(2026, 11, 30)) == 15
    b20 = next(row for row in _rows("ground_truth.csv") if row["brief_id"] == "B20")
    assert b20["expected_queue"] == "READY_FOR_PLANNING"


def test_scanned_briefs_have_images_but_no_text_layer() -> None:
    for row in _rows("ground_truth.csv"):
        content = read_bounded_pdf_content(
            ROOT / "packets" / row["packet_file"], include_images=True
        )
        if row["scan_quality"] == "degraded_image_only":
            assert content.image_data_urls
            assert row["campaign_name"] not in content.text
        else:
            assert content.image_data_urls == ()
            assert row["campaign_name"] in content.text
