import csv
from collections import Counter
from decimal import Decimal
from pathlib import Path

from pypdf import PdfReader

from intake_orchestrator.agent.pdf_content import read_bounded_pdf_content

ROOT = Path("test-data/claims")
QUEUES = {
    "fast_track",
    "desk_adjuster",
    "coverage_review",
    "siu_referral",
    "bodily_injury_adjuster",
    "total_loss_review",
    "below_deductible_notice",
    "duplicate_review",
}
_VIN_VALUES = dict(
    zip(
        "ABCDEFGHJKLMNPRSTUVWXYZ0123456789",
        [1, 2, 3, 4, 5, 6, 7, 8, 1, 2, 3, 4, 5, 7, 9, 2, 3, 4, 5, 6, 7, 8, 9] + list(range(10)),
        strict=True,
    )
)
_VIN_WEIGHTS = [8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2]


def _rows(name: str) -> list[dict[str, str]]:
    with (ROOT / name).open(newline="") as stream:
        return list(csv.DictReader(stream))


def _vin_check_digit_valid(vin: str) -> bool:
    if len(vin) != 17 or any(char not in _VIN_VALUES for char in vin):
        return False
    remainder = sum(_VIN_VALUES[c] * w for c, w in zip(vin, _VIN_WEIGHTS, strict=True)) % 11
    return vin[8] == ("X" if remainder == 10 else str(remainder))


def test_ground_truth_covers_twenty_packets_with_matching_page_counts() -> None:
    truth = _rows("ground_truth.csv")

    assert [row["claim_id"] for row in truth] == [f"C{n:02d}" for n in range(1, 21)]
    for row in truth:
        reader = PdfReader(ROOT / "packets" / row["packet_file"], strict=True)
        assert len(reader.pages) == int(row["page_count"])
        assert len(row["doc_types"].split(";")) == int(row["page_count"])


def test_expected_queues_and_mix_match_the_claims_scope() -> None:
    queues = Counter(row["expected_route"] for row in _rows("ground_truth.csv"))

    assert set(queues) <= QUEUES
    assert queues == Counter(
        fast_track=7,
        desk_adjuster=3,
        coverage_review=3,
        bodily_injury_adjuster=2,
        siu_referral=2,
        duplicate_review=1,
        total_loss_review=1,
        below_deductible_notice=1,
    )


def test_ground_truth_references_resolve_against_policy_and_vehicle_files() -> None:
    policies = {row["policy_number"]: row for row in _rows("policies.csv")}
    vehicle_vins = {row["vin"] for row in _rows("policy_vehicles.csv")}

    for row in _rows("ground_truth.csv"):
        assert row["policy_number"] in policies
        assert Decimal(row["estimate_total"]) > 0
        if row["vin_status"] == "not_on_policy":
            assert row["vin_on_policy"] == ""
            assert row["vin_reported"] not in vehicle_vins
        else:
            assert row["vin_on_policy"] in vehicle_vins


def test_only_the_seeded_typo_fails_the_vin_check_digit() -> None:
    vehicles = _rows("policy_vehicles.csv")
    reported = {row["claim_id"]: row["vin_reported"] for row in _rows("ground_truth.csv")}

    assert all(_vin_check_digit_valid(row["vin"]) for row in vehicles)
    assert [cid for cid, vin in reported.items() if not _vin_check_digit_valid(vin)] == ["C09"]


def test_scanned_packets_have_no_text_layer_but_bounded_images() -> None:
    for row in _rows("ground_truth.csv"):
        content = read_bounded_pdf_content(
            ROOT / "packets" / row["packet_file"], include_images=True
        )
        if row["scan_quality"] == "degraded_image_only":
            assert content.image_data_urls
            assert row["estimate_total"] not in content.text
        else:
            assert content.image_data_urls == ()
            assert row["policy_number"] in content.text
