"""End-to-end CDP onboarding requests through the durable graph (stub provider, ADR 0010)."""

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
from intake_api.application.cdp_reference import (
    load_cdp_reference_data,
    parse_agreements,
    parse_attributes,
    parse_connectors,
)
from intake_api.application.claims_reference import (
    load_claims_reference_data,
    parse_policies,
    parse_vehicles,
)
from intake_api.application.identity import configured_actor
from intake_api.application.problems import InvalidUpload
from intake_api.application.review import (
    CompleteReviewCommand,
    FieldCorrectionCommand,
    ReviewService,
)
from intake_api.application.review_queues import (
    ReviewQueueService,
)
from intake_api.domain.enums import (
    ReviewQueue,
    UserRole,
)
from intake_api.domain.models import (
    AgentToolCall,
    ClaimRoutingDecision,
    ConfidenceRun,
    ExtractionCandidate,
    ExtractionFieldEvidence,
    GraphRun,
    OutboxEvent,
    PiiScanFinding,
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
CDP = Path("test-data/cdp")


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
        load_cdp_reference_data(
            session,
            tenant_id=settings.bootstrap_tenant_id,
            agreements=parse_agreements((CDP / "data_agreements.csv").read_text()),
            attributes=parse_attributes((CDP / "canonical_attributes.csv").read_text()),
            connectors=parse_connectors((CDP / "connectors.csv").read_text()),
            actor_id="integration-test",
            correlation_id="cdp-pipeline-reference",
            source_sha256="0" * 64,
        )
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
                "data_agreements, canonical_attributes, cdp_connectors, "
                "users, tenants CASCADE"
            )
        )


def _upload(client: TestClient, packet: str, key: str) -> UUID:
    payload = (CDP / "packets" / packet).read_bytes()
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


def _queue_for(session: object, document_id: UUID) -> tuple[ReviewQueue, list[str]]:
    decision = session.scalar(  # type: ignore[attr-defined]
        select(ClaimRoutingDecision)
        .where(ClaimRoutingDecision.document_id == document_id)
        .order_by(ClaimRoutingDecision.created_at.desc())
    )
    assert decision is not None
    assert decision.routing_version == "cdp-routing-v1"
    return decision.review_queue, list(decision.flags)


@pytest.mark.asyncio
async def test_unmasked_sample_is_caught_by_the_scanner_and_never_stored(
    api: tuple[TestClient, object], database_url: str
) -> None:
    client, app = api
    document_id = _upload(client, "D11_onboarding.pdf", "cdp-d11")
    await _dispatch_runs(_graph_settings(app, database_url), 1)

    with app.state.session_factory() as session:  # type: ignore[attr-defined]
        candidate = session.scalar(
            select(ExtractionCandidate).where(ExtractionCandidate.document_id == document_id)
        )
        assert candidate is not None
        assert candidate.document_type == "CDP_ONBOARDING"
        findings = {
            (row.page_number, row.identifier_type, row.occurrences)
            for row in session.scalars(
                select(PiiScanFinding).where(PiiScanFinding.document_id == document_id)
            )
        }
        assert {(3, "EMAIL", 6), (3, "PHONE", 6)} <= findings
        # The stub extractor mirrors the clean D01 request, so only the scanner's facts (not the
        # model) can send this packet to privacy review.
        queue, flags = _queue_for(session, document_id)
        assert queue == ReviewQueue.PRIVACY_REVIEW
        assert "UNMASKED_IDENTIFIERS_IN_PACKET" in flags
        tools = list(
            session.scalars(
                select(AgentToolCall.tool_name)
                .where(AgentToolCall.document_id == document_id)
                .order_by(AgentToolCall.started_at)
            )
        )
        assert tools.index("scan_onboarding_packet") < tools.index("segment_onboarding_packet")
        stored_text = " ".join(
            [str(candidate.candidate_data)]
            + [
                row.evidence_excerpt
                for row in session.scalars(
                    select(ExtractionFieldEvidence).where(
                        ExtractionFieldEvidence.candidate_id == candidate.id
                    )
                )
            ]
        )
        assert "maya.chen@example.com" not in stored_text
        assert "555-0155" not in stored_text


@pytest.mark.asyncio
async def test_clean_request_is_ready_duplicate_is_caught_and_approval_emits_a_plan(
    api: tuple[TestClient, object], database_url: str
) -> None:
    client, app = api
    first = _upload(client, "D01_onboarding.pdf", "cdp-d01")
    copy = _upload(client, "D18_onboarding.pdf", "cdp-d18")
    settings = _graph_settings(app, database_url)
    await _dispatch_runs(settings, 2)

    with app.state.session_factory() as session:  # type: ignore[attr-defined]
        assert _queue_for(session, first)[0] == ReviewQueue.READY_TO_BUILD
        assert _queue_for(session, copy)[0] == ReviewQueue.DUPLICATE_REVIEW
        confidence = session.scalar(
            select(ConfidenceRun)
            .join(GraphRun, GraphRun.id == ConfidenceRun.graph_run_id)
            .where(GraphRun.document_id == first)
        )
        assert confidence is not None
        assert confidence.algorithm_version == "decision-confidence-cdp-v1"

    _add_users(app, UserRole.REVIEWER, UserRole.APPROVER)
    app_settings = app.state.settings  # type: ignore[attr-defined]
    reviewer = configured_actor(app_settings, UserRole.REVIEWER)
    approver = configured_actor(app_settings, UserRole.APPROVER)
    response = client.get(
        "/api/v1/review-queues?document_type=CDP_ONBOARDING",
        headers={"Authorization": "Bearer integration-reviewer"},
    )
    assert response.status_code == 200, response.text
    view = response.json()
    assert view["counts_by_queue"]["READY_TO_BUILD"] == 1
    summary = next(item for item in view["items"] if item["document_id"] == str(first))["summary"]
    assert (summary["subject"], summary["event_label"], summary["item_count"]) == (
        "Shopify orders (US store)",
        "Go-live",
        6,
    )
    task_id = next(
        item.review_task_id
        for item in ReviewQueueService(app_settings, app.state.session_factory)  # type: ignore[attr-defined]
        .list_queue(reviewer, document_type="CDP_ONBOARDING")
        .items
        if item.document_id == first
    )
    reviews = ReviewService(app_settings, app.state.session_factory)  # type: ignore[attr-defined]
    reviews.claim(task_id, approver)
    reviews.complete(
        task_id,
        CompleteReviewCommand(decision="APPROVE", reason="Mappings confirmed."),
        approver,
    )
    await _dispatch_resume(settings)

    with app.state.session_factory() as session:  # type: ignore[attr-defined]
        event = session.scalar(select(OutboxEvent).where(OutboxEvent.document_id == first))
        assert event is not None
        plan = event.payload["onboarding_plan"]
        assert plan["agreement_number"] == "DPA-2001"
        assert [task["task_key"] for task in plan["tasks"]] == [
            "connector",
            "mapping",
            "identity",
            "consent",
            "qa",
        ]
        assert plan["tasks"][2]["title"] == "Configure identity resolution on email_address"
        assert {entry["attribute"] for entry in plan["mappings"]} >= {"email_address", "order_id"}


@pytest.mark.asyncio
async def test_workbench_context_and_mapping_corrections_drive_revalidation(
    api: tuple[TestClient, object], database_url: str
) -> None:
    client, app = api
    # Upload the clean request first: the stub extractor gives every packet D01's values, so the
    # later packet is the duplicate and the clean one stays in READY_TO_BUILD.
    clean = _upload(client, "D01_onboarding.pdf", "bench-d01")
    flagged = _upload(client, "D11_onboarding.pdf", "bench-d11")
    settings = _graph_settings(app, database_url)
    await _dispatch_runs(settings, 2)
    _add_users(app, UserRole.REVIEWER, UserRole.APPROVER)
    app_settings = app.state.settings  # type: ignore[attr-defined]
    reviewer = configured_actor(app_settings, UserRole.REVIEWER)
    approver = configured_actor(app_settings, UserRole.APPROVER)
    queues = ReviewQueueService(app_settings, app.state.session_factory)  # type: ignore[attr-defined]
    tasks = {
        item.document_id: item.review_task_id
        for item in queues.list_queue(reviewer, document_type="CDP_ONBOARDING").items
    }

    bench = queues.workbench(tasks[flagged], reviewer)
    findings = bench.context["scan_findings"]
    assert {"page_number": 3, "identifier_type": "EMAIL", "occurrences": 6} in [
        {key: row[key] for key in ("page_number", "identifier_type", "occurrences")}
        for row in findings  # type: ignore[union-attr]
    ]
    attributes = bench.context["attributes"]
    assert attributes["email_address"]["personal_class"] == "DIRECT_IDENTIFIER"  # type: ignore[index]
    mappings = next(field for field in bench.fields if field.name == "mappings")
    assert mappings.items is not None
    assert len(mappings.items) == 6

    reviews = ReviewService(app_settings, app.state.session_factory)  # type: ignore[attr-defined]
    task_id = tasks[clean]
    reviews.claim(task_id, approver)
    with pytest.raises(InvalidUpload):
        reviews.complete(
            task_id,
            CompleteReviewCommand(
                decision="REQUEST_CORRECTION",
                reason="Bad path",
                field_corrections=[
                    FieldCorrectionCommand(
                        field="mappings[40].excluded", new_value="true", reason="No such row"
                    )
                ],
            ),
            approver,
        )
    reviews.complete(
        task_id,
        CompleteReviewCommand(
            decision="REQUEST_CORRECTION",
            reason="Client asked us not to ingest emails yet.",
            field_corrections=[
                FieldCorrectionCommand(
                    field="mappings[0].excluded", new_value="true", reason="Not yet approved"
                )
            ],
        ),
        approver,
    )
    await _dispatch_resume(settings)

    revalidated = queues.workbench(task_id, reviewer)
    assert revalidated.review_cycle == 2
    assert revalidated.item.route.review_queue == ReviewQueue.MAPPING_REVIEW
    assert "NO_IDENTITY_KEY" in revalidated.item.route.flags
    corrected = next(field for field in revalidated.fields if field.name == "mappings")
    assert corrected.reviewed is True
    assert corrected.value[0]["excluded"] is True  # type: ignore[index]


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


def _now() -> datetime:
    return datetime.now(UTC)
