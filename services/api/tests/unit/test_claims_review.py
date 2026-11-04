from datetime import UTC, datetime, timedelta

import pytest

from intake_api.application.claim_corrections import normalize_claim_correction
from intake_api.application.claims_review import service_level, target_minutes
from intake_api.application.problems import InvalidUpload
from intake_api.domain.enums import ReviewQueue
from intake_api.infrastructure.config import Settings

UPLOADED = datetime(2026, 10, 22, 8, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("age_minutes", "status", "remaining"),
    [(60, "ON_TRACK", 180), (185, "DUE_SOON", 55), (180, "DUE_SOON", 60), (245, "OVERDUE", -5)],
)
def test_open_claims_are_measured_against_their_target(
    age_minutes: int, status: str, remaining: int
) -> None:
    level = service_level(
        target_minutes=240,
        uploaded_at=UPLOADED,
        first_reviewed_at=None,
        now=UPLOADED + timedelta(minutes=age_minutes),
    )

    assert level.status == status
    assert level.minutes_remaining == remaining


def test_first_human_review_stops_the_clock() -> None:
    met = service_level(
        target_minutes=120,
        uploaded_at=UPLOADED,
        first_reviewed_at=UPLOADED + timedelta(minutes=90),
        now=UPLOADED + timedelta(days=3),
    )
    missed = service_level(
        target_minutes=120,
        uploaded_at=UPLOADED,
        first_reviewed_at=UPLOADED + timedelta(minutes=150),
        now=UPLOADED + timedelta(days=3),
    )

    assert (met.status, met.minutes_remaining) == ("MET", 30)
    assert (missed.status, missed.minutes_remaining) == ("MISSED", -30)


def test_targets_come_from_configuration_per_queue() -> None:
    settings = Settings(claims_sla_bodily_injury_hours=1, claims_sla_standard_hours=6)

    assert target_minutes(settings, ReviewQueue.FAST_TRACK) == 240
    assert target_minutes(settings, ReviewQueue.BODILY_INJURY_ADJUSTER) == 60
    assert target_minutes(settings, ReviewQueue.SIU_REFERRAL) == 360
    assert target_minutes(settings, ReviewQueue.BELOW_DEDUCTIBLE_NOTICE) == 1440


@pytest.mark.parametrize(
    ("field", "raw", "expected"),
    [
        ("estimate_total", " 2415.90 ", "2415.90"),
        ("loss_date", "2026-09-22", "2026-09-22"),
        ("injury_mentioned_in_correspondence", "TRUE", True),
        ("coverage", "comprehensive", "COMPREHENSIVE"),
        ("vehicle_year", "2020", 2020),
        ("vin_reported", "4t1l4e116lh848018", "4T1L4E116LH848018"),
        ("named_insured", "Danielle Okafor", "Danielle Okafor"),
    ],
)
def test_claim_corrections_are_typed(field: str, raw: str, expected: object) -> None:
    assert normalize_claim_correction(field, raw) == expected


@pytest.mark.parametrize(
    ("field", "raw"),
    [
        ("estimate_total", "-1"),
        ("loss_date", "09/22/2026"),
        ("third_party_involved", "maybe"),
        ("coverage", "LIABILITY"),
        ("vehicle_year", "20"),
        ("vin_reported", "4T1-L4E"),
        ("supplier_name", "Not a claim field"),
    ],
)
def test_invalid_claim_corrections_are_rejected(field: str, raw: str) -> None:
    with pytest.raises(InvalidUpload):
        normalize_claim_correction(field, raw)
