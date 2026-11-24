"""API request/response schemas (Pydantic v2)."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel


# ── Core audit schemas ────────────────────────────────────────────────────────

class CastingErrorOut(BaseModel):
    table: str
    axis: Literal["row", "column"]
    label: str
    declared: Decimal | None
    computed: Decimal
    delta: Decimal
    # Additive (optional) traceability fields
    row_label: str | None = None          # for column checks: the total row checked
    relation: str | None = None           # e.g. "Total assets = Cash + Inventory"
    cell_ref: str | None = None           # e.g. "Balance Sheet!C8"
    evidence: list[str] = []              # consequences merged into this finding


class CrossCastingErrorOut(BaseModel):
    table: str
    description: str
    row_total: Decimal
    col_total: Decimal
    grand_total: Decimal | None
    delta: Decimal
    row_label: str | None = None          # the total row whose block was cross-cast


class FindingOut(BaseModel):
    severity: str
    finding_type: str
    description_ar: str
    description_en: str
    table: str
    details: str


class ValidationResultOut(BaseModel):
    table_title: str
    page: int
    sheet: str | None
    casting_errors: list[CastingErrorOut]
    cross_casting_errors: list[CrossCastingErrorOut]
    disagreements: int
    total_verifications: int              # OCR-vs-VLM cell checks (PDF/image only; 0 for Excel)
    warnings: list[str] = []              # extraction / interpretation warnings
    # Arithmetic comparisons made, passed + failed (every file type).
    # None = report saved before this count existed; re-run the job to fill it.
    casting_checks: int | None = None
    cross_casting_checks: int | None = None
    total_checks: int | None = None


class AuditReportOut(BaseModel):
    source_file: str
    tables_processed: int
    total_casting_errors: int
    total_cross_casting_errors: int
    total_checks: int | None = None       # Σ results[].total_checks; None on older reports
    is_clean: bool
    summary_ar: str
    summary_en: str
    results: list[ValidationResultOut]
    findings: list[FindingOut]
    pages_skipped: list[int] = []         # not sent to the reader: OCR showed no table


# ── Async analysis response ───────────────────────────────────────────────────

class AnalyzeQueuedOut(BaseModel):
    """Returned immediately by POST /analyze — job is queued for background processing."""
    job_id: str
    status: str  # "QUEUED"
    message: str


# ── Job schemas ───────────────────────────────────────────────────────────────

class AuditJobSummaryOut(BaseModel):
    id: str
    created_at: datetime
    status: str
    stage: str | None
    filename: str
    mime_type: str
    file_size: int
    has_errors: bool
    total_casting_errors: int | None
    total_cross_casting_errors: int | None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    batch_id: str | None = None
    relative_path: str | None = None


class AuditJobDetailOut(AuditJobSummaryOut):
    report: AuditReportOut | None = None
    error_message: str | None = None


class JobListOut(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[AuditJobSummaryOut]


class JobErrorsOut(BaseModel):
    job_id: str
    filename: str
    is_clean: bool
    total_casting_errors: int
    total_cross_casting_errors: int
    tables_with_errors: list[ValidationResultOut]


class FileDownloadOut(BaseModel):
    job_id: str
    filename: str
    presigned_url: str
    expires_in_hours: int


# ── Issues ────────────────────────────────────────────────────────────────────

class IssueOut(BaseModel):
    id: str
    job_id: str
    table_id: str | None
    issue_type: str
    severity: str
    status: str
    table_name: str | None
    description: str
    description_ar: str
    expected_value: str | None
    actual_value: str | None
    difference: str | None
    source_reference: dict[str, Any] | None
    created_at: datetime


class IssueSummaryOut(BaseModel):
    total_issues: int
    casting_errors: int
    cross_casting_errors: int
    ocr_vlm_disagreements: int


class FileSummaryOut(BaseModel):
    file_name: str
    file_type: str


class IssueListOut(BaseModel):
    job_id: str
    file: FileSummaryOut
    summary: IssueSummaryOut
    total: int
    page: int
    page_size: int
    issues: list[IssueOut]


# ── Extracted tables ──────────────────────────────────────────────────────────

class ExtractedTableOut(BaseModel):
    id: str
    job_id: str
    table_name: str
    page: int
    sheet: str | None
    row_count: int
    col_count: int


class ExtractedTableDetailOut(ExtractedTableOut):
    raw_data: dict[str, Any] | None


class TableListOut(BaseModel):
    job_id: str
    total: int
    items: list[ExtractedTableOut]


# ── Processing logs ───────────────────────────────────────────────────────────

class ProcessingLogOut(BaseModel):
    stage: str
    status: str
    message: str | None
    timestamp: datetime


class JobLogsOut(BaseModel):
    job_id: str
    logs: list[ProcessingLogOut]


# ── Dashboard stats ───────────────────────────────────────────────────────────

class DashboardStatsOut(BaseModel):
    total_jobs: int
    completed: int
    failed: int
    queued: int
    processing: int
    total_issues: int
    clean_jobs: int
    error_jobs: int


# ── Batch (folder) upload ─────────────────────────────────────────────────────

class BatchQueuedFileOut(BaseModel):
    job_id: str
    filename: str
    relative_path: str


class BatchSkippedFileOut(BaseModel):
    relative_path: str
    reason: str


class BatchQueuedOut(BaseModel):
    """Returned immediately by POST /analyze/batch — one job per accepted file."""
    batch_id: str
    status: str  # "QUEUED"
    total_queued: int
    total_skipped: int
    jobs: list[BatchQueuedFileOut]
    skipped: list[BatchSkippedFileOut]
    message: str


class BatchStatusOut(BaseModel):
    batch_id: str
    # QUEUED | PROCESSING | COMPLETED | PARTIALLY_FAILED | FAILED
    status: str
    total_jobs: int
    queued: int
    processing: int
    completed: int
    failed: int
    jobs_with_errors: int
    clean_jobs: int
    total_casting_errors: int
    total_cross_casting_errors: int
    created_at: datetime
    items: list[AuditJobSummaryOut]


# ── Job management ────────────────────────────────────────────────────────────

class RetryJobOut(BaseModel):
    job_id: str
    status: str
    message: str


# ── Legacy AnalyzeJobOut kept for backward compat ─────────────────────────────

class AnalyzeJobOut(AuditReportOut):
    """Synchronous analyze response — kept for backward compat. Use AnalyzeQueuedOut for async."""
    job_id: str


# ── Conversion helpers ────────────────────────────────────────────────────────

def report_to_out(report) -> AuditReportOut:
    results = [_result_to_out(r) for r in report.validation_results]
    findings = [
        FindingOut(
            severity=f.severity,
            finding_type=f.finding_type,
            description_ar=f.description_ar,
            description_en=f.description_en,
            table=f.table,
            details=f.details,
        )
        for f in report.findings
    ]
    return AuditReportOut(
        source_file=report.source_file,
        tables_processed=report.tables_processed,
        total_casting_errors=report.total_casting_errors,
        total_cross_casting_errors=report.total_cross_casting_errors,
        total_checks=report.total_checks,
        is_clean=report.is_clean,
        summary_ar=report.summary_ar,
        summary_en=report.summary_en,
        results=results,
        findings=findings,
        pages_skipped=list(getattr(report, "pages_skipped", ()) or ()),
    )


def _result_to_out(r) -> ValidationResultOut:
    return ValidationResultOut(
        table_title=r.table.title,
        page=r.table.page,
        sheet=r.table.sheet,
        casting_errors=[
            CastingErrorOut(
                table=e.table_title, axis=e.axis, label=e.label,
                declared=e.declared, computed=e.computed, delta=e.delta,
                row_label=e.row_label, relation=e.relation or None,
                cell_ref=e.cell_ref, evidence=list(e.evidence),
            )
            for e in r.casting_errors
        ],
        cross_casting_errors=[
            CrossCastingErrorOut(
                table=e.table_title, description=e.description,
                row_total=e.row_total, col_total=e.col_total,
                grand_total=e.grand_total, delta=e.delta,
                row_label=e.row_label,
            )
            for e in r.cross_casting_errors
        ],
        disagreements=len(r.disagreements),
        total_verifications=len(r.cell_verifications),
        warnings=list(r.warnings),
        casting_checks=r.casting_checks,
        cross_casting_checks=r.cross_casting_checks,
        total_checks=r.total_checks,
    )
