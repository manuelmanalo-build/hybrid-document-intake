"""Add bounded-agent and deterministic workflow persistence.

Revision ID: 0002_agent_workflow
Revises: 0001_deterministic_intake
Create Date: 2026-07-14
"""

from collections.abc import Sequence

from alembic import op

from intake_api.domain.enums import AuditEventType
from intake_api.domain.models import Base

revision: str = "0002_agent_workflow"
down_revision: str | None = "0001_deterministic_intake"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = (
    "prompt_versions",
    "agent_runs",
    "agent_tool_calls",
    "extraction_runs",
    "extraction_candidates",
    "extraction_field_evidence",
    "agent_recommendations",
    "suppliers",
    "purchase_orders",
    "rule_definitions",
    "validation_runs",
    "validation_results",
    "confidence_runs",
    "confidence_components",
    "review_tasks",
    "review_task_claims",
    "reviewed_document_versions",
    "field_corrections",
    "review_decisions",
    "webhook_outbox_events",
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
    original_values = (
        "UPLOAD_RECEIVED",
        "FILE_VALIDATION_PASSED",
        "FILE_VALIDATION_FAILED",
        "DOCUMENT_STORED",
        "GRAPH_RUN_REQUESTED",
        "GRAPH_RUN_STARTED",
        "GRAPH_RUN_COMPLETED",
        "GRAPH_RUN_FAILED",
    )
    values = ", ".join(f"'{value}'" for value in original_values)
    op.create_check_constraint("audit_event_type", "audit_events", f"event_type IN ({values})")
    bind = op.get_bind()
    for table_name in reversed(TABLES):
        Base.metadata.tables[table_name].drop(bind=bind, checkfirst=False)
