from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from intake_api.domain.enums import (
    ActorType,
    AuditEventType,
    DispatchRequestStatus,
    GraphRunStatus,
)
from intake_api.domain.models import (
    AuditEvent,
    GraphResumeRequest,
    GraphRun,
    GraphRunRequest,
    ReviewTask,
)
from intake_api.infrastructure.config import Settings
from intake_orchestrator.graph import GraphExecutionOutcome, GraphRunInput


@dataclass(frozen=True, slots=True)
class LeasedGraphRunRequest:
    request_id: UUID
    run: GraphRunInput
    attempt_count: int
    max_attempts: int


@dataclass(frozen=True, slots=True)
class LeasedGraphResumeRequest:
    request_id: UUID
    run: GraphRunInput
    payload: dict[str, object]
    attempt_count: int
    max_attempts: int


class GraphRunRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def lease_next(
        self, *, owner: str, now: datetime, lease_seconds: int
    ) -> LeasedGraphRunRequest | None:
        async with self._session_factory() as session, session.begin():
            request = await session.scalar(
                select(GraphRunRequest)
                .where(
                    or_(
                        and_(
                            GraphRunRequest.status.in_(
                                [
                                    DispatchRequestStatus.PENDING,
                                    DispatchRequestStatus.RETRY_SCHEDULED,
                                ]
                            ),
                            GraphRunRequest.next_attempt_at <= now,
                            GraphRunRequest.attempt_count < GraphRunRequest.max_attempts,
                        ),
                        and_(
                            GraphRunRequest.status == DispatchRequestStatus.LEASED,
                            GraphRunRequest.lease_expires_at <= now,
                        ),
                    ),
                )
                .order_by(GraphRunRequest.next_attempt_at, GraphRunRequest.created_at)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if request is None:
                return None
            run = await self.get_run(session, request.tenant_id, request.graph_run_id)
            if run.status == GraphRunStatus.COMPLETED:
                request.status = DispatchRequestStatus.COMPLETED
                request.completed_at = run.completed_at or now
                request.lease_owner = None
                request.lease_expires_at = None
                return None

            recovering_expired_lease = request.status == DispatchRequestStatus.LEASED
            request.status = DispatchRequestStatus.LEASED
            request.attempt_count = _lease_attempt_number(
                request.attempt_count, recovering_expired_lease=recovering_expired_lease
            )
            request.lease_owner = owner
            request.lease_expires_at = now + timedelta(seconds=lease_seconds)
            return LeasedGraphRunRequest(
                request_id=request.id,
                run=_run_input(run),
                attempt_count=request.attempt_count,
                max_attempts=request.max_attempts,
            )

    async def mark_running(
        self, *, request: LeasedGraphRunRequest, owner: str, now: datetime
    ) -> bool:
        async with self._session_factory() as session, session.begin():
            record = await self._owned_lease(session, request.request_id, owner)
            run = await self.get_run(
                session, record.tenant_id, record.graph_run_id, for_update=True
            )
            if run.status == GraphRunStatus.COMPLETED:
                record.status = DispatchRequestStatus.COMPLETED
                record.completed_at = run.completed_at or now
                record.lease_owner = None
                record.lease_expires_at = None
                return False

            first_start = run.started_at is None
            run.status = GraphRunStatus.RUNNING
            run.started_at = run.started_at or now
            run.last_error_code = None
            if first_start:
                session.add(
                    _run_audit(
                        run=run,
                        event_type=AuditEventType.GRAPH_RUN_STARTED,
                        actor_id=owner,
                    )
                )
            return True

    async def mark_completed(
        self,
        *,
        request: LeasedGraphRunRequest,
        owner: str,
        now: datetime,
        outcome: GraphExecutionOutcome,
    ) -> None:
        async with self._session_factory() as session, session.begin():
            record = await self._owned_lease(session, request.request_id, owner)
            run = await self.get_run(
                session, record.tenant_id, record.graph_run_id, for_update=True
            )
            terminal = outcome.status == "COMPLETED"
            already_completed = run.status == GraphRunStatus.COMPLETED
            run.status = GraphRunStatus.COMPLETED if terminal else GraphRunStatus.INTERRUPTED
            run.started_at = run.started_at or now
            if terminal:
                run.completed_at = run.completed_at or now
            run.last_error_code = None
            record.status = DispatchRequestStatus.COMPLETED
            record.completed_at = record.completed_at or now
            record.lease_owner = None
            record.lease_expires_at = None
            record.last_error_code = None
            record.last_error_message = None
            if terminal and not already_completed:
                session.add(
                    _run_audit(
                        run=run,
                        event_type=AuditEventType.GRAPH_RUN_COMPLETED,
                        actor_id=owner,
                        metadata={"checkpoint_completion_replayed": outcome.replayed},
                    )
                )

    async def mark_failed(
        self,
        *,
        request: LeasedGraphRunRequest,
        owner: str,
        now: datetime,
        error_code: str,
        safe_message: str,
        delay_seconds: float,
        retryable: bool,
    ) -> DispatchRequestStatus:
        async with self._session_factory() as session, session.begin():
            record = await self._owned_lease(session, request.request_id, owner)
            run = await self.get_run(
                session, record.tenant_id, record.graph_run_id, for_update=True
            )
            exhausted = not retryable or record.attempt_count >= record.max_attempts
            record.status = (
                DispatchRequestStatus.EXHAUSTED
                if exhausted
                else DispatchRequestStatus.RETRY_SCHEDULED
            )
            record.next_attempt_at = now + timedelta(seconds=delay_seconds)
            record.lease_owner = None
            record.lease_expires_at = None
            record.last_error_code = error_code[:100]
            record.last_error_message = safe_message[:500]
            run.status = GraphRunStatus.FAILED if exhausted else GraphRunStatus.REQUESTED
            run.last_error_code = error_code[:100]
            if exhausted:
                run.completed_at = now
                session.add(
                    _run_audit(
                        run=run,
                        event_type=AuditEventType.GRAPH_RUN_FAILED,
                        actor_id=owner,
                        metadata={"error_code": error_code[:100]},
                    )
                )
            return record.status

    @staticmethod
    async def _owned_lease(session: AsyncSession, request_id: UUID, owner: str) -> GraphRunRequest:
        record = await session.scalar(
            select(GraphRunRequest)
            .where(
                GraphRunRequest.id == request_id,
                GraphRunRequest.status == DispatchRequestStatus.LEASED,
                GraphRunRequest.lease_owner == owner,
            )
            .with_for_update()
        )
        if record is None:
            raise RuntimeError("Graph-run request lease was lost")
        return record

    @staticmethod
    async def get_run(
        session: AsyncSession, tenant_id: UUID, graph_run_id: UUID, *, for_update: bool = False
    ) -> GraphRun:
        statement = select(GraphRun).where(
            GraphRun.tenant_id == tenant_id,
            GraphRun.id == graph_run_id,
        )
        if for_update:
            statement = statement.with_for_update()
        run = await session.scalar(statement)
        if run is None:
            raise RuntimeError("Graph run does not exist in the request tenant")
        return run


class GraphResumeRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def lease_next(
        self, *, owner: str, now: datetime, lease_seconds: int
    ) -> LeasedGraphResumeRequest | None:
        async with self._session_factory() as session, session.begin():
            request = await session.scalar(
                select(GraphResumeRequest)
                .where(
                    or_(
                        and_(
                            GraphResumeRequest.status.in_(
                                [
                                    DispatchRequestStatus.PENDING,
                                    DispatchRequestStatus.RETRY_SCHEDULED,
                                ]
                            ),
                            GraphResumeRequest.next_attempt_at <= now,
                            GraphResumeRequest.attempt_count < GraphResumeRequest.max_attempts,
                        ),
                        and_(
                            GraphResumeRequest.status == DispatchRequestStatus.LEASED,
                            GraphResumeRequest.lease_expires_at <= now,
                        ),
                    ),
                )
                .order_by(GraphResumeRequest.next_attempt_at, GraphResumeRequest.created_at)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if request is None:
                return None
            run = await GraphRunRepository.get_run(
                session, request.tenant_id, request.graph_run_id, for_update=True
            )
            if run.status == GraphRunStatus.COMPLETED:
                request.status = DispatchRequestStatus.COMPLETED
                request.completed_at = now
                return None
            if await self._complete_already_applied_correction(session, request, run, now):
                return None
            if run.status not in {GraphRunStatus.INTERRUPTED, GraphRunStatus.RUNNING}:
                raise RuntimeError("Graph resume request is not attached to an interrupted run")
            recovering_expired_lease = request.status == DispatchRequestStatus.LEASED
            request.status = DispatchRequestStatus.LEASED
            request.attempt_count = _lease_attempt_number(
                request.attempt_count, recovering_expired_lease=recovering_expired_lease
            )
            request.lease_owner = owner
            request.lease_expires_at = now + timedelta(seconds=lease_seconds)
            return LeasedGraphResumeRequest(
                request_id=request.id,
                run=_run_input(run),
                payload=request.resume_payload,
                attempt_count=request.attempt_count,
                max_attempts=request.max_attempts,
            )

    async def mark_running(
        self, *, request: LeasedGraphResumeRequest, owner: str, now: datetime
    ) -> bool:
        async with self._session_factory() as session, session.begin():
            record = await self._owned(session, request.request_id, owner)
            run = await GraphRunRepository.get_run(
                session, record.tenant_id, record.graph_run_id, for_update=True
            )
            if run.status == GraphRunStatus.COMPLETED:
                record.status = DispatchRequestStatus.COMPLETED
                record.completed_at = now
                return False
            if run.status not in {GraphRunStatus.INTERRUPTED, GraphRunStatus.RUNNING}:
                raise RuntimeError("Graph run is not interrupted")
            run.status = GraphRunStatus.RUNNING
            return True

    async def mark_completed(
        self,
        *,
        request: LeasedGraphResumeRequest,
        owner: str,
        now: datetime,
        outcome: GraphExecutionOutcome,
    ) -> None:
        async with self._session_factory() as session, session.begin():
            record = await self._owned(session, request.request_id, owner)
            run = await GraphRunRepository.get_run(
                session, record.tenant_id, record.graph_run_id, for_update=True
            )
            run.status = (
                GraphRunStatus.COMPLETED
                if outcome.status == "COMPLETED"
                else GraphRunStatus.INTERRUPTED
            )
            if outcome.status == "COMPLETED":
                run.completed_at = run.completed_at or now
            record.status = DispatchRequestStatus.COMPLETED
            record.completed_at = now
            record.lease_owner = None
            record.lease_expires_at = None
            session.add(
                AuditEvent(
                    id=UUID(str(request.request_id)),
                    tenant_id=run.tenant_id,
                    document_id=run.document_id,
                    graph_run_id=run.id,
                    event_type=AuditEventType.GRAPH_RESUMED,
                    actor_type=ActorType.WORKER,
                    actor_id=owner,
                    correlation_id=run.correlation_id,
                    sanitized_metadata={"resume_request_id": str(request.request_id)},
                )
            )

    async def mark_failed(
        self,
        *,
        request: LeasedGraphResumeRequest,
        owner: str,
        now: datetime,
        error_code: str,
        safe_message: str,
        delay_seconds: float,
        retryable: bool,
    ) -> DispatchRequestStatus:
        async with self._session_factory() as session, session.begin():
            record = await self._owned(session, request.request_id, owner)
            run = await GraphRunRepository.get_run(
                session, record.tenant_id, record.graph_run_id, for_update=True
            )
            exhausted = not retryable or record.attempt_count >= record.max_attempts
            record.status = (
                DispatchRequestStatus.EXHAUSTED
                if exhausted
                else DispatchRequestStatus.RETRY_SCHEDULED
            )
            record.next_attempt_at = now + timedelta(seconds=delay_seconds)
            record.lease_owner = None
            record.lease_expires_at = None
            record.last_error_code = error_code[:100]
            record.last_error_message = safe_message[:500]
            run.status = GraphRunStatus.FAILED if exhausted else GraphRunStatus.INTERRUPTED
            run.last_error_code = error_code[:100]
            if exhausted:
                run.completed_at = now
                session.add(
                    _run_audit(
                        run=run,
                        event_type=AuditEventType.GRAPH_RUN_FAILED,
                        actor_id=owner,
                        metadata={
                            "error_code": error_code[:100],
                            "resume_request_id": str(request.request_id),
                        },
                    )
                )
            return record.status

    @staticmethod
    async def _complete_already_applied_correction(
        session: AsyncSession,
        request: GraphResumeRequest,
        run: GraphRun,
        now: datetime,
    ) -> bool:
        review_cycle = request.resume_payload.get("review_cycle")
        if type(review_cycle) is not int:
            return False
        task = await session.scalar(
            select(ReviewTask).where(
                ReviewTask.tenant_id == request.tenant_id,
                ReviewTask.graph_run_id == request.graph_run_id,
            )
        )
        if task is None or task.cycle <= review_cycle:
            return False
        request.status = DispatchRequestStatus.COMPLETED
        request.completed_at = request.completed_at or now
        request.lease_owner = None
        request.lease_expires_at = None
        request.last_error_code = None
        request.last_error_message = None
        run.status = GraphRunStatus.INTERRUPTED
        run.last_error_code = None
        audit_exists = await session.scalar(
            select(AuditEvent.id).where(
                AuditEvent.id == request.id,
                AuditEvent.tenant_id == request.tenant_id,
            )
        )
        if audit_exists is None:
            session.add(
                AuditEvent(
                    id=request.id,
                    tenant_id=run.tenant_id,
                    document_id=run.document_id,
                    graph_run_id=run.id,
                    event_type=AuditEventType.GRAPH_RESUMED,
                    actor_type=ActorType.WORKER,
                    actor_id="orchestrator-recovery",
                    correlation_id=run.correlation_id,
                    sanitized_metadata={
                        "resume_request_id": str(request.id),
                        "checkpoint_completion_reconciled": True,
                    },
                )
            )
        return True

    @staticmethod
    async def _owned(session: AsyncSession, request_id: UUID, owner: str) -> GraphResumeRequest:
        record = await session.scalar(
            select(GraphResumeRequest)
            .where(
                GraphResumeRequest.id == request_id,
                GraphResumeRequest.status == DispatchRequestStatus.LEASED,
                GraphResumeRequest.lease_owner == owner,
            )
            .with_for_update()
        )
        if record is None:
            raise RuntimeError("Graph-resume request lease was lost")
        return record


def build_async_engine(settings: Settings) -> AsyncEngine:
    return create_async_engine(settings.database_url, pool_pre_ping=True)


def build_async_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False, autoflush=False)


def _run_input(run: GraphRun) -> GraphRunInput:
    return GraphRunInput(
        graph_run_id=run.id,
        graph_version=run.graph_version,
        thread_id=run.thread_id,
        tenant_id=run.tenant_id,
        document_id=run.document_id,
        correlation_id=run.correlation_id,
    )


def _run_audit(
    *,
    run: GraphRun,
    event_type: AuditEventType,
    actor_id: str,
    metadata: dict[str, object] | None = None,
) -> AuditEvent:
    return AuditEvent(
        tenant_id=run.tenant_id,
        document_id=run.document_id,
        graph_run_id=run.id,
        event_type=event_type,
        actor_type=ActorType.WORKER,
        actor_id=actor_id,
        correlation_id=run.correlation_id,
        sanitized_metadata=metadata or {"graph_version": run.graph_version},
    )


def _lease_attempt_number(attempt_count: int, *, recovering_expired_lease: bool) -> int:
    return attempt_count if recovering_expired_lease else attempt_count + 1
