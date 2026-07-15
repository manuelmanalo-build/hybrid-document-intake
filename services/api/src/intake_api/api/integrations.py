from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header

from intake_api.api.dependencies import get_identity, get_integration_admin_service
from intake_api.application.identity import RequestIdentity
from intake_api.application.integrations import (
    CallbackResult,
    IntegrationAdminService,
    ReplayResult,
    ZapierCallback,
)

router = APIRouter(prefix="/api/v1/integrations", tags=["integrations"])


@router.post("/outbox/{event_id}/replay", response_model=ReplayResult)
def replay_outbox_event(
    event_id: UUID,
    identity: Annotated[RequestIdentity, Depends(get_identity)],
    service: Annotated[IntegrationAdminService, Depends(get_integration_admin_service)],
) -> ReplayResult:
    return service.replay(event_id, identity)


@router.post("/zapier/callback", response_model=CallbackResult)
def zapier_callback(
    command: ZapierCallback,
    service: Annotated[IntegrationAdminService, Depends(get_integration_admin_service)],
    callback_token: Annotated[str | None, Header(alias="X-Zapier-Callback-Token")] = None,
) -> CallbackResult:
    return service.callback(command, callback_token)
