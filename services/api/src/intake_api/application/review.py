from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from intake_api.application.identity import RequestIdentity
from intake_api.application.problems import (
    AuthorizationDenied,
    InvalidUpload,
    ResourceNotFound,
    ReviewConflict,
)
from intake_api.domain.enums import ActorType, AuditEventType, DispatchRequestStatus, UserRole
from intake_api.domain.models import (
    AuditEvent,
    ExtractionCandidate,
    FieldCorrection,
    GraphResumeRequest,
    GraphRun,
    ReviewDecision,
    ReviewedDocumentVersion,
    ReviewTask,
    ReviewTaskClaim,
)
from intake_api.infrastructure.config import Settings

REVIEW_FIELDS = {
    "invoice_number",
    "invoice_date",
    "due_date",
    "supplier_name",
    "supplier_tax_id",
    "purchase_order_number",
    "currency",
    "subtotal",
    "tax",
    "total",
}
MONEY_FIELDS = {"subtotal", "tax", "total"}
DATE_FIELDS = {"invoice_date", "due_date"}
STRING_FIELDS = REVIEW_FIELDS - MONEY_FIELDS - DATE_FIELDS


class FieldCorrectionCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field: str = Field(min_length=1, max_length=64)
    new_value: Annotated[str, Field(max_length=500)] | None
    reason: str = Field(min_length=1, max_length=500)


class CompleteReviewCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["APPROVE", "REJECT", "REQUEST_CORRECTION"]
    reason: str = Field(min_length=1, max_length=1000)
    field_corrections: list[FieldCorrectionCommand] = Field(
        default_factory=lambda: list[FieldCorrectionCommand](), max_length=10
    )


class ReviewTaskView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    document_id: UUID
    graph_run_id: UUID
    status: str
    cycle: int
    claimed_by_user_id: UUID | None
    extraction_candidate_id: UUID | None
    validation_run_id: UUID | None
    confidence_run_id: UUID | None
    decision: str | None


class ReviewCompletionResult(BaseModel):
    review_task_id: UUID
    reviewed_document_version_id: UUID
    graph_resume_request_id: UUID
    decision: str


class ReviewService:
    def __init__(self, settings: Settings, sessions: sessionmaker[Session]) -> None:
        self._settings = settings
        self._sessions = sessions

    def list_tasks(self, identity: RequestIdentity) -> list[ReviewTaskView]:
        _require_role(
            identity,
            {
                UserRole.REVIEWER,
                UserRole.APPROVER,
                UserRole.AUDITOR,
            },
        )
        with self._sessions() as session:
            tasks = session.scalars(
                select(ReviewTask)
                .where(ReviewTask.tenant_id == identity.tenant_id)
                .order_by(ReviewTask.created_at.desc())
            )
            return [ReviewTaskView.model_validate(task) for task in tasks]

    def get_task(self, task_id: UUID, identity: RequestIdentity) -> ReviewTaskView:
        _require_role(
            identity,
            {
                UserRole.REVIEWER,
                UserRole.APPROVER,
                UserRole.AUDITOR,
            },
        )
        with self._sessions() as session:
            return ReviewTaskView.model_validate(_task(session, task_id, identity.tenant_id))

    def claim(self, task_id: UUID, identity: RequestIdentity) -> ReviewTaskView:
        _require_role(identity, {UserRole.REVIEWER, UserRole.APPROVER})
        with self._sessions() as session, session.begin():
            task = _task(session, task_id, identity.tenant_id, lock=True)
            if task.status != "OPEN":
                raise ReviewConflict("Only an open review task can be claimed.")
            if task.claimed_by_user_id not in {None, identity.user_id}:
                raise ReviewConflict("The review task is already claimed by another actor.")
            if task.claimed_by_user_id is None:
                task.claimed_by_user_id = identity.user_id
                task.lock_version += 1
                session.add(
                    ReviewTaskClaim(
                        tenant_id=identity.tenant_id,
                        review_task_id=task.id,
                        user_id=identity.user_id,
                    )
                )
                session.add(_audit(task, identity, AuditEventType.REVIEW_TASK_CLAIMED, {}))
            return ReviewTaskView.model_validate(task)

    def unclaim(self, task_id: UUID, identity: RequestIdentity) -> ReviewTaskView:
        _require_role(identity, {UserRole.REVIEWER, UserRole.APPROVER})
        with self._sessions() as session, session.begin():
            task = _task(session, task_id, identity.tenant_id, lock=True)
            if task.claimed_by_user_id is None:
                return ReviewTaskView.model_validate(task)
            if task.claimed_by_user_id != identity.user_id:
                raise AuthorizationDenied("Only the current claimant may unclaim this task.")
            claim = session.scalar(
                select(ReviewTaskClaim)
                .where(
                    ReviewTaskClaim.tenant_id == identity.tenant_id,
                    ReviewTaskClaim.review_task_id == task.id,
                    ReviewTaskClaim.released_at.is_(None),
                )
                .order_by(ReviewTaskClaim.claimed_at.desc())
                .with_for_update()
            )
            if claim:
                claim.released_at = datetime.now(UTC)
                claim.release_reason = "UNCLAIMED"
            task.claimed_by_user_id = None
            task.lock_version += 1
            session.add(_audit(task, identity, AuditEventType.REVIEW_TASK_UNCLAIMED, {}))
            return ReviewTaskView.model_validate(task)

    def complete(
        self, task_id: UUID, command: CompleteReviewCommand, identity: RequestIdentity
    ) -> ReviewCompletionResult:
        permitted = {UserRole.REVIEWER, UserRole.APPROVER}
        _require_role(identity, permitted)
        if command.decision in {"APPROVE", "REJECT"} and identity.role != UserRole.APPROVER:
            raise AuthorizationDenied("Only an approver may approve or reject a document.")
        with self._sessions() as session, session.begin():
            task = _task(session, task_id, identity.tenant_id, lock=True)
            if task.status != "OPEN" or task.completed_at is not None:
                raise ReviewConflict("The review task has already been completed.")
            if task.claimed_by_user_id != identity.user_id:
                raise ReviewConflict("The actor must claim the review task before completion.")
            graph_run = session.scalar(
                select(GraphRun)
                .where(GraphRun.id == task.graph_run_id, GraphRun.tenant_id == identity.tenant_id)
                .with_for_update()
            )
            if graph_run is None or graph_run.status != "INTERRUPTED":
                raise ReviewConflict("The graph run is not waiting for human review.")
            candidate = None
            if task.extraction_candidate_id is not None:
                candidate = session.scalar(
                    select(ExtractionCandidate).where(
                        ExtractionCandidate.id == task.extraction_candidate_id,
                        ExtractionCandidate.tenant_id == identity.tenant_id,
                    )
                )
                if candidate is None:
                    raise ReviewConflict("The extraction candidate is unavailable.")
            prior_version = session.scalar(
                select(ReviewedDocumentVersion)
                .where(
                    ReviewedDocumentVersion.tenant_id == identity.tenant_id,
                    ReviewedDocumentVersion.review_task_id == task.id,
                    ReviewedDocumentVersion.review_cycle < task.cycle,
                )
                .order_by(ReviewedDocumentVersion.review_cycle.desc())
                .limit(1)
            )
            corrected = dict(
                prior_version.business_data
                if prior_version
                else candidate.candidate_data
                if candidate
                else {}
            )
            normalized: dict[str, object] = {}
            previous_values: dict[str, object] = {}
            for correction in command.field_corrections:
                if correction.field in normalized:
                    raise InvalidUpload("A field may be corrected only once.", "invalid-correction")
                normalized[correction.field] = _normalize_value(
                    correction.field, correction.new_value
                )
                previous_values[correction.field] = corrected.get(correction.field)
            corrected.update(normalized)
            version_id = _stable_uuid(f"review-version:{task.id}:{task.cycle}")
            version = ReviewedDocumentVersion(
                id=version_id,
                tenant_id=identity.tenant_id,
                document_id=task.document_id,
                review_task_id=task.id,
                extraction_candidate_id=candidate.id if candidate else None,
                review_cycle=task.cycle,
                business_data=corrected,
                created_by_user_id=identity.user_id,
            )
            session.add(version)
            session.flush()
            for correction in command.field_corrections:
                session.add(
                    FieldCorrection(
                        id=_stable_uuid(f"field-correction:{version_id}:{correction.field}"),
                        tenant_id=identity.tenant_id,
                        reviewed_document_version_id=version_id,
                        field_name=correction.field,
                        old_value={"value": previous_values[correction.field]},
                        new_value={"value": normalized[correction.field]},
                        reason=correction.reason,
                        corrected_by_user_id=identity.user_id,
                    )
                )
                session.add(
                    _audit(
                        task,
                        identity,
                        AuditEventType.FIELD_CORRECTED,
                        {"field": correction.field, "review_cycle": task.cycle},
                    )
                )
            session.add(
                ReviewDecision(
                    id=_stable_uuid(f"review-decision:{task.id}:{task.cycle}"),
                    tenant_id=identity.tenant_id,
                    review_task_id=task.id,
                    reviewed_document_version_id=version_id,
                    review_cycle=task.cycle,
                    decision=command.decision,
                    reason=command.reason,
                    decided_by_user_id=identity.user_id,
                )
            )
            task.status = "COMPLETED"
            task.decision = command.decision
            task.completed_at = datetime.now(UTC)
            task.response_payload = {
                "decision": command.decision,
                "reason_codes": [f"HUMAN_{command.decision}"],
                "corrected_fields": sorted(normalized),
            }
            task.lock_version += 1
            resume_id = _stable_uuid(f"graph-resume:{task.id}:{task.cycle}")
            resume_payload: dict[str, object] = {
                "decision": command.decision,
                "actor_id": str(identity.user_id),
                "reason_codes": [f"HUMAN_{command.decision}"],
                "corrections": normalized,
                "review_task_id": str(task.id),
                "reviewed_document_version_id": str(version_id),
                "review_cycle": task.cycle,
            }
            session.add(
                GraphResumeRequest(
                    id=resume_id,
                    tenant_id=identity.tenant_id,
                    document_id=task.document_id,
                    graph_run_id=task.graph_run_id,
                    correlation_id=graph_run.correlation_id,
                    resume_key=f"review:{task.id}:{task.cycle}",
                    resume_payload=resume_payload,
                    status=DispatchRequestStatus.PENDING,
                    attempt_count=0,
                    max_attempts=self._settings.graph_request_max_attempts,
                    next_attempt_at=datetime.now(UTC),
                )
            )
            session.add(
                _audit(
                    task,
                    identity,
                    AuditEventType.REVIEW_COMPLETED,
                    {"decision": command.decision, "review_cycle": task.cycle},
                )
            )
            return ReviewCompletionResult(
                review_task_id=task.id,
                reviewed_document_version_id=version_id,
                graph_resume_request_id=resume_id,
                decision=command.decision,
            )


def _task(session: Session, task_id: UUID, tenant_id: UUID, *, lock: bool = False) -> ReviewTask:
    statement = select(ReviewTask).where(
        ReviewTask.id == task_id, ReviewTask.tenant_id == tenant_id
    )
    if lock:
        statement = statement.with_for_update()
    task = session.scalar(statement)
    if task is None:
        raise ResourceNotFound
    return task


def _require_role(identity: RequestIdentity, roles: set[UserRole]) -> None:
    if identity.role not in roles:
        raise AuthorizationDenied


def _normalize_value(field: str, value: str | None) -> object:
    if field not in REVIEW_FIELDS:
        raise InvalidUpload(f"Unsupported correction field: {field}", "invalid-correction")
    if value is None:
        return None
    text = value.strip()
    if field in MONEY_FIELDS:
        try:
            amount = Decimal(text)
        except InvalidOperation as exc:
            raise InvalidUpload(f"{field} must be a decimal string.", "invalid-correction") from exc
        if not amount.is_finite():
            raise InvalidUpload(f"{field} must be finite.", "invalid-correction")
        return str(amount)
    if field in DATE_FIELDS:
        try:
            return date.fromisoformat(text).isoformat()
        except ValueError as exc:
            raise InvalidUpload(f"{field} must be an ISO date.", "invalid-correction") from exc
    if field == "currency":
        if len(text) != 3 or not text.isalpha():
            raise InvalidUpload("currency must be a three-letter code.", "invalid-correction")
        return text.upper()
    if field in STRING_FIELDS and (not text or len(text) > 200):
        raise InvalidUpload(f"{field} must contain 1 to 200 characters.", "invalid-correction")
    return text


def _stable_uuid(material: str) -> UUID:
    from uuid import NAMESPACE_URL, uuid5

    return uuid5(NAMESPACE_URL, f"document-intake:{material}")


def _audit(
    task: ReviewTask,
    identity: RequestIdentity,
    event_type: AuditEventType,
    metadata: dict[str, object],
) -> AuditEvent:
    return AuditEvent(
        tenant_id=task.tenant_id,
        document_id=task.document_id,
        graph_run_id=task.graph_run_id,
        event_type=event_type,
        actor_type=ActorType.USER,
        actor_id=str(identity.user_id),
        correlation_id=str(task.graph_run_id),
        sanitized_metadata={"review_task_id": str(task.id), **metadata},
    )
