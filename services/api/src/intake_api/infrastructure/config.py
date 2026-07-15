from functools import lru_cache
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import AliasChoices, Field, PositiveInt, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="INTAKE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    app_name: str = "document-intake-api"
    environment: str = "local"
    log_level: str = "INFO"
    database_url: str = "postgresql+psycopg://document_intake@localhost:5432/document_intake"
    storage_root: Path = Path("storage/objects")
    upload_max_bytes: PositiveInt = 10 * 1024 * 1024
    upload_chunk_bytes: PositiveInt = 64 * 1024
    bootstrap_tenant_id: UUID = UUID("00000000-0000-4000-8000-000000000001")
    bootstrap_user_id: UUID = UUID("00000000-0000-4000-8000-000000000002")
    bootstrap_tenant_name: str = "Local Demo Tenant"
    bootstrap_user_subject: str = "local-demo-submitter"
    demo_submitter_token: SecretStr | None = None
    demo_reviewer_token: SecretStr | None = None
    demo_approver_token: SecretStr | None = None
    demo_operations_admin_token: SecretStr | None = None
    demo_auditor_token: SecretStr | None = None
    graph_version: str = "document-intake-v1"
    checkpoint_database_url: str | None = None
    graph_request_max_attempts: PositiveInt = 5
    graph_request_lease_seconds: PositiveInt = 30
    graph_request_poll_seconds: float = Field(default=2.0, gt=0, le=60)
    graph_request_backoff_base_seconds: PositiveInt = 2
    graph_request_backoff_max_seconds: PositiveInt = 60
    agent_provider: Literal["stub", "openai", "gemini"] = Field(
        default="stub", validation_alias=AliasChoices("AGENT_PROVIDER", "INTAKE_AGENT_PROVIDER")
    )
    agent_model: str = Field(
        default="deterministic-stub-v1",
        min_length=1,
        validation_alias=AliasChoices("AGENT_MODEL", "INTAKE_AGENT_MODEL"),
    )
    openai_api_key: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("OPENAI_API_KEY", "INTAKE_OPENAI_API_KEY")
    )
    google_api_key: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("GOOGLE_API_KEY", "INTAKE_GOOGLE_API_KEY")
    )
    agent_max_tool_calls: PositiveInt = Field(
        default=10,
        le=10,
        validation_alias=AliasChoices("AGENT_MAX_TOOL_CALLS", "INTAKE_AGENT_MAX_TOOL_CALLS"),
    )
    agent_max_extraction_attempts: PositiveInt = Field(
        default=3,
        le=3,
        validation_alias=AliasChoices(
            "AGENT_MAX_EXTRACTION_ATTEMPTS", "INTAKE_AGENT_MAX_EXTRACTION_ATTEMPTS"
        ),
    )
    agent_max_supplier_lookups: PositiveInt = 2
    agent_max_purchase_order_lookups: PositiveInt = 2
    agent_total_timeout_seconds: PositiveInt = Field(
        default=120,
        validation_alias=AliasChoices(
            "AGENT_TOTAL_TIMEOUT_SECONDS", "INTAKE_AGENT_TOTAL_TIMEOUT_SECONDS"
        ),
    )
    agent_tool_timeout_seconds: PositiveInt = Field(
        default=30,
        validation_alias=AliasChoices(
            "AGENT_TOOL_TIMEOUT_SECONDS", "INTAKE_AGENT_TOOL_TIMEOUT_SECONDS"
        ),
    )
    agent_model_timeout_seconds: PositiveInt = 30
    zapier_hook_url: SecretStr | None = None
    zapier_auth_token: SecretStr | None = None
    zapier_signing_secret: SecretStr | None = None
    zapier_callback_token: SecretStr | None = None
    webhook_timeout_seconds: float = Field(default=10.0, gt=0, le=60)
    webhook_max_attempts: PositiveInt = Field(default=5, le=20)
    webhook_lease_seconds: PositiveInt = Field(default=30, le=300)
    webhook_poll_seconds: float = Field(default=2.0, gt=0, le=60)
    webhook_backoff_base_seconds: PositiveInt = 2
    webhook_backoff_max_seconds: PositiveInt = 300

    @model_validator(mode="after")
    def validate_agent_provider_credentials(self) -> "Settings":
        if self.environment.casefold() in {"production", "prod"} and any(
            (
                self.demo_submitter_token,
                self.demo_reviewer_token,
                self.demo_approver_token,
                self.demo_operations_admin_token,
                self.demo_auditor_token,
            )
        ):
            raise ValueError("Development bearer authentication is forbidden in production")
        if self.agent_provider == "openai" and self.openai_api_key is None:
            raise ValueError("OPENAI_API_KEY is required when AGENT_PROVIDER=openai")
        if self.agent_provider == "gemini" and self.google_api_key is None:
            raise ValueError("GOOGLE_API_KEY is required when AGENT_PROVIDER=gemini")
        if self.agent_provider != "stub" and self.agent_model == "deterministic-stub-v1":
            raise ValueError("AGENT_MODEL must be explicitly configured for a live provider")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
