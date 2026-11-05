from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from intake_api.api.dependencies import get_identity
from intake_api.application.identity import RequestIdentity
from intake_api.application.review_queues import (
    ClientQuestionsView,
    QueueRoute,
    RerouteCommand,
    ReviewQueueService,
    ReviewQueueView,
    SendClientQuestionsCommand,
    SentClientRequest,
    WorkbenchView,
)
from intake_api.domain.enums import ReviewQueue

router = APIRouter(prefix="/api/v1/review-queues", tags=["review-queues"])
QueueDocumentType = Literal["CLAIM_PACKET", "CAMPAIGN_BRIEF"]


def get_review_queue_service(request: Request) -> ReviewQueueService:
    return request.app.state.review_queue_service


@router.get("", response_model=ReviewQueueView)
def review_queue(
    identity: Annotated[RequestIdentity, Depends(get_identity)],
    service: Annotated[ReviewQueueService, Depends(get_review_queue_service)],
    document_type: Annotated[QueueDocumentType, Query()],
    queue: Annotated[ReviewQueue | None, Query()] = None,
) -> ReviewQueueView:
    return service.list_queue(identity, document_type=document_type, queue=queue)


@router.get("/tasks/{task_id}", response_model=WorkbenchView)
def workbench(
    task_id: UUID,
    identity: Annotated[RequestIdentity, Depends(get_identity)],
    service: Annotated[ReviewQueueService, Depends(get_review_queue_service)],
) -> WorkbenchView:
    return service.workbench(task_id, identity)


@router.post("/tasks/{task_id}/reroute", response_model=QueueRoute)
def reroute(
    task_id: UUID,
    command: RerouteCommand,
    identity: Annotated[RequestIdentity, Depends(get_identity)],
    service: Annotated[ReviewQueueService, Depends(get_review_queue_service)],
) -> QueueRoute:
    return service.reroute(task_id, command, identity)


@router.get("/tasks/{task_id}/client-questions", response_model=ClientQuestionsView)
def client_questions(
    task_id: UUID,
    identity: Annotated[RequestIdentity, Depends(get_identity)],
    service: Annotated[ReviewQueueService, Depends(get_review_queue_service)],
) -> ClientQuestionsView:
    return service.client_questions(task_id, identity)


@router.post("/tasks/{task_id}/client-questions/sent", response_model=SentClientRequest)
def record_client_questions(
    task_id: UUID,
    command: SendClientQuestionsCommand,
    identity: Annotated[RequestIdentity, Depends(get_identity)],
    service: Annotated[ReviewQueueService, Depends(get_review_queue_service)],
) -> SentClientRequest:
    return service.record_client_questions(task_id, command, identity)
