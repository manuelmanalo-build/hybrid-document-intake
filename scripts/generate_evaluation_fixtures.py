from __future__ import annotations

import json
from pathlib import Path


def pdf_bytes(lines: list[str]) -> bytes:
    escaped = " ".join(lines).replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = f"BT /F1 10 Tf 20 110 Td ({escaped}) Tj ET".encode()
    return (
        b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R >>\nendobj\n4 0 obj\n<< /Length "
        + str(len(stream)).encode()
        + b" >>\nstream\n"
        + stream
        + b"\nendstream\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF\n"
    )


def main() -> None:
    root = Path(__file__).resolve().parents[1] / "test-data" / "evaluation"
    cases = json.loads((root / "cases.json").read_text())
    for case in cases:
        target = root / case["fixture"]
        if target.name == "malformed.pdf":
            target.write_bytes(b"This is intentionally not a PDF.")
            continue
        fields = case["expected"]["fields"]
        lines = [f"SYNTHETIC {case['case_id']}"] + [
            f"{key}: {value}" for key, value in fields.items()
        ]
        target.write_bytes(pdf_bytes(lines))


if __name__ == "__main__":
    main()
