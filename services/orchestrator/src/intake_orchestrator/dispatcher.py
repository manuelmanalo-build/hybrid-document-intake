from __future__ import annotations

import asyncio
import re
import socket
from datetime import UTC, datetime
from random import Random, SystemRandom
from typing import Protocol
from uuid import uuid4

import structlog
from sqlalchemy.exc import OperationalError

from intake_api.domain.enums import DispatchRequestStatus
from intake_api.infrastructure.config import Settings
from intake_orchestrator.graph import GraphExecutionOutcome, GraphRunInput
from intake_orchestrator.repository import LeasedGraphResumeRequest, LeasedGraphRunRequest

logger = structlog.get_logger(__name__)
_SAFE_ERROR_CODE = re.compile(r"[^A-Za-z0-9_.-]")


class GraphExecutor(Protocol):
    async def execute(self, run: GraphRunInput) -> GraphExecutionOutcome: ...


class ResumeExecutor(Protocol):
    async def resume(
        self, run: GraphRunInput, payload: dict[str, object]
    ) -> GraphExecutionOutcome: ...


class GraphRunStore(Protocol):
    async def lease_next(
        self, *, owner: str, now: datetime, lease_seconds: int
    ) -> LeasedGraphRunRequest | None: ...

    async def mark_running(
        self, *, request: LeasedGraphRunRequest, owner: str, now: datetime
    ) -> bool: ...

    async def mark_completed(
        self,
        *,
        request: LeasedGraphRunRequest,
        owner: str,
        now: datetime,
        outcome: GraphExecutionOutcome,
    ) -> None: ...

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
    ) -> DispatchRequestStatus: ...


class GraphRunDispatcher:
    def __init__(
        self,
        *,
        repository: GraphRunStore,
        executor: GraphExecutor,
        settings: Settings,
        owner: str | None = None,
        rng: Random | None = None,
    ) -> None:
        self._repository = repository
        self._executor = executor
        self._settings = settings
        self._owner = owner or f"{socket.gethostname()}-{uuid4()}"
        self._rng = rng or SystemRandom()

    async def dispatch_one(self, now: datetime | None = None) -> bool:
        attempt_time = now or datetime.now(UTC)
        request = await self._repository.lease_next(
            owner=self._owner,
            now=attempt_time,
            lease_seconds=self._settings.graph_request_lease_seconds,
        )
        if request is None:
            return False
        try:
            should_execute = await self._repository.mark_running(
                request=request,
                owner=self._owner,
                now=attempt_time,
            )
            if not should_execute:
                return True
            outcome = await self._executor.execute(request.run)
        except Exception as exc:
            error_code, safe_message = sanitize_error(exc)
            retryable = is_retryable_dispatch_error(exc)
            delay = calculate_backoff_seconds(
                attempt=request.attempt_count,
                base=self._settings.graph_request_backoff_base_seconds,
                cap=self._settings.graph_request_backoff_max_seconds,
                rng=self._rng,
            )
            status = await self._repository.mark_failed(
                request=request,
                owner=self._owner,
                now=attempt_time,
                error_code=error_code,
                safe_message=safe_message,
                delay_seconds=delay,
                retryable=retryable,
            )
            logger.warning(
                "graph_run_dispatch_failed",
                graph_run_id=str(request.run.graph_run_id),
                error_code=error_code,
                request_status=status,
            )
            return True
        await self._repository.mark_completed(
            request=request,
            owner=self._owner,
            now=datetime.now(UTC),
            outcome=outcome,
        )
        logger.info(
            "graph_run_dispatch_completed",
            graph_run_id=str(request.run.graph_run_id),
            graph_status=outcome.status,
            replayed=outcome.replayed,
        )
        return True


class GraphResumeStore(Protocol):
    async def lease_next(
        self, *, owner: str, now: datetime, lease_seconds: int
    ) -> LeasedGraphResumeRequest | None: ...

    async def mark_running(
        self, *, request: LeasedGraphResumeRequest, owner: str, now: datetime
    ) -> bool: ...

    async def mark_completed(
        self,
        *,
        request: LeasedGraphResumeRequest,
        owner: str,
        now: datetime,
        outcome: GraphExecutionOutcome,
    ) -> None: ...

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
    ) -> DispatchRequestStatus: ...


class GraphResumeDispatcher:
    def __init__(
        self,
        *,
        repository: GraphResumeStore,
        executor: ResumeExecutor,
        settings: Settings,
        owner: str | None = None,
        rng: Random | None = None,
    ) -> None:
        self._repository = repository
        self._executor = executor
        self._settings = settings
        self._owner = owner or f"{socket.gethostname()}-{uuid4()}"
        self._rng = rng or SystemRandom()

    async def dispatch_one(self, now: datetime | None = None) -> bool:
        attempt_time = now or datetime.now(UTC)
        request = await self._repository.lease_next(
            owner=self._owner,
            now=attempt_time,
            lease_seconds=self._settings.graph_request_lease_seconds,
        )
        if request is None:
            return False
        try:
            if not await self._repository.mark_running(
                request=request, owner=self._owner, now=attempt_time
            ):
                return True
            outcome = await self._executor.resume(request.run, request.payload)
        except Exception as exc:
            error_code, safe_message = sanitize_error(exc)
            retryable = is_retryable_dispatch_error(exc)
            delay = calculate_backoff_seconds(
                attempt=request.attempt_count,
                base=self._settings.graph_request_backoff_base_seconds,
                cap=self._settings.graph_request_backoff_max_seconds,
                rng=self._rng,
            )
            await self._repository.mark_failed(
                request=request,
                owner=self._owner,
                now=attempt_time,
                error_code=error_code,
                safe_message=safe_message,
                delay_seconds=delay,
                retryable=retryable,
            )
            logger.warning(
                "graph_resume_dispatch_failed",
                graph_run_id=str(request.run.graph_run_id),
                error_code=error_code,
            )
            return True
        await self._repository.mark_completed(
            request=request,
            owner=self._owner,
            now=datetime.now(UTC),
            outcome=outcome,
        )
        logger.info(
            "graph_resume_dispatch_completed",
            graph_run_id=str(request.run.graph_run_id),
            graph_status=outcome.status,
        )
        return True


async def run_forever(dispatcher: GraphRunDispatcher, poll_seconds: float) -> None:
    while True:
        processed = await dispatcher.dispatch_one()
        if not processed:
            await asyncio.sleep(poll_seconds)


async def run_dispatchers_forever(
    graph_runs: GraphRunDispatcher,
    graph_resumes: GraphResumeDispatcher,
    poll_seconds: float,
) -> None:
    while True:
        run_processed = await graph_runs.dispatch_one()
        resume_processed = await graph_resumes.dispatch_one()
        if not run_processed and not resume_processed:
            await asyncio.sleep(poll_seconds)


def calculate_backoff_seconds(*, attempt: int, base: int, cap: int, rng: Random) -> float:
    exponential = min(float(cap), float(base * (2 ** max(0, attempt - 1))))
    jittered = exponential * rng.uniform(0.75, 1.25)
    return min(float(cap), max(0.0, jittered))


def sanitize_error(exc: Exception) -> tuple[str, str]:
    code = _SAFE_ERROR_CODE.sub("_", type(exc).__name__)[:100] or "GraphExecutionError"
    return code, f"Graph execution failed with {code}."


def is_retryable_dispatch_error(exc: Exception) -> bool:
    return isinstance(exc, (ConnectionError, TimeoutError, OperationalError, OSError))
