"""Add audited reviewer re-routes for claims.

Revision ID: 0006_claim_reroutes
Revises: 0005_claims_reference_routing
Create Date: 2026-10-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from intake_api.domain.enums import AuditEventType
from intake_api.domain.models import Base

revision: str = "0006_claim_reroutes"
down_revision: str | None = "0005_claims_reference_routing"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ADDED_AUDIT_EVENTS = frozenset({AuditEventType.CLAIM_REROUTED})


def upgrade() -> None:
    Base.metadata.tables["claim_reroutes"].create(bind=op.get_bind(), checkfirst=False)
    op.drop_constraint("audit_event_type", "audit_events", type_="check")
    values = ", ".join(f"'{member.value}'" for member in AuditEventType)
    op.create_check_constraint("audit_event_type", "audit_events", f"event_type IN ({values})")


def downgrade() -> None:
    # Audit is append-only: refuse rather than delete re-route audit history.
    added = ", ".join(f"'{member.value}'" for member in ADDED_AUDIT_EVENTS)
    existing = op.get_bind().scalar(
        sa.text(f"SELECT count(*) FROM audit_events WHERE event_type IN ({added})")
    )
    if existing:
        raise RuntimeError(
            "Cannot downgrade 0006: re-route audit events exist; restore from backup instead"
        )
    op.drop_constraint("audit_event_type", "audit_events", type_="check")
    later = {AuditEventType.BRIEF_REFERENCE_DATA_LOADED}
    previous = [
        member for member in AuditEventType if member not in ADDED_AUDIT_EVENTS | later
    ]
    values = ", ".join(f"'{member.value}'" for member in previous)
    op.create_check_constraint("audit_event_type", "audit_events", f"event_type IN ({values})")
    Base.metadata.tables["claim_reroutes"].drop(bind=op.get_bind(), checkfirst=False)
