"""
Layer: Application — turn a job's stored issues into an actionable review report.

Pure: takes plain objects (ORM rows or anything with the same attributes) and
returns schemas.review models.  No DB, no I/O, no arithmetic on the document —
it only classifies, ranks, groups and attaches the bilingual guidance.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

from domain.cross_casting import DESC_COL_VS_GRAND, DESC_ROW_VS_COL, DESC_ROW_VS_GRAND
from schemas.review import (
    L10n,
    L10nList,
    ReviewCoverageOut,
    ReviewItemOut,
    ReviewLabelsOut,
    ReviewLocationOut,
    ReviewReportOut,
    ReviewSummaryOut,
    ReviewTableCountsOut,
    ReviewTableGroupOut,
    ReviewValueOut,
)

from . import guidance as g

SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}
CATEGORY_RANK = {"cross_casting": 0, "casting": 1, "verification": 2, "other": 3}
SCOPES = ("all", "casting", "cross_casting", "verification")


# ── helpers ───────────────────────────────────────────────────────────────────

def _dec(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _fmt(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return f"{value:,.2f}"


def _l10n(pair: g.Pair) -> L10n:
    return L10n(en=pair[0], ar=pair[1])


def _l10n_list(pairs: Iterable[g.Pair]) -> L10nList:
    pairs = list(pairs)
    return L10nList(en=[p[0] for p in pairs], ar=[p[1] for p in pairs])


def file_type(mime: str, filename: str) -> str:
    if filename.lower().endswith((".xlsx", ".xls")) or "spreadsheetml" in (mime or ""):
        return "excel"
    return "pdf" if mime == "application/pdf" else "image"


def classify(issue) -> tuple[str, str]:
    """(category, check) of a stored issue."""
    ref = issue.source_reference or {}
    if issue.issue_type == "CASTING_ERROR":
        if "trial balance" in (ref.get("relation") or "").lower():
            return "casting", "trial_balance"
        return "casting", ("horizontal" if ref.get("axis") == "row" else "vertical")
    if issue.issue_type == "CROSS_CASTING_ERROR":
        desc = issue.description or ""
        if desc == DESC_ROW_VS_GRAND:
            return "cross_casting", "row_vs_grand"
        if desc == DESC_COL_VS_GRAND:
            return "cross_casting", "col_vs_grand"
        return "cross_casting", "row_vs_col"
    if issue.issue_type == "OCR_VLM_DISAGREEMENT":
        return "verification", "ocr_vlm"
    return "other", "other"


def _sides(issue, category: str, check: str) -> tuple[Decimal | None, Decimal | None, Decimal | None]:
    """(first, second, delta) with delta = first − second."""
    delta = _dec(issue.difference)
    if category == "verification":
        return None, None, None  # values are text, not amounts
    if category == "cross_casting":
        row_total, col_total = _dec(issue.expected_value), _dec(issue.actual_value)
        if check == "row_vs_grand" and row_total is not None and delta is not None:
            return row_total, row_total - delta, delta
        if check == "col_vs_grand" and col_total is not None and delta is not None:
            return col_total, col_total - delta, delta
        return row_total, col_total, delta
    return _dec(issue.expected_value), _dec(issue.actual_value), delta


def _direction(delta: Decimal | None) -> str:
    if delta is None:
        return "n/a"
    return "equal" if delta == 0 else ("first_higher" if delta > 0 else "first_lower")


def _location(issue, table_meta: dict | None) -> ReviewLocationOut:
    ref = issue.source_reference or {}
    bbox = ref.get("bbox")
    meta = table_meta or {}
    page = ref.get("page")
    if page is None and ref.get("source") == "pdf":
        page = meta.get("page")
    return ReviewLocationOut(
        source=ref.get("source"),
        page=page,
        sheet=ref.get("sheet") or meta.get("sheet"),
        row=ref.get("row"),
        column=ref.get("column"),
        cell=ref.get("cell"),
        relation=ref.get("relation"),
        section=ref.get("section"),
        bbox=[float(x) for x in bbox] if bbox else None,
    )


def build_item(issue, table_meta: dict | None) -> ReviewItemOut:
    category, check = classify(issue)
    entry = g.CATALOG[check]
    first, second, delta = _sides(issue, category, check)
    labels = g.VALUE_LABELS[check]

    if category == "verification":
        values = [
            ReviewValueOut(label=_l10n(labels[0]), value=issue.expected_value),
            ReviewValueOut(label=_l10n(labels[1]), value=issue.actual_value),
        ]
    else:
        values = [
            ReviewValueOut(label=_l10n(labels[0]), value=_fmt(first)),
            ReviewValueOut(label=_l10n(labels[1]), value=_fmt(second)),
        ]
    hints = g.hints_for(first, second, delta) if category != "verification" else []
    ref = issue.source_reference or {}
    return ReviewItemOut(
        id=str(issue.id),
        priority=0,  # assigned after ranking
        category=category,
        check=check,
        issue_type=issue.issue_type,
        severity=issue.severity,
        status=issue.status,
        table_id=str(issue.table_id) if issue.table_id else None,
        table_name=issue.table_name,
        location=_location(issue, table_meta),
        values=values,
        delta=_fmt(delta),
        direction=_direction(delta),
        title=_l10n(entry.title),
        explanation=_l10n(entry.explanation),
        likely_causes=_l10n_list(entry.causes),
        hints=_l10n_list(hints),
        steps=_l10n_list(entry.steps),
        related=list(ref.get("related") or []),
    )


def _rank_key(item: ReviewItemOut):
    delta = _dec((item.delta or "").replace(",", "")) or Decimal(0)
    return (
        SEVERITY_RANK.get(item.severity, 9),
        CATEGORY_RANK.get(item.category, 9),
        -abs(delta),
        item.table_name or "",
    )


def _conclusion(scope: str, open_n: int, tables_n: int, coverage: ReviewCoverageOut) -> L10n:
    checked_en = checked_ar = ""
    if coverage.tables_processed is not None:
        checked_en = f" ({coverage.tables_processed} table(s)"
        checked_ar = f" ({coverage.tables_processed} جدول"
        if coverage.total_checks is not None:
            checked_en += f", {coverage.total_checks} comparisons"
            checked_ar += f"، {coverage.total_checks} مقارنة"
        checked_en += " checked)"
        checked_ar += " تم فحصها)"
    if open_n == 0:
        return L10n(
            en=f"No open arithmetic issues{checked_en}.",
            ar=f"لا توجد ملاحظات حسابية مفتوحة{checked_ar}.",
        )
    return L10n(
        en=f"{open_n} open issue(s) in {tables_n} table(s) need review{checked_en}.",
        ar=f"{open_n} ملاحظة مفتوحة في {tables_n} جدول تحتاج إلى مراجعة{checked_ar}.",
    )


def coverage_from_report(report: dict | None) -> ReviewCoverageOut:
    if not report:
        return ReviewCoverageOut()
    results = report.get("results") or []

    def total(key: str) -> int | None:
        vals = [r.get(key) for r in results]
        return None if not vals or any(v is None for v in vals) else sum(vals)

    return ReviewCoverageOut(
        tables_processed=report.get("tables_processed"),
        total_checks=report.get("total_checks"),
        casting_checks=total("casting_checks"),
        cross_casting_checks=total("cross_casting_checks"),
        pages_skipped=list(report.get("pages_skipped") or []),
    )


# ── public ────────────────────────────────────────────────────────────────────

def build_review(
    job,
    issues: list,
    tables: list,
    *,
    scope: str = "all",
    status: str | None = None,
    now: datetime | None = None,
) -> ReviewReportOut:
    """Build the review report for one job.

    * `priority` is ranked over ALL the job's issues, so it stays stable when
      the UI filters by scope/status.
    * `summary` counts the whole scope (all statuses) — handy for tab badges.
    * `tables` lists only the items matching `status` (if given).
    * `is_clean` is true when the scope has no OPEN issue.
    """
    if scope not in SCOPES:
        raise ValueError(f"unknown scope {scope!r}")
    meta = {
        str(t.id): {"name": t.table_name, "page": t.page, "sheet": t.sheet}
        for t in tables
    }
    items = [build_item(i, meta.get(str(i.table_id))) for i in issues]
    items.sort(key=_rank_key)
    for n, it in enumerate(items, start=1):
        it.priority = n

    scoped = [i for i in items if scope == "all" or i.category == scope]
    shown = [i for i in scoped if status is None or i.status == status]

    by_sev: dict[str, int] = {}
    for it in scoped:
        by_sev[it.severity] = by_sev.get(it.severity, 0) + 1

    groups: dict[str, list[ReviewItemOut]] = {}
    for it in shown:  # already priority-ordered → groups ordered by best item
        groups.setdefault(it.table_id or f"name:{it.table_name}", []).append(it)

    group_out: list[ReviewTableGroupOut] = []
    for key, its in groups.items():
        m = meta.get(its[0].table_id or "", {})
        group_out.append(ReviewTableGroupOut(
            table_id=its[0].table_id,
            table_name=its[0].table_name or m.get("name") or "—",
            page=m.get("page"),
            sheet=m.get("sheet"),
            max_severity=min((i.severity for i in its), key=lambda s: SEVERITY_RANK.get(s, 9)),
            counts=ReviewTableCountsOut(
                total=len(its),
                open=sum(1 for i in its if i.status == "open"),
                casting=sum(1 for i in its if i.category == "casting"),
                cross_casting=sum(1 for i in its if i.category == "cross_casting"),
                verification=sum(1 for i in its if i.category == "verification"),
            ),
            items=its,
        ))

    open_n = sum(1 for i in scoped if i.status == "open")
    summary = ReviewSummaryOut(
        total=len(scoped),
        open=open_n,
        acknowledged=sum(1 for i in scoped if i.status == "acknowledged"),
        resolved=sum(1 for i in scoped if i.status == "resolved"),
        casting=sum(1 for i in scoped if i.category == "casting"),
        cross_casting=sum(1 for i in scoped if i.category == "cross_casting"),
        verification=sum(1 for i in scoped if i.category == "verification"),
        by_severity=by_sev,
        tables_with_issues=len({i.table_id or i.table_name for i in scoped}),
    )
    coverage = coverage_from_report(job.report)
    open_tables = len({i.table_id or i.table_name for i in scoped if i.status == "open"})

    return ReviewReportOut(
        job_id=str(job.id),
        filename=job.filename,
        file_type=file_type(job.mime_type, job.filename),
        job_status=job.status,
        scope=scope,  # type: ignore[arg-type]
        generated_at=now or datetime.now(timezone.utc),
        is_clean=open_n == 0,
        conclusion=_conclusion(scope, open_n, open_tables, coverage),
        summary=summary,
        coverage=coverage,
        tables=group_out,
        labels=ReviewLabelsOut(
            status={k: _l10n(v) for k, v in g.STATUS_LABELS.items()},
            severity={k: _l10n(v) for k, v in g.SEVERITY_LABELS.items()},
            category={k: _l10n(v) for k, v in g.CATEGORY_LABELS.items()},
        ),
        how_to_resolve=_l10n_list(g.HOW_TO_RESOLVE),
    )
