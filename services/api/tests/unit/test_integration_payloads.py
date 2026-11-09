from datetime import UTC, datetime

from intake_api.application.integration_payloads import (
    brief_jira,
    cdp_jira,
    claim_payload,
    invoice_payload,
    jira_label,
    jira_summary,
    routed_event,
)


def test_quickbooks_bill_lines_sum_to_the_total() -> None:
    payload = invoice_payload(
        {
            "invoice_number": "INV-1042",
            "supplier_name": "Acme Paper Co.",
            "invoice_date": "2026-10-01",
            "due_date": "2026-10-31",
            "purchase_order_number": "PO-4500",
            "currency": "USD",
            "subtotal": "100.00",
            "tax": "8.25",
            "total": "108.25",
        }
    )

    bill = payload["accounting_bill"]
    assert payload["document_type"] == "AP_INVOICE"
    assert bill["doc_number"] == "INV-1042"  # type: ignore[index]
    assert bill["lines"] == [  # type: ignore[index]
        {"description": "Invoice INV-1042 (PO PO-4500)", "amount": "100.00"},
        {"description": "Sales tax", "amount": "8.25"},
    ]
    assert bill["lines_match_total"] is True  # type: ignore[index]


def test_a_mismatched_invoice_is_flagged_not_silently_fixed() -> None:
    bill = invoice_payload({"subtotal": "100", "tax": "8", "total": "120"})["accounting_bill"]

    assert bill["lines_match_total"] is False  # type: ignore[index]


def test_jira_labels_and_summaries_respect_jira_limits() -> None:
    assert jira_label("Winter Warmers (Harbor & Vine)") == "winter-warmers-harbor-vine"
    assert len(jira_summary("x" * 400)) == 255


def test_claim_issue_priority_follows_the_queue() -> None:
    high = claim_payload(
        {"named_insured": "Danielle Okafor"},
        queue="BODILY_INJURY_ADJUSTER",
        reason_code="INJURY_INDICATED",
        flags=["INJURY_CONFLICT"],
        event_key="approved-record:x",
    )
    issue = high["jira"]["issues"][0]  # type: ignore[index]

    assert issue["priority"] == "High"
    assert "injury-conflict" in issue["labels"]


def test_brief_and_cdp_blocks_have_an_epic_and_one_issue_per_task() -> None:
    brief = brief_jira(
        {
            "campaign_name": "Winter Warmers",
            "brand": "Harbor & Vine",
            "launch_date": "2026-12-01",
            "tasks": [
                {
                    "task_key": "brief-1",
                    "title": "Winter Warmers: 12 × Social static post",
                    "service": "SOCIAL",
                    "due_date": "2026-11-20",
                },
            ],
        },
        event_key="approved-record:b01",
    )
    cdp = cdp_jira(
        {
            "source_name": "Shopify orders",
            "go_live": "2026-11-30",
            "mappings": [
                {
                    "source_field": "email",
                    "attribute": "email_address",
                    "entity": "PROFILE",
                    "personal_class": "DIRECT_IDENTIFIER",
                    "identity_key": True,
                }
            ],
            "excluded_fields": ["vibe"],
            "tasks": [{"task_key": "mapping", "title": "Implement 1 field mappings"}],
        },
        event_key="approved-record:d01",
    )

    assert brief["epic"]["due_date"] == "2026-12-01"  # type: ignore[index]
    assert brief["issues"][0]["due_date"] == "2026-11-20"  # type: ignore[index]
    story = cdp["issues"][0]  # type: ignore[index]
    assert "email → email_address" in story["description"]
    assert "Not ingested: vibe" in story["description"]


def test_routed_event_carries_a_ready_to_post_alert() -> None:
    event = routed_event(
        event_key="routed:t:1:rules",
        document_type="CDP_ONBOARDING",
        document_id="d",
        review_task_id="t",
        review_cycle=1,
        queue="PRIVACY_REVIEW",
        routing_version="cdp-routing-v1",
        routed_by="RULES",
        reason_code="PERSONAL_DATA_FINDINGS",
        flags=["UNMASKED_IDENTIFIERS_IN_PACKET"],
        summary={"party": "Lumen Home", "subject": "Braze customer profiles"},
        target_hours=4,
        routed_at=datetime(2026, 11, 9, 14, 0, tzinfo=UTC),
    )

    assert event["priority"] == "High"
    assert event["service_level"] == {"target_hours": 4, "due_at": "2026-11-09T18:00:00+00:00"}
    assert event["alert"]["title"] == (  # type: ignore[index]
        "Onboarding request routed to Privacy review: Lumen Home · Braze customer profiles"
    )
