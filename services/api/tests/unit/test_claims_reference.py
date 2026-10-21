from decimal import Decimal
from pathlib import Path

import pytest

from intake_api.application.claims_reference import (
    ReferenceDataError,
    parse_policies,
    parse_vehicles,
)
from intake_api.domain.enums import PolicyStatus

ROOT = Path("test-data/claims")
POLICY_HEADER = (
    "policy_number,named_insured,mailing_address,phone,effective_date,expiration_date,status,"
    "cancellation_date,collision_deductible,comprehensive_deductible,liability_limits\n"
)


def _policy_row(status: str = "active", start: str = "2026-01-01", end: str = "2026-07-01") -> str:
    return f"NFM-PA-1,Test Insured,1 Main St,555-0100,{start},{end},{status},,500,250,50/100/50\n"


VEHICLE_HEADER = "policy_number,vehicle_seq,year,make,model,vin,plate,acv_valuation\n"


def test_fixture_reference_files_parse_with_typed_values() -> None:
    policies = parse_policies((ROOT / "policies.csv").read_text())
    vehicles = parse_vehicles((ROOT / "policy_vehicles.csv").read_text())

    assert len(policies) == 22
    assert len(vehicles) == 23
    cancelled = [p for p in policies if p.status == PolicyStatus.CANCELLED]
    assert len(cancelled) == 1
    assert cancelled[0].cancellation_date is not None
    assert all(isinstance(p.collision_deductible, Decimal) for p in policies)
    assert "mailing_address" not in type(policies[0]).model_fields


def test_cancelled_policy_requires_a_cancellation_date() -> None:
    row = _policy_row(status="cancelled")

    with pytest.raises(ReferenceDataError, match="line 2"):
        parse_policies(POLICY_HEADER + row)


def test_policy_term_must_be_ordered() -> None:
    row = (
        "NFM-PA-1,Test Insured,1 Main St,555-0100,2026-07-01,2026-01-01,active,,500,250,50/100/50\n"
    )

    with pytest.raises(ReferenceDataError):
        parse_policies(POLICY_HEADER + row)


def test_duplicate_policy_numbers_are_rejected() -> None:
    row = (
        "NFM-PA-1,Test Insured,1 Main St,555-0100,2026-01-01,2026-07-01,active,,500,250,50/100/50\n"
    )

    with pytest.raises(ReferenceDataError, match="Duplicate policy number"):
        parse_policies(POLICY_HEADER + row + row)


@pytest.mark.parametrize(
    "vin",
    ["1HGCM82633A00435", "1HGCM82633A0043520", "1HGCM82633AO04352", "1hgcm82633a004352"],
)
def test_vins_must_be_seventeen_valid_uppercase_characters(vin: str) -> None:
    row = f"NFM-PA-1,1,2021,Honda,Accord,{vin},ABC-1234,20000\n"

    with pytest.raises(ReferenceDataError):
        parse_vehicles(VEHICLE_HEADER + row)


def test_vehicle_values_must_be_positive() -> None:
    row = "NFM-PA-1,1,2021,Honda,Accord,1HGCM82633A004352,ABC-1234,0\n"

    with pytest.raises(ReferenceDataError):
        parse_vehicles(VEHICLE_HEADER + row)
