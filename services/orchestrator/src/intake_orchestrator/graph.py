from __future__ import annotations

# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownLambdaType=false
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Literal, Protocol, TypedDict
from uuid import NAMESPACE_URL, UUID, uuid5

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, START, StateGraph  # pyright: ignore[reportMissingTypeStubs]
from langgraph.graph.state import CompiledStateGraph  # pyright: ignore[reportMissingTypeStubs]
from langgraph.types import Command, RetryPolicy, interrupt
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from intake_api.infrastructure.config import Settings
from intake_orchestrator.agent.backend import PostgresAgentBackend
from intake_orchestrator.agent.extraction import (
    LangChainStructuredInvoiceExtractor,
    StubStructuredInvoiceExtractor,
)
from intake_orchestrator.agent.graph import compile_agent_subgraph, run_agent
from intake_orchestrator.agent.models import create_chat_model
from intake_orchestrator.agent.policy import AgentLimits
from intake_orchestrator.agent.prompts import PROMPT_VERSION
from intake_orchestrator.workflow import ReviewResponse, WorkflowBackend


class ParentGraphState(TypedDict):
    graph_run_id: str
    graph_version: str
    thread_id: str
    tenant_id: str
    document_id: str
    correlation_id: str
    current_stage: str
    agent_run_id: str | None
    recommendation_id: str | None
    extraction_candidate_id: str | None
    authoritative_validation_run_id: str | None
    confidence_run_id: str | None
    review_task_id: str | None
    review_response: dict[str, object] | None
    final_decision: str | None
    reason_codes: list[str]
    recoverable_errors: list[str]
    terminal_error: str | None


@dataclass(frozen=True, slots=True)
class GraphRunInput:
    graph_run_id: UUID
    graph_version: str
    thread_id: str
    tenant_id: UUID
    document_id: UUID
    correlation_id: str


@dataclass(frozen=True, slots=True)
class GraphExecutionOutcome:
    status: Literal["INTERRUPTED", "COMPLETED"]
    replayed: bool


class ResolutionOutcome(TypedDict):
    agent_run_id: str
    recommendation_id: str
    extraction_candidate_id: str | None
    reason_codes: list[str]
    incomplete: bool


class ResolutionAgent(Protocol):
    async def resolve(
        self,
        *,
        graph_run_id: UUID,
        tenant_id: UUID,
        document_id: UUID,
        correlation_id: str,
        agent_run_id: UUID,
    ) -> ResolutionOutcome: ...


class ConfiguredResolutionAgent:
    def __init__(
        self,
        *,
        settings: Settings,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._settings = settings
        self._session_factory = session_factory

    async def resolve(
        self,
        *,
        graph_run_id: UUID,
        tenant_id: UUID,
        document_id: UUID,
        correlation_id: str,
        agent_run_id: UUID,
    ) -> ResolutionOutcome:
        model = create_chat_model(self._settings)
        extractor = (
            StubStructuredInvoiceExtractor(
                model=self._settings.agent_model, prompt_version=PROMPT_VERSION
            )
            if self._settings.agent_provider == "stub"
            else LangChainStructuredInvoiceExtractor(
                model=model,
                provider=self._settings.agent_provider,
                model_name=self._settings.agent_model,
                prompt_version=PROMPT_VERSION,
            )
        )
        backend = PostgresAgentBackend(
            self._session_factory,
            graph_run_id=graph_run_id,
            tenant_id=tenant_id,
            document_id=document_id,
            correlation_id=correlation_id,
            provider=self._settings.agent_provider,
            model=self._settings.agent_model,
            prompt_version=PROMPT_VERSION,
            extractor=extractor,
            storage_root=self._settings.storage_root,
        )
        graph = compile_agent_subgraph(
            model=model,
            backend=backend,
            limits=AgentLimits(
                max_tool_calls=self._settings.agent_max_tool_calls,
                max_extraction_attempts=self._settings.agent_max_extraction_attempts,
                max_supplier_lookups=self._settings.agent_max_supplier_lookups,
                max_purchase_order_lookups=self._settings.agent_max_purchase_order_lookups,
            ),
            model_timeout_seconds=self._settings.agent_model_timeout_seconds,
            tool_timeout_seconds=self._settings.agent_tool_timeout_seconds,
            total_timeout_seconds=self._settings.agent_total_timeout_seconds,
        )
        return await run_agent(
            graph,
            graph_run_id=graph_run_id,
            tenant_id=tenant_id,
            document_id=document_id,
            correlation_id=correlation_id,
            agent_run_id=agent_run_id,
        )


def build_parent_graph(
    *, workflow: WorkflowBackend, resolution_agent: ResolutionAgent
) -> StateGraph[ParentGraphState, None, ParentGraphState, ParentGraphState]:
    async def initialize_case(state: ParentGraphState) -> dict[str, object]:
        _validate_identity(state)
        await workflow.initialize_case(
            graph_run_id=UUID(state["graph_run_id"]),
            tenant_id=UUID(state["tenant_id"]),
            document_id=UUID(state["document_id"]),
        )
        return {"current_stage": "CASE_INITIALIZED"}

    async def document_resolution_agent(state: ParentGraphState) -> dict[str, object]:
        agent_run_id = (
            UUID(state["agent_run_id"])
            if state["agent_run_id"]
            else uuid5(NAMESPACE_URL, f"agent-run:{state['graph_run_id']}")
        )
        outcome = await resolution_agent.resolve(
            graph_run_id=UUID(state["graph_run_id"]),
            tenant_id=UUID(state["tenant_id"]),
            document_id=UUID(state["document_id"]),
            correlation_id=state["correlation_id"],
            agent_run_id=agent_run_id,
        )
        reasons = list(dict.fromkeys([*state["reason_codes"], *outcome["reason_codes"]]))
        recoverable = list(state["recoverable_errors"])
        if outcome["incomplete"]:
            recoverable.append("AGENT_INCOMPLETE")
        return {
            "current_stage": "AGENT_COMPLETED",
            "agent_run_id": str(agent_run_id),
            "recommendation_id": outcome["recommendation_id"],
            "extraction_candidate_id": outcome["extraction_candidate_id"],
            "reason_codes": reasons,
            "recoverable_errors": list(dict.fromkeys(recoverable)),
        }

    async def schema_validation(state: ParentGraphState) -> dict[str, object]:
        run_id, reasons = await workflow.validate(
            graph_run_id=UUID(state["graph_run_id"]),
            tenant_id=UUID(state["tenant_id"]),
            document_id=UUID(state["document_id"]),
            candidate_id=_optional_uuid(state["extraction_candidate_id"]),
            phase="SCHEMA",
            reason_codes=[] if state["extraction_candidate_id"] else ["BR-010"],
        )
        return {
            "current_stage": "AUTHORITATIVE_SCHEMA_VALIDATED",
            "authoritative_validation_run_id": str(run_id),
            "reason_codes": list(dict.fromkeys([*state["reason_codes"], *reasons])),
        }

    async def business_validation(state: ParentGraphState) -> dict[str, object]:
        run_id, reasons = await workflow.validate(
            graph_run_id=UUID(state["graph_run_id"]),
            tenant_id=UUID(state["tenant_id"]),
            document_id=UUID(state["document_id"]),
            candidate_id=_optional_uuid(state["extraction_candidate_id"]),
            phase="BUSINESS",
            reason_codes=["BR-009"],
        )
        return {
            "current_stage": "AUTHORITATIVE_BUSINESS_VALIDATED",
            "authoritative_validation_run_id": str(run_id),
            "reason_codes": list(dict.fromkeys([*state["reason_codes"], *reasons])),
        }

    async def calculate_decision_confidence(state: ParentGraphState) -> dict[str, object]:
        validation_id = _required_uuid(
            state["authoritative_validation_run_id"], "Authoritative validation run"
        )
        confidence_id = await workflow.calculate_confidence(
            graph_run_id=UUID(state["graph_run_id"]),
            tenant_id=UUID(state["tenant_id"]),
            document_id=UUID(state["document_id"]),
            validation_run_id=validation_id,
            reason_codes=state["reason_codes"],
        )
        return {"current_stage": "CONFIDENCE_CALCULATED", "confidence_run_id": str(confidence_id)}

    async def create_review_task(state: ParentGraphState) -> dict[str, object]:
        task_id = await workflow.create_or_update_review_task(
            graph_run_id=UUID(state["graph_run_id"]),
            tenant_id=UUID(state["tenant_id"]),
            document_id=UUID(state["document_id"]),
            extraction_candidate_id=_optional_uuid(state["extraction_candidate_id"]),
            validation_run_id=_optional_uuid(state["authoritative_validation_run_id"]),
            confidence_run_id=_optional_uuid(state["confidence_run_id"]),
            recommendation_id=_optional_uuid(state["recommendation_id"]),
        )
        return {"current_stage": "REVIEW_TASK_CREATED", "review_task_id": str(task_id)}

    async def human_review_interrupt(state: ParentGraphState) -> dict[str, object]:
        task_id = _required_uuid(state["review_task_id"], "Review task")
        payload = await workflow.review_interrupt_payload(
            review_task_id=task_id, tenant_id=UUID(state["tenant_id"])
        )
        raw_response = interrupt(payload)
        response = ReviewResponse.model_validate(raw_response)
        return {
            "current_stage": "REVIEW_RESPONSE_RECEIVED",
            "review_response": response.model_dump(mode="json"),
        }

    async def apply_review_response(state: ParentGraphState) -> dict[str, object]:
        response = ReviewResponse.model_validate(state["review_response"])
        await workflow.apply_review_response(
            review_task_id=_required_uuid(state["review_task_id"], "Review task"),
            tenant_id=UUID(state["tenant_id"]),
            response=response,
        )
        return {"current_stage": "REVIEW_APPLIED", "final_decision": response.decision}

    async def revalidate_reviewed_data(state: ParentGraphState) -> dict[str, object]:
        run_id, reasons = await workflow.validate(
            graph_run_id=UUID(state["graph_run_id"]),
            tenant_id=UUID(state["tenant_id"]),
            document_id=UUID(state["document_id"]),
            candidate_id=_optional_uuid(state["extraction_candidate_id"]),
            phase="REVIEWED",
            reason_codes=[] if state["final_decision"] == "APPROVE" else state["reason_codes"],
        )
        confidence_id = await workflow.calculate_confidence(
            graph_run_id=UUID(state["graph_run_id"]),
            tenant_id=UUID(state["tenant_id"]),
            document_id=UUID(state["document_id"]),
            validation_run_id=run_id,
            reason_codes=reasons,
        )
        return {
            "current_stage": "REVIEWED_DATA_REVALIDATED",
            "authoritative_validation_run_id": str(run_id),
            "confidence_run_id": str(confidence_id),
            "reason_codes": reasons,
        }

    def review_decision_gateway(state: ParentGraphState) -> dict[str, object]:
        decision = state["final_decision"]
        if decision not in {"REQUEST_CORRECTION", "REJECT", "APPROVE"}:
            raise ValueError("Review decision is not valid for authoritative routing")
        return {"current_stage": "REVIEW_DECISION_ROUTED"}

    async def finalize_rejection(state: ParentGraphState) -> dict[str, object]:
        await workflow.finalize_rejection(
            graph_run_id=UUID(state["graph_run_id"]),
            tenant_id=UUID(state["tenant_id"]),
            document_id=UUID(state["document_id"]),
        )
        return {"current_stage": "REJECTED"}

    async def create_webhook_outbox(state: ParentGraphState) -> dict[str, object]:
        await workflow.create_webhook_outbox(
            graph_run_id=UUID(state["graph_run_id"]),
            tenant_id=UUID(state["tenant_id"]),
            document_id=UUID(state["document_id"]),
        )
        return {"current_stage": "APPROVED_OUTBOX_CREATED"}

    transient_retry = RetryPolicy(
        max_attempts=3,
        initial_interval=0.1,
        max_interval=1.0,
        jitter=False,
        retry_on=(ConnectionError, TimeoutError, OperationalError),
    )
    builder = StateGraph(ParentGraphState)
    builder.add_node("initialize_case", initialize_case, retry_policy=transient_retry)
    builder.add_node(
        "document_resolution_agent", document_resolution_agent, retry_policy=transient_retry
    )
    builder.add_node(
        "authoritative_schema_validation", schema_validation, retry_policy=transient_retry
    )
    builder.add_node(
        "authoritative_business_validation", business_validation, retry_policy=transient_retry
    )
    builder.add_node(
        "calculate_decision_confidence", calculate_decision_confidence, retry_policy=transient_retry
    )
    builder.add_node("create_review_task", create_review_task, retry_policy=transient_retry)
    builder.add_node("human_review_interrupt", human_review_interrupt)
    builder.add_node("apply_review_response", apply_review_response, retry_policy=transient_retry)
    builder.add_node(
        "revalidate_reviewed_data", revalidate_reviewed_data, retry_policy=transient_retry
    )
    builder.add_node("review_decision_gateway", review_decision_gateway)
    builder.add_node(
        "create_or_update_review_task", create_review_task, retry_policy=transient_retry
    )
    builder.add_node("finalize_rejection", finalize_rejection, retry_policy=transient_retry)
    builder.add_node("create_webhook_outbox", create_webhook_outbox, retry_policy=transient_retry)
    builder.add_edge(START, "initialize_case")
    builder.add_edge("initialize_case", "document_resolution_agent")
    builder.add_edge("document_resolution_agent", "authoritative_schema_validation")
    builder.add_edge("authoritative_schema_validation", "authoritative_business_validation")
    builder.add_edge("authoritative_business_validation", "calculate_decision_confidence")
    builder.add_edge("calculate_decision_confidence", "create_review_task")
    builder.add_edge("create_review_task", "human_review_interrupt")
    builder.add_edge("human_review_interrupt", "apply_review_response")
    builder.add_edge("apply_review_response", "revalidate_reviewed_data")
    builder.add_edge("revalidate_reviewed_data", "review_decision_gateway")
    builder.add_conditional_edges(
        "review_decision_gateway",
        lambda state: state["final_decision"],
        {
            "REQUEST_CORRECTION": "create_or_update_review_task",
            "REJECT": "finalize_rejection",
            "APPROVE": "create_webhook_outbox",
        },
    )
    builder.add_edge("create_or_update_review_task", "human_review_interrupt")
    builder.add_edge("finalize_rejection", END)
    builder.add_edge("create_webhook_outbox", END)
    return builder


class PostgresGraphExecutor:
    def __init__(
        self,
        graph: CompiledStateGraph[ParentGraphState, None, ParentGraphState, ParentGraphState],
        graph_version: str,
    ) -> None:
        self._graph = graph
        self._graph_version = graph_version

    async def execute(self, run: GraphRunInput) -> GraphExecutionOutcome:
        if run.graph_version != self._graph_version:
            raise ValueError("Persisted graph version is not supported by this executor")
        config = _config(run.thread_id)
        snapshot = await self._graph.aget_state(config)
        if snapshot.values:
            if _snapshot_interrupted(snapshot):
                return GraphExecutionOutcome(status="INTERRUPTED", replayed=True)
            if snapshot.values.get("current_stage") in {"REJECTED", "APPROVED_OUTBOX_CREATED"}:
                return GraphExecutionOutcome(status="COMPLETED", replayed=True)
        await self._graph.ainvoke(_initial_state(run), config=config)
        snapshot = await self._graph.aget_state(config)
        if _snapshot_interrupted(snapshot):
            return GraphExecutionOutcome(status="INTERRUPTED", replayed=False)
        if snapshot.values.get("current_stage") in {"REJECTED", "APPROVED_OUTBOX_CREATED"}:
            return GraphExecutionOutcome(status="COMPLETED", replayed=False)
        raise RuntimeError("Parent graph did not satisfy its interrupt or completion contract")

    async def resume(self, run: GraphRunInput, payload: dict[str, object]) -> GraphExecutionOutcome:
        if run.graph_version != self._graph_version:
            raise ValueError("Persisted graph version is not supported by this executor")
        config = _config(run.thread_id)
        before = await self._graph.aget_state(config)
        if before.values.get("current_stage") in {"REJECTED", "APPROVED_OUTBOX_CREATED"}:
            return GraphExecutionOutcome(status="COMPLETED", replayed=True)
        if not _snapshot_interrupted(before):
            raise RuntimeError("Graph thread is not waiting at a human-review interrupt")
        ReviewResponse.model_validate(payload)
        await self._graph.ainvoke(Command(resume=payload), config=config)
        after = await self._graph.aget_state(config)
        if _snapshot_interrupted(after):
            return GraphExecutionOutcome(status="INTERRUPTED", replayed=False)
        if after.values.get("current_stage") in {"REJECTED", "APPROVED_OUTBOX_CREATED"}:
            return GraphExecutionOutcome(status="COMPLETED", replayed=False)
        raise RuntimeError("Resumed graph did not satisfy its interrupt or completion contract")

    async def checkpoint_completed(self, thread_id: str) -> bool:
        snapshot = await self._graph.aget_state(_config(thread_id))
        return snapshot.values.get("current_stage") in {"REJECTED", "APPROVED_OUTBOX_CREATED"}


@asynccontextmanager
async def open_graph_executor(
    settings: Settings,
    *,
    initialize_checkpoint_schema: bool,
    workflow: WorkflowBackend,
    resolution_agent: ResolutionAgent,
) -> AsyncGenerator[PostgresGraphExecutor]:
    connection_string = checkpoint_connection_string(settings)
    async with AsyncPostgresSaver.from_conn_string(connection_string) as checkpointer:
        if initialize_checkpoint_schema:
            await checkpointer.setup()
        graph = build_parent_graph(workflow=workflow, resolution_agent=resolution_agent).compile(
            checkpointer=checkpointer, name=settings.graph_version
        )
        yield PostgresGraphExecutor(graph, settings.graph_version)


async def initialize_checkpoint_schema(settings: Settings) -> None:
    async with AsyncPostgresSaver.from_conn_string(
        checkpoint_connection_string(settings)
    ) as checkpointer:
        await checkpointer.setup()


def checkpoint_connection_string(settings: Settings) -> str:
    url = settings.checkpoint_database_url or settings.database_url
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


def _config(thread_id: str) -> RunnableConfig:
    return {"configurable": {"thread_id": thread_id}}


def _initial_state(run: GraphRunInput) -> ParentGraphState:
    return {
        "graph_run_id": str(run.graph_run_id),
        "graph_version": run.graph_version,
        "thread_id": run.thread_id,
        "tenant_id": str(run.tenant_id),
        "document_id": str(run.document_id),
        "correlation_id": run.correlation_id,
        "current_stage": "REQUESTED",
        "agent_run_id": None,
        "recommendation_id": None,
        "extraction_candidate_id": None,
        "authoritative_validation_run_id": None,
        "confidence_run_id": None,
        "review_task_id": None,
        "review_response": None,
        "final_decision": None,
        "reason_codes": [],
        "recoverable_errors": [],
        "terminal_error": None,
    }


def _validate_identity(state: ParentGraphState) -> None:
    required = (
        "graph_run_id",
        "graph_version",
        "thread_id",
        "tenant_id",
        "document_id",
        "correlation_id",
    )
    if any(not state[field].strip() for field in required):
        raise ValueError("Graph case identity must be complete")
    for field in ("graph_run_id", "tenant_id", "document_id"):
        UUID(state[field])


def _snapshot_interrupted(snapshot: object) -> bool:
    tasks = getattr(snapshot, "tasks", ())
    return any(getattr(task, "interrupts", ()) for task in tasks)


def _optional_uuid(value: str | None) -> UUID | None:
    return UUID(value) if value else None


def _required_uuid(value: str | None, label: str) -> UUID:
    if value is None:
        raise ValueError(f"{label} is required")
    return UUID(value)
