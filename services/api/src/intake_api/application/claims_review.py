"""Claims view of the generic review queues (ADR 0008), kept for the `/api/v1/claims` routes."""

from datetime import datetime
from uuid import UUID

from intake_api.application.document_types import display_for
from intake_api.application.identity import RequestIdentity
from intake_api.application.review_queues import (
    QueueRoute,
    RerouteCommand,
    ReviewQueueService,
    ReviewQueueView,
    WorkbenchView,
    service_level,
)
from intake_api.domain.enums import ReviewQueue
from intake_api.infrastructure.config import Settings

__all__ = [
    "ClaimsReviewService",
    "QueueRoute",
    "RerouteCommand",
    "ReviewQueueView",
    "WorkbenchView",
    "service_level",
    "target_minutes",
]


def target_minutes(settings: Settings, queue: ReviewQueue) -> int:
    return display_for("CLAIM_PACKET").target_hours(settings, queue) * 60


class ClaimsReviewService(ReviewQueueService):
    def list_queue(  # type: ignore[override]
        self,
        identity: RequestIdentity,
        *,
        queue: ReviewQueue | None = None,
        now: datetime | None = None,
        document_type: str = "CLAIM_PACKET",
    ) -> ReviewQueueView:
        return super().list_queue(identity, document_type=document_type, queue=queue, now=now)

    def workbench(
        self, task_id: UUID, identity: RequestIdentity, *, now: datetime | None = None
    ) -> WorkbenchView:
        return super().workbench(task_id, identity, now=now)
