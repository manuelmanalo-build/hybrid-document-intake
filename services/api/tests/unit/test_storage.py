from io import BytesIO
from pathlib import Path

import pytest

from intake_api.application.storage import LocalImmutableStorage, StorageLimitExceeded


def test_storage_streams_hashes_and_commits_create_only(tmp_path: Path) -> None:
    storage = LocalImmutableStorage(tmp_path)
    staged = storage.stage(BytesIO(b"%PDF-1.4\nbody"), max_bytes=100, chunk_bytes=3)

    assert staged.byte_size == 13
    assert staged.sha256 == "5b2de8fc92940656e7ae06d0864be972a778c80f7fc0a89a9ef9414956b0b789"
    storage.commit(staged, "originals/tenant/random-key")

    stored = tmp_path / "originals/tenant/random-key"
    assert stored.read_bytes() == b"%PDF-1.4\nbody"
    assert stored.stat().st_mode & 0o777 == 0o440


def test_storage_removes_staging_file_when_limit_is_exceeded(tmp_path: Path) -> None:
    storage = LocalImmutableStorage(tmp_path)

    with pytest.raises(StorageLimitExceeded):
        storage.stage(BytesIO(b"123456"), max_bytes=5, chunk_bytes=2)

    assert list((tmp_path / ".staging").iterdir()) == []


@pytest.mark.parametrize("key", ["../escape", "/absolute", "safe/../../escape"])
def test_storage_rejects_unsafe_object_keys(tmp_path: Path, key: str) -> None:
    storage = LocalImmutableStorage(tmp_path)
    staged = storage.stage(BytesIO(b"x"), max_bytes=2, chunk_bytes=1)

    with pytest.raises(ValueError, match="Object key"):
        storage.commit(staged, key)
