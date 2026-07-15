import secrets
from typing import Annotated

from fastapi import Header, Request

from intake_api.application.identity import RequestIdentity, configured_actor
from intake_api.application.intake import IntakeService
from intake_api.application.integrations import IntegrationAdminService
from intake_api.application.problems import AuthenticationRequired, AuthenticationUnavailable
from intake_api.application.query import QueryService
from intake_api.application.review import ReviewService
from intake_api.domain.enums import UserRole


def get_identity(
    request: Request,
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> RequestIdentity:
    settings = request.app.state.settings
    configured = {
        role: token
        for role, token in {
            UserRole.SUBMITTER: settings.demo_submitter_token,
            UserRole.REVIEWER: settings.demo_reviewer_token,
            UserRole.APPROVER: settings.demo_approver_token,
            UserRole.OPERATIONS_ADMIN: settings.demo_operations_admin_token,
            UserRole.AUDITOR: settings.demo_auditor_token,
        }.items()
        if token is not None
    }
    if not configured:
        raise AuthenticationUnavailable
    scheme, separator, supplied_token = (authorization or "").partition(" ")
    if separator != " " or scheme.lower() != "bearer" or not supplied_token:
        raise AuthenticationRequired
    role = next(
        (
            candidate_role
            for candidate_role, token in configured.items()
            if secrets.compare_digest(token.get_secret_value(), supplied_token)
        ),
        None,
    )
    if role is None:
        raise AuthenticationRequired
    return configured_actor(settings, role)


def get_intake_service(request: Request) -> IntakeService:
    return request.app.state.intake_service


def get_review_service(request: Request) -> ReviewService:
    return request.app.state.review_service


def get_integration_admin_service(request: Request) -> IntegrationAdminService:
    return request.app.state.integration_admin_service


def get_query_service(request: Request) -> QueryService:
    return request.app.state.query_service
