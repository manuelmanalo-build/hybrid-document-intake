from __future__ import annotations

from base64 import b64encode
from pathlib import Path

from pypdf import PdfReader

from intake_orchestrator.agent.extraction import BoundedExtractionContent

MAX_CONTENT_PAGES = 20
MAX_CONTENT_CHARS = 20_000
MAX_CONTENT_IMAGES = 10
MAX_CONTENT_IMAGE_BYTES = 4_000_000


def read_bounded_pdf_content(path: Path, *, include_images: bool) -> BoundedExtractionContent:
    """Read bounded page text and, optionally, bounded embedded JPEG/PNG images from a PDF.

    Callers own authorization, storage-root containment, and size checks. This function only
    enforces content bounds so the graph backend and offline evaluation read PDFs identically.
    """
    excerpts: list[str] = []
    image_data_urls: list[str] = []
    image_bytes = 0
    with path.open("rb") as stream:
        reader = PdfReader(stream, strict=True)
        for page_number, page in enumerate(reader.pages[:MAX_CONTENT_PAGES], start=1):
            remaining = MAX_CONTENT_CHARS - sum(len(value) for value in excerpts)
            if remaining <= 0:
                break
            text = (page.extract_text() or "")[:remaining]
            excerpts.append(f"[page {page_number}]\n{text}")
            if include_images:
                for image in page.images:
                    if (
                        len(image_data_urls) >= MAX_CONTENT_IMAGES
                        or image_bytes + len(image.data) > MAX_CONTENT_IMAGE_BYTES
                    ):
                        break
                    extension = image.name.rsplit(".", 1)[-1].casefold()
                    if extension not in {"jpg", "jpeg", "png"}:
                        continue
                    media_type = "image/jpeg" if extension in {"jpg", "jpeg"} else "image/png"
                    image_data_urls.append(
                        f"data:{media_type};base64,{b64encode(image.data).decode('ascii')}"
                    )
                    image_bytes += len(image.data)
    return BoundedExtractionContent(
        text="\n".join(excerpts)[:MAX_CONTENT_CHARS], image_data_urls=tuple(image_data_urls)
    )
