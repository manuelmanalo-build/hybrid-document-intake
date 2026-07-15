from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from uuid import uuid4

import structlog.contextvars
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from intake_api.api.problem_handlers import problem_response

_CORRELATION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        supplied = request.headers.get("X-Correlation-ID")
        correlation_id = supplied or str(uuid4())
        if supplied is not None and _CORRELATION_PATTERN.fullmatch(supplied) is None:
            correlation_id = str(uuid4())
            request.state.correlation_id = correlation_id
            return problem_response(
                request=request,
                status=400,
                title="Invalid correlation ID",
                detail=(
                    "X-Correlation-ID must be 1-128 URL-safe identifier characters "
                    "and begin with an alphanumeric character."
                ),
                problem_type="invalid-correlation-id",
            )

        request.state.correlation_id = correlation_id
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(correlation_id=correlation_id)
        try:
            response = await call_next(request)
            response.headers["X-Correlation-ID"] = correlation_id
            return response
        finally:
            structlog.contextvars.clear_contextvars()
