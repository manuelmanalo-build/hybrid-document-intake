from datetime import UTC, datetime
from uuid import UUID

from intake_api.domain.enums import ActorType, AuditEventType
from intake_api.domain.models import AuditEvent
from intake_orchestrator.identifiers import stable_id


def workflow_audit(
    *,
    kind: str,
    tenant_id: UUID,
    document_id: UUID,
    graph_run_id: UUID,
    event_type: AuditEventType,
    metadata: dict[str, object],
) -> AuditEvent:
    return AuditEvent(
        id=stable_id(f"workflow-audit-{kind}", str(graph_run_id)),
        tenant_id=tenant_id,
        document_id=document_id,
        graph_run_id=graph_run_id,
        event_type=event_type,
        actor_type=ActorType.WORKER,
        actor_id="langgraph-orchestrator",
        correlation_id=str(graph_run_id),
        sanitized_metadata=metadata,
        created_at=datetime.now(UTC),
    )
