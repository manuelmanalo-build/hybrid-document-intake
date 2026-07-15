import structlog
from sqlalchemy import select

from intake_api.application.identity import configured_actor
from intake_api.domain.enums import UserRole
from intake_api.domain.models import Tenant, User
from intake_api.infrastructure.config import get_settings
from intake_api.infrastructure.database import SessionFactory
from intake_api.infrastructure.logging import configure_logging

logger = structlog.get_logger(__name__)


def bootstrap_local_identity() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    with SessionFactory() as session, session.begin():
        tenant = session.scalar(select(Tenant).where(Tenant.id == settings.bootstrap_tenant_id))
        if tenant is None:
            session.add(
                Tenant(id=settings.bootstrap_tenant_id, name=settings.bootstrap_tenant_name)
            )
        for role in UserRole:
            identity = configured_actor(settings, role)
            user = session.scalar(select(User).where(User.id == identity.user_id))
            if user is None:
                session.add(
                    User(
                        id=identity.user_id,
                        tenant_id=identity.tenant_id,
                        external_subject=identity.subject,
                        display_name=f"Local Demo {role.value.replace('_', ' ').title()}",
                        role=role,
                    )
                )
    logger.info(
        "local_identity_bootstrapped",
        tenant_id=str(settings.bootstrap_tenant_id),
        user_id=str(settings.bootstrap_user_id),
    )


if __name__ == "__main__":
    bootstrap_local_identity()
