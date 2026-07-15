from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import BinaryIO
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi import UploadFile
from starlette.datastructures import Headers

from intake_api.application.identity import RequestIdentity
from intake_api.application.intake import IntakeService
from intake_api.application.storage import LocalImmutableStorage, StagedObject, StoragePort
from intake_api.domain.enums import UserRole
from intake_api.infrastructure.config import Settings


class FailingCommitStorage(StoragePort):
    backend_name = "failing-test-storage"

    def __init__(self, root: Path) -> None:
        self.delegate = LocalImmutableStorage(root)

    def stage(self, source: BinaryIO, *, max_bytes: int, chunk_bytes: int) -> StagedObject:
        return self.delegate.stage(source, max_bytes=max_bytes, chunk_bytes=chunk_bytes)

    def commit(self, staged: StagedObject, object_key: str) -> None:
        raise OSError("simulated storage failure")

    def discard_staged(self, staged: StagedObject) -> None:
        self.delegate.discard_staged(staged)

    def delete_unreferenced(self, object_key: str) -> None:
        self.delegate.delete_unreferenced(object_key)


def test_storage_failure_discards_staged_content(tmp_path: Path) -> None:
    session = MagicMock()
    session.__enter__.return_value = session
    session.__exit__.return_value = False
    transaction = MagicMock()
    transaction.__enter__.return_value = transaction
    transaction.__exit__.return_value = False
    session.begin.return_value = transaction
    session.scalar.side_effect = [None, False]
    session_factory = MagicMock(return_value=session)
    storage = FailingCommitStorage(tmp_path)
    service = IntakeService(Settings(storage_root=tmp_path), session_factory, storage)

    with pytest.raises(OSError, match="simulated storage failure"):
        service.create_document(
            upload=_upload(),
            idempotency_key="storage-failure",
            correlation_id="storage-failure",
            identity=_identity(),
        )

    assert [path for path in tmp_path.rglob("*") if path.is_file()] == []


def test_database_commit_failure_removes_unreferenced_final_object(tmp_path: Path) -> None:
    session = MagicMock()
    session.__enter__.return_value = session
    session.__exit__.return_value = False
    transaction = MagicMock()
    transaction.__enter__.return_value = transaction
    transaction.__exit__.side_effect = RuntimeError("simulated PostgreSQL commit failure")
    session.begin.return_value = transaction
    session.scalar.side_effect = [None, False]
    session_factory = MagicMock(return_value=session)
    storage = LocalImmutableStorage(tmp_path)
    service = IntakeService(Settings(storage_root=tmp_path), session_factory, storage)

    with pytest.raises(RuntimeError, match="simulated PostgreSQL commit failure"):
        service.create_document(
            upload=_upload(),
            idempotency_key="database-failure",
            correlation_id="database-failure",
            identity=_identity(),
        )

    assert [path for path in tmp_path.rglob("*") if path.is_file()] == []


def test_post_commit_logging_failure_does_not_delete_authoritative_original(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = MagicMock()
    session.__enter__.return_value = session
    session.__exit__.return_value = False
    transaction = MagicMock()
    transaction.__enter__.return_value = transaction
    transaction.__exit__.return_value = False
    session.begin.return_value = transaction
    session.scalar.side_effect = [None, False]
    session_factory = MagicMock(return_value=session)
    storage = LocalImmutableStorage(tmp_path)
    service = IntakeService(Settings(storage_root=tmp_path), session_factory, storage)

    def fail_log(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise RuntimeError("simulated post-commit logging failure")

    monkeypatch.setattr("intake_api.application.intake.logger.info", fail_log)
    with pytest.raises(RuntimeError, match="simulated post-commit logging failure"):
        service.create_document(
            upload=_upload(),
            idempotency_key="post-commit-failure",
            correlation_id="post-commit-failure",
            identity=_identity(),
        )

    originals = [
        path for path in tmp_path.rglob("*") if path.is_file() and ".staging" not in path.parts
    ]
    assert len(originals) == 1


def _upload() -> UploadFile:
    payload = Path("test-data/minimal.pdf").read_bytes()
    return UploadFile(
        file=BytesIO(payload),
        filename="invoice.txt",
        headers=Headers({"content-type": "application/pdf"}),
    )


def _identity() -> RequestIdentity:
    return RequestIdentity(
        tenant_id=uuid4(),
        user_id=uuid4(),
        subject="unit-test-submitter",
        role=UserRole.SUBMITTER,
    )
