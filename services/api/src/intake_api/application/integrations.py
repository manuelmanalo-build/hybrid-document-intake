from __future__ import annotations

import secrets
from datetime import UTC, datetime
from typing import Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from intake_api.application.identity import RequestIdentity
from intake_api.application.problems import (
    AuthenticationRequired,
    AuthenticationUnavailable,
    AuthorizationDenied,
    ResourceNotFound,
    ReviewConflict,
)
from intake_api.domain.enums import ActorType, AuditEventType, UserRole
from intake_api.domain.models import AuditEvent, DeadLetterEvent, OutboxEvent
from intake_api.infrastructure.config import Settings


class ReplayResult(BaseModel):
    outbox_event_id: UUID
    status: Literal["PENDING"]


class ZapierCallback(BaseModel):
    model_config = ConfigDict(extra="forbid")
    outbox_event_id: UUID
    idempotency_key: str = Field(min_length=1, max_length=128)
    callback_id: str = Field(min_length=1, max_length=128)
    status: Literal["COMPLETED", "FAILED"]


class CallbackResult(BaseModel):
    outbox_event_id: UUID
    callback_status: str
    replayed: bool


class IntegrationAdminService:
    def __init__(self, settings: Settings, sessions: sessionmaker[Session]) -> None:
        self._settings = settings
        self._sessions = sessions

    def replay(self, event_id: UUID, identity: RequestIdentity) -> ReplayResult:
        if identity.role != UserRole.OPERATIONS_ADMIN:
            raise AuthorizationDenied("Only operations administrators may replay deliveries.")
        with self._sessions() as session, session.begin():
            event = session.scalar(
                select(OutboxEvent)
                .where(OutboxEvent.id == event_id, OutboxEvent.tenant_id == identity.tenant_id)
                .with_for_update()
            )
            if event is None:
                raise ResourceNotFound("The outbox event was not found.")
            if event.status != "DEAD_LETTER":
                raise ReviewConflict("Only dead-letter events may be replayed.")
            event.status = "PENDING"
            event.max_attempts = event.attempt_count + self._settings.webhook_max_attempts
            event.next_attempt_at = datetime.now(UTC)
            event.lease_owner = None
            event.lease_expires_at = None
            event.last_error_code = None
            dead = session.scalar(
                select(DeadLetterEvent)
                .where(
                    DeadLetterEvent.tenant_id == identity.tenant_id,
                    DeadLetterEvent.outbox_event_id == event.id,
                )
                .with_for_update()
            )
            if dead:
                dead.replay_count += 1
                dead.last_replayed_at = datetime.now(UTC)
                dead.last_replayed_by_user_id = identity.user_id
            session.add(
                _audit(
                    event,
                    AuditEventType.WEBHOOK_REPLAY_REQUESTED,
                    ActorType.USER,
                    str(identity.user_id),
                    f"replay:{dead.replay_count if dead else 1}",
                )
            )
            return ReplayResult(outbox_event_id=event.id, status="PENDING")

    def callback(self, command: ZapierCallback, supplied_token: str | None) -> CallbackResult:
        configured = self._settings.zapier_callback_token
        if configured is None:
            raise AuthenticationUnavailable
        if supplied_token is None or not secrets.compare_digest(
            configured.get_secret_value(), supplied_token
        ):
            raise AuthenticationRequired
        with self._sessions() as session, session.begin():
            event = session.scalar(
                select(OutboxEvent)
                .where(
                    OutboxEvent.id == command.outbox_event_id,
                    OutboxEvent.idempotency_key == command.idempotency_key,
                )
                .with_for_update()
            )
            if event is None:
                raise ResourceNotFound("The callback event correlation was not found.")
            if event.callback_id is not None:
                if (
                    event.callback_id != command.callback_id
                    or event.callback_status != command.status
                ):
                    raise ReviewConflict("A different callback was already recorded.")
                return CallbackResult(
                    outbox_event_id=event.id,
                    callback_status=event.callback_status or command.status,
                    replayed=True,
                )
            event.callback_id = command.callback_id
            event.callback_status = command.status
            event.callback_received_at = datetime.now(UTC)
            session.add(
                _audit(
                    event,
                    AuditEventType.ZAPIER_CALLBACK_RECEIVED,
                    ActorType.SYSTEM,
                    "zapier-callback",
                    command.callback_id,
                )
            )
            return CallbackResult(
                outbox_event_id=event.id, callback_status=command.status, replayed=False
            )


def _audit(
    event: OutboxEvent,
    event_type: AuditEventType,
    actor_type: ActorType,
    actor_id: str,
    key: str,
) -> AuditEvent:
    return AuditEvent(
        id=uuid5(NAMESPACE_URL, f"document-intake:{event_type}:{event.id}:{key}"),
        tenant_id=event.tenant_id,
        document_id=event.document_id,
        graph_run_id=event.graph_run_id,
        event_type=event_type,
        actor_type=actor_type,
        actor_id=actor_id,
        correlation_id=str(event.graph_run_id),
        sanitized_metadata={"outbox_event_id": str(event.id)},
    )
