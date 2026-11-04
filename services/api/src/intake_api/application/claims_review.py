"""Claim work queues, the claim workbench read model, and audited reviewer re-routes (ADR 0008).

Queues come from the immutable `claim-routing-v1` decision of the task's latest authoritative
validation. A reviewer may re-route a claim with a reason; the latest re-route then wins until a
revalidation records a newer rules decision. The model never touches either.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from intake_api.application.identity import RequestIdentity
from intake_api.application.problems import (
    AuthorizationDenied,
    InvalidUpload,
    ResourceNotFound,
    ReviewConflict,
)
from intake_api.domain.enums import ActorType, AuditEventType, ReviewQueue, UserRole
from intake_api.domain.models import (
    AuditEvent,
    ClaimReroute,
    ClaimRoutingDecision,
    Document,
    DocumentPageSegment,
    DocumentVersion,
    ExtractionCandidate,
    ExtractionFieldEvidence,
    ReviewedDocumentVersion,
    ReviewTask,
    ReviewTaskClaim,
    RuleDefinition,
    ValidationResult,
)
from intake_api.infrastructure.config import Settings

ServiceLevelStatus = Literal["ON_TRACK", "DUE_SOON", "OVERDUE", "MET", "MISSED"]
READ_ROLES = {UserRole.REVIEWER, UserRole.APPROVER, UserRole.AUDITOR}
REROUTE_ROLES = {UserRole.REVIEWER, UserRole.APPROVER}
CLAIM_RULESET_VERSION = "claim-rules-v1"
DUE_SOON_MINUTES = 60
DUE_SOON_SHARE = 0.75


class ServiceLevel(BaseModel):
    target_minutes: int
    status: ServiceLevelStatus
    minutes_remaining: int


class ClaimRoute(BaseModel):
    review_queue: ReviewQueue
    reason_code: str
    flags: list[str]
    routed_by: Literal["RULES", "REVIEWER"]
    routing_version: str | None


class ClaimQueueItem(BaseModel):
    review_task_id: UUID
    document_id: UUID
    task_status: str
    claimed_by_user_id: UUID | None
    original_filename: str | None
    policy_number: str | None
    named_insured: str | None
    vehicle: str | None
    loss_date: str | None
    cause_of_loss: str | None
    estimate_total: str | None
    route: ClaimRoute
    uploaded_at: datetime
    age_minutes: int
    service_level: ServiceLevel


class ClaimQueueView(BaseModel):
    generated_at: datetime
    queue: ReviewQueue | None
    items: list[ClaimQueueItem]
    counts_by_queue: dict[str, int]
    counts_by_service_level: dict[str, int]


class RuleOutcomeView(BaseModel):
    rule_id: str
    name: str | None
    outcome: str
    reason_code: str | None


class FieldView(BaseModel):
    name: str
    value: object
    confidence: str | None
    source_page: int | None
    evidence_excerpt: str | None
    reviewed: bool


class SegmentView(BaseModel):
    page_number: int
    label: str
    confidence: str


class RerouteView(BaseModel):
    from_queue: ReviewQueue
    to_queue: ReviewQueue
    reason: str
    rerouted_by_user_id: UUID
    created_at: datetime


class ClaimWorkbenchView(BaseModel):
    item: ClaimQueueItem
    review_cycle: int
    rules: list[RuleOutcomeView]
    fields: list[FieldView]
    segments: list[SegmentView]
    reroutes: list[RerouteView]


class RerouteCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    to_queue: ReviewQueue
    reason: Annotated[str, Field(min_length=1, max_length=500)]


def service_level(
    *,
    target_minutes: int,
    uploaded_at: datetime,
    first_reviewed_at: datetime | None,
    now: datetime,
) -> ServiceLevel:
    """Time to first human review against a per-queue target."""
    if first_reviewed_at is not None:
        took = _minutes(first_reviewed_at - uploaded_at)
        return ServiceLevel(
            target_minutes=target_minutes,
            status="MET" if took <= target_minutes else "MISSED",
            minutes_remaining=target_minutes - took,
        )
    age = _minutes(now - uploaded_at)
    remaining = target_minutes - age
    if remaining < 0:
        status: ServiceLevelStatus = "OVERDUE"
    elif remaining <= DUE_SOON_MINUTES or age >= target_minutes * DUE_SOON_SHARE:
        status = "DUE_SOON"
    else:
        status = "ON_TRACK"
    return ServiceLevel(target_minutes=target_minutes, status=status, minutes_remaining=remaining)


def target_minutes(settings: Settings, queue: ReviewQueue) -> int:
    hours = {
        ReviewQueue.FAST_TRACK: settings.claims_sla_fast_track_hours,
        ReviewQueue.BODILY_INJURY_ADJUSTER: settings.claims_sla_bodily_injury_hours,
        ReviewQueue.BELOW_DEDUCTIBLE_NOTICE: settings.claims_sla_notice_hours,
    }.get(queue, settings.claims_sla_standard_hours)
    return hours * 60


class ClaimsReviewService:
    def __init__(self, settings: Settings, sessions: sessionmaker[Session]) -> None:
        self._settings = settings
        self._sessions = sessions

    def list_queue(
        self,
        identity: RequestIdentity,
        *,
        queue: ReviewQueue | None = None,
        now: datetime | None = None,
    ) -> ClaimQueueView:
        _require_role(identity, READ_ROLES)
        current = now or datetime.now(UTC)
        with self._sessions() as session:
            tasks = session.scalars(
                select(ReviewTask)
                .join(
                    ExtractionCandidate,
                    (ExtractionCandidate.id == ReviewTask.extraction_candidate_id)
                    & (ExtractionCandidate.tenant_id == ReviewTask.tenant_id),
                )
                .where(
                    ReviewTask.tenant_id == identity.tenant_id,
                    ReviewTask.status == "OPEN",
                    ExtractionCandidate.document_type == "CLAIM_PACKET",
                )
            ).all()
            items = [
                item for task in tasks if (item := self._item(session, task, current)) is not None
            ]
        counts_by_queue = {member.value: 0 for member in ReviewQueue}
        counts_by_level: dict[str, int] = {"OVERDUE": 0, "DUE_SOON": 0, "ON_TRACK": 0}
        for item in items:
            counts_by_queue[item.route.review_queue.value] += 1
            if item.service_level.status in counts_by_level:
                counts_by_level[item.service_level.status] += 1
        selected = [item for item in items if queue is None or item.route.review_queue == queue]
        selected.sort(key=_urgency)
        return ClaimQueueView(
            generated_at=current,
            queue=queue,
            items=selected,
            counts_by_queue=counts_by_queue,
            counts_by_service_level=counts_by_level,
        )

    def workbench(
        self, task_id: UUID, identity: RequestIdentity, *, now: datetime | None = None
    ) -> ClaimWorkbenchView:
        _require_role(identity, READ_ROLES)
        with self._sessions() as session:
            task = _claim_task(session, task_id, identity.tenant_id)
            item = self._item(session, task, now or datetime.now(UTC))
            if item is None:
                raise ResourceNotFound
            return ClaimWorkbenchView(
                item=item,
                review_cycle=task.cycle,
                rules=_rules(session, task),
                fields=_fields(session, task),
                segments=_segments(session, task),
                reroutes=[
                    RerouteView(
                        from_queue=reroute.from_queue,
                        to_queue=reroute.to_queue,
                        reason=reroute.reason,
                        rerouted_by_user_id=reroute.rerouted_by_user_id,
                        created_at=reroute.created_at,
                    )
                    for reroute in _reroutes(session, task)
                ],
            )

    def reroute(
        self, task_id: UUID, command: RerouteCommand, identity: RequestIdentity
    ) -> ClaimRoute:
        _require_role(identity, REROUTE_ROLES)
        with self._sessions() as session, session.begin():
            task = _claim_task(session, task_id, identity.tenant_id, lock=True)
            if task.status != "OPEN" or task.completed_at is not None:
                raise ReviewConflict("Only an open claim review task can be re-routed.")
            if task.claimed_by_user_id not in {None, identity.user_id}:
                raise ReviewConflict("The claim is claimed by another reviewer.")
            current = _effective_route(session, task)
            if current is None:
                raise ReviewConflict("The claim has no routing decision to change.")
            if current.review_queue == command.to_queue:
                raise InvalidUpload("The claim is already in that queue.", "invalid-reroute")
            reason = command.reason.strip()
            if not reason:
                raise InvalidUpload("A re-route reason is required.", "invalid-reroute")
            session.add(
                ClaimReroute(
                    tenant_id=identity.tenant_id,
                    review_task_id=task.id,
                    review_cycle=task.cycle,
                    from_queue=current.review_queue,
                    to_queue=command.to_queue,
                    reason=reason,
                    rerouted_by_user_id=identity.user_id,
                )
            )
            if task.claimed_by_user_id is not None:
                # Hand the claim to the new queue: release the actor's own claim.
                claim = session.scalar(
                    select(ReviewTaskClaim)
                    .where(
                        ReviewTaskClaim.tenant_id == identity.tenant_id,
                        ReviewTaskClaim.review_task_id == task.id,
                        ReviewTaskClaim.released_at.is_(None),
                    )
                    .with_for_update()
                )
                if claim is not None:
                    claim.released_at = datetime.now(UTC)
                    claim.release_reason = "REROUTED"
                task.claimed_by_user_id = None
            task.lock_version += 1
            session.add(
                AuditEvent(
                    tenant_id=task.tenant_id,
                    document_id=task.document_id,
                    graph_run_id=task.graph_run_id,
                    event_type=AuditEventType.CLAIM_REROUTED,
                    actor_type=ActorType.USER,
                    actor_id=str(identity.user_id),
                    correlation_id=str(task.graph_run_id),
                    sanitized_metadata={
                        "review_task_id": str(task.id),
                        "review_cycle": task.cycle,
                        "from_queue": current.review_queue.value,
                        "to_queue": command.to_queue.value,
                    },
                )
            )
            return ClaimRoute(
                review_queue=command.to_queue,
                reason_code="REVIEWER_REROUTE",
                flags=current.flags,
                routed_by="REVIEWER",
                routing_version=None,
            )

    def _item(self, session: Session, task: ReviewTask, now: datetime) -> ClaimQueueItem | None:
        route = _effective_route(session, task)
        if route is None:
            return None
        document = session.scalar(
            select(Document).where(
                Document.tenant_id == task.tenant_id, Document.id == task.document_id
            )
        )
        if document is None:
            return None
        filename = session.scalar(
            select(DocumentVersion.original_filename).where(
                DocumentVersion.tenant_id == task.tenant_id,
                DocumentVersion.id == document.current_version_id,
            )
        )
        first_reviewed = session.scalar(
            select(func.min(ReviewTaskClaim.claimed_at)).where(
                ReviewTaskClaim.tenant_id == task.tenant_id,
                ReviewTaskClaim.review_task_id == task.id,
            )
        )
        data = _business_data(session, task)
        vehicle = " ".join(
            str(part)
            for part in (
                data.get("vehicle_year"),
                data.get("vehicle_make"),
                data.get("vehicle_model"),
            )
            if part
        )
        return ClaimQueueItem(
            review_task_id=task.id,
            document_id=task.document_id,
            task_status=task.status,
            claimed_by_user_id=task.claimed_by_user_id,
            original_filename=filename,
            policy_number=_text(data.get("policy_number")),
            named_insured=_text(data.get("named_insured")),
            vehicle=vehicle or None,
            loss_date=_text(data.get("loss_date")),
            cause_of_loss=_text(data.get("cause_of_loss")),
            estimate_total=_text(data.get("estimate_total")),
            route=route,
            uploaded_at=document.created_at,
            age_minutes=_minutes(now - document.created_at),
            service_level=service_level(
                target_minutes=target_minutes(self._settings, route.review_queue),
                uploaded_at=document.created_at,
                first_reviewed_at=first_reviewed,
                now=now,
            ),
        )


def _effective_route(session: Session, task: ReviewTask) -> ClaimRoute | None:
    decision = session.scalar(
        select(ClaimRoutingDecision)
        .where(
            ClaimRoutingDecision.tenant_id == task.tenant_id,
            ClaimRoutingDecision.graph_run_id == task.graph_run_id,
        )
        .order_by(ClaimRoutingDecision.created_at.desc())
        .limit(1)
    )
    if decision is None:
        return None
    reroute = session.scalar(
        select(ClaimReroute)
        .where(
            ClaimReroute.tenant_id == task.tenant_id,
            ClaimReroute.review_task_id == task.id,
            ClaimReroute.created_at >= decision.created_at,
        )
        .order_by(ClaimReroute.created_at.desc())
        .limit(1)
    )
    if reroute is not None:
        return ClaimRoute(
            review_queue=reroute.to_queue,
            reason_code="REVIEWER_REROUTE",
            flags=list(decision.flags),
            routed_by="REVIEWER",
            routing_version=None,
        )
    return ClaimRoute(
        review_queue=decision.review_queue,
        reason_code=decision.reason_code,
        flags=list(decision.flags),
        routed_by="RULES",
        routing_version=decision.routing_version,
    )


def _business_data(session: Session, task: ReviewTask) -> dict[str, object]:
    reviewed = session.scalar(
        select(ReviewedDocumentVersion)
        .where(
            ReviewedDocumentVersion.tenant_id == task.tenant_id,
            ReviewedDocumentVersion.review_task_id == task.id,
        )
        .order_by(ReviewedDocumentVersion.review_cycle.desc())
        .limit(1)
    )
    if reviewed is not None:
        return dict(reviewed.business_data)
    candidate = session.scalar(
        select(ExtractionCandidate).where(
            ExtractionCandidate.tenant_id == task.tenant_id,
            ExtractionCandidate.id == task.extraction_candidate_id,
        )
    )
    return dict(candidate.candidate_data) if candidate is not None else {}


def _rules(session: Session, task: ReviewTask) -> list[RuleOutcomeView]:
    if task.validation_run_id is None:
        return []
    names = dict(
        session.execute(
            select(RuleDefinition.rule_id, RuleDefinition.name).where(
                RuleDefinition.tenant_id == task.tenant_id,
                RuleDefinition.version == CLAIM_RULESET_VERSION,
            )
        )
        .tuples()
        .all()
    )
    results = session.scalars(
        select(ValidationResult)
        .where(
            ValidationResult.tenant_id == task.tenant_id,
            ValidationResult.validation_run_id == task.validation_run_id,
        )
        .order_by(ValidationResult.rule_id)
    )
    return [
        RuleOutcomeView(
            rule_id=result.rule_id,
            name=names.get(result.rule_id),
            outcome=result.outcome,
            reason_code=result.reason_code,
        )
        for result in results
    ]


def _fields(session: Session, task: ReviewTask) -> list[FieldView]:
    candidate = session.scalar(
        select(ExtractionCandidate).where(
            ExtractionCandidate.tenant_id == task.tenant_id,
            ExtractionCandidate.id == task.extraction_candidate_id,
        )
    )
    if candidate is None:
        return []
    extracted = dict(candidate.candidate_data)
    current = _business_data(session, task)
    evidence = {
        item.field_name: item
        for item in session.scalars(
            select(ExtractionFieldEvidence).where(
                ExtractionFieldEvidence.tenant_id == task.tenant_id,
                ExtractionFieldEvidence.candidate_id == candidate.id,
            )
        )
    }
    names = list(dict.fromkeys([*evidence, *extracted, *current]))
    return [
        FieldView(
            name=name,
            value=current.get(name),
            confidence=str(evidence[name].confidence) if name in evidence else None,
            source_page=evidence[name].source_page if name in evidence else None,
            evidence_excerpt=evidence[name].evidence_excerpt if name in evidence else None,
            reviewed=current.get(name) != extracted.get(name),
        )
        for name in names
    ]


def _segments(session: Session, task: ReviewTask) -> list[SegmentView]:
    latest_run = session.scalar(
        select(DocumentPageSegment.agent_run_id)
        .where(
            DocumentPageSegment.tenant_id == task.tenant_id,
            DocumentPageSegment.document_id == task.document_id,
        )
        .order_by(DocumentPageSegment.created_at.desc())
        .limit(1)
    )
    if latest_run is None:
        return []
    return [
        SegmentView(
            page_number=segment.page_number,
            label=segment.segment_label.value,
            confidence=str(segment.confidence),
        )
        for segment in session.scalars(
            select(DocumentPageSegment)
            .where(
                DocumentPageSegment.tenant_id == task.tenant_id,
                DocumentPageSegment.agent_run_id == latest_run,
            )
            .order_by(DocumentPageSegment.page_number)
        )
    ]


def _reroutes(session: Session, task: ReviewTask) -> list[ClaimReroute]:
    return list(
        session.scalars(
            select(ClaimReroute)
            .where(
                ClaimReroute.tenant_id == task.tenant_id,
                ClaimReroute.review_task_id == task.id,
            )
            .order_by(ClaimReroute.created_at)
        )
    )


def _claim_task(
    session: Session, task_id: UUID, tenant_id: UUID, *, lock: bool = False
) -> ReviewTask:
    statement = select(ReviewTask).where(
        ReviewTask.id == task_id, ReviewTask.tenant_id == tenant_id
    )
    if lock:
        statement = statement.with_for_update()
    task = session.scalar(statement)
    if task is None or task.extraction_candidate_id is None:
        raise ResourceNotFound
    document_type = session.scalar(
        select(ExtractionCandidate.document_type).where(
            ExtractionCandidate.tenant_id == tenant_id,
            ExtractionCandidate.id == task.extraction_candidate_id,
        )
    )
    if document_type != "CLAIM_PACKET":
        raise ResourceNotFound
    return task


def _urgency(item: ClaimQueueItem) -> tuple[int, int]:
    open_first = 0 if item.service_level.status in {"OVERDUE", "DUE_SOON", "ON_TRACK"} else 1
    return open_first, item.service_level.minutes_remaining


def _minutes(delta: timedelta) -> int:
    return int(delta.total_seconds() // 60)


def _text(value: object) -> str | None:
    return None if value is None else str(value)


def _require_role(identity: RequestIdentity, roles: set[UserRole]) -> None:
    if identity.role not in roles:
        raise AuthorizationDenied
