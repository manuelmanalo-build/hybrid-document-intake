from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import socket
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from random import Random, SystemRandom
from typing import Protocol
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import httpx
import structlog
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from intake_api.domain.enums import ActorType, AuditEventType
from intake_api.domain.models import (
    AuditEvent,
    DeadLetterEvent,
    OutboxEvent,
    WebhookDelivery,
)
from intake_api.infrastructure.config import Settings

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class LeasedOutboxEvent:
    id: UUID
    tenant_id: UUID
    document_id: UUID
    graph_run_id: UUID
    idempotency_key: str
    payload: dict[str, object]
    attempt_number: int
    max_attempts: int


@dataclass(frozen=True, slots=True)
class DeliveryOutcome:
    success: bool
    retryable: bool
    http_status: int | None
    duration_ms: int
    error_code: str | None
    response_summary: dict[str, object]


class WebhookSender(Protocol):
    async def send(self, event: LeasedOutboxEvent) -> DeliveryOutcome: ...


class OutboxStore(Protocol):
    async def lease_next(
        self, *, owner: str, now: datetime, lease_seconds: int
    ) -> LeasedOutboxEvent | None: ...

    async def record_outcome(
        self,
        *,
        event: LeasedOutboxEvent,
        owner: str,
        outcome: DeliveryOutcome,
        now: datetime,
        retry_delay_seconds: float,
    ) -> str: ...


class HttpxZapierSender:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self._settings = settings
        self._client = client or httpx.AsyncClient(timeout=settings.webhook_timeout_seconds)
        self._owns_client = client is None

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def send(self, event: LeasedOutboxEvent) -> DeliveryOutcome:
        started = time.monotonic()
        if self._settings.zapier_hook_url is None:
            return DeliveryOutcome(
                success=False,
                retryable=False,
                http_status=None,
                duration_ms=0,
                error_code="ZAPIER_NOT_CONFIGURED",
                response_summary={},
            )
        body = json.dumps(event.payload, sort_keys=True, separators=(",", ":")).encode()
        headers = {
            "Content-Type": "application/json",
            "Idempotency-Key": event.idempotency_key,
            "X-Correlation-ID": str(event.graph_run_id),
        }
        if self._settings.zapier_auth_token is not None:
            headers["Authorization"] = (
                f"Bearer {self._settings.zapier_auth_token.get_secret_value()}"
            )
        if self._settings.zapier_signing_secret is not None:
            signature = hmac.new(
                self._settings.zapier_signing_secret.get_secret_value().encode(),
                body,
                hashlib.sha256,
            ).hexdigest()
            headers["X-Webhook-Signature"] = f"sha256={signature}"
        try:
            response = await self._client.post(
                self._settings.zapier_hook_url.get_secret_value(), content=body, headers=headers
            )
        except httpx.TimeoutException:
            return _failure("WEBHOOK_TIMEOUT", True, None, started)
        except httpx.TransportError:
            return _failure("WEBHOOK_TRANSPORT_ERROR", True, None, started)
        retryable = response.status_code in {408, 425, 429} or response.status_code >= 500
        success = 200 <= response.status_code < 300
        return DeliveryOutcome(
            success=success,
            retryable=retryable,
            http_status=response.status_code,
            duration_ms=_duration_ms(started),
            error_code=None if success else f"HTTP_{response.status_code}",
            response_summary={
                "content_type": response.headers.get("content-type", "")[:100],
                "content_length": len(response.content),
            },
        )


class OutboxRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def lease_next(
        self, *, owner: str, now: datetime, lease_seconds: int
    ) -> LeasedOutboxEvent | None:
        async with self._sessions() as session, session.begin():
            event = await session.scalar(
                select(OutboxEvent)
                .where(
                    or_(
                        and_(
                            OutboxEvent.status.in_(["PENDING", "RETRY_SCHEDULED"]),
                            OutboxEvent.next_attempt_at <= now,
                            OutboxEvent.attempt_count < OutboxEvent.max_attempts,
                        ),
                        and_(OutboxEvent.status == "LEASED", OutboxEvent.lease_expires_at <= now),
                    ),
                )
                .order_by(OutboxEvent.next_attempt_at, OutboxEvent.created_at)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if event is None:
                return None
            recovering_expired_lease = event.status == "LEASED"
            event.status = "LEASED"
            event.attempt_count = _lease_attempt_number(
                event.attempt_count, recovering_expired_lease=recovering_expired_lease
            )
            event.lease_owner = owner
            event.lease_expires_at = now + timedelta(seconds=lease_seconds)
            return LeasedOutboxEvent(
                id=event.id,
                tenant_id=event.tenant_id,
                document_id=event.document_id,
                graph_run_id=event.graph_run_id,
                idempotency_key=event.idempotency_key,
                payload=event.payload,
                attempt_number=event.attempt_count,
                max_attempts=event.max_attempts,
            )

    async def record_outcome(
        self,
        *,
        event: LeasedOutboxEvent,
        owner: str,
        outcome: DeliveryOutcome,
        now: datetime,
        retry_delay_seconds: float,
    ) -> str:
        async with self._sessions() as session, session.begin():
            record = await session.scalar(
                select(OutboxEvent)
                .where(
                    OutboxEvent.id == event.id,
                    OutboxEvent.tenant_id == event.tenant_id,
                    OutboxEvent.status == "LEASED",
                    OutboxEvent.lease_owner == owner,
                )
                .with_for_update()
            )
            if record is None:
                raise RuntimeError("Outbox lease was lost")
            delivery_id = _stable_id(f"delivery:{event.id}:{event.attempt_number}")
            delivery_exists = await session.scalar(
                select(WebhookDelivery.id).where(
                    WebhookDelivery.id == delivery_id,
                    WebhookDelivery.tenant_id == event.tenant_id,
                )
            )
            if delivery_exists is None:
                session.add(
                    WebhookDelivery(
                        id=delivery_id,
                        tenant_id=event.tenant_id,
                        outbox_event_id=event.id,
                        attempt_number=event.attempt_number,
                        status="DELIVERED" if outcome.success else "FAILED",
                        http_status=outcome.http_status,
                        duration_ms=outcome.duration_ms,
                        response_summary=outcome.response_summary,
                        error_code=outcome.error_code,
                    )
                )
                session.add(_audit(event, AuditEventType.WEBHOOK_ATTEMPTED, owner, {}))
            record.lease_owner = None
            record.lease_expires_at = None
            record.last_error_code = outcome.error_code
            if outcome.success:
                record.status = "DELIVERED"
                record.delivered_at = now
                session.add(_audit(event, AuditEventType.WEBHOOK_DELIVERED, owner, {}))
                return record.status
            exhausted = event.attempt_number >= event.max_attempts or not outcome.retryable
            if exhausted:
                record.status = "DEAD_LETTER"
                dead_id = _stable_id(f"dead-letter:{event.id}")
                dead_letter_exists = await session.scalar(
                    select(DeadLetterEvent.id).where(
                        DeadLetterEvent.id == dead_id,
                        DeadLetterEvent.tenant_id == event.tenant_id,
                    )
                )
                if dead_letter_exists is None:
                    session.add(
                        DeadLetterEvent(
                            id=dead_id,
                            tenant_id=event.tenant_id,
                            outbox_event_id=event.id,
                            reason_code=outcome.error_code or "DELIVERY_FAILED",
                            sanitized_error=(
                                "Webhook delivery exhausted without storing response body."
                            ),
                        )
                    )
                session.add(_audit(event, AuditEventType.WEBHOOK_DELIVERY_EXHAUSTED, owner, {}))
                return record.status
            record.status = "RETRY_SCHEDULED"
            record.next_attempt_at = now + timedelta(seconds=retry_delay_seconds)
            session.add(_audit(event, AuditEventType.WEBHOOK_RETRY_SCHEDULED, owner, {}))
            return record.status


class IntegrationWorker:
    def __init__(
        self,
        *,
        repository: OutboxStore,
        sender: WebhookSender,
        settings: Settings,
        owner: str | None = None,
        rng: Random | None = None,
    ) -> None:
        self._repository = repository
        self._sender = sender
        self._settings = settings
        self._owner = owner or f"{socket.gethostname()}-{uuid4()}"
        self._rng = rng or SystemRandom()

    async def deliver_one(self, now: datetime | None = None) -> bool:
        attempt_time = now or datetime.now(UTC)
        event = await self._repository.lease_next(
            owner=self._owner,
            now=attempt_time,
            lease_seconds=self._settings.webhook_lease_seconds,
        )
        if event is None:
            return False
        outcome = await self._sender.send(event)
        delay = calculate_backoff(
            event.attempt_number,
            self._settings.webhook_backoff_base_seconds,
            self._settings.webhook_backoff_max_seconds,
            self._rng,
        )
        status = await self._repository.record_outcome(
            event=event,
            owner=self._owner,
            outcome=outcome,
            now=datetime.now(UTC),
            retry_delay_seconds=delay,
        )
        logger.info("webhook_delivery_recorded", outbox_event_id=str(event.id), status=status)
        return True


async def run_forever(worker: IntegrationWorker, poll_seconds: float) -> None:
    while True:
        if not await worker.deliver_one():
            await asyncio.sleep(poll_seconds)


def calculate_backoff(attempt: int, base: int, cap: int, rng: Random) -> float:
    exponential = min(float(cap), float(base * (2 ** max(0, attempt - 1))))
    return min(float(cap), exponential * rng.uniform(0.75, 1.25))


def _lease_attempt_number(attempt_count: int, *, recovering_expired_lease: bool) -> int:
    return attempt_count if recovering_expired_lease else attempt_count + 1


def _failure(code: str, retryable: bool, status: int | None, started: float) -> DeliveryOutcome:
    return DeliveryOutcome(
        success=False,
        retryable=retryable,
        http_status=status,
        duration_ms=_duration_ms(started),
        error_code=code,
        response_summary={},
    )


def _duration_ms(started: float) -> int:
    return max(0, int((time.monotonic() - started) * 1000))


def _stable_id(material: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"document-intake:{material}")


def _audit(
    event: LeasedOutboxEvent,
    event_type: AuditEventType,
    actor_id: str,
    metadata: dict[str, object],
) -> AuditEvent:
    return AuditEvent(
        id=_stable_id(f"audit:{event_type}:{event.id}:{event.attempt_number}"),
        tenant_id=event.tenant_id,
        document_id=event.document_id,
        graph_run_id=event.graph_run_id,
        event_type=event_type,
        actor_type=ActorType.WORKER,
        actor_id=actor_id,
        correlation_id=str(event.graph_run_id),
        sanitized_metadata={"outbox_event_id": str(event.id), **metadata},
    )
