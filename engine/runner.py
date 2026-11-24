"""
Engine runner — wraps CrossCastingPipeline with job lifecycle management.

Called from BackgroundTasks in the API layer. Updates PostgreSQL as it
progresses through processing stages.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy.engine import Engine

from adapters.excel_reader import read_excel
from application.factory import build_pipeline
from config.settings import Settings
from db.database import get_session
from db.models import AuditJob, ExtractedTable, Issue, ProcessingLog

log = logging.getLogger(__name__)

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PDF = "application/pdf"
PNG = "image/png"
JPEG = "image/jpeg"


def run_pipeline_task(
    job_id: str,
    content: bytes,
    mime: str,
    filename: str,
    engine: Engine,
    settings: Settings,
) -> None:
    """
    Background task: run the full AI pipeline and persist all results.

    Lifecycle: QUEUED → PROCESSING → (stage tracking) → COMPLETED | FAILED
    """
    log.info("Starting pipeline for job %s (%s)", job_id, filename)
    _set_status(engine, job_id, "PROCESSING", "DOCUMENT_LOADING")
    _log_stage(engine, job_id, "DOCUMENT_LOADING", "started")

    try:
        pipeline = build_pipeline(settings)
        _log_stage(engine, job_id, "DOCUMENT_LOADING", "completed")

        _set_stage(engine, job_id, "TABLE_EXTRACTION")
        _log_stage(engine, job_id, "TABLE_EXTRACTION", "started")

        is_excel = mime == XLSX or filename.lower().endswith((".xlsx", ".xls"))

        if is_excel:
            tables = read_excel(content, source_file=filename)
            _log_stage(engine, job_id, "TABLE_EXTRACTION", "completed",
                       f"Extracted {len(tables)} table(s) from Excel")

            _set_stage(engine, job_id, "NUMBER_NORMALIZATION")
            _log_stage(engine, job_id, "NUMBER_NORMALIZATION", "started")
            # Normalization is embedded in read_excel / pipeline — mark as completed
            _log_stage(engine, job_id, "NUMBER_NORMALIZATION", "completed")

            _set_stage(engine, job_id, "CASTING")
            _log_stage(engine, job_id, "CASTING", "started")
            report = pipeline.process_tables(tables, source_file=filename)

        elif mime == PDF:
            report = pipeline.process_pdf(content, source_file=filename)
            _log_stage(engine, job_id, "TABLE_EXTRACTION", "completed",
                       f"Extracted {report.tables_processed} table(s) from PDF"
                       + _vlm_note(pipeline))
            _log_stage(engine, job_id, "NUMBER_NORMALIZATION", "completed")
            _log_stage(engine, job_id, "OCR_VLM_VERIFICATION", "completed")

        elif mime in {PNG, JPEG}:
            report = pipeline.process_image(content, mime, source_file=filename)
            _log_stage(engine, job_id, "TABLE_EXTRACTION", "completed",
                       f"Extracted {report.tables_processed} table(s) from image"
                       + _vlm_note(pipeline))
            _log_stage(engine, job_id, "OCR_VLM_VERIFICATION", "completed")

        else:
            raise ValueError(f"Unsupported MIME type: {mime!r}")

        _log_stage(engine, job_id, "CASTING", "completed",
                   f"{report.total_casting_errors} casting error(s)")
        _set_stage(engine, job_id, "CROSS_CASTING")
        _log_stage(engine, job_id, "CROSS_CASTING", "completed",
                   f"{report.total_cross_casting_errors} cross-casting error(s)")

        _set_stage(engine, job_id, "REPORT_GENERATION")
        _log_stage(engine, job_id, "REPORT_GENERATION", "started")

        source = "excel" if is_excel else "pdf"
        _persist_results(engine, job_id, report, source)

        _log_stage(engine, job_id, "REPORT_GENERATION", "completed")

        from schemas.api import report_to_out
        report_out = report_to_out(report)

        with get_session(engine) as session:
            job = session.get(AuditJob, uuid.UUID(job_id))
            if job:
                job.status = "COMPLETED"
                job.stage = "REPORT_GENERATION"
                job.completed_at = datetime.now(timezone.utc)
                job.tables_processed = report.tables_processed
                job.total_casting_errors = report.total_casting_errors
                job.total_cross_casting_errors = report.total_cross_casting_errors
                job.is_clean = report.is_clean
                job.report = report_out.model_dump(mode="json")

        log.info(
            "Job %s COMPLETED: %d tables, %d casting + %d cross-casting errors",
            job_id, report.tables_processed,
            report.total_casting_errors, report.total_cross_casting_errors,
        )

    except Exception as exc:
        log.exception("Job %s FAILED: %s", job_id, exc)
        _set_status(engine, job_id, "FAILED", error=str(exc))
        _log_stage(engine, job_id, "FAILED", "failed", message=str(exc))


# ── Stage / status helpers ─────────────────────────────────────────────────────

def _vlm_note(pipeline) -> str:
    """Which VLM produced the tables, and what it cost — never a secret."""
    label = getattr(pipeline, "vlm_label", "unknown")
    usage = pipeline.vlm_usage() if hasattr(pipeline, "vlm_usage") else None
    note = f" via {label}"
    if usage is not None and hasattr(usage, "summary"):
        note += f" [{usage.summary()}]"
    return note


def _set_status(
    engine: Engine,
    job_id: str,
    status: str,
    stage: str | None = None,
    error: str | None = None,
) -> None:
    with get_session(engine) as session:
        job = session.get(AuditJob, uuid.UUID(job_id))
        if not job:
            return
        job.status = status
        if stage:
            job.stage = stage
        if status == "PROCESSING" and job.started_at is None:
            job.started_at = datetime.now(timezone.utc)
        if status in ("COMPLETED", "FAILED"):
            job.completed_at = datetime.now(timezone.utc)
        if error:
            job.error_message = error


def _set_stage(engine: Engine, job_id: str, stage: str) -> None:
    with get_session(engine) as session:
        job = session.get(AuditJob, uuid.UUID(job_id))
        if job:
            job.stage = stage


def _log_stage(
    engine: Engine,
    job_id: str,
    stage: str,
    status: str,
    message: str | None = None,
) -> None:
    with get_session(engine) as session:
        entry = ProcessingLog(
            id=uuid.uuid4(),
            job_id=uuid.UUID(job_id),
            stage=stage,
            status=status,
            message=message,
            timestamp=datetime.now(timezone.utc),
        )
        session.add(entry)


# ── Result persistence ─────────────────────────────────────────────────────────

def clear_job_results(session, job_id: uuid.UUID, *, include_logs: bool = False) -> None:
    """Delete a job's issues and extracted tables (and optionally its stage log).

    Used before a re-run so results from an earlier run are never mixed with,
    or duplicated by, the new one.
    """
    session.query(Issue).filter(Issue.job_id == job_id).delete(synchronize_session=False)
    session.query(ExtractedTable).filter(ExtractedTable.job_id == job_id).delete(
        synchronize_session=False,
    )
    if include_logs:
        session.query(ProcessingLog).filter(ProcessingLog.job_id == job_id).delete(
            synchronize_session=False,
        )


def _persist_results(engine: Engine, job_id: str, report, source: str) -> None:
    """Save extracted tables and unified issues derived from the pipeline report.

    Replaces anything saved by a previous run of the same job.
    """
    with get_session(engine) as session:
        clear_job_results(session, uuid.UUID(job_id))
        for vr in report.validation_results:
            table = vr.table

            # ── Extracted table ──────────────────────────────────────────────
            et = ExtractedTable(
                id=uuid.uuid4(),
                job_id=uuid.UUID(job_id),
                table_name=table.title,
                page=table.page,
                sheet=table.sheet,
                row_count=len(table.rows),
                col_count=len(table.headers),
                raw_data={
                    "title": table.title,
                    "headers": list(table.headers),
                    "subtitle": table.subtitle,
                    "label_header": table.label_header,
                    "columns": [
                        {
                            "index": c.index, "header": c.header, "role": c.role,
                            "period": c.period, "currency": c.currency, "scale": c.scale,
                            "decimals": c.decimals, "source_col": c.source_col,
                        }
                        for c in table.columns
                    ],
                    "warnings": list(vr.warnings),
                    "rows": [
                        {
                            "label": r.label,
                            "kind": r.kind.value,
                            "source_row": r.source_row,
                            "cells": [
                                {
                                    "value": str(c.value) if c.value is not None else None,
                                    "raw_text": c.raw_text,
                                    "row_index": c.row_index,
                                    "col_index": c.col_index,
                                }
                                for c in r.cells
                            ],
                        }
                        for r in table.rows
                    ],
                },
            )
            session.add(et)
            session.flush()  # get et.id before creating issues

            # ── Casting errors ───────────────────────────────────────────────
            for err in vr.casting_errors:
                # row = the row that is wrong (for column checks: the total row)
                row_name = err.row_label if err.row_label is not None else err.label
                err_row = err.index if err.axis == "row" else err.row_index
                src = _pdf_ref(row_page(table, err_row), table.title, row_name, err.axis) \
                    if source == "pdf" \
                    else _excel_ref(table.sheet, table.title, row_name, err.axis)
                if err.column_label is not None:
                    src["column"] = err.column_label
                section = row_section(table, err_row)
                if section:
                    src["section"] = section
                if err.cell_ref:
                    src["cell"] = err.cell_ref
                if err.relation:
                    src["relation"] = err.relation
                if err.evidence:
                    src["related"] = list(err.evidence)
                where = (
                    f"row '{err.row_label}', column '{err.label}'"
                    if err.axis == "column" and err.row_label is not None
                    else f"{err.axis} '{err.label}'"
                )

                session.add(Issue(
                    id=uuid.uuid4(),
                    job_id=uuid.UUID(job_id),
                    table_id=et.id,
                    issue_type="CASTING_ERROR",
                    # An inferred formula (no "Total"-like label; confirmed only by format)
                    # is a plausible source of the mismatch itself, so these findings
                    # default to "medium" / "needs review" rather than HIGH.
                    severity="medium" if getattr(err, "confidence", "high") == "low" else "high",
                    status="open",
                    table_name=table.title,
                    description=(
                        f"Casting error in '{table.title}': {where} — "
                        f"declared={err.declared}, computed={err.computed}, delta={err.delta}"
                        + (f" [{err.relation}]" if err.relation else "")
                    ),
                    description_ar=(
                        f"خطأ تدقيق في الجدول «{table.title}»: {where}"
                    ),
                    expected_value=str(err.declared) if err.declared is not None else None,
                    actual_value=str(err.computed),
                    difference=str(err.delta),
                    source_reference=src,
                ))

            # ── Cross-casting errors ─────────────────────────────────────────
            for err in vr.cross_casting_errors:
                cross_row = next((r.index for r in table.rows if r.label == err.row_label), None)
                src = _pdf_ref(row_page(table, cross_row), table.title, err.row_label) \
                    if source == "pdf" \
                    else _excel_ref(table.sheet, table.title, err.row_label)
                section = row_section(table, cross_row)
                if section:
                    src["section"] = section

                session.add(Issue(
                    id=uuid.uuid4(),
                    job_id=uuid.UUID(job_id),
                    table_id=et.id,
                    issue_type="CROSS_CASTING_ERROR",
                    severity="critical",
                    status="open",
                    table_name=table.title,
                    description=err.description,
                    description_ar=err.description,
                    expected_value=str(err.row_total),
                    actual_value=str(err.col_total),
                    difference=str(err.delta),
                    source_reference=src,
                ))

            # ── OCR/VLM disagreements ────────────────────────────────────────
            for cv in vr.disagreements:
                bbox = list(cv.bbox) if cv.bbox else None
                src = _pdf_ref(cv.cell.page, table.title, bbox=bbox) \
                    if source == "pdf" else None

                session.add(Issue(
                    id=uuid.uuid4(),
                    job_id=uuid.UUID(job_id),
                    table_id=et.id,
                    issue_type="OCR_VLM_DISAGREEMENT",
                    severity="medium",
                    status="open",
                    table_name=table.title,
                    description=f"OCR/VLM disagreement (reason: {cv.reason})",
                    description_ar=f"اختلاف بين OCR و VLM: {cv.reason}",
                    expected_value=_fit(cv.ocr_value),
                    actual_value=_fit(cv.vlm_value),
                    difference=None,
                    source_reference=src,
                ))


def _pdf_ref(
    page: int,
    table_name: str,
    row: str | None = None,
    axis: str | None = None,
    bbox: list | None = None,
) -> dict:
    ref: dict = {"source": "pdf", "page": page, "table_name": table_name}
    if row is not None:
        ref["row"] = row
    if axis is not None:
        ref["axis"] = axis
    if bbox is not None:
        ref["bbox"] = bbox
    return ref


def _excel_ref(
    sheet: str | None,
    table_name: str,
    row: str | None = None,
    axis: str | None = None,
) -> dict:
    ref: dict = {"source": "excel", "table_name": table_name}
    if sheet is not None:
        ref["sheet"] = sheet
    if row is not None:
        ref["row"] = row
    if axis is not None:
        ref["axis"] = axis
    return ref


def row_page(table, row_index: int | None) -> int:
    """Page the row is printed on — a table joined across pages keeps each row's page."""
    if row_index is not None:
        for r in table.rows:
            if r.index == row_index:
                pages = [c.page for c in r.cells if getattr(c, "page", None)]
                if pages:
                    return pages[0]
    return table.page


def row_section(table, row_index: int | None) -> str | None:
    """The block a row belongs to, so a label repeated in one table is unambiguous.

    A movement note often repeats the same line ("Currency translation adjustments")
    under the cost, the accumulated depreciation and the comparative year. The
    section is the nearest heading above the row (a label with no amounts, e.g.
    "Accumulated depreciation and impairment:") or the nearest subtotal / total
    above it (e.g. "Balance as on 1 January, 2024"), whichever comes first.
    """
    if row_index is None:
        return None
    rows = sorted((r for r in table.rows if r.index < row_index), key=lambda r: r.index)
    for r in reversed(rows):
        label = (r.label or "").strip()
        if not label:
            continue
        no_amounts = all(c.value is None and not (c.raw_text or "").strip() for c in r.cells)
        if no_amounts or r.kind.is_aggregate:
            return label
    return None


def _fit(text: str | None, limit: int = 100) -> str | None:
    """OCR can return a whole line of text; the DB column holds at most `limit` chars."""
    if text is None or len(text) <= limit:
        return text
    return text[: limit - 1] + "…"
