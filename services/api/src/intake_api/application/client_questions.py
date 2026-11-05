"""Draft clarifying questions for an incomplete campaign brief (ADR 0009).

Drafts are derived deterministically from the brief's failed rules and extracted values, so the
same brief always produces the same draft and every question traces to a rule. A reviewer edits
and sends the draft through their own email; the system never contacts a client.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from pydantic import BaseModel

QUESTION_REASONS = frozenset(
    {
        "BRIEF_INCOMPLETE",
        "KPI_NOT_MEASURABLE",
        "BUDGET_MISSING",
        "CRITICAL_FIELD_MISSING",
        "CRITICAL_FIELD_LOW_CONFIDENCE",
        "DATES_INVALID",
        "CORRESPONDENCE_CONFLICT",
    }
)
_REQUIRED = (
    ("objective", "the campaign objective"),
    ("audience", "the target audience"),
    ("primary_kpi", "the primary KPI"),
    ("launch_date", "the launch date"),
)


class ClientQuestionsDraft(BaseModel):
    subject: str
    body: str
    questions: list[str]
    reason_codes: list[str]


def draft_client_questions(
    data: Mapping[str, object], flags: Sequence[str], *, agency: str = "Meridian & Pike"
) -> ClientQuestionsDraft:
    campaign = _text(data.get("campaign_name")) or "your campaign"
    contact = (_text(data.get("approver")) or "").split(",")[0].strip()
    questions: list[str] = []
    reasons = [flag for flag in dict.fromkeys(flags) if flag in QUESTION_REASONS]
    if "BRIEF_INCOMPLETE" in reasons:
        missing = [label for name, label in _REQUIRED if not _text(data.get(name))]
        if not data.get("deliverables"):
            missing.append("the deliverables you need, with quantities and due dates")
        if missing:
            questions.append(f"Could you share {_join(missing)}?")
    if "KPI_NOT_MEASURABLE" in reasons:
        kpi = _text(data.get("primary_kpi")) or "the primary KPI"
        target = _text(data.get("kpi_target"))
        stated = f' (the brief says "{target}")' if target else ""
        questions.append(
            f"What numeric target should we plan against for {kpi.lower()}{stated}? "
            "For example, a percentage lift or a count."
        )
    if "BUDGET_MISSING" in reasons:
        questions.append(
            f"What is the approved budget for {campaign}? If approval is pending, when do you "
            "expect it?"
        )
    if "DATES_INVALID" in reasons:
        questions.append(
            "The launch and end dates appear to be out of order. Could you confirm both dates?"
        )
    if "CORRESPONDENCE_CONFLICT" in reasons:
        questions.append(
            "Your follow-up note changes the brief. Could you confirm the final launch date and "
            "deliverables so we plan against one version?"
        )
    if {"CRITICAL_FIELD_MISSING", "CRITICAL_FIELD_LOW_CONFIDENCE"} & set(reasons):
        questions.append(
            "Could you confirm the client, brand, campaign name, and launch date exactly as they "
            "should appear on the work request?"
        )
    greeting = f"Hi {contact.split()[0]}," if contact else "Hi,"
    numbered = "\n".join(f"{index}. {question}" for index, question in enumerate(questions, 1))
    body = (
        f"{greeting}\n\nThanks for the brief for {campaign}. Before we start planning, we need a "
        f"little more information:\n\n{numbered}\n\nOnce we have these, we'll confirm the plan "
        f"and timeline.\n\nThanks,\n{agency}"
        if questions
        else ""
    )
    return ClientQuestionsDraft(
        subject=f"Questions on the {campaign} brief",
        body=body,
        questions=questions,
        reason_codes=reasons,
    )


def _text(value: object) -> str | None:
    return None if value is None or value == "" else str(value)


def _join(items: list[str]) -> str:
    if len(items) <= 2:
        return " and ".join(items)
    return f"{', '.join(items[:-1])}, and {items[-1]}"
