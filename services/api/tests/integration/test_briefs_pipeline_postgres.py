"""End-to-end campaign briefs through the durable graph with the stub provider (ADR 0009)."""

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

from intake_api.application.briefs_reference import (
    load_brief_reference_data,
    parse_lead_times,
    parse_sow_brands,
    parse_sows,
)
from intake_api.application.claims_reference import (
    load_claims_reference_data,
    parse_policies,
    parse_vehicles,
)
from intake_api.application.identity import configured_actor
from intake_api.application.problems import InvalidUpload, ReviewConflict
from intake_api.application.review import (
    CompleteReviewCommand,
    FieldCorrectionCommand,
    ReviewService,
)
from intake_api.application.review_queues import (
    RerouteCommand,
    ReviewQueueService,
    SendClientQuestionsCommand,
)
from intake_api.domain.enums import (
    AuditEventType,
    ReviewQueue,
    UserRole,
)
from intake_api.domain.models import (
    AgentToolCall,
    AuditEvent,
    BriefIntakeKey,
    ClaimRoutingDecision,
    ConfidenceRun,
    DocumentPageSegment,
    ExtractionCandidate,
    ExtractionFieldEvidence,
    GraphRun,
    OutboxEvent,
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
BRIEFS = Path("test-data/briefs")


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
        demo_reviewer_token="integration-reviewer",
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
        load_brief_reference_data(
            session,
            tenant_id=settings.bootstrap_tenant_id,
            sows=parse_sows((BRIEFS / "statements_of_work.csv").read_text()),
            brands=parse_sow_brands((BRIEFS / "sow_brands.csv").read_text()),
            lead_times=parse_lead_times((BRIEFS / "lead_times.csv").read_text()),
            actor_id="integration-test",
            correlation_id="brief-pipeline-reference",
            source_sha256="0" * 64,
        )
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
                "documents, stored_objects, policies, statements_of_work, asset_lead_times, "
                "users, tenants CASCADE"
            )
        )


def _upload(client: TestClient, packet: str, key: str) -> UUID:
    payload = (BRIEFS / "packets" / packet).read_bytes()
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
    assert decision.routing_version == "brief-routing-v1"
    return decision.review_queue


@pytest.mark.asyncio
async def test_brief_is_segmented_extracted_routed_and_deduplicated(
    api: tuple[TestClient, object], database_url: str
) -> None:
    client, app = api
    first = _upload(client, "B01_brief.pdf", "briefs-b01")
    copy = _upload(client, "B14_brief.pdf", "briefs-b14")
    await _dispatch_runs(_graph_settings(app, database_url), 2)

    with app.state.session_factory() as session:  # type: ignore[attr-defined]
        candidate = session.scalar(
            select(ExtractionCandidate).where(ExtractionCandidate.document_id == first)
        )
        assert candidate is not None
        assert candidate.document_type == "CAMPAIGN_BRIEF"
        assert len(candidate.candidate_data["deliverables"]) == 3
        evidence = set(
            session.scalars(
                select(ExtractionFieldEvidence.field_name).where(
                    ExtractionFieldEvidence.candidate_id == candidate.id
                )
            )
        )
        assert {"brand", "deliverables[0]", "deliverables[2]"} <= evidence
        labels = list(
            session.scalars(
                select(DocumentPageSegment.segment_label).where(
                    DocumentPageSegment.document_id == first
                )
            )
        )
        assert [label.value for label in labels] == ["BRIEF"]
        tools = set(
            session.scalars(
                select(AgentToolCall.tool_name).where(AgentToolCall.document_id == first)
            )
        )
        assert {"segment_brief_packet", "extract_brief_from_text"} <= tools
        assert not any("claim" in name or "invoice" in name for name in tools)
        # The stub extractor mirrors fixture B01: B01 is ready for planning and the email-only
        # resubmission (B14) repeats its SOW, campaign, and launch and is caught as a duplicate.
        assert _queue_for(session, first) == ReviewQueue.READY_FOR_PLANNING
        assert _queue_for(session, copy) == ReviewQueue.DUPLICATE_REVIEW
        confidence = session.scalar(
            select(ConfidenceRun)
            .join(GraphRun, GraphRun.id == ConfidenceRun.graph_run_id)
            .where(GraphRun.document_id == first)
        )
        assert confidence is not None
        assert confidence.algorithm_version == "decision-confidence-briefs-v1"
        key = session.scalar(select(BriefIntakeKey).where(BriefIntakeKey.document_id == first))
        assert key is not None
        assert (key.sow_number, key.campaign_key) == ("SOW-1001", "winter warmers")
        assert session.scalar(select(ReviewTask.status).where(ReviewTask.document_id == first)) == (
            "OPEN"
        )


def _now() -> datetime:
    return datetime.now(UTC)


def _add_users(app: object, *roles: UserRole) -> None:
    settings = app.state.settings  # type: ignore[attr-defined]
    with app.state.session_factory() as session, session.begin():  # type: ignore[attr-defined]
        for role in roles:
            actor = configured_actor(settings, role)
            session.add(
                User(
                    id=actor.user_id,
                    tenant_id=actor.tenant_id,
                    external_subject=actor.subject,
                    display_name=actor.subject,
                    role=actor.role,
                )
            )


@pytest.mark.asyncio
async def test_generic_review_queue_api_serves_briefs(
    api: tuple[TestClient, object], database_url: str
) -> None:
    client, app = api
    first = _upload(client, "B01_brief.pdf", "queue-b01")
    copy = _upload(client, "B14_brief.pdf", "queue-b14")
    settings = _graph_settings(app, database_url)
    await _dispatch_runs(settings, 2)
    _add_users(app, UserRole.REVIEWER, UserRole.APPROVER)
    app_settings = app.state.settings  # type: ignore[attr-defined]
    reviewer = configured_actor(app_settings, UserRole.REVIEWER)
    approver = configured_actor(app_settings, UserRole.APPROVER)
    auth = {"Authorization": "Bearer integration-reviewer"}

    response = client.get("/api/v1/review-queues?document_type=CAMPAIGN_BRIEF", headers=auth)
    assert response.status_code == 200, response.text
    view = response.json()
    assert view["document_type"] == "CAMPAIGN_BRIEF"
    assert len(view["items"]) == 2
    assert view["counts_by_queue"]["READY_FOR_PLANNING"] == 1
    assert view["counts_by_queue"]["DUPLICATE_REVIEW"] == 1
    assert "FAST_TRACK" not in view["counts_by_queue"]
    summary = next(item for item in view["items"] if item["document_id"] == str(first))["summary"]
    assert summary["party"] == "Harbor & Vine Coffee Co. · Harbor & Vine"
    assert (summary["subject"], summary["event_label"], summary["item_count"]) == (
        "Winter Warmers",
        "Launch",
        3,
    )
    claims_view = client.get("/api/v1/review-queues?document_type=CLAIM_PACKET", headers=auth)
    assert claims_view.json()["items"] == []

    service = ReviewQueueService(app_settings, app.state.session_factory)  # type: ignore[attr-defined]
    items = {
        item.document_id: item
        for item in service.list_queue(reviewer, document_type="CAMPAIGN_BRIEF").items
    }
    bench = service.workbench(items[first].review_task_id, reviewer)
    assert bench.document_type == "CAMPAIGN_BRIEF"
    assert len(bench.rules) == 14
    assert next(rule for rule in bench.rules if rule.rule_id == "CB-007").name
    deliverables = next(field for field in bench.fields if field.name == "deliverables")
    assert deliverables.items is not None
    assert len(deliverables.items) == 3
    assert deliverables.items[0].source_page == 1

    with pytest.raises(InvalidUpload):
        service.reroute(
            items[copy].review_task_id,
            RerouteCommand(to_queue=ReviewQueue.FAST_TRACK, reason="Not a brief queue"),
            reviewer,
        )
    moved = service.reroute(
        items[copy].review_task_id,
        RerouteCommand(
            to_queue=ReviewQueue.TRAFFIC_REVIEW, reason="Client confirmed a new flight."
        ),
        reviewer,
    )
    assert moved.review_queue == ReviewQueue.TRAFFIC_REVIEW

    reviews = ReviewService(app_settings, app.state.session_factory)  # type: ignore[attr-defined]
    task_id = items[first].review_task_id
    reviews.claim(task_id, approver)
    reviews.complete(
        task_id,
        CompleteReviewCommand(
            decision="REQUEST_CORRECTION",
            reason="Client raised the budget.",
            field_corrections=[
                FieldCorrectionCommand(
                    field="budget_total", new_value="500000", reason="Revised budget"
                )
            ],
        ),
        approver,
    )
    await _dispatch_resume(settings)

    revalidated = service.workbench(task_id, reviewer)
    assert revalidated.review_cycle == 2
    assert revalidated.item.route.review_queue == ReviewQueue.SCOPE_REVIEW
    assert "OVER_SOW_BUDGET" in revalidated.item.route.flags


@pytest.mark.asyncio
async def test_approved_brief_emits_one_task_per_deliverable_and_records_client_questions(
    api: tuple[TestClient, object], database_url: str
) -> None:
    client, app = api
    document_id = _upload(client, "B01_brief.pdf", "approve-b01")
    settings = _graph_settings(app, database_url)
    await _dispatch_runs(settings, 1)
    _add_users(app, UserRole.REVIEWER, UserRole.APPROVER)
    app_settings = app.state.settings  # type: ignore[attr-defined]
    reviewer = configured_actor(app_settings, UserRole.REVIEWER)
    approver = configured_actor(app_settings, UserRole.APPROVER)
    queues = ReviewQueueService(app_settings, app.state.session_factory)  # type: ignore[attr-defined]
    task_id = queues.list_queue(reviewer, document_type="CAMPAIGN_BRIEF").items[0].review_task_id

    questions = queues.client_questions(task_id, reviewer)
    assert questions.draft.questions == []
    with pytest.raises(ReviewConflict):
        queues.record_client_questions(
            task_id, SendClientQuestionsCommand(subject="Questions", body="Hi"), reviewer
        )
    reviews = ReviewService(app_settings, app.state.session_factory)  # type: ignore[attr-defined]
    reviews.claim(task_id, reviewer)
    sent = queues.record_client_questions(
        task_id,
        SendClientQuestionsCommand(
            subject="Questions on the Winter Warmers brief",
            body="Hi Elena,\n\n1. Can you confirm the café list?\n\nThanks",
        ),
        reviewer,
    )
    assert sent.review_cycle == 1
    assert len(queues.client_questions(task_id, reviewer).sent) == 1
    reviews.unclaim(task_id, reviewer)

    reviews.claim(task_id, approver)
    reviews.complete(
        task_id,
        CompleteReviewCommand(decision="APPROVE", reason="Brief complete and in scope."),
        approver,
    )
    await _dispatch_resume(settings)

    with app.state.session_factory() as session:  # type: ignore[attr-defined]
        event = session.scalar(
            select(OutboxEvent).where(
                OutboxEvent.document_id == document_id,
                OutboxEvent.event_type == "DOCUMENT_APPROVED",
            )
        )
        assert event is not None
        assert event.payload["document_type"] == "CAMPAIGN_BRIEF"
        jira = event.payload["jira"]
        assert jira["epic"]["summary"] == "Winter Warmers (Harbor & Vine)"
        assert jira["epic"]["issue_type"] == "Epic"
        assert [issue["summary"] for issue in jira["issues"]][0] == (
            "Winter Warmers: 12 × Social static post"
        )
        assert jira["issues"][0]["due_date"] == "2026-11-20"
        assert all(" " not in label for issue in jira["issues"] for label in issue["labels"])
        assert event.payload["event_key"].startswith("approved-record:")
        routed = session.scalar(
            select(OutboxEvent).where(
                OutboxEvent.document_id == document_id,
                OutboxEvent.event_type == "DOCUMENT_ROUTED",
            )
        )
        assert routed is not None
        assert routed.approved_business_record_id is None
        assert routed.payload["queue"] == "READY_FOR_PLANNING"
        assert routed.payload["service_level"]["target_hours"] == 24
        assert routed.payload["alert"]["title"].startswith("Brief routed to Ready for planning")
        work_request = event.payload["work_request"]
        assert work_request["sow_number"] == "SOW-1001"
        assert [task["task_key"] for task in work_request["tasks"]] == [
            "brief-1",
            "brief-2",
            "brief-3",
        ]
        first = work_request["tasks"][0]
        assert first["title"] == "Winter Warmers: 12 × Social static post"
        assert (first["service"], first["due_date"]) == ("SOCIAL", "2026-11-20")
        audit = session.scalar(
            select(AuditEvent).where(AuditEvent.event_type == AuditEventType.CLIENT_QUESTIONS_SENT)
        )
        assert audit is not None
        assert "body" not in audit.sanitized_metadata
