from pydantic import SecretStr

from intake_api.infrastructure.config import Settings
from intake_integration_worker.worker import resolve_hook_url


def _settings() -> Settings:
    return Settings(
        zapier_hook_url=SecretStr("https://hooks.example/default"),
        zapier_hook_url_routed=SecretStr("https://hooks.example/alerts"),
        zapier_hook_url_campaign_brief=SecretStr("https://hooks.example/briefs"),
    )


def test_events_route_to_their_own_zap_with_a_default_fallback() -> None:
    settings = _settings()

    def url(payload: dict[str, object]) -> str:
        resolved = resolve_hook_url(settings, payload)
        assert resolved is not None
        return resolved.get_secret_value()

    assert url({"event_type": "DOCUMENT_ROUTED", "document_type": "CAMPAIGN_BRIEF"}).endswith(
        "alerts"
    )
    assert url({"event_type": "DOCUMENT_APPROVED", "document_type": "CAMPAIGN_BRIEF"}).endswith(
        "briefs"
    )
    assert url({"event_type": "DOCUMENT_APPROVED", "document_type": "AP_INVOICE"}).endswith(
        "default"
    )


def test_nothing_configured_means_no_destination() -> None:
    assert resolve_hook_url(Settings(), {"document_type": "AP_INVOICE"}) is None


def test_blank_values_from_a_copied_env_file_count_as_unset() -> None:
    settings = Settings(
        zapier_hook_url=SecretStr("https://hooks.example/default"),
        zapier_hook_url_routed=SecretStr(""),
        zapier_hook_url_ap_invoice=SecretStr("  "),
    )

    resolved = resolve_hook_url(
        settings, {"event_type": "DOCUMENT_ROUTED", "document_type": "AP_INVOICE"}
    )

    assert resolved is not None
    assert resolved.get_secret_value().endswith("default")
