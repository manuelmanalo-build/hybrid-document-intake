import os
from collections.abc import Iterator
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import InstrumentedAttribute, Session, sessionmaker

from intake_api.application.claims_reference import (
    LoadSummary,
    ReferenceDataError,
    VehicleRecord,
    load_claims_reference_data,
    parse_policies,
    parse_vehicles,
)
from intake_api.domain.enums import AuditEventType
from intake_api.domain.models import AuditEvent, Policy, PolicyVehicle, Tenant

pytestmark = pytest.mark.integration
ROOT = Path("test-data/claims")


@pytest.fixture(scope="module")
def database_url() -> str:
    value = os.environ.get("TEST_DATABASE_URL")
    if value is None:
        pytest.skip("TEST_DATABASE_URL is required for the documented PostgreSQL integration suite")
    if "test" not in (make_url(value).database or "").lower():
        pytest.fail(
            "TEST_DATABASE_URL must identify a disposable database whose name contains 'test'"
        )
    return value


@pytest.fixture(scope="module")
def engine(database_url: str) -> Iterator[Engine]:
    config = Config("services/api/alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    built = create_engine(database_url)
    yield built
    built.dispose()
    command.downgrade(config, "base")


@pytest.fixture
def sessions(engine: Engine) -> Iterator[sessionmaker[Session]]:
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    with factory() as session, session.begin():
        session.execute(text("TRUNCATE policy_vehicles, policies, audit_events, tenants CASCADE"))


def _tenant(factory: sessionmaker[Session], name: str) -> UUID:
    tenant_id = uuid4()
    with factory() as session, session.begin():
        session.add(Tenant(id=tenant_id, name=name))
    return tenant_id


def _load(
    factory: sessionmaker[Session],
    tenant_id: UUID,
    vehicles: list[VehicleRecord] | None = None,
) -> LoadSummary:
    with factory() as session, session.begin():
        return load_claims_reference_data(
            session,
            tenant_id=tenant_id,
            policies=parse_policies((ROOT / "policies.csv").read_text()),
            vehicles=vehicles
            if vehicles is not None
            else parse_vehicles((ROOT / "policy_vehicles.csv").read_text()),
            actor_id="integration-test",
            correlation_id="integration-claims-reference",
            source_sha256="0" * 64,
        )


def _count(
    factory: sessionmaker[Session], column: InstrumentedAttribute[UUID], tenant_id: UUID
) -> int:
    with factory() as session:
        return session.scalar(select(func.count()).where(column == tenant_id)) or 0


def test_load_is_idempotent_audited_and_tenant_scoped(sessions: sessionmaker[Session]) -> None:
    tenant_a = _tenant(sessions, "Tenant A")
    tenant_b = _tenant(sessions, "Tenant B")

    first = _load(sessions, tenant_a)
    second = _load(sessions, tenant_a)

    assert first.policies_inserted == 22
    assert first.vehicles_inserted == 23
    assert second.policies_unchanged == 22
    assert second.vehicles_unchanged == 23
    assert _count(sessions, Policy.tenant_id, tenant_a) == 22
    assert _count(sessions, PolicyVehicle.tenant_id, tenant_a) == 23
    assert _count(sessions, Policy.tenant_id, tenant_b) == 0
    with sessions() as session:
        events = session.scalars(
            select(AuditEvent).where(
                AuditEvent.tenant_id == tenant_a,
                AuditEvent.event_type == AuditEventType.CLAIMS_REFERENCE_DATA_LOADED,
            )
        ).all()
    assert len(events) == 2
    assert events[0].sanitized_metadata["policy_rows"] == 22


def test_changed_values_are_updated_not_duplicated(sessions: sessionmaker[Session]) -> None:
    tenant_id = _tenant(sessions, "Tenant C")
    _load(sessions, tenant_id)
    vehicles = parse_vehicles((ROOT / "policy_vehicles.csv").read_text())
    vehicles[0] = vehicles[0].model_copy(update={"plate": "NEW-0001"})

    summary = _load(sessions, tenant_id, vehicles)

    assert summary.vehicles_updated == 1
    assert _count(sessions, PolicyVehicle.tenant_id, tenant_id) == 23


def test_orphan_vehicle_rejects_the_whole_load(sessions: sessionmaker[Session]) -> None:
    tenant_id = _tenant(sessions, "Tenant D")
    vehicles = parse_vehicles((ROOT / "policy_vehicles.csv").read_text())
    vehicles.append(vehicles[0].model_copy(update={"policy_number": "NFM-PA-0000000"}))

    with pytest.raises(ReferenceDataError):
        _load(sessions, tenant_id, vehicles)

    assert _count(sessions, Policy.tenant_id, tenant_id) == 0
    assert _count(sessions, PolicyVehicle.tenant_id, tenant_id) == 0
