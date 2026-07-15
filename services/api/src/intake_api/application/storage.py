from __future__ import annotations

import hashlib
import os
import secrets
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Protocol


class StorageLimitExceeded(Exception):
    """Raised after the configured maximum byte count is crossed."""


class ImmutableObjectExists(Exception):
    """Raised when create-only finalization finds an existing key."""


@dataclass(frozen=True, slots=True)
class StagedObject:
    token: str
    sha256: str
    byte_size: int
    head: bytes


class StoragePort(Protocol):
    backend_name: str

    def stage(self, source: BinaryIO, *, max_bytes: int, chunk_bytes: int) -> StagedObject: ...

    def commit(self, staged: StagedObject, object_key: str) -> None: ...

    def discard_staged(self, staged: StagedObject) -> None: ...

    def delete_unreferenced(self, object_key: str) -> None: ...


class LocalImmutableStorage:
    backend_name = "local"

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()
        self._staging = self._root / ".staging"
        self._staging.mkdir(parents=True, exist_ok=True)

    def stage(self, source: BinaryIO, *, max_bytes: int, chunk_bytes: int) -> StagedObject:
        token = secrets.token_hex(32)
        path = self._staging / token
        digest = hashlib.sha256()
        total = 0
        head = bytearray()
        try:
            with path.open("xb") as target:
                while chunk := source.read(chunk_bytes):
                    total += len(chunk)
                    if total > max_bytes:
                        raise StorageLimitExceeded
                    digest.update(chunk)
                    if len(head) < 4096:
                        head.extend(chunk[: 4096 - len(head)])
                    target.write(chunk)
                target.flush()
                os.fsync(target.fileno())
        except Exception:
            path.unlink(missing_ok=True)
            raise
        return StagedObject(
            token=token, sha256=digest.hexdigest(), byte_size=total, head=bytes(head)
        )

    def commit(self, staged: StagedObject, object_key: str) -> None:
        staged_path = self._staging / staged.token
        final_path = self._resolve_key(object_key)
        final_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(staged_path, final_path)
        except FileExistsError as exc:
            raise ImmutableObjectExists(object_key) from exc
        os.chmod(final_path, 0o440)
        staged_path.unlink()

    def discard_staged(self, staged: StagedObject) -> None:
        (self._staging / staged.token).unlink(missing_ok=True)

    def delete_unreferenced(self, object_key: str) -> None:
        self._resolve_key(object_key).unlink(missing_ok=True)

    def _resolve_key(self, object_key: str) -> Path:
        pure = PurePosixPath(object_key)
        if pure.is_absolute() or ".." in pure.parts or not pure.parts:
            raise ValueError("Object key must be a safe relative path")
        resolved = (self._root / Path(*pure.parts)).resolve()
        if not resolved.is_relative_to(self._root):
            raise ValueError("Object key escapes storage root")
        return resolved
