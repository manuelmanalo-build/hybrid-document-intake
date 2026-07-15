from __future__ import annotations

from datetime import UTC, datetime
from random import Random
from uuid import uuid4

import pytest

from intake_api.domain.enums import DispatchRequestStatus
from intake_api.infrastructure.config import Settings
from intake_orchestrator.dispatcher import (
    GraphResumeDispatcher,
    GraphRunDispatcher,
    calculate_backoff_seconds,
    is_retryable_dispatch_error,
)
from intake_orchestrator.graph import GraphExecutionOutcome, GraphRunInput
from intake_orchestrator.repository import (
    LeasedGraphResumeRequest,
    LeasedGraphRunRequest,
    _lease_attempt_number,
)


class FakeRepository:
    def __init__(self, request: LeasedGraphRunRequest) -> None:
        self.request = request
        self.running = False
        self.completed: GraphExecutionOutcome | None = None
        self.failed: tuple[str, str, float, bool] | None = None

    async def lease_next(
        self, *, owner: str, now: datetime, lease_seconds: int
    ) -> LeasedGraphRunRequest | None:
        return self.request

    async def mark_running(
        self, *, request: LeasedGraphRunRequest, owner: str, now: datetime
    ) -> bool:
        self.running = True
        return True

    async def mark_completed(
        self,
        *,
        request: LeasedGraphRunRequest,
        owner: str,
        now: datetime,
        outcome: GraphExecutionOutcome,
    ) -> None:
        self.completed = outcome

    async def mark_failed(
        self,
        *,
        request: LeasedGraphRunRequest,
        owner: str,
        now: datetime,
        error_code: str,
        safe_message: str,
        delay_seconds: float,
        retryable: bool,
    ) -> DispatchRequestStatus:
        self.failed = (error_code, safe_message, delay_seconds, retryable)
        return (
            DispatchRequestStatus.RETRY_SCHEDULED if retryable else DispatchRequestStatus.EXHAUSTED
        )


class SuccessfulExecutor:
    async def execute(self, run: GraphRunInput) -> GraphExecutionOutcome:
        return GraphExecutionOutcome(status="INTERRUPTED", replayed=False)


class FailingExecutor:
    async def execute(self, run: GraphRunInput) -> GraphExecutionOutcome:
        raise ConnectionError("secret database address must not be persisted")


class PermanentlyFailingExecutor:
    async def execute(self, run: GraphRunInput) -> GraphExecutionOutcome:
        raise ValueError("unsupported persisted graph version")


def leased_request() -> LeasedGraphRunRequest:
    return LeasedGraphRunRequest(
        request_id=uuid4(),
        run=GraphRunInput(
            graph_run_id=uuid4(),
            graph_version="document-intake-v1",
            thread_id=str(uuid4()),
            tenant_id=uuid4(),
            document_id=uuid4(),
            correlation_id="unit-dispatch",
        ),
        attempt_count=1,
        max_attempts=5,
    )


@pytest.mark.asyncio
async def test_dispatcher_completes_a_successful_graph() -> None:
    repository = FakeRepository(leased_request())
    dispatcher = GraphRunDispatcher(
        repository=repository,
        executor=SuccessfulExecutor(),
        settings=Settings(),
        owner="unit-orchestrator",
    )

    assert await dispatcher.dispatch_one(datetime(2026, 7, 14, tzinfo=UTC)) is True
    assert repository.running is True
    assert repository.completed == GraphExecutionOutcome(status="INTERRUPTED", replayed=False)


@pytest.mark.asyncio
async def test_dispatcher_schedules_retry_with_sanitized_error() -> None:
    repository = FakeRepository(leased_request())
    dispatcher = GraphRunDispatcher(
        repository=repository,
        executor=FailingExecutor(),
        settings=Settings(),
        owner="unit-orchestrator",
        rng=Random(7),  # noqa: S311 -- deterministic retry fixture
    )

    assert await dispatcher.dispatch_one(datetime(2026, 7, 14, tzinfo=UTC)) is True
    assert repository.completed is None
    assert repository.failed is not None
    assert repository.failed[0] == "ConnectionError"
    assert "secret" not in repository.failed[1]
    assert repository.failed[3] is True


@pytest.mark.asyncio
async def test_dispatcher_exhausts_permanent_failure_without_retry() -> None:
    repository = FakeRepository(leased_request())
    dispatcher = GraphRunDispatcher(
        repository=repository,
        executor=PermanentlyFailingExecutor(),
        settings=Settings(),
        owner="unit-orchestrator",
    )

    assert await dispatcher.dispatch_one(datetime(2026, 7, 14, tzinfo=UTC)) is True
    assert repository.failed is not None
    assert repository.failed[0] == "ValueError"
    assert repository.failed[3] is False


def test_backoff_is_exponential_jittered_and_capped() -> None:
    rng = Random(7)  # noqa: S311 -- deterministic retry fixture
    first = calculate_backoff_seconds(attempt=1, base=2, cap=10, rng=rng)
    later = calculate_backoff_seconds(attempt=8, base=2, cap=10, rng=rng)

    assert 1.5 <= first <= 2.5
    assert 7.5 <= later <= 10


def test_expired_lease_recovery_does_not_consume_an_attempt() -> None:
    assert _lease_attempt_number(5, recovering_expired_lease=True) == 5
    assert _lease_attempt_number(4, recovering_expired_lease=False) == 5
    assert is_retryable_dispatch_error(ConnectionError()) is True
    assert is_retryable_dispatch_error(ValueError()) is False


class FakeResumeRepository:
    def __init__(self, request: LeasedGraphResumeRequest) -> None:
        self.request = request
        self.completed: GraphExecutionOutcome | None = None
        self.failed: tuple[str, str, float, bool] | None = None

    async def lease_next(
        self, *, owner: str, now: datetime, lease_seconds: int
    ) -> LeasedGraphResumeRequest | None:
        return self.request

    async def mark_running(
        self, *, request: LeasedGraphResumeRequest, owner: str, now: datetime
    ) -> bool:
        return True

    async def mark_completed(
        self,
        *,
        request: LeasedGraphResumeRequest,
        owner: str,
        now: datetime,
        outcome: GraphExecutionOutcome,
    ) -> None:
        self.completed = outcome

    async def mark_failed(
        self,
        *,
        request: LeasedGraphResumeRequest,
        owner: str,
        now: datetime,
        error_code: str,
        safe_message: str,
        delay_seconds: float,
        retryable: bool,
    ) -> DispatchRequestStatus:
        self.failed = (error_code, safe_message, delay_seconds, retryable)
        return (
            DispatchRequestStatus.RETRY_SCHEDULED if retryable else DispatchRequestStatus.EXHAUSTED
        )


class SuccessfulResumeExecutor:
    def __init__(self) -> None:
        self.payloads: list[dict[str, object]] = []

    async def resume(self, run: GraphRunInput, payload: dict[str, object]) -> GraphExecutionOutcome:
        self.payloads.append(payload)
        return GraphExecutionOutcome(status="COMPLETED", replayed=False)


def leased_resume_request() -> LeasedGraphResumeRequest:
    original = leased_request()
    return LeasedGraphResumeRequest(
        request_id=uuid4(),
        run=original.run,
        payload={"decision": "APPROVE", "review_cycle": 1},
        attempt_count=1,
        max_attempts=5,
    )


@pytest.mark.asyncio
async def test_resume_dispatcher_passes_validated_payload_once() -> None:
    repository = FakeResumeRepository(leased_resume_request())
    executor = SuccessfulResumeExecutor()
    dispatcher = GraphResumeDispatcher(
        repository=repository,
        executor=executor,
        settings=Settings(),
        owner="unit-resumer",
    )

    assert await dispatcher.dispatch_one(datetime(2026, 7, 14, tzinfo=UTC)) is True
    assert executor.payloads == [{"decision": "APPROVE", "review_cycle": 1}]
    assert repository.completed == GraphExecutionOutcome(status="COMPLETED", replayed=False)
