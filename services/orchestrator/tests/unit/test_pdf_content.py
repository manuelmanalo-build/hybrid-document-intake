from pathlib import Path

from intake_orchestrator.agent.pdf_content import MAX_CONTENT_CHARS, read_bounded_pdf_content


def test_bounded_pdf_content_reads_page_tagged_text() -> None:
    content = read_bounded_pdf_content(
        Path("test-data/evaluation/valid-invoice.pdf"), include_images=False
    )

    assert content.text.startswith("[page 1]")
    assert "INV-1001" in content.text
    assert len(content.text) <= MAX_CONTENT_CHARS
    assert content.image_data_urls == ()
