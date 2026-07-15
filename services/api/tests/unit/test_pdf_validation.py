from pathlib import Path

import pytest

from intake_api.application.intake import _validate_pdf
from intake_api.application.problems import InvalidUpload
from intake_api.application.storage import StagedObject


def staged(head: bytes, size: int | None = None) -> StagedObject:
    return StagedObject(token="test", sha256="0" * 64, byte_size=size or len(head), head=head)


def test_valid_pdf_is_detected_from_content() -> None:
    payload = Path("test-data/minimal.pdf").read_bytes()

    assert (
        _validate_pdf(staged=staged(payload), declared_media_type="application/pdf")
        == "application/pdf"
    )


@pytest.mark.parametrize(
    ("payload", "media_type", "reason"),
    [
        (b"", "application/pdf", "empty-file"),
        (b"%PDF-1.4", "text/plain", "declared-mime-mismatch"),
        (b"not a pdf", "application/pdf", "invalid-pdf-magic"),
    ],
)
def test_invalid_pdf_boundaries(payload: bytes, media_type: str, reason: str) -> None:
    with pytest.raises(InvalidUpload) as caught:
        _validate_pdf(staged=staged(payload, len(payload)), declared_media_type=media_type)

    assert caught.value.problem_type == reason
