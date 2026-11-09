"""Destination-ready blocks for outbound integration events (Zapier → Jira, QuickBooks, Slack).

Pure functions over business data, so the same shapes are produced by the parent workflow, the
review API, the sample-payload generator, and tests. Every block is additive: consumers that ignore
it keep working. Destinations deduplicate on `event_key` (Jira label) or the invoice number
(QuickBooks `DocNumber`), because Zapier itself does not deduplicate webhooks.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import cast

JIRA_SUMMARY_LIMIT = 255
JIRA_DESCRIPTION_LIMIT = 30_000
QUEUE_LABELS: Mapping[str, str] = {
    "FAST_TRACK": "Fast track",
    "DESK_ADJUSTER": "Desk adjuster",
    "COVERAGE_REVIEW": "Coverage review",
    "SIU_REFERRAL": "SIU referral",
    "BODILY_INJURY_ADJUSTER": "Bodily injury",
    "TOTAL_LOSS_REVIEW": "Total loss review",
    "BELOW_DEDUCTIBLE_NOTICE": "Below deductible",
    "DUPLICATE_REVIEW": "Duplicate review",
    "READY_FOR_PLANNING": "Ready for planning",
    "NEEDS_CLIENT_INFO": "Needs client info",
    "SCOPE_REVIEW": "Scope review",
    "LEGAL_REVIEW": "Legal review",
    "RUSH_REVIEW": "Rush review",
    "TRAFFIC_REVIEW": "Traffic review",
    "READY_TO_BUILD": "Ready to build",
    "AGREEMENT_REVIEW": "Agreement review",
    "PRIVACY_REVIEW": "Privacy review",
    "MAPPING_REVIEW": "Mapping review",
    "ONBOARDING_REVIEW": "Onboarding review",
}
HIGH_PRIORITY_QUEUES = frozenset(
    {
        "BODILY_INJURY_ADJUSTER",
        "SIU_REFERRAL",
        "COVERAGE_REVIEW",
        "PRIVACY_REVIEW",
        "LEGAL_REVIEW",
        "RUSH_REVIEW",
        "AGREEMENT_REVIEW",
    }
)
TYPE_NOUNS: Mapping[str, str] = {
    "AP_INVOICE": "Invoice",
    "CLAIM_PACKET": "Claim",
    "CAMPAIGN_BRIEF": "Brief",
    "CDP_ONBOARDING": "Onboarding request",
}


def jira_label(text: str) -> str:
    """Jira labels cannot contain spaces; keep them short, lowercase, and readable."""
    label = re.sub(r"[^a-z0-9]+", "-", text.casefold()).strip("-")
    return label[:60] or "intake"


def jira_summary(text: str) -> str:
    clean = " ".join(text.split())
    return clean if len(clean) <= JIRA_SUMMARY_LIMIT else f"{clean[: JIRA_SUMMARY_LIMIT - 1]}…"


def event_label(event_key: str) -> str:
    """A stable per-event Jira label; a Zap searches for it before creating, to stay idempotent."""
    return f"intake-{jira_label(event_key)[-24:]}"


def _text(value: object) -> str | None:
    return None if value is None or value == "" else str(value)


def _money(value: object) -> str | None:
    try:
        return None if value in (None, "") else f"{Decimal(str(value)):.2f}"
    except InvalidOperation:
        return None


def _issue(
    key: str,
    summary: str,
    description: str,
    labels: Sequence[str],
    *,
    issue_type: str = "Task",
    priority: str = "Medium",
    due_date: str | None = None,
) -> dict[str, object]:
    return {
        "key": key,
        "issue_type": issue_type,
        "summary": jira_summary(summary),
        "description": description[:JIRA_DESCRIPTION_LIMIT],
        "labels": list(dict.fromkeys(labels)),
        "priority": priority,
        "due_date": due_date,
    }


def invoice_payload(business_data: Mapping[str, object]) -> dict[str, object]:
    """A QuickBooks Online bill: vendor, document number, dates, and lines that sum to the total."""
    number = _text(business_data.get("invoice_number"))
    purchase_order = _text(business_data.get("purchase_order_number"))
    subtotal = _money(business_data.get("subtotal"))
    tax = _money(business_data.get("tax"))
    total = _money(business_data.get("total"))
    lines: list[dict[str, object]] = []
    if subtotal is not None:
        po = f" (PO {purchase_order})" if purchase_order else ""
        lines.append({"description": f"Invoice {number or ''}{po}".strip(), "amount": subtotal})
    if tax is not None and Decimal(tax) != 0:
        lines.append({"description": "Sales tax", "amount": tax})
    if not lines and total is not None:
        lines.append({"description": f"Invoice {number or ''}".strip(), "amount": total})
    lines_total = sum((Decimal(str(line["amount"])) for line in lines), Decimal("0"))
    return {
        "document_type": "AP_INVOICE",
        "accounting_bill": {
            "schema_version": "quickbooks-bill-v1",
            "vendor_name": _text(business_data.get("supplier_name")),
            "vendor_tax_id": _text(business_data.get("supplier_tax_id")),
            "doc_number": number,
            "txn_date": _text(business_data.get("invoice_date")),
            "due_date": _text(business_data.get("due_date")),
            "currency": _text(business_data.get("currency")),
            "memo": f"PO {purchase_order}" if purchase_order else None,
            "lines": lines,
            "total": total,
            "lines_match_total": total is not None and f"{lines_total:.2f}" == total,
        },
    }


def claim_payload(
    business_data: Mapping[str, object],
    *,
    queue: str | None,
    reason_code: str | None,
    flags: Sequence[str],
    event_key: str,
) -> dict[str, object]:
    """An approved claim: a summary plus one Jira issue for the claims project."""
    vehicle = " ".join(
        str(part)
        for part in (
            business_data.get("vehicle_year"),
            business_data.get("vehicle_make"),
            business_data.get("vehicle_model"),
        )
        if part
    )
    insured = _text(business_data.get("named_insured")) or "Unknown insured"
    loss_date = _text(business_data.get("loss_date"))
    claim = {
        "named_insured": insured,
        "policy_number": _text(business_data.get("policy_number")),
        "loss_date": loss_date,
        "cause_of_loss": _text(business_data.get("cause_of_loss")),
        "vehicle": vehicle or None,
        "vin": _text(business_data.get("vin_reported")),
        "estimate_total": _money(business_data.get("estimate_total")),
        "police_report_number": _text(business_data.get("police_report_number")),
        "queue": queue,
        "queue_label": QUEUE_LABELS.get(queue or "", queue),
        "routing_reason": reason_code,
        "flags": list(flags),
    }
    description = "\n".join(
        f"{label}: {value}"
        for label, value in (
            ("Insured", insured),
            ("Policy", claim["policy_number"]),
            ("Date of loss", loss_date),
            ("Cause", claim["cause_of_loss"]),
            ("Vehicle", claim["vehicle"]),
            ("VIN", claim["vin"]),
            ("Estimate", claim["estimate_total"]),
            ("Queue", claim["queue_label"]),
            ("Flags", ", ".join(flags) or "none"),
        )
        if value
    )
    labels = ["claim", jira_label(queue or "unrouted"), event_label(event_key)]
    labels += [jira_label(flag) for flag in flags]
    return {
        "document_type": "CLAIM_PACKET",
        "claim": claim,
        "jira": {
            "schema_version": "jira-issues-v1",
            "epic": None,
            "issues": [
                _issue(
                    "claim",
                    f"FNOL: {insured} · {vehicle or 'vehicle unknown'} · "
                    f"loss {loss_date or 'unknown'}",
                    description,
                    labels,
                    priority="High" if queue in HIGH_PRIORITY_QUEUES else "Medium",
                )
            ],
        },
    }


def brief_jira(work_request: Mapping[str, object], *, event_key: str) -> dict[str, object]:
    """One Jira epic per campaign and one issue per deliverable task."""
    campaign = _text(work_request.get("campaign_name")) or "Untitled campaign"
    brand = _text(work_request.get("brand")) or ""
    description = "\n".join(
        f"{label}: {value}"
        for label, value in (
            ("Client", work_request.get("client_name")),
            ("Brand", brand),
            ("SOW", work_request.get("sow_number")),
            ("Launch", work_request.get("launch_date")),
            ("End", work_request.get("end_date")),
            ("Budget", work_request.get("budget_total")),
            ("KPI", f"{work_request.get('primary_kpi')} → {work_request.get('kpi_target')}"),
        )
        if value
    )
    base_labels = ["campaign", jira_label(campaign), event_label(event_key)]
    issues: list[dict[str, object]] = []
    for task in cast(list[dict[str, object]], work_request.get("tasks") or []):
        details = "\n".join(
            f"{label}: {value}"
            for label, value in (
                ("Deliverable", task.get("asset_type")),
                ("Quantity", task.get("quantity")),
                ("Description", task.get("description")),
                ("Requested in", task.get("requested_in")),
            )
            if value
        )
        issues.append(
            _issue(
                str(task.get("task_key")),
                str(task.get("title")),
                details,
                [*base_labels, jira_label(str(task.get("service") or "unassigned"))],
                due_date=_text(task.get("due_date")),
            )
        )
    return {
        "schema_version": "jira-issues-v1",
        "epic": _issue(
            "epic",
            f"{campaign} ({brand})" if brand else campaign,
            description,
            base_labels,
            issue_type="Epic",
            due_date=_text(work_request.get("launch_date")),
        ),
        "issues": issues,
    }


def cdp_jira(plan: Mapping[str, object], *, event_key: str) -> dict[str, object]:
    """One Jira epic per source and one story per build workstream; the mapping story lists all."""
    source = _text(plan.get("source_name")) or "Data source"
    client = _text(plan.get("client_name")) or ""
    go_live = _text(plan.get("go_live"))
    mappings = cast(list[dict[str, object]], plan.get("mappings") or [])
    mapping_lines = "\n".join(
        f"{item.get('source_field')} → {item.get('attribute')} ({item.get('entity')}, "
        f"{item.get('personal_class')}{', identity key' if item.get('identity_key') else ''})"
        for item in mappings
    )
    excluded = cast(list[str], plan.get("excluded_fields") or [])
    labels = ["cdp-onboarding", jira_label(source), event_label(event_key)]
    issues: list[dict[str, object]] = []
    for task in cast(list[dict[str, object]], plan.get("tasks") or []):
        key = str(task.get("task_key"))
        details = str(task.get("title"))
        if key == "mapping":
            details = f"{details}\n\n{mapping_lines}"
            if excluded:
                details += f"\n\nNot ingested: {', '.join(excluded)}"
        issues.append(
            _issue(
                key,
                f"{source}: {task.get('title')}",
                details,
                [*labels, jira_label(key)],
                issue_type="Story",
                priority="High" if key == "consent" else "Medium",
                due_date=go_live,
            )
        )
    description = "\n".join(
        f"{label}: {value}"
        for label, value in (
            ("Client", client),
            ("Agreement", plan.get("agreement_number")),
            ("Connector", plan.get("connector_type")),
            ("Cadence", plan.get("refresh_cadence")),
            ("Region", plan.get("data_region")),
            ("Lawful basis", plan.get("lawful_basis")),
            ("Retention (days)", plan.get("retention_days")),
            ("Go-live", go_live),
        )
        if value
    )
    return {
        "schema_version": "jira-issues-v1",
        "epic": _issue(
            "epic",
            f"Onboard {source} ({client})" if client else f"Onboard {source}",
            description,
            labels,
            issue_type="Epic",
            due_date=go_live,
        ),
        "issues": issues,
        "mapping_rows": [
            {
                "source_field": item.get("source_field"),
                "attribute": item.get("attribute"),
                "entity": item.get("entity"),
                "personal_class": item.get("personal_class"),
                "identity_key": item.get("identity_key"),
            }
            for item in mappings
        ],
    }


def routed_event(
    *,
    event_key: str,
    document_type: str,
    document_id: str,
    review_task_id: str,
    review_cycle: int,
    queue: str,
    routing_version: str | None,
    routed_by: str,
    reason_code: str,
    flags: Sequence[str],
    summary: Mapping[str, object],
    target_hours: int,
    routed_at: datetime,
) -> dict[str, object]:
    """A routing event for queue alerts (for example a Slack message to the owning team)."""
    queue_label = QUEUE_LABELS.get(queue, queue)
    noun = TYPE_NOUNS.get(document_type, "Document")
    party = summary.get("party") or "Unknown"
    subject = summary.get("subject") or ""
    due_at = routed_at + timedelta(hours=target_hours)
    title = f"{noun} routed to {queue_label}: {party}" + (f" · {subject}" if subject else "")
    reasons = ", ".join(flags) or "no flags"
    return {
        "event_type": "DOCUMENT_ROUTED",
        "event_version": "1.0",
        "event_key": event_key,
        "document_type": document_type,
        "document_id": document_id,
        "review_task_id": review_task_id,
        "review_cycle": review_cycle,
        "queue": queue,
        "queue_label": queue_label,
        "routing_version": routing_version,
        "routed_by": routed_by,
        "reason_code": reason_code,
        "flags": list(flags),
        "priority": "High" if queue in HIGH_PRIORITY_QUEUES else "Normal",
        "service_level": {"target_hours": target_hours, "due_at": due_at.isoformat()},
        "summary": dict(summary),
        "alert": {
            "title": jira_summary(title),
            "text": f"{title}\nWhy: {reasons}\nFirst review due by {due_at:%Y-%m-%d %H:%M} UTC.",
        },
    }
