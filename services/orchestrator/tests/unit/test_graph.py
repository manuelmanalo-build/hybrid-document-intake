from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from intake_orchestrator.graph import GraphRunInput, _initial_state, build_parent_graph
from intake_orchestrator.workflow import InMemoryWorkflowBackend

pytestmark = pytest.mark.graph


class FakeResolutionAgent:
    def __init__(self, *, incomplete: bool = False) -> None:
        self.incomplete = incomplete

    async def resolve(
        self,
        *,
        graph_run_id: UUID,
        tenant_id: UUID,
        document_id: UUID,
        correlation_id: str,
        agent_run_id: UUID,
    ) -> dict[str, object]:
        del graph_run_id, tenant_id, document_id, correlation_id
        return {
            "agent_run_id": str(agent_run_id),
            "recommendation_id": str(uuid4()),
            "extraction_candidate_id": str(uuid4()) if not self.incomplete else None,
            "reason_codes": ["AGENT_LIMIT"] if self.incomplete else [],
            "incomplete": self.incomplete,
        }


def graph_input() -> GraphRunInput:
    return GraphRunInput(
        graph_run_id=uuid4(),
        graph_version="document-intake-v1",
        thread_id=str(uuid4()),
        tenant_id=uuid4(),
        document_id=uuid4(),
        correlation_id="unit-parent",
    )


@pytest.mark.asyncio
async def test_all_initial_cases_pause_at_human_review() -> None:
    workflow = InMemoryWorkflowBackend()
    run = graph_input()
    config = {"configurable": {"thread_id": run.thread_id}}
    graph = build_parent_graph(workflow=workflow, resolution_agent=FakeResolutionAgent()).compile(
        checkpointer=InMemorySaver()
    )

    result = await graph.ainvoke(_initial_state(run), config=config)
    snapshot = await graph.aget_state(config)

    assert result["current_stage"] == "REVIEW_TASK_CREATED"
    assert result["review_task_id"] is not None
    assert snapshot.next == ("human_review_interrupt",)
    assert snapshot.tasks[0].interrupts
    payload = snapshot.tasks[0].interrupts[0].value
    assert set(payload) == {
        "review_task_id",
        "document_id",
        "review_cycle",
        "extraction_candidate_id",
        "validation_run_id",
        "confidence_run_id",
        "agent_recommendation_summary",
        "reason_codes",
        "allowed_response_types",
    }
    assert "pdf" not in str(payload).casefold()
    assert len(str(payload).encode()) < 4_096


@pytest.mark.asyncio
async def test_approve_routes_to_transactional_outbox_boundary() -> None:
    workflow = InMemoryWorkflowBackend()
    run = graph_input()
    config = {"configurable": {"thread_id": run.thread_id}}
    graph = build_parent_graph(workflow=workflow, resolution_agent=FakeResolutionAgent()).compile(
        checkpointer=InMemorySaver()
    )
    await graph.ainvoke(_initial_state(run), config=config)

    result = await graph.ainvoke(
        Command(
            resume={
                "decision": "APPROVE",
                "actor_id": "reviewer-1",
                "reason_codes": [],
                "corrections": {},
            }
        ),
        config=config,
    )

    assert result["current_stage"] == "APPROVED_OUTBOX_CREATED"
    assert run.graph_run_id in workflow.outbox


@pytest.mark.asyncio
async def test_reject_routes_to_authoritative_rejection() -> None:
    workflow = InMemoryWorkflowBackend()
    run = graph_input()
    config = {"configurable": {"thread_id": run.thread_id}}
    graph = build_parent_graph(workflow=workflow, resolution_agent=FakeResolutionAgent()).compile(
        checkpointer=InMemorySaver()
    )
    await graph.ainvoke(_initial_state(run), config=config)

    result = await graph.ainvoke(
        Command(
            resume={
                "decision": "REJECT",
                "actor_id": "reviewer-1",
                "reason_codes": ["INVALID_INVOICE"],
                "corrections": {},
            }
        ),
        config=config,
    )

    assert result["current_stage"] == "REJECTED"
    assert run.graph_run_id in workflow.rejections


@pytest.mark.asyncio
async def test_request_correction_creates_new_review_cycle_and_interrupts_again() -> None:
    workflow = InMemoryWorkflowBackend()
    run = graph_input()
    config = {"configurable": {"thread_id": run.thread_id}}
    graph = build_parent_graph(workflow=workflow, resolution_agent=FakeResolutionAgent()).compile(
        checkpointer=InMemorySaver()
    )
    initial = await graph.ainvoke(_initial_state(run), config=config)
    task_id = UUID(initial["review_task_id"])

    await graph.ainvoke(
        Command(
            resume={
                "decision": "REQUEST_CORRECTION",
                "actor_id": "reviewer-1",
                "reason_codes": ["MISSING_FIELD"],
                "corrections": {"invoice_number": "INV-42"},
            }
        ),
        config=config,
    )
    snapshot = await graph.aget_state(config)

    assert snapshot.next == ("human_review_interrupt",)
    assert workflow.review_tasks[task_id]["cycle"] == 2
    assert workflow.review_tasks[task_id]["corrections"] == {"invoice_number": "INV-42"}


@pytest.mark.asyncio
async def test_parent_progresses_after_incomplete_agent_run() -> None:
    workflow = InMemoryWorkflowBackend()
    run = graph_input()
    config = {"configurable": {"thread_id": run.thread_id}}
    graph = build_parent_graph(
        workflow=workflow, resolution_agent=FakeResolutionAgent(incomplete=True)
    ).compile(checkpointer=InMemorySaver())

    result = await graph.ainvoke(_initial_state(run), config=config)

    assert result["current_stage"] == "REVIEW_TASK_CREATED"
    assert "AGENT_INCOMPLETE" in result["recoverable_errors"]
    assert "BR-010" in result["reason_codes"]
