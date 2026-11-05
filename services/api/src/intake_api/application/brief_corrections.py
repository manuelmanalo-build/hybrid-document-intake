"""Typed reviewer corrections for campaign-brief work requests (ADR 0009).

Scalar fields only in v1; deliverable lists are corrected by requesting information from the
client or by re-routing to traffic review.
"""

from datetime import date
from decimal import Decimal, InvalidOperation

from intake_api.application.problems import InvalidUpload

BRIEF_STRING_FIELDS = {
    "client_name",
    "brand",
    "campaign_name",
    "sow_number_stated",
    "objective",
    "primary_kpi",
    "kpi_target",
    "audience",
    "markets",
    "approver",
    "mandatories",
}
BRIEF_DATE_FIELDS = {"received_date", "launch_date", "end_date", "launch_date_in_correspondence"}
BRIEF_MONEY_FIELDS = {"budget_total"}
BRIEF_REVIEW_FIELDS = BRIEF_STRING_FIELDS | BRIEF_DATE_FIELDS | BRIEF_MONEY_FIELDS


def normalize_brief_correction(field: str, value: str | None) -> object:
    if field not in BRIEF_REVIEW_FIELDS:
        raise InvalidUpload(f"Unsupported brief correction field: {field}", "invalid-correction")
    if value is None:
        return None
    text = value.strip()
    if field in BRIEF_MONEY_FIELDS:
        try:
            amount = Decimal(text)
        except InvalidOperation as exc:
            raise InvalidUpload(f"{field} must be a decimal string.", "invalid-correction") from exc
        if not amount.is_finite() or amount < 0:
            raise InvalidUpload(f"{field} must be a non-negative amount.", "invalid-correction")
        return str(amount)
    if field in BRIEF_DATE_FIELDS:
        try:
            return date.fromisoformat(text).isoformat()
        except ValueError as exc:
            raise InvalidUpload(f"{field} must be an ISO date.", "invalid-correction") from exc
    if not text or len(text) > 500:
        raise InvalidUpload(f"{field} must contain 1 to 500 characters.", "invalid-correction")
    return text
