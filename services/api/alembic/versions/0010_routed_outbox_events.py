"""Allow outbox events without an approved record (DOCUMENT_ROUTED queue alerts).

Revision ID: 0010_routed_outbox_events
Revises: 0009_cdp_onboarding_pack
Create Date: 2026-11-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_routed_outbox_events"
down_revision: str | None = "0009_cdp_onboarding_pack"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("outbox_events", "approved_business_record_id", nullable=True)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.scalar(
        sa.text("SELECT count(*) FROM outbox_events WHERE approved_business_record_id IS NULL")
    ):
        raise RuntimeError("Cannot downgrade 0010: routed outbox events exist")
    op.alter_column("outbox_events", "approved_business_record_id", nullable=False)
