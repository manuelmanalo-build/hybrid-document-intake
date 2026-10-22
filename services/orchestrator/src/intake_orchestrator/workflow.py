from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Literal, Protocol, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from intake_api.domain.enums import (
    ActorType,
    AuditEventType,
    ClaimKeyStatus,
    GraphRunStatus,
    ReviewQueue,
)
from intake_api.domain.models import (
    AgentRecommendation,
    AgentRun,
    ApprovedBusinessRecord,
    AuditEvent,
    ClaimIntakeKey,
    ClaimRoutingDecision,
    ConfidenceComponent,
    ConfidenceRun,
    ExtractionCandidate,
    ExtractionFieldEvidence,
    GraphRun,
    OutboxEvent,
    PurchaseOrder,
    ReviewDecision,
    ReviewedDocumentVersion,
    ReviewTask,
    RuleDefinition,
    Supplier,
    ValidationResult,
    ValidationRun,
)
from intake_orchestrator.agent.backend import stable_id
from intake_orchestrator.claim_facts import load_claim_context
from intake_orchestrator.claim_validation import (
    CLAIM_ROUTING_VERSION,
    CLAIM_RULE_DEFINITIONS,
    CLAIM_RULESET_VERSION,
    ClaimCandidateData,
    route_claim,
    safe_claim_data,
    validate_claim_rules,
)
from intake_orchestrator.confidence import (
    ALGORITHM_VERSION,
    CLAIM_ALGORITHM_VERSION,
    CLAIM_WEIGHTS,
    WEIGHTS,
    decision_confidence,
)
from intake_orchestrator.validation import (
    RULE_DEFINITIONS,
    RULESET_VERSION,
    InvoiceCandidateData,
    RuleResult,
    ValidationContext,
    failing_reason_codes,
    normalize_document_type,
    safe_candidate_data,
    validate_candidate_rules,
)


class ReviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    decision: Literal["REQUEST_CORRECTION", "REJECT", "APPROVE"]
    actor_id: str = Field(min_length=1, max_length=200)
    reason_codes: list[
        Annotated[str, Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_.:-]+$")]
    ] = Field(default_factory=list, max_length=20)
    corrections: dict[
        Annotated[str, Field(min_length=1, max_length=64)],
        Annotated[str, Field(max_length=500)] | int | bool | None,
    ] = Field(default_factory=dict, max_length=10)
    review_task_id: UUID | None = None
    reviewed_document_version_id: UUID | None = None
    review_cycle: int | None = Field(default=None, ge=1)


class WorkflowBackend(Protocol):
    async def initialize_case(
        self, *, graph_run_id: UUID, tenant_id: UUID, document_id: UUID
    ) -> None: ...

    async def validate(
        self,
        *,
        graph_run_id: UUID,
        tenant_id: UUID,
        document_id: UUID,
        candidate_id: UUID | None,
        phase: str,
        reason_codes: list[str],
    ) -> tuple[UUID, list[str]]: ...

    async def calculate_confidence(
        self,
        *,
        graph_run_id: UUID,
        tenant_id: UUID,
        document_id: UUID,
        validation_run_id: UUID,
        reason_codes: list[str],
    ) -> UUID: ...

    async def create_or_update_review_task(
        self,
        *,
        graph_run_id: UUID,
        tenant_id: UUID,
        document_id: UUID,
        extraction_candidate_id: UUID | None = None,
        validation_run_id: UUID | None = None,
        confidence_run_id: UUID | None = None,
        recommendation_id: UUID | None = None,
    ) -> UUID: ...

    async def review_interrupt_payload(
        self, *, review_task_id: UUID, tenant_id: UUID
    ) -> dict[str, object]: ...

    async def apply_review_response(
        self, *, review_task_id: UUID, tenant_id: UUID, response: ReviewResponse
    ) -> None: ...

    async def finalize_rejection(
        self, *, graph_run_id: UUID, tenant_id: UUID, document_id: UUID
    ) -> None: ...

    async def create_webhook_outbox(
        self, *, graph_run_id: UUID, tenant_id: UUID, document_id: UUID
    ) -> UUID: ...


class InMemoryWorkflowBackend:
    def __init__(self) -> None:
        self.validation_runs: dict[tuple[UUID, str], UUID] = {}
        self.validation_reasons: dict[UUID, list[str]] = {}
        self.confidence_runs: dict[UUID, UUID] = {}
        self.review_tasks: dict[UUID, dict[str, object]] = {}
        self.outbox: dict[UUID, UUID] = {}
        self.rejections: set[UUID] = set()

    async def initialize_case(
        self, *, graph_run_id: UUID, tenant_id: UUID, document_id: UUID
    ) -> None:
        del graph_run_id, tenant_id, document_id

    async def validate(
        self,
        *,
        graph_run_id: UUID,
        tenant_id: UUID,
        document_id: UUID,
        candidate_id: UUID | None,
        phase: str,
        reason_codes: list[str],
    ) -> tuple[UUID, list[str]]:
        del tenant_id, document_id, reason_codes
        candidate = InvoiceCandidateData()
        document_type = "UNKNOWN" if candidate_id is None else "AP_INVOICE"
        results = validate_candidate_rules(
            candidate,
            ValidationContext(business_date=datetime.now(UTC).date(), document_type=document_type),
        )
        selected = _phase_reasons(results, phase)
        stored_phase = phase
        if phase == "REVIEWED":
            review_task = self.review_tasks.get(stable_id("review-task", str(graph_run_id)))
            cycle = cast(int, review_task["cycle"]) if review_task else 1
            stored_phase = f"REVIEWED_{cycle}"
        run_id = self.validation_runs.setdefault(
            (graph_run_id, stored_phase),
            stable_id(f"validation-{stored_phase}", str(graph_run_id)),
        )
        self.validation_reasons.setdefault(run_id, selected)
        return run_id, selected

    async def calculate_confidence(
        self,
        *,
        graph_run_id: UUID,
        tenant_id: UUID,
        document_id: UUID,
        validation_run_id: UUID,
        reason_codes: list[str],
    ) -> UUID:
        del tenant_id, document_id
        authoritative_reasons = self.validation_reasons.get(validation_run_id, reason_codes)
        components = _confidence_components(authoritative_reasons)
        decision_confidence(components, rule_based_risk=_rule_risk(authoritative_reasons))
        return self.confidence_runs.setdefault(
            graph_run_id, stable_id("confidence", str(graph_run_id))
        )

    async def create_or_update_review_task(
        self,
        *,
        graph_run_id: UUID,
        tenant_id: UUID,
        document_id: UUID,
        extraction_candidate_id: UUID | None = None,
        validation_run_id: UUID | None = None,
        confidence_run_id: UUID | None = None,
        recommendation_id: UUID | None = None,
    ) -> UUID:
        task_id = stable_id("review-task", str(graph_run_id))
        task = self.review_tasks.setdefault(
            task_id,
            {
                "tenant_id": tenant_id,
                "document_id": document_id,
                "status": "OPEN",
                "cycle": 0,
            },
        )
        task["cycle"] = cast(int, task["cycle"]) + 1
        task["status"] = "OPEN"
        task.update(
            extraction_candidate_id=extraction_candidate_id,
            validation_run_id=validation_run_id,
            confidence_run_id=confidence_run_id,
            recommendation_id=recommendation_id,
        )
        return task_id

    async def review_interrupt_payload(
        self, *, review_task_id: UUID, tenant_id: UUID
    ) -> dict[str, object]:
        task = self.review_tasks[review_task_id]
        if task["tenant_id"] != tenant_id:
            raise PermissionError("Review task is not in the graph tenant")
        return {
            "review_task_id": str(review_task_id),
            "document_id": str(task["document_id"]),
            "review_cycle": cast(int, task["cycle"]),
            "extraction_candidate_id": _json_uuid(task.get("extraction_candidate_id")),
            "validation_run_id": _json_uuid(task.get("validation_run_id")),
            "confidence_run_id": _json_uuid(task.get("confidence_run_id")),
            "agent_recommendation_summary": "Human review is required.",
            "reason_codes": [],
            "allowed_response_types": ["REQUEST_CORRECTION", "REJECT", "APPROVE"],
        }

    async def apply_review_response(
        self, *, review_task_id: UUID, tenant_id: UUID, response: ReviewResponse
    ) -> None:
        task = self.review_tasks[review_task_id]
        if task["tenant_id"] != tenant_id:
            raise PermissionError("Review task is not in the graph tenant")
        task["response"] = response.model_dump(mode="json")
        task["corrections"] = dict(response.corrections)
        task["status"] = "RESPONDED"

    async def finalize_rejection(
        self, *, graph_run_id: UUID, tenant_id: UUID, document_id: UUID
    ) -> None:
        del tenant_id, document_id
        self.rejections.add(graph_run_id)

    async def create_webhook_outbox(
        self, *, graph_run_id: UUID, tenant_id: UUID, document_id: UUID
    ) -> UUID:
        del tenant_id, document_id
        return self.outbox.setdefault(graph_run_id, stable_id("approval-outbox", str(graph_run_id)))


class PostgresWorkflowBackend:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        webhook_max_attempts: int = 5,
    ) -> None:
        self._sessions = session_factory
        self._webhook_max_attempts = webhook_max_attempts

    async def initialize_case(
        self, *, graph_run_id: UUID, tenant_id: UUID, document_id: UUID
    ) -> None:
        async with self._sessions() as session:
            exists = await session.scalar(
                select(GraphRun.id).where(
                    GraphRun.id == graph_run_id,
                    GraphRun.tenant_id == tenant_id,
                    GraphRun.document_id == document_id,
                )
            )
            if exists is None:
                raise PermissionError("Graph run identity is not tenant-consistent")

    async def validate(
        self,
        *,
        graph_run_id: UUID,
        tenant_id: UUID,
        document_id: UUID,
        candidate_id: UUID | None,
        phase: str,
        reason_codes: list[str],
    ) -> tuple[UUID, list[str]]:
        del reason_codes
        async with self._sessions() as session, session.begin():
            stored_phase = phase
            review_task: ReviewTask | None = None
            if phase == "REVIEWED":
                review_task = await session.scalar(
                    select(ReviewTask).where(
                        ReviewTask.tenant_id == tenant_id,
                        ReviewTask.graph_run_id == graph_run_id,
                    )
                )
                if review_task is None:
                    raise RuntimeError("Reviewed validation requires a review task")
                stored_phase = f"REVIEWED_{review_task.cycle}"
            run_id = stable_id(f"validation-{stored_phase}", str(graph_run_id))
            existing = await session.scalar(
                select(ValidationRun).where(
                    ValidationRun.id == run_id,
                    ValidationRun.tenant_id == tenant_id,
                )
            )
            if existing is None:
                candidate = (
                    await session.scalar(
                        select(ExtractionCandidate).where(
                            ExtractionCandidate.id == candidate_id,
                            ExtractionCandidate.tenant_id == tenant_id,
                            ExtractionCandidate.document_id == document_id,
                        )
                    )
                    if candidate_id is not None
                    else None
                )
                if candidate is not None and candidate.document_type == "CLAIM_PACKET":
                    reasons = await self._validate_claim(
                        session,
                        candidate=candidate,
                        review_task=review_task,
                        phase=phase,
                        stored_phase=stored_phase,
                        run_id=run_id,
                        graph_run_id=graph_run_id,
                        tenant_id=tenant_id,
                        document_id=document_id,
                    )
                else:
                    candidate_data = (
                        safe_candidate_data(candidate.candidate_data)
                        if candidate is not None
                        else InvoiceCandidateData()
                    )
                    confidences: dict[str, Decimal] = {}
                    if candidate is not None:
                        confidences = dict(
                            (
                                await session.execute(
                                    select(
                                        ExtractionFieldEvidence.field_name,
                                        ExtractionFieldEvidence.confidence,
                                    ).where(
                                        ExtractionFieldEvidence.tenant_id == tenant_id,
                                        ExtractionFieldEvidence.candidate_id == candidate.id,
                                    )
                                )
                            )
                            .tuples()
                            .all()
                        )
                        candidate_data = candidate_data.model_copy(
                            update={"field_confidence": confidences}
                        )
                    if phase == "REVIEWED":
                        if review_task is None:
                            raise RuntimeError("Reviewed validation requires a review task")
                        reviewed = await session.scalar(
                            select(ReviewedDocumentVersion).where(
                                ReviewedDocumentVersion.tenant_id == tenant_id,
                                ReviewedDocumentVersion.review_task_id == review_task.id,
                                ReviewedDocumentVersion.review_cycle == review_task.cycle,
                            )
                        )
                        if reviewed is None:
                            raise RuntimeError("Reviewed business-data version is unavailable")
                        candidate_data = safe_candidate_data(reviewed.business_data)
                        candidate_data = candidate_data.model_copy(
                            update={
                                "field_confidence": confidences if candidate is not None else {}
                            }
                        )
                    document_type = normalize_document_type(
                        candidate.document_type if candidate is not None else "UNKNOWN"
                    )
                    supplier = None
                    if candidate_data.supplier_name:
                        supplier = await session.scalar(
                            select(Supplier).where(
                                Supplier.tenant_id == tenant_id,
                                Supplier.name == candidate_data.supplier_name,
                            )
                        )
                    purchase_order = None
                    if candidate_data.purchase_order_number:
                        purchase_order = await session.scalar(
                            select(PurchaseOrder).where(
                                PurchaseOrder.tenant_id == tenant_id,
                                PurchaseOrder.purchase_order_number
                                == candidate_data.purchase_order_number,
                            )
                        )
                    results = validate_candidate_rules(
                        candidate_data,
                        ValidationContext(
                            business_date=datetime.now(UTC).date(),
                            document_type=document_type,
                            duplicate_approved_invoice=await _approved_duplicate_exists(
                                session,
                                tenant_id=tenant_id,
                                graph_run_id=graph_run_id,
                                candidate=candidate_data,
                            ),
                            supplier_matched=supplier is not None,
                            supplier_active=supplier.active if supplier else None,
                            purchase_order_matched=purchase_order is not None,
                            purchase_order_authorized_amount=(
                                purchase_order.authorized_amount if purchase_order else None
                            ),
                        ),
                    )
                    reasons = _phase_reasons(results, phase)
                    validation_run = ValidationRun(
                        id=run_id,
                        tenant_id=tenant_id,
                        document_id=document_id,
                        graph_run_id=graph_run_id,
                        extraction_candidate_id=candidate_id,
                        phase=stored_phase,
                        authoritative=True,
                        reason_codes=reasons,
                    )
                    session.add(validation_run)
                    await session.flush([validation_run])
                    for rule_id, name, severity in RULE_DEFINITIONS:
                        definition_id = stable_id(
                            "rule-definition", f"{tenant_id}:{RULESET_VERSION}:{rule_id}"
                        )
                        if not await session.scalar(
                            select(RuleDefinition.id).where(
                                RuleDefinition.id == definition_id,
                                RuleDefinition.tenant_id == tenant_id,
                            )
                        ):
                            session.add(
                                RuleDefinition(
                                    id=definition_id,
                                    tenant_id=tenant_id,
                                    rule_id=rule_id,
                                    version=RULESET_VERSION,
                                    name=name,
                                    severity=severity,
                                    configuration={},
                                )
                            )
                    for result in results:
                        session.add(
                            ValidationResult(
                                id=stable_id("validation-result", f"{run_id}:{result.rule_id}"),
                                tenant_id=tenant_id,
                                validation_run_id=run_id,
                                rule_id=result.rule_id,
                                rule_version=RULESET_VERSION,
                                outcome=result.outcome,
                                reason_code=result.reason_code,
                                sanitized_details={},
                            )
                        )
                    session.add(
                        _workflow_audit(
                            kind=f"validation-{stored_phase}",
                            tenant_id=tenant_id,
                            document_id=document_id,
                            graph_run_id=graph_run_id,
                            event_type=AuditEventType.AUTHORITATIVE_VALIDATION_COMPLETED,
                            metadata={"phase": stored_phase, "reason_codes": reasons},
                        )
                    )
            else:
                reasons = existing.reason_codes
        return run_id, reasons

    async def _validate_claim(
        self,
        session: AsyncSession,
        *,
        candidate: ExtractionCandidate,
        review_task: ReviewTask | None,
        phase: str,
        stored_phase: str,
        run_id: UUID,
        graph_run_id: UUID,
        tenant_id: UUID,
        document_id: UUID,
    ) -> list[str]:
        """Authoritative CR rules, duplicate key, and claim-routing-v1 decision (ADR 0008)."""
        if phase == "REVIEWED":
            if review_task is None:
                raise RuntimeError("Reviewed validation requires a review task")
            reviewed = await session.scalar(
                select(ReviewedDocumentVersion).where(
                    ReviewedDocumentVersion.tenant_id == tenant_id,
                    ReviewedDocumentVersion.review_task_id == review_task.id,
                    ReviewedDocumentVersion.review_cycle == review_task.cycle,
                )
            )
            if reviewed is None:
                raise RuntimeError("Reviewed business-data version is unavailable")
            # Human-reviewed values are verified; extraction confidence no longer applies.
            data = safe_claim_data(reviewed.business_data)
        else:
            confidences = dict(
                (
                    await session.execute(
                        select(
                            ExtractionFieldEvidence.field_name,
                            ExtractionFieldEvidence.confidence,
                        ).where(
                            ExtractionFieldEvidence.tenant_id == tenant_id,
                            ExtractionFieldEvidence.candidate_id == candidate.id,
                        )
                    )
                )
                .tuples()
                .all()
            )
            data = safe_claim_data(candidate.candidate_data).model_copy(
                update={"field_confidence": confidences}
            )
        context = await load_claim_context(
            session,
            tenant_id=tenant_id,
            document_id=document_id,
            data=data,
            business_date=datetime.now(UTC).date(),
        )
        results = validate_claim_rules(data, context)
        selected = (
            [result for result in results if result.rule_id in CLAIM_SCHEMA_RULES]
            if phase == "SCHEMA"
            else results
        )
        reasons = failing_reason_codes(selected)
        session.add(
            ValidationRun(
                id=run_id,
                tenant_id=tenant_id,
                document_id=document_id,
                graph_run_id=graph_run_id,
                extraction_candidate_id=candidate.id,
                phase=stored_phase,
                authoritative=True,
                reason_codes=reasons,
            )
        )
        await session.flush()
        for rule_id, name, severity in CLAIM_RULE_DEFINITIONS:
            definition_id = stable_id(
                "rule-definition", f"{tenant_id}:{CLAIM_RULESET_VERSION}:{rule_id}"
            )
            if not await session.scalar(
                select(RuleDefinition.id).where(
                    RuleDefinition.id == definition_id, RuleDefinition.tenant_id == tenant_id
                )
            ):
                session.add(
                    RuleDefinition(
                        id=definition_id,
                        tenant_id=tenant_id,
                        rule_id=rule_id,
                        version=CLAIM_RULESET_VERSION,
                        name=name,
                        severity=severity,
                        configuration={},
                    )
                )
        for result in results:
            session.add(
                ValidationResult(
                    id=stable_id("validation-result", f"{run_id}:{result.rule_id}"),
                    tenant_id=tenant_id,
                    validation_run_id=run_id,
                    rule_id=result.rule_id,
                    rule_version=CLAIM_RULESET_VERSION,
                    outcome=result.outcome,
                    reason_code=result.reason_code,
                    sanitized_details={},
                )
            )
        audit_metadata: dict[str, object] = {"phase": stored_phase, "reason_codes": reasons}
        if phase != "SCHEMA":
            route = route_claim(results, data, context)
            session.add(
                ClaimRoutingDecision(
                    id=stable_id("claim-routing", str(run_id)),
                    tenant_id=tenant_id,
                    document_id=document_id,
                    graph_run_id=graph_run_id,
                    validation_run_id=run_id,
                    review_queue=ReviewQueue(route.queue),
                    routing_version=CLAIM_ROUTING_VERSION,
                    reason_code=route.reason_code,
                    flags=route.flags,
                )
            )
            await _upsert_claim_intake_key(
                session,
                tenant_id=tenant_id,
                document_id=document_id,
                graph_run_id=graph_run_id,
                data=data,
            )
            audit_metadata.update(
                {
                    "review_queue": route.queue,
                    "routing_version": CLAIM_ROUTING_VERSION,
                    "routing_reason": route.reason_code,
                }
            )
        session.add(
            _workflow_audit(
                kind=f"validation-{stored_phase}",
                tenant_id=tenant_id,
                document_id=document_id,
                graph_run_id=graph_run_id,
                event_type=AuditEventType.AUTHORITATIVE_VALIDATION_COMPLETED,
                metadata=audit_metadata,
            )
        )
        return reasons

    async def calculate_confidence(
        self,
        *,
        graph_run_id: UUID,
        tenant_id: UUID,
        document_id: UUID,
        validation_run_id: UUID,
        reason_codes: list[str],
    ) -> UUID:
        async with self._sessions() as session, session.begin():
            validation = await session.scalar(
                select(ValidationRun).where(
                    ValidationRun.id == validation_run_id,
                    ValidationRun.tenant_id == tenant_id,
                )
            )
            if validation is None:
                raise RuntimeError("Authoritative validation run is not available")
            phase = validation.phase
            run_id = stable_id(f"confidence-{phase}", str(graph_run_id))
            if not await session.scalar(
                select(ConfidenceRun.id).where(
                    ConfidenceRun.id == run_id,
                    ConfidenceRun.tenant_id == tenant_id,
                )
            ):
                authoritative_reasons = validation.reason_codes
                candidate_type = (
                    await session.scalar(
                        select(ExtractionCandidate.document_type).where(
                            ExtractionCandidate.id == validation.extraction_candidate_id,
                            ExtractionCandidate.tenant_id == tenant_id,
                        )
                    )
                    if validation.extraction_candidate_id
                    else None
                )
                is_claim = candidate_type == "CLAIM_PACKET"
                weights = CLAIM_WEIGHTS if is_claim else WEIGHTS
                algorithm_version = CLAIM_ALGORITHM_VERSION if is_claim else ALGORITHM_VERSION
                components = await (
                    _claim_confidence_components
                    if is_claim
                    else _confidence_components_from_records
                )(
                    session,
                    tenant_id=tenant_id,
                    graph_run_id=graph_run_id,
                    candidate_id=validation.extraction_candidate_id,
                    validation_run_id=validation_run_id,
                )
                risk = _rule_risk(authoritative_reasons)
                score = decision_confidence(components, rule_based_risk=risk, weights=weights)
                confidence_run = ConfidenceRun(
                    id=run_id,
                    tenant_id=tenant_id,
                    document_id=document_id,
                    graph_run_id=graph_run_id,
                    validation_run_id=validation_run_id,
                    phase=phase,
                    algorithm_version=algorithm_version,
                    overall_score=score,
                    component_scores={
                        **{
                            key: str(value) if value is not None else None
                            for key, value in components.items()
                        },
                        "rule_based_risk": str(risk),
                        "all_cases_require_review": True,
                    },
                )
                session.add(confidence_run)
                await session.flush([confidence_run])
                for name, value in components.items():
                    weight = weights[name]
                    session.add(
                        ConfidenceComponent(
                            id=stable_id("confidence-component", f"{run_id}:{name}"),
                            tenant_id=tenant_id,
                            confidence_run_id=run_id,
                            component_name=name,
                            raw_score=value,
                            weight=weight,
                            weighted_score=value * weight if value is not None else None,
                            applicable=value is not None,
                        )
                    )
                session.add(
                    _workflow_audit(
                        kind=f"confidence-{phase}",
                        tenant_id=tenant_id,
                        document_id=document_id,
                        graph_run_id=graph_run_id,
                        event_type=AuditEventType.CONFIDENCE_CALCULATED,
                        metadata={"algorithm_version": algorithm_version},
                    )
                )
        return run_id

    async def create_or_update_review_task(
        self,
        *,
        graph_run_id: UUID,
        tenant_id: UUID,
        document_id: UUID,
        extraction_candidate_id: UUID | None = None,
        validation_run_id: UUID | None = None,
        confidence_run_id: UUID | None = None,
        recommendation_id: UUID | None = None,
    ) -> UUID:
        task_id = stable_id("review-task", str(graph_run_id))
        async with self._sessions() as session, session.begin():
            graph_run = await session.scalar(
                select(GraphRun)
                .where(GraphRun.id == graph_run_id, GraphRun.tenant_id == tenant_id)
                .with_for_update()
            )
            if graph_run is None or graph_run.document_id != document_id:
                raise RuntimeError("Review task graph run is unavailable")
            task = await session.scalar(
                select(ReviewTask)
                .where(ReviewTask.id == task_id, ReviewTask.tenant_id == tenant_id)
                .with_for_update()
            )
            if task is None:
                task = ReviewTask(
                    id=task_id,
                    tenant_id=tenant_id,
                    document_id=document_id,
                    graph_run_id=graph_run_id,
                    status="OPEN",
                    cycle=1,
                    extraction_candidate_id=extraction_candidate_id,
                    validation_run_id=validation_run_id,
                    confidence_run_id=confidence_run_id,
                    recommendation_id=recommendation_id,
                )
                session.add(task)
                session.add(
                    _workflow_audit(
                        kind="review-task-created",
                        tenant_id=tenant_id,
                        document_id=document_id,
                        graph_run_id=graph_run_id,
                        event_type=AuditEventType.REVIEW_TASK_CREATED,
                        metadata={"review_task_id": str(task_id)},
                    )
                )
            elif task.status != "OPEN":
                task.status = "OPEN"
                task.cycle += 1
                task.response_payload = None
                task.decision = None
                task.completed_at = None
                task.claimed_by_user_id = None
            task.extraction_candidate_id = extraction_candidate_id
            task.validation_run_id = validation_run_id
            task.confidence_run_id = confidence_run_id
            task.recommendation_id = recommendation_id
            # Make the application-owned review task usable even if the orchestrator crashes
            # after the durable interrupt checkpoint but before dispatcher reconciliation.
            graph_run.status = GraphRunStatus.INTERRUPTED
            graph_run.last_error_code = None
        return task_id

    async def review_interrupt_payload(
        self, *, review_task_id: UUID, tenant_id: UUID
    ) -> dict[str, object]:
        async with self._sessions() as session:
            task = await session.scalar(
                select(ReviewTask).where(
                    ReviewTask.id == review_task_id, ReviewTask.tenant_id == tenant_id
                )
            )
            if task is None:
                raise PermissionError("Review task is not available in the graph tenant")
            recommendation = (
                await session.scalar(
                    select(AgentRecommendation).where(
                        AgentRecommendation.id == task.recommendation_id,
                        AgentRecommendation.tenant_id == tenant_id,
                    )
                )
                if task.recommendation_id
                else None
            )
            validation = (
                await session.scalar(
                    select(ValidationRun).where(
                        ValidationRun.id == task.validation_run_id,
                        ValidationRun.tenant_id == tenant_id,
                    )
                )
                if task.validation_run_id
                else None
            )
            return {
                "review_task_id": str(task.id),
                "document_id": str(task.document_id),
                "review_cycle": task.cycle,
                "extraction_candidate_id": _json_uuid(task.extraction_candidate_id),
                "validation_run_id": _json_uuid(task.validation_run_id),
                "confidence_run_id": _json_uuid(task.confidence_run_id),
                "agent_recommendation_summary": (
                    recommendation.summary[:500]
                    if recommendation
                    else "Agent did not provide a complete recommendation."
                ),
                "reason_codes": list(
                    dict.fromkeys(
                        [
                            *(validation.reason_codes if validation else []),
                            *(recommendation.reason_codes if recommendation else []),
                        ]
                    )
                )[:20],
                "allowed_response_types": ["REQUEST_CORRECTION", "REJECT", "APPROVE"],
            }

    async def apply_review_response(
        self, *, review_task_id: UUID, tenant_id: UUID, response: ReviewResponse
    ) -> None:
        async with self._sessions() as session, session.begin():
            task = await session.scalar(
                select(ReviewTask)
                .where(ReviewTask.id == review_task_id, ReviewTask.tenant_id == tenant_id)
                .with_for_update()
            )
            if task is None:
                raise PermissionError("Review task is not available in the graph tenant")
            if response.review_task_id != task.id or response.review_cycle != task.cycle:
                raise ValueError("Review resume payload does not match the persisted task cycle")
            if response.reviewed_document_version_id is None:
                raise ValueError("Review resume requires a reviewed business-data version")
            reviewed = await session.scalar(
                select(ReviewedDocumentVersion).where(
                    ReviewedDocumentVersion.id == response.reviewed_document_version_id,
                    ReviewedDocumentVersion.tenant_id == tenant_id,
                )
            )
            if (
                reviewed is None
                or reviewed.tenant_id != tenant_id
                or reviewed.review_task_id != task.id
                or reviewed.review_cycle != task.cycle
                or reviewed.document_id != task.document_id
            ):
                raise PermissionError("Reviewed business-data version is unavailable")
            decision = await session.scalar(
                select(ReviewDecision).where(
                    ReviewDecision.tenant_id == tenant_id,
                    ReviewDecision.review_task_id == task.id,
                    ReviewDecision.review_cycle == task.cycle,
                    ReviewDecision.reviewed_document_version_id == reviewed.id,
                )
            )
            if (
                decision is None
                or decision.decision != response.decision
                or str(decision.decided_by_user_id) != response.actor_id
            ):
                raise PermissionError("Review resume payload is not authoritative")
            task.response_payload = response.model_dump(mode="json")
            task.decision = response.decision
            task.status = "RESPONDED"

    async def finalize_rejection(
        self, *, graph_run_id: UUID, tenant_id: UUID, document_id: UUID
    ) -> None:
        async with self._sessions() as session, session.begin():
            key = await session.scalar(
                select(ClaimIntakeKey)
                .where(
                    ClaimIntakeKey.tenant_id == tenant_id,
                    ClaimIntakeKey.document_id == document_id,
                )
                .with_for_update()
            )
            # A rejected claim must not block a later legitimate claim as a duplicate (CR-006).
            if key is not None:
                key.status = ClaimKeyStatus.REJECTED
        await self._finalize(
            graph_run_id=graph_run_id,
            tenant_id=tenant_id,
            document_id=document_id,
            decision="REJECT",
            event_type=AuditEventType.DOCUMENT_REJECTED,
        )

    async def create_webhook_outbox(
        self, *, graph_run_id: UUID, tenant_id: UUID, document_id: UUID
    ) -> UUID:
        approved_record_id = stable_id("approved-business-record", str(document_id))
        event_id = stable_id("approval-outbox", str(approved_record_id))
        idempotency_key = f"approved-record:{approved_record_id}:DOCUMENT_APPROVED"
        async with self._sessions() as session, session.begin():
            task = await session.scalar(
                select(ReviewTask)
                .where(ReviewTask.graph_run_id == graph_run_id, ReviewTask.tenant_id == tenant_id)
                .with_for_update()
            )
            if task is None:
                raise RuntimeError("Approval requires an application review task")
            decision = await session.scalar(
                select(ReviewDecision).where(
                    ReviewDecision.tenant_id == tenant_id,
                    ReviewDecision.review_task_id == task.id,
                    ReviewDecision.review_cycle == task.cycle,
                    ReviewDecision.decision == "APPROVE",
                )
            )
            if decision is None:
                raise PermissionError("Outbox creation requires authoritative human approval")
            reviewed = await session.scalar(
                select(ReviewedDocumentVersion).where(
                    ReviewedDocumentVersion.id == decision.reviewed_document_version_id,
                    ReviewedDocumentVersion.tenant_id == tenant_id,
                )
            )
            if reviewed is None:
                raise RuntimeError("Approved reviewed document version is unavailable")
            approved = await session.scalar(
                select(ApprovedBusinessRecord).where(
                    ApprovedBusinessRecord.id == approved_record_id,
                    ApprovedBusinessRecord.tenant_id == tenant_id,
                )
            )
            if approved is None:
                approved = ApprovedBusinessRecord(
                    id=approved_record_id,
                    tenant_id=tenant_id,
                    document_id=document_id,
                    graph_run_id=graph_run_id,
                    review_decision_id=decision.id,
                    reviewed_document_version_id=reviewed.id,
                    record_version=1,
                    business_data=reviewed.business_data,
                    approved_by_user_id=decision.decided_by_user_id,
                    approved_at=decision.created_at,
                )
                session.add(approved)
                await session.flush([approved])
            else:
                approved.review_decision_id = decision.id
                approved.reviewed_document_version_id = reviewed.id
                approved.record_version = max(approved.record_version, task.cycle)
                approved.business_data = reviewed.business_data
                approved.approved_by_user_id = decision.decided_by_user_id
                approved.approved_at = decision.created_at
            task.status = "APPROVED"
            if not await session.scalar(
                select(OutboxEvent.id).where(
                    OutboxEvent.id == event_id,
                    OutboxEvent.tenant_id == tenant_id,
                )
            ):
                session.add(
                    OutboxEvent(
                        id=event_id,
                        tenant_id=tenant_id,
                        document_id=document_id,
                        graph_run_id=graph_run_id,
                        approved_business_record_id=approved_record_id,
                        event_type="DOCUMENT_APPROVED",
                        idempotency_key=idempotency_key,
                        payload={
                            "event_version": "1.0",
                            "event_type": "DOCUMENT_APPROVED",
                            "approved_business_record_id": str(approved_record_id),
                            "document_id": str(document_id),
                            "graph_run_id": str(graph_run_id),
                            "business_data": reviewed.business_data,
                        },
                        status="PENDING",
                        attempt_count=0,
                        max_attempts=self._webhook_max_attempts,
                        next_attempt_at=datetime.now(UTC),
                    )
                )
                session.add_all(
                    [
                        _workflow_audit(
                            kind="approved",
                            tenant_id=tenant_id,
                            document_id=document_id,
                            graph_run_id=graph_run_id,
                            event_type=AuditEventType.DOCUMENT_APPROVED,
                            metadata={},
                        ),
                        _workflow_audit(
                            kind="outbox",
                            tenant_id=tenant_id,
                            document_id=document_id,
                            graph_run_id=graph_run_id,
                            event_type=AuditEventType.OUTBOX_EVENT_CREATED,
                            metadata={"outbox_event_id": str(event_id)},
                        ),
                    ]
                )
        return event_id

    async def _finalize(
        self,
        *,
        graph_run_id: UUID,
        tenant_id: UUID,
        document_id: UUID,
        decision: str,
        event_type: AuditEventType,
    ) -> None:
        async with self._sessions() as session, session.begin():
            task = await session.scalar(
                select(ReviewTask)
                .where(ReviewTask.graph_run_id == graph_run_id, ReviewTask.tenant_id == tenant_id)
                .with_for_update()
            )
            if task is None:
                raise RuntimeError("Final decision requires an application review task")
            if task.status != decision:
                task.status = decision
                session.add(
                    _workflow_audit(
                        kind=decision.casefold(),
                        tenant_id=tenant_id,
                        document_id=document_id,
                        graph_run_id=graph_run_id,
                        event_type=event_type,
                        metadata={},
                    )
                )


def _workflow_audit(
    *,
    kind: str,
    tenant_id: UUID,
    document_id: UUID,
    graph_run_id: UUID,
    event_type: AuditEventType,
    metadata: dict[str, object],
) -> AuditEvent:
    return AuditEvent(
        id=stable_id(f"workflow-audit-{kind}", str(graph_run_id)),
        tenant_id=tenant_id,
        document_id=document_id,
        graph_run_id=graph_run_id,
        event_type=event_type,
        actor_type=ActorType.WORKER,
        actor_id="langgraph-orchestrator",
        correlation_id=str(graph_run_id),
        sanitized_metadata=metadata,
        created_at=datetime.now(UTC),
    )


def _phase_reasons(results: list[RuleResult], phase: str) -> list[str]:
    if phase == "SCHEMA":
        selected = [
            result for result in results if result.rule_id in {"BR-001", "BR-006", "BR-010"}
        ]
    elif phase == "BUSINESS":
        selected = [
            result for result in results if result.rule_id not in {"BR-001", "BR-006", "BR-010"}
        ]
    else:
        selected = results
    return failing_reason_codes(selected)


def _confidence_components(reason_codes: list[str]) -> dict[str, Decimal | None]:
    degraded = Decimal("0") if reason_codes else Decimal("0.75")
    return {
        "required_field_completeness": degraded,
        "critical_field_confidence": degraded,
        "evidence_coverage": degraded,
        "classification_confidence": Decimal("0.50"),
        "supplier_match_quality": None,
        "purchase_order_match_quality": None,
        "deterministic_rule_score": Decimal("0") if reason_codes else Decimal("1"),
    }


def _rule_risk(reason_codes: list[str]) -> Decimal:
    return min(Decimal("1"), Decimal(len(reason_codes)) / Decimal("6"))


async def _confidence_components_from_records(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    graph_run_id: UUID,
    candidate_id: UUID | None,
    validation_run_id: UUID,
) -> dict[str, Decimal | None]:
    recommendation = await session.scalar(
        select(AgentRecommendation)
        .join(AgentRun, AgentRun.id == AgentRecommendation.agent_run_id)
        .where(
            AgentRecommendation.tenant_id == tenant_id,
            AgentRun.graph_run_id == graph_run_id,
        )
    )
    # AgentRecommendation is keyed through AgentRun; avoid treating missing provider metadata
    # as positive evidence.
    classification = (
        recommendation.classification_confidence if recommendation is not None else Decimal("0")
    )
    candidate = (
        await session.scalar(
            select(ExtractionCandidate).where(
                ExtractionCandidate.id == candidate_id,
                ExtractionCandidate.tenant_id == tenant_id,
            )
        )
        if candidate_id
        else None
    )
    data = safe_candidate_data(candidate.candidate_data) if candidate else InvoiceCandidateData()
    validation = await session.scalar(
        select(ValidationRun).where(
            ValidationRun.id == validation_run_id,
            ValidationRun.tenant_id == tenant_id,
        )
    )
    if validation is not None and validation.phase.startswith("REVIEWED_"):
        review_task = await session.scalar(
            select(ReviewTask).where(
                ReviewTask.tenant_id == tenant_id,
                ReviewTask.graph_run_id == graph_run_id,
            )
        )
        if review_task is not None:
            reviewed = await session.scalar(
                select(ReviewedDocumentVersion).where(
                    ReviewedDocumentVersion.tenant_id == tenant_id,
                    ReviewedDocumentVersion.review_task_id == review_task.id,
                    ReviewedDocumentVersion.review_cycle == review_task.cycle,
                )
            )
            if reviewed is not None:
                data = safe_candidate_data(reviewed.business_data)
    required_fields = (
        "invoice_number",
        "invoice_date",
        "supplier_name",
        "currency",
        "subtotal",
        "tax",
        "total",
    )
    completeness = Decimal(
        sum(getattr(data, name) is not None for name in required_fields)
    ) / Decimal(len(required_fields))
    evidence = (
        list(
            await session.scalars(
                select(ExtractionFieldEvidence).where(
                    ExtractionFieldEvidence.tenant_id == tenant_id,
                    ExtractionFieldEvidence.candidate_id == candidate_id,
                )
            )
        )
        if candidate_id
        else []
    )
    critical_values = [
        item.confidence
        for item in evidence
        if item.field_name in {"invoice_number", "supplier_name", "total"}
    ]
    critical = (
        sum(critical_values, Decimal("0")) / Decimal(len(critical_values))
        if critical_values
        else Decimal("0")
    )
    populated_names = {
        name for name in InvoiceCandidateData.model_fields if getattr(data, name, None) is not None
    } - {"field_confidence"}
    covered = [
        item
        for item in evidence
        if item.field_name in populated_names and item.evidence_excerpt.strip()
    ]
    coverage = (
        Decimal(len(covered)) / Decimal(len(populated_names)) if populated_names else Decimal("0")
    )
    supplier_quality: Decimal | None = None
    if data.supplier_name:
        matched_supplier = await session.scalar(
            select(Supplier.id).where(
                Supplier.tenant_id == tenant_id, Supplier.name == data.supplier_name
            )
        )
        supplier_quality = Decimal("1") if matched_supplier else Decimal("0")
    po_quality: Decimal | None = None
    if data.purchase_order_number:
        matched_po = await session.scalar(
            select(PurchaseOrder.id).where(
                PurchaseOrder.tenant_id == tenant_id,
                PurchaseOrder.purchase_order_number == data.purchase_order_number,
            )
        )
        po_quality = Decimal("1") if matched_po else Decimal("0")
    results = list(
        await session.scalars(
            select(ValidationResult).where(
                ValidationResult.tenant_id == tenant_id,
                ValidationResult.validation_run_id == validation_run_id,
            )
        )
    )
    applicable = [result for result in results if result.outcome != "NOT_APPLICABLE"]
    passes = sum(result.outcome == "PASS" for result in applicable)
    rule_score = Decimal(passes) / Decimal(len(applicable)) if applicable else Decimal("0")
    return {
        "classification_confidence": classification,
        "required_field_completeness": completeness,
        "critical_field_confidence": critical,
        "evidence_coverage": min(Decimal("1"), coverage),
        "supplier_match_quality": supplier_quality,
        "purchase_order_match_quality": po_quality,
        "deterministic_rule_score": rule_score,
    }


def _json_uuid(value: object) -> str | None:
    return str(value) if isinstance(value, UUID) else None


async def _approved_duplicate_exists(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    graph_run_id: UUID,
    candidate: InvoiceCandidateData,
) -> bool:
    if not candidate.supplier_name or not candidate.invoice_number:
        return False
    duplicate = await session.scalar(
        select(ReviewedDocumentVersion.id)
        .join(ReviewTask, ReviewTask.id == ReviewedDocumentVersion.review_task_id)
        .where(
            ReviewedDocumentVersion.tenant_id == tenant_id,
            ReviewTask.tenant_id == tenant_id,
            ReviewTask.graph_run_id != graph_run_id,
            ReviewTask.status == "APPROVED",
            ReviewedDocumentVersion.business_data["supplier_name"].as_string()
            == candidate.supplier_name,
            ReviewedDocumentVersion.business_data["invoice_number"].as_string()
            == candidate.invoice_number,
        )
        .limit(1)
    )
    return duplicate is not None


CLAIM_SCHEMA_RULES = frozenset({"CR-001", "CR-002", "CR-013", "CR-014"})


async def _upsert_claim_intake_key(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    document_id: UUID,
    graph_run_id: UUID,
    data: ClaimCandidateData,
) -> None:
    """Record this document's duplicate-detection key once its identifying fields are known."""
    if (
        not data.policy_number
        or not data.vin_reported
        or data.loss_date is None
        or data.report_date is None
    ):
        return
    key = await session.scalar(
        select(ClaimIntakeKey)
        .where(ClaimIntakeKey.tenant_id == tenant_id, ClaimIntakeKey.document_id == document_id)
        .with_for_update()
    )
    values = {
        "graph_run_id": graph_run_id,
        "policy_number": data.policy_number,
        "vin": data.vin_reported.strip().upper()[:17],
        "loss_date": data.loss_date,
        "report_date": data.report_date,
        "status": ClaimKeyStatus.ACTIVE,
    }
    if key is None:
        session.add(
            ClaimIntakeKey(
                id=stable_id("claim-intake-key", f"{tenant_id}:{document_id}"),
                tenant_id=tenant_id,
                document_id=document_id,
                **values,
            )
        )
    else:
        for name, value in values.items():
            setattr(key, name, value)


CLAIM_REQUIRED_FIELDS = (
    "policy_number",
    "named_insured",
    "loss_date",
    "report_date",
    "coverage",
    "vehicle_year",
    "vehicle_make",
    "vin_reported",
    "estimate_total",
)
CLAIM_CRITICAL_FIELDS = frozenset({"policy_number", "loss_date", "vin_reported", "estimate_total"})


async def _claim_confidence_components(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    graph_run_id: UUID,
    candidate_id: UUID | None,
    validation_run_id: UUID,
) -> dict[str, Decimal | None]:
    """Claim decision-confidence inputs: invoice components with policy and vehicle match."""
    recommendation = await session.scalar(
        select(AgentRecommendation)
        .join(AgentRun, AgentRun.id == AgentRecommendation.agent_run_id)
        .where(AgentRecommendation.tenant_id == tenant_id, AgentRun.graph_run_id == graph_run_id)
    )
    classification = (
        recommendation.classification_confidence if recommendation is not None else Decimal("0")
    )
    candidate = await session.scalar(
        select(ExtractionCandidate).where(
            ExtractionCandidate.id == candidate_id, ExtractionCandidate.tenant_id == tenant_id
        )
    )
    raw: dict[str, object] = dict(candidate.candidate_data) if candidate is not None else {}
    validation = await session.scalar(
        select(ValidationRun).where(
            ValidationRun.id == validation_run_id, ValidationRun.tenant_id == tenant_id
        )
    )
    if validation is not None and validation.phase.startswith("REVIEWED_"):
        reviewed = await session.scalar(
            select(ReviewedDocumentVersion)
            .join(ReviewTask, ReviewTask.id == ReviewedDocumentVersion.review_task_id)
            .where(
                ReviewedDocumentVersion.tenant_id == tenant_id,
                ReviewTask.graph_run_id == graph_run_id,
                ReviewedDocumentVersion.review_cycle == ReviewTask.cycle,
            )
        )
        if reviewed is not None:
            raw = dict(reviewed.business_data)
    completeness = Decimal(
        sum(raw.get(name) is not None for name in CLAIM_REQUIRED_FIELDS)
    ) / Decimal(len(CLAIM_REQUIRED_FIELDS))
    evidence = (
        list(
            await session.scalars(
                select(ExtractionFieldEvidence).where(
                    ExtractionFieldEvidence.tenant_id == tenant_id,
                    ExtractionFieldEvidence.candidate_id == candidate_id,
                )
            )
        )
        if candidate_id
        else []
    )
    critical_values = [
        item.confidence for item in evidence if item.field_name in CLAIM_CRITICAL_FIELDS
    ]
    critical = (
        sum(critical_values, Decimal("0")) / Decimal(len(critical_values))
        if critical_values
        else Decimal("0")
    )
    populated = {name for name, value in raw.items() if value is not None}
    covered = [
        item for item in evidence if item.field_name in populated and item.evidence_excerpt.strip()
    ]
    coverage = Decimal(len(covered)) / Decimal(len(populated)) if populated else Decimal("0")
    results = {
        result.rule_id: result
        for result in await session.scalars(
            select(ValidationResult).where(
                ValidationResult.tenant_id == tenant_id,
                ValidationResult.validation_run_id == validation_run_id,
            )
        )
    }
    policy_quality = _match_quality(results.get("CR-001"))
    vehicle_result = results.get("CR-005")
    vehicle_quality = (
        Decimal("0.5")
        if vehicle_result is not None and vehicle_result.outcome == "REVIEW"
        else _match_quality(vehicle_result)
    )
    applicable = [result for result in results.values() if result.outcome != "NOT_APPLICABLE"]
    passes = sum(result.outcome == "PASS" for result in applicable)
    rule_score = Decimal(passes) / Decimal(len(applicable)) if applicable else Decimal("0")
    return {
        "classification_confidence": classification,
        "required_field_completeness": completeness,
        "critical_field_confidence": critical,
        "evidence_coverage": min(Decimal("1"), coverage),
        "policy_match_quality": policy_quality,
        "vehicle_match_quality": vehicle_quality,
        "deterministic_rule_score": rule_score,
    }


def _match_quality(result: ValidationResult | None) -> Decimal | None:
    if result is None or result.outcome == "NOT_APPLICABLE":
        return None
    return Decimal("1") if result.outcome == "PASS" else Decimal("0")
