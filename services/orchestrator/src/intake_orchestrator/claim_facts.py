"""Load tenant-scoped facts for claim rules (policy, vehicles, duplicates, page segments).

Shared by the advisory agent `validate_candidate` tool and authoritative parent validation so
both evaluate CR rules against identical inputs.
"""

from __future__ import annotations

from datetime import date
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from intake_api.domain.enums import ClaimKeyStatus
from intake_api.domain.models import (
    ClaimIntakeKey,
    DocumentPageSegment,
    Policy,
    PolicyVehicle,
)
from intake_orchestrator.claim_validation import (
    ClaimCandidateData,
    ClaimValidationContext,
    PolicyFacts,
    VehicleFacts,
)


async def load_claim_context(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    document_id: UUID,
    data: ClaimCandidateData,
    business_date: date,
) -> ClaimValidationContext:
    policy = (
        await session.scalar(
            select(Policy).where(
                Policy.tenant_id == tenant_id, Policy.policy_number == data.policy_number
            )
        )
        if data.policy_number
        else None
    )
    vehicles: tuple[VehicleFacts, ...] = ()
    if policy is not None:
        vehicles = tuple(
            VehicleFacts(
                vin=vehicle.vin,
                model_year=vehicle.model_year,
                make=vehicle.make,
                actual_cash_value=vehicle.actual_cash_value,
            )
            for vehicle in await session.scalars(
                select(PolicyVehicle)
                .where(PolicyVehicle.tenant_id == tenant_id, PolicyVehicle.policy_id == policy.id)
                .order_by(PolicyVehicle.vehicle_seq)
            )
        )
    return ClaimValidationContext(
        business_date=business_date,
        policy=(
            PolicyFacts(
                policy_number=policy.policy_number,
                effective_date=policy.effective_date,
                expiration_date=policy.expiration_date,
                status=policy.status.value,
                cancellation_date=policy.cancellation_date,
                collision_deductible=policy.collision_deductible,
                comprehensive_deductible=policy.comprehensive_deductible,
            )
            if policy is not None
            else None
        ),
        vehicles=vehicles,
        duplicate_of_earlier_claim=await duplicate_claim_exists(
            session, tenant_id=tenant_id, document_id=document_id, data=data
        ),
        segment_labels=await latest_segment_labels(
            session, tenant_id=tenant_id, document_id=document_id
        ),
    )


async def duplicate_claim_exists(
    session: AsyncSession, *, tenant_id: UUID, document_id: UUID, data: ClaimCandidateData
) -> bool:
    """CR-006: another document already holds an active key with this policy, VIN, and date."""
    if not data.policy_number or not data.vin_reported or data.loss_date is None:
        return False
    found = await session.scalar(
        select(ClaimIntakeKey.id)
        .where(
            ClaimIntakeKey.tenant_id == tenant_id,
            ClaimIntakeKey.document_id != document_id,
            ClaimIntakeKey.policy_number == data.policy_number,
            ClaimIntakeKey.vin == data.vin_reported.strip().upper(),
            ClaimIntakeKey.loss_date == data.loss_date,
            ClaimIntakeKey.status == ClaimKeyStatus.ACTIVE,
        )
        .limit(1)
    )
    return found is not None


async def latest_segment_labels(
    session: AsyncSession, *, tenant_id: UUID, document_id: UUID
) -> tuple[str, ...]:
    latest_run = await session.scalar(
        select(DocumentPageSegment.agent_run_id)
        .where(
            DocumentPageSegment.tenant_id == tenant_id,
            DocumentPageSegment.document_id == document_id,
        )
        .order_by(DocumentPageSegment.created_at.desc())
        .limit(1)
    )
    if latest_run is None:
        return ()
    rows = await session.scalars(
        select(DocumentPageSegment.segment_label)
        .where(
            DocumentPageSegment.tenant_id == tenant_id,
            DocumentPageSegment.agent_run_id == latest_run,
        )
        .order_by(DocumentPageSegment.page_number)
    )
    return tuple(label.value for label in rows)
