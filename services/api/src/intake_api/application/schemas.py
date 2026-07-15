from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class DocumentCreated(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: UUID
    document_version_id: UUID
    status: Literal["RECEIVED"]
    duplicate_hash_detected: bool
    correlation_id: str = Field(min_length=1, max_length=128)
    graph_run_id: UUID
    graph_run_status: Literal["REQUESTED"]


class ProblemDetails(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: str
    title: str
    status: int
    detail: str
    instance: str
    correlation_id: str


class CreateDocumentResult(BaseModel):
    response: DocumentCreated
    response_status: int
    replayed: bool
