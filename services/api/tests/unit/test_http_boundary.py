from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from intake_api.application.demo_sessions import DemoSessionStore
from intake_api.domain.enums import UserRole
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


def demo_client(tmp_path: Path) -> TestClient:
    return TestClient(
        create_app(
            Settings(
                database_url="postgresql+psycopg://unused@localhost/unused",
                storage_root=tmp_path,
                demo_role_login_enabled=True,
                demo_session_ttl_seconds=3600,
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


def test_production_configuration_rejects_demo_role_login() -> None:
    with pytest.raises(ValidationError, match="role authentication is forbidden"):
        Settings(environment="production", demo_role_login_enabled=True)


def test_demo_role_session_authenticates_without_exposing_bearer_token(tmp_path: Path) -> None:
    with demo_client(tmp_path) as test_client:
        options = test_client.get("/api/v1/demo-auth/options")
        connected = test_client.post(
            "/api/v1/demo-auth/session",
            json={"role": "OPERATIONS_ADMIN"},
        )
        session = test_client.get("/api/v1/session")

    assert options.status_code == 200
    assert options.json()["roles"] == [
        "SUBMITTER",
        "REVIEWER",
        "APPROVER",
        "OPERATIONS_ADMIN",
        "AUDITOR",
    ]
    assert connected.status_code == 200
    assert connected.json()["role"] == "OPERATIONS_ADMIN"
    assert "token" not in connected.json()
    assert session.status_code == 200
    assert session.json()["role"] == "OPERATIONS_ADMIN"
    cookie = connected.headers["set-cookie"]
    assert "intake_demo_session=" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie
    assert "Path=/api" in cookie


def test_demo_session_logout_revokes_cookie(tmp_path: Path) -> None:
    with demo_client(tmp_path) as test_client:
        test_client.post("/api/v1/demo-auth/session", json={"role": "REVIEWER"})
        disconnected = test_client.delete("/api/v1/demo-auth/session")
        session = test_client.get("/api/v1/session")

    assert disconnected.status_code == 204
    assert session.status_code == 401


def test_demo_auth_routes_are_absent_when_disabled(tmp_path: Path) -> None:
    with client(tmp_path) as test_client:
        response = test_client.get("/api/v1/demo-auth/options")

    assert response.status_code == 404


def test_demo_session_store_bounds_active_sessions() -> None:
    store = DemoSessionStore(ttl_seconds=3600, max_active=1)
    first = store.create(UserRole.SUBMITTER)
    second = store.create(UserRole.AUDITOR)

    assert store.resolve(first) is None
    assert store.resolve(second) == UserRole.AUDITOR


def test_live_provider_configuration_requires_explicit_model_name() -> None:
    with pytest.raises(ValidationError, match="AGENT_MODEL must be explicitly configured"):
        Settings(
            agent_provider="openai",
            agent_model="deterministic-stub-v1",
            openai_api_key="test-only-key",
        )
