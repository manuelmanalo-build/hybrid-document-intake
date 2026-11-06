"""Add CDP-onboarding reference data, PII scan findings, and duplicate keys; widen labels and queues.

Revision ID: 0009_cdp_onboarding_pack
Revises: 0008_brief_client_requests
Create Date: 2026-11-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from intake_api.domain.enums import AuditEventType, PageSegmentLabel, ReviewQueue
from intake_api.domain.models import Base

revision: str = "0009_cdp_onboarding_pack"
down_revision: str | None = "0008_brief_client_requests"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = (
    "data_agreements",
    "canonical_attributes",
    "cdp_connectors",
    "pii_scan_findings",
    "onboarding_intake_keys",
)
ADDED = AuditEventType.CDP_REFERENCE_DATA_LOADED
NEW_LABELS = ("ONBOARDING_FORM", "DATA_DICTIONARY", "SAMPLE_EXTRACT", "PRIVACY_ADDENDUM")
NEW_QUEUES = (
    "READY_TO_BUILD",
    "AGREEMENT_REVIEW",
    "PRIVACY_REVIEW",
    "MAPPING_REVIEW",
    "ONBOARDING_REVIEW",
)
QUEUE_COLUMNS = (
    ("claim_routing_decisions", "review_queue", "claim_review_queue"),
    ("claim_reroutes", "from_queue", "claim_reroute_from_queue"),
    ("claim_reroutes", "to_queue", "claim_reroute_to_queue"),
)


def _in(column: str, values: Sequence[str]) -> str:
    return f"{column} IN ({', '.join(repr(value) for value in values)})"


def _replace(table: str, name: str, condition: str) -> None:
    op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {name}")
    op.create_check_constraint(name, table, condition)


def upgrade() -> None:
    bind = op.get_bind()
    for table_name in TABLES:
        Base.metadata.tables[table_name].create(bind=bind, checkfirst=False)
    _replace(
        "document_page_segments",
        "page_segment_label",
        _in("segment_label", [label.value for label in PageSegmentLabel]),
    )
    for table, column, constraint in QUEUE_COLUMNS:
        _replace(table, constraint, _in(column, [queue.value for queue in ReviewQueue]))
    _replace("audit_events", "audit_event_type", _in("event_type", [e.value for e in AuditEventType]))


def downgrade() -> None:
    bind = op.get_bind()
    if bind.scalar(
        sa.text("SELECT count(*) FROM audit_events WHERE event_type = :event"),
        {"event": ADDED.value},
    ):
        raise RuntimeError("Cannot downgrade 0009: CDP audit events exist; restore from backup")
    _replace(
        "audit_events",
        "audit_event_type",
        _in("event_type", [e.value for e in AuditEventType if e != ADDED]),
    )
    for table, column, constraint in QUEUE_COLUMNS:
        _replace(
            table,
            constraint,
            _in(column, [queue.value for queue in ReviewQueue if queue.value not in NEW_QUEUES]),
        )
    _replace(
        "document_page_segments",
        "page_segment_label",
        _in(
            "segment_label",
            [label.value for label in PageSegmentLabel if label.value not in NEW_LABELS],
        ),
    )
    for table_name in reversed(TABLES):
        Base.metadata.tables[table_name].drop(bind=bind, checkfirst=False)
