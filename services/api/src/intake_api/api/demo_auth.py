from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Cookie, Request, Response, status
from pydantic import BaseModel, ConfigDict

from intake_api.application.demo_sessions import DEMO_SESSION_COOKIE, DemoSessionStore
from intake_api.application.identity import configured_actor
from intake_api.domain.enums import UserRole

router = APIRouter(prefix="/api/v1/demo-auth", tags=["development authentication"])


class DemoSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: UserRole


class DemoSessionIdentity(BaseModel):
    tenant_id: str
    user_id: str
    role: UserRole
    subject: str


class DemoAuthOptions(BaseModel):
    roles: list[UserRole]
    session_ttl_seconds: int


@router.get("/options", response_model=DemoAuthOptions)
def options(request: Request) -> DemoAuthOptions:
    settings = request.app.state.settings
    return DemoAuthOptions(
        roles=list(UserRole),
        session_ttl_seconds=settings.demo_session_ttl_seconds,
    )


@router.post("/session", response_model=DemoSessionIdentity)
def create_session(
    payload: DemoSessionRequest,
    request: Request,
    response: Response,
) -> DemoSessionIdentity:
    settings = request.app.state.settings
    store: DemoSessionStore = request.app.state.demo_session_store
    prior_token = request.cookies.get(DEMO_SESSION_COOKIE)
    if prior_token:
        store.revoke(prior_token)
    token = store.create(payload.role)
    response.set_cookie(
        key=DEMO_SESSION_COOKIE,
        value=token,
        max_age=settings.demo_session_ttl_seconds,
        httponly=True,
        secure=False,
        samesite="lax",
        path="/api",
    )
    identity = configured_actor(settings, payload.role)
    return DemoSessionIdentity(
        tenant_id=str(identity.tenant_id),
        user_id=str(identity.user_id),
        role=identity.role,
        subject=identity.subject,
    )


@router.delete("/session", status_code=status.HTTP_204_NO_CONTENT)
def delete_session(
    request: Request,
    response: Response,
    session_token: Annotated[str | None, Cookie(alias=DEMO_SESSION_COOKIE)] = None,
) -> None:
    store: DemoSessionStore = request.app.state.demo_session_store
    if session_token:
        store.revoke(session_token)
    response.delete_cookie(
        key=DEMO_SESSION_COOKIE,
        httponly=True,
        secure=False,
        samesite="lax",
        path="/api",
    )
