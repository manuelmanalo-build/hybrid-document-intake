from datetime import UTC, datetime
from random import Random
from uuid import uuid4

import httpx
import pytest

from intake_api.infrastructure.config import Settings
from intake_integration_worker.worker import (
    DeliveryOutcome,
    HttpxZapierSender,
    IntegrationWorker,
    LeasedOutboxEvent,
    _lease_attempt_number,
    calculate_backoff,
)


def event() -> LeasedOutboxEvent:
    return LeasedOutboxEvent(
        id=uuid4(),
        tenant_id=uuid4(),
        document_id=uuid4(),
        graph_run_id=uuid4(),
        idempotency_key="approved-record:test:DOCUMENT_APPROVED",
        payload={"event_type": "DOCUMENT_APPROVED", "document_id": str(uuid4())},
        attempt_number=1,
        max_attempts=3,
    )


@pytest.mark.asyncio
async def test_sender_authenticates_signs_and_sanitizes_success() -> None:
    captured: httpx.Request | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured
        captured = request
        return httpx.Response(200, content=b"accepted", headers={"content-type": "text/plain"})

    sender = HttpxZapierSender(
        Settings(
            zapier_hook_url="https://hooks.example.invalid/catch",
            zapier_auth_token="server-auth",
            zapier_signing_secret="server-signing",
        ),
        httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    outcome = await sender.send(event())

    assert outcome.success is True
    assert outcome.response_summary == {"content_type": "text/plain", "content_length": 8}
    assert captured is not None
    assert captured.headers["Authorization"] == "Bearer server-auth"
    assert captured.headers["X-Webhook-Signature"].startswith("sha256=")
    assert captured.headers["Idempotency-Key"].startswith("approved-record:")


@pytest.mark.asyncio
async def test_timeout_is_retryable_without_leaking_exception() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("secret upstream detail", request=request)

    sender = HttpxZapierSender(
        Settings(zapier_hook_url="https://hooks.example.invalid/catch"),
        httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    outcome = await sender.send(event())

    assert outcome.success is False
    assert outcome.retryable is True
    assert outcome.error_code == "WEBHOOK_TIMEOUT"
    assert "secret" not in str(outcome.response_summary)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "retryable"), [(400, False), (408, True), (429, True), (503, True)]
)
async def test_http_retry_classification(status: int, retryable: bool) -> None:
    sender = HttpxZapierSender(
        Settings(zapier_hook_url="https://hooks.example.invalid/catch"),
        httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(status, content=b"ignored"))
        ),
    )

    outcome = await sender.send(event())

    assert outcome.retryable is retryable


class FakeRepository:
    def __init__(self, leased: LeasedOutboxEvent) -> None:
        self.leased = leased
        self.outcomes: list[DeliveryOutcome] = []

    async def lease_next(
        self, *, owner: str, now: datetime, lease_seconds: int
    ) -> LeasedOutboxEvent | None:
        return self.leased

    async def record_outcome(
        self,
        *,
        event: LeasedOutboxEvent,
        owner: str,
        outcome: DeliveryOutcome,
        now: datetime,
        retry_delay_seconds: float,
    ) -> str:
        self.outcomes.append(outcome)
        exhausted = event.attempt_number >= event.max_attempts or not outcome.retryable
        return "DEAD_LETTER" if exhausted else "RETRY_SCHEDULED"


class FixedSender:
    def __init__(self, outcome: DeliveryOutcome) -> None:
        self.outcome = outcome

    async def send(self, leased: LeasedOutboxEvent) -> DeliveryOutcome:
        return self.outcome


@pytest.mark.asyncio
async def test_worker_records_permanent_failure_for_dead_letter_transition() -> None:
    repository = FakeRepository(event())
    outcome = DeliveryOutcome(False, False, 400, 10, "HTTP_400", {})
    worker = IntegrationWorker(
        repository=repository,
        sender=FixedSender(outcome),
        settings=Settings(),
        owner="unit-integration-worker",
        rng=Random(7),  # noqa: S311
    )

    assert await worker.deliver_one(datetime(2026, 7, 14, tzinfo=UTC)) is True
    assert repository.outcomes == [outcome]


@pytest.mark.asyncio
async def test_retryable_failure_dead_letters_when_attempt_budget_is_exhausted() -> None:
    exhausted_event = event()
    exhausted_event = LeasedOutboxEvent(
        id=exhausted_event.id,
        tenant_id=exhausted_event.tenant_id,
        document_id=exhausted_event.document_id,
        graph_run_id=exhausted_event.graph_run_id,
        idempotency_key=exhausted_event.idempotency_key,
        payload=exhausted_event.payload,
        attempt_number=3,
        max_attempts=3,
    )
    repository = FakeRepository(exhausted_event)
    worker = IntegrationWorker(
        repository=repository,
        sender=FixedSender(DeliveryOutcome(False, True, 500, 10, "HTTP_500", {})),
        settings=Settings(),
        owner="unit-integration-worker",
        rng=Random(7),  # noqa: S311
    )

    assert await worker.deliver_one(datetime(2026, 7, 14, tzinfo=UTC)) is True
    assert repository.outcomes[0].retryable is True


def test_retry_backoff_is_exponential_jittered_and_capped() -> None:
    rng = Random(7)  # noqa: S311
    assert 1.5 <= calculate_backoff(1, 2, 10, rng) <= 2.5
    assert 7.5 <= calculate_backoff(9, 2, 10, rng) <= 10


def test_expired_webhook_lease_reuses_the_same_delivery_attempt() -> None:
    assert _lease_attempt_number(3, recovering_expired_lease=True) == 3
    assert _lease_attempt_number(2, recovering_expired_lease=False) == 3
