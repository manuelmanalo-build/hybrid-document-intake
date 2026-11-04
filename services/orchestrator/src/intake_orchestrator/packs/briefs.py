"""Campaign-brief domain pack for agencies (ADR 0009).

The brief tools' effects, advisory and authoritative CB validation, brief-routing-v1 decisions,
duplicate keys, brief decision confidence, and releasing a duplicate key on rejection.
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

from intake_api.domain.enums import AuditEventType, ClaimKeyStatus, PageSegmentLabel, ReviewQueue
from intake_api.domain.models import (
    AgentRecommendation,
    AgentRun,
    BriefIntakeKey,
    ClaimRoutingDecision,
    DocumentPageSegment,
    ExtractionCandidate,
    ExtractionFieldEvidence,
    ReviewedDocumentVersion,
    ReviewTask,
    RuleDefinition,
    ValidationResult,
    ValidationRun,
)
from intake_api.infrastructure.config import Settings
from intake_orchestrator.agent.brief_contracts import LookupClientSowInput
from intake_orchestrator.agent.brief_extraction import (
    BRIEF_EXTRACTION_SCHEMA_VERSION,
    LangChainStructuredBriefExtractor,
    LangChainStructuredBriefSegmenter,
    StructuredBriefExtractor,
    StructuredBriefSegmenter,
    StubStructuredBriefExtractor,
    StubStructuredBriefSegmenter,
)
from intake_orchestrator.agent.claim_contracts import ExtractClaimInput
from intake_orchestrator.agent.extraction import candidate_completeness
from intake_orchestrator.agent.pdf_content import pdf_page_count
from intake_orchestrator.agent.prompts import BRIEF_PROMPT_VERSION
from intake_orchestrator.audit_events import workflow_audit
from intake_orchestrator.brief_facts import brand_sows, campaign_key, load_brief_context
from intake_orchestrator.brief_validation import (
    BRIEF_ROUTING_VERSION,
    BRIEF_RULE_DEFINITIONS,
    BRIEF_RULESET_VERSION,
    BriefCandidateData,
    resolve_sow,
    route_brief,
    safe_brief_data,
    validate_brief_rules,
)
from intake_orchestrator.identifiers import stable_id
from intake_orchestrator.packs.base import (
    DomainPack,
    LookupLimit,
    PackAgentSpec,
    PackBackendServices,
    ToolEffectContext,
)
from intake_orchestrator.validation import RuleResult, failing_reason_codes

BRIEF_SCHEMA_RULES = frozenset({"CB-003", "CB-012", "CB-013", "CB-014"})
BRIEF_REQUIRED_FIELDS = (
    "client_name",
    "brand",
    "campaign_name",
    "received_date",
    "objective",
    "primary_kpi",
    "kpi_target",
    "audience",
    "budget_total",
    "launch_date",
    "end_date",
)
BRIEF_CRITICAL_FIELDS = frozenset({"client_name", "brand", "campaign_name", "launch_date"})
BRIEF_ALGORITHM_VERSION = "decision-confidence-briefs-v1"
BRIEF_WEIGHTS: Mapping[str, Decimal] = {
    "classification_confidence": Decimal("0.15"),
    "required_field_completeness": Decimal("0.20"),
    "critical_field_confidence": Decimal("0.20"),
    "evidence_coverage": Decimal("0.15"),
    "sow_match_quality": Decimal("0.10"),
    "scope_fit": Decimal("0.10"),
    "deterministic_rule_score": Decimal("0.10"),
}

BRIEF_AGENT_SPEC = PackAgentSpec(
    document_type="CAMPAIGN_BRIEF",
    segmentation_tool="segment_brief_packet",
    text_extraction_tool="extract_brief_from_text",
    visual_extraction_tool="extract_brief_from_visual_content",
    lookups=(
        LookupLimit(
            "lookup_client_sow", "sow_lookups", "max_sow_lookups", "SOW-lookup limit reached"
        ),
    ),
    human_action="Verify the extracted work request",
    deterministic_results={
        "segment_brief_packet": {"page_count": 1, "labels": ["BRIEF"], "reason_codes": []},
        "lookup_client_sow": {"matched": False, "sow_id": None, "sow_number": None, "status": None},
    },
    stub_arguments={"lookup_client_sow": {"brand": "Harbor & Vine"}},
)


class BriefAgentEffects:
    def __init__(
        self, *, extractor: StructuredBriefExtractor, segmenter: StructuredBriefSegmenter
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
        if tool_name == BRIEF_AGENT_SPEC.segmentation_tool:
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
                        segment_label=PageSegmentLabel(segment.label),
                        confidence=segment.confidence,
                    )
                )
            labels = [segment.label for segment in segmentation.segments]
            return {
                "page_count": page_count,
                "labels": labels,
                "reason_codes": ["UNSUPPORTED_SEGMENT"] if "OTHER" in labels else [],
            }
        if tool_name in BRIEF_AGENT_SPEC.extraction_tools:
            parsed = cast(ExtractClaimInput, arguments)
            strategy: Literal["TEXT", "VISUAL"] = (
                "TEXT" if tool_name == BRIEF_AGENT_SPEC.text_extraction_tool else "VISUAL"
            )
            brief = await self._extractor.extract(
                strategy=strategy,
                bounded_content=await services.bounded_document_content(
                    session,
                    tenant_id=tenant_id,
                    document_id=document_id,
                    include_images=strategy == "VISUAL",
                ),
            )
            completeness = candidate_completeness(brief)
            reasons = [] if completeness == Decimal("1.0000") else ["EXTRACTION_INCOMPLETE"]
            if not brief.deliverables:
                reasons.append("NO_DELIVERABLES_EXTRACTED")
            result: dict[str, object] = {
                "extraction_run_id": str(stable_id("extraction-run", operation_key)),
                "extraction_candidate_id": str(stable_id("candidate", operation_key)),
                "strategy": strategy,
                "completeness_score": float(completeness),
                "reason_codes": reasons,
            }
            await services.persist_candidate(
                session,
                candidate=brief,
                document_type=BRIEF_AGENT_SPEC.document_type,
                schema_version=BRIEF_EXTRACTION_SCHEMA_VERSION,
                prompt_version=BRIEF_PROMPT_VERSION,
                attempt_number=parsed.attempt_number,
                result=result,
                identity=(tenant_id, document_id, agent_run_id),
                operation_key=operation_key,
            )
            return result
        if tool_name == "lookup_client_sow":
            lookup = cast(LookupClientSowInput, arguments)
            sows = await brand_sows(session, tenant_id=tenant_id, brand=lookup.brand)
            sow = resolve_sow(BriefCandidateData(sow_number_stated=lookup.sow_number), sows)
            return {
                "matched": sow is not None,
                "sow_id": str(stable_id("sow-reference", f"{tenant_id}:{sow.sow_number}"))
                if sow
                else None,
                "sow_number": sow.sow_number if sow else None,
                "status": sow.status if sow else None,
            }
        raise PermissionError(f"{tool_name} is not a brief-pack tool")

    async def advisory_validation(
        self,
        services: PackBackendServices,
        context: ToolEffectContext,
        candidate: ExtractionCandidate,
    ) -> dict[str, object]:
        session = context.session
        data = await _candidate_data(session, context.tenant_id, candidate)
        results = validate_brief_rules(
            data,
            await load_brief_context(
                session,
                tenant_id=context.tenant_id,
                document_id=context.document_id,
                data=data,
                business_date=datetime.now(UTC).date(),
            ),
        )
        reasons = failing_reason_codes(results)
        validation_id = stable_id("agent-validation", context.operation_key)
        run = ValidationRun(
            id=validation_id,
            tenant_id=context.tenant_id,
            document_id=context.document_id,
            graph_run_id=services.graph_run_id,
            extraction_candidate_id=candidate.id,
            phase=f"AGENT_{context.operation_key[:20]}",
            authoritative=False,
            reason_codes=reasons,
        )
        session.add(run)
        await session.flush([run])
        _add_results(session, context.tenant_id, validation_id, results)
        retry_codes = {"CRITICAL_FIELD_MISSING", "CRITICAL_FIELD_LOW_CONFIDENCE"}
        return {
            "valid": not reasons,
            "retry_recommended": any(result.reason_code in retry_codes for result in results),
            "reason_codes": reasons,
        }


def build_brief_effects(
    *, settings: Settings | None, model: BaseChatModel | None, model_name: str
) -> BriefAgentEffects:
    """Model-backed components for live providers; deterministic stubs otherwise."""
    if settings is not None and settings.agent_provider != "stub" and model is not None:
        return BriefAgentEffects(
            extractor=LangChainStructuredBriefExtractor(
                model=model,
                provider=settings.agent_provider,
                model_name=settings.agent_model,
                prompt_version=BRIEF_PROMPT_VERSION,
            ),
            segmenter=LangChainStructuredBriefSegmenter(model=model),
        )
    return BriefAgentEffects(
        extractor=StubStructuredBriefExtractor(
            model=model_name, prompt_version=BRIEF_PROMPT_VERSION
        ),
        segmenter=StubStructuredBriefSegmenter(),
    )


class BriefWorkflow:
    confidence_weights: Mapping[str, Decimal] = BRIEF_WEIGHTS
    confidence_algorithm: str = BRIEF_ALGORITHM_VERSION

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
        """Authoritative CB rules, duplicate key, and brief-routing-v1 decision."""
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
            data = safe_brief_data(reviewed.business_data)
        else:
            data = await _candidate_data(session, tenant_id, candidate)
        context = await load_brief_context(
            session,
            tenant_id=tenant_id,
            document_id=document_id,
            data=data,
            business_date=datetime.now(UTC).date(),
        )
        results = validate_brief_rules(data, context)
        selected = (
            [result for result in results if result.rule_id in BRIEF_SCHEMA_RULES]
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
        for rule_id, name, severity in BRIEF_RULE_DEFINITIONS:
            definition_id = stable_id(
                "rule-definition", f"{tenant_id}:{BRIEF_RULESET_VERSION}:{rule_id}"
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
                        version=BRIEF_RULESET_VERSION,
                        name=name,
                        severity=severity,
                        configuration={},
                    )
                )
        _add_results(session, tenant_id, run_id, results)
        metadata: dict[str, object] = {"phase": stored_phase, "reason_codes": reasons}
        if phase != "SCHEMA":
            route = route_brief(results)
            session.add(
                ClaimRoutingDecision(
                    id=stable_id("brief-routing", str(run_id)),
                    tenant_id=tenant_id,
                    document_id=document_id,
                    graph_run_id=graph_run_id,
                    validation_run_id=run_id,
                    review_queue=ReviewQueue(route.queue),
                    routing_version=BRIEF_ROUTING_VERSION,
                    reason_code=route.reason_code,
                    flags=route.flags,
                )
            )
            sow = resolve_sow(data, context.brand_sows)
            await _upsert_brief_key(
                session,
                tenant_id=tenant_id,
                document_id=document_id,
                graph_run_id=graph_run_id,
                sow_number=sow.sow_number if sow else None,
                data=data,
            )
            metadata.update(
                {
                    "review_queue": route.queue,
                    "routing_version": BRIEF_ROUTING_VERSION,
                    "routing_reason": route.reason_code,
                }
            )
        session.add(
            workflow_audit(
                kind=f"validation-{stored_phase}",
                tenant_id=tenant_id,
                document_id=document_id,
                graph_run_id=graph_run_id,
                event_type=AuditEventType.AUTHORITATIVE_VALIDATION_COMPLETED,
                metadata=metadata,
            )
        )
        return reasons

    async def confidence_components(
        self,
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
                AgentRecommendation.tenant_id == tenant_id, AgentRun.graph_run_id == graph_run_id
            )
        )
        candidate = await session.scalar(
            select(ExtractionCandidate).where(
                ExtractionCandidate.id == candidate_id, ExtractionCandidate.tenant_id == tenant_id
            )
        )
        raw: dict[str, object] = dict(candidate.candidate_data) if candidate is not None else {}
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
        critical = [
            item.confidence for item in evidence if item.field_name in BRIEF_CRITICAL_FIELDS
        ]
        populated = {name for name, value in raw.items() if value not in (None, [], "")}
        covered = {item.field_name.split("[")[0] for item in evidence if item.evidence_excerpt}
        results = {
            result.rule_id: result.outcome
            for result in await session.scalars(
                select(ValidationResult).where(
                    ValidationResult.tenant_id == tenant_id,
                    ValidationResult.validation_run_id == validation_run_id,
                )
            )
        }
        applicable = [outcome for outcome in results.values() if outcome != "NOT_APPLICABLE"]
        return {
            "classification_confidence": recommendation.classification_confidence
            if recommendation
            else Decimal("0"),
            "required_field_completeness": Decimal(
                sum(raw.get(name) not in (None, "") for name in BRIEF_REQUIRED_FIELDS)
            )
            / Decimal(len(BRIEF_REQUIRED_FIELDS)),
            "critical_field_confidence": sum(critical, Decimal("0")) / Decimal(len(critical))
            if critical
            else Decimal("0"),
            "evidence_coverage": Decimal(len(populated & covered)) / Decimal(len(populated))
            if populated
            else Decimal("0"),
            "sow_match_quality": _passed(results, "CB-001", "CB-002"),
            "scope_fit": _passed(results, "CB-006", "CB-007"),
            "deterministic_rule_score": Decimal(sum(outcome == "PASS" for outcome in applicable))
            / Decimal(len(applicable))
            if applicable
            else Decimal("0"),
        }

    async def on_rejection(
        self, session: AsyncSession, *, tenant_id: UUID, document_id: UUID
    ) -> None:
        key = await session.scalar(
            select(BriefIntakeKey)
            .where(BriefIntakeKey.tenant_id == tenant_id, BriefIntakeKey.document_id == document_id)
            .with_for_update()
        )
        # A rejected brief must not block a later legitimate brief as a duplicate (CB-011).
        if key is not None:
            key.status = ClaimKeyStatus.REJECTED


async def _candidate_data(
    session: AsyncSession, tenant_id: UUID, candidate: ExtractionCandidate
) -> BriefCandidateData:
    confidences = dict(
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
    return safe_brief_data(candidate.candidate_data).model_copy(
        update={"field_confidence": confidences}
    )


def _add_results(
    session: AsyncSession, tenant_id: UUID, validation_id: UUID, results: list[RuleResult]
) -> None:
    for result in results:
        session.add(
            ValidationResult(
                id=stable_id("validation-result", f"{validation_id}:{result.rule_id}"),
                tenant_id=tenant_id,
                validation_run_id=validation_id,
                rule_id=result.rule_id,
                rule_version=BRIEF_RULESET_VERSION,
                outcome=result.outcome,
                reason_code=result.reason_code,
                sanitized_details={},
            )
        )


def _passed(results: Mapping[str, str], *rule_ids: str) -> Decimal | None:
    outcomes = [
        results[rule_id]
        for rule_id in rule_ids
        if results.get(rule_id) not in (None, "NOT_APPLICABLE")
    ]
    if not outcomes:
        return None
    return Decimal("1") if all(outcome == "PASS" for outcome in outcomes) else Decimal("0")


async def _upsert_brief_key(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    document_id: UUID,
    graph_run_id: UUID,
    sow_number: str | None,
    data: BriefCandidateData,
) -> None:
    if not sow_number or not data.campaign_name or data.launch_date is None:
        return
    key = await session.scalar(
        select(BriefIntakeKey)
        .where(BriefIntakeKey.tenant_id == tenant_id, BriefIntakeKey.document_id == document_id)
        .with_for_update()
    )
    values = {
        "graph_run_id": graph_run_id,
        "sow_number": sow_number,
        "campaign_key": campaign_key(data.campaign_name),
        "launch_date": data.launch_date,
        "status": ClaimKeyStatus.ACTIVE,
    }
    if key is None:
        session.add(
            BriefIntakeKey(
                id=stable_id("brief-intake-key", f"{tenant_id}:{document_id}"),
                tenant_id=tenant_id,
                document_id=document_id,
                **values,
            )
        )
    else:
        for name, value in values.items():
            setattr(key, name, value)


BRIEFS_PACK = DomainPack(
    agent=BRIEF_AGENT_SPEC, build_effects=build_brief_effects, workflow=BriefWorkflow()
)
