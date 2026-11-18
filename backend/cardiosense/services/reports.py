"""PDF patient report (FR-19): disclaimer, model + rule versions, trend summary, alert table."""

from __future__ import annotations

import io
from datetime import datetime
from typing import Any
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from cardiosense.config import DISCLAIMER

INK = colors.HexColor("#0B1B2B")
NAVY = colors.HexColor("#0E2A47")
TEAL = colors.HexColor("#00A896")
CORAL = colors.HexColor("#E0484D")
AMBER = colors.HexColor("#C9820A")
GRID = colors.HexColor("#C9D3DD")
TIER_COLOURS = {"green": TEAL, "amber": AMBER, "orange": colors.HexColor("#D9641E"), "red": CORAL}


def _fmt(value: Any, suffix: str = "", digits: int = 1) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.{digits}f}{suffix}"
    return f"{value}{suffix}"


def _dt(value: str | datetime | None) -> str:
    if value is None:
        return "—"
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return value.strftime("%Y-%m-%d %H:%M")


def _table(rows: list[list[Any]], widths: list[float], header: bool = True) -> Table:
    table = Table(rows, colWidths=widths, repeatRows=1 if header else 0)
    style = [
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.4, GRID),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]
    if header:
        style += [("BACKGROUND", (0, 0), (-1, 0), NAVY), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                  ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold")]
    table.setStyle(TableStyle(style))
    return table


def build_patient_report(data: dict[str, Any]) -> bytes:
    """Render the report. ``data`` keys: patient, range, start, end, generated_at, generated_by, versions,
    summary, daily (buckets), bucket_label, alerts, sessions, limitations, gaps."""
    styles = getSampleStyleSheet()
    body = ParagraphStyle("body", parent=styles["BodyText"], fontSize=9, leading=12, textColor=INK)
    small = ParagraphStyle("small", parent=body, fontSize=7.5, leading=10)
    cell = ParagraphStyle("cell", parent=body, fontSize=8, leading=10)
    h1 = ParagraphStyle("h1", parent=styles["Title"], fontSize=18, textColor=NAVY, alignment=0, spaceAfter=4)
    h2 = ParagraphStyle("h2", parent=styles["Heading2"], fontSize=11.5, textColor=NAVY, spaceBefore=10, spaceAfter=4)
    banner = ParagraphStyle("banner", parent=body, fontSize=9, textColor=colors.white, backColor=CORAL,
                            borderPadding=(4, 6, 4, 6), spaceAfter=8, spaceBefore=4)
    notice = ParagraphStyle("notice", parent=body, fontSize=9, backColor=colors.HexColor("#FFF4DC"),
                            borderColor=AMBER, borderWidth=0.8, borderPadding=(5, 6, 5, 6), spaceAfter=8, spaceBefore=6)

    patient = data["patient"]
    story: list[Any] = [Paragraph("CardioSense AI — monitoring report", h1)]
    if patient["is_demo"]:
        story.append(Paragraph("<b>DEMO / REPLAY DATA</b> — public labelled recordings and generated demo history, "
                               "not a real patient record.", banner))
    story.append(Paragraph(f"<b>{escape(DISCLAIMER)}</b> Findings are decision support for clinician review; this "
                           "report does not contain a diagnosis.", notice))

    info = [
        ["Patient", escape(patient["name"]), "MRN", escape(patient.get("mrn") or "—")],
        ["Date of birth", str(patient.get("dob") or "—"), "Sex", escape(patient.get("sex") or "—")],
        ["Report range", f"{data['range']} ({_dt(data['start'])} → {_dt(data['end'])} UTC)", "Generated",
         f"{_dt(data['generated_at'])} UTC by {escape(data['generated_by'])}"],
    ]
    table = Table(info, colWidths=[26 * mm, 62 * mm, 22 * mm, 64 * mm])
    table.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 8.5), ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                               ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                               ("LINEBELOW", (0, 0), (-1, -1), 0.3, GRID)]))
    story.append(table)

    versions = data["versions"]
    story.append(Paragraph("Model and rule versions", h2))
    story.append(Paragraph(
        f"Beat model in this range: <b>{escape(', '.join(versions['models']) or 'none (rules-only / aggregates)')}</b>. "
        f"Rhythm rules: <b>{escape(', '.join(versions['rules']) or '—')}</b>. "
        f"Currently deployed: model {escape(str(versions['current_model'] or 'not loaded (rules-only mode)'))}, "
        f"rules {escape(str(versions['current_rules']))}.", body))

    s = data["summary"]
    story.append(Paragraph("Trend summary", h2))
    if not s["n_windows"]:
        story.append(Paragraph("No monitoring data was recorded in this range.", body))
    else:
        story.append(_table([
            ["Monitored", "Mean HR", "SDNN", "RMSSD", "S burden", "V burden", "Mean SQI", "Highest risk"],
            [f"{s['monitored_s'] / 3600:.1f} h", _fmt(s["hr_bpm"], " bpm", 0), _fmt(s["sdnn_ms"], " ms", 0),
             _fmt(s["rmssd_ms"], " ms", 0), _fmt(s["s_burden_pct"], " %", 2), _fmt(s["v_burden_pct"], " %", 2),
             _fmt(s["sqi"], "", 2), _fmt(s["risk_score_max"], " / 100")],
        ], [22 * mm] * 8))
        story.append(Spacer(1, 6))
        rows: list[list[Any]] = [[data["bucket_label"], "Monitored", "HR (bpm)", "S %", "V %", "Max risk", "Findings"]]
        for b in data["daily"]:
            findings = ", ".join(f"{f['label']} ×{f['count']}" for f in b["findings"][:4]) or "—"
            rows.append([_dt(b["t"]), f"{b['n_windows'] * 10 / 60:.0f} min", _fmt(b["hr_bpm"], "", 0),
                         _fmt(b["s_burden_pct"], "", 2), _fmt(b["v_burden_pct"], "", 2), _fmt(b["risk_score"]),
                         Paragraph(escape(findings), cell)])
        story.append(_table(rows, [28 * mm, 18 * mm, 16 * mm, 13 * mm, 13 * mm, 16 * mm, 72 * mm]))
        if data.get("gaps"):
            hours = sum(g["duration_s"] for g in data["gaps"]) / 3600
            story.append(Paragraph(f"Monitoring gaps: {len(data['gaps'])} period(s) without data, {hours:.1f} h in "
                                   "total. Gaps are shown as recorded and never interpolated.", small))
        if s["findings"]:
            story.append(Spacer(1, 6))
            rows = [["Rhythm finding (rule)", "Highest severity", "Windows"]]
            rows += [[f"{f['label']} ({f['rule_id']})", f["severity"], f["count"]] for f in s["findings"]]
            story.append(_table(rows, [90 * mm, 40 * mm, 30 * mm]))

    story.append(Paragraph("Alerts", h2))
    alerts = data["alerts"]
    if not alerts:
        story.append(Paragraph("No alerts were raised in this range.", body))
    else:
        rows = [["Raised (UTC)", "Tier", "Rule", "Alert", "State", "Acknowledged by", "Note"]]
        for a in alerts:
            rows.append([_dt(a["created_at"]), a["tier"].upper(), a["rule_id"],
                         Paragraph(escape(f"{a['title']} — {a.get('detail') or ''}"), cell), a["state"],
                         Paragraph(escape(a.get("acknowledged_by") or "—"), cell),
                         Paragraph(escape(a.get("note") or "—"), cell)])
        table = _table(rows, [25 * mm, 14 * mm, 22 * mm, 46 * mm, 21 * mm, 24 * mm, 24 * mm])
        for i, a in enumerate(alerts, start=1):
            table.setStyle(TableStyle([("TEXTCOLOR", (1, i), (1, i), TIER_COLOURS.get(a["tier"], INK)),
                                       ("FONTNAME", (1, i), (1, i), "Helvetica-Bold")]))
        story.append(table)
        story.append(Paragraph("Alerts are never closed automatically; every state change is recorded in the audit "
                               "log. Each alert stores its evidence snapshot, rule id and version, and model version.",
                               small))

    story.append(Paragraph("Monitoring sessions", h2))
    sessions = data["sessions"]
    if not sessions:
        story.append(Paragraph("No sessions in this range.", body))
    else:
        rows = [["Started (UTC)", "Source", "Duration", "Beats", "V %", "Max risk", "Alerts", "Model / rules"]]
        for ses in sessions[:40]:
            st = ses["stats"]
            source = ses["source"] + (f" {ses['record']}" if ses.get("record") else "")
            rows.append([_dt(ses["started_at"]), source, f"{(st.get('duration_s') or 0) / 60:.1f} min",
                         st.get("beats", "—"), _fmt(st.get("v_burden_pct"), "", 1), st.get("max_risk_score", "—"),
                         st.get("alerts", "—"),
                         Paragraph(escape(f"{ses.get('model_version') or 'none'} / {ses.get('rule_version') or '—'}"), cell)])
        story.append(_table(rows, [27 * mm, 22 * mm, 18 * mm, 14 * mm, 12 * mm, 16 * mm, 13 * mm, 54 * mm]))
        if len(sessions) > 40:
            story.append(Paragraph(f"{len(sessions) - 40} earlier sessions are not listed.", small))

    story.append(Paragraph("Known limitations", h2))
    for item in data["limitations"]:
        story.append(Paragraph(f"• {escape(str(item))}", small))

    def footer(canvas: Any, doc: Any) -> None:
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(INK)
        canvas.drawString(18 * mm, 10 * mm, DISCLAIMER)
        canvas.drawRightString(A4[0] - 18 * mm, 10 * mm, f"Page {doc.page}")
        canvas.restoreState()

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm,
                            bottomMargin=18 * mm, title="CardioSense AI monitoring report", author="CardioSense AI")
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()
