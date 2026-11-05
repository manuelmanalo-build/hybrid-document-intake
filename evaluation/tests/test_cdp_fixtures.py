import csv
import json
from collections import Counter
from pathlib import Path

from pypdf import PdfReader
from scripts.cdp_fixtures.data import ATTRIBUTES, CONNECTORS, REQUESTS
from scripts.cdp_fixtures.expected import expect, scan

from intake_orchestrator.agent.pdf_content import read_bounded_pdf_content

ROOT = Path("test-data/cdp")


def _rows(name: str) -> list[dict[str, str]]:
    with (ROOT / name).open(newline="") as stream:
        return list(csv.DictReader(stream))


def test_ground_truth_covers_twenty_packets_with_matching_pages() -> None:
    truth = _rows("ground_truth.csv")

    assert [row["request_id"] for row in truth] == [f"D{n:02d}" for n in range(1, 21)]
    for row in truth:
        reader = PdfReader(ROOT / "packets" / row["packet_file"], strict=True)
        assert len(reader.pages) == int(row["page_count"]) == len(row["segments"].split(";"))


def test_queue_mix_matches_the_scope() -> None:
    assert Counter(row["expected_queue"] for row in _rows("ground_truth.csv")) == Counter(
        READY_TO_BUILD=4,
        MAPPING_REVIEW=4,
        PRIVACY_REVIEW=4,
        AGREEMENT_REVIEW=3,
        RUSH_REVIEW=2,
        NEEDS_CLIENT_INFO=1,
        ONBOARDING_REVIEW=1,
        DUPLICATE_REVIEW=1,
    )


def test_ground_truth_matches_the_reference_rules() -> None:
    truth = {row["request_id"]: row for row in _rows("ground_truth.csv")}
    seen: set[tuple[str, str, str]] = set()
    for request in sorted(REQUESTS, key=lambda item: (item.received, item.request_id)):
        row = truth[request.request_id]
        expected = expect(request, json.loads(row["sample_identifier_findings"]), seen)
        assert row["expected_queue"] == expected.queue, request.request_id
        assert row["flags"] == ";".join(expected.flags), request.request_id


def test_mappings_reference_known_attributes_and_connectors_are_catalogued() -> None:
    attributes = {row["attribute"] for row in _rows("canonical_attributes.csv")}
    connectors = {row["connector_type"] for row in _rows("connectors.csv")}

    assert attributes == set(ATTRIBUTES)
    assert connectors == set(CONNECTORS)
    for row in _rows("ground_truth.csv"):
        assert row["connector_type"] in connectors
        for item in json.loads(row["mappings"]) + json.loads(row["fields_added_in_correspondence"]):
            assert item["proposed_attribute"] is None or item["proposed_attribute"] in attributes


def test_only_the_seeded_sample_carries_unmasked_identifiers_in_the_pdf_text() -> None:
    for row in _rows("ground_truth.csv"):
        content = read_bounded_pdf_content(
            ROOT / "packets" / row["packet_file"], include_images=True
        )
        if row["scan_quality"] == "degraded_image_only":
            assert content.image_data_urls
            continue
        sample_pages = [
            page
            for index, page in enumerate(content.text.split("[page ")[1:], start=1)
            if row["segments"].split(";")[index - 1] == "SAMPLE_EXTRACT"
        ]
        findings = scan("\n".join(sample_pages))
        assert findings == json.loads(row["sample_identifier_findings"]), row["request_id"]
    d11 = next(row for row in _rows("ground_truth.csv") if row["request_id"] == "D11")
    assert json.loads(d11["sample_identifier_findings"]) == {"EMAIL": 6, "PHONE": 6}
