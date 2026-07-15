from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

RULESET_VERSION = "ap-invoice-rules-v1"
RULE_DEFINITIONS: tuple[tuple[str, str, str], ...] = (
    ("BR-001", "Invoice number is required", "ERROR"),
    ("BR-002", "Invoice date cannot be in the future", "ERROR"),
    ("BR-003", "Due date cannot precede invoice date", "ERROR"),
    ("BR-004", "Total must equal subtotal plus tax within tolerance", "ERROR"),
    ("BR-005", "Currency must be supported", "ERROR"),
    ("BR-006", "Supplier name is required", "ERROR"),
    ("BR-007", "Approved supplier/invoice number must not be duplicated", "ERROR"),
    ("BR-008", "Purchase-order number is required above threshold", "ERROR"),
    ("BR-009", "Critical low-confidence fields require review", "REVIEW"),
    ("BR-010", "Unsupported or unknown documents require review", "REVIEW"),
    ("BR-011", "A matched supplier must be active", "ERROR"),
    ("BR-012", "A matched purchase order must not be exceeded", "ERROR"),
)


class InvoiceCandidateData(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)
    invoice_number: str | None = None
    invoice_date: date | None = None
    due_date: date | None = None
    supplier_name: str | None = None
    supplier_tax_id: str | None = None
    purchase_order_number: str | None = None
    currency: str | None = None
    subtotal: Decimal | None = None
    tax: Decimal | None = None
    total: Decimal | None = None
    field_confidence: dict[str, Decimal] = Field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ValidationContext:
    document_type: Literal["AP_INVOICE", "UNSUPPORTED", "UNKNOWN"] = "AP_INVOICE"
    business_date: date = date(2026, 1, 1)
    supported_currencies: frozenset[str] = frozenset({"USD", "EUR", "GBP"})
    amount_tolerance: Decimal = Decimal("0.01")
    purchase_order_threshold: Decimal = Decimal("10000.00")
    critical_confidence_threshold: Decimal = Decimal("0.70")
    duplicate_approved_invoice: bool = False
    supplier_matched: bool = False
    supplier_active: bool | None = None
    purchase_order_matched: bool = False
    purchase_order_authorized_amount: Decimal | None = None


class RuleResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    rule_id: str
    outcome: Literal["PASS", "FAIL", "REVIEW", "NOT_APPLICABLE"]
    reason_code: str | None = None


def validate_candidate_rules(
    candidate: InvoiceCandidateData, context: ValidationContext
) -> list[RuleResult]:
    return [
        _required("BR-001", candidate.invoice_number, "INVOICE_NUMBER_REQUIRED"),
        _not_future(candidate.invoice_date, context.business_date),
        _date_order(candidate.invoice_date, candidate.due_date),
        _total_math(candidate, context.amount_tolerance),
        _currency(candidate.currency, context.supported_currencies),
        _required("BR-006", candidate.supplier_name, "SUPPLIER_NAME_REQUIRED"),
        _boolean_failure(
            "BR-007", context.duplicate_approved_invoice, "APPROVED_INVOICE_DUPLICATE"
        ),
        _purchase_order_required(candidate, context.purchase_order_threshold),
        _critical_confidence(candidate, context.critical_confidence_threshold),
        _document_type(context.document_type),
        _supplier_active(context),
        _purchase_order_amount(candidate, context),
    ]


def failing_reason_codes(results: list[RuleResult]) -> list[str]:
    return [result.rule_id for result in results if result.outcome in {"FAIL", "REVIEW"}]


def _required(rule_id: str, value: str | None, reason: str) -> RuleResult:
    failed = value is None or not value.strip()
    return RuleResult(
        rule_id=rule_id,
        outcome="FAIL" if failed else "PASS",
        reason_code=reason if failed else None,
    )


def _not_future(value: date | None, business_date: date) -> RuleResult:
    if value is None:
        return RuleResult(rule_id="BR-002", outcome="NOT_APPLICABLE")
    failed = value > business_date
    return RuleResult(
        rule_id="BR-002",
        outcome="FAIL" if failed else "PASS",
        reason_code="INVOICE_DATE_IN_FUTURE" if failed else None,
    )


def _date_order(invoice_date: date | None, due_date: date | None) -> RuleResult:
    if invoice_date is None or due_date is None:
        return RuleResult(rule_id="BR-003", outcome="NOT_APPLICABLE")
    failed = due_date < invoice_date
    return RuleResult(
        rule_id="BR-003",
        outcome="FAIL" if failed else "PASS",
        reason_code="DUE_DATE_PRECEDES_INVOICE_DATE" if failed else None,
    )


def _total_math(candidate: InvoiceCandidateData, tolerance: Decimal) -> RuleResult:
    if candidate.subtotal is None or candidate.tax is None or candidate.total is None:
        return RuleResult(rule_id="BR-004", outcome="REVIEW", reason_code="AMOUNTS_INCOMPLETE")
    failed = abs(candidate.total - (candidate.subtotal + candidate.tax)) > tolerance
    return RuleResult(
        rule_id="BR-004",
        outcome="FAIL" if failed else "PASS",
        reason_code="TOTAL_MISMATCH" if failed else None,
    )


def _currency(value: str | None, supported: frozenset[str]) -> RuleResult:
    failed = value is None or value.upper() not in supported
    return RuleResult(
        rule_id="BR-005",
        outcome="FAIL" if failed else "PASS",
        reason_code="CURRENCY_UNSUPPORTED" if failed else None,
    )


def _boolean_failure(rule_id: str, failed: bool, reason: str) -> RuleResult:
    return RuleResult(
        rule_id=rule_id,
        outcome="FAIL" if failed else "PASS",
        reason_code=reason if failed else None,
    )


def _purchase_order_required(candidate: InvoiceCandidateData, threshold: Decimal) -> RuleResult:
    if candidate.total is None or candidate.total <= threshold:
        return RuleResult(rule_id="BR-008", outcome="NOT_APPLICABLE")
    failed = not candidate.purchase_order_number
    return RuleResult(
        rule_id="BR-008",
        outcome="FAIL" if failed else "PASS",
        reason_code="PURCHASE_ORDER_REQUIRED" if failed else None,
    )


def _critical_confidence(candidate: InvoiceCandidateData, threshold: Decimal) -> RuleResult:
    critical = ("invoice_number", "supplier_name", "total")
    low = [
        name for name in critical if candidate.field_confidence.get(name, Decimal("0")) < threshold
    ]
    return RuleResult(
        rule_id="BR-009",
        outcome="REVIEW" if low else "PASS",
        reason_code="CRITICAL_FIELD_LOW_CONFIDENCE" if low else None,
    )


def _document_type(document_type: str) -> RuleResult:
    review = document_type != "AP_INVOICE"
    return RuleResult(
        rule_id="BR-010",
        outcome="REVIEW" if review else "PASS",
        reason_code="DOCUMENT_TYPE_REQUIRES_REVIEW" if review else None,
    )


def _supplier_active(context: ValidationContext) -> RuleResult:
    if not context.supplier_matched:
        return RuleResult(rule_id="BR-011", outcome="NOT_APPLICABLE")
    failed = context.supplier_active is not True
    return RuleResult(
        rule_id="BR-011",
        outcome="FAIL" if failed else "PASS",
        reason_code="MATCHED_SUPPLIER_INACTIVE" if failed else None,
    )


def _purchase_order_amount(
    candidate: InvoiceCandidateData, context: ValidationContext
) -> RuleResult:
    if not context.purchase_order_matched:
        return RuleResult(rule_id="BR-012", outcome="NOT_APPLICABLE")
    if candidate.total is None or context.purchase_order_authorized_amount is None:
        return RuleResult(rule_id="BR-012", outcome="REVIEW", reason_code="PO_AMOUNT_INCOMPLETE")
    failed = candidate.total > context.purchase_order_authorized_amount
    return RuleResult(
        rule_id="BR-012",
        outcome="FAIL" if failed else "PASS",
        reason_code="PURCHASE_ORDER_AMOUNT_EXCEEDED" if failed else None,
    )


def safe_candidate_data(value: dict[str, object]) -> InvoiceCandidateData:
    try:
        return InvoiceCandidateData.model_validate(value)
    except (ValueError, InvalidOperation):
        return InvoiceCandidateData()


def normalize_document_type(
    value: str,
) -> Literal["AP_INVOICE", "UNSUPPORTED", "UNKNOWN"]:
    if value == "AP_INVOICE":
        return "AP_INVOICE"
    if value == "UNSUPPORTED":
        return "UNSUPPORTED"
    return "UNKNOWN"
