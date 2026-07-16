import asyncio
import hashlib
import os
from collections.abc import Iterator
from datetime import timedelta
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from alembic import command
from alembic.config import Config
from fastapi import UploadFile
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url
from starlette.datastructures import Headers

from intake_api.application.identity import RequestIdentity, configured_actor
from intake_api.application.integrations import IntegrationAdminService, ZapierCallback
from intake_api.application.problems import ResourceNotFound, ReviewConflict
from intake_api.application.review import (
    CompleteReviewCommand,
    FieldCorrectionCommand,
    ReviewService,
)
from intake_api.domain.enums import (
    AuditEventType,
    DispatchRequestStatus,
    GraphRunStatus,
    UserRole,
)
from intake_api.domain.models import (
    AgentRecommendation,
    AgentRun,
    AgentToolCall,
    ApprovedBusinessRecord,
    AuditEvent,
    ConfidenceRun,
    DeadLetterEvent,
    Document,
    DocumentStatusHistory,
    DocumentVersion,
    ExtractionCandidate,
    ExtractionFieldEvidence,
    ExtractionRun,
    GraphResumeRequest,
    GraphRun,
    GraphRunRequest,
    IdempotencyRecord,
    OutboxEvent,
    PromptVersion,
    ReviewedDocumentVersion,
    ReviewTask,
    StoredObject,
    Tenant,
    User,
    ValidationRun,
    WebhookDelivery,
)
from intake_api.infrastructure.config import Settings
from intake_api.main import create_app
from intake_integration_worker.worker import (
    HttpxZapierSender,
    IntegrationWorker,
    OutboxRepository,
)
from intake_orchestrator.dispatcher import GraphResumeDispatcher, GraphRunDispatcher
from intake_orchestrator.graph import ConfiguredResolutionAgent, GraphRunInput, open_graph_executor
from intake_orchestrator.repository import (
    GraphResumeRepository,
    GraphRunRepository,
    build_async_engine,
    build_async_session_factory,
)
from intake_orchestrator.workflow import PostgresWorkflowBackend
from mock_zapier.main import app as mock_zapier_app

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def database_url() -> str:
    value = os.environ.get("TEST_DATABASE_URL")
    if value is None:
        pytest.skip("TEST_DATABASE_URL is required for the documented PostgreSQL integration suite")
    database = make_url(value).database or ""
    if "test" not in database.lower():
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
        upload_max_bytes=1024,
    )
    app = create_app(settings)
    with app.state.session_factory() as session, session.begin():
        session.add(Tenant(id=settings.bootstrap_tenant_id, name="Integration Tenant"))
        session.add(
            User(
                id=settings.bootstrap_user_id,
                tenant_id=settings.bootstrap_tenant_id,
                external_subject=settings.bootstrap_user_subject,
                display_name="Integration Submitter",
                role=UserRole.SUBMITTER,
            )
        )
    with TestClient(app) as test_client:
        yield test_client, app
    with app.state.session_factory() as session, session.begin():
        session.execute(
            text(
                "TRUNCATE audit_events, document_status_history, graph_resume_requests, "
                "graph_run_requests, graph_runs, idempotency_records, document_versions, "
                "documents, stored_objects, users, tenants CASCADE"
            )
        )


def test_upload_commits_authoritative_boundary_and_replays(api: tuple[TestClient, object]) -> None:
    test_client, app = api
    payload = Path("test-data/minimal.pdf").read_bytes()
    headers = {
        "Authorization": "Bearer integration-token",
        "Idempotency-Key": "invoice-001",
        "X-Correlation-ID": "integration-001",
    }

    first = test_client.post(
        "/api/v1/documents",
        headers=headers,
        files={"file": ("invoice.txt", payload, "application/pdf")},
    )
    replay = test_client.post(
        "/api/v1/documents",
        headers=headers,
        files={"file": ("invoice.txt", payload, "application/pdf")},
    )

    assert first.status_code == 201
    assert replay.status_code == 201
    assert replay.headers["Idempotent-Replayed"] == "true"
    assert replay.json() == first.json()
    assert "object" not in first.text.lower()

    with app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Document)) == 1
        assert session.scalar(select(func.count()).select_from(DocumentVersion)) == 1
        assert session.scalar(select(func.count()).select_from(StoredObject)) == 1
        assert session.scalar(select(func.count()).select_from(DocumentStatusHistory)) == 1
        assert session.scalar(select(func.count()).select_from(IdempotencyRecord)) == 1
        assert session.scalar(select(func.count()).select_from(GraphRun)) == 1
        assert session.scalar(select(func.count()).select_from(GraphRunRequest)) == 1
        run = session.scalar(select(GraphRun))
        request = session.scalar(select(GraphRunRequest))
        assert run is not None
        assert request is not None
        assert run.status == GraphRunStatus.REQUESTED
        assert request.status == DispatchRequestStatus.PENDING
        assert request.graph_run_id == run.id
        assert str(run.id) == first.json()["graph_run_id"]
        assert run.thread_id != str(run.document_id)
        graph_event = session.scalar(
            select(AuditEvent).where(AuditEvent.event_type == AuditEventType.GRAPH_RUN_REQUESTED)
        )
        assert graph_event is not None
        assert graph_event.graph_run_id == run.id
        events = set(session.scalars(select(AuditEvent.event_type)))
        assert events == {
            AuditEventType.UPLOAD_RECEIVED,
            AuditEventType.FILE_VALIDATION_PASSED,
            AuditEventType.DOCUMENT_STORED,
            AuditEventType.GRAPH_RUN_REQUESTED,
        }


def test_duplicate_hash_signal_and_idempotency_conflict(api: tuple[TestClient, object]) -> None:
    test_client, _ = api
    payload = Path("test-data/minimal.pdf").read_bytes()

    first = test_client.post(
        "/api/v1/documents",
        headers={"Authorization": "Bearer integration-token", "Idempotency-Key": "first"},
        files={"file": ("one.pdf", payload, "application/pdf")},
    )
    duplicate = test_client.post(
        "/api/v1/documents",
        headers={"Authorization": "Bearer integration-token", "Idempotency-Key": "second"},
        files={"file": ("two.pdf", payload, "application/pdf")},
    )
    conflict = test_client.post(
        "/api/v1/documents",
        headers={"Authorization": "Bearer integration-token", "Idempotency-Key": "first"},
        files={"file": ("changed.pdf", payload + b"\n", "application/pdf")},
    )

    assert first.json()["duplicate_hash_detected"] is False
    assert duplicate.json()["duplicate_hash_detected"] is True
    assert conflict.status_code == 409
    assert conflict.headers["content-type"].startswith("application/problem+json")


def test_invalid_upload_is_audited_without_document(api: tuple[TestClient, object]) -> None:
    test_client, app = api
    response = test_client.post(
        "/api/v1/documents",
        headers={"Authorization": "Bearer integration-token", "Idempotency-Key": "invalid"},
        files={"file": ("fake.pdf", b"not-pdf", "application/pdf")},
    )

    assert response.status_code == 400
    with app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Document)) == 0
        events = list(session.scalars(select(AuditEvent.event_type)))
        assert events == [AuditEventType.UPLOAD_RECEIVED, AuditEventType.FILE_VALIDATION_FAILED]


def test_oversized_upload_is_rejected_and_audited(api: tuple[TestClient, object]) -> None:
    test_client, app = api
    response = test_client.post(
        "/api/v1/documents",
        headers={"Authorization": "Bearer integration-token", "Idempotency-Key": "oversized"},
        files={"file": ("large.pdf", b"%PDF-1.4\n" + b"x" * 1024, "application/pdf")},
    )

    assert response.status_code == 413
    with app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Document)) == 0
        events = list(session.scalars(select(AuditEvent.event_type)))
        assert events == [AuditEventType.UPLOAD_RECEIVED, AuditEventType.FILE_VALIDATION_FAILED]


def test_duplicate_hash_signal_is_tenant_scoped(api: tuple[TestClient, object]) -> None:
    test_client, app = api
    payload = Path("test-data/minimal.pdf").read_bytes()
    first = test_client.post(
        "/api/v1/documents",
        headers={"Authorization": "Bearer integration-token", "Idempotency-Key": "tenant-one"},
        files={"file": ("invoice.pdf", payload, "application/pdf")},
    )
    assert first.status_code == 201

    tenant_id = uuid4()
    user_id = uuid4()
    with app.state.session_factory() as session, session.begin():
        session.add(Tenant(id=tenant_id, name="Isolated Tenant"))
        session.add(
            User(
                id=user_id,
                tenant_id=tenant_id,
                external_subject="isolated-submitter",
                display_name="Isolated Submitter",
                role=UserRole.SUBMITTER,
            )
        )
    second = app.state.intake_service.create_document(
        upload=UploadFile(
            file=BytesIO(payload),
            filename="same-content.pdf",
            headers=Headers({"content-type": "application/pdf"}),
        ),
        idempotency_key="tenant-two",
        correlation_id="tenant-two-correlation",
        identity=RequestIdentity(
            tenant_id=tenant_id,
            user_id=user_id,
            subject="isolated-submitter",
            role=UserRole.SUBMITTER,
        ),
    )

    assert second.response.duplicate_hash_detected is False
    with app.state.session_factory() as session:
        tenant_counts = dict(
            session.execute(
                select(Document.tenant_id, func.count()).group_by(Document.tenant_id)
            ).all()
        )
        assert tenant_counts == {app.state.settings.bootstrap_tenant_id: 1, tenant_id: 1}


@pytest.mark.asyncio
@pytest.mark.graph
@pytest.mark.checkpoint
@pytest.mark.e2e
async def test_dispatcher_leases_retries_and_prevents_duplicate_graph_start(
    api: tuple[TestClient, object], database_url: str
) -> None:
    test_client, app = api
    payload = await asyncio.to_thread(Path("test-data/minimal.pdf").read_bytes)
    response = test_client.post(
        "/api/v1/documents",
        headers={"Authorization": "Bearer integration-token", "Idempotency-Key": "dispatch"},
        files={
            "file": (
                "invoice.pdf",
                payload,
                "application/pdf",
            )
        },
    )
    assert response.status_code == 201

    settings = Settings(
        database_url=database_url,
        checkpoint_database_url=database_url,
        storage_root=app.state.settings.storage_root,
    )
    engine = build_async_engine(settings)
    session_factory = build_async_session_factory(engine)
    repository = GraphRunRepository(session_factory)
    workflow = PostgresWorkflowBackend(session_factory)
    resolution_agent = ConfiguredResolutionAgent(settings=settings, session_factory=session_factory)
    try:
        leased = await asyncio.gather(
            repository.lease_next(owner="lease-a", now=_now(), lease_seconds=30),
            repository.lease_next(owner="lease-b", now=_now(), lease_seconds=30),
        )
        assert sum(request is not None for request in leased) == 1
        request = next(request for request in leased if request is not None)
        await repository.mark_failed(
            request=request,
            owner="lease-a" if request in leased[:1] else "lease-b",
            now=_now(),
            error_code="TEST_TRANSIENT",
            safe_message="A transient graph test failure occurred.",
            delay_seconds=0,
            retryable=True,
        )

        with app.state.session_factory() as session:
            retry = session.scalar(select(GraphRunRequest))
            assert retry is not None
            assert retry.status == DispatchRequestStatus.RETRY_SCHEDULED
            assert retry.attempt_count == 1
            run = session.scalar(select(GraphRun))
            assert run is not None
            run_input = GraphRunInput(
                graph_run_id=run.id,
                graph_version=run.graph_version,
                thread_id=run.thread_id,
                tenant_id=run.tenant_id,
                document_id=run.document_id,
                correlation_id=run.correlation_id,
            )

        async with open_graph_executor(
            settings,
            initialize_checkpoint_schema=True,
            workflow=workflow,
            resolution_agent=resolution_agent,
        ) as first_executor:
            first_outcome = await first_executor.execute(run_input)
            assert first_outcome.status == "INTERRUPTED"
            assert first_outcome.replayed is False

        async with open_graph_executor(
            settings,
            initialize_checkpoint_schema=False,
            workflow=workflow,
            resolution_agent=resolution_agent,
        ) as restarted_executor:
            assert await restarted_executor.checkpoint_completed(run_input.thread_id) is False
            dispatcher = GraphRunDispatcher(
                repository=repository,
                executor=restarted_executor,
                settings=settings,
                owner="restart-orchestrator",
            )
            assert await dispatcher.dispatch_one(_now()) is True
            assert await dispatcher.dispatch_one(_now()) is False

        with app.state.session_factory() as session:
            run = session.scalar(select(GraphRun))
            request_record = session.scalar(select(GraphRunRequest))
            assert run is not None
            assert request_record is not None
            assert run.status == GraphRunStatus.INTERRUPTED
            assert request_record.status == DispatchRequestStatus.COMPLETED
            assert request_record.attempt_count == 2
            event_counts = dict(
                session.execute(
                    select(AuditEvent.event_type, func.count()).group_by(AuditEvent.event_type)
                ).all()
            )
            assert event_counts[AuditEventType.GRAPH_RUN_STARTED] == 1
            assert event_counts.get(AuditEventType.GRAPH_RUN_COMPLETED, 0) == 0
            agent_run = session.scalar(select(AgentRun))
            recommendation = session.scalar(select(AgentRecommendation))
            candidate = session.scalar(select(ExtractionCandidate))
            extraction_run = session.scalar(select(ExtractionRun))
            tool_names = list(
                session.scalars(select(AgentToolCall.tool_name).order_by(AgentToolCall.started_at))
            )
            stored = session.scalar(select(StoredObject))
            assert agent_run is not None
            assert recommendation is not None
            assert recommendation.recommended_disposition != "AUTO_APPROVE"
            assert candidate is not None
            assert extraction_run is not None
            assert candidate.extraction_run_id == extraction_run.id
            assert session.scalar(select(func.count()).select_from(ExtractionFieldEvidence)) == len(
                candidate.candidate_data
            )
            prompt_version = session.scalar(select(PromptVersion))
            assert prompt_version is not None
            assert len(prompt_version.content_sha256) == 64
            assert tool_names[:3] == [
                "inspect_document",
                "classify_document",
                "extract_invoice_from_text",
            ]
            assert set(tool_names) <= {
                "inspect_document",
                "classify_document",
                "extract_invoice_from_text",
                "extract_invoice_from_visual_content",
                "lookup_supplier",
                "lookup_purchase_order",
                "validate_candidate",
                "retry_extraction",
                "submit_recommendation",
            }
            assert stored is not None
            original_path = app.state.settings.storage_root / stored.object_key
            original_digest = hashlib.sha256(original_path.read_bytes()).hexdigest()
            assert original_digest == stored.sha256

        reviewer = configured_actor(app.state.settings, UserRole.REVIEWER)
        approver = configured_actor(app.state.settings, UserRole.APPROVER)
        operations_admin = configured_actor(app.state.settings, UserRole.OPERATIONS_ADMIN)
        competing_reviewer = RequestIdentity(
            tenant_id=reviewer.tenant_id,
            user_id=uuid4(),
            subject="competing-reviewer",
            role=UserRole.REVIEWER,
        )
        other_tenant_reviewer = RequestIdentity(
            tenant_id=uuid4(),
            user_id=uuid4(),
            subject="other-tenant-reviewer",
            role=UserRole.REVIEWER,
        )
        with app.state.session_factory() as session, session.begin():
            session.add(Tenant(id=other_tenant_reviewer.tenant_id, name="Other Tenant"))
            session.add_all(
                [
                    User(
                        id=actor.user_id,
                        tenant_id=actor.tenant_id,
                        external_subject=actor.subject,
                        display_name=actor.subject,
                        role=actor.role,
                    )
                    for actor in (
                        reviewer,
                        approver,
                        operations_admin,
                        competing_reviewer,
                        other_tenant_reviewer,
                    )
                ]
            )
        reviews = ReviewService(app.state.settings, app.state.session_factory)
        first_task = reviews.list_tasks(reviewer)[0]
        with pytest.raises(ResourceNotFound):
            reviews.get_task(first_task.id, other_tenant_reviewer)
        reviews.claim(first_task.id, reviewer)
        with pytest.raises(ReviewConflict):
            reviews.claim(first_task.id, competing_reviewer)
        correction_command = CompleteReviewCommand(
            decision="REQUEST_CORRECTION",
            reason="Confirm the authoritative total.",
            field_corrections=[
                FieldCorrectionCommand(
                    field="total",
                    new_value="1360.00",
                    reason="Confirmed from source document",
                )
            ],
        )
        completion_race = await asyncio.gather(
            asyncio.to_thread(reviews.complete, first_task.id, correction_command, reviewer),
            asyncio.to_thread(reviews.complete, first_task.id, correction_command, reviewer),
            return_exceptions=True,
        )
        completed = [result for result in completion_race if not isinstance(result, BaseException)]
        conflicts = [result for result in completion_race if isinstance(result, ReviewConflict)]
        assert len(completed) == 1
        assert len(conflicts) == 1
        correction = completed[0]
        assert correction.decision == "REQUEST_CORRECTION"
        resume_repository = GraphResumeRepository(session_factory)
        async with open_graph_executor(
            settings,
            initialize_checkpoint_schema=False,
            workflow=workflow,
            resolution_agent=resolution_agent,
        ) as correction_executor:
            crashed_request = await resume_repository.lease_next(
                owner="crashed-correction-resumer",
                now=_now(),
                lease_seconds=1,
            )
            assert crashed_request is not None
            assert await resume_repository.mark_running(
                request=crashed_request,
                owner="crashed-correction-resumer",
                now=_now(),
            )
            correction_outcome = await correction_executor.resume(
                crashed_request.run, crashed_request.payload
            )
            assert correction_outcome.status == "INTERRUPTED"

        with app.state.session_factory() as session, session.begin():
            crashed_record = session.get(GraphResumeRequest, crashed_request.request_id)
            assert crashed_record is not None
            crashed_record.lease_expires_at = _now() - timedelta(seconds=1)
            crashed_record.attempt_count = crashed_record.max_attempts

        second_task = reviews.get_task(first_task.id, reviewer)
        assert second_task.cycle == 2
        assert second_task.status == "OPEN"
        reviews.claim(second_task.id, approver)
        approval = reviews.complete(
            second_task.id,
            CompleteReviewCommand(decision="APPROVE", reason="Invoice confirmed."),
            approver,
        )
        assert approval.decision == "APPROVE"

        async with open_graph_executor(
            settings,
            initialize_checkpoint_schema=False,
            workflow=workflow,
            resolution_agent=resolution_agent,
        ) as recovery_executor:
            correction_dispatcher = GraphResumeDispatcher(
                repository=resume_repository,
                executor=recovery_executor,
                settings=settings,
                owner="restarted-correction-resumer",
            )
            assert await correction_dispatcher.dispatch_one(_now()) is False

        with app.state.session_factory() as session:
            crashed_record = session.get(GraphResumeRequest, crashed_request.request_id)
            assert crashed_record is not None
            assert crashed_record.status == DispatchRequestStatus.COMPLETED
            assert crashed_record.attempt_count == crashed_record.max_attempts

        async with open_graph_executor(
            settings,
            initialize_checkpoint_schema=False,
            workflow=workflow,
            resolution_agent=resolution_agent,
        ) as approval_executor:
            approval_dispatcher = GraphResumeDispatcher(
                repository=resume_repository,
                executor=approval_executor,
                settings=settings,
                owner="approval-resumer",
            )
            assert await approval_dispatcher.dispatch_one(_now()) is True
            assert await approval_dispatcher.dispatch_one(_now()) is False

        with app.state.session_factory() as session:
            assert session.scalar(select(func.count()).select_from(GraphResumeRequest)) == 2
            assert session.scalar(select(func.count()).select_from(ReviewedDocumentVersion)) == 2
            assert session.scalar(select(func.count()).select_from(ValidationRun)) == 5
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(ValidationRun)
                    .where(ValidationRun.authoritative.is_(True))
                )
                == 4
            )
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(ValidationRun)
                    .where(ValidationRun.authoritative.is_(False))
                )
                == 1
            )
            assert session.scalar(select(func.count()).select_from(ConfidenceRun)) == 3
            assert session.scalar(select(func.count()).select_from(ApprovedBusinessRecord)) == 1
            assert session.scalar(select(func.count()).select_from(OutboxEvent)) == 1
            final_task = session.scalar(select(ReviewTask))
            final_run = session.scalar(select(GraphRun))
            assert final_task is not None
            assert final_task.status == "APPROVED"
            assert final_run is not None
            assert final_run.status == GraphRunStatus.COMPLETED

        delivery_settings = Settings(
            database_url=database_url,
            checkpoint_database_url=database_url,
            storage_root=app.state.settings.storage_root,
            zapier_hook_url="https://mock-zapier.invalid/hooks/catch",
            zapier_callback_token="integration-callback-secret",
        )
        delivery_client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=mock_zapier_app), base_url="http://mock-zapier"
        )
        sender = HttpxZapierSender(delivery_settings, delivery_client)
        delivery_worker = IntegrationWorker(
            repository=OutboxRepository(session_factory),
            sender=sender,
            settings=delivery_settings,
            owner="integration-delivery-worker",
        )
        assert await delivery_worker.deliver_one(_now()) is True
        assert await delivery_worker.deliver_one(_now()) is False
        mock_deliveries = (await delivery_client.get("/deliveries")).json()
        assert len(mock_deliveries) == 1
        await delivery_client.aclose()

        with app.state.session_factory() as session:
            outbox = session.scalar(select(OutboxEvent))
            assert outbox is not None
            assert outbox.status == "DELIVERED"
            assert session.scalar(select(func.count()).select_from(WebhookDelivery)) == 1
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(AuditEvent)
                    .where(AuditEvent.event_type == AuditEventType.WEBHOOK_DELIVERED)
                )
                == 1
            )
            outbox_id = outbox.id
            outbox_key = outbox.idempotency_key

        integrations = IntegrationAdminService(delivery_settings, app.state.session_factory)
        callback = ZapierCallback(
            outbox_event_id=outbox_id,
            idempotency_key=outbox_key,
            callback_id="integration-zap-run",
            status="COMPLETED",
        )
        assert integrations.callback(callback, "integration-callback-secret").replayed is False
        assert integrations.callback(callback, "integration-callback-secret").replayed is True

        with app.state.session_factory() as session, session.begin():
            outbox = session.get(OutboxEvent, outbox_id)
            assert outbox is not None
            outbox.status = "PENDING"
            outbox.delivered_at = None
            outbox.next_attempt_at = _now()
        failure_client = httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(400, content=b"rejected", request=request)
            )
        )
        failure_worker = IntegrationWorker(
            repository=OutboxRepository(session_factory),
            sender=HttpxZapierSender(delivery_settings, failure_client),
            settings=delivery_settings,
            owner="integration-failure-worker",
        )
        assert await failure_worker.deliver_one(_now()) is True
        await failure_client.aclose()
        with app.state.session_factory() as session:
            outbox = session.get(OutboxEvent, outbox_id)
            assert outbox is not None
            assert outbox.status == "DEAD_LETTER"
            assert session.scalar(select(func.count()).select_from(WebhookDelivery)) == 2

        replay = integrations.replay(outbox_id, operations_admin)
        assert replay.status == "PENDING"
        with app.state.session_factory() as session:
            outbox = session.get(OutboxEvent, outbox_id)
            dead_letter = session.scalar(
                select(DeadLetterEvent).where(DeadLetterEvent.outbox_event_id == outbox_id)
            )
            assert outbox is not None
            assert outbox.attempt_count == 2
            assert outbox.max_attempts == 2 + delivery_settings.webhook_max_attempts
            assert dead_letter is not None
            assert dead_letter.replay_count == 1
            stored = session.scalar(select(StoredObject))
            assert stored is not None
            assert (
                hashlib.sha256(
                    (app.state.settings.storage_root / stored.object_key).read_bytes()
                ).hexdigest()
                == stored.sha256
            )
            material_events = set(session.scalars(select(AuditEvent.event_type)))
            confidence_event_count = session.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.event_type == AuditEventType.CONFIDENCE_CALCULATED)
            )
            assert confidence_event_count is not None
            assert confidence_event_count >= 2
            assert {
                AuditEventType.UPLOAD_RECEIVED,
                AuditEventType.FILE_VALIDATION_PASSED,
                AuditEventType.DOCUMENT_STORED,
                AuditEventType.GRAPH_RUN_REQUESTED,
                AuditEventType.GRAPH_RUN_STARTED,
                AuditEventType.AGENT_STARTED,
                AuditEventType.AGENT_TOOL_REQUESTED,
                AuditEventType.AGENT_TOOL_COMPLETED,
                AuditEventType.EXTRACTION_COMPLETED,
                AuditEventType.RECOMMENDATION_SUBMITTED,
                AuditEventType.AUTHORITATIVE_VALIDATION_COMPLETED,
                AuditEventType.CONFIDENCE_CALCULATED,
                AuditEventType.REVIEW_TASK_CREATED,
                AuditEventType.REVIEW_TASK_CLAIMED,
                AuditEventType.FIELD_CORRECTED,
                AuditEventType.REVIEW_COMPLETED,
                AuditEventType.GRAPH_RESUMED,
                AuditEventType.DOCUMENT_APPROVED,
                AuditEventType.OUTBOX_EVENT_CREATED,
                AuditEventType.WEBHOOK_ATTEMPTED,
                AuditEventType.WEBHOOK_DELIVERED,
                AuditEventType.WEBHOOK_DELIVERY_EXHAUSTED,
                AuditEventType.ZAPIER_CALLBACK_RECEIVED,
                AuditEventType.WEBHOOK_REPLAY_REQUESTED,
            } <= material_events
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.graph
@pytest.mark.checkpoint
async def test_incomplete_agent_without_candidate_can_be_rejected_and_resumed(
    api: tuple[TestClient, object], database_url: str
) -> None:
    test_client, app = api
    payload = await asyncio.to_thread(Path("test-data/minimal.pdf").read_bytes)
    response = test_client.post(
        "/api/v1/documents",
        headers={
            "Authorization": "Bearer integration-token",
            "Idempotency-Key": "incomplete-agent",
        },
        files={"file": ("invoice.pdf", payload, "application/pdf")},
    )
    assert response.status_code == 201

    settings = Settings(
        database_url=database_url,
        checkpoint_database_url=database_url,
        storage_root=app.state.settings.storage_root,
        agent_max_tool_calls=1,
    )
    engine = build_async_engine(settings)
    session_factory = build_async_session_factory(engine)
    workflow = PostgresWorkflowBackend(session_factory)
    resolution_agent = ConfiguredResolutionAgent(settings=settings, session_factory=session_factory)
    run_repository = GraphRunRepository(session_factory)
    resume_repository = GraphResumeRepository(session_factory)
    try:
        async with open_graph_executor(
            settings,
            initialize_checkpoint_schema=True,
            workflow=workflow,
            resolution_agent=resolution_agent,
        ) as executor:
            dispatcher = GraphRunDispatcher(
                repository=run_repository,
                executor=executor,
                settings=settings,
                owner="incomplete-starter",
            )
            assert await dispatcher.dispatch_one(_now()) is True

        approver = configured_actor(app.state.settings, UserRole.APPROVER)
        with app.state.session_factory() as session, session.begin():
            session.add(
                User(
                    id=approver.user_id,
                    tenant_id=approver.tenant_id,
                    external_subject=approver.subject,
                    display_name=approver.subject,
                    role=approver.role,
                )
            )
        reviews = ReviewService(app.state.settings, app.state.session_factory)
        task = reviews.list_tasks(approver)[0]
        assert task.extraction_candidate_id is None
        reviews.claim(task.id, approver)
        reviews.complete(
            task.id,
            CompleteReviewCommand(decision="REJECT", reason="Insufficient extraction evidence."),
            approver,
        )

        async with open_graph_executor(
            settings,
            initialize_checkpoint_schema=False,
            workflow=workflow,
            resolution_agent=resolution_agent,
        ) as executor:
            dispatcher = GraphResumeDispatcher(
                repository=resume_repository,
                executor=executor,
                settings=settings,
                owner="incomplete-resumer",
            )
            assert await dispatcher.dispatch_one(_now()) is True

        with app.state.session_factory() as session:
            reviewed = session.scalar(select(ReviewedDocumentVersion))
            run = session.scalar(select(GraphRun))
            assert reviewed is not None
            assert reviewed.extraction_candidate_id is None
            assert run is not None
            assert run.status == GraphRunStatus.COMPLETED
            assert session.scalar(select(func.count()).select_from(OutboxEvent)) == 0
    finally:
        await engine.dispose()


def _now():
    from datetime import UTC, datetime

    return datetime.now(UTC)
