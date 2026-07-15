from __future__ import annotations

from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from intake_api.application.problems import ProblemException

logger = structlog.get_logger(__name__)
PROBLEM_BASE = "https://example.com/problems/document-intake"


def problem_response(
    *,
    request: Request,
    status: int,
    title: str,
    detail: str,
    problem_type: str,
    extensions: dict[str, Any] | None = None,
) -> JSONResponse:
    correlation_id = getattr(request.state, "correlation_id", "unavailable")
    body: dict[str, Any] = {
        "type": f"{PROBLEM_BASE}/{problem_type}",
        "title": title,
        "status": status,
        "detail": detail,
        "instance": request.url.path,
        "correlation_id": correlation_id,
    }
    if extensions:
        body.update(extensions)
    return JSONResponse(
        status_code=status,
        content=body,
        media_type="application/problem+json",
        headers={"X-Correlation-ID": correlation_id},
    )


def install_problem_handlers(app: FastAPI) -> None:
    @app.exception_handler(ProblemException)
    async def handle_problem(  # pyright: ignore[reportUnusedFunction]
        request: Request, exc: ProblemException
    ) -> JSONResponse:
        return problem_response(
            request=request,
            status=exc.status,
            title=exc.title,
            detail=exc.detail,
            problem_type=exc.problem_type,
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation(  # pyright: ignore[reportUnusedFunction]
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        errors = [
            {"location": ".".join(str(part) for part in error["loc"]), "code": error["type"]}
            for error in exc.errors()
        ]
        return problem_response(
            request=request,
            status=422,
            title="Request validation failed",
            detail="The request did not satisfy the API contract.",
            problem_type="request-validation-failed",
            extensions={"errors": errors},
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http(  # pyright: ignore[reportUnusedFunction]
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        return problem_response(
            request=request,
            status=exc.status_code,
            title="HTTP request failed",
            detail=str(exc.detail),
            problem_type="http-error",
        )

    @app.exception_handler(Exception)
    async def handle_unexpected(  # pyright: ignore[reportUnusedFunction]
        request: Request, exc: Exception
    ) -> JSONResponse:
        logger.exception("unhandled_api_error", error_type=type(exc).__name__)
        return problem_response(
            request=request,
            status=500,
            title="Internal server error",
            detail="The request could not be completed. Use the correlation ID for support.",
            problem_type="internal-server-error",
        )
