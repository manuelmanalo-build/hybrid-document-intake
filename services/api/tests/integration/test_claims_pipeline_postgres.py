"""End-to-end claim packets through the durable graph with the stub provider (ADR 0008)."""

import os
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.engine import make_url

from intake_api.application.claims_reference import (
    load_claims_reference_data,
    parse_policies,
    parse_vehicles,
)
from intake_api.application.identity import configured_actor
from intake_api.application.review import CompleteReviewCommand, ReviewService
from intake_api.domain.enums import ClaimKeyStatus, GraphRunStatus, ReviewQueue, UserRole
from intake_api.domain.models import (
    AgentToolCall,
    ClaimIntakeKey,
    ClaimRoutingDecision,
    ConfidenceRun,
    Document,
    DocumentPageSegment,
    ExtractionCandidate,
    GraphRun,
    ReviewTask,
    Tenant,
    User,
)
from intake_api.infrastructure.config import Settings
from intake_api.main import create_app
from intake_orchestrator.dispatcher import GraphResumeDispatcher, GraphRunDispatcher
from intake_orchestrator.graph import ConfiguredResolutionAgent, open_graph_executor
from intake_orchestrator.repository import (
    GraphResumeRepository,
    GraphRunRepository,
    build_async_engine,
    build_async_session_factory,
)
from intake_orchestrator.workflow import PostgresWorkflowBackend

pytestmark = pytest.mark.integration
CLAIMS = Path("test-data/claims")


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


@pytest.fixture(scope="module", autouse=True)
def migrated_database(database_url: str) -> Iterator[None]:
    config = Config("services/api/alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    yield
    command.downgrade(config, "base")


@pytest.fixture
def api(database_url: str, tmp_path: Path) -> Iterator[tuple[TestClient, object]]:
    settings = Settings(
        database_url=database_url,
        storage_root=tmp_path,
        demo_submitter_token="integration-token",
        upload_max_bytes=500_000,
    )
    app = create_app(settings)
    with app.state.session_factory() as session, session.begin():
        session.add(Tenant(id=settings.bootstrap_tenant_id, name="Claims Integration Tenant"))
        session.add(
            User(
                id=settings.bootstrap_user_id,
                tenant_id=settings.bootstrap_tenant_id,
                external_subject=settings.bootstrap_user_subject,
                display_name="Integration Submitter",
                role=UserRole.SUBMITTER,
            )
        )
        session.flush()
        load_claims_reference_data(
            session,
            tenant_id=settings.bootstrap_tenant_id,
            policies=parse_policies((CLAIMS / "policies.csv").read_text()),
            vehicles=parse_vehicles((CLAIMS / "policy_vehicles.csv").read_text()),
            actor_id="integration-test",
            correlation_id="claims-pipeline-reference",
            source_sha256="0" * 64,
        )
    with TestClient(app) as test_client:
        yield test_client, app
    with app.state.session_factory() as session, session.begin():
        session.execute(
            text(
                "TRUNCATE audit_events, document_status_history, graph_resume_requests, "
                "graph_run_requests, graph_runs, idempotency_records, document_versions, "
                "documents, stored_objects, policies, users, tenants CASCADE"
            )
        )


def _upload(client: TestClient, packet: str, key: str) -> UUID:
    payload = (CLAIMS / "packets" / packet).read_bytes()
    response = client.post(
        "/api/v1/documents",
        headers={"Authorization": "Bearer integration-token", "Idempotency-Key": key},
        files={"file": (packet, payload, "application/pdf")},
    )
    assert response.status_code == 201, response.text
    return UUID(response.json()["document_id"])


async def _dispatch_runs(settings: Settings, count: int) -> None:
    engine = build_async_engine(settings)
    sessions = build_async_session_factory(engine)
    try:
        async with open_graph_executor(
            settings,
            initialize_checkpoint_schema=True,
            workflow=PostgresWorkflowBackend(sessions),
            resolution_agent=ConfiguredResolutionAgent(settings=settings, session_factory=sessions),
        ) as executor:
            dispatcher = GraphRunDispatcher(
                repository=GraphRunRepository(sessions),
                executor=executor,
                settings=settings,
                owner="claims-integration",
            )
            for _ in range(count):
                assert await dispatcher.dispatch_one(_now()) is True
    finally:
        await engine.dispose()


async def _dispatch_resume(settings: Settings) -> None:
    engine = build_async_engine(settings)
    sessions = build_async_session_factory(engine)
    try:
        async with open_graph_executor(
            settings,
            initialize_checkpoint_schema=False,
            workflow=PostgresWorkflowBackend(sessions),
            resolution_agent=ConfiguredResolutionAgent(settings=settings, session_factory=sessions),
        ) as executor:
            dispatcher = GraphResumeDispatcher(
                repository=GraphResumeRepository(sessions),
                executor=executor,
                settings=settings,
                owner="claims-resumer",
            )
            assert await dispatcher.dispatch_one(_now()) is True
    finally:
        await engine.dispose()


def _graph_settings(app: object, database_url: str) -> Settings:
    return Settings(
        database_url=database_url,
        checkpoint_database_url=database_url,
        storage_root=app.state.settings.storage_root,  # type: ignore[attr-defined]
    )


def _queue_for(session: object, document_id: UUID) -> ReviewQueue:
    decision = session.scalar(  # type: ignore[attr-defined]
        select(ClaimRoutingDecision)
        .where(ClaimRoutingDecision.document_id == document_id)
        .order_by(ClaimRoutingDecision.created_at.desc())
    )
    assert decision is not None
    return decision.review_queue


@pytest.mark.asyncio
async def test_claim_packet_is_segmented_extracted_routed_and_reviewed(
    api: tuple[TestClient, object], database_url: str
) -> None:
    client, app = api
    first = _upload(client, "C01_packet.pdf", "claims-c01")
    second = _upload(client, "C02_packet.pdf", "claims-c02")
    settings = _graph_settings(app, database_url)

    await _dispatch_runs(settings, 2)

    with app.state.session_factory() as session:  # type: ignore[attr-defined]
        candidate = session.scalar(
            select(ExtractionCandidate).where(ExtractionCandidate.document_id == first)
        )
        assert candidate is not None
        assert candidate.document_type == "CLAIM_PACKET"
        labels = list(
            session.scalars(
                select(DocumentPageSegment.segment_label)
                .where(DocumentPageSegment.document_id == first)
                .order_by(DocumentPageSegment.page_number)
            )
        )
        assert [label.value for label in labels] == ["LOSS_NOTICE", "REPAIR_ESTIMATE"]
        tools = set(
            session.scalars(
                select(AgentToolCall.tool_name).where(AgentToolCall.document_id == first)
            )
        )
        assert {"segment_claim_packet", "extract_claim_from_text"} <= tools
        assert not any(name.startswith("extract_invoice") for name in tools)
        # The stub extractor mirrors fixture C01, so C01 is clean and fast-tracked, and the
        # second packet repeats C01's policy, VIN, and loss date and is caught as a duplicate.
        assert _queue_for(session, first) == ReviewQueue.FAST_TRACK
        assert _queue_for(session, second) == ReviewQueue.DUPLICATE_REVIEW
        confidence = session.scalar(
            select(ConfidenceRun)
            .join(GraphRun, GraphRun.id == ConfidenceRun.graph_run_id)
            .where(GraphRun.document_id == first)
        )
        assert confidence is not None
        assert confidence.algorithm_version == "decision-confidence-claims-v1"
        assert (
            session.scalar(select(ReviewTask.status).where(ReviewTask.document_id == first))
            == "OPEN"
        )

    approver = configured_actor(app.state.settings, UserRole.APPROVER)  # type: ignore[attr-defined]
    with app.state.session_factory() as session, session.begin():  # type: ignore[attr-defined]
        session.add(
            User(
                id=approver.user_id,
                tenant_id=approver.tenant_id,
                external_subject=approver.subject,
                display_name=approver.subject,
                role=approver.role,
            )
        )
    reviews = ReviewService(app.state.settings, app.state.session_factory)  # type: ignore[attr-defined]
    duplicate_task = next(
        task for task in reviews.list_tasks(approver) if task.document_id == second
    )
    reviews.claim(duplicate_task.id, approver)
    reviews.complete(
        duplicate_task.id,
        CompleteReviewCommand(decision="REJECT", reason="Duplicate of an open claim."),
        approver,
    )
    await _dispatch_resume(settings)

    with app.state.session_factory() as session:  # type: ignore[attr-defined]
        rejected_key = session.scalar(
            select(ClaimIntakeKey).where(ClaimIntakeKey.document_id == second)
        )
        kept_key = session.scalar(select(ClaimIntakeKey).where(ClaimIntakeKey.document_id == first))
        assert rejected_key is not None
        assert rejected_key.status == ClaimKeyStatus.REJECTED
        assert kept_key is not None
        assert kept_key.status == ClaimKeyStatus.ACTIVE
        run = session.scalar(
            select(GraphRun)
            .join(Document, Document.id == GraphRun.document_id)
            .where(Document.id == second)
        )
        assert run is not None
        assert run.status == GraphRunStatus.COMPLETED


def _now() -> datetime:
    return datetime.now(UTC)
