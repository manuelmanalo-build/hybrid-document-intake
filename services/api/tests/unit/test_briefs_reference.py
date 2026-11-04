from pathlib import Path

import pytest

from intake_api.application.briefs_reference import parse_lead_times, parse_sow_brands, parse_sows
from intake_api.application.claims_reference import ReferenceDataError
from intake_api.domain.enums import RegulatedCategory, SowStatus

ROOT = Path("test-data/briefs")
HEADER = (
    "sow_number,client,start_date,end_date,status,total_fees,committed_fees,services_in_scope,"
    "regulated_category,account_lead\n"
)


def test_fixture_reference_files_parse() -> None:
    sows = parse_sows((ROOT / "statements_of_work.csv").read_text())
    brands = parse_sow_brands((ROOT / "sow_brands.csv").read_text())
    leads = parse_lead_times((ROOT / "lead_times.csv").read_text())

    assert len(sows) == 11
    assert len(brands) == 13
    assert len(leads) == 12
    assert {sow.status for sow in sows} == {SowStatus.ACTIVE, SowStatus.EXPIRED}
    assert RegulatedCategory.PHARMA in {sow.regulated_category for sow in sows}


@pytest.mark.parametrize(
    "row",
    [
        "SOW-1,Client,2026-01-01,2025-01-01,ACTIVE,100,10,SOCIAL,NONE,Lead\n",
        "SOW-1,Client,2026-01-01,2027-01-01,ACTIVE,100,200,SOCIAL,NONE,Lead\n",
        "SOW-1,Client,2026-01-01,2027-01-01,ACTIVE,100,10,TELEPATHY,NONE,Lead\n",
        "SOW-1,Client,2026-01-01,2027-01-01,PAUSED,100,10,SOCIAL,NONE,Lead\n",
    ],
)
def test_invalid_sows_are_rejected(row: str) -> None:
    with pytest.raises(ReferenceDataError):
        parse_sows(HEADER + row)
