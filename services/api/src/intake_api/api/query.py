from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse

from intake_api.api.dependencies import get_identity, get_query_service
from intake_api.application.identity import RequestIdentity
from intake_api.application.query import (
    DocumentDetail,
    DocumentSummary,
    OverviewMetrics,
    QueryService,
)

router = APIRouter(prefix="/api/v1", tags=["queries"])


@router.get("/session", response_model=dict[str, str])
def get_session(
    identity: Annotated[RequestIdentity, Depends(get_identity)],
) -> dict[str, str]:
    return {
        "tenant_id": str(identity.tenant_id),
        "user_id": str(identity.user_id),
        "role": identity.role,
        "subject": identity.subject,
    }


@router.get("/documents", response_model=list[DocumentSummary])
def list_documents(
    identity: Annotated[RequestIdentity, Depends(get_identity)],
    service: Annotated[QueryService, Depends(get_query_service)],
) -> list[DocumentSummary]:
    return service.documents(identity)


@router.get("/documents/{document_id}", response_model=DocumentDetail)
def get_document(
    document_id: UUID,
    identity: Annotated[RequestIdentity, Depends(get_identity)],
    service: Annotated[QueryService, Depends(get_query_service)],
) -> DocumentDetail:
    return service.document(document_id, identity)


@router.get("/documents/{document_id}/content", response_class=FileResponse)
def get_document_content(
    document_id: UUID,
    identity: Annotated[RequestIdentity, Depends(get_identity)],
    service: Annotated[QueryService, Depends(get_query_service)],
) -> FileResponse:
    return FileResponse(
        service.document_content(document_id, identity),
        media_type="application/pdf",
        filename=f"document-{document_id}.pdf",
        content_disposition_type="inline",
    )


@router.get("/operations/overview", response_model=OverviewMetrics)
def operations_overview(
    identity: Annotated[RequestIdentity, Depends(get_identity)],
    service: Annotated[QueryService, Depends(get_query_service)],
) -> OverviewMetrics:
    return service.overview(identity)


@router.get("/operations/{resource}", response_model=list[dict[str, object]])
def operations_resource(
    resource: str,
    identity: Annotated[RequestIdentity, Depends(get_identity)],
    service: Annotated[QueryService, Depends(get_query_service)],
) -> list[dict[str, object]]:
    return service.operations_rows(resource, identity)
