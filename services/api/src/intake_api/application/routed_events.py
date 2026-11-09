"""Build `DOCUMENT_ROUTED` outbox events for queue alerts (workflow and review API)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, UUID, uuid5

from intake_api.application.document_types import REVIEW_DISPLAYS
from intake_api.application.integration_payloads import routed_event
from intake_api.domain.enums import ReviewQueue
from intake_api.domain.models import OutboxEvent
from intake_api.infrastructure.config import Settings


def routed_event_key(review_task_id: UUID, cycle: int, suffix: str = "rules") -> str:
    return f"routed:{review_task_id}:{cycle}:{suffix}"


def build_routed_outbox_event(
    *,
    settings: Settings,
    tenant_id: UUID,
    document_id: UUID,
    graph_run_id: UUID,
    document_type: str,
    review_task_id: UUID,
    review_cycle: int,
    queue: str,
    routing_version: str | None,
    routed_by: str,
    reason_code: str,
    flags: Sequence[str],
    business_data: Mapping[str, object],
    suffix: str = "rules",
) -> OutboxEvent | None:
    display = REVIEW_DISPLAYS.get(document_type)
    if display is None:
        return None
    key = routed_event_key(review_task_id, review_cycle, suffix)
    now = datetime.now(UTC)
    return OutboxEvent(
        id=uuid5(NAMESPACE_URL, f"document-intake:routed-outbox:{key}"),
        tenant_id=tenant_id,
        document_id=document_id,
        graph_run_id=graph_run_id,
        approved_business_record_id=None,
        event_type="DOCUMENT_ROUTED",
        idempotency_key=key,
        payload=routed_event(
            event_key=key,
            document_type=document_type,
            document_id=str(document_id),
            review_task_id=str(review_task_id),
            review_cycle=review_cycle,
            queue=queue,
            routing_version=routing_version,
            routed_by=routed_by,
            reason_code=reason_code,
            flags=flags,
            summary=display.summary(business_data).model_dump(),
            target_hours=display.target_hours(settings, ReviewQueue(queue)),
            routed_at=now,
        ),
        status="PENDING",
        attempt_count=0,
        max_attempts=settings.webhook_max_attempts,
        next_attempt_at=now,
    )
