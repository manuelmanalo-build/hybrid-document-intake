"""Tenant-scoped policy and vehicle reference data for the claims slice (ADR 0008).

The loader validates every row before writing anything, upserts idempotently by business key,
never deletes reference rows, and appends one audit event per load. Only fields the claim rules
need are stored; contact details in the source files are deliberately dropped.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Annotated, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from intake_api.domain.enums import ActorType, AuditEventType, PolicyStatus
from intake_api.domain.models import AuditEvent, Policy, PolicyVehicle

MAX_REFERENCE_FILE_BYTES = 5 * 1024 * 1024
MAX_REFERENCE_ROWS = 10_000

PolicyNumber = Annotated[str, Field(min_length=1, max_length=40, pattern=r"^[A-Z0-9-]+$")]
Money = Annotated[Decimal, Field(ge=0, max_digits=19, decimal_places=4)]


class ReferenceDataError(ValueError):
    """Raised when reference input is malformed; nothing has been written."""


class PolicyRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    policy_number: PolicyNumber
    named_insured: Annotated[str, Field(min_length=1, max_length=200)]
    effective_date: date
    expiration_date: date
    status: PolicyStatus
    cancellation_date: date | None
    collision_deductible: Money
    comprehensive_deductible: Money
    liability_limits: Annotated[str, Field(min_length=1, max_length=32)]

    @model_validator(mode="after")
    def validate_term_and_cancellation(self) -> Self:
        if self.expiration_date <= self.effective_date:
            raise ValueError("Policy expiration must be after its effective date")
        if (self.status == PolicyStatus.CANCELLED) != (self.cancellation_date is not None):
            raise ValueError("A cancellation date is required exactly when status is CANCELLED")
        return self


class VehicleRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    policy_number: PolicyNumber
    vehicle_seq: Annotated[int, Field(ge=1, le=20)]
    model_year: Annotated[int, Field(ge=1980, le=2100)]
    make: Annotated[str, Field(min_length=1, max_length=64)]
    model: Annotated[str, Field(min_length=1, max_length=100)]
    vin: Annotated[str, Field(pattern=r"^[A-HJ-NPR-Z0-9]{17}$")]
    plate: Annotated[str, Field(min_length=1, max_length=20)]
    actual_cash_value: Annotated[Decimal, Field(gt=0, max_digits=19, decimal_places=4)]


@dataclass(frozen=True, slots=True)
class LoadSummary:
    policies_inserted: int
    policies_updated: int
    policies_unchanged: int
    vehicles_inserted: int
    vehicles_updated: int
    vehicles_unchanged: int


def parse_policies(text: str) -> list[PolicyRecord]:
    records: list[PolicyRecord] = []
    for line_number, row in _bounded_rows(text):
        try:
            records.append(
                PolicyRecord(
                    policy_number=row["policy_number"],
                    named_insured=row["named_insured"],
                    effective_date=date.fromisoformat(row["effective_date"]),
                    expiration_date=date.fromisoformat(row["expiration_date"]),
                    status=PolicyStatus(row["status"].upper()),
                    cancellation_date=(
                        date.fromisoformat(row["cancellation_date"])
                        if row["cancellation_date"]
                        else None
                    ),
                    collision_deductible=Decimal(row["collision_deductible"]),
                    comprehensive_deductible=Decimal(row["comprehensive_deductible"]),
                    liability_limits=row["liability_limits"],
                )
            )
        except (KeyError, ValueError, ArithmeticError, ValidationError) as error:
            raise ReferenceDataError(f"Invalid policy row at line {line_number}") from error
    _require_unique((record.policy_number for record in records), "policy number")
    return records


def parse_vehicles(text: str) -> list[VehicleRecord]:
    records: list[VehicleRecord] = []
    for line_number, row in _bounded_rows(text):
        try:
            records.append(
                VehicleRecord(
                    policy_number=row["policy_number"],
                    vehicle_seq=int(row["vehicle_seq"]),
                    model_year=int(row["year"]),
                    make=row["make"],
                    model=row["model"],
                    vin=row["vin"],
                    plate=row["plate"],
                    actual_cash_value=Decimal(row["acv_valuation"]),
                )
            )
        except (KeyError, ValueError, ArithmeticError, ValidationError) as error:
            raise ReferenceDataError(f"Invalid vehicle row at line {line_number}") from error
    _require_unique(
        (f"{record.policy_number}:{record.vin}" for record in records), "policy vehicle VIN"
    )
    _require_unique(
        (f"{record.policy_number}:{record.vehicle_seq}" for record in records),
        "policy vehicle sequence",
    )
    return records


def load_claims_reference_data(
    session: Session,
    *,
    tenant_id: UUID,
    policies: list[PolicyRecord],
    vehicles: list[VehicleRecord],
    actor_id: str,
    correlation_id: str,
    source_sha256: str,
) -> LoadSummary:
    """Upsert tenant reference data inside the caller's transaction and append one audit event."""
    known = {record.policy_number for record in policies}
    orphaned = sorted({v.policy_number for v in vehicles if v.policy_number not in known})
    if orphaned:
        raise ReferenceDataError(f"{len(orphaned)} vehicle policy numbers are not in the policies")

    existing_policies = {
        policy.policy_number: policy
        for policy in session.scalars(
            select(Policy).where(
                Policy.tenant_id == tenant_id,
                Policy.policy_number.in_(sorted(known)),
            )
        )
    }
    inserted = updated = unchanged = 0
    policy_ids: dict[str, UUID] = {}
    for record in policies:
        values = record.model_dump(exclude={"policy_number"})
        current = existing_policies.get(record.policy_number)
        if current is None:
            current = Policy(tenant_id=tenant_id, policy_number=record.policy_number, **values)
            session.add(current)
            session.flush()
            inserted += 1
        elif _apply_changes(current, values):
            updated += 1
        else:
            unchanged += 1
        policy_ids[record.policy_number] = current.id

    existing_vehicles = {
        (vehicle.policy_id, vehicle.vin): vehicle
        for vehicle in session.scalars(
            select(PolicyVehicle).where(
                PolicyVehicle.tenant_id == tenant_id,
                PolicyVehicle.policy_id.in_(list(policy_ids.values())),
            )
        )
    }
    v_inserted = v_updated = v_unchanged = 0
    for record in vehicles:
        policy_id = policy_ids[record.policy_number]
        values = record.model_dump(exclude={"policy_number", "vin"})
        vehicle = existing_vehicles.get((policy_id, record.vin))
        if vehicle is None:
            session.add(
                PolicyVehicle(tenant_id=tenant_id, policy_id=policy_id, vin=record.vin, **values)
            )
            v_inserted += 1
        elif _apply_changes(vehicle, values):
            v_updated += 1
        else:
            v_unchanged += 1

    summary = LoadSummary(inserted, updated, unchanged, v_inserted, v_updated, v_unchanged)
    session.add(
        AuditEvent(
            tenant_id=tenant_id,
            event_type=AuditEventType.CLAIMS_REFERENCE_DATA_LOADED,
            actor_type=ActorType.SYSTEM,
            actor_id=actor_id,
            correlation_id=correlation_id,
            after_values={
                "policies_inserted": summary.policies_inserted,
                "policies_updated": summary.policies_updated,
                "policies_unchanged": summary.policies_unchanged,
                "vehicles_inserted": summary.vehicles_inserted,
                "vehicles_updated": summary.vehicles_updated,
                "vehicles_unchanged": summary.vehicles_unchanged,
            },
            sanitized_metadata={
                "source_sha256": source_sha256,
                "policy_rows": len(policies),
                "vehicle_rows": len(vehicles),
            },
        )
    )
    session.flush()
    return summary


def _bounded_rows(text: str) -> list[tuple[int, dict[str, str]]]:
    if len(text.encode()) > MAX_REFERENCE_FILE_BYTES:
        raise ReferenceDataError("Reference file exceeds the bounded size")
    rows = [
        (index + 2, {key: (value or "").strip() for key, value in row.items() if key})
        for index, row in enumerate(csv.DictReader(io.StringIO(text)))
    ]
    if len(rows) > MAX_REFERENCE_ROWS:
        raise ReferenceDataError("Reference file exceeds the bounded row count")
    return rows


def _require_unique(keys: Iterable[str], label: str) -> None:
    seen: set[str] = set()
    for key in keys:
        if key in seen:
            raise ReferenceDataError(f"Duplicate {label} in reference input")
        seen.add(key)


def _apply_changes(target: object, values: dict[str, object]) -> bool:
    changed = False
    for name, value in values.items():
        if getattr(target, name) != value:
            setattr(target, name, value)
            changed = True
    return changed
