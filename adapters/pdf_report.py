"""
Layer: Adapter — render a ReviewReportOut as a PDF (Arabic RTL or English LTR).

reportlab does not shape or reorder Arabic, so text is shaped with
arabic-reshaper and reordered with python-bidi *per wrapped line* (wrapping the
logical text first, then reordering each line — the only order that is correct).
"""
from __future__ import annotations

import io
import os
import re
from pathlib import Path

import arabic_reshaper
from bidi.algorithm import get_display
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Flowable, SimpleDocTemplate, Spacer, Table, TableStyle

from schemas.review import L10n, ReviewItemOut, ReviewReportOut

FONT = "RptSans"
FONT_BOLD = "RptSans-Bold"

_FONT_CANDIDATES = [
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
     "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ("/Library/Fonts/Arial Unicode.ttf", "/Library/Fonts/Arial Unicode.ttf"),
    ("/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
     "/System/Library/Fonts/Supplemental/Arial Unicode.ttf"),
]
_ARABIC = re.compile(r"[؀-ۿݐ-ݿﭐ-﷿ﹰ-﻿]")
_registered = False


def _register_fonts() -> None:
    global _registered
    if _registered:
        return
    env = os.environ.get("PDF_FONT_PATH")  # optional override: regular[,bold]
    candidates = list(_FONT_CANDIDATES)
    if env:
        parts = [p.strip() for p in env.split(",")]
        candidates.insert(0, (parts[0], parts[1] if len(parts) > 1 else parts[0]))
    for regular, bold in candidates:
        if Path(regular).is_file() and Path(bold).is_file():
            pdfmetrics.registerFont(TTFont(FONT, regular))
            pdfmetrics.registerFont(TTFont(FONT_BOLD, bold))
            _registered = True
            return
    raise RuntimeError(
        "No Arabic-capable TrueType font found for the PDF export. Install "
        "fonts-dejavu-core or set PDF_FONT_PATH=/path/regular.ttf[,/path/bold.ttf]."
    )


# ── RTL-aware text flowable ───────────────────────────────────────────────────

def _shape(text: str, rtl: bool) -> str:
    if _ARABIC.search(text):
        text = arabic_reshaper.reshape(text)
        return get_display(text, base_dir="R" if rtl else "L")
    return get_display(text, base_dir="R") if rtl else text


def _wrap(text: str, font: str, size: float, width: float, rtl: bool) -> list[str]:
    """Wrap logical text, return display-ordered lines."""
    out: list[str] = []
    for para in text.split("\n"):
        words = para.split(" ")
        line = ""
        for w in words:
            trial = f"{line} {w}" if line else w
            if line and stringWidth(_shape(trial, rtl), font, size) > width:
                out.append(_shape(line, rtl))
                line = w
            else:
                line = trial
        out.append(_shape(line, rtl))
    return out


class RText(Flowable):
    def __init__(self, text, *, rtl, size=9.5, bold=False, color=colors.HexColor("#111827"),
                 leading=None, indent=0.0, literal=False):
        """`literal`: an LTR token (number, id, file name) — never reordered/mirrored,
        only aligned to the page direction."""
        super().__init__()
        self.text, self.rtl, self.size, self.bold = text or "", rtl, size, bold
        self.shape_rtl = rtl and not literal
        self.color, self.leading, self.indent = color, leading or size * 1.45, indent
        self.lines: list[str] = []

    @property
    def _font(self):
        return FONT_BOLD if self.bold else FONT

    def wrap(self, aw, ah):
        self.width = aw
        self.lines = _wrap(self.text, self._font, self.size, max(aw - self.indent, 10), self.shape_rtl)
        self.height = self.leading * len(self.lines)
        return self.width, self.height

    def draw(self):
        c = self.canv
        c.setFont(self._font, self.size)
        c.setFillColor(self.color)
        for i, line in enumerate(self.lines):
            y = self.height - (i + 1) * self.leading + self.leading * 0.28
            if self.rtl:
                c.drawRightString(self.width - self.indent, y, line)
            else:
                c.drawString(self.indent, y, line)


# ── Static UI strings ─────────────────────────────────────────────────────────

T = {
    "title": ("Cross-Casting Review Report", "تقرير مراجعة التدقيق الحسابي (Cross-Casting)"),
    "file": ("File", "الملف"),
    "job": ("Job ID", "رقم المهمة"),
    "generated": ("Generated", "تاريخ الإصدار"),
    "scope": ("Scope", "النطاق"),
    "scopes": {
        "all": ("All findings", "كل الملاحظات"),
        "casting": ("Casting only", "التدقيق (casting) فقط"),
        "cross_casting": ("Cross-casting only", "التدقيق المتقاطع فقط"),
        "verification": ("OCR/VLM verification only", "التحقق OCR/VLM فقط"),
    },
    "summary": ("Summary", "الملخص"),
    "total": ("Total findings", "إجمالي الملاحظات"),
    "open": ("Open", "مفتوحة"),
    "acknowledged": ("Acknowledged", "تمت مراجعتها"),
    "resolved": ("Resolved", "تم حلها"),
    "casting": ("Casting errors", "أخطاء التدقيق"),
    "cross": ("Cross-casting errors", "أخطاء التقاطع"),
    "verification": ("OCR/VLM disagreements", "اختلافات OCR/VLM"),
    "tables": ("Tables with findings", "جداول فيها ملاحظات"),
    "checked": ("Tables / comparisons checked", "الجداول / المقارنات المفحوصة"),
    "how": ("How to resolve", "كيفية المعالجة"),
    "table": ("Table", "الجدول"),
    "page": ("Page", "صفحة"),
    "sheet": ("Sheet", "الورقة"),
    "row": ("Row", "الصف"),
    "section": ("Under", "ضمن"),
    "column": ("Column", "العمود"),
    "cell": ("Cell", "الخلية"),
    "relation": ("Relation", "العلاقة"),
    "difference": ("Difference", "الفرق"),
    "causes": ("Likely causes", "الأسباب المحتملة"),
    "hints": ("Hints from the numbers", "ملاحظات من الأرقام"),
    "steps": ("Steps to resolve", "خطوات الحل"),
    "related": ("Consequences merged into this finding", "نتائج مرتبطة بهذه الملاحظة"),
    "none": ("No findings in this scope.", "لا توجد ملاحظات في هذا النطاق."),
    "footer": ("Cross-Casting Audit", "Cross-Casting Audit"),
    "page_n": ("Page", "صفحة"),
    "priority": ("Priority", "الأولوية"),
}
SEV_COLOR = {
    "critical": colors.HexColor("#B91C1C"),
    "high": colors.HexColor("#C2410C"),
    "medium": colors.HexColor("#A16207"),
    "low": colors.HexColor("#475569"),
}
SEV_BG = {
    "critical": colors.HexColor("#FEE2E2"),
    "high": colors.HexColor("#FFEDD5"),
    "medium": colors.HexColor("#FEF3C7"),
    "low": colors.HexColor("#F1F5F9"),
}
MUTED = colors.HexColor("#4B5563")
GRID = colors.HexColor("#D1D5DB")


def render_review_pdf(report: ReviewReportOut, lang: str = "ar") -> bytes:
    if lang not in ("ar", "en"):
        raise ValueError("lang must be 'ar' or 'en'")
    _register_fonts()
    rtl = lang == "ar"
    i = 1 if rtl else 0

    def t(key):  # static string
        return T[key][i]

    def lz(x: L10n) -> str:
        return x.ar if rtl else x.en

    margin = 15 * mm
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4, leftMargin=margin, rightMargin=margin,
        topMargin=18 * mm, bottomMargin=16 * mm,
        title=t("title"), author="Cross-Casting",
    )
    width = A4[0] - 2 * margin

    def R(text, **kw):
        return RText(text, rtl=rtl, **kw)

    def kv_table(rows, label_w=45 * mm):
        data = []
        for label, value in rows:
            cells = [R(label, size=9, color=MUTED), R(str(value), size=9.5, bold=True, literal=True)]
            data.append(cells[::-1] if rtl else cells)
        cw = [width - label_w, label_w] if rtl else [label_w, width - label_w]
        tb = Table(data, colWidths=cw)
        tb.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LINEBELOW", (0, 0), (-1, -1), 0.3, GRID),
            ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        return tb

    story: list = []
    story.append(R(t("title"), size=18, bold=True, color=colors.HexColor("#1B3A4B")))
    story.append(Spacer(1, 4 * mm))
    story.append(kv_table([
        (t("file"), report.filename),
        (t("job"), report.job_id),
        (t("generated"), report.generated_at.strftime("%Y-%m-%d %H:%M UTC")),
        (t("scope"), T["scopes"][report.scope][i]),
    ]))
    story.append(Spacer(1, 5 * mm))

    # Conclusion banner
    ok = report.is_clean
    banner = Table([[R(lz(report.conclusion), size=11, bold=True,
                       color=colors.HexColor("#065F46" if ok else "#991B1B"))]], colWidths=[width])
    banner.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#D1FAE5" if ok else "#FEE2E2")),
        ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
    ]))
    story += [banner, Spacer(1, 5 * mm)]

    # Summary
    s, cv = report.summary, report.coverage
    rows = [
        (t("total"), s.total), (t("open"), s.open), (t("acknowledged"), s.acknowledged),
        (t("resolved"), s.resolved), (t("casting"), s.casting), (t("cross"), s.cross_casting),
        (t("verification"), s.verification), (t("tables"), s.tables_with_issues),
    ]
    if cv.tables_processed is not None:
        rows.append((t("checked"), f"{cv.tables_processed} / {cv.total_checks if cv.total_checks is not None else '—'}"))
    story += [R(t("summary"), size=12, bold=True, color=colors.HexColor("#0F766E")),
              Spacer(1, 2 * mm), kv_table(rows), Spacer(1, 5 * mm)]

    # How to resolve
    story.append(R(t("how"), size=12, bold=True, color=colors.HexColor("#0F766E")))
    story.append(Spacer(1, 2 * mm))
    steps = report.how_to_resolve.ar if rtl else report.how_to_resolve.en
    for n, line in enumerate(steps, 1):
        story.append(R(f"{n}. {line}", size=9.5, indent=2))
        story.append(Spacer(1, 1.2 * mm))
    story.append(Spacer(1, 4 * mm))

    # Findings by table
    if not report.tables:
        story.append(R(t("none"), size=11, color=MUTED))
    for grp in report.tables:
        place = []
        if grp.page is not None:
            place.append(f"{t('page')} {grp.page}")
        if grp.sheet:
            place.append(f"{t('sheet')} {grp.sheet}")
        head = f"{t('table')}: {grp.table_name}" + (f"  ({' · '.join(place)})" if place else "")
        head_tb = Table([[R(head, size=11, bold=True, color=colors.white)]], colWidths=[width])
        head_tb.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#1B3A4B")),
            ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ]))
        story += [Spacer(1, 3 * mm), head_tb, Spacer(1, 2 * mm)]
        for item in grp.items:
            story += [_card(item, report, rtl, i, width, R, t, lz), Spacer(1, 3 * mm)]

    def footer(canvas, d):
        canvas.saveState()
        canvas.setFont(FONT, 8)
        canvas.setFillColor(MUTED)
        label = f"{t('page_n')} {d.page}"
        left, right = _shape("Cross-Casting Audit · " + report.filename[:60], False), _shape(label, rtl)
        canvas.drawString(margin, 9 * mm, left)
        canvas.drawRightString(A4[0] - margin, 9 * mm, right)
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()


def _card(item: ReviewItemOut, report, rtl, i, width, R, t, lz) -> Table:
    sev = item.severity if item.severity in SEV_COLOR else "low"
    sev_label = report.labels.severity.get(item.severity)
    status_label = report.labels.status.get(item.status)
    head = (f"#{item.priority}  ·  {lz(sev_label) if sev_label else item.severity}"
            f"  ·  {lz(status_label) if status_label else item.status}  ·  {lz(item.title)}")

    loc = item.location
    loc_parts = []
    if loc.page is not None:
        loc_parts.append(f"{t('page')} {loc.page}")
    if loc.sheet:
        loc_parts.append(f"{t('sheet')}: {loc.sheet}")
    if loc.row:
        loc_parts.append(f"{t('row')}: {loc.row}")
    if loc.section:
        loc_parts.append(f"{t('section')}: {loc.section}")
    if loc.column:
        loc_parts.append(f"{t('column')}: {loc.column}")
    if loc.cell:
        loc_parts.append(f"{t('cell')}: {loc.cell}")

    # Figures: value0 | value1 | difference
    figs = [(lz(v.label), v.value if v.value is not None else "—") for v in item.values]
    if item.delta is not None:
        figs.append((t("difference"), item.delta))
    fig_cells = [[R(lab, size=8, color=MUTED), R(val, size=11, bold=True, literal=True)] for lab, val in figs]
    top = [c[0] for c in fig_cells]
    bot = [c[1] for c in fig_cells]
    if rtl:
        top, bot = top[::-1], bot[::-1]
    fig_tb = Table([top, bot], colWidths=[(width - 16) / max(len(figs), 1)] * len(figs))
    fig_tb.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F9FAFB")),
        ("BOX", (0, 0), (-1, -1), 0.4, GRID), ("INNERGRID", (0, 0), (-1, -1), 0.3, GRID),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("ALIGN", (0, 0), (-1, -1), "RIGHT" if rtl else "LEFT"),
    ]))

    def bullets(heading, lines, numbered=False):
        out = [R(heading, size=9.5, bold=True, color=colors.HexColor("#0F766E"))]
        for n, line in enumerate(lines, 1):
            mark = f"{n}." if numbered else "•"
            out.append(R(f"{mark} {line}", size=9, indent=2))
        return out

    body: list = [R(head, size=10.5, bold=True, color=SEV_COLOR[sev])]
    rows: list[list] = [[body[0]]]
    if loc_parts:
        rows.append([R("  |  ".join(loc_parts), size=9, color=MUTED)])
    if loc.relation:
        rows.append([R(f"{t('relation')}: {loc.relation}", size=9, color=MUTED)])
    rows.append([fig_tb])
    rows.append([R(lz(item.explanation), size=9.5)])
    causes = item.likely_causes.ar if rtl else item.likely_causes.en
    if causes:
        rows.append([bullets(t("causes"), causes)])
    hints = item.hints.ar if rtl else item.hints.en
    if hints:
        rows.append([bullets(t("hints"), hints)])
    steps = item.steps.ar if rtl else item.steps.en
    if steps:
        rows.append([bullets(t("steps"), steps, numbered=True)])
    if item.related:
        rows.append([bullets(t("related"), item.related)])

    card = Table(rows, colWidths=[width])
    card.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.6, SEV_COLOR[sev]),
        ("BACKGROUND", (0, 0), (0, 0), SEV_BG[sev]),
        ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return card
