import pytest

from intake_orchestrator.pii import redact_identifiers, scan_identifiers


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("contact maya.chen@example.com today", "EMAIL"),
        ("call (212) 555-0155", "PHONE"),
        ("call +1 212-555-0155", "PHONE"),
        ("ssn 123-45-6789 on file", "SSN"),
        ("card 4111 1111 1111 1111 exp 12/29", "CARD"),
    ],
)
def test_direct_identifiers_are_found_and_redacted(text: str, kind: str) -> None:
    assert scan_identifiers(text) == {kind: 1}
    redacted = redact_identifiers(text)
    assert f"[{kind}]" in redacted
    assert "example.com" not in redacted or kind != "EMAIL"


@pytest.mark.parametrize(
    "text",
    [
        "m***@example.com",
        "(***) ***-0155",
        "order #90412 total 18.50",
        "SKU-3100 qty 2 on 2026-10-12",
        "4111 1111 1111 1112",
    ],
)
def test_masked_values_and_ordinary_numbers_are_left_alone(text: str) -> None:
    assert scan_identifiers(text) == {}
    assert redact_identifiers(text) == text


def test_counts_cover_every_occurrence_without_returning_values() -> None:
    text = "a@example.com, b@example.com | (212) 555-0155 | (212) 555-0156"

    counts = scan_identifiers(text)

    assert counts == {"EMAIL": 2, "PHONE": 2}
    assert redact_identifiers(text) == "[EMAIL], [EMAIL] | [PHONE] | [PHONE]"
