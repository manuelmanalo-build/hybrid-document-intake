from pathlib import Path

from pdf_fixture_writer import build_text_pdf


def main() -> None:
    target = Path(__file__).resolve().parents[1] / "test-data" / "minimal.pdf"
    target.write_bytes(
        build_text_pdf(
            [
                "SYNTHETIC TEST FIXTURE - AP INVOICE",
                "Invoice number: MINIMAL-001",
                "Supplier: Synthetic Example Supplier",
                "Currency: USD",
                "Total: 1.00",
            ]
        )
    )


if __name__ == "__main__":
    main()
