"""Typed reviewer corrections for claim-packet fields (ADR 0008)."""

from datetime import date
from decimal import Decimal, InvalidOperation

from intake_api.application.problems import InvalidUpload

CLAIM_STRING_FIELDS = {
    "policy_number",
    "named_insured",
    "cause_of_loss",
    "vehicle_make",
    "vehicle_model",
    "police_report_number",
    "estimate_number",
}
CLAIM_VIN_FIELDS = {"vin_reported", "vin_on_estimate"}
CLAIM_DATE_FIELDS = {"loss_date", "report_date"}
CLAIM_MONEY_FIELDS = {
    "estimate_parts",
    "estimate_labor",
    "estimate_materials",
    "estimate_tax",
    "estimate_total",
}
CLAIM_BOOLEAN_FIELDS = {
    "injury_indicated_on_form",
    "injury_mentioned_in_correspondence",
    "third_party_involved",
}
CLAIM_REVIEW_FIELDS = (
    CLAIM_STRING_FIELDS
    | CLAIM_VIN_FIELDS
    | CLAIM_DATE_FIELDS
    | CLAIM_MONEY_FIELDS
    | CLAIM_BOOLEAN_FIELDS
    | {"coverage", "vehicle_year"}
)


def normalize_claim_correction(field: str, value: str | None) -> object:
    if field not in CLAIM_REVIEW_FIELDS:
        raise InvalidUpload(f"Unsupported claim correction field: {field}", "invalid-correction")
    if value is None:
        return None
    text = value.strip()
    if field in CLAIM_MONEY_FIELDS:
        try:
            amount = Decimal(text)
        except InvalidOperation as exc:
            raise InvalidUpload(f"{field} must be a decimal string.", "invalid-correction") from exc
        if not amount.is_finite() or amount < 0:
            raise InvalidUpload(f"{field} must be a non-negative amount.", "invalid-correction")
        return str(amount)
    if field in CLAIM_DATE_FIELDS:
        try:
            return date.fromisoformat(text).isoformat()
        except ValueError as exc:
            raise InvalidUpload(f"{field} must be an ISO date.", "invalid-correction") from exc
    if field in CLAIM_BOOLEAN_FIELDS:
        lowered = text.casefold()
        if lowered not in {"true", "false"}:
            raise InvalidUpload(f"{field} must be true or false.", "invalid-correction")
        return lowered == "true"
    if field == "coverage":
        if text.upper() not in {"COLLISION", "COMPREHENSIVE"}:
            raise InvalidUpload(
                "coverage must be COLLISION or COMPREHENSIVE.", "invalid-correction"
            )
        return text.upper()
    if field == "vehicle_year":
        if not text.isdigit() or not 1980 <= int(text) <= 2100:
            raise InvalidUpload("vehicle_year must be a four-digit year.", "invalid-correction")
        return int(text)
    if field in CLAIM_VIN_FIELDS:
        vin = text.upper()
        if not vin.isalnum() or not 1 <= len(vin) <= 20:
            raise InvalidUpload(f"{field} must be 1 to 20 letters or digits.", "invalid-correction")
        return vin
    if not text or len(text) > 200:
        raise InvalidUpload(f"{field} must contain 1 to 200 characters.", "invalid-correction")
    return text
