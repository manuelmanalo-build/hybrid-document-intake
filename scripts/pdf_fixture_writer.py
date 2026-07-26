from __future__ import annotations

from collections.abc import Sequence


def build_text_pdf(lines: Sequence[str]) -> bytes:
    """Build a deterministic, standards-compliant, one-page PDF with extractable text."""
    if not lines:
        raise ValueError("At least one line is required")

    commands = ["BT", "/F1 16 Tf", "72 728 Td", "20 TL"]
    for index, line in enumerate(lines):
        if index:
            commands.append("T*")
        if index == 1:
            commands.extend(["/F1 10 Tf", "16 TL"])
        commands.append(f"({_escape_literal(line)}) Tj")
    commands.append("ET")
    content_stream = "\n".join(commands).encode("ascii")

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"
        ),
        (
            f"<< /Length {len(content_stream)} >>\nstream\n".encode()
            + content_stream
            + b"\nendstream"
        ),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]

    document = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for object_number, body in enumerate(objects, start=1):
        offsets.append(len(document))
        document.extend(f"{object_number} 0 obj\n".encode())
        document.extend(body)
        document.extend(b"\nendobj\n")

    xref_offset = len(document)
    document.extend(f"xref\n0 {len(objects) + 1}\n".encode())
    document.extend(b"0000000000 65535 f\r\n")
    for offset in offsets[1:]:
        document.extend(f"{offset:010d} 00000 n\r\n".encode())
    document.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode()
    )
    return bytes(document)


def _escape_literal(value: str) -> str:
    value.encode("ascii")
    return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
