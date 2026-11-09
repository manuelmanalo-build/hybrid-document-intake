"""Write sample Zapier payloads for each event and document type to docs/integrations/samples.

The samples are produced by the same builders the platform uses, from the synthetic fixtures, so
they can be pasted into a Zap's test step. Run from the repository root:

    uv run python -m scripts.generate_zapier_samples
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from intake_api.application.integration_payloads import (
    brief_jira,
    cdp_jira,
    claim_payload,
    invoice_payload,
    routed_event,
)

OUT = Path("docs/integrations/samples")


def _envelope(key: str, document_type: str, business_data: dict[str, object]) -> dict[str, object]:
    """The shared DOCUMENT_APPROVED envelope, with the same keys the parent workflow writes."""
    suffix = {
        "AP_INVOICE": "000000000001",
        "CLAIM_PACKET": "000000000002",
        "CAMPAIGN_BRIEF": "000000000003",
        "CDP_ONBOARDING": "000000000004",
    }[document_type]
    return {
        "event_version": "1.0",
        "event_type": "DOCUMENT_APPROVED",
        "approved_business_record_id": f"00000000-0000-4000-8000-{suffix}",
        "document_id": f"00000000-0000-4000-9000-{suffix}",
        "graph_run_id": f"00000000-0000-4000-a000-{suffix}",
        "business_data": business_data,
        "event_key": key,
    }


def samples() -> dict[str, dict[str, object]]:
    invoice = {
        "invoice_number": "INV-1042",
        "invoice_date": "2026-10-01",
        "due_date": "2026-10-31",
        "supplier_name": "Acme Paper Co.",
        "supplier_tax_id": "12-3456789",
        "purchase_order_number": "PO-4500",
        "currency": "USD",
        "subtotal": "100.00",
        "tax": "8.25",
        "total": "108.25",
    }
    claim = {
        "named_insured": "Danielle Okafor",
        "policy_number": "NFM-PA-4472433",
        "loss_date": "2026-09-22",
        "cause_of_loss": "Rear-ended by other party",
        "vehicle_year": 2020,
        "vehicle_make": "Toyota",
        "vehicle_model": "RAV4 XLE",
        "vin_reported": "2T3W1RFV8LW083215",
        "estimate_total": "2415.90",
        "police_report_number": "HPD-26-118734",
    }
    work_request: dict[str, object] = {
        "schema_version": "work-request-v1",
        "client_name": "Harbor & Vine Coffee Co.",
        "brand": "Harbor & Vine",
        "campaign_name": "Winter Warmers",
        "sow_number": "SOW-1001",
        "launch_date": "2026-12-01",
        "end_date": "2027-01-31",
        "budget_total": "85000",
        "primary_kpi": "Store visits vs. last year",
        "kpi_target": "+8%",
        "tasks": [
            {
                "task_key": "brief-1",
                "title": "Winter Warmers: 12 × Social static post",
                "asset_type": "SOCIAL_STATIC",
                "service": "SOCIAL",
                "quantity": 12,
                "description": "Menu feature posts",
                "due_date": "2026-11-20",
                "requested_in": "BRIEF",
            },
            {
                "task_key": "brief-2",
                "title": "Winter Warmers: 4 × Social video (15s or under)",
                "asset_type": "SOCIAL_VIDEO",
                "service": "SOCIAL",
                "quantity": 4,
                "description": "Barista cut-downs",
                "due_date": "2026-11-20",
                "requested_in": "BRIEF",
            },
            {
                "task_key": "brief-3",
                "title": "Winter Warmers: 1 × Display banner set",
                "asset_type": "DISPLAY_SET",
                "service": "DISPLAY",
                "quantity": 1,
                "description": "Geo-targeted banner set, 6 sizes",
                "due_date": "2026-11-24",
                "requested_in": "BRIEF",
            },
        ],
    }
    plan: dict[str, object] = {
        "schema_version": "onboarding-plan-v1",
        "client_name": "Harbor & Vine Coffee Co.",
        "agreement_number": "DPA-2001",
        "source_name": "Shopify orders (US store)",
        "connector_type": "SHOPIFY",
        "refresh_cadence": "DAILY",
        "data_region": "US",
        "lawful_basis": "CONTRACT",
        "retention_days": 730,
        "go_live": "2026-11-30",
        "mappings": [
            {
                "source_field": "customer_email",
                "attribute": "email_address",
                "entity": "PROFILE",
                "personal_class": "DIRECT_IDENTIFIER",
                "identity_key": True,
            },
            {
                "source_field": "order_number",
                "attribute": "order_id",
                "entity": "EVENT",
                "personal_class": "NONE",
                "identity_key": False,
            },
            {
                "source_field": "total_price",
                "attribute": "order_total",
                "entity": "EVENT",
                "personal_class": "NONE",
                "identity_key": False,
            },
        ],
        "excluded_fields": [],
        "tasks": [
            {
                "task_key": "connector",
                "title": "Configure SHOPIFY connector for Shopify orders (US store)",
            },
            {
                "task_key": "mapping",
                "title": "Implement 3 field mappings for Shopify orders (US store)",
            },
            {"task_key": "identity", "title": "Configure identity resolution on email_address"},
            {"task_key": "consent", "title": "Configure consent enforcement (CONTRACT)"},
            {"task_key": "qa", "title": "QA Shopify orders (US store) against the redacted sample"},
        ],
    }
    key = "approved-record:00000000-0000-4000-8000-000000000101:DOCUMENT_APPROVED"
    return {
        "approved-invoice.json": {
            **_envelope(key, "AP_INVOICE", invoice),
            **invoice_payload(invoice),
        },
        "approved-claim.json": {
            **_envelope(key, "CLAIM_PACKET", claim),
            **claim_payload(
                claim,
                queue="BODILY_INJURY_ADJUSTER",
                reason_code="INJURY_INDICATED",
                flags=["INJURY_INDICATED", "INJURY_CONFLICT", "THIRD_PARTY_INVOLVED"],
                event_key=key,
            ),
        },
        "approved-brief.json": {
            **_envelope(key, "CAMPAIGN_BRIEF", {}),
            "document_type": "CAMPAIGN_BRIEF",
            "work_request": work_request,
            "jira": brief_jira(work_request, event_key=key),
        },
        "approved-cdp-onboarding.json": {
            **_envelope(key, "CDP_ONBOARDING", {}),
            "document_type": "CDP_ONBOARDING",
            "onboarding_plan": plan,
            "jira": cdp_jira(plan, event_key=key),
        },
        "routed-claim.json": routed_event(
            event_key="routed:00000000-0000-4000-8000-0000000000c1:1:rules",
            document_type="CLAIM_PACKET",
            document_id="00000000-0000-4000-8000-0000000000d1",
            review_task_id="00000000-0000-4000-8000-0000000000c1",
            review_cycle=1,
            queue="BODILY_INJURY_ADJUSTER",
            routing_version="claim-routing-v1",
            routed_by="RULES",
            reason_code="INJURY_INDICATED",
            flags=["INJURY_INDICATED", "INJURY_CONFLICT"],
            summary={
                "reference": "NFM-PA-4472433",
                "party": "Danielle Okafor",
                "subject": "2020 Toyota RAV4 XLE",
                "event_label": "Loss",
                "event_date": "2026-09-22",
                "context": "Rear-ended by other party",
                "amount": "2415.90",
                "item_count": None,
            },
            target_hours=2,
            routed_at=datetime(2026, 11, 9, 14, 0, tzinfo=UTC),
        ),
    }


def main(argv: list[str]) -> None:
    out = Path(argv[1]) if len(argv) > 1 else OUT
    out.mkdir(parents=True, exist_ok=True)
    for name, payload in samples().items():
        (out / name).write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    sys.stdout.write(f"Wrote {len(samples())} samples to {out}\n")


if __name__ == "__main__":
    main(sys.argv)
