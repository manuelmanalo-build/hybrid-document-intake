"""Record clarifying questions reviewers send to clients for incomplete briefs.

Revision ID: 0008_brief_client_requests
Revises: 0007_campaign_brief_pack
Create Date: 2026-11-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from intake_api.domain.enums import AuditEventType
from intake_api.domain.models import Base

revision: str = "0008_brief_client_requests"
down_revision: str | None = "0007_campaign_brief_pack"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ADDED = AuditEventType.CLIENT_QUESTIONS_SENT


def _events(values: Sequence[str]) -> str:
    return f"event_type IN ({', '.join(repr(value) for value in values)})"


def upgrade() -> None:
    Base.metadata.tables["brief_client_requests"].create(bind=op.get_bind(), checkfirst=False)
    op.drop_constraint("audit_event_type", "audit_events", type_="check")
    op.create_check_constraint(
        "audit_event_type", "audit_events", _events([e.value for e in AuditEventType])
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.scalar(
        sa.text("SELECT count(*) FROM audit_events WHERE event_type = :event"),
        {"event": ADDED.value},
    ):
        raise RuntimeError("Cannot downgrade 0008: client-question audit events exist")
    op.drop_constraint("audit_event_type", "audit_events", type_="check")
    op.create_check_constraint(
        "audit_event_type",
        "audit_events",
        _events(
            [
                e.value
                for e in AuditEventType
                if e not in (ADDED, AuditEventType.CDP_REFERENCE_DATA_LOADED)
            ]
        ),
    )
    Base.metadata.tables["brief_client_requests"].drop(bind=bind, checkfirst=False)
