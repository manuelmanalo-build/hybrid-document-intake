"""Tenant-scoped CDP-onboarding reference data (ADR 0010): agreements, canonical schema, connectors.

Validates every row before writing, upserts by business key, never deletes, and appends one audit
event per load.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from intake_api.application.claims_reference import ReferenceDataError
from intake_api.domain.enums import ActorType, AuditEventType, SowStatus
from intake_api.domain.models import AuditEvent, CanonicalAttribute, CdpConnector, DataAgreement

REGIONS = frozenset({"US", "EU", "UK", "CA", "APAC"})
BASES = frozenset({"CONSENT", "CONTRACT", "LEGITIMATE_INTEREST"})
CADENCES = frozenset({"REALTIME", "HOURLY", "DAILY", "WEEKLY", "ONE_TIME"})
DATA_TYPES = frozenset({"STRING", "EMAIL", "PHONE", "NUMBER", "DATE", "TIMESTAMP", "BOOLEAN"})
Code = Annotated[str, Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")]


class AgreementRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    agreement_number: Code
    client_name: Annotated[str, Field(min_length=1, max_length=200)]
    start_date: date
    end_date: date
    status: SowStatus
    regions: tuple[str, ...]
    lawful_bases: tuple[str, ...]
    sensitive_categories: tuple[str, ...]
    max_retention_days: Annotated[int, Field(ge=1, le=36_500)]

    @model_validator(mode="after")
    def validate_record(self) -> Self:
        if self.end_date <= self.start_date:
            raise ValueError("Agreement end must be after its start")
        if not self.regions or not set(self.regions) <= REGIONS:
            raise ValueError("Unknown data region")
        if not set(self.lawful_bases) <= BASES:
            raise ValueError("Unknown lawful basis")
        return self


class AttributeRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    attribute: Code
    entity: Literal["PROFILE", "EVENT"]
    data_type: str
    personal_class: Literal["NONE", "DIRECT_IDENTIFIER", "QUASI_IDENTIFIER", "SENSITIVE"]
    identity_key: bool

    @model_validator(mode="after")
    def validate_type(self) -> Self:
        if self.data_type not in DATA_TYPES:
            raise ValueError("Unknown data type")
        return self


class ConnectorRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    connector_type: Code
    label: Annotated[str, Field(min_length=1, max_length=120)]
    cadences: tuple[str, ...]
    min_business_days: Annotated[int, Field(ge=0, le=365)]

    @model_validator(mode="after")
    def validate_cadences(self) -> Self:
        if not self.cadences or not set(self.cadences) <= CADENCES:
            raise ValueError("Unknown cadence")
        return self


@dataclass(frozen=True, slots=True)
class CdpLoadSummary:
    agreements_upserted: int
    attributes_upserted: int
    connectors_upserted: int


def _rows(text: str) -> list[dict[str, str]]:
    return [
        {key: (value or "").strip() for key, value in row.items() if key}
        for row in csv.DictReader(io.StringIO(text))
    ]


def _split(value: str) -> tuple[str, ...]:
    return tuple(filter(None, value.split(";")))


def parse_agreements(text: str) -> list[AgreementRecord]:
    try:
        records = [
            AgreementRecord(
                agreement_number=row["agreement_number"],
                client_name=row["client"],
                start_date=date.fromisoformat(row["start_date"]),
                end_date=date.fromisoformat(row["end_date"]),
                status=SowStatus(row["status"].upper()),
                regions=_split(row["regions"]),
                lawful_bases=_split(row["lawful_bases"]),
                sensitive_categories=_split(row["sensitive_categories"]),
                max_retention_days=int(row["max_retention_days"]),
            )
            for row in _rows(text)
        ]
    except (KeyError, ValueError, ValidationError) as error:
        raise ReferenceDataError("Invalid data agreement row") from error
    if len({record.agreement_number for record in records}) != len(records):
        raise ReferenceDataError("Duplicate agreement number in reference input")
    return records


def parse_attributes(text: str) -> list[AttributeRecord]:
    try:
        return [
            AttributeRecord(
                attribute=row["attribute"],
                entity=row["entity"],  # type: ignore[arg-type]
                data_type=row["data_type"],
                personal_class=row["personal_class"],  # type: ignore[arg-type]
                identity_key=row["identity_key"].casefold() == "true",
            )
            for row in _rows(text)
        ]
    except (KeyError, ValueError, ValidationError) as error:
        raise ReferenceDataError("Invalid canonical attribute row") from error


def parse_connectors(text: str) -> list[ConnectorRecord]:
    try:
        return [
            ConnectorRecord(
                connector_type=row["connector_type"],
                label=row["label"],
                cadences=_split(row["cadences"]),
                min_business_days=int(row["min_business_days"]),
            )
            for row in _rows(text)
        ]
    except (KeyError, ValueError, ValidationError) as error:
        raise ReferenceDataError("Invalid connector row") from error


def load_cdp_reference_data(
    session: Session,
    *,
    tenant_id: UUID,
    agreements: list[AgreementRecord],
    attributes: list[AttributeRecord],
    connectors: list[ConnectorRecord],
    actor_id: str,
    correlation_id: str,
    source_sha256: str,
) -> CdpLoadSummary:
    current_agreements = {
        row.agreement_number: row
        for row in session.scalars(
            select(DataAgreement).where(DataAgreement.tenant_id == tenant_id)
        )
    }
    for record in agreements:
        values = record.model_dump(exclude={"agreement_number"})
        for name in ("regions", "lawful_bases", "sensitive_categories"):
            values[name] = list(values[name])
        _upsert(
            session,
            current_agreements.get(record.agreement_number),
            DataAgreement,
            tenant_id,
            {"agreement_number": record.agreement_number, **values},
        )
    current_attributes = {
        row.attribute: row
        for row in session.scalars(
            select(CanonicalAttribute).where(CanonicalAttribute.tenant_id == tenant_id)
        )
    }
    for record in attributes:
        _upsert(
            session,
            current_attributes.get(record.attribute),
            CanonicalAttribute,
            tenant_id,
            record.model_dump(),
        )
    current_connectors = {
        row.connector_type: row
        for row in session.scalars(select(CdpConnector).where(CdpConnector.tenant_id == tenant_id))
    }
    for record in connectors:
        values = record.model_dump()
        values["cadences"] = list(record.cadences)
        _upsert(
            session, current_connectors.get(record.connector_type), CdpConnector, tenant_id, values
        )
    summary = CdpLoadSummary(len(agreements), len(attributes), len(connectors))
    session.add(
        AuditEvent(
            tenant_id=tenant_id,
            event_type=AuditEventType.CDP_REFERENCE_DATA_LOADED,
            actor_type=ActorType.SYSTEM,
            actor_id=actor_id,
            correlation_id=correlation_id,
            after_values={
                "agreements_upserted": summary.agreements_upserted,
                "attributes_upserted": summary.attributes_upserted,
                "connectors_upserted": summary.connectors_upserted,
            },
            sanitized_metadata={"source_sha256": source_sha256},
        )
    )
    session.flush()
    return summary


def _upsert(
    session: Session,
    current: object | None,
    model: type[DataAgreement] | type[CanonicalAttribute] | type[CdpConnector],
    tenant_id: UUID,
    values: dict[str, object],
) -> None:
    if current is None:
        session.add(model(tenant_id=tenant_id, **values))
        return
    for name, value in values.items():
        if getattr(current, name) != value:
            setattr(current, name, value)
