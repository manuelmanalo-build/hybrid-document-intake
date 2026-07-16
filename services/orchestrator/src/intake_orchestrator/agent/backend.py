from __future__ import annotations

import hashlib
import time
from base64 import b64encode
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Literal, cast
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import BaseModel
from pypdf import PdfReader
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from intake_api.domain.enums import ActorType, AuditEventType
from intake_api.domain.models import (
    AgentRecommendation,
    AgentRun,
    AgentToolCall,
    AuditEvent,
    Document,
    DocumentVersion,
    ExtractionCandidate,
    ExtractionFieldEvidence,
    ExtractionRun,
    PromptVersion,
    PurchaseOrder,
    ReviewedDocumentVersion,
    ReviewTask,
    StoredObject,
    Supplier,
    ValidationResult,
    ValidationRun,
)
from intake_orchestrator.agent.contracts import (
    APInvoiceCandidate,
    ExtractInvoiceInput,
    LookupPurchaseOrderInput,
    LookupSupplierInput,
    RetryExtractionInput,
    SubmitRecommendationInput,
    ValidateCandidateInput,
)
from intake_orchestrator.agent.extraction import (
    BoundedExtractionContent,
    StructuredInvoiceExtractor,
    candidate_business_data,
    candidate_completeness,
)
from intake_orchestrator.agent.prompts import PROMPT_CONTENT_SHA256
from intake_orchestrator.agent.tools import validate_tool_output
from intake_orchestrator.validation import (
    RULESET_VERSION,
    ValidationContext,
    failing_reason_codes,
    normalize_document_type,
    safe_candidate_data,
    validate_candidate_rules,
)


def stable_id(kind: str, operation_key: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"document-intake:{kind}:{operation_key}")


class InMemoryAgentBackend:
    def __init__(self, *, text_available: bool = True, validation_retry: bool = False) -> None:
        self.text_available = text_available
        self.validation_retry = validation_retry
        self.results: dict[str, dict[str, object]] = {}
        self.calls: list[tuple[str, str]] = []
        self.agent_runs: dict[UUID, str] = {}
        self.agent_started_at: dict[UUID, float] = {}
        self.recommendations: dict[UUID, dict[str, object]] = {}
        self.policy_rejections: list[str] = []
        self.tool_failures: list[str] = []
        self.usage_metadata: dict[UUID, dict[str, object]] = {}

    async def start_run(self, agent_run_id: UUID) -> float:
        self.agent_runs.setdefault(agent_run_id, "RUNNING")
        return self.agent_started_at.setdefault(agent_run_id, time.time())

    async def complete_run(self, agent_run_id: UUID, *, incomplete: bool) -> None:
        self.agent_runs[agent_run_id] = "AGENT_INCOMPLETE" if incomplete else "COMPLETED"

    async def record_model_usage(
        self, *, agent_run_id: UUID, call_index: int, usage: dict[str, object]
    ) -> None:
        self.usage_metadata.setdefault(agent_run_id, {}).setdefault(str(call_index), usage)

    async def persist_incomplete(
        self, *, agent_run_id: UUID, document_id: UUID, reason_codes: list[str]
    ) -> UUID:
        recommendation_id = stable_id("incomplete-recommendation", str(agent_run_id))
        self.recommendations.setdefault(
            recommendation_id,
            {
                "recommendation_id": str(recommendation_id),
                "document_id": str(document_id),
                "recommended_disposition": "HUMAN_REVIEW",
                "reason_codes": reason_codes,
                "incomplete": True,
            },
        )
        return recommendation_id

    async def record_policy_rejection(
        self,
        *,
        agent_run_id: UUID,
        tenant_id: UUID,
        document_id: UUID,
        tool_name: str,
        argument_names: list[str],
        reason_code: str,
        call_index: int,
    ) -> None:
        del agent_run_id, tenant_id, document_id, argument_names, call_index
        self.policy_rejections.append(f"{tool_name}:{reason_code}")

    async def record_tool_failure(
        self,
        *,
        agent_run_id: UUID,
        tenant_id: UUID,
        document_id: UUID,
        tool_name: str,
        validated_arguments: dict[str, object],
        operation_key: str,
        reason_code: str,
    ) -> None:
        del agent_run_id, tenant_id, document_id, validated_arguments
        failure = f"{tool_name}:{operation_key}:{reason_code}"
        if failure not in self.tool_failures:
            self.tool_failures.append(failure)

    async def execute(
        self, *, tool_name: str, arguments: BaseModel, operation_key: str
    ) -> dict[str, object]:
        if operation_key in self.results:
            return self.results[operation_key]
        result = _deterministic_result(
            tool_name,
            arguments,
            operation_key,
            text_available=self.text_available,
            validation_retry=self.validation_retry,
        )
        self.results[operation_key] = result
        self.calls.append((tool_name, operation_key))
        if tool_name == "submit_recommendation":
            self.recommendations[UUID(str(result["recommendation_id"]))] = result
        return result


class PostgresAgentBackend:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        graph_run_id: UUID,
        tenant_id: UUID,
        document_id: UUID,
        correlation_id: str,
        provider: str,
        model: str,
        prompt_version: str,
        extractor: StructuredInvoiceExtractor,
        storage_root: Path,
    ) -> None:
        self._sessions = session_factory
        self._graph_run_id = graph_run_id
        self._tenant_id = tenant_id
        self._document_id = document_id
        self._correlation_id = correlation_id
        self._provider = provider
        self._model = model
        self._prompt_version = prompt_version
        self._extractor = extractor
        self._storage_root = storage_root.resolve()

    async def start_run(self, agent_run_id: UUID) -> float:
        async with self._sessions() as session, session.begin():
            await session.execute(
                insert(PromptVersion)
                .values(
                    id=stable_id(
                        "prompt-version",
                        f"{self._tenant_id}:document-intake-resolution-agent:{self._prompt_version}",
                    ),
                    tenant_id=self._tenant_id,
                    name="document-intake-resolution-agent",
                    version=self._prompt_version,
                    content_sha256=PROMPT_CONTENT_SHA256,
                    active=True,
                )
                .on_conflict_do_nothing(index_elements=["tenant_id", "name", "version"])
            )
            existing = await session.scalar(
                select(AgentRun).where(
                    AgentRun.id == agent_run_id,
                    AgentRun.tenant_id == self._tenant_id,
                    AgentRun.document_id == self._document_id,
                )
            )
            if existing is not None:
                return existing.started_at.timestamp()
            graph_run = await _graph_identity(
                session, self._graph_run_id, self._tenant_id, self._document_id
            )
            started_at = datetime.now(UTC)
            run = AgentRun(
                id=agent_run_id,
                tenant_id=graph_run[0],
                document_id=graph_run[1],
                graph_run_id=self._graph_run_id,
                provider=self._provider,
                model=self._model,
                prompt_version=self._prompt_version,
                policy_version="1.0",
                status="RUNNING",
                started_at=started_at,
            )
            session.add(run)
            session.add(
                _audit(
                    event_id=stable_id("agent-start", str(agent_run_id)),
                    tenant_id=graph_run[0],
                    document_id=graph_run[1],
                    graph_run_id=self._graph_run_id,
                    agent_run_id=agent_run_id,
                    correlation_id=self._correlation_id,
                    event_type=AuditEventType.AGENT_STARTED,
                    metadata={"provider": self._provider, "model": self._model},
                )
            )
            return started_at.timestamp()

    async def complete_run(self, agent_run_id: UUID, *, incomplete: bool) -> None:
        async with self._sessions() as session, session.begin():
            run = await self._agent_run(session, agent_run_id, with_for_update=True)
            if run is None:
                raise RuntimeError("Agent run does not exist")
            run.status = "AGENT_INCOMPLETE" if incomplete else "COMPLETED"
            run.completed_at = run.completed_at or datetime.now(UTC)

    async def record_model_usage(
        self, *, agent_run_id: UUID, call_index: int, usage: dict[str, object]
    ) -> None:
        async with self._sessions() as session, session.begin():
            run = await self._agent_run(session, agent_run_id, with_for_update=True)
            if run is None:
                raise RuntimeError("Agent run does not exist")
            metadata = dict(run.usage_metadata)
            metadata.setdefault(str(call_index), usage)
            run.usage_metadata = metadata

    async def persist_incomplete(
        self, *, agent_run_id: UUID, document_id: UUID, reason_codes: list[str]
    ) -> UUID:
        recommendation_id = stable_id("incomplete-recommendation", str(agent_run_id))
        async with self._sessions() as session, session.begin():
            existing = await session.scalar(
                select(AgentRecommendation).where(
                    AgentRecommendation.id == recommendation_id,
                    AgentRecommendation.tenant_id == self._tenant_id,
                    AgentRecommendation.document_id == document_id,
                )
            )
            if existing is not None:
                return existing.id
            run = await self._agent_run(session, agent_run_id)
            if run is None or run.document_id != document_id:
                raise RuntimeError("Agent run identity is invalid")
            session.add(
                AgentRecommendation(
                    id=recommendation_id,
                    tenant_id=run.tenant_id,
                    document_id=document_id,
                    agent_run_id=agent_run_id,
                    extraction_candidate_id=None,
                    document_type="UNKNOWN",
                    classification_confidence=Decimal("0"),
                    recommended_disposition="HUMAN_REVIEW",
                    reason_codes=reason_codes,
                    summary="Agent did not complete; authoritative human review is required.",
                    requested_human_actions=["Review document manually"],
                    tools_used=[],
                    prompt_version=self._prompt_version,
                    agent_policy_version="1.0",
                    incomplete=True,
                )
            )
        return recommendation_id

    async def record_policy_rejection(
        self,
        *,
        agent_run_id: UUID,
        tenant_id: UUID,
        document_id: UUID,
        tool_name: str,
        argument_names: list[str],
        reason_code: str,
        call_index: int,
    ) -> None:
        material = f"{agent_run_id}:{call_index}:{tool_name}:rejected".encode()
        operation_key = hashlib.sha256(material).hexdigest()
        async with self._sessions() as session, session.begin():
            existing = await session.scalar(
                select(AgentToolCall.id).where(
                    AgentToolCall.tenant_id == tenant_id,
                    AgentToolCall.agent_run_id == agent_run_id,
                    AgentToolCall.operation_key == operation_key,
                )
            )
            if existing is not None:
                return
            session.add(
                AgentToolCall(
                    id=stable_id("tool-rejection", operation_key),
                    tenant_id=tenant_id,
                    document_id=document_id,
                    agent_run_id=agent_run_id,
                    operation_key=operation_key,
                    tool_name=tool_name[:100],
                    validated_arguments={"argument_names": sorted(argument_names)},
                    result_summary={"reason_code": reason_code},
                    status="REJECTED",
                    reason_codes=[reason_code],
                    completed_at=datetime.now(UTC),
                )
            )
            session.add(
                _audit(
                    event_id=stable_id("tool-rejected", operation_key),
                    tenant_id=tenant_id,
                    document_id=document_id,
                    graph_run_id=self._graph_run_id,
                    agent_run_id=agent_run_id,
                    correlation_id=self._correlation_id,
                    event_type=AuditEventType.AGENT_TOOL_REJECTED,
                    metadata={"tool": tool_name[:100], "reason_code": reason_code},
                )
            )

    async def record_tool_failure(
        self,
        *,
        agent_run_id: UUID,
        tenant_id: UUID,
        document_id: UUID,
        tool_name: str,
        validated_arguments: dict[str, object],
        operation_key: str,
        reason_code: str,
    ) -> None:
        async with self._sessions() as session, session.begin():
            run = await session.scalar(
                select(AgentRun).where(
                    AgentRun.id == agent_run_id,
                    AgentRun.tenant_id == tenant_id,
                    AgentRun.document_id == document_id,
                )
            )
            if run is None:
                raise RuntimeError("Agent run identity is invalid for failed tool recording")
            tool_call = await session.scalar(
                select(AgentToolCall).where(
                    AgentToolCall.tenant_id == tenant_id,
                    AgentToolCall.agent_run_id == agent_run_id,
                    AgentToolCall.operation_key == operation_key,
                )
            )
            now = datetime.now(UTC)
            if tool_call is None:
                tool_call = AgentToolCall(
                    id=stable_id("tool-call", operation_key),
                    tenant_id=tenant_id,
                    document_id=document_id,
                    agent_run_id=agent_run_id,
                    operation_key=operation_key,
                    tool_name=tool_name[:100],
                    validated_arguments=validated_arguments,
                    result_summary={"reason_code": reason_code},
                    status="FAILED",
                    reason_codes=[reason_code],
                    completed_at=now,
                )
                session.add(tool_call)
            elif tool_call.status != "COMPLETED":
                tool_call.status = "FAILED"
                tool_call.result_summary = {"reason_code": reason_code}
                tool_call.reason_codes = [reason_code]
                tool_call.completed_at = now
            await _add_audit_if_absent(
                session,
                _audit(
                    event_id=stable_id("tool-requested", operation_key),
                    tenant_id=tenant_id,
                    document_id=document_id,
                    graph_run_id=self._graph_run_id,
                    agent_run_id=agent_run_id,
                    correlation_id=self._correlation_id,
                    event_type=AuditEventType.AGENT_TOOL_REQUESTED,
                    metadata={"tool": tool_name[:100], "operation_key": operation_key},
                ),
            )
            await _add_audit_if_absent(
                session,
                _audit(
                    event_id=stable_id("tool-failed", operation_key),
                    tenant_id=tenant_id,
                    document_id=document_id,
                    graph_run_id=self._graph_run_id,
                    agent_run_id=agent_run_id,
                    correlation_id=self._correlation_id,
                    event_type=AuditEventType.AGENT_TOOL_FAILED,
                    metadata={"tool": tool_name[:100], "reason_code": reason_code},
                ),
            )
            run.last_error_code = reason_code

    async def execute(
        self, *, tool_name: str, arguments: BaseModel, operation_key: str
    ) -> dict[str, object]:
        started = time.monotonic()
        identity = arguments.model_dump(mode="json")
        tenant_id = UUID(str(identity["tenant_id"]))
        document_id = UUID(str(identity["document_id"]))
        agent_run_id = UUID(str(identity["agent_run_id"]))
        async with self._sessions() as session, session.begin():
            existing = await session.scalar(
                select(AgentToolCall).where(
                    AgentToolCall.tenant_id == tenant_id,
                    AgentToolCall.agent_run_id == agent_run_id,
                    AgentToolCall.operation_key == operation_key,
                )
            )
            if (
                existing is not None
                and existing.status == "COMPLETED"
                and existing.result_summary is not None
            ):
                return existing.result_summary
            document = await session.scalar(
                select(Document.id).where(
                    Document.tenant_id == tenant_id, Document.id == document_id
                )
            )
            if document is None:
                raise PermissionError("Document is not available in the tool tenant")

            if existing is None:
                tool_call = AgentToolCall(
                    id=stable_id("tool-call", operation_key),
                    tenant_id=tenant_id,
                    document_id=document_id,
                    agent_run_id=agent_run_id,
                    operation_key=operation_key,
                    tool_name=tool_name,
                    validated_arguments=identity,
                    status="RUNNING",
                )
                session.add(tool_call)
            else:
                tool_call = existing
                tool_call.status = "RUNNING"
                tool_call.result_summary = None
                tool_call.reason_codes = []
                tool_call.completed_at = None
            await session.flush()
            result = await self._execute_effect(session, tool_name, arguments, operation_key)
            result = validate_tool_output(tool_name, result)
            tool_call.status = "COMPLETED"
            tool_call.result_summary = result
            tool_call.completed_at = datetime.now(UTC)
            tool_call.duration_ms = max(0, int((time.monotonic() - started) * 1000))
            await _add_audit_if_absent(
                session,
                _audit(
                    event_id=stable_id("tool-requested", operation_key),
                    tenant_id=tenant_id,
                    document_id=document_id,
                    graph_run_id=self._graph_run_id,
                    agent_run_id=agent_run_id,
                    correlation_id=self._correlation_id,
                    event_type=AuditEventType.AGENT_TOOL_REQUESTED,
                    metadata={"tool": tool_name, "operation_key": operation_key},
                ),
            )
            await _add_audit_if_absent(
                session,
                _audit(
                    event_id=stable_id("tool-completed", operation_key),
                    tenant_id=tenant_id,
                    document_id=document_id,
                    graph_run_id=self._graph_run_id,
                    agent_run_id=agent_run_id,
                    correlation_id=self._correlation_id,
                    event_type=AuditEventType.AGENT_TOOL_COMPLETED,
                    metadata={"tool": tool_name, "operation_key": operation_key},
                ),
            )
            run = await session.scalar(
                select(AgentRun)
                .where(
                    AgentRun.id == agent_run_id,
                    AgentRun.tenant_id == tenant_id,
                    AgentRun.document_id == document_id,
                )
                .with_for_update()
            )
            if run is not None:
                run.last_error_code = None
                run.tool_call_count += 1
                if tool_name.startswith("extract_invoice_from_"):
                    run.extraction_attempt_count += 1
            return result

    async def _agent_run(
        self, session: AsyncSession, agent_run_id: UUID, *, with_for_update: bool = False
    ) -> AgentRun | None:
        statement = select(AgentRun).where(
            AgentRun.id == agent_run_id,
            AgentRun.tenant_id == self._tenant_id,
            AgentRun.document_id == self._document_id,
        )
        if with_for_update:
            statement = statement.with_for_update()
        return await session.scalar(statement)

    async def _execute_effect(
        self, session: AsyncSession, tool_name: str, arguments: BaseModel, operation_key: str
    ) -> dict[str, object]:
        result = _deterministic_result(tool_name, arguments, operation_key)
        identity = arguments.model_dump(mode="json")
        tenant_id = UUID(str(identity["tenant_id"]))
        document_id = UUID(str(identity["document_id"]))
        agent_run_id = UUID(str(identity["agent_run_id"]))
        if tool_name.startswith("extract_invoice_from_"):
            parsed = cast(ExtractInvoiceInput, arguments)
            run_id = UUID(str(result["extraction_run_id"]))
            candidate_id = UUID(str(result["extraction_candidate_id"]))
            strategy = str(result["strategy"])
            structured = await self._extractor.extract(
                strategy=cast("Literal['TEXT', 'VISUAL']", strategy),
                bounded_content=await self._bounded_document_content(
                    session,
                    tenant_id=tenant_id,
                    document_id=document_id,
                    include_images=strategy == "VISUAL",
                ),
            )
            candidate_data = candidate_business_data(structured)
            completeness = candidate_completeness(structured)
            result["completeness_score"] = float(completeness)
            result["reason_codes"] = (
                [] if completeness == Decimal("1.0000") else ["EXTRACTION_INCOMPLETE"]
            )
            extraction_run = ExtractionRun(
                id=run_id,
                tenant_id=tenant_id,
                document_id=document_id,
                agent_run_id=agent_run_id,
                operation_key=operation_key,
                strategy=strategy,
                attempt_number=parsed.attempt_number,
                provider=self._provider,
                model=self._model,
                prompt_version=self._prompt_version,
                schema_version="ap-invoice-v1",
                status="COMPLETED",
            )
            session.add(extraction_run)
            # These models intentionally expose identifiers rather than ORM relationships.
            # Flush each parent before its composite tenant-scoped child so PostgreSQL never
            # observes a candidate or evidence row before the referenced record exists.
            await session.flush([extraction_run])
            extraction_candidate = ExtractionCandidate(
                id=candidate_id,
                tenant_id=tenant_id,
                document_id=document_id,
                extraction_run_id=run_id,
                document_type="AP_INVOICE",
                candidate_data=candidate_data,
                completeness_score=completeness,
            )
            session.add(extraction_candidate)
            await session.flush([extraction_candidate])
            for field_name in APInvoiceCandidate.model_fields:
                field = getattr(structured, field_name)
                session.add(
                    ExtractionFieldEvidence(
                        id=stable_id("field-evidence", f"{candidate_id}:{field_name}"),
                        tenant_id=tenant_id,
                        candidate_id=candidate_id,
                        field_name=field_name,
                        confidence=field.confidence,
                        source_page=field.source_page,
                        evidence_excerpt=field.evidence_excerpt[:500],
                        extraction_strategy=field.extraction_strategy,
                        provider=field.provider,
                        model=field.model,
                        schema_version=field.schema_version,
                        prompt_version=field.prompt_version,
                    )
                )
            session.add(
                _audit(
                    event_id=stable_id("extraction-completed", operation_key),
                    tenant_id=tenant_id,
                    document_id=document_id,
                    graph_run_id=self._graph_run_id,
                    agent_run_id=agent_run_id,
                    correlation_id=self._correlation_id,
                    event_type=AuditEventType.EXTRACTION_COMPLETED,
                    metadata={"strategy": strategy, "attempt": parsed.attempt_number},
                )
            )
        elif tool_name == "lookup_supplier":
            parsed = cast(LookupSupplierInput, arguments)
            supplier = await session.scalar(
                select(Supplier).where(
                    Supplier.tenant_id == tenant_id,
                    Supplier.name == parsed.supplier_name,
                )
            )
            result = {
                "matched": supplier is not None,
                "supplier_id": str(supplier.id) if supplier else None,
                "active": supplier.active if supplier else None,
                "match_quality": 1.0 if supplier else 0.0,
            }
        elif tool_name == "lookup_purchase_order":
            parsed = cast(LookupPurchaseOrderInput, arguments)
            purchase_order = await session.scalar(
                select(PurchaseOrder).where(
                    PurchaseOrder.tenant_id == tenant_id,
                    PurchaseOrder.purchase_order_number == parsed.purchase_order_number,
                )
            )
            result = {
                "matched": purchase_order is not None,
                "purchase_order_id": str(purchase_order.id) if purchase_order else None,
                "active": purchase_order.active if purchase_order else None,
                "match_quality": 1.0 if purchase_order else 0.0,
            }
        elif tool_name == "validate_candidate":
            parsed = cast(ValidateCandidateInput, arguments)
            candidate = await session.scalar(
                select(ExtractionCandidate).where(
                    ExtractionCandidate.tenant_id == tenant_id,
                    ExtractionCandidate.id == parsed.extraction_candidate_id,
                )
            )
            if candidate is None:
                raise PermissionError("Extraction candidate is not available in the tool tenant")
            candidate_data = safe_candidate_data(candidate.candidate_data)
            field_confidence = dict(
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
                update={"field_confidence": field_confidence}
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
                        PurchaseOrder.purchase_order_number == candidate_data.purchase_order_number,
                    )
                )
            results = validate_candidate_rules(
                candidate_data,
                ValidationContext(
                    business_date=datetime.now(UTC).date(),
                    document_type=normalize_document_type(candidate.document_type),
                    duplicate_approved_invoice=await _approved_duplicate_exists(
                        session,
                        tenant_id=tenant_id,
                        graph_run_id=self._graph_run_id,
                        supplier_name=candidate_data.supplier_name,
                        invoice_number=candidate_data.invoice_number,
                    ),
                    supplier_matched=supplier is not None,
                    supplier_active=supplier.active if supplier else None,
                    purchase_order_matched=purchase_order is not None,
                    purchase_order_authorized_amount=(
                        purchase_order.authorized_amount if purchase_order else None
                    ),
                ),
            )
            reasons = failing_reason_codes(results)
            validation_id = stable_id("agent-validation", operation_key)
            validation_run = ValidationRun(
                id=validation_id,
                tenant_id=tenant_id,
                document_id=document_id,
                graph_run_id=self._graph_run_id,
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
                        rule_version=RULESET_VERSION,
                        outcome=rule_result.outcome,
                        reason_code=rule_result.reason_code,
                        sanitized_details={},
                    )
                )
            result = {
                "valid": not reasons,
                "retry_recommended": any(code in {"BR-001", "BR-006"} for code in reasons),
                "reason_codes": reasons,
            }
        elif tool_name == "submit_recommendation":
            parsed = cast(SubmitRecommendationInput, arguments)
            recommendation_id = UUID(str(result["recommendation_id"]))
            session.add(
                AgentRecommendation(
                    id=recommendation_id,
                    tenant_id=tenant_id,
                    document_id=document_id,
                    agent_run_id=agent_run_id,
                    extraction_candidate_id=parsed.extraction_candidate_id,
                    document_type=parsed.document_type,
                    classification_confidence=Decimal(str(parsed.document_type_confidence)),
                    recommended_disposition=parsed.recommended_disposition,
                    reason_codes=parsed.reason_codes,
                    summary=parsed.summary,
                    requested_human_actions=parsed.requested_human_actions,
                    tools_used=parsed.tools_used,
                    prompt_version=self._prompt_version,
                    agent_policy_version="1.0",
                    incomplete=False,
                )
            )
            session.add(
                _audit(
                    event_id=stable_id("recommendation-submitted", operation_key),
                    tenant_id=tenant_id,
                    document_id=document_id,
                    graph_run_id=self._graph_run_id,
                    agent_run_id=agent_run_id,
                    correlation_id=self._correlation_id,
                    event_type=AuditEventType.RECOMMENDATION_SUBMITTED,
                    metadata={"recommendation_id": str(recommendation_id)},
                )
            )
        return cast(dict[str, object], result)

    async def _bounded_document_content(
        self,
        session: AsyncSession,
        *,
        tenant_id: UUID,
        document_id: UUID,
        include_images: bool,
    ) -> BoundedExtractionContent:
        row = await session.execute(
            select(StoredObject.object_key, StoredObject.byte_size)
            .join(DocumentVersion, DocumentVersion.stored_object_id == StoredObject.id)
            .join(Document, Document.current_version_id == DocumentVersion.id)
            .where(Document.tenant_id == tenant_id, Document.id == document_id)
        )
        stored = row.one_or_none()
        if stored is None:
            raise RuntimeError("Immutable document object is unavailable")
        object_key, byte_size = stored
        if byte_size > 10 * 1024 * 1024:
            raise ValueError("Document exceeds the bounded extraction size")
        path = (self._storage_root / object_key).resolve()
        if not path.is_relative_to(self._storage_root):
            raise PermissionError("Stored object key escapes the configured storage root")
        if path.stat().st_size != byte_size:
            raise RuntimeError("Immutable stored object size does not match metadata")
        if self._provider == "stub":
            return BoundedExtractionContent(
                text="Deterministic stub extraction input; bytes remain out of graph state."
            )
        excerpts: list[str] = []
        image_data_urls: list[str] = []
        image_bytes = 0
        with path.open("rb") as stream:
            reader = PdfReader(stream, strict=True)
            for page_number, page in enumerate(reader.pages[:20], start=1):
                remaining = 20_000 - sum(len(value) for value in excerpts)
                if remaining <= 0:
                    break
                text = (page.extract_text() or "")[:remaining]
                excerpts.append(f"[page {page_number}]\n{text}")
                if include_images:
                    for image in page.images:
                        if len(image_data_urls) >= 10 or image_bytes + len(image.data) > 4_000_000:
                            break
                        extension = image.name.rsplit(".", 1)[-1].casefold()
                        if extension not in {"jpg", "jpeg", "png"}:
                            continue
                        media_type = "image/jpeg" if extension in {"jpg", "jpeg"} else "image/png"
                        image_data_urls.append(
                            f"data:{media_type};base64,{b64encode(image.data).decode('ascii')}"
                        )
                        image_bytes += len(image.data)
        return BoundedExtractionContent(
            text="\n".join(excerpts)[:20_000], image_data_urls=tuple(image_data_urls)
        )


async def _graph_identity(
    session: AsyncSession, graph_run_id: UUID, tenant_id: UUID, document_id: UUID
) -> tuple[UUID, UUID]:
    from intake_api.domain.models import GraphRun

    row = await session.execute(
        select(GraphRun.tenant_id, GraphRun.document_id).where(
            GraphRun.id == graph_run_id,
            GraphRun.tenant_id == tenant_id,
            GraphRun.document_id == document_id,
        )
    )
    identity = row.one_or_none()
    if identity is None:
        raise RuntimeError("Graph run does not exist")
    return identity[0], identity[1]


async def _approved_duplicate_exists(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    graph_run_id: UUID,
    supplier_name: str | None,
    invoice_number: str | None,
) -> bool:
    if not supplier_name or not invoice_number:
        return False
    duplicate = await session.scalar(
        select(ReviewedDocumentVersion.id)
        .join(ReviewTask, ReviewTask.id == ReviewedDocumentVersion.review_task_id)
        .where(
            ReviewedDocumentVersion.tenant_id == tenant_id,
            ReviewTask.tenant_id == tenant_id,
            ReviewTask.graph_run_id != graph_run_id,
            ReviewTask.status == "APPROVED",
            ReviewedDocumentVersion.business_data["supplier_name"].as_string() == supplier_name,
            ReviewedDocumentVersion.business_data["invoice_number"].as_string() == invoice_number,
        )
        .limit(1)
    )
    return duplicate is not None


def _deterministic_result(
    tool_name: str,
    arguments: BaseModel,
    operation_key: str,
    *,
    text_available: bool = True,
    validation_retry: bool = False,
) -> dict[str, object]:
    if tool_name == "inspect_document":
        return {
            "document_id": str(arguments.document_id),  # type: ignore[attr-defined]
            "page_count": 1,
            "text_available": text_available,
            "visual_content_present": True,
            "media_type": "application/pdf",
        }
    if tool_name == "classify_document":
        return {"value": "AP_INVOICE", "confidence": 0.9, "reason_codes": []}
    if tool_name.startswith("extract_invoice_from_"):
        return {
            "extraction_run_id": str(stable_id("extraction-run", operation_key)),
            "extraction_candidate_id": str(stable_id("candidate", operation_key)),
            "strategy": "TEXT" if tool_name.endswith("text") else "VISUAL",
            "completeness_score": 0.0,
            "reason_codes": ["EXTRACTION_REQUIRES_REVIEW"],
        }
    if tool_name == "lookup_supplier":
        return {"matched": False, "supplier_id": None, "active": None, "match_quality": 0.0}
    if tool_name == "lookup_purchase_order":
        return {
            "matched": False,
            "purchase_order_id": None,
            "active": None,
            "match_quality": 0.0,
        }
    if tool_name == "validate_candidate":
        cast(ValidateCandidateInput, arguments)
        return {
            "valid": False,
            "retry_recommended": validation_retry,
            "reason_codes": ["BR-001", "BR-006"],
        }
    if tool_name == "retry_extraction":
        parsed = cast(RetryExtractionInput, arguments)
        return {
            "authorized": True,
            "next_attempt_number": 2,
            "next_strategy": parsed.next_strategy,
        }
    if tool_name == "submit_recommendation":
        return {"recommendation_id": str(stable_id("recommendation", operation_key))}
    raise ValueError(f"Unknown tool: {tool_name}")


def _audit(
    *,
    event_id: UUID,
    tenant_id: UUID,
    document_id: UUID,
    graph_run_id: UUID,
    agent_run_id: UUID,
    correlation_id: str,
    event_type: AuditEventType,
    metadata: dict[str, object],
) -> AuditEvent:
    return AuditEvent(
        id=event_id,
        tenant_id=tenant_id,
        document_id=document_id,
        graph_run_id=graph_run_id,
        agent_run_id=agent_run_id,
        event_type=event_type,
        actor_type=ActorType.WORKER,
        actor_id="langgraph-orchestrator",
        correlation_id=correlation_id,
        sanitized_metadata=metadata,
    )


async def _add_audit_if_absent(session: AsyncSession, event: AuditEvent) -> None:
    existing = await session.scalar(
        select(AuditEvent.id).where(
            AuditEvent.id == event.id,
            AuditEvent.tenant_id == event.tenant_id,
        )
    )
    if existing is None:
        session.add(event)
