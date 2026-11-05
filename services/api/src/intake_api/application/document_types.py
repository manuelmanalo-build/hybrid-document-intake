"""Per-document-type API behavior (ADR 0009). AP invoices use the built-in defaults."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import cast

from pydantic import BaseModel

from intake_api.application.brief_corrections import normalize_brief_correction
from intake_api.application.claim_corrections import normalize_claim_correction
from intake_api.application.problems import ResourceNotFound
from intake_api.domain.enums import ReviewQueue
from intake_api.infrastructure.config import Settings

CorrectionNormalizer = Callable[[str, str | None], object]

CORRECTION_NORMALIZERS: Mapping[str, CorrectionNormalizer] = {
    "CLAIM_PACKET": normalize_claim_correction,
    "CAMPAIGN_BRIEF": normalize_brief_correction,
}


class ItemSummary(BaseModel):
    """Type-neutral queue-row summary; each pack decides what fills each slot."""

    reference: str | None
    party: str | None
    subject: str | None
    event_label: str
    event_date: str | None
    context: str | None
    amount: str | None
    item_count: int | None


@dataclass(frozen=True, slots=True)
class ReviewDisplay:
    rule_version: str
    queues: frozenset[ReviewQueue]
    summary: Callable[[Mapping[str, object]], ItemSummary]
    target_hours: Callable[[Settings, ReviewQueue], int]


def _text(value: object) -> str | None:
    return None if value is None or value == "" else str(value)


def _claim_summary(data: Mapping[str, object]) -> ItemSummary:
    vehicle = " ".join(
        str(part)
        for part in (data.get("vehicle_year"), data.get("vehicle_make"), data.get("vehicle_model"))
        if part
    )
    return ItemSummary(
        reference=_text(data.get("policy_number")),
        party=_text(data.get("named_insured")),
        subject=vehicle or None,
        event_label="Loss",
        event_date=_text(data.get("loss_date")),
        context=_text(data.get("cause_of_loss")),
        amount=_text(data.get("estimate_total")),
        item_count=None,
    )


def _brief_summary(data: Mapping[str, object]) -> ItemSummary:
    client, brand = _text(data.get("client_name")), _text(data.get("brand"))
    deliverables = data.get("deliverables")
    return ItemSummary(
        reference=_text(data.get("sow_number_stated")),
        party=" · ".join(part for part in (client, brand) if part) or None,
        subject=_text(data.get("campaign_name")),
        event_label="Launch",
        event_date=_text(data.get("launch_date")),
        context=_text(data.get("primary_kpi")),
        amount=_text(data.get("budget_total")),
        item_count=len(cast(list[object], deliverables))
        if isinstance(deliverables, list)
        else None,
    )


def _claim_hours(settings: Settings, queue: ReviewQueue) -> int:
    return {
        ReviewQueue.FAST_TRACK: settings.claims_sla_fast_track_hours,
        ReviewQueue.BODILY_INJURY_ADJUSTER: settings.claims_sla_bodily_injury_hours,
        ReviewQueue.BELOW_DEDUCTIBLE_NOTICE: settings.claims_sla_notice_hours,
    }.get(queue, settings.claims_sla_standard_hours)


def _brief_hours(settings: Settings, queue: ReviewQueue) -> int:
    return {
        ReviewQueue.RUSH_REVIEW: settings.briefs_sla_rush_hours,
        ReviewQueue.NEEDS_CLIENT_INFO: settings.briefs_sla_needs_info_hours,
        ReviewQueue.READY_FOR_PLANNING: settings.briefs_sla_ready_hours,
    }.get(queue, settings.briefs_sla_standard_hours)


CLAIM_QUEUES = frozenset(
    {
        ReviewQueue.FAST_TRACK,
        ReviewQueue.DESK_ADJUSTER,
        ReviewQueue.COVERAGE_REVIEW,
        ReviewQueue.SIU_REFERRAL,
        ReviewQueue.BODILY_INJURY_ADJUSTER,
        ReviewQueue.TOTAL_LOSS_REVIEW,
        ReviewQueue.BELOW_DEDUCTIBLE_NOTICE,
        ReviewQueue.DUPLICATE_REVIEW,
    }
)
BRIEF_QUEUES = frozenset(
    {
        ReviewQueue.READY_FOR_PLANNING,
        ReviewQueue.NEEDS_CLIENT_INFO,
        ReviewQueue.SCOPE_REVIEW,
        ReviewQueue.LEGAL_REVIEW,
        ReviewQueue.RUSH_REVIEW,
        ReviewQueue.TRAFFIC_REVIEW,
        ReviewQueue.DUPLICATE_REVIEW,
    }
)

REVIEW_DISPLAYS: Mapping[str, ReviewDisplay] = {
    "CLAIM_PACKET": ReviewDisplay("claim-rules-v1", CLAIM_QUEUES, _claim_summary, _claim_hours),
    "CAMPAIGN_BRIEF": ReviewDisplay("brief-rules-v1", BRIEF_QUEUES, _brief_summary, _brief_hours),
}


def display_for(document_type: str) -> ReviewDisplay:
    display = REVIEW_DISPLAYS.get(document_type)
    if display is None:
        raise ResourceNotFound("That document type has no review queues.")
    return display
