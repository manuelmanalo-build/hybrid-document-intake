"""Tenant-scoped agency reference data for the brief slice (ADR 0009).

Validates every row before writing, upserts by business key, never deletes, and appends one audit
event per load, mirroring the claims reference loader.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Annotated, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from intake_api.application.claims_reference import ReferenceDataError
from intake_api.domain.enums import ActorType, AuditEventType, RegulatedCategory, SowStatus
from intake_api.domain.models import AssetLeadTime, AuditEvent, SowBrand, StatementOfWork

MAX_REFERENCE_ROWS = 10_000
SERVICES = frozenset(
    {
        "SOCIAL",
        "PAID_SEARCH",
        "DISPLAY",
        "VIDEO",
        "OOH",
        "EMAIL",
        "WEB",
        "INFLUENCER",
        "AUDIO",
        "PRINT",
    }
)
Code = Annotated[str, Field(min_length=1, max_length=40, pattern=r"^[A-Z0-9_-]+$")]


class SowRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    sow_number: Code
    client_name: Annotated[str, Field(min_length=1, max_length=200)]
    start_date: date
    end_date: date
    status: SowStatus
    total_fees: Annotated[Decimal, Field(ge=0, max_digits=19, decimal_places=4)]
    committed_fees: Annotated[Decimal, Field(ge=0, max_digits=19, decimal_places=4)]
    services_in_scope: tuple[str, ...]
    regulated_category: RegulatedCategory
    account_lead: Annotated[str, Field(min_length=1, max_length=120)]

    @model_validator(mode="after")
    def validate_record(self) -> Self:
        if self.end_date <= self.start_date:
            raise ValueError("SOW end date must be after its start date")
        if self.committed_fees > self.total_fees:
            raise ValueError("Committed fees cannot exceed total fees")
        if not self.services_in_scope or not set(self.services_in_scope) <= SERVICES:
            raise ValueError("Services must be a non-empty subset of the closed service set")
        return self


class SowBrandRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    sow_number: Code
    brand: Annotated[str, Field(min_length=1, max_length=120)]


class LeadTimeRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    asset_type: Code
    label: Annotated[str, Field(min_length=1, max_length=120)]
    service: str
    min_business_days: Annotated[int, Field(ge=0, le=365)]

    @model_validator(mode="after")
    def validate_service(self) -> Self:
        if self.service not in SERVICES:
            raise ValueError("Unknown service")
        return self


@dataclass(frozen=True, slots=True)
class BriefLoadSummary:
    sows_inserted: int
    sows_updated: int
    brands_inserted: int
    lead_times_upserted: int


def parse_sows(text: str) -> list[SowRecord]:
    records: list[SowRecord] = []
    for line, row in _rows(text):
        try:
            records.append(
                SowRecord(
                    sow_number=row["sow_number"],
                    client_name=row["client"],
                    start_date=date.fromisoformat(row["start_date"]),
                    end_date=date.fromisoformat(row["end_date"]),
                    status=SowStatus(row["status"].upper()),
                    total_fees=Decimal(row["total_fees"]),
                    committed_fees=Decimal(row["committed_fees"]),
                    services_in_scope=tuple(filter(None, row["services_in_scope"].split(";"))),
                    regulated_category=RegulatedCategory(row["regulated_category"].upper()),
                    account_lead=row["account_lead"],
                )
            )
        except (KeyError, ValueError, ArithmeticError, ValidationError) as error:
            raise ReferenceDataError(f"Invalid SOW row at line {line}") from error
    if len({record.sow_number for record in records}) != len(records):
        raise ReferenceDataError("Duplicate SOW number in reference input")
    return records


def parse_sow_brands(text: str) -> list[SowBrandRecord]:
    try:
        return [
            SowBrandRecord(sow_number=row["sow_number"], brand=row["brand"])
            for _, row in _rows(text)
        ]
    except (KeyError, ValidationError) as error:
        raise ReferenceDataError("Invalid SOW brand row") from error


def parse_lead_times(text: str) -> list[LeadTimeRecord]:
    try:
        return [
            LeadTimeRecord(
                asset_type=row["asset_type"],
                label=row["label"],
                service=row["service"],
                min_business_days=int(row["min_business_days"]),
            )
            for _, row in _rows(text)
        ]
    except (KeyError, ValueError, ValidationError) as error:
        raise ReferenceDataError("Invalid lead-time row") from error


def load_brief_reference_data(
    session: Session,
    *,
    tenant_id: UUID,
    sows: list[SowRecord],
    brands: list[SowBrandRecord],
    lead_times: list[LeadTimeRecord],
    actor_id: str,
    correlation_id: str,
    source_sha256: str,
) -> BriefLoadSummary:
    known = {record.sow_number for record in sows}
    if any(record.sow_number not in known for record in brands):
        raise ReferenceDataError("A brand references a SOW that is not in the input")
    existing = {
        sow.sow_number: sow
        for sow in session.scalars(
            select(StatementOfWork).where(StatementOfWork.tenant_id == tenant_id)
        )
    }
    inserted = updated = 0
    ids: dict[str, UUID] = {}
    for record in sows:
        values = record.model_dump(exclude={"sow_number"})
        values["services_in_scope"] = list(record.services_in_scope)
        current = existing.get(record.sow_number)
        if current is None:
            current = StatementOfWork(tenant_id=tenant_id, sow_number=record.sow_number, **values)
            session.add(current)
            session.flush()
            inserted += 1
        else:
            changed = False
            for name, value in values.items():
                if getattr(current, name) != value:
                    setattr(current, name, value)
                    changed = True
            updated += changed
        ids[record.sow_number] = current.id
    present = {
        (row.sow_id, row.brand)
        for row in session.scalars(select(SowBrand).where(SowBrand.tenant_id == tenant_id))
    }
    brands_inserted = 0
    for record in brands:
        key = (ids[record.sow_number], record.brand)
        if key not in present:
            session.add(SowBrand(tenant_id=tenant_id, sow_id=key[0], brand=record.brand))
            present.add(key)
            brands_inserted += 1
    current_leads = {
        row.asset_type: row
        for row in session.scalars(
            select(AssetLeadTime).where(AssetLeadTime.tenant_id == tenant_id)
        )
    }
    for record in lead_times:
        row = current_leads.get(record.asset_type)
        if row is None:
            session.add(AssetLeadTime(tenant_id=tenant_id, **record.model_dump()))
        else:
            row.label, row.service = record.label, record.service
            row.min_business_days = record.min_business_days
    summary = BriefLoadSummary(inserted, updated, brands_inserted, len(lead_times))
    session.add(
        AuditEvent(
            tenant_id=tenant_id,
            event_type=AuditEventType.BRIEF_REFERENCE_DATA_LOADED,
            actor_type=ActorType.SYSTEM,
            actor_id=actor_id,
            correlation_id=correlation_id,
            after_values={
                "sows_inserted": summary.sows_inserted,
                "sows_updated": summary.sows_updated,
                "brands_inserted": summary.brands_inserted,
                "lead_times_upserted": summary.lead_times_upserted,
            },
            sanitized_metadata={"source_sha256": source_sha256, "sow_rows": len(sows)},
        )
    )
    session.flush()
    return summary


def _rows(text: str) -> list[tuple[int, dict[str, str]]]:
    rows = [
        (index + 2, {key: (value or "").strip() for key, value in row.items() if key})
        for index, row in enumerate(csv.DictReader(io.StringIO(text)))
    ]
    if len(rows) > MAX_REFERENCE_ROWS:
        raise ReferenceDataError("Reference file exceeds the bounded row count")
    return rows
