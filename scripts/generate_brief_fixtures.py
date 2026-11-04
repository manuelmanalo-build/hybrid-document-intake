"""Generate the synthetic campaign-brief evaluation set in test-data/briefs (ADR 0009).

Run from the repository root (needs poppler's pdftoppm on PATH for the two scanned packets):

    uv run --with reportlab==5.0.1 --with pillow==12.3.0 \
        python -m scripts.generate_brief_fixtures [output-directory]
"""

from __future__ import annotations

import csv
import io
import json
import random
import shutil
import subprocess
import sys
import tempfile
import textwrap
from datetime import date
from decimal import Decimal
from pathlib import Path

from PIL import Image, ImageChops, ImageFilter
from reportlab.lib import colors
from reportlab.lib.pagesizes import landscape, letter
from reportlab.pdfgen import canvas
from scripts.brief_fixtures.data import AGENCY, ASSET_TYPES, BRIEFS, SOWS, Brief, Deliverable
from scripts.brief_fixtures.expected import expect, resolve_sow

INK = colors.HexColor("#1B2430")
ACCENT = colors.HexColor("#5B2A86")
GRAY = colors.HexColor("#5A6472")
LIGHT = colors.HexColor("#F1ECF7")


def mdy(value: date | None) -> str:
    return value.strftime("%m/%d/%Y") if value else "—"


def money(value: Decimal | None, fallback: str) -> str:
    return f"${value:,.0f}" if value is not None else fallback


def wrap(pdf: canvas.Canvas, text: str, x: float, y: float, width: int, size: float = 9.5) -> float:
    pdf.setFont("Helvetica", size)
    for paragraph in text.split("\n"):
        for line in textwrap.wrap(paragraph, width) or [""]:
            pdf.drawString(x, y, line)
            y -= size + 3.5
    return y


def boxed(
    pdf: canvas.Canvas, x: float, y: float, w: float, h: float, label: str, value: str
) -> None:
    pdf.setStrokeColor(colors.black)
    pdf.setLineWidth(0.6)
    pdf.rect(x, y, w, h)
    pdf.setFont("Helvetica", 6.3)
    pdf.setFillColor(GRAY)
    pdf.drawString(x + 3, y + h - 8, label.upper())
    pdf.setFillColor(colors.black)
    pdf.setFont("Courier", 9.2)
    lines = textwrap.wrap(value, int((w - 6) / 5.6)) or [""]
    line_y = y + h - 19
    for line in lines:
        if line_y < y + 2:
            break
        pdf.drawString(x + 3, line_y, line)
        line_y -= 11


def deliverable_table(
    pdf: canvas.Canvas, items: list[Deliverable], x: float, y: float, width: float
) -> float:
    columns = (0, 0.42, 0.52, 0.72)
    pdf.setFillColor(LIGHT)
    pdf.rect(x, y - 3, width, 14, fill=1, stroke=0)
    pdf.setFillColor(INK)
    pdf.setFont("Helvetica-Bold", 8.5)
    for offset, heading in zip(columns, ("Deliverable", "Qty", "Channel", "Due"), strict=True):
        pdf.drawString(x + 4 + offset * width, y, heading)
    y -= 16
    pdf.setFont("Helvetica", 9)
    for item in items:
        asset = ASSET_TYPES[item.asset_type]
        cells = (
            f"{asset.label}: {item.description}",
            str(item.quantity),
            asset.service.replace("_", " ").title(),
            mdy(item.due),
        )
        for offset, cell in zip(columns, cells, strict=True):
            pdf.drawString(x + 4 + offset * width, y, cell[:58])
        y -= 14
    return y


def page_form(pdf: canvas.Canvas, brief: Brief) -> None:
    width, height = letter
    pdf.setFillColor(ACCENT)
    pdf.rect(0, height - 64, width, 64, fill=1, stroke=0)
    pdf.setFillColor(colors.white)
    pdf.setFont("Helvetica-Bold", 15)
    pdf.drawString(36, height - 38, f"{AGENCY.upper()}  ·  CAMPAIGN BRIEF")
    pdf.setFont("Helvetica", 8.5)
    pdf.drawString(
        36, height - 52, "Client intake form MP-CB-03 · return to briefs@meridianpike.example"
    )
    pdf.setFillColor(colors.black)
    y = height - 78
    rows = (
        (
            ("Client", brief.client, 270),
            ("Brand", brief.brand, 140),
            ("SOW number", brief.sow_stated or "", 130),
        ),
        (
            ("Campaign name", brief.campaign, 270),
            ("Date submitted", mdy(brief.received), 140),
            ("Submitted by", brief.submitted_by, 130),
        ),
        (("Objective", brief.objective, 540),),
        (("Primary KPI", brief.kpi or "", 270), ("KPI target", brief.kpi_target or "", 270)),
        (("Target audience", brief.audience, 270), ("Markets", brief.markets, 270)),
        (
            ("Total budget", money(brief.budget, brief.budget_text), 180),
            ("Launch date", mdy(brief.launch), 180),
            ("End date", mdy(brief.end), 180),
        ),
        (("Approver", brief.approver, 540),),
        (("Mandatories and legal", brief.mandatories, 540),),
    )
    for row in rows:
        tall = 40 if any(len(value) > 60 for _, value, _ in row) else 28
        y -= tall
        x = 36.0
        for label, value, w in row:
            boxed(pdf, x, y, w, tall, label, value)
            x += w
    y -= 22
    pdf.setFont("Helvetica-Bold", 10)
    pdf.drawString(36, y, "DELIVERABLES")
    deliverable_table(pdf, brief.deliverables, 36, y - 18, 540)
    pdf.setFont("Helvetica", 7)
    pdf.setFillColor(GRAY)
    pdf.drawString(36, 40, "SYNTHETIC TEST DATA — fictional agency, client, and campaign.")


def page_memo(pdf: canvas.Canvas, brief: Brief) -> None:
    width, height = letter
    pdf.setFont("Helvetica-Bold", 16)
    pdf.setFillColor(INK)
    pdf.drawString(54, height - 60, brief.client)
    pdf.setFont("Helvetica", 9)
    pdf.setFillColor(GRAY)
    pdf.drawString(54, height - 74, "Marketing Department · Memorandum")
    pdf.setFillColor(colors.black)
    pdf.line(54, height - 82, width - 54, height - 82)
    y = height - 104
    for label, value in (
        ("TO", f"{AGENCY} account team"),
        ("FROM", brief.submitted_by),
        ("DATE", brief.received.strftime("%B %d, %Y").replace(" 0", " ")),
        ("RE", f"Campaign brief: {brief.campaign} ({brief.brand})"),
    ):
        pdf.setFont("Helvetica-Bold", 9.5)
        pdf.drawString(54, y, label)
        pdf.setFont("Helvetica", 9.5)
        pdf.drawString(110, y, value)
        y -= 14
    y -= 10
    sow_line = f" under {brief.sow_stated}" if brief.sow_stated else ""
    body = (
        f"We'd like {AGENCY} to plan and produce {brief.campaign}{sow_line}. "
        f"Objective: {brief.objective} "
        f"We'll judge success on {(brief.kpi or 'results').lower()}"
        + (f", targeting {brief.kpi_target}." if brief.kpi_target else ".")
        + f"\n\nAudience: {brief.audience}. Markets: {brief.markets}."
        + f"\n\nBudget: {money(brief.budget, brief.budget_text)}. The campaign launches "
        + f"{mdy(brief.launch)} and runs through {mdy(brief.end)}. "
        + f"Final approval: {brief.approver}."
        + f"\n\nMandatories: {brief.mandatories}"
    )
    y = wrap(pdf, body, 54, y, 98, 10)
    y -= 12
    pdf.setFont("Helvetica-Bold", 10)
    pdf.drawString(54, y, "What we need")
    deliverable_table(pdf, brief.deliverables, 54, y - 18, 504)
    pdf.setFont("Helvetica", 7)
    pdf.setFillColor(GRAY)
    pdf.drawString(54, 40, "SYNTHETIC TEST DATA — fictional agency, client, and campaign.")


def page_slides(pdf: canvas.Canvas, brief: Brief, slide: int) -> None:
    width, height = landscape(letter)
    pdf.setFillColor(INK)
    pdf.rect(0, 0, width, height, fill=1, stroke=0)
    pdf.setFillColor(colors.white)
    if slide == 1:
        pdf.setFont("Helvetica-Bold", 28)
        pdf.drawString(56, height - 100, brief.campaign)
        pdf.setFont("Helvetica", 14)
        pdf.drawString(
            56, height - 126, f"{brief.brand} · brief for {AGENCY} · {mdy(brief.received)}"
        )
        y = height - 180
        for label, value in (
            ("Objective", brief.objective),
            ("KPI", f"{brief.kpi}: {brief.kpi_target}"),
            ("Audience", brief.audience),
            ("Markets", brief.markets),
            ("Budget", money(brief.budget, brief.budget_text)),
            ("Flight", f"{mdy(brief.launch)} to {mdy(brief.end)}"),
            ("Contract", brief.sow_stated or "—"),
            ("Approver", brief.approver),
        ):
            pdf.setFont("Helvetica-Bold", 12)
            pdf.drawString(56, y, label)
            pdf.setFont("Helvetica", 12)
            pdf.drawString(170, y, value[:90])
            y -= 26
        return
    pdf.setFont("Helvetica-Bold", 22)
    pdf.drawString(56, height - 80, "Deliverables and timing")
    pdf.setFillColor(colors.white)
    y = height - 130
    for item in brief.deliverables:
        asset = ASSET_TYPES[item.asset_type]
        pdf.setFont("Helvetica", 13)
        pdf.drawString(
            56, y, f"{item.quantity} × {asset.label} ({item.description}), due {mdy(item.due)}"
        )
        y -= 26
    pdf.setFont("Helvetica-Oblique", 11)
    pdf.drawString(56, y - 16, f"Mandatories: {brief.mandatories}"[:120])
    pdf.setFont("Helvetica", 8)
    pdf.drawString(56, 30, "SYNTHETIC TEST DATA — fictional agency, client, and campaign.")


def page_email(pdf: canvas.Canvas, brief: Brief, *, body: str, subject: str, sent: date) -> None:
    width, height = letter
    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawString(40, height - 50, subject)
    pdf.line(40, height - 58, width - 40, height - 58)
    y = height - 76
    for label, value in (
        ("From:", f"{brief.submitted_by} <{brief.submitter_email}>"),
        ("To:", "Briefs <briefs@meridianpike.example>"),
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


def email_only_body(brief: Brief) -> str:
    lines = [
        "Hi Meridian & Pike team,",
        "",
        f"Sending our brief for {brief.campaign} ({brief.brand}) under {brief.sow_stated}. "
        f"{brief.objective} Success = {brief.kpi}, target {brief.kpi_target}.",
        f"Audience is {brief.audience}; markets are {brief.markets}. Budget is "
        f"{money(brief.budget, brief.budget_text)}. Launch {mdy(brief.launch)}, end "
        f"{mdy(brief.end)}. {brief.approver} signs off.",
        f"Mandatories: {brief.mandatories}",
        "",
        "Deliverables:",
    ]
    for item in brief.deliverables:
        asset = ASSET_TYPES[item.asset_type]
        lines.append(f"- {item.quantity} x {asset.label}: {item.description}, due {mdy(item.due)}")
    if brief.duplicate_of:
        lines += ["", "Resending in case the form didn't come through last week."]
    lines += ["", "Thanks,", brief.submitted_by]
    return "\n".join(lines)


def page_media_plan(pdf: canvas.Canvas, brief: Brief) -> None:
    width, height = letter
    pdf.setFont("Helvetica-Bold", 13)
    pdf.drawString(40, height - 56, f"{brief.campaign} · media plan (working draft)")
    budget = brief.budget or Decimal("0")
    shares = {"DISPLAY": Decimal("0.35"), "SOCIAL": Decimal("0.40"), "VIDEO": Decimal("0.25")}
    y = height - 90
    pdf.setFont("Helvetica-Bold", 9)
    for offset, heading in zip(
        (0, 160, 300, 430), ("Channel", "Flight", "Share", "Budget"), strict=True
    ):
        pdf.drawString(40 + offset, y, heading)
    pdf.setFont("Helvetica", 9)
    total = Decimal("0")
    for channel, share in shares.items():
        y -= 15
        amount = (budget * share).quantize(Decimal("1"))
        total += amount
        cells = (
            channel.title(),
            f"{mdy(brief.launch)} – {mdy(brief.end)}",
            f"{share:.0%}",
            f"${amount:,.0f}",
        )
        for offset, cell in zip((0, 160, 300, 430), cells, strict=True):
            pdf.drawString(40 + offset, y, cell)
    pdf.setFont("Helvetica-Bold", 9)
    pdf.drawString(470, y - 18, f"Total ${total:,.0f}")


def degrade(path: Path, seed: int) -> None:
    pdftoppm = shutil.which("pdftoppm")
    if pdftoppm is None:
        raise RuntimeError("pdftoppm (poppler) is required to render the scanned packets")
    rng = random.Random(seed)  # noqa: S311 - deterministic fixture noise, not security
    with tempfile.TemporaryDirectory() as scratch:
        prefix = Path(scratch) / "page"
        subprocess.run(  # noqa: S603 - fixed binary resolved with shutil.which, no shell
            [pdftoppm, "-r", "110", "-gray", "-png", str(path), str(prefix)], check=True
        )
        pages: list[Image.Image] = []
        for image_path in sorted(Path(scratch).glob("page*.png")):
            image = Image.open(image_path).convert("L")
            image = image.rotate(
                rng.uniform(-1.6, 1.6), fillcolor=255, resample=Image.Resampling.BICUBIC
            )
            image = image.filter(ImageFilter.GaussianBlur(0.7))
            noise = Image.effect_noise(image.size, 28).convert("L")
            image = ImageChops.blend(image, noise, 0.16)
            buffer = io.BytesIO()
            image.save(buffer, "JPEG", quality=40)
            buffer.seek(0)
            pages.append(Image.open(buffer).convert("L"))
    pages[0].save(path, save_all=True, append_images=pages[1:], resolution=110)


def render(brief: Brief, path: Path) -> list[str]:
    size = landscape(letter) if brief.template == "SLIDES" else letter
    pdf = canvas.Canvas(str(path), pagesize=size)
    pdf.setTitle(f"Campaign brief {brief.brief_id}")
    pdf.setAuthor("Synthetic test data")
    segments: list[str] = []
    if brief.template == "FORM":
        page_form(pdf, brief)
        segments.append("BRIEF")
    elif brief.template == "MEMO":
        page_memo(pdf, brief)
        segments.append("BRIEF")
    elif brief.template == "SLIDES":
        for slide in (1, 2):
            page_slides(pdf, brief, slide)
            pdf.showPage()
            segments.append("BRIEF")
        pdf.setPageSize(letter)
    else:
        page_email(
            pdf,
            brief,
            body=email_only_body(brief),
            subject=f"Brief: {brief.campaign}",
            sent=brief.received,
        )
        segments.append("CORRESPONDENCE")
    if brief.template != "SLIDES":
        pdf.showPage()
    if brief.email:
        page_email(
            pdf, brief, body=brief.email, subject=f"Re: {brief.campaign} brief", sent=brief.received
        )
        pdf.showPage()
        segments.append("CORRESPONDENCE")
    if brief.media_plan:
        page_media_plan(pdf, brief)
        pdf.showPage()
        segments.append("MEDIA_PLAN")
    pdf.save()
    if brief.scanned:
        degrade(path, seed=int(brief.brief_id[1:]))
    return segments


def deliverables_json(items: list[Deliverable]) -> str:
    return json.dumps(
        [
            {
                "asset_type": item.asset_type,
                "quantity": item.quantity,
                "description": item.description,
                "due_date": item.due.isoformat() if item.due else None,
            }
            for item in items
        ]
    )


def write_csv(path: Path, header: list[str], rows: list[list[object]]) -> None:
    with path.open("w", newline="") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows)


def main(argv: list[str]) -> None:
    root = Path(argv[1] if len(argv) > 1 else "test-data/briefs")
    packets = root / "packets"
    packets.mkdir(parents=True, exist_ok=True)
    write_csv(
        root / "statements_of_work.csv",
        [
            "sow_number",
            "client",
            "start_date",
            "end_date",
            "status",
            "total_fees",
            "committed_fees",
            "services_in_scope",
            "regulated_category",
            "account_lead",
        ],
        [
            [
                sow.sow_number,
                sow.client,
                sow.start,
                sow.end,
                sow.status,
                sow.total_fees,
                sow.committed_fees,
                ";".join(sow.services),
                sow.regulated_category,
                sow.account_lead,
            ]
            for sow in SOWS
        ],
    )
    write_csv(
        root / "sow_brands.csv",
        ["sow_number", "brand"],
        [[sow.sow_number, brand] for sow in SOWS for brand in sow.brands],
    )
    write_csv(
        root / "lead_times.csv",
        ["asset_type", "label", "service", "min_business_days"],
        [[a.code, a.label, a.service, a.min_business_days] for a in ASSET_TYPES.values()],
    )
    seen: set[tuple[str, str, date]] = set()
    rows: list[list[object]] = []
    for brief in sorted(BRIEFS, key=lambda item: (item.received, item.brief_id)):
        name = f"{brief.brief_id}_brief.pdf"
        segments = render(brief, packets / name)
        expected = expect(brief, seen)
        sow = resolve_sow(brief)
        rows.append(
            [
                brief.brief_id,
                name,
                len(segments),
                ";".join(segments),
                "degraded_image_only" if brief.scanned else "clean_digital",
                brief.template,
                brief.client,
                brief.brand,
                brief.campaign,
                brief.sow_stated or "",
                sow.sow_number if sow else "",
                brief.received,
                brief.objective,
                brief.kpi or "",
                brief.kpi_target or "",
                brief.audience,
                brief.markets,
                "" if brief.budget is None else f"{brief.budget:.2f}",
                brief.launch,
                brief.end,
                brief.email_launch or "",
                brief.approver,
                brief.mandatories,
                deliverables_json(brief.deliverables),
                deliverables_json(brief.email_added),
                expected.queue,
                ";".join(expected.flags),
                expected.reason,
                brief.edge_case,
            ]
        )
    rows.sort(key=lambda row: str(row[0]))
    write_csv(
        root / "ground_truth.csv",
        [
            "brief_id",
            "packet_file",
            "page_count",
            "segments",
            "scan_quality",
            "template",
            "client_name",
            "brand",
            "campaign_name",
            "sow_number_stated",
            "sow_number_resolved",
            "received_date",
            "objective",
            "primary_kpi",
            "kpi_target",
            "audience",
            "markets",
            "budget_total",
            "launch_date",
            "end_date",
            "launch_date_in_correspondence",
            "approver",
            "mandatories",
            "deliverables",
            "deliverables_added_in_correspondence",
            "expected_queue",
            "flags",
            "route_reason",
            "edge_case",
        ],
        rows,
    )
    sys.stdout.write(f"Wrote {len(rows)} briefs to {root}\n")


if __name__ == "__main__":
    main(sys.argv)
