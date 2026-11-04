"""Claim-packet domain pack (ADR 0008, packaged per ADR 0009).

Holds every claim-specific behavior the shared agent, backend, and workflow dispatch to: the claim
tools' effects, advisory and authoritative CR validation, claim-routing-v1 decisions, duplicate
keys, claim decision confidence, and releasing a duplicate key on rejection.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal, cast
from uuid import UUID

from langchain_core.language_models import BaseChatModel
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from intake_api.domain.enums import (
    AuditEventType,
    ClaimKeyStatus,
    ClaimSegmentLabel,
    ReviewQueue,
)
from intake_api.domain.models import (
    AgentRecommendation,
    AgentRun,
    ClaimIntakeKey,
    ClaimRoutingDecision,
    DocumentPageSegment,
    ExtractionCandidate,
    ExtractionFieldEvidence,
    Policy,
    PolicyVehicle,
    ReviewedDocumentVersion,
    ReviewTask,
    RuleDefinition,
    ValidationResult,
    ValidationRun,
)
from intake_api.infrastructure.config import Settings
from intake_orchestrator.agent.claim_contracts import (
    ExtractClaimInput,
    LookupPolicyInput,
    LookupPolicyVehicleInput,
)
from intake_orchestrator.agent.extraction import (
    CLAIM_EXTRACTION_SCHEMA_VERSION,
    LangChainStructuredClaimExtractor,
    LangChainStructuredClaimSegmenter,
    StructuredClaimExtractor,
    StructuredClaimSegmenter,
    StubStructuredClaimExtractor,
    StubStructuredClaimSegmenter,
    candidate_completeness,
)
from intake_orchestrator.agent.pdf_content import pdf_page_count
from intake_orchestrator.agent.prompts import CLAIM_PROMPT_VERSION
from intake_orchestrator.audit_events import workflow_audit as _workflow_audit
from intake_orchestrator.claim_facts import load_claim_context
from intake_orchestrator.claim_validation import (
    CLAIM_ROUTING_VERSION,
    CLAIM_RULE_DEFINITIONS,
    CLAIM_RULESET_VERSION,
    ClaimCandidateData,
    VehicleFacts,
    match_vehicle,
    route_claim,
    safe_claim_data,
    validate_claim_rules,
)
from intake_orchestrator.confidence import CLAIM_ALGORITHM_VERSION, CLAIM_WEIGHTS
from intake_orchestrator.identifiers import stable_id
from intake_orchestrator.packs.base import (
    DomainPack,
    LookupLimit,
    PackAgentSpec,
    PackBackendServices,
    ToolEffectContext,
)
from intake_orchestrator.validation import failing_reason_codes

CLAIM_AGENT_SPEC = PackAgentSpec(
    document_type="CLAIM_PACKET",
    segmentation_tool="segment_claim_packet",
    text_extraction_tool="extract_claim_from_text",
    visual_extraction_tool="extract_claim_from_visual_content",
    lookups=(
        LookupLimit(
            "lookup_policy", "policy_lookups", "max_policy_lookups", "Policy-lookup limit reached"
        ),
        LookupLimit(
            "lookup_policy_vehicle",
            "vehicle_lookups",
            "max_vehicle_lookups",
            "Policy-vehicle-lookup limit reached",
        ),
    ),
    human_action="Verify extracted claim fields",
    deterministic_results={
        "segment_claim_packet": {
            "page_count": 2,
            "labels": ["LOSS_NOTICE", "REPAIR_ESTIMATE"],
            "reason_codes": [],
        },
        "lookup_policy": {"matched": False, "policy_id": None, "status": None},
        "lookup_policy_vehicle": {"match_kind": "NONE", "vehicle_id": None},
    },
)


class ClaimAgentEffects:
    def __init__(
        self, *, extractor: StructuredClaimExtractor, segmenter: StructuredClaimSegmenter
    ) -> None:
        self._extractor = extractor
        self._segmenter = segmenter

    async def execute(
        self,
        services: PackBackendServices,
        context: ToolEffectContext,
        tool_name: str,
        arguments: BaseModel,
    ) -> dict[str, object]:
        session = context.session
        tenant_id, document_id = context.tenant_id, context.document_id
        agent_run_id, operation_key = context.agent_run_id, context.operation_key
        if tool_name == "segment_claim_packet":
            path = await services.stored_document_path(
                session, tenant_id=tenant_id, document_id=document_id
            )
            page_count = pdf_page_count(path)
            segmentation = await self._segmenter.segment(
                page_count=page_count,
                bounded_content=await services.bounded_document_content(
                    session, tenant_id=tenant_id, document_id=document_id, include_images=True
                ),
            )
            for segment in segmentation.segments:
                session.add(
                    DocumentPageSegment(
                        id=stable_id("page-segment", f"{agent_run_id}:{segment.page_number}"),
                        tenant_id=tenant_id,
                        document_id=document_id,
                        agent_run_id=agent_run_id,
                        page_number=segment.page_number,
                        segment_label=ClaimSegmentLabel(segment.label),
                        confidence=segment.confidence,
                    )
                )
            labels = [segment.label for segment in segmentation.segments]
            return {
                "page_count": page_count,
                "labels": labels,
                "reason_codes": ["UNSUPPORTED_SEGMENT"] if "OTHER" in labels else [],
            }
        if tool_name in CLAIM_AGENT_SPEC.extraction_tools:
            parsed_claim = cast(ExtractClaimInput, arguments)
            claim_strategy: Literal["TEXT", "VISUAL"] = (
                "TEXT" if tool_name == CLAIM_AGENT_SPEC.text_extraction_tool else "VISUAL"
            )
            claim = await self._extractor.extract(
                strategy=claim_strategy,
                bounded_content=await services.bounded_document_content(
                    session,
                    tenant_id=tenant_id,
                    document_id=document_id,
                    include_images=claim_strategy == "VISUAL",
                ),
            )
            completeness = candidate_completeness(claim)
            result: dict[str, object] = {
                "extraction_run_id": str(stable_id("extraction-run", operation_key)),
                "extraction_candidate_id": str(stable_id("candidate", operation_key)),
                "strategy": claim_strategy,
                "completeness_score": float(completeness),
                "reason_codes": []
                if completeness == Decimal("1.0000")
                else ["EXTRACTION_INCOMPLETE"],
            }
            await services.persist_candidate(
                session,
                candidate=claim,
                document_type="CLAIM_PACKET",
                schema_version=CLAIM_EXTRACTION_SCHEMA_VERSION,
                prompt_version=CLAIM_PROMPT_VERSION,
                attempt_number=parsed_claim.attempt_number,
                result=result,
                identity=(tenant_id, document_id, agent_run_id),
                operation_key=operation_key,
            )
            return result
        if tool_name == "lookup_policy":
            parsed_policy = cast(LookupPolicyInput, arguments)
            policy = await session.scalar(
                select(Policy).where(
                    Policy.tenant_id == tenant_id,
                    Policy.policy_number == parsed_policy.policy_number.strip(),
                )
            )
            return {
                "matched": policy is not None,
                "policy_id": str(policy.id) if policy else None,
                "status": policy.status.value if policy else None,
            }
        if tool_name == "lookup_policy_vehicle":
            parsed_vehicle = cast(LookupPolicyVehicleInput, arguments)
            vehicle_result: dict[str, object] = {"match_kind": "NONE", "vehicle_id": None}
            policy = await session.scalar(
                select(Policy).where(
                    Policy.tenant_id == tenant_id,
                    Policy.policy_number == parsed_vehicle.policy_number.strip(),
                )
            )
            if policy is not None:
                vehicles = list(
                    await session.scalars(
                        select(PolicyVehicle).where(
                            PolicyVehicle.tenant_id == tenant_id,
                            PolicyVehicle.policy_id == policy.id,
                        )
                    )
                )
                for vehicle in vehicles:
                    kind, matched = match_vehicle(
                        parsed_vehicle.vin,
                        (
                            VehicleFacts(
                                vin=vehicle.vin,
                                model_year=vehicle.model_year,
                                make=vehicle.make,
                                actual_cash_value=vehicle.actual_cash_value,
                            ),
                        ),
                        model_year=vehicle.model_year,
                        make=vehicle.make,
                    )
                    if matched is not None:
                        vehicle_result = {"match_kind": kind, "vehicle_id": str(vehicle.id)}
                        if kind == "EXACT":
                            break
            return vehicle_result
        raise PermissionError(f"{tool_name} is not a claim-pack tool")

    async def advisory_validation(
        self,
        services: PackBackendServices,
        context: ToolEffectContext,
        candidate: ExtractionCandidate,
    ) -> dict[str, object]:
        session = context.session
        tenant_id, document_id = context.tenant_id, context.document_id
        operation_key = context.operation_key
        field_confidence = dict(
            (
                await session.execute(
                    select(
                        ExtractionFieldEvidence.field_name, ExtractionFieldEvidence.confidence
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
            update={"field_confidence": field_confidence}
        )
        results = validate_claim_rules(
            data,
            await load_claim_context(
                session,
                tenant_id=tenant_id,
                document_id=document_id,
                data=data,
                business_date=datetime.now(UTC).date(),
            ),
        )
        reasons = failing_reason_codes(results)
        validation_id = stable_id("agent-validation", operation_key)
        validation_run = ValidationRun(
            id=validation_id,
            tenant_id=tenant_id,
            document_id=document_id,
            graph_run_id=services.graph_run_id,
            extraction_candidate_id=candidate.id,
            phase=f"AGENT_{operation_key[:20]}",
            authoritative=False,
            reason_codes=reasons,
        )
        session.add(validation_run)
        await session.flush([validation_run])
        for rule_result in results:
            session.add(
                ValidationResult(
                    id=stable_id("validation-result", f"{validation_id}:{rule_result.rule_id}"),
                    tenant_id=tenant_id,
                    validation_run_id=validation_id,
                    rule_id=rule_result.rule_id,
                    rule_version=CLAIM_RULESET_VERSION,
                    outcome=rule_result.outcome,
                    reason_code=rule_result.reason_code,
                    sanitized_details={},
                )
            )
        retry_codes = {"CRITICAL_FIELD_MISSING", "CRITICAL_FIELD_LOW_CONFIDENCE"}
        return {
            "valid": not reasons,
            "retry_recommended": any(result.reason_code in retry_codes for result in results),
            "reason_codes": reasons,
        }


def build_claim_effects(
    *, settings: Settings | None, model: BaseChatModel | None, model_name: str
) -> ClaimAgentEffects:
    """Model-backed components for live providers; deterministic stubs otherwise."""
    if settings is not None and settings.agent_provider != "stub" and model is not None:
        return ClaimAgentEffects(
            extractor=LangChainStructuredClaimExtractor(
                model=model,
                provider=settings.agent_provider,
                model_name=settings.agent_model,
                prompt_version=CLAIM_PROMPT_VERSION,
            ),
            segmenter=LangChainStructuredClaimSegmenter(model=model),
        )
    return ClaimAgentEffects(
        extractor=StubStructuredClaimExtractor(
            model=model_name, prompt_version=CLAIM_PROMPT_VERSION
        ),
        segmenter=StubStructuredClaimSegmenter(),
    )


async def validate_claim(
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


class ClaimWorkflow:
    confidence_weights: Mapping[str, Decimal] = CLAIM_WEIGHTS
    confidence_algorithm: str = CLAIM_ALGORITHM_VERSION

    async def validate(
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
        return await validate_claim(
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

    async def confidence_components(
        self,
        session: AsyncSession,
        *,
        tenant_id: UUID,
        graph_run_id: UUID,
        candidate_id: UUID | None,
        validation_run_id: UUID,
    ) -> dict[str, Decimal | None]:
        return await _claim_confidence_components(
            session,
            tenant_id=tenant_id,
            graph_run_id=graph_run_id,
            candidate_id=candidate_id,
            validation_run_id=validation_run_id,
        )

    async def on_rejection(
        self, session: AsyncSession, *, tenant_id: UUID, document_id: UUID
    ) -> None:
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


CLAIMS_PACK = DomainPack(
    agent=CLAIM_AGENT_SPEC, build_effects=build_claim_effects, workflow=ClaimWorkflow()
)
