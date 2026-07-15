from typing import Annotated

from fastapi import APIRouter, Depends, File, Header, Request, Response, UploadFile, status

from intake_api.api.dependencies import get_identity, get_intake_service
from intake_api.application.identity import RequestIdentity
from intake_api.application.intake import IntakeService
from intake_api.application.schemas import DocumentCreated, ProblemDetails

router = APIRouter(prefix="/api/v1/documents", tags=["documents"])


@router.post(
    "",
    response_model=DocumentCreated,
    status_code=status.HTTP_201_CREATED,
    responses={
        400: {"model": ProblemDetails},
        401: {"model": ProblemDetails},
        409: {"model": ProblemDetails},
        413: {"model": ProblemDetails},
        422: {"model": ProblemDetails},
        503: {"model": ProblemDetails},
    },
)
def create_document(
    request: Request,
    response: Response,
    file: Annotated[UploadFile, File(description="PDF document")],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)],
    identity: Annotated[RequestIdentity, Depends(get_identity)],
    service: Annotated[IntakeService, Depends(get_intake_service)],
) -> DocumentCreated:
    result = service.create_document(
        upload=file,
        idempotency_key=idempotency_key,
        correlation_id=request.state.correlation_id,
        identity=identity,
    )
    response.status_code = result.response_status
    response.headers["Idempotent-Replayed"] = "true" if result.replayed else "false"
    return result.response
