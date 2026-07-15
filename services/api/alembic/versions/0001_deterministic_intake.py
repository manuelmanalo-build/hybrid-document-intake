"""Create deterministic document intake tables.

Revision ID: 0001_deterministic_intake
Revises:
Create Date: 2026-07-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_deterministic_intake"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tenants",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("external_subject", sa.String(200), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column(
            "role",
            sa.Enum(
                "SUBMITTER",
                "REVIEWER",
                "APPROVER",
                "OPERATIONS_ADMIN",
                "AUDITOR",
                name="user_role",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_users_tenant"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "external_subject", name="uq_users_tenant_subject"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_users_tenant_id_id"),
    )
    op.create_index("ix_users_tenant_id", "users", ["tenant_id"])

    op.create_table(
        "stored_objects",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("object_key", sa.String(512), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("media_type", sa.String(127), nullable=False),
        sa.Column("storage_backend", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("byte_size > 0", name="ck_stored_objects_positive_size"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_stored_objects_tenant"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("object_key", name="uq_stored_objects_object_key"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_stored_objects_tenant_id_id"),
    )
    op.create_index("ix_stored_objects_tenant_id", "stored_objects", ["tenant_id"])

    op.create_table(
        "documents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("current_version_id", sa.Uuid(), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "RECEIVED",
                name="document_status",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("duplicate_hash_detected", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "created_by_user_id"],
            ["users.tenant_id", "users.id"],
            name="fk_documents_created_by_user",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_documents_tenant"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_documents_tenant_id_id"),
    )
    op.create_index("ix_documents_tenant_id", "documents", ["tenant_id"])

    op.create_table(
        "document_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("stored_object_id", sa.Uuid(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("original_filename", sa.String(255), nullable=False),
        sa.Column("declared_media_type", sa.String(127), nullable=False),
        sa.Column("detected_media_type", sa.String(127), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("byte_size > 0", name="ck_document_versions_positive_size"),
        sa.CheckConstraint("version_number > 0", name="ck_document_versions_positive_version"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "document_id"],
            ["documents.tenant_id", "documents.id"],
            name="fk_document_versions_document",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "stored_object_id"],
            ["stored_objects.tenant_id", "stored_objects.id"],
            name="fk_document_versions_stored_object",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "document_id", "version_number", name="uq_document_versions_number"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_document_versions_tenant_id_id"),
    )
    op.create_index(
        "ix_document_versions_tenant_sha256", "document_versions", ["tenant_id", "sha256"]
    )
    op.create_foreign_key(
        "fk_documents_current_version",
        "documents",
        "document_versions",
        ["tenant_id", "current_version_id"],
        ["tenant_id", "id"],
    )

    op.create_table(
        "document_status_history",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "RECEIVED",
                name="document_status_history_status",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "actor_type",
            sa.Enum(
                "USER",
                "SYSTEM",
                "WORKER",
                name="document_status_actor_type",
                native_enum=False,
                create_constraint=True,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("actor_id", sa.String(200), nullable=False),
        sa.Column("reason_code", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "document_id"],
            ["documents.tenant_id", "documents.id"],
            name="fk_document_status_history_document",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_document_status_history_tenant_document_created",
        "document_status_history",
        ["tenant_id", "document_id", "created_at"],
    )

    op.create_table(
        "audit_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=True),
        sa.Column("graph_run_id", sa.Uuid(), nullable=True),
        sa.Column("agent_run_id", sa.Uuid(), nullable=True),
        sa.Column(
            "event_type",
            sa.Enum(
                "UPLOAD_RECEIVED",
                "FILE_VALIDATION_PASSED",
                "FILE_VALIDATION_FAILED",
                "DOCUMENT_STORED",
                "GRAPH_RUN_REQUESTED",
                "GRAPH_RUN_STARTED",
                "GRAPH_RUN_COMPLETED",
                "GRAPH_RUN_FAILED",
                name="audit_event_type",
                native_enum=False,
                create_constraint=True,
                length=64,
            ),
            nullable=False,
        ),
        sa.Column(
            "actor_type",
            sa.Enum(
                "USER",
                "SYSTEM",
                "WORKER",
                name="audit_actor_type",
                native_enum=False,
                create_constraint=True,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("actor_id", sa.String(200), nullable=False),
        sa.Column("correlation_id", sa.String(128), nullable=False),
        sa.Column("before_values", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("after_values", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "sanitized_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "document_id"],
            ["documents.tenant_id", "documents.id"],
            name="fk_audit_events_document",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_audit_events_tenant"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_audit_events_tenant_document_created",
        "audit_events",
        ["tenant_id", "document_id", "created_at"],
    )
    op.create_index(
        "ix_audit_events_correlation", "audit_events", ["tenant_id", "correlation_id"]
    )

    op.create_table(
        "idempotency_records",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("operation", sa.String(100), nullable=False),
        sa.Column("key_hash", sa.String(64), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "COMPLETED",
                name="idempotency_status",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("response_status", sa.Integer(), nullable=False),
        sa.Column("response_body", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("resource_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "response_status >= 200 AND response_status < 600", name="ck_idempotency_status"
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_idempotency_tenant"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "resource_id"],
            ["documents.tenant_id", "documents.id"],
            name="fk_idempotency_document",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id", "operation", "key_hash", name="uq_idempotency_tenant_operation_key"
        ),
    )

    op.create_table(
        "graph_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("graph_version", sa.String(64), nullable=False),
        sa.Column("thread_id", sa.String(128), nullable=False),
        sa.Column("correlation_id", sa.String(128), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "REQUESTED",
                "RUNNING",
                "INTERRUPTED",
                "COMPLETED",
                "FAILED",
                name="graph_run_status",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("last_error_code", sa.String(100), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "document_id"],
            ["documents.tenant_id", "documents.id"],
            name="fk_graph_runs_document",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "document_id", name="uq_graph_runs_document"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_graph_runs_tenant_id_id"),
        sa.UniqueConstraint("thread_id", name="uq_graph_run_thread_id"),
    )
    op.create_index("ix_graph_runs_status_created", "graph_runs", ["status", "created_at"])

    dispatch_status = (
        "PENDING",
        "LEASED",
        "RETRY_SCHEDULED",
        "COMPLETED",
        "EXHAUSTED",
    )
    op.create_table(
        "graph_run_requests",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("graph_run_id", sa.Uuid(), nullable=False),
        sa.Column("correlation_id", sa.String(128), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                *dispatch_status,
                name="graph_dispatch_request_status",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_owner", sa.String(128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(100), nullable=True),
        sa.Column("last_error_message", sa.Text(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("attempt_count >= 0", name="ck_graph_run_requests_attempt_count"),
        sa.CheckConstraint("max_attempts > 0", name="ck_graph_run_requests_max_attempts"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "document_id"],
            ["documents.tenant_id", "documents.id"],
            name="fk_graph_run_requests_document",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "graph_run_id"],
            ["graph_runs.tenant_id", "graph_runs.id"],
            name="fk_graph_run_requests_run",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "graph_run_id", name="uq_graph_run_requests_run"),
    )
    op.create_index(
        "ix_graph_run_requests_due", "graph_run_requests", ["status", "next_attempt_at"]
    )
    op.create_index(
        "ix_graph_run_requests_lease", "graph_run_requests", ["status", "lease_expires_at"]
    )

    op.create_table(
        "graph_resume_requests",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("graph_run_id", sa.Uuid(), nullable=False),
        sa.Column("correlation_id", sa.String(128), nullable=False),
        sa.Column("resume_key", sa.String(128), nullable=False),
        sa.Column("resume_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                *dispatch_status,
                name="graph_resume_request_status",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_owner", sa.String(128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(100), nullable=True),
        sa.Column("last_error_message", sa.Text(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("attempt_count >= 0", name="ck_graph_resume_requests_attempt_count"),
        sa.CheckConstraint("max_attempts > 0", name="ck_graph_resume_requests_max_attempts"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "document_id"],
            ["documents.tenant_id", "documents.id"],
            name="fk_graph_resume_requests_document",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "graph_run_id"],
            ["graph_runs.tenant_id", "graph_runs.id"],
            name="fk_graph_resume_requests_run",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id", "graph_run_id", "resume_key", name="uq_graph_resume_key"
        ),
    )
    op.create_index(
        "ix_graph_resume_requests_due", "graph_resume_requests", ["status", "next_attempt_at"]
    )
    op.create_index(
        "ix_graph_resume_requests_lease",
        "graph_resume_requests",
        ["status", "lease_expires_at"],
    )

    op.execute(
        """
        CREATE FUNCTION reject_append_only_change() RETURNS trigger AS $$
        BEGIN
          RAISE EXCEPTION '% is append-only', TG_TABLE_NAME USING ERRCODE = '55000';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    for table_name in (
        "stored_objects",
        "document_versions",
        "document_status_history",
        "audit_events",
    ):
        op.execute(
            f"CREATE TRIGGER {table_name}_append_only "
            f"BEFORE UPDATE OR DELETE ON {table_name} "
            "FOR EACH ROW EXECUTE FUNCTION reject_append_only_change()"
        )


def downgrade() -> None:
    for table_name in (
        "audit_events",
        "document_status_history",
        "document_versions",
        "stored_objects",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {table_name}_append_only ON {table_name}")
    op.execute("DROP FUNCTION IF EXISTS reject_append_only_change()")
    op.drop_table("graph_resume_requests")
    op.drop_table("graph_run_requests")
    op.drop_table("graph_runs")
    op.drop_table("idempotency_records")
    op.drop_table("audit_events")
    op.drop_table("document_status_history")
    op.drop_constraint("fk_documents_current_version", "documents", type_="foreignkey")
    op.drop_table("document_versions")
    op.drop_table("documents")
    op.drop_table("stored_objects")
    op.drop_table("users")
    op.drop_table("tenants")
