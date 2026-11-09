"""CDP data-source onboarding domain pack (ADR 0010).

The deterministic personal-data scan runs first; every later model call in the run receives text
that `pii-scanner-v1` has redacted, and evidence is redacted again before it is stored.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Literal, cast
from uuid import UUID

from langchain_core.language_models import BaseChatModel
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from intake_api.application.integration_payloads import cdp_jira
from intake_api.domain.enums import AuditEventType, ClaimKeyStatus, PageSegmentLabel, ReviewQueue
from intake_api.domain.models import (
    AgentRecommendation,
    AgentRun,
    CanonicalAttribute,
    ClaimRoutingDecision,
    DocumentPageSegment,
    ExtractionCandidate,
    ExtractionFieldEvidence,
    OnboardingIntakeKey,
    PiiScanFinding,
    ReviewedDocumentVersion,
    ReviewTask,
    RuleDefinition,
    ValidationResult,
    ValidationRun,
)
from intake_api.infrastructure.config import Settings
from intake_orchestrator.agent.cdp_contracts import (
    LookupDataAgreementInput,
    OnboardingRequestCandidate,
)
from intake_orchestrator.agent.cdp_extraction import (
    CDP_EXTRACTION_SCHEMA_VERSION,
    LangChainStructuredOnboardingExtractor,
    LangChainStructuredOnboardingSegmenter,
    StructuredOnboardingExtractor,
    StructuredOnboardingSegmenter,
    StubStructuredOnboardingExtractor,
    StubStructuredOnboardingSegmenter,
)
from intake_orchestrator.agent.claim_contracts import ExtractClaimInput
from intake_orchestrator.agent.extraction import BoundedExtractionContent, candidate_completeness
from intake_orchestrator.agent.pdf_content import pdf_page_count, read_bounded_pdf_content
from intake_orchestrator.agent.prompts import CDP_PROMPT_VERSION
from intake_orchestrator.audit_events import workflow_audit
from intake_orchestrator.cdp_facts import client_agreements, load_onboarding_context, source_key
from intake_orchestrator.cdp_validation import (
    CDP_ROUTING_VERSION,
    CDP_RULE_DEFINITIONS,
    CDP_RULESET_VERSION,
    OnboardingCandidateData,
    resolve_agreement,
    route_onboarding,
    safe_onboarding_data,
    validate_onboarding_rules,
)
from intake_orchestrator.identifiers import stable_id
from intake_orchestrator.packs.base import (
    DomainPack,
    LookupLimit,
    PackAgentSpec,
    PackBackendServices,
    ToolEffectContext,
)
from intake_orchestrator.pii import PII_SCANNER_VERSION, redact_identifiers, scan_identifiers
from intake_orchestrator.validation import RuleResult, failing_reason_codes

CDP_SCHEMA_RULES = frozenset({"CD-003", "CD-015"})
CDP_REQUIRED_FIELDS = (
    "client_name",
    "source_name",
    "connector_type",
    "refresh_cadence",
    "data_region",
    "retention_days",
    "requested_go_live",
)
CDP_CRITICAL_FIELDS = frozenset(
    {"client_name", "connector_type", "source_name", "requested_go_live"}
)
CDP_ALGORITHM_VERSION = "decision-confidence-cdp-v1"
CDP_WEIGHTS: Mapping[str, Decimal] = {
    "classification_confidence": Decimal("0.10"),
    "required_field_completeness": Decimal("0.15"),
    "critical_field_confidence": Decimal("0.15"),
    "evidence_coverage": Decimal("0.10"),
    "agreement_match_quality": Decimal("0.10"),
    "mapping_validity": Decimal("0.20"),
    "privacy_clearance": Decimal("0.10"),
    "deterministic_rule_score": Decimal("0.10"),
}
PRIVACY_RULES = ("CD-007", "CD-008", "CD-009", "CD-010", "CD-011")

CDP_AGENT_SPEC = PackAgentSpec(
    document_type="CDP_ONBOARDING",
    segmentation_tool="segment_onboarding_packet",
    text_extraction_tool="extract_onboarding_from_text",
    visual_extraction_tool="extract_onboarding_from_visual_content",
    lookups=(
        LookupLimit(
            "lookup_data_agreement",
            "agreement_lookups",
            "max_agreement_lookups",
            "Agreement-lookup limit reached",
        ),
    ),
    human_action="Verify the onboarding request and confirm every field mapping",
    deterministic_results={
        "scan_onboarding_packet": {
            "scanner_version": PII_SCANNER_VERSION,
            "pages": [{"page_number": 1, "text_layer": True, "findings": dict[str, int]()}],
            "total_identifiers": 0,
            "reason_codes": list[str](),
        },
        "segment_onboarding_packet": {
            "page_count": 1,
            "labels": ["ONBOARDING_FORM"],
            "reason_codes": [],
        },
        "lookup_data_agreement": {
            "matched": False,
            "agreement_number": None,
            "status": None,
            "agreement_reference": None,
        },
    },
    stub_arguments={"lookup_data_agreement": {"client_name": "Harbor & Vine Coffee Co."}},
    preparation_tool="scan_onboarding_packet",
)


def redacted(content: BoundedExtractionContent) -> BoundedExtractionContent:
    return BoundedExtractionContent(
        text=redact_identifiers(content.text), image_data_urls=content.image_data_urls
    )


def redact_candidate(candidate: OnboardingRequestCandidate) -> OnboardingRequestCandidate:
    """Redact every string value and evidence excerpt before persistence (scans may carry PII)."""
    payload = candidate.model_dump()
    for name, value in payload.items():
        if isinstance(value, list):
            for item in cast(list[dict[str, object]], value):
                item["evidence_excerpt"] = redact_identifiers(str(item["evidence_excerpt"]))
                item["source_field"] = redact_identifiers(str(item["source_field"]))
            continue
        field = cast(dict[str, object], value)
        field["evidence_excerpt"] = redact_identifiers(str(field["evidence_excerpt"]))
        if isinstance(field.get("value"), str):
            field["value"] = redact_identifiers(cast(str, field["value"]))
        payload[name] = field
    return OnboardingRequestCandidate.model_validate(payload)


class CdpAgentEffects:
    def __init__(
        self, *, extractor: StructuredOnboardingExtractor, segmenter: StructuredOnboardingSegmenter
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
        if tool_name == "scan_onboarding_packet":
            path = await services.stored_document_path(
                session, tenant_id=tenant_id, document_id=document_id
            )
            pages = _page_texts(read_bounded_pdf_content(path, include_images=False).text)
            page_count = pdf_page_count(path)
            results: list[dict[str, object]] = []
            total = 0
            for page in range(1, page_count + 1):
                text = pages.get(page, "")
                has_text = bool(text.strip())
                findings = scan_identifiers(text) if has_text else {}
                total += sum(findings.values())
                rows = {**findings} if has_text else {"NO_TEXT_LAYER": 0}
                for kind, count in rows.items():
                    session.add(
                        PiiScanFinding(
                            id=stable_id("pii-finding", f"{agent_run_id}:{page}:{kind}"),
                            tenant_id=tenant_id,
                            document_id=document_id,
                            agent_run_id=agent_run_id,
                            page_number=page,
                            identifier_type=kind,
                            occurrences=count,
                            scanner_version=PII_SCANNER_VERSION,
                        )
                    )
                results.append({"page_number": page, "text_layer": has_text, "findings": findings})
            return {
                "scanner_version": PII_SCANNER_VERSION,
                "pages": results,
                "total_identifiers": total,
                "reason_codes": ["UNMASKED_IDENTIFIERS_FOUND"] if total else [],
            }
        if tool_name == CDP_AGENT_SPEC.segmentation_tool:
            path = await services.stored_document_path(
                session, tenant_id=tenant_id, document_id=document_id
            )
            page_count = pdf_page_count(path)
            segmentation = await self._segmenter.segment(
                page_count=page_count,
                bounded_content=redacted(
                    await services.bounded_document_content(
                        session, tenant_id=tenant_id, document_id=document_id, include_images=True
                    )
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
        if tool_name in CDP_AGENT_SPEC.extraction_tools:
            parsed = cast(ExtractClaimInput, arguments)
            strategy: Literal["TEXT", "VISUAL"] = (
                "TEXT" if tool_name == CDP_AGENT_SPEC.text_extraction_tool else "VISUAL"
            )
            request = redact_candidate(
                await self._extractor.extract(
                    strategy=strategy,
                    bounded_content=redacted(
                        await services.bounded_document_content(
                            session,
                            tenant_id=tenant_id,
                            document_id=document_id,
                            include_images=strategy == "VISUAL",
                        )
                    ),
                )
            )
            completeness = candidate_completeness(request)
            reasons = [] if completeness == Decimal("1.0000") else ["EXTRACTION_INCOMPLETE"]
            if not request.mappings:
                reasons.append("NO_MAPPINGS_EXTRACTED")
            result: dict[str, object] = {
                "extraction_run_id": str(stable_id("extraction-run", operation_key)),
                "extraction_candidate_id": str(stable_id("candidate", operation_key)),
                "strategy": strategy,
                "completeness_score": float(completeness),
                "reason_codes": reasons,
            }
            await services.persist_candidate(
                session,
                candidate=request,
                document_type=CDP_AGENT_SPEC.document_type,
                schema_version=CDP_EXTRACTION_SCHEMA_VERSION,
                prompt_version=CDP_PROMPT_VERSION,
                attempt_number=parsed.attempt_number,
                result=result,
                identity=(tenant_id, document_id, agent_run_id),
                operation_key=operation_key,
            )
            return result
        if tool_name == "lookup_data_agreement":
            lookup = cast(LookupDataAgreementInput, arguments)
            agreements = await client_agreements(
                session, tenant_id=tenant_id, client_name=lookup.client_name
            )
            agreement = resolve_agreement(
                OnboardingCandidateData(agreement_number_stated=lookup.agreement_number), agreements
            )
            return {
                "matched": agreement is not None,
                "agreement_number": agreement.agreement_number if agreement else None,
                "status": agreement.status if agreement else None,
                "agreement_reference": str(
                    stable_id("agreement-reference", f"{tenant_id}:{agreement.agreement_number}")
                )
                if agreement
                else None,
            }
        raise PermissionError(f"{tool_name} is not a CDP-pack tool")

    async def advisory_validation(
        self,
        services: PackBackendServices,
        context: ToolEffectContext,
        candidate: ExtractionCandidate,
    ) -> dict[str, object]:
        session = context.session
        data = await _candidate_data(session, context.tenant_id, candidate)
        results = validate_onboarding_rules(
            data,
            await load_onboarding_context(
                session, tenant_id=context.tenant_id, document_id=context.document_id, data=data
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
        return {
            "valid": not reasons,
            "retry_recommended": "CRITICAL_FIELD_LOW_CONFIDENCE" in reasons,
            "reason_codes": reasons,
        }


def build_cdp_effects(
    *, settings: Settings | None, model: BaseChatModel | None, model_name: str
) -> CdpAgentEffects:
    if settings is not None and settings.agent_provider != "stub" and model is not None:
        return CdpAgentEffects(
            extractor=LangChainStructuredOnboardingExtractor(
                model=model,
                provider=settings.agent_provider,
                model_name=settings.agent_model,
                prompt_version=CDP_PROMPT_VERSION,
            ),
            segmenter=LangChainStructuredOnboardingSegmenter(model=model),
        )
    return CdpAgentEffects(
        extractor=StubStructuredOnboardingExtractor(
            model=model_name, prompt_version=CDP_PROMPT_VERSION
        ),
        segmenter=StubStructuredOnboardingSegmenter(),
    )


class CdpWorkflow:
    confidence_weights: Mapping[str, Decimal] = CDP_WEIGHTS
    confidence_algorithm: str = CDP_ALGORITHM_VERSION

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
        """Authoritative CD rules, duplicate key, and cdp-routing-v1 decision."""
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
            data = safe_onboarding_data(reviewed.business_data)
        else:
            data = await _candidate_data(session, tenant_id, candidate)
        context = await load_onboarding_context(
            session, tenant_id=tenant_id, document_id=document_id, data=data
        )
        results = validate_onboarding_rules(data, context)
        selected = (
            [result for result in results if result.rule_id in CDP_SCHEMA_RULES]
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
        for rule_id, name, severity in CDP_RULE_DEFINITIONS:
            definition_id = stable_id(
                "rule-definition", f"{tenant_id}:{CDP_RULESET_VERSION}:{rule_id}"
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
                        version=CDP_RULESET_VERSION,
                        name=name,
                        severity=severity,
                        configuration={},
                    )
                )
        _add_results(session, tenant_id, run_id, results)
        metadata: dict[str, object] = {"phase": stored_phase, "reason_codes": reasons}
        if phase != "SCHEMA":
            route = route_onboarding(results)
            session.add(
                ClaimRoutingDecision(
                    id=stable_id("cdp-routing", str(run_id)),
                    tenant_id=tenant_id,
                    document_id=document_id,
                    graph_run_id=graph_run_id,
                    validation_run_id=run_id,
                    review_queue=ReviewQueue(route.queue),
                    routing_version=CDP_ROUTING_VERSION,
                    reason_code=route.reason_code,
                    flags=route.flags,
                )
            )
            agreement = resolve_agreement(data, context.agreements)
            await _upsert_key(
                session,
                tenant_id=tenant_id,
                document_id=document_id,
                agreement_number=agreement.agreement_number if agreement else None,
                data=data,
            )
            metadata.update(
                {
                    "review_queue": route.queue,
                    "routing_version": CDP_ROUTING_VERSION,
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
        results = {
            result.rule_id: result.outcome
            for result in await session.scalars(
                select(ValidationResult).where(
                    ValidationResult.tenant_id == tenant_id,
                    ValidationResult.validation_run_id == validation_run_id,
                )
            )
        }
        known = set(
            await session.scalars(
                select(CanonicalAttribute.attribute).where(
                    CanonicalAttribute.tenant_id == tenant_id
                )
            )
        )
        mappings = [item for item in safe_onboarding_data(raw).mappings if not item.excluded]
        critical = [item.confidence for item in evidence if item.field_name in CDP_CRITICAL_FIELDS]
        populated = {name for name, value in raw.items() if value not in (None, [], "")}
        covered = {item.field_name.split("[")[0] for item in evidence if item.evidence_excerpt}
        applicable = [outcome for outcome in results.values() if outcome != "NOT_APPLICABLE"]
        privacy = [results.get(rule_id) for rule_id in PRIVACY_RULES]
        return {
            "classification_confidence": recommendation.classification_confidence
            if recommendation
            else Decimal("0"),
            "required_field_completeness": _ratio(
                sum(raw.get(name) not in (None, "") for name in CDP_REQUIRED_FIELDS),
                len(CDP_REQUIRED_FIELDS),
            ),
            "critical_field_confidence": sum(critical, Decimal("0")) / Decimal(len(critical))
            if critical
            else Decimal("0"),
            "evidence_coverage": _ratio(len(populated & covered), len(populated)),
            "agreement_match_quality": Decimal("1")
            if results.get("CD-001") == "PASS" and results.get("CD-002") == "PASS"
            else Decimal("0"),
            "mapping_validity": _ratio(
                sum(item.proposed_attribute in known for item in mappings), len(mappings)
            ),
            "privacy_clearance": Decimal("1")
            if all(outcome in ("PASS", "NOT_APPLICABLE", None) for outcome in privacy)
            else Decimal("0"),
            "deterministic_rule_score": _ratio(
                sum(outcome == "PASS" for outcome in applicable), len(applicable)
            ),
        }

    async def on_rejection(
        self, session: AsyncSession, *, tenant_id: UUID, document_id: UUID
    ) -> None:
        key = await session.scalar(
            select(OnboardingIntakeKey)
            .where(
                OnboardingIntakeKey.tenant_id == tenant_id,
                OnboardingIntakeKey.document_id == document_id,
            )
            .with_for_update()
        )
        if key is not None:
            key.status = ClaimKeyStatus.REJECTED

    async def outbox_payload(
        self,
        session: AsyncSession,
        *,
        tenant_id: UUID,
        document_id: UUID,
        event_key: str,
        business_data: Mapping[str, object],
    ) -> dict[str, object]:
        """An approved onboarding plan: the confirmed mapping spec and one task per workstream."""
        attributes = {
            row.attribute: row
            for row in await session.scalars(
                select(CanonicalAttribute).where(CanonicalAttribute.tenant_id == tenant_id)
            )
        }
        data = safe_onboarding_data(business_data)
        agreement = resolve_agreement(
            data,
            await client_agreements(session, tenant_id=tenant_id, client_name=data.client_name),
        )
        mapped: list[dict[str, object]] = []
        excluded: list[str] = []
        for item in [*data.mappings, *data.fields_added_in_correspondence]:
            attribute = attributes.get(item.proposed_attribute or "")
            if item.excluded or attribute is None:
                excluded.append(item.source_field)
                continue
            mapped.append(
                {
                    "source_field": item.source_field,
                    "attribute": attribute.attribute,
                    "entity": attribute.entity,
                    "personal_class": attribute.personal_class,
                    "identity_key": attribute.identity_key,
                }
            )
        identity_keys = [entry["attribute"] for entry in mapped if entry["identity_key"]]
        personal = any(entry["personal_class"] != "NONE" for entry in mapped)
        go_live = data.go_live_in_correspondence or data.requested_go_live
        source = data.source_name or "the source"
        tasks = [
            ("connector", f"Configure {data.connector_type} connector for {source}"),
            ("mapping", f"Implement {len(mapped)} field mappings for {source}"),
            ("identity", f"Configure identity resolution on {', '.join(map(str, identity_keys))}"),
        ]
        if personal:
            tasks.append(("consent", f"Configure consent enforcement ({data.lawful_basis})"))
        tasks.append(("qa", f"QA {source} against the redacted sample"))
        plan: dict[str, object] = {
            "schema_version": "onboarding-plan-v1",
            "client_name": data.client_name,
            "agreement_number": agreement.agreement_number if agreement else None,
            "source_name": data.source_name,
            "connector_type": data.connector_type,
            "refresh_cadence": data.refresh_cadence,
            "data_region": data.data_region,
            "lawful_basis": data.lawful_basis,
            "retention_days": data.retention_days,
            "go_live": go_live.isoformat() if go_live else None,
            "mappings": mapped,
            "excluded_fields": excluded,
            "tasks": [{"task_key": key, "title": title} for key, title in tasks],
        }
        return {
            "document_type": "CDP_ONBOARDING",
            "onboarding_plan": plan,
            "jira": cdp_jira(plan, event_key=event_key),
        }


async def _candidate_data(
    session: AsyncSession, tenant_id: UUID, candidate: ExtractionCandidate
) -> OnboardingCandidateData:
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
    return safe_onboarding_data(candidate.candidate_data).model_copy(
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
                rule_version=CDP_RULESET_VERSION,
                outcome=result.outcome,
                reason_code=result.reason_code,
                sanitized_details={},
            )
        )


async def _upsert_key(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    document_id: UUID,
    agreement_number: str | None,
    data: OnboardingCandidateData,
) -> None:
    if not agreement_number or not data.connector_type or not data.source_name:
        return
    key = await session.scalar(
        select(OnboardingIntakeKey)
        .where(
            OnboardingIntakeKey.tenant_id == tenant_id,
            OnboardingIntakeKey.document_id == document_id,
        )
        .with_for_update()
    )
    values = {
        "agreement_number": agreement_number,
        "connector_type": data.connector_type,
        "source_key": source_key(data.source_name),
        "status": ClaimKeyStatus.ACTIVE,
    }
    if key is None:
        session.add(
            OnboardingIntakeKey(
                id=stable_id("onboarding-intake-key", f"{tenant_id}:{document_id}"),
                tenant_id=tenant_id,
                document_id=document_id,
                **values,
            )
        )
    else:
        for name, value in values.items():
            setattr(key, name, value)


def _page_texts(text: str) -> dict[int, str]:
    pages: dict[int, str] = {}
    for chunk in text.split("[page ")[1:]:
        number, _, body = chunk.partition("]")
        if number.isdigit():
            pages[int(number)] = body
    return pages


def _ratio(numerator: int, denominator: int) -> Decimal:
    if denominator == 0:
        return Decimal("0")
    return Decimal(numerator) / Decimal(denominator)


CDP_PACK = DomainPack(agent=CDP_AGENT_SPEC, build_effects=build_cdp_effects, workflow=CdpWorkflow())
