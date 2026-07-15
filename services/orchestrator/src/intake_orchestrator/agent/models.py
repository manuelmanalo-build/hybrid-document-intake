from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any, Literal, cast

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable
from pydantic import Field

from intake_api.infrastructure.config import Settings
from intake_orchestrator.agent.errors import (
    MalformedModelResponse,
    ProviderUnavailable,
    TransientProviderRateLimit,
    TransientProviderTimeout,
)


class StubChatModel(BaseChatModel):
    """Deterministic model double that still chooses tools from bounded state."""

    model_name: str
    decision_plan: list[str] = Field(default_factory=list)
    failure_mode: Literal["none", "timeout", "rate_limit", "unavailable", "malformed"] = "none"

    @property
    def _llm_type(self) -> str:
        return "deterministic-tool-selecting-stub"

    @property
    def _identifying_params(self) -> dict[str, Any]:
        return {"model_name": self.model_name}

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Any],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> Runnable[Any, AIMessage]:
        del tools, tool_choice, kwargs
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        del stop, run_manager, kwargs
        if self.failure_mode == "timeout":
            raise TransientProviderTimeout("stub provider timeout")
        if self.failure_mode == "rate_limit":
            raise TransientProviderRateLimit("stub provider rate limit")
        if self.failure_mode == "unavailable":
            raise ProviderUnavailable("stub provider unavailable")
        if self.failure_mode == "malformed":
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content="invalid"))])
        payload = json.loads(str(messages[-1].content))
        tool_name, arguments = self._choose(payload)
        message = AIMessage(
            content="",
            tool_calls=[
                {
                    "name": tool_name,
                    "args": arguments,
                    "id": f"stub-call-{payload['tool_call_count'] + 1}",
                    "type": "tool_call",
                }
            ],
            response_metadata={"provider": "stub", "model": self.model_name},
        )
        return ChatResult(generations=[ChatGeneration(message=message)])

    def _choose(self, state: dict[str, Any]) -> tuple[str, dict[str, object]]:
        call_count = int(state["tool_call_count"])
        results = state.get("tool_results", {})
        if call_count < len(self.decision_plan):
            name = self.decision_plan[call_count]
        elif "inspect_document" not in results:
            name = "inspect_document"
        elif "classify_document" not in results:
            name = "classify_document"
        elif not any(key.startswith("extract_invoice_from_") for key in results):
            inspection = results["inspect_document"]
            name = (
                "extract_invoice_from_text"
                if inspection.get("text_available", False)
                else "extract_invoice_from_visual_content"
            )
        elif "validate_candidate" not in results:
            name = "validate_candidate"
        elif results["validate_candidate"].get("retry_recommended") and (
            "retry_extraction" not in results
        ):
            name = "retry_extraction"
        elif "retry_extraction" in results and state["extraction_attempts"] < 2:
            strategy = results["retry_extraction"]["next_strategy"]
            name = (
                "extract_invoice_from_text"
                if strategy == "TEXT"
                else "extract_invoice_from_visual_content"
            )
        else:
            name = "submit_recommendation"
        return name, _stub_arguments(name, state)


def create_chat_model(settings: Settings) -> BaseChatModel:
    if settings.agent_provider == "stub":
        return StubChatModel(model_name=settings.agent_model)
    if settings.agent_provider == "openai":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=settings.agent_model,
            api_key=settings.openai_api_key,
            timeout=settings.agent_model_timeout_seconds,
            max_retries=0,
        )
    if settings.agent_provider == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI

        return ChatGoogleGenerativeAI(
            model=settings.agent_model,
            google_api_key=settings.google_api_key,
            timeout=settings.agent_model_timeout_seconds,
            max_retries=0,
        )
    raise ValueError("Unsupported configured agent provider")


def require_one_tool_call(message: AIMessage) -> tuple[str, dict[str, object]]:
    if len(message.tool_calls) != 1:
        raise MalformedModelResponse("Model must request exactly one approved tool")
    call = message.tool_calls[0]
    return _validated_tool_call_parts(call.get("name"), call.get("args"))


def _validated_tool_call_parts(name: object, arguments: object) -> tuple[str, dict[str, object]]:
    if not isinstance(name, str) or not name or not isinstance(arguments, dict):
        raise MalformedModelResponse("Model tool request name and arguments must be structured")
    return name, cast(dict[str, object], arguments)


def _stub_arguments(name: str, state: dict[str, Any]) -> dict[str, object]:
    identity: dict[str, object] = {
        "tenant_id": state["tenant_id"],
        "document_id": state["document_id"],
        "agent_run_id": state["agent_run_id"],
    }
    results = state.get("tool_results", {})
    latest_candidate = state.get("extraction_candidate_id")
    if name == "classify_document":
        identity["inspection_completed"] = True
    elif name.startswith("extract_invoice_from_"):
        identity["attempt_number"] = int(state["extraction_attempts"]) + 1
    elif name == "lookup_supplier":
        identity["supplier_name"] = "Demo Supplier"
    elif name == "lookup_purchase_order":
        identity["purchase_order_number"] = "PO-DEMO"
    elif name == "validate_candidate":
        identity["extraction_candidate_id"] = latest_candidate
    elif name == "retry_extraction":
        identity.update(
            {
                "extraction_candidate_id": latest_candidate,
                "next_strategy": "VISUAL",
                "reason_codes": results["validate_candidate"].get("reason_codes", ["RETRY"]),
            }
        )
    elif name == "submit_recommendation":
        classification = results.get("classify_document", {})
        identity.update(
            {
                "extraction_candidate_id": latest_candidate,
                "document_type": classification.get("value", "UNKNOWN"),
                "document_type_confidence": classification.get("confidence", 0.0),
                "recommended_disposition": "HUMAN_REVIEW",
                "reason_codes": results.get("validate_candidate", {}).get(
                    "reason_codes", ["AGENT_INCOMPLETE"]
                ),
                "summary": "Agent completed bounded evidence gathering; human review is required.",
                "requested_human_actions": ["Verify extracted invoice fields"],
                "tools_used": state["tools_used"],
            }
        )
    return identity
