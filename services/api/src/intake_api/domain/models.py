from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import Uuid

from intake_api.domain.enums import (
    ActorType,
    AuditEventType,
    DispatchRequestStatus,
    DocumentStatus,
    GraphRunStatus,
    IdempotencyStatus,
    UserRole,
)


class Base(DeclarativeBase):
    """Declarative base for application-owned PostgreSQL records."""


def enum_type(enum_class: type[Any], name: str, length: int) -> Enum:
    return Enum(
        enum_class,
        name=name,
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
        length=length,
    )


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_users_tenant_id_id"),
        UniqueConstraint("tenant_id", "external_subject", name="uq_users_tenant_subject"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    external_subject: Mapped[str] = mapped_column(String(200), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    role: Mapped[UserRole] = mapped_column(enum_type(UserRole, "user_role", 32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ += (ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_users_tenant"),)


class StoredObject(Base):
    __tablename__ = "stored_objects"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_stored_objects_tenant_id_id"),
        UniqueConstraint("object_key", name="uq_stored_objects_object_key"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_stored_objects_tenant"),
        CheckConstraint("byte_size > 0", name="ck_stored_objects_positive_size"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    object_key: Mapped[str] = mapped_column(String(512), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    media_type: Mapped[str] = mapped_column(String(127), nullable=False)
    storage_backend: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_documents_tenant_id_id"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_documents_tenant"),
        ForeignKeyConstraint(
            ["tenant_id", "created_by_user_id"],
            ["users.tenant_id", "users.id"],
            name="fk_documents_created_by_user",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "current_version_id"],
            ["document_versions.tenant_id", "document_versions.id"],
            name="fk_documents_current_version",
            use_alter=True,
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    created_by_user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    current_version_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    status: Mapped[DocumentStatus] = mapped_column(
        enum_type(DocumentStatus, "document_status", 32), nullable=False
    )
    duplicate_hash_detected: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class DocumentVersion(Base):
    __tablename__ = "document_versions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_document_versions_tenant_id_id"),
        UniqueConstraint(
            "tenant_id", "document_id", "version_number", name="uq_document_versions_number"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "document_id"],
            ["documents.tenant_id", "documents.id"],
            name="fk_document_versions_document",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "stored_object_id"],
            ["stored_objects.tenant_id", "stored_objects.id"],
            name="fk_document_versions_stored_object",
        ),
        CheckConstraint("version_number > 0", name="ck_document_versions_positive_version"),
        CheckConstraint("byte_size > 0", name="ck_document_versions_positive_size"),
        Index("ix_document_versions_tenant_sha256", "tenant_id", "sha256"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    document_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    stored_object_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    declared_media_type: Mapped[str] = mapped_column(String(127), nullable=False)
    detected_media_type: Mapped[str] = mapped_column(String(127), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class DocumentStatusHistory(Base):
    __tablename__ = "document_status_history"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "document_id"],
            ["documents.tenant_id", "documents.id"],
            name="fk_document_status_history_document",
        ),
        Index(
            "ix_document_status_history_tenant_document_created",
            "tenant_id",
            "document_id",
            "created_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    document_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    status: Mapped[DocumentStatus] = mapped_column(
        enum_type(DocumentStatus, "document_status_history_status", 32), nullable=False
    )
    actor_type: Mapped[ActorType] = mapped_column(
        enum_type(ActorType, "document_status_actor_type", 16), nullable=False
    )
    actor_id: Mapped[str] = mapped_column(String(200), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_audit_events_tenant"),
        ForeignKeyConstraint(
            ["tenant_id", "document_id"],
            ["documents.tenant_id", "documents.id"],
            name="fk_audit_events_document",
        ),
        Index(
            "ix_audit_events_tenant_document_created",
            "tenant_id",
            "document_id",
            "created_at",
        ),
        Index("ix_audit_events_correlation", "tenant_id", "correlation_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    document_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    graph_run_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    agent_run_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    event_type: Mapped[AuditEventType] = mapped_column(
        enum_type(AuditEventType, "audit_event_type", 64), nullable=False
    )
    actor_type: Mapped[ActorType] = mapped_column(
        enum_type(ActorType, "audit_actor_type", 16), nullable=False
    )
    actor_id: Mapped[str] = mapped_column(String(200), nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    before_values: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    after_values: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    sanitized_metadata: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "operation", "key_hash", name="uq_idempotency_tenant_operation_key"
        ),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_idempotency_tenant"),
        ForeignKeyConstraint(
            ["tenant_id", "resource_id"],
            ["documents.tenant_id", "documents.id"],
            name="fk_idempotency_document",
        ),
        CheckConstraint(
            "response_status >= 200 AND response_status < 600", name="ck_idempotency_status"
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    operation: Mapped[str] = mapped_column(String(100), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[IdempotencyStatus] = mapped_column(
        enum_type(IdempotencyStatus, "idempotency_status", 32), nullable=False
    )
    response_status: Mapped[int] = mapped_column(Integer, nullable=False)
    response_body: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    resource_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class GraphRun(Base):
    __tablename__ = "graph_runs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_graph_runs_tenant_id_id"),
        UniqueConstraint("tenant_id", "document_id", name="uq_graph_runs_document"),
        UniqueConstraint("thread_id", name="uq_graph_run_thread_id"),
        ForeignKeyConstraint(
            ["tenant_id", "document_id"],
            ["documents.tenant_id", "documents.id"],
            name="fk_graph_runs_document",
        ),
        Index("ix_graph_runs_status_created", "status", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    document_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    graph_version: Mapped[str] = mapped_column(String(64), nullable=False)
    thread_id: Mapped[str] = mapped_column(String(128), nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[GraphRunStatus] = mapped_column(
        enum_type(GraphRunStatus, "graph_run_status", 32), nullable=False
    )
    last_error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class GraphRunRequest(Base):
    __tablename__ = "graph_run_requests"
    __table_args__ = (
        UniqueConstraint("tenant_id", "graph_run_id", name="uq_graph_run_requests_run"),
        ForeignKeyConstraint(
            ["tenant_id", "document_id"],
            ["documents.tenant_id", "documents.id"],
            name="fk_graph_run_requests_document",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "graph_run_id"],
            ["graph_runs.tenant_id", "graph_runs.id"],
            name="fk_graph_run_requests_run",
        ),
        CheckConstraint("attempt_count >= 0", name="ck_graph_run_requests_attempt_count"),
        CheckConstraint("max_attempts > 0", name="ck_graph_run_requests_max_attempts"),
        Index("ix_graph_run_requests_due", "status", "next_attempt_at"),
        Index("ix_graph_run_requests_lease", "status", "lease_expires_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    document_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    graph_run_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[DispatchRequestStatus] = mapped_column(
        enum_type(DispatchRequestStatus, "graph_dispatch_request_status", 32), nullable=False
    )
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    last_error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class GraphResumeRequest(Base):
    __tablename__ = "graph_resume_requests"
    __table_args__ = (
        UniqueConstraint("tenant_id", "graph_run_id", "resume_key", name="uq_graph_resume_key"),
        ForeignKeyConstraint(
            ["tenant_id", "document_id"],
            ["documents.tenant_id", "documents.id"],
            name="fk_graph_resume_requests_document",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "graph_run_id"],
            ["graph_runs.tenant_id", "graph_runs.id"],
            name="fk_graph_resume_requests_run",
        ),
        CheckConstraint("attempt_count >= 0", name="ck_graph_resume_requests_attempt_count"),
        CheckConstraint("max_attempts > 0", name="ck_graph_resume_requests_max_attempts"),
        Index("ix_graph_resume_requests_due", "status", "next_attempt_at"),
        Index("ix_graph_resume_requests_lease", "status", "lease_expires_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    document_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    graph_run_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    resume_key: Mapped[str] = mapped_column(String(128), nullable=False)
    resume_payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    status: Mapped[DispatchRequestStatus] = mapped_column(
        enum_type(DispatchRequestStatus, "graph_resume_request_status", 32), nullable=False
    )
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    last_error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class PromptVersion(Base):
    __tablename__ = "prompt_versions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", "version", name="uq_prompt_version"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_prompt_versions_tenant"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AgentRun(Base):
    __tablename__ = "agent_runs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_agent_runs_tenant_id_id"),
        UniqueConstraint("tenant_id", "graph_run_id", name="uq_agent_runs_graph_run"),
        ForeignKeyConstraint(["tenant_id", "document_id"], ["documents.tenant_id", "documents.id"]),
        ForeignKeyConstraint(
            ["tenant_id", "graph_run_id"], ["graph_runs.tenant_id", "graph_runs.id"]
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    document_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    graph_run_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(200), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    tool_call_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    extraction_attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    usage_metadata: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(100))


class AgentToolCall(Base):
    __tablename__ = "agent_tool_calls"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "agent_run_id", "operation_key", name="uq_agent_tool_operation"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "agent_run_id"], ["agent_runs.tenant_id", "agent_runs.id"]
        ),
        ForeignKeyConstraint(["tenant_id", "document_id"], ["documents.tenant_id", "documents.id"]),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    document_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    agent_run_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    operation_key: Mapped[str] = mapped_column(String(64), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(100), nullable=False)
    validated_arguments: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    result_summary: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    reason_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)


class ExtractionRun(Base):
    __tablename__ = "extraction_runs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_extraction_runs_tenant_id_id"),
        UniqueConstraint(
            "tenant_id", "agent_run_id", "operation_key", name="uq_extraction_operation"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "agent_run_id"], ["agent_runs.tenant_id", "agent_runs.id"]
        ),
        ForeignKeyConstraint(["tenant_id", "document_id"], ["documents.tenant_id", "documents.id"]),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    document_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    agent_run_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    operation_key: Mapped[str] = mapped_column(String(64), nullable=False)
    strategy: Mapped[str] = mapped_column(String(32), nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(200), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ExtractionCandidate(Base):
    __tablename__ = "extraction_candidates"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_extraction_candidates_tenant_id_id"),
        UniqueConstraint("tenant_id", "extraction_run_id", name="uq_candidate_extraction_run"),
        ForeignKeyConstraint(
            ["tenant_id", "extraction_run_id"], ["extraction_runs.tenant_id", "extraction_runs.id"]
        ),
        ForeignKeyConstraint(["tenant_id", "document_id"], ["documents.tenant_id", "documents.id"]),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    document_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    extraction_run_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    document_type: Mapped[str] = mapped_column(String(32), nullable=False)
    candidate_data: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    completeness_score: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ExtractionFieldEvidence(Base):
    __tablename__ = "extraction_field_evidence"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "candidate_id", "field_name", name="uq_candidate_field_evidence"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "candidate_id"],
            ["extraction_candidates.tenant_id", "extraction_candidates.id"],
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    candidate_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    field_name: Mapped[str] = mapped_column(String(64), nullable=False)
    confidence: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
    source_page: Mapped[int | None] = mapped_column(Integer)
    evidence_excerpt: Mapped[str] = mapped_column(String(500), nullable=False)
    extraction_strategy: Mapped[str] = mapped_column(String(32), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(200), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)


class AgentRecommendation(Base):
    __tablename__ = "agent_recommendations"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_agent_recommendations_tenant_id_id"),
        UniqueConstraint("tenant_id", "agent_run_id", name="uq_agent_recommendations_run"),
        ForeignKeyConstraint(
            ["tenant_id", "agent_run_id"], ["agent_runs.tenant_id", "agent_runs.id"]
        ),
        ForeignKeyConstraint(["tenant_id", "document_id"], ["documents.tenant_id", "documents.id"]),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    document_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    agent_run_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    extraction_candidate_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    document_type: Mapped[str] = mapped_column(String(32), nullable=False)
    classification_confidence: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
    recommended_disposition: Mapped[str] = mapped_column(String(32), nullable=False)
    reason_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    summary: Mapped[str] = mapped_column(String(1000), nullable=False)
    requested_human_actions: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    tools_used: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    agent_policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    incomplete: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Supplier(Base):
    __tablename__ = "suppliers"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_suppliers_tenant_id_id"),
        UniqueConstraint("tenant_id", "tax_id", name="uq_suppliers_tax_id"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_suppliers_tenant"),
    )
    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    tax_id: Mapped[str | None] = mapped_column(String(100))
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_purchase_orders_tenant_id_id"),
        UniqueConstraint("tenant_id", "purchase_order_number", name="uq_purchase_orders_number"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_purchase_orders_tenant"),
        ForeignKeyConstraint(["tenant_id", "supplier_id"], ["suppliers.tenant_id", "suppliers.id"]),
    )
    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    supplier_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    purchase_order_number: Mapped[str] = mapped_column(String(100), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    authorized_amount: Mapped[Decimal] = mapped_column(Numeric(19, 4), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ValidationRun(Base):
    __tablename__ = "validation_runs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_validation_runs_tenant_id_id"),
        UniqueConstraint("tenant_id", "graph_run_id", "phase", name="uq_validation_phase"),
        ForeignKeyConstraint(
            ["tenant_id", "graph_run_id"], ["graph_runs.tenant_id", "graph_runs.id"]
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    document_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    graph_run_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    extraction_candidate_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    phase: Mapped[str] = mapped_column(String(32), nullable=False)
    authoritative: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    reason_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RuleDefinition(Base):
    __tablename__ = "rule_definitions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_rule_definitions_tenant_id_id"),
        UniqueConstraint("tenant_id", "rule_id", "version", name="uq_rule_definition_version"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_rule_definitions_tenant"),
    )
    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    rule_id: Mapped[str] = mapped_column(String(16), nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    configuration: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ValidationResult(Base):
    __tablename__ = "validation_results"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "validation_run_id", "rule_id", name="uq_validation_result_rule"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "validation_run_id"],
            ["validation_runs.tenant_id", "validation_runs.id"],
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    validation_run_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    rule_id: Mapped[str] = mapped_column(String(16), nullable=False)
    rule_version: Mapped[str] = mapped_column(String(32), nullable=False)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(100))
    sanitized_details: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ConfidenceRun(Base):
    __tablename__ = "confidence_runs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_confidence_runs_tenant_id_id"),
        UniqueConstraint("tenant_id", "graph_run_id", "phase", name="uq_confidence_graph_phase"),
        ForeignKeyConstraint(
            ["tenant_id", "graph_run_id"], ["graph_runs.tenant_id", "graph_runs.id"]
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    document_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    graph_run_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    validation_run_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    phase: Mapped[str] = mapped_column(String(32), nullable=False)
    algorithm_version: Mapped[str] = mapped_column(String(64), nullable=False)
    overall_score: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
    component_scores: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ConfidenceComponent(Base):
    __tablename__ = "confidence_components"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "confidence_run_id", "component_name", name="uq_confidence_component"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "confidence_run_id"],
            ["confidence_runs.tenant_id", "confidence_runs.id"],
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    confidence_run_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    component_name: Mapped[str] = mapped_column(String(64), nullable=False)
    raw_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    weight: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
    weighted_score: Mapped[Decimal | None] = mapped_column(Numeric(7, 6))
    applicable: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ReviewTask(Base):
    __tablename__ = "review_tasks"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_review_tasks_tenant_id_id"),
        UniqueConstraint("tenant_id", "graph_run_id", name="uq_review_task_graph_run"),
        ForeignKeyConstraint(
            ["tenant_id", "graph_run_id"], ["graph_runs.tenant_id", "graph_runs.id"]
        ),
        ForeignKeyConstraint(["tenant_id", "claimed_by_user_id"], ["users.tenant_id", "users.id"]),
    )
    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    document_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    graph_run_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    extraction_candidate_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    validation_run_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    confidence_run_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    recommendation_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    cycle: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    claimed_by_user_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    lock_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    response_payload: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    decision: Mapped[str | None] = mapped_column(String(32))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ReviewTaskClaim(Base):
    __tablename__ = "review_task_claims"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_review_task_claims_tenant_id_id"),
        ForeignKeyConstraint(
            ["tenant_id", "review_task_id"], ["review_tasks.tenant_id", "review_tasks.id"]
        ),
        ForeignKeyConstraint(["tenant_id", "user_id"], ["users.tenant_id", "users.id"]),
    )
    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    review_task_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    claimed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    release_reason: Mapped[str | None] = mapped_column(String(64))


class ReviewedDocumentVersion(Base):
    __tablename__ = "reviewed_document_versions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_reviewed_versions_tenant_id_id"),
        UniqueConstraint(
            "tenant_id", "review_task_id", "review_cycle", name="uq_reviewed_version_cycle"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "review_task_id"], ["review_tasks.tenant_id", "review_tasks.id"]
        ),
        ForeignKeyConstraint(["tenant_id", "created_by_user_id"], ["users.tenant_id", "users.id"]),
    )
    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    document_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    review_task_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    extraction_candidate_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    review_cycle: Mapped[int] = mapped_column(Integer, nullable=False)
    business_data: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    created_by_user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class FieldCorrection(Base):
    __tablename__ = "field_corrections"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "reviewed_document_version_id", "field_name", name="uq_field_correction"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "reviewed_document_version_id"],
            ["reviewed_document_versions.tenant_id", "reviewed_document_versions.id"],
        ),
        ForeignKeyConstraint(
            ["tenant_id", "corrected_by_user_id"], ["users.tenant_id", "users.id"]
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    reviewed_document_version_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    field_name: Mapped[str] = mapped_column(String(64), nullable=False)
    old_value: Mapped[object | None] = mapped_column(JSONB)
    new_value: Mapped[object | None] = mapped_column(JSONB)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    corrected_by_user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ReviewDecision(Base):
    __tablename__ = "review_decisions"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "review_task_id", "review_cycle", name="uq_review_decision_cycle"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "review_task_id"], ["review_tasks.tenant_id", "review_tasks.id"]
        ),
        ForeignKeyConstraint(
            ["tenant_id", "reviewed_document_version_id"],
            ["reviewed_document_versions.tenant_id", "reviewed_document_versions.id"],
        ),
        ForeignKeyConstraint(["tenant_id", "decided_by_user_id"], ["users.tenant_id", "users.id"]),
    )
    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    review_task_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    reviewed_document_version_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    review_cycle: Mapped[int] = mapped_column(Integer, nullable=False)
    decision: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str] = mapped_column(String(1000), nullable=False)
    decided_by_user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class WebhookOutboxEvent(Base):
    __tablename__ = "webhook_outbox_events"
    __table_args__ = (
        UniqueConstraint("tenant_id", "event_key", name="uq_webhook_outbox_event_key"),
        ForeignKeyConstraint(
            ["tenant_id", "graph_run_id"], ["graph_runs.tenant_id", "graph_runs.id"]
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    document_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    graph_run_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    event_key: Mapped[str] = mapped_column(String(128), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ApprovedBusinessRecord(Base):
    __tablename__ = "approved_business_records"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_approved_records_tenant_id_id"),
        UniqueConstraint("tenant_id", "document_id", name="uq_approved_record_document"),
        ForeignKeyConstraint(["tenant_id", "document_id"], ["documents.tenant_id", "documents.id"]),
        ForeignKeyConstraint(
            ["tenant_id", "graph_run_id"], ["graph_runs.tenant_id", "graph_runs.id"]
        ),
        ForeignKeyConstraint(
            ["tenant_id", "reviewed_document_version_id"],
            ["reviewed_document_versions.tenant_id", "reviewed_document_versions.id"],
        ),
        ForeignKeyConstraint(["review_decision_id"], ["review_decisions.id"]),
        ForeignKeyConstraint(["tenant_id", "approved_by_user_id"], ["users.tenant_id", "users.id"]),
    )
    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    document_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    graph_run_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    review_decision_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    reviewed_document_version_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    record_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    business_data: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    approved_by_user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    approved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class OutboxEvent(Base):
    __tablename__ = "outbox_events"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_outbox_events_tenant_id_id"),
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_outbox_idempotency_key"),
        ForeignKeyConstraint(
            ["tenant_id", "approved_business_record_id"],
            ["approved_business_records.tenant_id", "approved_business_records.id"],
        ),
        ForeignKeyConstraint(["tenant_id", "document_id"], ["documents.tenant_id", "documents.id"]),
        ForeignKeyConstraint(
            ["tenant_id", "graph_run_id"], ["graph_runs.tenant_id", "graph_runs.id"]
        ),
        CheckConstraint("attempt_count >= 0", name="ck_outbox_attempt_count"),
        CheckConstraint("max_attempts > 0", name="ck_outbox_max_attempts"),
        Index("ix_outbox_events_due", "status", "next_attempt_at"),
        Index("ix_outbox_events_lease", "status", "lease_expires_at"),
    )
    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    document_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    graph_run_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    approved_business_record_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    lease_owner: Mapped[str | None] = mapped_column(String(128))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(100))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    callback_id: Mapped[str | None] = mapped_column(String(128))
    callback_status: Mapped[str | None] = mapped_column(String(32))
    callback_received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class WebhookDelivery(Base):
    __tablename__ = "webhook_deliveries"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_webhook_deliveries_tenant_id_id"),
        UniqueConstraint(
            "tenant_id", "outbox_event_id", "attempt_number", name="uq_delivery_attempt"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "outbox_event_id"], ["outbox_events.tenant_id", "outbox_events.id"]
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    outbox_event_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    http_status: Mapped[int | None] = mapped_column(Integer)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    response_summary: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DeadLetterEvent(Base):
    __tablename__ = "dead_letter_events"
    __table_args__ = (
        UniqueConstraint("tenant_id", "outbox_event_id", name="uq_dead_letter_outbox"),
        ForeignKeyConstraint(
            ["tenant_id", "outbox_event_id"], ["outbox_events.tenant_id", "outbox_events.id"]
        ),
        ForeignKeyConstraint(
            ["tenant_id", "last_replayed_by_user_id"], ["users.tenant_id", "users.id"]
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    outbox_event_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(100), nullable=False)
    sanitized_error: Mapped[str] = mapped_column(String(500), nullable=False)
    replay_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_replayed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_replayed_by_user_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
