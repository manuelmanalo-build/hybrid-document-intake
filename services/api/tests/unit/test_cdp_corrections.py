import pytest

from intake_api.application.cdp_corrections import normalize_cdp_correction
from intake_api.application.problems import InvalidUpload
from intake_api.application.review import (
    _read_path,  # pyright: ignore[reportPrivateUsage]
    _write_path,  # pyright: ignore[reportPrivateUsage]
)


@pytest.mark.parametrize(
    ("field", "raw", "expected"),
    [
        ("mappings[0].proposed_attribute", "email_address", "email_address"),
        ("mappings[3].excluded", "TRUE", True),
        ("fields_added_in_correspondence[0].client_marked_personal", "false", False),
        ("refresh_cadence", "daily", "DAILY"),
        ("retention_days", "365", 365),
        ("requested_go_live", "2026-12-14", "2026-12-14"),
    ],
)
def test_onboarding_corrections_are_typed(field: str, raw: str, expected: object) -> None:
    assert normalize_cdp_correction(field, raw) == expected


@pytest.mark.parametrize(
    ("field", "raw"),
    [
        ("mappings[0].proposed_attribute", "Email Address"),
        ("mappings[0].source_field", "x"),
        ("mappings[x].excluded", "true"),
        ("refresh_cadence", "every so often"),
        ("retention_days", "-1"),
    ],
)
def test_invalid_onboarding_corrections_are_rejected(field: str, raw: str) -> None:
    with pytest.raises(InvalidUpload):
        normalize_cdp_correction(field, raw)


def test_list_item_paths_read_and_write_one_attribute() -> None:
    data: dict[str, object] = {"mappings": [{"source_field": "a"}, {"source_field": "b"}]}

    _write_path(data, "mappings[1].excluded", True)

    assert _read_path(data, "mappings[1].excluded") is True
    assert data["mappings"] == [{"source_field": "a"}, {"source_field": "b", "excluded": True}]
    with pytest.raises(InvalidUpload):
        _write_path(data, "mappings[5].excluded", True)
