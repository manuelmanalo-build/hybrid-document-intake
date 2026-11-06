"""Generate the synthetic CDP-onboarding evaluation set in test-data/cdp (ADR 0010).

Run from the repository root (needs poppler's pdftoppm on PATH for the scanned packet):

    uv run --with reportlab==5.0.1 --with pillow==12.3.0 \
        python -m scripts.generate_cdp_fixtures [output-directory]
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from scripts.cdp_fixtures.data import (
    AGREEMENTS,
    ATTRIBUTES,
    CONNECTORS,
    PRACTICE,
    REQUESTS,
    Mapping,
    Request,
)
from scripts.cdp_fixtures.expected import expect, resolve_agreement, scan
from scripts.generate_brief_fixtures import boxed, degrade, mdy, wrap, write_csv

INK = colors.HexColor("#123A3A")
LIGHT = colors.HexColor("#E6F2F1")
GRAY = colors.HexColor("#5A6472")
FIRST = ("maya.chen", "luis.ortega", "priya.n", "sam.okafor", "ava.martin", "noah.kim")


def sample_value(attribute: str | None, row: int, *, unmasked: bool) -> str:
    phone_tail = f"{155 + row:04d}"
    if attribute == "email_address":
        return f"{FIRST[row]}@example.com" if unmasked else f"{FIRST[row][0]}***@example.com"
    if attribute == "phone_number":
        return f"(212) 555-{phone_tail}" if unmasked else f"(***) ***-{phone_tail}"
    values = {
        "customer_id": f"C-{48210 + row * 7}",
        "order_id": f"#{90412 + row}",
        "order_total": f"{18.5 + row * 6.25:.2f}",
        "order_timestamp": f"2026-10-{12 + row:02d}T14:{10 + row:02d}:00Z",
        "product_sku": f"SKU-{3100 + row}",
        "quantity": str(1 + row % 3),
        "loyalty_tier": ("Bronze", "Silver", "Gold")[row % 3],
        "loyalty_id": f"L-***-{4400 + row}",
        "device_id": f"GA1.2.{70000 + row}",
        "first_name": "J***",
        "last_name": "S***",
        "postal_code": "100**",
        "marketing_opt_in": ("true", "false")[row % 2],
        "health_condition": "allergy",
        "accessibility_needs": "wheelchair",
        "account_balance_band": "ABCDE"[row % 5],
        "store_id": f"ST-{12 + row}",
        "event_name": ("page_view", "add_to_cart")[row % 2],
        "page_url": f"/products/{row + 1}",
        "event_timestamp": f"2026-10-{12 + row:02d}T09:00:00Z",
        "ride_id": f"R-{88100 + row}",
        "ride_fare": f"{12 + row * 3:.2f}",
        "pickup_city": ("Chicago", "Austin", "Seattle")[row % 3],
        "account_created_at": f"2025-0{1 + row % 9}-15T00:00:00Z",
        "country_code": "US",
    }
    return values.get(attribute or "", f"{0.42 + row / 10:.2f}")


def page_form(pdf: canvas.Canvas, request: Request) -> None:
    width, height = letter
    pdf.setFillColor(INK)
    pdf.rect(0, height - 64, width, 64, fill=1, stroke=0)
    pdf.setFillColor(colors.white)
    pdf.setFont("Helvetica-Bold", 15)
    pdf.drawString(36, height - 38, f"{PRACTICE.upper()}  ·  DATA SOURCE ONBOARDING")
    pdf.setFont("Helvetica", 8.5)
    pdf.drawString(
        36, height - 52, "CDP intake form DS-ON-02 · onboarding@meridianpike-data.example"
    )
    pdf.setFillColor(colors.black)
    y = height - 78
    rows = (
        (
            ("Client", request.client, 270),
            ("Data agreement", request.agreement_stated or "", 140),
            ("Date submitted", mdy(request.received), 130),
        ),
        (
            ("Source name", request.source_name, 270),
            ("Connector", CONNECTORS[request.connector].label, 140),
            ("Refresh cadence", (request.cadence or "TBD").replace("_", " ").title(), 130),
        ),
        (
            ("Data region", request.region, 135),
            ("Estimated records", f"{request.estimated_records:,}", 135),
            ("Requested go-live", mdy(request.go_live) if request.go_live else "TBD", 135),
            ("Retention (days)", str(request.retention_days), 135),
        ),
        (
            ("Lawful basis", (request.lawful_basis or "").replace("_", " ").title(), 180),
            ("Consent mechanism", request.consent_mechanism or "", 360),
        ),
        (
            ("Contains personal data?", "No" if request.declares_no_personal else "Yes", 180),
            ("Contains sensitive data?", "Yes" if request.declares_sensitive else "No", 180),
            ("Data owner", request.data_owner, 180),
        ),
        (("Submitted by", f"{request.contact} <{request.contact_email}>", 540),),
    )
    for row in rows:
        tall = 40 if any(len(value) > 55 for _, value, _ in row) else 28
        y -= tall
        x = 36.0
        for label, value, w in row:
            boxed(pdf, x, y, w, tall, label, value)
            x += w
    pdf.setFont("Helvetica", 7)
    pdf.setFillColor(GRAY)
    pdf.drawString(36, 40, "SYNTHETIC TEST DATA — fictional practice, client, and data.")
    pdf.setFillColor(colors.black)


def page_dictionary(pdf: canvas.Canvas, request: Request) -> None:
    width, height = letter
    pdf.setFont("Helvetica-Bold", 13)
    pdf.drawString(36, height - 54, f"Data dictionary · {request.source_name}")
    pdf.setFont("Helvetica", 8.5)
    pdf.setFillColor(GRAY)
    pdf.drawString(36, height - 68, f"{request.client} · prepared for {PRACTICE}")
    pdf.setFillColor(colors.black)
    columns = (0, 130, 210, 380, 490)
    y = height - 96
    pdf.setFillColor(LIGHT)
    pdf.rect(36, y - 3, width - 72, 14, fill=1, stroke=0)
    pdf.setFillColor(INK)
    pdf.setFont("Helvetica-Bold", 8.5)
    for offset, heading in zip(
        columns, ("Source field", "Type", "Description", "Maps to (CDP)", "Personal?"), strict=True
    ):
        pdf.drawString(40 + offset, y, heading)
    pdf.setFillColor(colors.black)
    pdf.setFont("Helvetica", 9)
    for item in request.mappings:
        y -= 16
        cells = (
            item.source_field,
            item.source_type,
            item.description[:34],
            item.proposed_attribute or "—",
            "Yes" if item.client_marked_personal else "No",
        )
        for offset, cell in zip(columns, cells, strict=True):
            pdf.drawString(40 + offset, y, cell)


def sample_lines(request: Request) -> list[str]:
    header = " | ".join(item.source_field for item in request.mappings)
    rows = [
        " | ".join(
            sample_value(item.proposed_attribute, row, unmasked=request.sample_unmasked)
            for item in request.mappings
        )
        for row in range(6)
    ]
    return [header, *rows]


def page_sample(pdf: canvas.Canvas, request: Request) -> None:
    width, height = letter
    pdf.setFont("Helvetica-Bold", 13)
    pdf.drawString(36, height - 54, f"Sample extract · {request.source_name} (first 6 rows)")
    pdf.setFont("Courier", 8.6)
    y = height - 84
    for line in sample_lines(request):
        pdf.drawString(36, y, line[:110])
        y -= 13
    pdf.setFont("Helvetica-Oblique", 8)
    pdf.setFillColor(GRAY)
    note = (
        "Values exported directly from production."
        if request.sample_unmasked
        else "Identifiers masked by the client before sending."
    )
    pdf.drawString(36, y - 10, note)
    pdf.setFillColor(colors.black)


def page_addendum(pdf: canvas.Canvas, request: Request) -> None:
    _, height = letter
    pdf.setFont("Helvetica-Bold", 13)
    pdf.drawString(36, height - 54, f"Privacy addendum · {request.client}")
    wrap(pdf, request.privacy_addendum or "", 36, height - 80, 100, 10)


def page_email(
    pdf: canvas.Canvas, request: Request, *, subject: str, body: str, sent: date
) -> None:
    width, height = letter
    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawString(40, height - 50, subject)
    pdf.line(40, height - 58, width - 40, height - 58)
    y = height - 76
    for label, value in (
        ("From:", f"{request.contact} <{request.contact_email}>"),
        ("To:", "Onboarding <onboarding@meridianpike-data.example>"),
        ("Date:", sent.strftime("%a, %b %d, %Y").replace(" 0", " ")),
        ("Subject:", subject),
    ):
        pdf.setFont("Helvetica-Bold", 9)
        pdf.drawString(40, y, label)
        pdf.setFont("Helvetica", 9)
        pdf.drawString(100, y, value)
        y -= 13
    pdf.line(40, y + 3, width - 40, y + 3)
    wrap(pdf, body, 40, y - 14, 98, 10)


def email_only_body(request: Request) -> str:
    cadence = request.cadence.lower() if request.cadence else "TBD"
    basis = (request.lawful_basis or "TBD").lower()
    lines = [
        "Hi Meridian & Pike Data,",
        "",
        f"Resending our onboarding request for {request.source_name} under "
        f"{request.agreement_stated}: {CONNECTORS[request.connector].label}, {cadence} refresh, "
        f"{request.region} data, about {request.estimated_records:,} records, go-live "
        f"{mdy(request.go_live)}. Lawful basis: {basis}; retention {request.retention_days} days. "
        f"{request.data_owner} owns the source.",
        "",
        "Fields (source -> CDP, personal?):",
    ]
    for item in request.mappings:
        lines.append(
            f"- {item.source_field} ({item.source_type}) -> {item.proposed_attribute}, "
            f"{'personal' if item.client_marked_personal else 'not personal'}"
        )
    lines += ["", "Not sure the form went through last week.", "", "Thanks,", request.contact]
    return "\n".join(lines)


def render(request: Request, path: Path) -> tuple[list[str], dict[str, int]]:
    pdf = canvas.Canvas(str(path), pagesize=letter)
    pdf.setTitle(f"CDP onboarding request {request.request_id}")
    pdf.setAuthor("Synthetic test data")
    segments: list[str] = []
    findings: dict[str, int] = {}
    if request.email_only:
        page_email(
            pdf,
            request,
            subject=f"Onboarding request: {request.source_name}",
            body=email_only_body(request),
            sent=request.received,
        )
        segments.append("CORRESPONDENCE")
        pdf.showPage()
    else:
        page_form(pdf, request)
        pdf.showPage()
        segments.append("ONBOARDING_FORM")
        page_dictionary(pdf, request)
        pdf.showPage()
        segments.append("DATA_DICTIONARY")
        if request.include_sample:
            page_sample(pdf, request)
            pdf.showPage()
            segments.append("SAMPLE_EXTRACT")
            findings = scan("\n".join(sample_lines(request)))
        if request.privacy_addendum:
            page_addendum(pdf, request)
            pdf.showPage()
            segments.append("PRIVACY_ADDENDUM")
    if request.email:
        page_email(
            pdf,
            request,
            subject=f"Re: {request.source_name} onboarding",
            body=request.email,
            sent=request.received,
        )
        pdf.showPage()
        segments.append("CORRESPONDENCE")
    pdf.save()
    if request.scanned:
        degrade(path, seed=int(request.request_id[1:]))
    return segments, findings


def mappings_json(items: list[Mapping]) -> str:
    return json.dumps(
        [
            {
                "source_field": item.source_field,
                "source_type": item.source_type,
                "proposed_attribute": item.proposed_attribute,
                "client_marked_personal": item.client_marked_personal,
            }
            for item in items
        ]
    )


def main(argv: list[str]) -> None:
    root = Path(argv[1] if len(argv) > 1 else "test-data/cdp")
    packets = root / "packets"
    packets.mkdir(parents=True, exist_ok=True)
    write_csv(
        root / "data_agreements.csv",
        [
            "agreement_number",
            "client",
            "start_date",
            "end_date",
            "status",
            "regions",
            "lawful_bases",
            "sensitive_categories",
            "max_retention_days",
        ],
        [
            [
                a.number,
                a.client,
                a.start,
                a.end,
                a.status,
                ";".join(a.regions),
                ";".join(a.lawful_bases),
                ";".join(a.sensitive),
                a.max_retention_days,
            ]
            for a in AGREEMENTS
        ],
    )
    write_csv(
        root / "canonical_attributes.csv",
        ["attribute", "entity", "data_type", "personal_class", "identity_key"],
        [
            [a.name, a.entity, a.data_type, a.personal_class, str(a.identity_key).lower()]
            for a in ATTRIBUTES.values()
        ],
    )
    write_csv(
        root / "connectors.csv",
        ["connector_type", "label", "cadences", "min_business_days"],
        [[c.code, c.label, ";".join(c.cadences), c.min_business_days] for c in CONNECTORS.values()],
    )
    seen: set[tuple[str, str, str]] = set()
    rows: list[list[object]] = []
    for request in sorted(REQUESTS, key=lambda item: (item.received, item.request_id)):
        name = f"{request.request_id}_onboarding.pdf"
        segments, findings = render(request, packets / name)
        expected = expect(request, findings, seen)
        agreement = resolve_agreement(request)
        rows.append(
            [
                request.request_id,
                name,
                len(segments),
                ";".join(segments),
                "degraded_image_only" if request.scanned else "clean_digital",
                request.client,
                request.agreement_stated or "",
                agreement.number if agreement else "",
                request.received,
                request.source_name,
                request.connector,
                request.cadence or "",
                request.region,
                request.lawful_basis or "",
                request.consent_mechanism or "",
                str(request.declares_no_personal).lower(),
                str(request.declares_sensitive).lower(),
                request.retention_days,
                request.estimated_records,
                request.go_live or "",
                request.email_go_live or "",
                request.data_owner,
                mappings_json(request.mappings),
                mappings_json(request.email_added),
                json.dumps(findings),
                expected.queue,
                ";".join(expected.flags),
                expected.reason,
                request.edge_case,
            ]
        )
    rows.sort(key=lambda row: str(row[0]))
    write_csv(
        root / "ground_truth.csv",
        [
            "request_id",
            "packet_file",
            "page_count",
            "segments",
            "scan_quality",
            "client_name",
            "agreement_number_stated",
            "agreement_resolved",
            "received_date",
            "source_name",
            "connector_type",
            "refresh_cadence",
            "data_region",
            "lawful_basis",
            "consent_mechanism",
            "declares_no_personal_data",
            "declares_sensitive_data",
            "retention_days",
            "estimated_records",
            "requested_go_live",
            "go_live_in_correspondence",
            "data_owner",
            "mappings",
            "fields_added_in_correspondence",
            "sample_identifier_findings",
            "expected_queue",
            "flags",
            "route_reason",
            "edge_case",
        ],
        rows,
    )
    sys.stdout.write(f"Wrote {len(rows)} onboarding requests to {root}\n")


if __name__ == "__main__":
    main(sys.argv)
