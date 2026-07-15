from dataclasses import dataclass
from uuid import NAMESPACE_URL, UUID, uuid5

from intake_api.domain.enums import UserRole
from intake_api.infrastructure.config import Settings


@dataclass(frozen=True, slots=True)
class RequestIdentity:
    tenant_id: UUID
    user_id: UUID
    subject: str
    role: UserRole


def configured_submitter(settings: Settings) -> RequestIdentity:
    return RequestIdentity(
        tenant_id=settings.bootstrap_tenant_id,
        user_id=settings.bootstrap_user_id,
        subject=settings.bootstrap_user_subject,
        role=UserRole.SUBMITTER,
    )


def configured_actor(settings: Settings, role: UserRole) -> RequestIdentity:
    if role == UserRole.SUBMITTER:
        return configured_submitter(settings)
    subject = f"local-demo-{role.value.casefold()}"
    return RequestIdentity(
        tenant_id=settings.bootstrap_tenant_id,
        user_id=uuid5(NAMESPACE_URL, f"document-intake:{settings.bootstrap_tenant_id}:{subject}"),
        subject=subject,
        role=role,
    )
