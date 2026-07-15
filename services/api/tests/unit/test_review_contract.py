from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from intake_api.application.identity import RequestIdentity
from intake_api.application.problems import AuthorizationDenied, InvalidUpload
from intake_api.application.review import FieldCorrectionCommand, _normalize_value, _require_role
from intake_api.domain.enums import UserRole


def identity(role: UserRole) -> RequestIdentity:
    return RequestIdentity(tenant_id=uuid4(), user_id=uuid4(), subject="unit-reviewer", role=role)


def test_review_correction_types_preserve_decimal_and_date_semantics() -> None:
    assert Decimal(str(_normalize_value("total", "1360.00"))) == Decimal("1360.00")
    assert _normalize_value("invoice_date", "2026-07-14") == "2026-07-14"
    assert _normalize_value("currency", "usd") == "USD"


@pytest.mark.parametrize(
    ("field", "value"),
    [("total", "NaN"), ("invoice_date", "07/14/2026"), ("currency", "US")],
)
def test_review_correction_rejects_invalid_typed_values(field: str, value: str) -> None:
    with pytest.raises(InvalidUpload):
        _normalize_value(field, value)


def test_submitter_cannot_use_review_operations() -> None:
    with pytest.raises(AuthorizationDenied):
        _require_role(identity(UserRole.SUBMITTER), {UserRole.REVIEWER})


def test_operations_admin_has_no_human_decision_authority() -> None:
    with pytest.raises(AuthorizationDenied):
        _require_role(
            identity(UserRole.OPERATIONS_ADMIN),
            {UserRole.REVIEWER, UserRole.APPROVER},
        )


def test_review_correction_value_is_bounded_before_checkpointing() -> None:
    with pytest.raises(ValidationError, match="at most 500 characters"):
        FieldCorrectionCommand(field="total", new_value="1" * 501, reason="bounded")
