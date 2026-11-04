"""Load tenant-scoped facts for brief rules: SOWs for the brand, lead times, duplicates, pages."""

from __future__ import annotations

from datetime import date
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from intake_api.domain.enums import ClaimKeyStatus
from intake_api.domain.models import AssetLeadTime, BriefIntakeKey, SowBrand, StatementOfWork
from intake_orchestrator.brief_validation import (
    BriefCandidateData,
    BriefValidationContext,
    LeadTime,
    SowFacts,
    resolve_sow,
)
from intake_orchestrator.claim_facts import latest_segment_labels


def campaign_key(name: str) -> str:
    return " ".join(name.casefold().split())[:200]


async def brand_sows(
    session: AsyncSession, *, tenant_id: UUID, brand: str | None
) -> tuple[SowFacts, ...]:
    if not brand:
        return ()
    rows = await session.scalars(
        select(StatementOfWork)
        .join(
            SowBrand,
            (SowBrand.sow_id == StatementOfWork.id)
            & (SowBrand.tenant_id == StatementOfWork.tenant_id),
        )
        .where(StatementOfWork.tenant_id == tenant_id, SowBrand.brand == brand.strip())
        .order_by(StatementOfWork.end_date.desc())
    )
    return tuple(
        SowFacts(
            sow_number=sow.sow_number,
            status=sow.status.value,
            start_date=sow.start_date,
            end_date=sow.end_date,
            total_fees=sow.total_fees,
            committed_fees=sow.committed_fees,
            services=frozenset(sow.services_in_scope),
            regulated_category=sow.regulated_category.value,
        )
        for sow in rows
    )


async def load_brief_context(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    document_id: UUID,
    data: BriefCandidateData,
    business_date: date,
) -> BriefValidationContext:
    sows = await brand_sows(session, tenant_id=tenant_id, brand=data.brand)
    lead_times = {
        row.asset_type: LeadTime(row.service, row.min_business_days)
        for row in await session.scalars(
            select(AssetLeadTime).where(AssetLeadTime.tenant_id == tenant_id)
        )
    }
    sow = resolve_sow(data, sows)
    return BriefValidationContext(
        business_date=business_date,
        brand_sows=sows,
        lead_times=lead_times,
        duplicate_of_earlier_brief=await duplicate_brief_exists(
            session,
            tenant_id=tenant_id,
            document_id=document_id,
            sow_number=sow.sow_number if sow else None,
            data=data,
        ),
        segment_labels=await latest_segment_labels(
            session, tenant_id=tenant_id, document_id=document_id
        ),
    )


async def duplicate_brief_exists(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    document_id: UUID,
    sow_number: str | None,
    data: BriefCandidateData,
) -> bool:
    """CB-011: an EARLIER brief document holds an active key with this SOW, campaign, and launch."""
    if not sow_number or not data.campaign_name or data.launch_date is None:
        return False
    own_created_at = await session.scalar(
        select(BriefIntakeKey.created_at).where(
            BriefIntakeKey.tenant_id == tenant_id, BriefIntakeKey.document_id == document_id
        )
    )
    statement = select(BriefIntakeKey.id).where(
        BriefIntakeKey.tenant_id == tenant_id,
        BriefIntakeKey.document_id != document_id,
        BriefIntakeKey.sow_number == sow_number,
        BriefIntakeKey.campaign_key == campaign_key(data.campaign_name),
        BriefIntakeKey.launch_date == data.launch_date,
        BriefIntakeKey.status == ClaimKeyStatus.ACTIVE,
    )
    if own_created_at is not None:
        statement = statement.where(BriefIntakeKey.created_at < own_created_at)
    return await session.scalar(statement.limit(1)) is not None
