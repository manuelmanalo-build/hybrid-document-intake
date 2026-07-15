from pathlib import Path

PDF_BYTES = (
    b"%PDF-1.4\n"
    b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
    b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
    b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 144] "
    b"/Contents 4 0 R >>\nendobj\n"
    b"4 0 obj\n<< /Length 44 >>\nstream\n"
    b"BT /F1 12 Tf 20 100 Td (Synthetic invoice) Tj ET\n"
    b"endstream\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF\n"
)


def main() -> None:
    target = Path(__file__).resolve().parents[1] / "test-data" / "minimal.pdf"
    target.write_bytes(PDF_BYTES)


if __name__ == "__main__":
    main()
