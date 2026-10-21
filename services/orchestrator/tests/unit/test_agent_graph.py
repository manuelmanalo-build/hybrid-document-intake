from __future__ import annotations

import asyncio
import time
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from intake_api.infrastructure.config import Settings
from intake_orchestrator.agent.backend import InMemoryAgentBackend
from intake_orchestrator.agent.contracts import SubmitRecommendationInput
from intake_orchestrator.agent.errors import (
    PolicyViolation,
    ProviderUnavailable,
    TransientDatabaseError,
    TransientProviderRateLimit,
    TransientProviderTimeout,
)
from intake_orchestrator.agent.graph import (
    _raise_mapped_provider_error,  # pyright: ignore[reportPrivateUsage]
    compile_agent_subgraph,
    run_agent,
)
from intake_orchestrator.agent.models import (
    ANTHROPIC_MAX_OUTPUT_TOKENS,
    StubChatModel,
    create_chat_model,
)
from intake_orchestrator.agent.policy import AgentLimits, AgentPolicyGuard
from intake_orchestrator.agent.tools import validate_tool_output

pytestmark = pytest.mark.graph


async def run_stub(
    *,
    backend: InMemoryAgentBackend | None = None,
    model: StubChatModel | None = None,
    limits: AgentLimits | None = None,
    identities: tuple[UUID, UUID, UUID, UUID] | None = None,
    total_timeout_seconds: float = 30,
    tool_timeout_seconds: float = 2,
) -> tuple[dict[str, object], InMemoryAgentBackend, tuple[UUID, UUID, UUID, UUID]]:
    graph_run_id, tenant_id, document_id, agent_run_id = identities or (
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
    )
    selected_backend = backend or InMemoryAgentBackend()
    graph = compile_agent_subgraph(
        model=model or StubChatModel(model_name="unit-stub"),
        backend=selected_backend,
        limits=limits or AgentLimits(),
        model_timeout_seconds=2,
        tool_timeout_seconds=tool_timeout_seconds,
        total_timeout_seconds=total_timeout_seconds,
    )
    outcome = await run_agent(
        graph,
        graph_run_id=graph_run_id,
        tenant_id=tenant_id,
        document_id=document_id,
        correlation_id="agent-unit",
        agent_run_id=agent_run_id,
    )
    return (
        dict(outcome),
        selected_backend,
        (
            graph_run_id,
            tenant_id,
            document_id,
            agent_run_id,
        ),
    )


@pytest.mark.asyncio
async def test_valid_dynamic_text_extraction_selection() -> None:
    outcome, backend, _ = await run_stub()

    assert outcome["incomplete"] is False
    assert [name for name, _ in backend.calls] == [
        "inspect_document",
        "classify_document",
        "extract_invoice_from_text",
        "validate_candidate",
        "submit_recommendation",
    ]


@pytest.mark.asyncio
async def test_visual_extraction_path() -> None:
    outcome, backend, _ = await run_stub(backend=InMemoryAgentBackend(text_available=False))

    assert outcome["incomplete"] is False
    assert "extract_invoice_from_visual_content" in [name for name, _ in backend.calls]


@pytest.mark.asyncio
@pytest.mark.parametrize("lookup_tool", ["lookup_supplier", "lookup_purchase_order"])
async def test_reference_lookup_paths(lookup_tool: str) -> None:
    plan = [
        "inspect_document",
        "classify_document",
        "extract_invoice_from_text",
        lookup_tool,
        "validate_candidate",
        "submit_recommendation",
    ]
    outcome, backend, _ = await run_stub(
        model=StubChatModel(model_name="unit-stub", decision_plan=plan)
    )

    assert outcome["incomplete"] is False
    assert lookup_tool in [name for name, _ in backend.calls]


@pytest.mark.asyncio
async def test_retry_extraction_path() -> None:
    outcome, backend, _ = await run_stub(backend=InMemoryAgentBackend(validation_retry=True))

    names = [name for name, _ in backend.calls]
    assert outcome["incomplete"] is False
    assert names.count("retry_extraction") == 1
    assert names.count("extract_invoice_from_visual_content") == 1


@pytest.mark.asyncio
async def test_different_valid_tool_ordering() -> None:
    plan = [
        "inspect_document",
        "classify_document",
        "lookup_supplier",
        "extract_invoice_from_text",
        "validate_candidate",
        "submit_recommendation",
    ]
    outcome, backend, _ = await run_stub(
        model=StubChatModel(model_name="unit-stub", decision_plan=plan)
    )

    assert outcome["incomplete"] is False
    assert [name for name, _ in backend.calls][2:4] == [
        "lookup_supplier",
        "extract_invoice_from_text",
    ]


@pytest.mark.asyncio
async def test_invalid_tool_is_rejected_and_persisted_incomplete() -> None:
    outcome, backend, _ = await run_stub(
        model=StubChatModel(model_name="unit-stub", decision_plan=["create_zapier_event"])
    )

    assert outcome["incomplete"] is True
    assert "AGENT_POLICY_REJECTED" in outcome["reason_codes"]
    assert backend.calls == []


def test_policy_rejects_malformed_arguments_and_tenant_mismatch() -> None:
    guard = AgentPolicyGuard(AgentLimits())
    tenant_id, document_id, agent_run_id = uuid4(), uuid4(), uuid4()
    base = {
        "tenant_id": str(tenant_id),
        "document_id": str(document_id),
        "agent_run_id": str(agent_run_id),
    }
    with pytest.raises(PolicyViolation, match="schema"):
        guard.validate(
            tool_name="inspect_document",
            arguments={**base, "unexpected": True},
            tenant_id=str(tenant_id),
            document_id=str(document_id),
            agent_run_id=str(agent_run_id),
            tool_call_count=0,
            extraction_attempts=0,
            supplier_lookups=0,
            purchase_order_lookups=0,
            tools_used=[],
            extraction_candidate_id=None,
        )
    with pytest.raises(PolicyViolation, match="identity"):
        guard.validate(
            tool_name="inspect_document",
            arguments={**base, "tenant_id": str(uuid4())},
            tenant_id=str(tenant_id),
            document_id=str(document_id),
            agent_run_id=str(agent_run_id),
            tool_call_count=0,
            extraction_attempts=0,
            supplier_lookups=0,
            purchase_order_lookups=0,
            tools_used=[],
            extraction_candidate_id=None,
        )


@pytest.mark.asyncio
async def test_tool_call_limit_persists_incomplete_recommendation() -> None:
    outcome, backend, _ = await run_stub(limits=AgentLimits(max_tool_calls=2))

    assert outcome["incomplete"] is True
    assert "AGENT_TOOL_LIMIT_REACHED" in outcome["reason_codes"]
    assert len(backend.calls) == 2


@pytest.mark.asyncio
async def test_extraction_attempt_limit_is_policy_enforced() -> None:
    backend = InMemoryAgentBackend(validation_retry=True)
    outcome, _, _ = await run_stub(backend=backend, limits=AgentLimits(max_extraction_attempts=1))

    assert outcome["incomplete"] is True
    assert "AGENT_POLICY_REJECTED" in outcome["reason_codes"]


@pytest.mark.asyncio
@pytest.mark.parametrize("lookup_tool", ["lookup_supplier", "lookup_purchase_order"])
async def test_individual_lookup_limits_are_policy_enforced(lookup_tool: str) -> None:
    plan = [
        "inspect_document",
        "classify_document",
        "extract_invoice_from_text",
        lookup_tool,
        lookup_tool,
        lookup_tool,
    ]
    outcome, backend, _ = await run_stub(
        model=StubChatModel(model_name="unit-stub", decision_plan=plan)
    )

    assert outcome["incomplete"] is True
    assert [name for name, _ in backend.calls].count(lookup_tool) == 2


@pytest.mark.asyncio
async def test_total_agent_timeout_persists_incomplete_recommendation() -> None:
    outcome, backend, _ = await run_stub(total_timeout_seconds=0)

    assert outcome["incomplete"] is True
    assert "AGENT_TOTAL_TIMEOUT" in outcome["reason_codes"]
    assert backend.calls == []


@pytest.mark.asyncio
async def test_total_agent_timeout_survives_orchestrator_restart() -> None:
    identities = (uuid4(), uuid4(), uuid4(), uuid4())
    backend = InMemoryAgentBackend()
    backend.agent_started_at[identities[3]] = time.time() - 60

    outcome, _, _ = await run_stub(
        backend=backend,
        identities=identities,
        total_timeout_seconds=30,
    )

    assert outcome["incomplete"] is True
    assert "AGENT_TOTAL_TIMEOUT" in outcome["reason_codes"]
    assert backend.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure_mode", "reason_code"),
    [
        ("timeout", "AGENT_PROVIDER_TIMEOUT"),
        ("rate_limit", "AGENT_PROVIDER_UNAVAILABLE"),
        ("unavailable", "AGENT_PROVIDER_UNAVAILABLE"),
        ("malformed", "AGENT_MALFORMED_MODEL_RESPONSE"),
    ],
)
async def test_controlled_provider_and_model_failures(failure_mode: str, reason_code: str) -> None:
    outcome, _, _ = await run_stub(
        model=StubChatModel(model_name="unit-stub", failure_mode=failure_mode)
    )

    assert outcome["incomplete"] is True
    assert reason_code in outcome["reason_codes"]


@pytest.mark.asyncio
async def test_graph_replay_does_not_duplicate_side_effects() -> None:
    identities = (uuid4(), uuid4(), uuid4(), uuid4())
    backend = InMemoryAgentBackend()
    first, _, _ = await run_stub(backend=backend, identities=identities)
    call_count = len(backend.calls)
    second, _, _ = await run_stub(backend=backend, identities=identities)

    assert second["recommendation_id"] == first["recommendation_id"]
    assert len(backend.calls) == call_count


class RetryOnceBackend(InMemoryAgentBackend):
    def __init__(self) -> None:
        super().__init__()
        self.failures = 0

    async def execute(
        self, *, tool_name: str, arguments: object, operation_key: str
    ) -> dict[str, object]:
        if tool_name == "inspect_document" and self.failures == 0:
            self.failures += 1
            raise TransientDatabaseError("temporary")
        return await super().execute(
            tool_name=tool_name,
            arguments=arguments,  # type: ignore[arg-type]
            operation_key=operation_key,
        )


class SlowToolBackend(InMemoryAgentBackend):
    async def execute(
        self, *, tool_name: str, arguments: object, operation_key: str
    ) -> dict[str, object]:
        await asyncio.sleep(0.02)
        return await super().execute(
            tool_name=tool_name,
            arguments=arguments,  # type: ignore[arg-type]
            operation_key=operation_key,
        )


class MalformedToolOutputBackend(InMemoryAgentBackend):
    async def execute(
        self, *, tool_name: str, arguments: object, operation_key: str
    ) -> dict[str, object]:
        if tool_name == "inspect_document":
            return {"document_id": "not-a-uuid"}
        return await super().execute(
            tool_name=tool_name,
            arguments=arguments,  # type: ignore[arg-type]
            operation_key=operation_key,
        )


@pytest.mark.asyncio
async def test_transient_tool_node_retries() -> None:
    backend = RetryOnceBackend()
    outcome, _, _ = await run_stub(backend=backend)

    assert outcome["incomplete"] is False
    assert backend.failures == 1


@pytest.mark.asyncio
async def test_tool_timeout_degrades_to_incomplete_human_review_result() -> None:
    outcome, backend, _ = await run_stub(backend=SlowToolBackend(), tool_timeout_seconds=0.001)

    assert outcome["incomplete"] is True
    assert "AGENT_TOOL_TIMEOUT" in outcome["reason_codes"]
    assert backend.calls == []
    assert len(backend.tool_failures) == 1


@pytest.mark.asyncio
async def test_total_deadline_caps_an_in_flight_tool_call() -> None:
    outcome, _, _ = await run_stub(
        backend=SlowToolBackend(),
        total_timeout_seconds=0.005,
        tool_timeout_seconds=2,
    )

    assert outcome["incomplete"] is True
    assert "AGENT_TOTAL_TIMEOUT" in outcome["reason_codes"]


@pytest.mark.asyncio
async def test_malformed_tool_output_degrades_to_incomplete_human_review_result() -> None:
    outcome, backend, _ = await run_stub(backend=MalformedToolOutputBackend())

    assert outcome["incomplete"] is True
    assert "AGENT_MALFORMED_MODEL_RESPONSE" in outcome["reason_codes"]
    assert len(backend.tool_failures) == 1


def test_every_tool_output_is_pydantic_validated() -> None:
    validated = validate_tool_output(
        "inspect_document",
        {
            "document_id": str(uuid4()),
            "page_count": 1,
            "text_available": True,
            "visual_content_present": False,
            "media_type": "application/pdf",
        },
    )
    assert validated["page_count"] == 1

    with pytest.raises(ValidationError):
        validate_tool_output(
            "inspect_document",
            {
                "document_id": str(uuid4()),
                "page_count": 0,
                "text_available": True,
                "visual_content_present": False,
                "media_type": "application/pdf",
            },
        )


def test_agent_cannot_approve_or_encode_zapier_operation() -> None:
    identity = {
        "tenant_id": uuid4(),
        "document_id": uuid4(),
        "agent_run_id": uuid4(),
        "extraction_candidate_id": uuid4(),
        "document_type": "AP_INVOICE",
        "document_type_confidence": 1,
        "reason_codes": [],
        "summary": "review",
        "requested_human_actions": [],
        "tools_used": [],
    }
    with pytest.raises(ValidationError):
        SubmitRecommendationInput.model_validate(
            {**identity, "recommended_disposition": "AUTO_APPROVE"}
        )
    with pytest.raises(PolicyViolation, match="allowlisted"):
        AgentPolicyGuard(AgentLimits()).validate(
            tool_name="create_zapier_event",
            arguments=identity,
            tenant_id=str(identity["tenant_id"]),
            document_id=str(identity["document_id"]),
            agent_run_id=str(identity["agent_run_id"]),
            tool_call_count=0,
            extraction_attempts=0,
            supplier_lookups=0,
            purchase_order_lookups=0,
            tools_used=[],
            extraction_candidate_id=None,
        )


def test_provider_factories_use_configured_model_without_network_calls() -> None:
    stub = create_chat_model(Settings(agent_provider="stub", agent_model="configured-stub"))
    openai = create_chat_model(
        Settings(
            agent_provider="openai",
            agent_model="configured-openai",
            openai_api_key="test-only",
        )
    )
    gemini = create_chat_model(
        Settings(
            agent_provider="gemini",
            agent_model="configured-gemini",
            google_api_key="test-only",
        )
    )

    anthropic = create_chat_model(
        Settings(
            agent_provider="anthropic",
            agent_model="configured-claude",
            anthropic_api_key="test-only",
        )
    )

    assert type(stub).__name__ == "StubChatModel"
    assert type(openai).__name__ == "ChatOpenAI"
    assert type(gemini).__name__ == "ChatGoogleGenerativeAI"
    assert type(anthropic).__name__ == "ChatAnthropic"
    assert anthropic.model == "configured-claude"  # type: ignore[attr-defined]
    assert anthropic.max_retries == 0  # type: ignore[attr-defined]
    assert anthropic.max_tokens == ANTHROPIC_MAX_OUTPUT_TOKENS  # type: ignore[attr-defined]


@pytest.mark.parametrize("provider", ["openai", "gemini", "anthropic"])
def test_live_provider_configuration_requires_its_credential(provider: str) -> None:
    with pytest.raises(ValidationError, match="API_KEY"):
        Settings(
            agent_provider=provider,
            agent_model="configured-model",
            openai_api_key=None,
            google_api_key=None,
            anthropic_api_key=None,
        )


@pytest.mark.parametrize(
    ("error_name", "expected"),
    [
        ("RateLimitError", TransientProviderRateLimit),
        ("APITimeoutError", TransientProviderTimeout),
        ("APIConnectionError", ProviderUnavailable),
        ("OverloadedError", ProviderUnavailable),
    ],
)
def test_anthropic_style_provider_errors_map_to_safe_transient_errors(
    error_name: str, expected: type[Exception]
) -> None:
    error = type(error_name, (Exception,), {})()
    with pytest.raises(expected):
        _raise_mapped_provider_error(error)
