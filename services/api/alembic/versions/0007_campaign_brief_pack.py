"""Add campaign-brief reference data and duplicate keys; widen page labels and queues.

Revision ID: 0007_campaign_brief_pack
Revises: 0006_claim_reroutes
Create Date: 2026-11-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from intake_api.domain.enums import AuditEventType, PageSegmentLabel, ReviewQueue
from intake_api.domain.models import Base

revision: str = "0007_campaign_brief_pack"
down_revision: str | None = "0006_claim_reroutes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("statements_of_work", "sow_brands", "asset_lead_times", "brief_intake_keys")
ADDED_AUDIT_EVENTS = frozenset({AuditEventType.BRIEF_REFERENCE_DATA_LOADED})
CLAIM_LABELS = ("LOSS_NOTICE", "REPAIR_ESTIMATE", "POLICE_REPORT", "CORRESPONDENCE", "OTHER")
CLAIM_QUEUES = (
    "FAST_TRACK",
    "DESK_ADJUSTER",
    "COVERAGE_REVIEW",
    "SIU_REFERRAL",
    "BODILY_INJURY_ADJUSTER",
    "TOTAL_LOSS_REVIEW",
    "BELOW_DEDUCTIBLE_NOTICE",
    "DUPLICATE_REVIEW",
)
QUEUE_COLUMNS = (
    ("claim_routing_decisions", "review_queue", "claim_review_queue"),
    ("claim_reroutes", "from_queue", "claim_reroute_from_queue"),
    ("claim_reroutes", "to_queue", "claim_reroute_to_queue"),
)


def _replace_check(table: str, drop: Sequence[str], name: str, condition: str) -> None:
    # Earlier migrations build tables from current ORM metadata, so on a fresh database the
    # constraints may already carry their new name and values; drop whichever exists.
    for constraint in drop:
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {constraint}")
    op.create_check_constraint(name, table, condition)


def _in(column: str, values: Sequence[str]) -> str:
    return f"{column} IN ({', '.join(repr(value) for value in values)})"


def upgrade() -> None:
    bind = op.get_bind()
    for table_name in TABLES:
        Base.metadata.tables[table_name].create(bind=bind, checkfirst=False)
    _replace_check(
        "document_page_segments",
        ("claim_segment_label", "page_segment_label"),
        "page_segment_label",
        _in("segment_label", [label.value for label in PageSegmentLabel]),
    )
    for table, column, constraint in QUEUE_COLUMNS:
        _replace_check(table, (constraint,), constraint, _in(column, [q.value for q in ReviewQueue]))
    op.drop_constraint("audit_event_type", "audit_events", type_="check")
    op.create_check_constraint(
        "audit_event_type", "audit_events", _in("event_type", [e.value for e in AuditEventType])
    )


def downgrade() -> None:
    bind = op.get_bind()
    added = ", ".join(f"'{member.value}'" for member in ADDED_AUDIT_EVENTS)
    if bind.scalar(sa.text(f"SELECT count(*) FROM audit_events WHERE event_type IN ({added})")):
        raise RuntimeError(
            "Cannot downgrade 0007: brief audit events exist; restore from backup instead"
        )
    if bind.scalar(
        sa.text(
            "SELECT count(*) FROM document_page_segments WHERE segment_label IN "
            "('BRIEF', 'MEDIA_PLAN')"
        )
    ):
        raise RuntimeError("Cannot downgrade 0007: brief page segments exist")
    op.drop_constraint("audit_event_type", "audit_events", type_="check")
    op.create_check_constraint(
        "audit_event_type",
        "audit_events",
        _in(
            "event_type",
            [
                e.value
                for e in AuditEventType
                if e not in ADDED_AUDIT_EVENTS and e != AuditEventType.CLIENT_QUESTIONS_SENT
            ],
        ),
    )
    for table, column, constraint in QUEUE_COLUMNS:
        _replace_check(table, (constraint,), constraint, _in(column, CLAIM_QUEUES))
    _replace_check(
        "document_page_segments",
        ("page_segment_label", "claim_segment_label"),
        "claim_segment_label",
        _in("segment_label", CLAIM_LABELS),
    )
    for table_name in reversed(TABLES):
        Base.metadata.tables[table_name].drop(bind=bind, checkfirst=False)
