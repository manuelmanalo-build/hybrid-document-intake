"""Tenant facts for CDP rules: agreements, schema, connectors, duplicates, scan findings."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from intake_api.domain.enums import ClaimKeyStatus
from intake_api.domain.models import (
    CanonicalAttribute,
    CdpConnector,
    DataAgreement,
    OnboardingIntakeKey,
    PiiScanFinding,
)
from intake_orchestrator.cdp_validation import (
    AgreementFacts,
    AttributeFacts,
    ConnectorFacts,
    OnboardingCandidateData,
    OnboardingValidationContext,
    resolve_agreement,
)
from intake_orchestrator.claim_facts import latest_segment_labels


def source_key(name: str) -> str:
    return " ".join(name.casefold().split())[:200]


async def client_agreements(
    session: AsyncSession, *, tenant_id: UUID, client_name: str | None
) -> tuple[AgreementFacts, ...]:
    if not client_name:
        return ()
    rows = await session.scalars(
        select(DataAgreement)
        .where(
            DataAgreement.tenant_id == tenant_id, DataAgreement.client_name == client_name.strip()
        )
        .order_by(DataAgreement.end_date.desc())
    )
    return tuple(
        AgreementFacts(
            agreement_number=row.agreement_number,
            status=row.status.value,
            start_date=row.start_date,
            end_date=row.end_date,
            regions=frozenset(row.regions),
            lawful_bases=frozenset(row.lawful_bases),
            sensitive_categories=frozenset(row.sensitive_categories),
            max_retention_days=row.max_retention_days,
        )
        for row in rows
    )


async def load_onboarding_context(
    session: AsyncSession, *, tenant_id: UUID, document_id: UUID, data: OnboardingCandidateData
) -> OnboardingValidationContext:
    agreements = await client_agreements(session, tenant_id=tenant_id, client_name=data.client_name)
    attributes = {
        row.attribute: AttributeFacts(row.data_type, row.personal_class, row.identity_key)
        for row in await session.scalars(
            select(CanonicalAttribute).where(CanonicalAttribute.tenant_id == tenant_id)
        )
    }
    connectors = {
        row.connector_type: ConnectorFacts(frozenset(row.cadences), row.min_business_days)
        for row in await session.scalars(
            select(CdpConnector).where(CdpConnector.tenant_id == tenant_id)
        )
    }
    findings, without_text = await latest_scan_findings(
        session, tenant_id=tenant_id, document_id=document_id
    )
    agreement = resolve_agreement(data, agreements)
    return OnboardingValidationContext(
        agreements=agreements,
        attributes=attributes,
        connectors=connectors,
        duplicate_of_earlier_request=await duplicate_request_exists(
            session,
            tenant_id=tenant_id,
            document_id=document_id,
            agreement_number=agreement.agreement_number if agreement else None,
            data=data,
        ),
        segment_labels=await latest_segment_labels(
            session, tenant_id=tenant_id, document_id=document_id
        ),
        page_findings=findings,
        pages_without_text=without_text,
    )


async def latest_scan_findings(
    session: AsyncSession, *, tenant_id: UUID, document_id: UUID
) -> tuple[dict[int, dict[str, int]], frozenset[int]]:
    latest_run = await session.scalar(
        select(PiiScanFinding.agent_run_id)
        .where(PiiScanFinding.tenant_id == tenant_id, PiiScanFinding.document_id == document_id)
        .order_by(PiiScanFinding.created_at.desc())
        .limit(1)
    )
    if latest_run is None:
        return {}, frozenset()
    findings: dict[int, dict[str, int]] = {}
    without_text: set[int] = set()
    for row in await session.scalars(
        select(PiiScanFinding).where(
            PiiScanFinding.tenant_id == tenant_id, PiiScanFinding.agent_run_id == latest_run
        )
    ):
        if row.identifier_type == "NO_TEXT_LAYER":
            without_text.add(row.page_number)
        else:
            findings.setdefault(row.page_number, {})[row.identifier_type] = row.occurrences
    return findings, frozenset(without_text)


async def duplicate_request_exists(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    document_id: UUID,
    agreement_number: str | None,
    data: OnboardingCandidateData,
) -> bool:
    """CD-014: an EARLIER document holds an active key for this agreement, connector, and source."""
    if not agreement_number or not data.connector_type or not data.source_name:
        return False
    own_created_at = await session.scalar(
        select(OnboardingIntakeKey.created_at).where(
            OnboardingIntakeKey.tenant_id == tenant_id,
            OnboardingIntakeKey.document_id == document_id,
        )
    )
    statement = select(OnboardingIntakeKey.id).where(
        OnboardingIntakeKey.tenant_id == tenant_id,
        OnboardingIntakeKey.document_id != document_id,
        OnboardingIntakeKey.agreement_number == agreement_number,
        OnboardingIntakeKey.connector_type == data.connector_type,
        OnboardingIntakeKey.source_key == source_key(data.source_name),
        OnboardingIntakeKey.status == ClaimKeyStatus.ACTIVE,
    )
    if own_created_at is not None:
        statement = statement.where(OnboardingIntakeKey.created_at < own_created_at)
    return await session.scalar(statement.limit(1)) is not None
