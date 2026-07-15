"""Add authoritative approval and Zapier delivery ledger.

Revision ID: 0003_zapier_outbox
Revises: 0002_agent_workflow
Create Date: 2026-07-14
"""

from collections.abc import Sequence

from alembic import op

from intake_api.domain.enums import AuditEventType
from intake_api.domain.models import Base

revision: str = "0003_zapier_outbox"
down_revision: str | None = "0002_agent_workflow"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = (
    "approved_business_records",
    "outbox_events",
    "webhook_deliveries",
    "dead_letter_events",
)


def upgrade() -> None:
    bind = op.get_bind()
    for table_name in TABLES:
        Base.metadata.tables[table_name].create(bind=bind, checkfirst=False)
    op.drop_constraint("audit_event_type", "audit_events", type_="check")
    values = ", ".join(f"'{member.value}'" for member in AuditEventType)
    op.create_check_constraint("audit_event_type", "audit_events", f"event_type IN ({values})")


def downgrade() -> None:
    op.drop_constraint("audit_event_type", "audit_events", type_="check")
    added = {
        AuditEventType.WEBHOOK_ATTEMPTED,
        AuditEventType.WEBHOOK_DELIVERED,
        AuditEventType.WEBHOOK_RETRY_SCHEDULED,
        AuditEventType.WEBHOOK_DELIVERY_EXHAUSTED,
        AuditEventType.WEBHOOK_REPLAY_REQUESTED,
        AuditEventType.ZAPIER_CALLBACK_RECEIVED,
    }
    previous = [member for member in AuditEventType if member not in added]
    values = ", ".join(f"'{member.value}'" for member in previous)
    op.create_check_constraint("audit_event_type", "audit_events", f"event_type IN ({values})")
    bind = op.get_bind()
    for table_name in reversed(TABLES):
        Base.metadata.tables[table_name].drop(bind=bind, checkfirst=False)
