from __future__ import annotations

import hashlib
import re
import secrets
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import puremagic
import structlog
from fastapi import UploadFile
from sqlalchemy import exists, select, text
from sqlalchemy.orm import Session, sessionmaker

from intake_api.application.identity import RequestIdentity
from intake_api.application.problems import IdempotencyConflict, InvalidUpload, UploadTooLarge
from intake_api.application.schemas import CreateDocumentResult, DocumentCreated
from intake_api.application.storage import (
    LocalImmutableStorage,
    StagedObject,
    StorageLimitExceeded,
    StoragePort,
)
from intake_api.domain.enums import (
    ActorType,
    AuditEventType,
    DispatchRequestStatus,
    DocumentStatus,
    GraphRunStatus,
    IdempotencyStatus,
)
from intake_api.domain.models import (
    AuditEvent,
    Document,
    DocumentStatusHistory,
    DocumentVersion,
    GraphRun,
    GraphRunRequest,
    IdempotencyRecord,
    StoredObject,
)
from intake_api.infrastructure.config import Settings

logger = structlog.get_logger(__name__)
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")
_IDEMPOTENCY_OPERATION = "POST:/api/v1/documents"


class IntakeService:
    def __init__(
        self,
        settings: Settings,
        session_factory: sessionmaker[Session],
        storage: StoragePort,
    ) -> None:
        self._settings = settings
        self._session_factory = session_factory
        self._storage = storage

    def create_document(
        self,
        *,
        upload: UploadFile,
        idempotency_key: str,
        correlation_id: str,
        identity: RequestIdentity,
    ) -> CreateDocumentResult:
        normalized_key = idempotency_key.strip()
        if not normalized_key or len(normalized_key) > 128:
            raise InvalidUpload(
                "Idempotency-Key must contain between 1 and 128 non-whitespace characters.",
                "invalid-idempotency-key",
            )

        filename = _sanitize_filename(upload.filename)
        declared_media_type = (upload.content_type or "").lower().strip()
        staged: StagedObject | None = None
        object_key: str | None = None
        database_committed = False
        try:
            try:
                staged = self._storage.stage(
                    upload.file,
                    max_bytes=self._settings.upload_max_bytes,
                    chunk_bytes=self._settings.upload_chunk_bytes,
                )
            except StorageLimitExceeded as exc:
                raise UploadTooLarge(self._settings.upload_max_bytes) from exc

            detected_media_type = _validate_pdf(
                staged=staged,
                declared_media_type=declared_media_type,
            )
            key_hash = _hash_text(f"{identity.tenant_id}:{normalized_key}")
            request_fingerprint = _hash_text(f"{staged.sha256}:{filename}:{declared_media_type}")
            sha256 = staged.sha256
            byte_size = staged.byte_size

            with self._session_factory() as session, session.begin():
                _lock_idempotency_key(session, key_hash)
                existing = session.scalar(
                    select(IdempotencyRecord).where(
                        IdempotencyRecord.tenant_id == identity.tenant_id,
                        IdempotencyRecord.operation == _IDEMPOTENCY_OPERATION,
                        IdempotencyRecord.key_hash == key_hash,
                    )
                )
                if existing is not None:
                    if existing.request_fingerprint != request_fingerprint:
                        raise IdempotencyConflict
                    response = DocumentCreated.model_validate(existing.response_body)
                    self._storage.discard_staged(staged)
                    staged = None
                    logger.info(
                        "document_upload_replayed",
                        document_id=str(response.document_id),
                        tenant_id=str(identity.tenant_id),
                    )
                    return CreateDocumentResult(
                        response=response,
                        response_status=existing.response_status,
                        replayed=True,
                    )

                duplicate_hash_detected = bool(
                    session.scalar(
                        select(
                            exists().where(
                                DocumentVersion.tenant_id == identity.tenant_id,
                                DocumentVersion.sha256 == sha256,
                            )
                        )
                    )
                )
                document_id = uuid4()
                document_version_id = uuid4()
                stored_object_id = uuid4()
                graph_run_id = uuid4()
                graph_run_request_id = uuid4()
                thread_id = str(uuid4())
                object_key = _generate_object_key(identity.tenant_id)
                self._storage.commit(staged, object_key)
                staged = None

                stored_object = StoredObject(
                    id=stored_object_id,
                    tenant_id=identity.tenant_id,
                    object_key=object_key,
                    sha256=sha256,
                    byte_size=byte_size,
                    media_type=detected_media_type,
                    storage_backend=self._storage.backend_name,
                )
                document = Document(
                    id=document_id,
                    tenant_id=identity.tenant_id,
                    created_by_user_id=identity.user_id,
                    current_version_id=None,
                    status=DocumentStatus.RECEIVED,
                    duplicate_hash_detected=duplicate_hash_detected,
                )
                document_version = DocumentVersion(
                    id=document_version_id,
                    tenant_id=identity.tenant_id,
                    document_id=document_id,
                    stored_object_id=stored_object_id,
                    version_number=1,
                    original_filename=filename,
                    declared_media_type=declared_media_type,
                    detected_media_type=detected_media_type,
                    sha256=sha256,
                    byte_size=byte_size,
                )
                session.add_all([stored_object, document])
                session.flush()
                session.add(document_version)
                session.flush()
                document.current_version_id = document_version_id

                graph_run = GraphRun(
                    id=graph_run_id,
                    tenant_id=identity.tenant_id,
                    document_id=document_id,
                    graph_version=self._settings.graph_version,
                    thread_id=thread_id,
                    correlation_id=correlation_id,
                    status=GraphRunStatus.REQUESTED,
                )
                graph_run_request = GraphRunRequest(
                    id=graph_run_request_id,
                    tenant_id=identity.tenant_id,
                    document_id=document_id,
                    graph_run_id=graph_run_id,
                    correlation_id=correlation_id,
                    status=DispatchRequestStatus.PENDING,
                    attempt_count=0,
                    max_attempts=self._settings.graph_request_max_attempts,
                    next_attempt_at=datetime.now(UTC),
                )
                status_history = DocumentStatusHistory(
                    tenant_id=identity.tenant_id,
                    document_id=document_id,
                    status=DocumentStatus.RECEIVED,
                    actor_type=ActorType.USER,
                    actor_id=str(identity.user_id),
                    reason_code="UPLOAD_ACCEPTED",
                )
                response = DocumentCreated(
                    document_id=document_id,
                    document_version_id=document_version_id,
                    status="RECEIVED",
                    duplicate_hash_detected=duplicate_hash_detected,
                    correlation_id=correlation_id,
                    graph_run_id=graph_run_id,
                    graph_run_status="REQUESTED",
                )
                response_body = response.model_dump(mode="json")
                idempotency_record = IdempotencyRecord(
                    tenant_id=identity.tenant_id,
                    operation=_IDEMPOTENCY_OPERATION,
                    key_hash=key_hash,
                    request_fingerprint=request_fingerprint,
                    status=IdempotencyStatus.COMPLETED,
                    response_status=201,
                    response_body=response_body,
                    resource_id=document_id,
                )
                audit_events = [
                    _audit_event(
                        identity=identity,
                        correlation_id=correlation_id,
                        event_type=AuditEventType.UPLOAD_RECEIVED,
                        document_id=document_id,
                        metadata={"filename_present": bool(filename)},
                    ),
                    _audit_event(
                        identity=identity,
                        correlation_id=correlation_id,
                        event_type=AuditEventType.FILE_VALIDATION_PASSED,
                        document_id=document_id,
                        metadata={
                            "byte_size": byte_size,
                            "detected_media_type": detected_media_type,
                        },
                    ),
                    _audit_event(
                        identity=identity,
                        correlation_id=correlation_id,
                        event_type=AuditEventType.DOCUMENT_STORED,
                        document_id=document_id,
                        metadata={
                            "stored_object_id": str(stored_object_id),
                            "storage_backend": self._storage.backend_name,
                        },
                    ),
                    _audit_event(
                        identity=identity,
                        correlation_id=correlation_id,
                        event_type=AuditEventType.GRAPH_RUN_REQUESTED,
                        document_id=document_id,
                        graph_run_id=graph_run_id,
                        metadata={
                            "graph_run_id": str(graph_run_id),
                            "graph_run_request_id": str(graph_run_request_id),
                            "graph_version": self._settings.graph_version,
                            "thread_id": thread_id,
                        },
                    ),
                ]
                session.add_all(
                    [
                        status_history,
                        graph_run,
                        graph_run_request,
                        idempotency_record,
                        *audit_events,
                    ]
                )
            database_committed = True

            logger.info(
                "document_upload_committed",
                document_id=str(document_id),
                tenant_id=str(identity.tenant_id),
                duplicate_hash_detected=duplicate_hash_detected,
            )
            return CreateDocumentResult(response=response, response_status=201, replayed=False)
        except (InvalidUpload, UploadTooLarge) as exc:
            if staged is not None:
                self._storage.discard_staged(staged)
            self._record_validation_failure(
                identity=identity,
                correlation_id=correlation_id,
                filename=filename,
                declared_media_type=declared_media_type,
                reason_code=exc.problem_type,
            )
            raise
        except Exception:
            if staged is not None:
                self._storage.discard_staged(staged)
            if object_key is not None and not database_committed:
                self._storage.delete_unreferenced(object_key)
            raise

    def _record_validation_failure(
        self,
        *,
        identity: RequestIdentity,
        correlation_id: str,
        filename: str,
        declared_media_type: str,
        reason_code: str,
    ) -> None:
        with self._session_factory() as session, session.begin():
            session.add_all(
                [
                    _audit_event(
                        identity=identity,
                        correlation_id=correlation_id,
                        event_type=AuditEventType.UPLOAD_RECEIVED,
                        document_id=None,
                        metadata={"filename_present": bool(filename)},
                    ),
                    _audit_event(
                        identity=identity,
                        correlation_id=correlation_id,
                        event_type=AuditEventType.FILE_VALIDATION_FAILED,
                        document_id=None,
                        metadata={
                            "declared_media_type": declared_media_type or "missing",
                            "reason_code": reason_code,
                        },
                    ),
                ]
            )


def build_intake_service(
    settings: Settings, session_factory: sessionmaker[Session]
) -> IntakeService:
    return IntakeService(settings, session_factory, LocalImmutableStorage(settings.storage_root))


def _validate_pdf(*, staged: StagedObject, declared_media_type: str) -> str:
    if staged.byte_size == 0:
        raise InvalidUpload("The uploaded file is empty.", "empty-file")
    if declared_media_type != "application/pdf":
        raise InvalidUpload(
            "The multipart file media type must be application/pdf.", "declared-mime-mismatch"
        )
    if not staged.head.startswith(b"%PDF-"):
        raise InvalidUpload("The uploaded bytes do not contain a PDF header.", "invalid-pdf-magic")
    try:
        detected_media_type: str = puremagic.from_string(  # pyright: ignore[reportUnknownMemberType]
            staged.head, mime=True
        )
    except puremagic.PureError as exc:
        raise InvalidUpload(
            "The uploaded file type could not be identified.", "mime-undetected"
        ) from exc
    if detected_media_type != "application/pdf":
        raise InvalidUpload(
            "The detected file media type is not application/pdf.", "detected-mime-mismatch"
        )
    return detected_media_type


def _sanitize_filename(filename: str | None) -> str:
    leaf = Path(filename or "upload.pdf").name
    sanitized = _CONTROL_CHARACTERS.sub("_", leaf).strip()
    return (sanitized or "upload.pdf")[:255]


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _generate_object_key(tenant_id: UUID) -> str:
    return f"originals/{tenant_id.hex}/{secrets.token_hex(32)}"


def _lock_idempotency_key(session: Session, key_hash: str) -> None:
    lock_key = int.from_bytes(bytes.fromhex(key_hash[:16]), byteorder="big", signed=True)
    session.execute(text("SELECT pg_advisory_xact_lock(:lock_key)"), {"lock_key": lock_key})


def _audit_event(
    *,
    identity: RequestIdentity,
    correlation_id: str,
    event_type: AuditEventType,
    document_id: UUID | None,
    metadata: dict[str, object],
    graph_run_id: UUID | None = None,
) -> AuditEvent:
    return AuditEvent(
        tenant_id=identity.tenant_id,
        document_id=document_id,
        graph_run_id=graph_run_id,
        event_type=event_type,
        actor_type=ActorType.USER,
        actor_id=str(identity.user_id),
        correlation_id=correlation_id,
        sanitized_metadata=metadata,
    )
