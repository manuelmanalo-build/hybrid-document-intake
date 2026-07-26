from __future__ import annotations

import json
from pathlib import Path

from pdf_fixture_writer import build_text_pdf

FIELD_LABELS = {
    "invoice_number": "Invoice number",
    "invoice_date": "Invoice date",
    "due_date": "Due date",
    "supplier_name": "Supplier",
    "supplier_tax_id": "Supplier tax ID",
    "purchase_order_number": "Purchase order",
    "currency": "Currency",
    "subtotal": "Subtotal",
    "tax": "Tax",
    "total": "Total",
}


def fixture_lines(case_id: str, fields: dict[str, object]) -> list[str]:
    if not fields:
        return [
            "SYNTHETIC TEST FIXTURE - DELIVERY NOTE",
            f"Scenario: {case_id}",
            "Document reference: DN-1006",
            "Document type: Delivery note (intentionally not an AP invoice)",
            "Purpose: unsupported-document classification test",
            "All names and identifiers are fictional.",
        ]

    lines = [
        "SYNTHETIC TEST FIXTURE - AP INVOICE",
        f"Scenario: {case_id}",
        "All names, identifiers, dates, and amounts are fictional.",
        "",
    ]
    for field_name, value in fields.items():
        if value is not None:
            lines.append(f"{FIELD_LABELS[field_name]}: {value}")
    if case_id == "low-confidence-invoice":
        lines.append("Fixture note: simulates a low-confidence visual extraction case.")
    return lines


def main() -> None:
    root = Path(__file__).resolve().parents[1] / "test-data" / "evaluation"
    cases = json.loads((root / "cases.json").read_text())
    for case in cases:
        target = root / case["fixture"]
        if target.name == "malformed.pdf":
            target.write_bytes(b"This is intentionally not a PDF.")
            continue
        fields = case["expected"]["fields"]
        target.write_bytes(build_text_pdf(fixture_lines(case["case_id"], fields)))


if __name__ == "__main__":
    main()
