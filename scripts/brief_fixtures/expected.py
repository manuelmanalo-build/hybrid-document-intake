"""Reference implementation of brief rules CB-001..CB-014 and brief-routing-v1 for ground truth.

Kept deliberately simple and separate from the production rules so the production code can be
tested against independently derived expectations, as the claims slice was.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from scripts.brief_fixtures.data import ASSET_TYPES, SOWS, Brief, Deliverable, Sow


@dataclass(frozen=True)
class Expectation:
    sow_resolved: str | None
    flags: tuple[str, ...]
    queue: str
    reason: str


def business_days(start: date, end: date) -> int:
    """Weekdays after `start` up to and including `end`."""
    days = 0
    current = start
    while current < end:
        current += timedelta(days=1)
        if current.weekday() < 5:
            days += 1
    return days


def resolve_sow(brief: Brief) -> Sow | None:
    candidates = [sow for sow in SOWS if brief.brand in sow.brands]
    if brief.sow_stated:
        stated = [sow for sow in candidates if sow.sow_number == brief.sow_stated]
        if stated:
            return stated[0]
    covering = [sow for sow in candidates if sow.start <= brief.launch <= sow.end]
    if covering:
        return covering[0]
    return max(candidates, key=lambda sow: sow.end) if candidates else None


def expect(brief: Brief, seen: set[tuple[str, str, date]]) -> Expectation:
    sow = resolve_sow(brief)
    flags: list[str] = []
    if sow is None:
        flags.append("SOW_NOT_FOUND")
    elif sow.status != "ACTIVE" or not sow.start <= brief.launch <= sow.end:
        flags.append("SOW_NOT_ACTIVE_AT_LAUNCH")
    if not (brief.objective and brief.audience and brief.kpi and brief.deliverables):
        flags.append("BRIEF_INCOMPLETE")
    if not brief.kpi_target or not any(char.isdigit() for char in brief.kpi_target):
        flags.append("KPI_NOT_MEASURABLE")
    if brief.budget is None:
        flags.append("BUDGET_MISSING")
    elif sow is not None and brief.budget > sow.total_fees - sow.committed_fees:
        flags.append("OVER_SOW_BUDGET")
    everything: list[Deliverable] = [*brief.deliverables, *brief.email_added]
    if sow is not None and any(
        ASSET_TYPES[item.asset_type].service not in sow.services for item in everything
    ):
        flags.append("OUT_OF_SCOPE_SERVICE")
    effective_launch = min(brief.launch, brief.email_launch or brief.launch)
    for item in everything:
        due = min(item.due or effective_launch, effective_launch)
        if business_days(brief.received, due) < ASSET_TYPES[item.asset_type].min_business_days:
            flags.append("TIMELINE_AT_RISK")
            break
    if (brief.email_launch and brief.email_launch != brief.launch) or brief.email_added:
        flags.append("CORRESPONDENCE_CONFLICT")
    if sow is not None and sow.regulated_category != "NONE":
        flags.append("REGULATED_CONTENT")
    key = (sow.sow_number if sow else "", brief.campaign.casefold(), brief.launch)
    if key in seen:
        flags.append("DUPLICATE_BRIEF")
    seen.add(key)
    if not brief.received < brief.launch < brief.end:
        flags.append("DATES_INVALID")

    table = (
        (
            {"DUPLICATE_BRIEF"},
            "DUPLICATE_REVIEW",
            "Same SOW, campaign, and launch as an earlier brief",
        ),
        ({"SOW_NOT_FOUND", "SOW_NOT_ACTIVE_AT_LAUNCH"}, "SCOPE_REVIEW", "No active SOW at launch"),
        ({"OVER_SOW_BUDGET", "OUT_OF_SCOPE_SERVICE"}, "SCOPE_REVIEW", "Change order needed"),
        ({"REGULATED_CONTENT"}, "LEGAL_REVIEW", "Regulated category"),
        (
            {"BRIEF_INCOMPLETE", "KPI_NOT_MEASURABLE", "BUDGET_MISSING"},
            "NEEDS_CLIENT_INFO",
            "Brief is missing required information",
        ),
        ({"TIMELINE_AT_RISK"}, "RUSH_REVIEW", "Production lead time at risk"),
        ({"CORRESPONDENCE_CONFLICT", "DATES_INVALID"}, "TRAFFIC_REVIEW", "Brief needs reconciling"),
    )
    for triggers, queue, reason in table:
        if triggers & set(flags):
            return Expectation(sow.sow_number if sow else None, tuple(flags), queue, reason)
    return Expectation(
        sow.sow_number if sow else None, tuple(flags), "READY_FOR_PLANNING", "Complete and in scope"
    )
