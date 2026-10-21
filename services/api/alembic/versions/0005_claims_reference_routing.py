"""Add claims-slice reference data, page segments, duplicate keys, and routing decisions.

Revision ID: 0005_claims_reference_routing
Revises: 0004_release_readiness_hardening
Create Date: 2026-10-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from intake_api.domain.enums import AuditEventType
from intake_api.domain.models import Base

revision: str = "0005_claims_reference_routing"
down_revision: str | None = "0004_release_readiness_hardening"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = (
    "policies",
    "policy_vehicles",
    "document_page_segments",
    "claim_intake_keys",
    "claim_routing_decisions",
)
ADDED_AUDIT_EVENTS = frozenset({AuditEventType.CLAIMS_REFERENCE_DATA_LOADED})


def upgrade() -> None:
    bind = op.get_bind()
    for table_name in TABLES:
        Base.metadata.tables[table_name].create(bind=bind, checkfirst=False)
    op.drop_constraint("audit_event_type", "audit_events", type_="check")
    values = ", ".join(f"'{member.value}'" for member in AuditEventType)
    op.create_check_constraint("audit_event_type", "audit_events", f"event_type IN ({values})")


def downgrade() -> None:
    # Audit is append-only, so a downgrade never deletes audit history. If claims audit events
    # exist, stop and require an explicit restore from backup instead.
    added = ", ".join(f"'{member.value}'" for member in ADDED_AUDIT_EVENTS)
    existing = op.get_bind().scalar(
        sa.text(f"SELECT count(*) FROM audit_events WHERE event_type IN ({added})")
    )
    if existing:
        raise RuntimeError(
            "Cannot downgrade 0005: claims audit events exist; restore from backup instead"
        )
    op.drop_constraint("audit_event_type", "audit_events", type_="check")
    previous = [member for member in AuditEventType if member not in ADDED_AUDIT_EVENTS]
    values = ", ".join(f"'{member.value}'" for member in previous)
    op.create_check_constraint("audit_event_type", "audit_events", f"event_type IN ({values})")
    bind = op.get_bind()
    for table_name in reversed(TABLES):
        Base.metadata.tables[table_name].drop(bind=bind, checkfirst=False)
