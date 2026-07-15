from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from intake_orchestrator.confidence import decision_confidence
from intake_orchestrator.validation import (
    InvoiceCandidateData,
    ValidationContext,
    validate_candidate_rules,
)


def valid_candidate() -> InvoiceCandidateData:
    return InvoiceCandidateData(
        invoice_number="INV-1",
        invoice_date=date(2026, 1, 1),
        due_date=date(2026, 1, 31),
        supplier_name="Active Supplier",
        supplier_tax_id="TAX-1",
        purchase_order_number="PO-1",
        currency="USD",
        subtotal=Decimal("100.00"),
        tax=Decimal("8.00"),
        total=Decimal("108.00"),
        field_confidence={
            "invoice_number": Decimal("0.95"),
            "supplier_name": Decimal("0.95"),
            "total": Decimal("0.95"),
        },
    )


def context(**changes: object) -> ValidationContext:
    values: dict[str, object] = {
        "business_date": date(2026, 2, 1),
        "supplier_matched": True,
        "supplier_active": True,
        "purchase_order_matched": True,
        "purchase_order_authorized_amount": Decimal("200.00"),
    }
    values.update(changes)
    return ValidationContext(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("rule_id", "candidate_changes", "context_changes"),
    [
        ("BR-001", {"invoice_number": None}, {}),
        ("BR-002", {"invoice_date": date(2026, 2, 2)}, {}),
        ("BR-003", {"due_date": date(2025, 12, 31)}, {}),
        ("BR-004", {"total": Decimal("109.00")}, {}),
        ("BR-005", {"currency": "ZZZ"}, {}),
        ("BR-006", {"supplier_name": None}, {}),
        ("BR-007", {}, {"duplicate_approved_invoice": True}),
        (
            "BR-008",
            {
                "total": Decimal("12000"),
                "subtotal": Decimal("11992"),
                "purchase_order_number": None,
            },
            {"purchase_order_matched": False, "purchase_order_authorized_amount": None},
        ),
        (
            "BR-009",
            {"field_confidence": {"invoice_number": Decimal("0.1")}},
            {},
        ),
        ("BR-010", {}, {"document_type": "UNKNOWN"}),
        ("BR-011", {}, {"supplier_active": False}),
        ("BR-012", {}, {"purchase_order_authorized_amount": Decimal("50.00")}),
    ],
)
def test_each_deterministic_rule_can_block_or_require_review(
    rule_id: str,
    candidate_changes: dict[str, object],
    context_changes: dict[str, object],
) -> None:
    candidate = valid_candidate().model_copy(update=candidate_changes)
    results = validate_candidate_rules(candidate, context(**context_changes))
    result = next(item for item in results if item.rule_id == rule_id)

    assert result.outcome in {"FAIL", "REVIEW"}
    assert result.reason_code is not None


def test_all_rules_pass_or_are_not_applicable_for_valid_invoice() -> None:
    results = validate_candidate_rules(valid_candidate(), context())

    assert all(result.outcome in {"PASS", "NOT_APPLICABLE"} for result in results)
    assert {result.rule_id for result in results} == {f"BR-{number:03d}" for number in range(1, 13)}


def test_versioned_confidence_algorithm_uses_decimal_and_risk_penalty() -> None:
    components = {
        "critical_field_confidence": Decimal("0.5"),
        "required_field_completeness": Decimal("0.5"),
        "evidence_coverage": Decimal("0.5"),
        "classification_confidence": Decimal("0.5"),
        "supplier_match_quality": Decimal("0.5"),
        "purchase_order_match_quality": Decimal("0.5"),
        "deterministic_rule_score": Decimal("0.5"),
    }

    assert decision_confidence(components, rule_based_risk=Decimal("0.5")) == Decimal("0.3500")
    assert decision_confidence(components, rule_based_risk=Decimal("1")) == Decimal("0.2000")


def test_confidence_omits_inapplicable_reference_weights() -> None:
    components = {
        "critical_field_confidence": Decimal("1"),
        "required_field_completeness": Decimal("1"),
        "evidence_coverage": Decimal("1"),
        "classification_confidence": Decimal("1"),
        "supplier_match_quality": None,
        "purchase_order_match_quality": None,
        "deterministic_rule_score": Decimal("1"),
    }

    assert decision_confidence(components, rule_based_risk=Decimal("0")) == Decimal("1.0000")


def test_decimal_tolerance_and_date_boundaries_are_inclusive() -> None:
    candidate = valid_candidate().model_copy(
        update={
            "invoice_date": date(2026, 2, 1),
            "due_date": date(2026, 2, 1),
            "total": Decimal("108.01"),
        }
    )
    results = {item.rule_id: item for item in validate_candidate_rules(candidate, context())}

    assert results["BR-002"].outcome == "PASS"
    assert results["BR-003"].outcome == "PASS"
    assert results["BR-004"].outcome == "PASS"
