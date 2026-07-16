from typing import cast
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session, sessionmaker

from intake_api.application.identity import RequestIdentity
from intake_api.application.integrations import IntegrationAdminService, ZapierCallback
from intake_api.application.problems import (
    AuthenticationRequired,
    AuthenticationUnavailable,
    AuthorizationDenied,
)
from intake_api.domain.enums import UserRole
from intake_api.infrastructure.config import Settings


def identity(role: UserRole) -> RequestIdentity:
    return RequestIdentity(tenant_id=uuid4(), user_id=uuid4(), subject="unit-actor", role=role)


def unused_sessions() -> sessionmaker[Session]:
    return cast(sessionmaker[Session], object())


def callback() -> ZapierCallback:
    return ZapierCallback(
        outbox_event_id=uuid4(),
        idempotency_key="approved-record:test:DOCUMENT_APPROVED",
        callback_id="zap-run-1",
        status="COMPLETED",
    )


def test_replay_rejects_non_operations_role_before_database_access() -> None:
    service = IntegrationAdminService(Settings(), unused_sessions())

    with pytest.raises(AuthorizationDenied):
        service.replay(uuid4(), identity(UserRole.APPROVER))


def test_callback_rejects_when_optional_authentication_is_not_configured() -> None:
    service = IntegrationAdminService(Settings(zapier_callback_token=None), unused_sessions())

    with pytest.raises(AuthenticationUnavailable):
        service.callback(callback(), None)


def test_callback_rejects_invalid_token_before_database_access() -> None:
    service = IntegrationAdminService(
        Settings(zapier_callback_token="callback-secret"), unused_sessions()
    )

    with pytest.raises(AuthenticationRequired):
        service.callback(callback(), "wrong-token")
