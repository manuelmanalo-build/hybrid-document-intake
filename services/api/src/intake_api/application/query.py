from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.sql.elements import ColumnElement

from intake_api.application.identity import RequestIdentity
from intake_api.application.problems import AuthorizationDenied, ResourceNotFound
from intake_api.domain.enums import UserRole
from intake_api.domain.models import (
    AgentRecommendation,
    AgentRun,
    AgentToolCall,
    AuditEvent,
    Base,
    ConfidenceComponent,
    ConfidenceRun,
    DeadLetterEvent,
    Document,
    DocumentVersion,
    ExtractionCandidate,
    ExtractionFieldEvidence,
    ExtractionRun,
    GraphRun,
    OutboxEvent,
    ReviewTask,
    StoredObject,
    ValidationResult,
    ValidationRun,
    WebhookDelivery,
)
from intake_api.infrastructure.config import Settings


class DocumentSummary(BaseModel):
    id: UUID
    status: str
    processing_stage: str
    graph_status: str
    duplicate_content: bool
    created_at: datetime
    review_task_id: UUID | None = None


class ExtractionFieldView(BaseModel):
    name: str
    value: object | None
    confidence: Decimal
    page_number: int | None
    evidence_excerpt: str
    extraction_strategy: str
    provider: str
    model: str


class DocumentDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document: DocumentSummary
    graph_run_id: UUID
    graph_version: str
    correlation_id: str
    pdf_content_url: str
    agent_tools: list[dict[str, object]]
    advisory_recommendation: dict[str, object] | None
    extraction_candidate_id: UUID | None
    extracted_fields: list[ExtractionFieldView]
    authoritative_validation: list[dict[str, object]]
    confidence: dict[str, object] | None
    review: dict[str, object] | None
    audit_timeline: list[dict[str, object]]


class OverviewMetrics(BaseModel):
    uploads: int
    graph_completion: int
    interrupted_graph_count: int
    agent_completion: int
    agent_tool_calls: int
    extraction_attempts: int
    average_confidence: Decimal | None
    confidence_distribution: dict[str, int]
    low_confidence_fields: int
    rule_failures: int
    human_corrections: int
    human_correction_rate: Decimal
    completed_reviews: int
    average_review_duration_seconds: Decimal | None
    approvals: int
    approval_rate: Decimal
    provider_latency_ms: Decimal | None
    token_usage: int
    webhook_success: int
    webhook_retries: int
    dead_letter_count: int
    rule_failure_frequency: dict[str, int]


class QueryService:
    def __init__(self, settings: Settings, sessions: sessionmaker[Session]) -> None:
        self._settings = settings
        self._sessions = sessions

    def documents(self, identity: RequestIdentity) -> list[DocumentSummary]:
        with self._sessions() as session:
            statement = (
                select(Document, GraphRun, ReviewTask)
                .join(GraphRun, GraphRun.document_id == Document.id)
                .outerjoin(ReviewTask, ReviewTask.graph_run_id == GraphRun.id)
                .where(Document.tenant_id == identity.tenant_id)
                .order_by(Document.created_at.desc())
            )
            if identity.role == UserRole.SUBMITTER:
                statement = statement.where(Document.created_by_user_id == identity.user_id)
            return [
                _summary(document, run, review)
                for document, run, review in session.execute(statement)
            ]

    def document(self, document_id: UUID, identity: RequestIdentity) -> DocumentDetail:
        with self._sessions() as session:
            document, run, review = self._document_row(session, document_id, identity)
            agent_run = session.scalar(
                select(AgentRun).where(
                    AgentRun.tenant_id == identity.tenant_id, AgentRun.graph_run_id == run.id
                )
            )
            tools = (
                list(
                    session.scalars(
                        select(AgentToolCall)
                        .where(
                            AgentToolCall.tenant_id == identity.tenant_id,
                            AgentToolCall.agent_run_id == agent_run.id,
                        )
                        .order_by(AgentToolCall.started_at)
                    )
                )
                if agent_run
                else []
            )
            recommendation = (
                session.scalar(
                    select(AgentRecommendation).where(
                        AgentRecommendation.tenant_id == identity.tenant_id,
                        AgentRecommendation.agent_run_id == agent_run.id,
                    )
                )
                if agent_run
                else None
            )
            candidate = (
                session.scalar(
                    select(ExtractionCandidate).where(
                        ExtractionCandidate.id == recommendation.extraction_candidate_id,
                        ExtractionCandidate.tenant_id == identity.tenant_id,
                    )
                )
                if recommendation and recommendation.extraction_candidate_id
                else None
            )
            evidence = (
                list(
                    session.scalars(
                        select(ExtractionFieldEvidence)
                        .where(
                            ExtractionFieldEvidence.tenant_id == identity.tenant_id,
                            ExtractionFieldEvidence.candidate_id == candidate.id,
                        )
                        .order_by(ExtractionFieldEvidence.field_name)
                    )
                )
                if candidate
                else []
            )
            validation = session.scalar(
                select(ValidationRun)
                .where(
                    ValidationRun.tenant_id == identity.tenant_id,
                    ValidationRun.graph_run_id == run.id,
                    ValidationRun.authoritative.is_(True),
                )
                .order_by(ValidationRun.created_at.desc())
                .limit(1)
            )
            validation_results = (
                list(
                    session.scalars(
                        select(ValidationResult)
                        .where(
                            ValidationResult.tenant_id == identity.tenant_id,
                            ValidationResult.validation_run_id == validation.id,
                        )
                        .order_by(ValidationResult.rule_id)
                    )
                )
                if validation
                else []
            )
            confidence = session.scalar(
                select(ConfidenceRun)
                .where(
                    ConfidenceRun.tenant_id == identity.tenant_id,
                    ConfidenceRun.graph_run_id == run.id,
                )
                .order_by(ConfidenceRun.created_at.desc())
                .limit(1)
            )
            components = (
                list(
                    session.scalars(
                        select(ConfidenceComponent).where(
                            ConfidenceComponent.tenant_id == identity.tenant_id,
                            ConfidenceComponent.confidence_run_id == confidence.id,
                        )
                    )
                )
                if confidence
                else []
            )
            audits = list(
                session.scalars(
                    select(AuditEvent)
                    .where(
                        AuditEvent.tenant_id == identity.tenant_id,
                        AuditEvent.document_id == document.id,
                    )
                    .order_by(AuditEvent.created_at)
                )
            )
            return DocumentDetail(
                document=_summary(document, run, review),
                graph_run_id=run.id,
                graph_version=run.graph_version,
                correlation_id=run.correlation_id,
                pdf_content_url=f"/api/v1/documents/{document.id}/content",
                agent_tools=[
                    {
                        "tool": tool.tool_name,
                        "status": tool.status,
                        "duration_ms": tool.duration_ms,
                        "reason_codes": tool.reason_codes,
                        "started_at": tool.started_at.isoformat(),
                    }
                    for tool in tools
                ],
                advisory_recommendation=(
                    {
                        "disposition": recommendation.recommended_disposition,
                        "summary": recommendation.summary,
                        "reason_codes": recommendation.reason_codes,
                        "document_type": recommendation.document_type,
                        "classification_confidence": str(recommendation.classification_confidence),
                        "incomplete": recommendation.incomplete,
                    }
                    if recommendation
                    else None
                ),
                extraction_candidate_id=candidate.id if candidate else None,
                extracted_fields=[
                    ExtractionFieldView(
                        name=item.field_name,
                        value=candidate.candidate_data.get(item.field_name) if candidate else None,
                        confidence=item.confidence,
                        page_number=item.source_page,
                        evidence_excerpt=item.evidence_excerpt,
                        extraction_strategy=item.extraction_strategy,
                        provider=item.provider,
                        model=item.model,
                    )
                    for item in evidence
                ],
                authoritative_validation=[
                    {
                        "rule_id": item.rule_id,
                        "outcome": item.outcome,
                        "reason_code": item.reason_code,
                        "rule_version": item.rule_version,
                    }
                    for item in validation_results
                ],
                confidence=(
                    {
                        "overall_score": str(confidence.overall_score),
                        "algorithm_version": confidence.algorithm_version,
                        "components": {
                            item.component_name: (
                                str(item.raw_score) if item.raw_score is not None else None
                            )
                            for item in components
                        },
                        "human_review_required": True,
                    }
                    if confidence
                    else None
                ),
                review=(
                    {
                        "task_id": str(review.id),
                        "status": review.status,
                        "cycle": review.cycle,
                        "claimed_by_user_id": (
                            str(review.claimed_by_user_id) if review.claimed_by_user_id else None
                        ),
                        "decision": review.decision,
                    }
                    if review
                    else None
                ),
                audit_timeline=[
                    {
                        "event_type": event.event_type,
                        "actor_type": event.actor_type,
                        "actor_id": event.actor_id,
                        "created_at": event.created_at.isoformat(),
                        "metadata": event.sanitized_metadata,
                    }
                    for event in audits
                ],
            )

    def document_content(self, document_id: UUID, identity: RequestIdentity) -> Path:
        with self._sessions() as session:
            document, _, _ = self._document_row(session, document_id, identity)
            row = session.execute(
                select(StoredObject.object_key)
                .join(DocumentVersion, DocumentVersion.stored_object_id == StoredObject.id)
                .where(
                    DocumentVersion.tenant_id == identity.tenant_id,
                    DocumentVersion.id == document.current_version_id,
                )
            ).one_or_none()
            if row is None:
                raise ResourceNotFound("The immutable PDF is unavailable.")
            root = self._settings.storage_root.resolve()
            path = (root / row[0]).resolve()
            if not path.is_relative_to(root) or not path.is_file():
                raise ResourceNotFound("The immutable PDF is unavailable.")
            return path

    def overview(self, identity: RequestIdentity) -> OverviewMetrics:
        _require_operations(identity)
        tenant = identity.tenant_id
        with self._sessions() as session:

            def scalar(model: type[Base], *criteria: ColumnElement[bool]) -> int:
                return int(
                    session.scalar(select(func.count()).select_from(model).where(*criteria)) or 0
                )

            confidence_scores = list(
                session.scalars(
                    select(ConfidenceRun.overall_score).where(ConfidenceRun.tenant_id == tenant)
                )
            )
            completed_review_rows = list(
                session.scalars(
                    select(ReviewTask).where(
                        ReviewTask.tenant_id == tenant,
                        ReviewTask.completed_at.is_not(None),
                    )
                )
            )
            human_corrections = scalar(
                AuditEvent,
                AuditEvent.tenant_id == tenant,
                AuditEvent.event_type == "FIELD_CORRECTED",
            )
            corrected_documents = int(
                session.scalar(
                    select(func.count(func.distinct(AuditEvent.document_id))).where(
                        AuditEvent.tenant_id == tenant,
                        AuditEvent.event_type == "FIELD_CORRECTED",
                    )
                )
                or 0
            )
            approvals = scalar(
                ReviewTask, ReviewTask.tenant_id == tenant, ReviewTask.status == "APPROVED"
            )
            rule_frequency = {
                rule_id: int(count)
                for rule_id, count in session.execute(
                    select(ValidationResult.rule_id, func.count())
                    .where(
                        ValidationResult.tenant_id == tenant,
                        ValidationResult.outcome.in_(["FAIL", "REVIEW"]),
                    )
                    .group_by(ValidationResult.rule_id)
                    .order_by(ValidationResult.rule_id)
                )
            }
            review_durations = [
                Decimal(str((task.completed_at - task.created_at).total_seconds()))
                for task in completed_review_rows
                if task.completed_at is not None
            ]
            agent_runs = list(session.scalars(select(AgentRun).where(AgentRun.tenant_id == tenant)))
            provider_latencies = [
                latency
                for row in agent_runs
                for latency in _usage_values(row.usage_metadata, "latency_ms")
            ]
            return OverviewMetrics(
                uploads=scalar(Document, Document.tenant_id == tenant),
                graph_completion=scalar(
                    GraphRun, GraphRun.tenant_id == tenant, GraphRun.status == "COMPLETED"
                ),
                interrupted_graph_count=scalar(
                    GraphRun, GraphRun.tenant_id == tenant, GraphRun.status == "INTERRUPTED"
                ),
                agent_completion=scalar(
                    AgentRun, AgentRun.tenant_id == tenant, AgentRun.status == "COMPLETED"
                ),
                agent_tool_calls=scalar(AgentToolCall, AgentToolCall.tenant_id == tenant),
                extraction_attempts=scalar(ExtractionRun, ExtractionRun.tenant_id == tenant),
                average_confidence=_average(confidence_scores),
                confidence_distribution={
                    "low": sum(score < Decimal("0.50") for score in confidence_scores),
                    "medium": sum(
                        Decimal("0.50") <= score < Decimal("0.80") for score in confidence_scores
                    ),
                    "high": sum(score >= Decimal("0.80") for score in confidence_scores),
                },
                low_confidence_fields=scalar(
                    ExtractionFieldEvidence,
                    ExtractionFieldEvidence.tenant_id == tenant,
                    ExtractionFieldEvidence.confidence < Decimal("0.70"),
                ),
                rule_failures=scalar(
                    ValidationResult,
                    ValidationResult.tenant_id == tenant,
                    ValidationResult.outcome.in_(["FAIL", "REVIEW"]),
                ),
                human_corrections=human_corrections,
                human_correction_rate=_rate(corrected_documents, len(completed_review_rows)),
                completed_reviews=len(completed_review_rows),
                average_review_duration_seconds=_average(review_durations),
                approvals=approvals,
                approval_rate=_rate(approvals, len(completed_review_rows)),
                provider_latency_ms=_average(provider_latencies),
                token_usage=sum(_token_count(row.usage_metadata) for row in agent_runs),
                webhook_success=scalar(
                    OutboxEvent, OutboxEvent.tenant_id == tenant, OutboxEvent.status == "DELIVERED"
                ),
                webhook_retries=scalar(
                    WebhookDelivery,
                    WebhookDelivery.tenant_id == tenant,
                    WebhookDelivery.attempt_number > 1,
                ),
                dead_letter_count=scalar(DeadLetterEvent, DeadLetterEvent.tenant_id == tenant),
                rule_failure_frequency=rule_frequency,
            )

    def operations_rows(self, resource: str, identity: RequestIdentity) -> list[dict[str, object]]:
        _require_operations(identity)
        tenant = identity.tenant_id
        with self._sessions() as session:
            if resource == "graph-runs":
                return [
                    {
                        "id": str(row.id),
                        "document_id": str(row.document_id),
                        "status": row.status,
                        "graph_version": row.graph_version,
                        "created_at": row.created_at.isoformat(),
                    }
                    for row in session.scalars(
                        select(GraphRun)
                        .where(GraphRun.tenant_id == tenant)
                        .order_by(GraphRun.created_at.desc())
                        .limit(200)
                    )
                ]
            if resource == "agent-runs":
                return [
                    {
                        "id": str(row.id),
                        "document_id": str(row.document_id),
                        "status": row.status,
                        "provider": row.provider,
                        "model": row.model,
                        "tool_calls": row.tool_call_count,
                        "usage": row.usage_metadata,
                    }
                    for row in session.scalars(
                        select(AgentRun)
                        .where(AgentRun.tenant_id == tenant)
                        .order_by(AgentRun.started_at.desc())
                        .limit(200)
                    )
                ]
            if resource == "tool-activity":
                return [
                    {
                        "id": str(row.id),
                        "tool": row.tool_name,
                        "status": row.status,
                        "duration_ms": row.duration_ms,
                        "started_at": row.started_at.isoformat(),
                    }
                    for row in session.scalars(
                        select(AgentToolCall)
                        .where(AgentToolCall.tenant_id == tenant)
                        .order_by(AgentToolCall.started_at.desc())
                        .limit(200)
                    )
                ]
            if resource == "extraction-quality":
                return [
                    {
                        "candidate_id": str(row.id),
                        "document_id": str(row.document_id),
                        "document_type": row.document_type,
                        "completeness": str(row.completeness_score),
                        "created_at": row.created_at.isoformat(),
                    }
                    for row in session.scalars(
                        select(ExtractionCandidate)
                        .where(ExtractionCandidate.tenant_id == tenant)
                        .order_by(ExtractionCandidate.created_at.desc())
                        .limit(200)
                    )
                ]
            if resource == "validation-failures":
                return [
                    {
                        "validation_run_id": str(row.validation_run_id),
                        "rule_id": row.rule_id,
                        "outcome": row.outcome,
                        "reason_code": row.reason_code,
                        "created_at": row.created_at.isoformat(),
                    }
                    for row in session.scalars(
                        select(ValidationResult)
                        .where(
                            ValidationResult.tenant_id == tenant,
                            ValidationResult.outcome.in_(["FAIL", "REVIEW"]),
                        )
                        .order_by(ValidationResult.created_at.desc())
                        .limit(200)
                    )
                ]
            if resource == "human-review":
                return [
                    {
                        "task_id": str(row.id),
                        "document_id": str(row.document_id),
                        "status": row.status,
                        "cycle": row.cycle,
                        "decision": row.decision,
                        "created_at": row.created_at.isoformat(),
                    }
                    for row in session.scalars(
                        select(ReviewTask)
                        .where(ReviewTask.tenant_id == tenant)
                        .order_by(ReviewTask.created_at.desc())
                        .limit(200)
                    )
                ]
            if resource == "zapier-delivery":
                return [
                    {
                        "event_id": str(row.id),
                        "document_id": str(row.document_id),
                        "event_type": row.event_type,
                        "status": row.status,
                        "attempts": row.attempt_count,
                        "last_error_code": row.last_error_code,
                        "created_at": row.created_at.isoformat(),
                    }
                    for row in session.scalars(
                        select(OutboxEvent)
                        .where(OutboxEvent.tenant_id == tenant)
                        .order_by(OutboxEvent.created_at.desc())
                        .limit(200)
                    )
                ]
            if resource == "audit":
                return [
                    {
                        "event_id": str(row.id),
                        "document_id": str(row.document_id) if row.document_id else None,
                        "event_type": row.event_type,
                        "actor_type": row.actor_type,
                        "actor_id": row.actor_id,
                        "created_at": row.created_at.isoformat(),
                        "metadata": row.sanitized_metadata,
                    }
                    for row in session.scalars(
                        select(AuditEvent)
                        .where(AuditEvent.tenant_id == tenant)
                        .order_by(AuditEvent.created_at.desc())
                        .limit(500)
                    )
                ]
        raise ResourceNotFound("The operations projection was not found.")

    @staticmethod
    def _document_row(
        session: Session, document_id: UUID, identity: RequestIdentity
    ) -> tuple[Document, GraphRun, ReviewTask | None]:
        statement = (
            select(Document, GraphRun, ReviewTask)
            .join(GraphRun, GraphRun.document_id == Document.id)
            .outerjoin(ReviewTask, ReviewTask.graph_run_id == GraphRun.id)
            .where(Document.id == document_id, Document.tenant_id == identity.tenant_id)
        )
        if identity.role == UserRole.SUBMITTER:
            statement = statement.where(Document.created_by_user_id == identity.user_id)
        row = session.execute(statement).one_or_none()
        if row is None:
            raise ResourceNotFound("The document was not found.")
        return row[0], row[1], row[2]


def _summary(document: Document, run: GraphRun, review: ReviewTask | None) -> DocumentSummary:
    stage = review.status if review else run.status
    return DocumentSummary(
        id=document.id,
        status=document.status,
        processing_stage=stage,
        graph_status=run.status,
        duplicate_content=document.duplicate_hash_detected,
        created_at=document.created_at,
        review_task_id=review.id if review else None,
    )


def _require_operations(identity: RequestIdentity) -> None:
    if identity.role not in {UserRole.OPERATIONS_ADMIN, UserRole.AUDITOR}:
        raise AuthorizationDenied("Operations projections require administrator or auditor access.")


def _average(values: list[Decimal]) -> Decimal | None:
    if not values:
        return None
    return (sum(values, start=Decimal("0")) / Decimal(len(values))).quantize(Decimal("0.01"))


def _rate(numerator: int, denominator: int) -> Decimal:
    if denominator == 0:
        return Decimal("0.0000")
    return (Decimal(numerator) / Decimal(denominator)).quantize(Decimal("0.0001"))


def _token_count(metadata: dict[str, object]) -> int:
    total = metadata.get("total_tokens")
    if isinstance(total, int) and not isinstance(total, bool):
        return total
    direct = 0
    for key in ("input_tokens", "output_tokens"):
        value = metadata.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            direct += value
    if direct:
        return direct
    return sum(
        _token_count(_string_mapping(cast(Mapping[object, object], value)))
        for value in metadata.values()
        if isinstance(value, Mapping)
    )


def _usage_values(metadata: dict[str, object], key: str) -> list[Decimal]:
    values: list[Decimal] = []
    value = metadata.get(key)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        values.append(Decimal(str(value)))
    for nested in metadata.values():
        if isinstance(nested, Mapping):
            values.extend(
                _usage_values(_string_mapping(cast(Mapping[object, object], nested)), key)
            )
    return values


def _string_mapping(value: Mapping[object, object]) -> dict[str, object]:
    return {str(key): item for key, item in value.items() if isinstance(key, str)}
