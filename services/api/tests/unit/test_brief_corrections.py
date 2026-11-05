import pytest

from intake_api.application.brief_corrections import normalize_brief_correction
from intake_api.application.document_types import CORRECTION_NORMALIZERS, REVIEW_DISPLAYS
from intake_api.application.problems import InvalidUpload
from intake_api.domain.enums import ReviewQueue
from intake_api.infrastructure.config import Settings


@pytest.mark.parametrize(
    ("field", "raw", "expected"),
    [
        ("budget_total", " 85000 ", "85000"),
        ("launch_date", "2026-12-01", "2026-12-01"),
        ("campaign_name", "Winter Warmers", "Winter Warmers"),
    ],
)
def test_brief_corrections_are_typed(field: str, raw: str, expected: object) -> None:
    assert normalize_brief_correction(field, raw) == expected


@pytest.mark.parametrize(
    ("field", "raw"),
    [("budget_total", "-5"), ("launch_date", "12/01/2026"), ("deliverables", "[]"), ("vin", "X")],
)
def test_invalid_brief_corrections_are_rejected(field: str, raw: str) -> None:
    with pytest.raises(InvalidUpload):
        normalize_brief_correction(field, raw)


def test_each_reviewable_type_has_corrections_queues_and_service_levels() -> None:
    settings = Settings()

    assert set(REVIEW_DISPLAYS) == set(CORRECTION_NORMALIZERS)
    briefs = REVIEW_DISPLAYS["CAMPAIGN_BRIEF"]
    assert ReviewQueue.FAST_TRACK not in briefs.queues
    assert briefs.target_hours(settings, ReviewQueue.RUSH_REVIEW) == 2
    assert briefs.target_hours(settings, ReviewQueue.SCOPE_REVIEW) == 8
    assert REVIEW_DISPLAYS["CLAIM_PACKET"].target_hours(settings, ReviewQueue.FAST_TRACK) == 4
