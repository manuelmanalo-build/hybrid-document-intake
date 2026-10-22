from uuid import uuid4

import pytest

from intake_orchestrator.agent.backend import InMemoryAgentBackend
from intake_orchestrator.agent.errors import PolicyViolation
from intake_orchestrator.agent.graph import compile_agent_subgraph, run_agent
from intake_orchestrator.agent.models import StubChatModel
from intake_orchestrator.agent.policy import AgentLimits, AgentPolicyGuard


async def _run(
    *,
    backend: InMemoryAgentBackend,
    plan: list[str] | None = None,
    limits: AgentLimits | None = None,
) -> dict[str, object]:
    graph = compile_agent_subgraph(
        model=StubChatModel(model_name="unit-stub", decision_plan=plan or []),
        backend=backend,
        limits=limits or AgentLimits(),
        model_timeout_seconds=2,
        tool_timeout_seconds=2,
        total_timeout_seconds=30,
    )
    return dict(
        await run_agent(
            graph,
            graph_run_id=uuid4(),
            tenant_id=uuid4(),
            document_id=uuid4(),
            correlation_id="claims-unit",
            agent_run_id=uuid4(),
        )
    )


def _names(backend: InMemoryAgentBackend) -> list[str]:
    return [name for name, _ in backend.calls]


@pytest.mark.asyncio
async def test_claim_packet_follows_the_claim_tool_path() -> None:
    backend = InMemoryAgentBackend(document_type="CLAIM_PACKET")

    outcome = await _run(backend=backend)

    assert outcome["incomplete"] is False
    assert _names(backend) == [
        "inspect_document",
        "classify_document",
        "segment_claim_packet",
        "extract_claim_from_text",
        "validate_candidate",
        "submit_recommendation",
    ]
    submitted = next(iter(backend.recommendations.values()))
    assert submitted["recommendation_id"]


@pytest.mark.asyncio
async def test_scanned_claim_uses_visual_extraction() -> None:
    backend = InMemoryAgentBackend(document_type="CLAIM_PACKET", text_available=False)

    await _run(backend=backend)

    assert "extract_claim_from_visual_content" in _names(backend)


@pytest.mark.asyncio
async def test_claim_retry_stays_on_claim_tools() -> None:
    backend = InMemoryAgentBackend(document_type="CLAIM_PACKET", validation_retry=True)

    outcome = await _run(backend=backend)

    names = _names(backend)
    assert outcome["incomplete"] is False
    assert names.count("retry_extraction") == 1
    assert "extract_claim_from_visual_content" in names
    assert not any(name.startswith("extract_invoice_from_") for name in names)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("document_type", "forbidden_tool"),
    [
        ("CLAIM_PACKET", "extract_invoice_from_text"),
        ("CLAIM_PACKET", "lookup_supplier"),
        ("AP_INVOICE", "segment_claim_packet"),
        ("AP_INVOICE", "lookup_policy"),
    ],
)
async def test_tools_are_scoped_to_the_classified_document_type(
    document_type: str, forbidden_tool: str
) -> None:
    backend = InMemoryAgentBackend(document_type=document_type)

    outcome = await _run(
        backend=backend, plan=["inspect_document", "classify_document", forbidden_tool]
    )

    assert outcome["incomplete"] is True
    assert "AGENT_POLICY_REJECTED" in outcome["reason_codes"]  # type: ignore[operator]
    assert forbidden_tool not in _names(backend)


@pytest.mark.asyncio
async def test_claim_extraction_requires_segmentation_first() -> None:
    backend = InMemoryAgentBackend(document_type="CLAIM_PACKET")

    outcome = await _run(
        backend=backend,
        plan=["inspect_document", "classify_document", "extract_claim_from_text"],
    )

    assert outcome["incomplete"] is True
    assert "extract_claim_from_text" not in _names(backend)


@pytest.mark.asyncio
@pytest.mark.parametrize("lookup_tool", ["lookup_policy", "lookup_policy_vehicle"])
async def test_claim_lookup_limits_are_enforced(lookup_tool: str) -> None:
    backend = InMemoryAgentBackend(document_type="CLAIM_PACKET")

    outcome = await _run(
        backend=backend,
        plan=["inspect_document", "classify_document", lookup_tool, lookup_tool, lookup_tool],
    )

    assert outcome["incomplete"] is True
    assert _names(backend).count(lookup_tool) == 2


@pytest.mark.asyncio
async def test_unknown_document_skips_extraction_and_goes_to_review() -> None:
    backend = InMemoryAgentBackend(document_type="UNKNOWN")

    outcome = await _run(backend=backend)

    assert outcome["incomplete"] is False
    assert _names(backend) == ["inspect_document", "classify_document", "submit_recommendation"]


def test_recommendation_type_must_match_the_classification() -> None:
    tenant_id, document_id, agent_run_id = uuid4(), uuid4(), uuid4()
    identity = {
        "tenant_id": str(tenant_id),
        "document_id": str(document_id),
        "agent_run_id": str(agent_run_id),
    }
    with pytest.raises(PolicyViolation, match="does not match classification"):
        AgentPolicyGuard(AgentLimits()).validate(
            tool_name="submit_recommendation",
            arguments={
                **identity,
                "document_type": "UNSUPPORTED",
                "document_type_confidence": 0.5,
                "recommended_disposition": "HUMAN_REVIEW",
                "reason_codes": [],
                "summary": "review",
                "requested_human_actions": [],
                "tools_used": ["inspect_document", "classify_document"],
            },
            tenant_id=str(tenant_id),
            document_id=str(document_id),
            agent_run_id=str(agent_run_id),
            tool_call_count=2,
            extraction_attempts=0,
            supplier_lookups=0,
            purchase_order_lookups=0,
            tools_used=["inspect_document", "classify_document"],
            extraction_candidate_id=None,
            document_type="CLAIM_PACKET",
        )
