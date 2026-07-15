from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends

from intake_api.api.dependencies import get_identity, get_review_service
from intake_api.application.identity import RequestIdentity
from intake_api.application.review import (
    CompleteReviewCommand,
    ReviewCompletionResult,
    ReviewService,
    ReviewTaskView,
)

router = APIRouter(prefix="/api/v1/review-tasks", tags=["review-tasks"])


@router.get("", response_model=list[ReviewTaskView])
def list_review_tasks(
    identity: Annotated[RequestIdentity, Depends(get_identity)],
    service: Annotated[ReviewService, Depends(get_review_service)],
) -> list[ReviewTaskView]:
    return service.list_tasks(identity)


@router.get("/{task_id}", response_model=ReviewTaskView)
def get_review_task(
    task_id: UUID,
    identity: Annotated[RequestIdentity, Depends(get_identity)],
    service: Annotated[ReviewService, Depends(get_review_service)],
) -> ReviewTaskView:
    return service.get_task(task_id, identity)


@router.post("/{task_id}/claim", response_model=ReviewTaskView)
def claim_review_task(
    task_id: UUID,
    identity: Annotated[RequestIdentity, Depends(get_identity)],
    service: Annotated[ReviewService, Depends(get_review_service)],
) -> ReviewTaskView:
    return service.claim(task_id, identity)


@router.post("/{task_id}/unclaim", response_model=ReviewTaskView)
def unclaim_review_task(
    task_id: UUID,
    identity: Annotated[RequestIdentity, Depends(get_identity)],
    service: Annotated[ReviewService, Depends(get_review_service)],
) -> ReviewTaskView:
    return service.unclaim(task_id, identity)


@router.post("/{task_id}/complete", response_model=ReviewCompletionResult)
def complete_review_task(
    task_id: UUID,
    command: CompleteReviewCommand,
    identity: Annotated[RequestIdentity, Depends(get_identity)],
    service: Annotated[ReviewService, Depends(get_review_service)],
) -> ReviewCompletionResult:
    return service.complete(task_id, command, identity)
