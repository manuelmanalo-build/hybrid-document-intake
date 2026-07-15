"""Permit authoritative review when an agent has no extraction candidate.

Revision ID: 0004_release_readiness_hardening
Revises: 0003_zapier_outbox
Create Date: 2026-07-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_release_readiness_hardening"
down_revision: str | None = "0003_zapier_outbox"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "reviewed_document_versions",
        "extraction_candidate_id",
        existing_type=sa.Uuid(),
        nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "reviewed_document_versions",
        "extraction_candidate_id",
        existing_type=sa.Uuid(),
        nullable=False,
    )
