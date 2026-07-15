from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from intake_api.infrastructure.config import Settings
from intake_api.main import create_app


def client(tmp_path: Path) -> TestClient:
    return TestClient(
        create_app(
            Settings(
                database_url="postgresql+psycopg://unused@localhost/unused",
                storage_root=tmp_path,
                demo_submitter_token="test-token",
            )
        )
    )


def test_generates_correlation_id_and_returns_problem_details(tmp_path: Path) -> None:
    with client(tmp_path) as test_client:
        response = test_client.post(
            "/api/v1/documents", headers={"Authorization": "Bearer test-token"}
        )

    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.headers["X-Correlation-ID"] == response.json()["correlation_id"]


def test_rejects_invalid_correlation_id(tmp_path: Path) -> None:
    with client(tmp_path) as test_client:
        response = test_client.post(
            "/api/v1/documents",
            headers={
                "Authorization": "Bearer test-token",
                "X-Correlation-ID": "contains spaces",
            },
        )

    assert response.status_code == 400
    assert response.json()["type"].endswith("/invalid-correlation-id")


def test_rejects_unverified_submitter(tmp_path: Path) -> None:
    with client(tmp_path) as test_client:
        response = test_client.post("/api/v1/documents")

    assert response.status_code == 401
    assert response.json()["type"].endswith("/authentication-required")


def test_submitter_token_cannot_access_review_tasks(tmp_path: Path) -> None:
    with client(tmp_path) as test_client:
        response = test_client.get(
            "/api/v1/review-tasks", headers={"Authorization": "Bearer test-token"}
        )

    assert response.status_code == 403
    assert response.json()["type"].endswith("/authorization-denied")


def test_production_configuration_rejects_demo_authentication() -> None:
    with pytest.raises(ValidationError, match="forbidden in production"):
        Settings(environment="production", demo_reviewer_token="unsafe-demo-token")


def test_live_provider_configuration_requires_explicit_model_name() -> None:
    with pytest.raises(ValidationError, match="AGENT_MODEL must be explicitly configured"):
        Settings(agent_provider="openai", openai_api_key="test-only-key")
