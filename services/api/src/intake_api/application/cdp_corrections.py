"""Typed reviewer corrections for CDP onboarding requests (ADR 0010).

Request-level fields in this increment; individual mapping corrections arrive with the mapping
table in the workbench.
"""

from datetime import date

from intake_api.application.problems import InvalidUpload

CDP_STRING_FIELDS = {
    "client_name",
    "agreement_number_stated",
    "source_name",
    "consent_mechanism",
    "data_owner",
}
CDP_DATE_FIELDS = {"received_date", "requested_go_live", "go_live_in_correspondence"}
CDP_INT_FIELDS = {"retention_days", "estimated_records"}
CDP_BOOLEAN_FIELDS = {"declares_no_personal_data", "declares_sensitive_data"}
CDP_CHOICES = {
    "connector_type": {
        "SHOPIFY",
        "SALESFORCE_CRM",
        "SFTP_CSV",
        "SNOWFLAKE_SHARE",
        "GA4",
        "BRAZE",
        "REST_WEBHOOK",
    },
    "refresh_cadence": {"REALTIME", "HOURLY", "DAILY", "WEEKLY", "ONE_TIME"},
    "data_region": {"US", "EU", "UK", "CA", "APAC"},
    "lawful_basis": {"CONSENT", "CONTRACT", "LEGITIMATE_INTEREST"},
}
CDP_REVIEW_FIELDS = (
    CDP_STRING_FIELDS | CDP_DATE_FIELDS | CDP_INT_FIELDS | CDP_BOOLEAN_FIELDS | set(CDP_CHOICES)
)


def normalize_cdp_correction(field: str, value: str | None) -> object:
    if field not in CDP_REVIEW_FIELDS:
        raise InvalidUpload(
            f"Unsupported onboarding correction field: {field}", "invalid-correction"
        )
    if value is None:
        return None
    text = value.strip()
    if field in CDP_DATE_FIELDS:
        try:
            return date.fromisoformat(text).isoformat()
        except ValueError as exc:
            raise InvalidUpload(f"{field} must be an ISO date.", "invalid-correction") from exc
    if field in CDP_INT_FIELDS:
        if not text.isdigit():
            raise InvalidUpload(f"{field} must be a whole number.", "invalid-correction")
        return int(text)
    if field in CDP_BOOLEAN_FIELDS:
        if text.casefold() not in {"true", "false"}:
            raise InvalidUpload(f"{field} must be true or false.", "invalid-correction")
        return text.casefold() == "true"
    if field in CDP_CHOICES:
        if text.upper() not in CDP_CHOICES[field]:
            raise InvalidUpload(f"{field} is not a supported value.", "invalid-correction")
        return text.upper()
    if not text or len(text) > 500:
        raise InvalidUpload(f"{field} must contain 1 to 500 characters.", "invalid-correction")
    return text
