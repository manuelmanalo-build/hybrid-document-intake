from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from intake_api.api.dependencies import get_identity
from intake_api.application.claims_review import (
    ClaimQueueView,
    ClaimRoute,
    ClaimsReviewService,
    ClaimWorkbenchView,
    RerouteCommand,
)
from intake_api.application.identity import RequestIdentity
from intake_api.domain.enums import ReviewQueue

router = APIRouter(prefix="/api/v1/claims", tags=["claims"])


def get_claims_review_service(request: Request) -> ClaimsReviewService:
    return request.app.state.claims_review_service


@router.get("/queue", response_model=ClaimQueueView)
def claim_queue(
    identity: Annotated[RequestIdentity, Depends(get_identity)],
    service: Annotated[ClaimsReviewService, Depends(get_claims_review_service)],
    queue: Annotated[ReviewQueue | None, Query()] = None,
) -> ClaimQueueView:
    return service.list_queue(identity, queue=queue)


@router.get("/{task_id}", response_model=ClaimWorkbenchView)
def claim_workbench(
    task_id: UUID,
    identity: Annotated[RequestIdentity, Depends(get_identity)],
    service: Annotated[ClaimsReviewService, Depends(get_claims_review_service)],
) -> ClaimWorkbenchView:
    return service.workbench(task_id, identity)


@router.post("/{task_id}/reroute", response_model=ClaimRoute)
def reroute_claim(
    task_id: UUID,
    command: RerouteCommand,
    identity: Annotated[RequestIdentity, Depends(get_identity)],
    service: Annotated[ClaimsReviewService, Depends(get_claims_review_service)],
) -> ClaimRoute:
    return service.reroute(task_id, command, identity)
