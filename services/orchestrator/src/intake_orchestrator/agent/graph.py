from __future__ import annotations

import asyncio
import time

# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownLambdaType=false
from typing import Literal, NoReturn, Protocol, TypedDict, cast
from uuid import UUID

from langchain_core.exceptions import OutputParserException
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.errors import NodeError
from langgraph.graph import END, START, StateGraph  # pyright: ignore[reportMissingTypeStubs]
from langgraph.graph.state import CompiledStateGraph  # pyright: ignore[reportMissingTypeStubs]
from langgraph.types import Command, RetryPolicy
from pydantic import BaseModel, ValidationError
from sqlalchemy.exc import OperationalError

from intake_orchestrator.agent.errors import (
    AgentDeadlineExceeded,
    AgentToolTimeout,
    MalformedModelResponse,
    PolicyViolation,
    ProviderUnavailable,
    TransientDatabaseError,
    TransientProviderRateLimit,
    TransientProviderTimeout,
    TransientStorageError,
)
from intake_orchestrator.agent.models import require_one_tool_call
from intake_orchestrator.agent.policy import AgentLimits, AgentPolicyGuard
from intake_orchestrator.agent.prompts import AGENT_SYSTEM_PROMPT
from intake_orchestrator.agent.tools import build_tools, stable_operation_key, tool_map


class AgentBackend(Protocol):
    async def start_run(self, agent_run_id: UUID) -> float: ...

    async def complete_run(self, agent_run_id: UUID, *, incomplete: bool) -> None: ...

    async def record_model_usage(
        self, *, agent_run_id: UUID, call_index: int, usage: dict[str, object]
    ) -> None: ...

    async def persist_incomplete(
        self, *, agent_run_id: UUID, document_id: UUID, reason_codes: list[str]
    ) -> UUID: ...

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
    ) -> None: ...

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
    ) -> None: ...

    async def execute(
        self, *, tool_name: str, arguments: BaseModel, operation_key: str
    ) -> dict[str, object]: ...


class AgentState(TypedDict):
    graph_run_id: str
    tenant_id: str
    document_id: str
    correlation_id: str
    agent_run_id: str
    started_epoch_seconds: float
    tool_call_count: int
    extraction_attempts: int
    supplier_lookups: int
    purchase_order_lookups: int
    tools_used: list[str]
    tool_results: dict[str, dict[str, object]]
    requested_tool: str
    requested_arguments: dict[str, object]
    validated_arguments: dict[str, object]
    operation_key: str
    last_tool_result: dict[str, object]
    extraction_candidate_id: str | None
    recommendation_id: str
    termination_reason: str
    reason_codes: list[str]


class AgentOutcome(TypedDict):
    agent_run_id: str
    recommendation_id: str
    extraction_candidate_id: str | None
    reason_codes: list[str]
    incomplete: bool


def build_agent_subgraph(
    *,
    model: BaseChatModel,
    backend: AgentBackend,
    limits: AgentLimits,
    model_timeout_seconds: int,
    tool_timeout_seconds: int,
    total_timeout_seconds: float,
) -> StateGraph[AgentState, None, AgentState, AgentState]:
    tools = build_tools(backend)
    tools_by_name = tool_map(tools)
    bound_model = model.bind_tools(tools)
    guard = AgentPolicyGuard(limits)

    async def load_agent_context(state: AgentState) -> dict[str, object]:
        started_epoch_seconds = await backend.start_run(UUID(state["agent_run_id"]))
        return {"reason_codes": [], "started_epoch_seconds": started_epoch_seconds}

    async def model_decision(state: AgentState) -> dict[str, object]:
        remaining = total_timeout_seconds - (time.time() - state["started_epoch_seconds"])
        if remaining <= 0:
            return {"termination_reason": "AGENT_TOTAL_TIMEOUT"}
        if state["tool_call_count"] >= limits.max_tool_calls:
            return {"termination_reason": "AGENT_TOOL_LIMIT_REACHED"}
        payload: dict[str, object] = {
            key: state[key]
            for key in (
                "tenant_id",
                "document_id",
                "agent_run_id",
                "tool_call_count",
                "extraction_attempts",
                "tools_used",
                "tool_results",
            )
        }
        payload["extraction_candidate_id"] = state.get("extraction_candidate_id")
        model_started = time.monotonic()
        call_timeout = min(float(model_timeout_seconds), remaining)
        try:
            async with asyncio.timeout(call_timeout):
                raw = await bound_model.ainvoke(
                    [
                        SystemMessage(content=AGENT_SYSTEM_PROMPT),
                        HumanMessage(content=_bounded_json(payload)),
                    ]
                )
        except TimeoutError as exc:
            if call_timeout < model_timeout_seconds:
                raise AgentDeadlineExceeded("Persisted total agent deadline elapsed") from exc
            raise TransientProviderTimeout("Configured model-call timeout elapsed") from exc
        except Exception as exc:
            _raise_mapped_provider_error(exc)
        usage: dict[str, object] = dict(raw.usage_metadata or {})
        usage["latency_ms"] = max(0, int((time.monotonic() - model_started) * 1000))
        await backend.record_model_usage(
            agent_run_id=UUID(state["agent_run_id"]),
            call_index=state["tool_call_count"] + 1,
            usage=usage,
        )
        name, arguments = require_one_tool_call(raw)
        return {
            "requested_tool": name,
            "requested_arguments": arguments,
            "termination_reason": "",
        }

    def policy_guard(state: AgentState) -> dict[str, object]:
        parsed = guard.validate(
            tool_name=state["requested_tool"],
            arguments=state["requested_arguments"],
            tenant_id=state["tenant_id"],
            document_id=state["document_id"],
            agent_run_id=state["agent_run_id"],
            tool_call_count=state["tool_call_count"],
            extraction_attempts=state["extraction_attempts"],
            supplier_lookups=state["supplier_lookups"],
            purchase_order_lookups=state["purchase_order_lookups"],
            tools_used=state["tools_used"],
            extraction_candidate_id=state.get("extraction_candidate_id"),
        )
        validated = parsed.model_dump(mode="json")
        operation_key = stable_operation_key(
            agent_run_id=state["agent_run_id"],
            call_index=state["tool_call_count"] + 1,
            tool_name=state["requested_tool"],
            arguments=validated,
        )
        return {"validated_arguments": validated, "operation_key": operation_key}

    def tool_router(state: AgentState) -> dict[str, object]:
        if state["requested_tool"] not in tools_by_name:
            raise PolicyViolation("Tool router received a non-allowlisted tool")
        return {}

    async def selected_tool(state: AgentState) -> dict[str, object]:
        tool = tools_by_name[state["requested_tool"]]
        config: RunnableConfig = {"configurable": {"operation_key": state["operation_key"]}}
        remaining = total_timeout_seconds - (time.time() - state["started_epoch_seconds"])
        if remaining <= 0:
            raise AgentDeadlineExceeded("Persisted total agent deadline elapsed")
        call_timeout = min(float(tool_timeout_seconds), remaining)
        try:
            async with asyncio.timeout(call_timeout):
                result = await tool.ainvoke(state["validated_arguments"], config=config)
        except TimeoutError as exc:
            if call_timeout < tool_timeout_seconds:
                raise AgentDeadlineExceeded("Persisted total agent deadline elapsed") from exc
            raise AgentToolTimeout("Configured tool timeout elapsed") from exc
        except OperationalError as exc:
            raise TransientDatabaseError("Application database is temporarily unavailable") from exc
        except OSError as exc:
            raise TransientStorageError("Immutable storage is temporarily unavailable") from exc
        except (OutputParserException, ValidationError) as exc:
            raise MalformedModelResponse(
                "Tool output did not match its structured contract"
            ) from exc
        except Exception as exc:
            _raise_mapped_provider_error(exc)
        if not isinstance(result, dict):
            raise TypeError("Tool returned an invalid result contract")
        return {"last_tool_result": cast(dict[str, object], result)}

    def record_tool_result(state: AgentState) -> dict[str, object]:
        tool_name = state["requested_tool"]
        results = dict(state["tool_results"])
        results[tool_name] = state["last_tool_result"]
        tools_used = [*state["tools_used"], tool_name]
        update: dict[str, object] = {
            "tool_results": results,
            "tools_used": tools_used,
            "tool_call_count": state["tool_call_count"] + 1,
        }
        if tool_name.startswith("extract_invoice_from_"):
            update["extraction_attempts"] = state["extraction_attempts"] + 1
            update["extraction_candidate_id"] = str(
                state["last_tool_result"]["extraction_candidate_id"]
            )
        elif tool_name == "lookup_supplier":
            update["supplier_lookups"] = state["supplier_lookups"] + 1
        elif tool_name == "lookup_purchase_order":
            update["purchase_order_lookups"] = state["purchase_order_lookups"] + 1
        elif tool_name == "submit_recommendation":
            update["recommendation_id"] = str(state["last_tool_result"]["recommendation_id"])
        return update

    async def persist_recommendation(state: AgentState) -> dict[str, object]:
        await backend.complete_run(UUID(state["agent_run_id"]), incomplete=False)
        return {"termination_reason": "RECOMMENDATION_SUBMITTED"}

    async def persist_incomplete_recommendation(state: AgentState) -> dict[str, object]:
        reason = state.get("termination_reason") or "AGENT_INCOMPLETE"
        reasons = list(dict.fromkeys([*state["reason_codes"], reason]))
        recommendation_id = await backend.persist_incomplete(
            agent_run_id=UUID(state["agent_run_id"]),
            document_id=UUID(state["document_id"]),
            reason_codes=reasons,
        )
        await backend.complete_run(UUID(state["agent_run_id"]), incomplete=True)
        return {"recommendation_id": str(recommendation_id), "reason_codes": reasons}

    async def incomplete_error_handler(
        state: AgentState, error: NodeError
    ) -> Command[Literal["persist_incomplete_recommendation"]]:
        if isinstance(error.error, (TransientDatabaseError, TransientStorageError)):
            raise error.error
        code = _safe_error_code(error.error)
        if code == "AGENT_UNEXPECTED_FAILURE":
            raise error.error
        if isinstance(error.error, PolicyViolation):
            await backend.record_policy_rejection(
                agent_run_id=UUID(state["agent_run_id"]),
                tenant_id=UUID(state["tenant_id"]),
                document_id=UUID(state["document_id"]),
                tool_name=state["requested_tool"] or "MALFORMED_TOOL_REQUEST",
                argument_names=list(state["requested_arguments"]),
                reason_code=code,
                call_index=state["tool_call_count"] + 1,
            )
        return Command(
            update={"termination_reason": code},
            goto="persist_incomplete_recommendation",
        )

    async def tool_error_handler(
        state: AgentState, error: NodeError
    ) -> Command[Literal["persist_incomplete_recommendation"]]:
        if isinstance(error.error, TransientDatabaseError):
            raise error.error
        code = _safe_error_code(error.error)
        await backend.record_tool_failure(
            agent_run_id=UUID(state["agent_run_id"]),
            tenant_id=UUID(state["tenant_id"]),
            document_id=UUID(state["document_id"]),
            tool_name=state["requested_tool"],
            validated_arguments=state["validated_arguments"],
            operation_key=state["operation_key"],
            reason_code=code,
        )
        if isinstance(error.error, TransientStorageError) or code == "AGENT_UNEXPECTED_FAILURE":
            raise error.error
        return Command(
            update={"termination_reason": code},
            goto="persist_incomplete_recommendation",
        )

    transient_provider = RetryPolicy(
        max_attempts=3,
        initial_interval=0.1,
        max_interval=1.0,
        jitter=False,
        retry_on=(TransientProviderTimeout, TransientProviderRateLimit, TimeoutError),
    )
    transient_tool = RetryPolicy(
        max_attempts=3,
        initial_interval=0.1,
        max_interval=1.0,
        jitter=False,
        retry_on=(
            TransientDatabaseError,
            TransientProviderRateLimit,
            TransientProviderTimeout,
            TransientStorageError,
            TimeoutError,
        ),
    )
    builder = StateGraph(AgentState)
    builder.add_node("load_agent_context", load_agent_context, retry_policy=transient_tool)
    builder.add_node(
        "model_decision",
        model_decision,
        retry_policy=transient_provider,
        error_handler=incomplete_error_handler,  # pyright: ignore[reportArgumentType]
        destinations=("policy_guard", "persist_incomplete_recommendation"),
    )
    builder.add_node(
        "policy_guard",
        policy_guard,
        error_handler=incomplete_error_handler,  # pyright: ignore[reportArgumentType]
        destinations=("tool_router", "persist_incomplete_recommendation"),
    )
    builder.add_node("tool_router", tool_router)
    builder.add_node(
        "selected_tool",
        selected_tool,
        retry_policy=transient_tool,
        error_handler=tool_error_handler,  # pyright: ignore[reportArgumentType]
        destinations=("record_tool_result", "persist_incomplete_recommendation"),
    )
    builder.add_node("record_tool_result", record_tool_result)
    builder.add_node("persist_recommendation", persist_recommendation, retry_policy=transient_tool)
    builder.add_node(
        "persist_incomplete_recommendation",
        persist_incomplete_recommendation,
        retry_policy=transient_tool,
    )
    builder.add_edge(START, "load_agent_context")
    builder.add_edge("load_agent_context", "model_decision")
    builder.add_conditional_edges(
        "model_decision",
        lambda state: "incomplete" if state.get("termination_reason") else "continue",
        {"incomplete": "persist_incomplete_recommendation", "continue": "policy_guard"},
    )
    builder.add_edge("policy_guard", "tool_router")
    builder.add_edge("tool_router", "selected_tool")
    builder.add_edge("selected_tool", "record_tool_result")
    builder.add_conditional_edges(
        "record_tool_result",
        lambda state: (
            "complete" if state["requested_tool"] == "submit_recommendation" else "continue"
        ),
        {"complete": "persist_recommendation", "continue": "model_decision"},
    )
    builder.add_edge("persist_recommendation", END)
    builder.add_edge("persist_incomplete_recommendation", END)
    return builder


def compile_agent_subgraph(
    *,
    model: BaseChatModel,
    backend: AgentBackend,
    limits: AgentLimits,
    model_timeout_seconds: int,
    tool_timeout_seconds: int,
    total_timeout_seconds: float,
) -> CompiledStateGraph[AgentState, None, AgentState, AgentState]:
    return build_agent_subgraph(
        model=model,
        backend=backend,
        limits=limits,
        model_timeout_seconds=model_timeout_seconds,
        tool_timeout_seconds=tool_timeout_seconds,
        total_timeout_seconds=total_timeout_seconds,
    ).compile(name="document-intake-resolution-agent")


async def run_agent(
    graph: CompiledStateGraph[AgentState, None, AgentState, AgentState],
    *,
    graph_run_id: UUID,
    tenant_id: UUID,
    document_id: UUID,
    correlation_id: str,
    agent_run_id: UUID,
) -> AgentOutcome:
    state: AgentState = {
        "graph_run_id": str(graph_run_id),
        "tenant_id": str(tenant_id),
        "document_id": str(document_id),
        "correlation_id": correlation_id,
        "agent_run_id": str(agent_run_id),
        "started_epoch_seconds": 0.0,
        "tool_call_count": 0,
        "extraction_attempts": 0,
        "supplier_lookups": 0,
        "purchase_order_lookups": 0,
        "tools_used": [],
        "tool_results": {},
        "reason_codes": [],
        "requested_tool": "",
        "requested_arguments": {},
        "validated_arguments": {},
        "operation_key": "",
        "last_tool_result": {},
        "extraction_candidate_id": None,
        "recommendation_id": "",
        "termination_reason": "",
    }
    result = cast(AgentState, await graph.ainvoke(state))
    return {
        "agent_run_id": result["agent_run_id"],
        "recommendation_id": result["recommendation_id"],
        "extraction_candidate_id": result.get("extraction_candidate_id"),
        "reason_codes": result["reason_codes"],
        "incomplete": result.get("termination_reason") != "RECOMMENDATION_SUBMITTED",
    }


def _bounded_json(payload: dict[str, object]) -> str:
    import json

    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    if len(encoded) > 20_000:
        raise ValueError("Agent model context exceeded its bounded contract")
    return encoded


def _safe_error_code(error: BaseException) -> str:
    if isinstance(error, AgentDeadlineExceeded):
        return "AGENT_TOTAL_TIMEOUT"
    if isinstance(error, AgentToolTimeout):
        return "AGENT_TOOL_TIMEOUT"
    if isinstance(error, (TransientProviderTimeout, TimeoutError)):
        return "AGENT_PROVIDER_TIMEOUT"
    if isinstance(error, (ProviderUnavailable, TransientProviderRateLimit)):
        return "AGENT_PROVIDER_UNAVAILABLE"
    if isinstance(error, MalformedModelResponse):
        return "AGENT_MALFORMED_MODEL_RESPONSE"
    if isinstance(error, PolicyViolation):
        return "AGENT_POLICY_REJECTED"
    return "AGENT_UNEXPECTED_FAILURE"


def _raise_mapped_provider_error(error: Exception) -> NoReturn:
    if isinstance(
        error,
        (
            TransientProviderTimeout,
            TransientProviderRateLimit,
            ProviderUnavailable,
            MalformedModelResponse,
        ),
    ):
        raise error
    name = type(error).__name__.casefold()
    if "ratelimit" in name or "resourceexhausted" in name:
        raise TransientProviderRateLimit("Model provider rate limit") from error
    if "timeout" in name or "deadlineexceeded" in name:
        raise TransientProviderTimeout("Model provider timeout") from error
    if "apiconnection" in name or "serviceunavailable" in name or "overloaded" in name:
        raise ProviderUnavailable("Model provider unavailable") from error
    raise error
