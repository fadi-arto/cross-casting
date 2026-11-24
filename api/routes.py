"""FastAPI application — routes, lifespan, and dependency injection."""
from __future__ import annotations

import io
import logging
import os
import posixpath
import uuid
import zipfile
from contextlib import asynccontextmanager
from typing import Literal

from fastapi.middleware.cors import CORSMiddleware
from fastapi import BackgroundTasks, Depends, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from starlette.concurrency import run_in_threadpool

from config.settings import Settings, get_settings
from db.database import build_engine, create_tables, get_session, ping as db_ping
from db.models import AuditJob, ExtractedTable, Issue, ProcessingLog
from engine.runner import clear_job_results, run_pipeline_task
from schemas.api import (
    AnalyzeQueuedOut,
    AuditJobDetailOut,
    AuditJobSummaryOut,
    AuditReportOut,
    BatchQueuedFileOut,
    BatchQueuedOut,
    BatchSkippedFileOut,
    BatchStatusOut,
    DashboardStatsOut,
    ExtractedTableDetailOut,
    ExtractedTableOut,
    FileDownloadOut,
    FileSummaryOut,
    IssueListOut,
    IssueOut,
    IssueSummaryOut,
    JobErrorsOut,
    JobListOut,
    JobLogsOut,
    ProcessingLogOut,
    RetryJobOut,
    TableListOut,
    ValidationResultOut,
    report_to_out,
)
from storage.minio_client import (
    build_minio_client,
    download_object,
    ensure_bucket,
    build_signing_client,
    get_presigned_url,
    ping as minio_ping,
    upload_object,
)

# Application logging (uvicorn configures only its own loggers). LOG_LEVEL, default INFO.
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
# Never let SDK/HTTP debug logs (which can include request headers) through,
# whatever LOG_LEVEL is; INFO still shows SDK retries.
for _name in ("anthropic", "httpx", "httpx2", "httpcore", "openai"):
    logging.getLogger(_name).setLevel(max(logging.INFO, logging.getLogger().level))
logging.getLogger("httpx2").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)

log = logging.getLogger(__name__)

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PDF = "application/pdf"
PNG = "image/png"
JPEG = "image/jpeg"
MAX_FILE_MB = 100
MAX_FILE_BYTES = MAX_FILE_MB * 1024 * 1024


def _guess_mime(filename: str) -> str:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return {
        "pdf": PDF, "png": PNG, "jpg": JPEG, "jpeg": JPEG,
        "xlsx": XLSX, "xls": XLSX,
    }.get(ext, "application/octet-stream")


def _file_type(mime: str, filename: str) -> str:
    if mime == XLSX or filename.lower().endswith((".xlsx", ".xls")):
        return "excel"
    if mime == PDF:
        return "pdf"
    return "image"


# ── Lifespan ───────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()

    engine = build_engine(settings.db.url)
    await run_in_threadpool(create_tables, engine)
    app.state.engine = engine
    log.info("PostgreSQL connected — tables ready")

    s = settings.storage
    minio = build_minio_client(
        endpoint=s.endpoint,
        access_key=s.access_key,
        secret_key=s.secret_key.get_secret_value(),
        secure=s.secure,
    )
    await run_in_threadpool(ensure_bucket, minio, s.bucket)
    app.state.minio = minio
    log.info("MinIO connected — bucket '%s' ready", s.bucket)

    app.state.settings = settings
    yield

    engine.dispose()


# ── FastAPI app ────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Cross-Casting Audit API",
    description=(
        "Financial table casting and cross-casting verification.\n\n"
        "Upload PDF, Excel (.xlsx), or image files and receive a detailed report "
        "of arithmetic errors. All files and results are persisted in MinIO (S3) "
        "and PostgreSQL for later retrieval.\n\n"
        "**Job lifecycle**: QUEUED → PROCESSING → COMPLETED | FAILED\n\n"
        "**Processing stages**: DOCUMENT_LOADING → TABLE_EXTRACTION → "
        "NUMBER_NORMALIZATION → OCR_VLM_VERIFICATION → CASTING → "
        "CROSS_CASTING → REPORT_GENERATION"
    ),
    version="2.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

_cors_origins = get_settings().app.cors_origins_list
if _cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_credentials="*" not in _cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["Content-Disposition"],
    )


# ── Dependencies ───────────────────────────────────────────────────────────────

def get_engine(request: Request) -> Engine:
    return request.app.state.engine


def get_minio(request: Request):
    return request.app.state.minio


def get_app_settings(request: Request) -> Settings:
    return request.app.state.settings


# ── UUID parsing ───────────────────────────────────────────────────────────────

def _parse_uuid(val: str) -> uuid.UUID:
    try:
        return uuid.UUID(val)
    except ValueError:
        raise HTTPException(400, f"Invalid ID format: {val!r}")


# ── DB → schema mappers ────────────────────────────────────────────────────────

def _job_to_summary(job: AuditJob) -> AuditJobSummaryOut:
    return AuditJobSummaryOut(
        id=str(job.id),
        created_at=job.created_at,
        status=job.status,
        stage=job.stage,
        filename=job.filename,
        mime_type=job.mime_type,
        file_size=job.file_size,
        has_errors=not job.is_clean if job.is_clean is not None else False,
        total_casting_errors=job.total_casting_errors,
        total_cross_casting_errors=job.total_cross_casting_errors,
        started_at=job.started_at,
        completed_at=job.completed_at,
        batch_id=str(job.batch_id) if job.batch_id else None,
        relative_path=job.relative_path,
    )


def _issue_to_out(issue: Issue) -> IssueOut:
    return IssueOut(
        id=str(issue.id),
        job_id=str(issue.job_id),
        table_id=str(issue.table_id) if issue.table_id else None,
        issue_type=issue.issue_type,
        severity=issue.severity,
        status=issue.status,
        table_name=issue.table_name,
        description=issue.description,
        description_ar=issue.description_ar,
        expected_value=issue.expected_value,
        actual_value=issue.actual_value,
        difference=issue.difference,
        source_reference=issue.source_reference,
        created_at=issue.created_at,
    )


def _table_to_out(et: ExtractedTable) -> ExtractedTableOut:
    return ExtractedTableOut(
        id=str(et.id),
        job_id=str(et.job_id),
        table_name=et.table_name,
        page=et.page,
        sheet=et.sheet,
        row_count=et.row_count,
        col_count=et.col_count,
    )


# ── Sync DB helpers ────────────────────────────────────────────────────────────

def _require_job(session, job_id: str) -> AuditJob:
    job = session.get(AuditJob, _parse_uuid(job_id))
    if not job:
        raise HTTPException(404, f"Job {job_id!r} not found")
    return job


_RERUNNABLE_STATUSES = {"FAILED", "COMPLETED"}


def _require_rerunnable(job: AuditJob) -> None:
    if (job.status or "").upper() not in _RERUNNABLE_STATUSES:
        raise HTTPException(
            409,
            f"Job is still {job.status}; only FAILED or COMPLETED jobs can be re-run.",
        )


def _require_completed(job: AuditJob) -> None:
    if job.status not in ("COMPLETED", "completed"):
        raise HTTPException(
            409,
            f"Job is not completed yet (status={job.status}). "
            "Poll GET /jobs/{id} until status is COMPLETED.",
        )


def _list_jobs_sync(engine, page, page_size, status, has_errors, batch_id=None) -> JobListOut:
    offset = (page - 1) * page_size
    with get_session(engine) as session:
        q = select(AuditJob).order_by(AuditJob.created_at.desc())
        if batch_id:
            q = q.where(AuditJob.batch_id == _parse_uuid(batch_id))
        if status:
            q = q.where(AuditJob.status == status.upper())
        if has_errors is True:
            q = q.where(AuditJob.is_clean == False)  # noqa: E712
        elif has_errors is False:
            q = q.where(AuditJob.is_clean == True)   # noqa: E712
        total = session.execute(select(func.count()).select_from(q.subquery())).scalar_one()
        jobs = session.execute(q.offset(offset).limit(page_size)).scalars().all()
        return JobListOut(
            total=total, page=page, page_size=page_size,
            items=[_job_to_summary(j) for j in jobs],
        )


def _get_job_detail_sync(engine, job_id) -> AuditJobDetailOut:
    with get_session(engine) as session:
        job = _require_job(session, job_id)
        return AuditJobDetailOut(
            **_job_to_summary(job).model_dump(),
            report=AuditReportOut(**job.report) if job.report else None,
            error_message=job.error_message,
        )


def _get_job_errors_sync(engine, job_id) -> JobErrorsOut:
    with get_session(engine) as session:
        job = _require_job(session, job_id)
        if not job.report:
            return JobErrorsOut(
                job_id=job_id, filename=job.filename,
                is_clean=True, total_casting_errors=0,
                total_cross_casting_errors=0, tables_with_errors=[],
            )
        report = AuditReportOut(**job.report)
        tables_with_errors = [
            ValidationResultOut(**r.model_dump())
            for r in report.results
            if r.casting_errors or r.cross_casting_errors
        ]
        return JobErrorsOut(
            job_id=job_id, filename=job.filename,
            is_clean=report.is_clean,
            total_casting_errors=report.total_casting_errors,
            total_cross_casting_errors=report.total_cross_casting_errors,
            tables_with_errors=tables_with_errors,
        )


def _get_file_url_sync(engine, minio, settings, job_id) -> FileDownloadOut:
    with get_session(engine) as session:
        job = _require_job(session, job_id)
    expires = settings.storage.presigned_url_expires_hours
    signer = build_signing_client(settings.storage) or minio
    url = get_presigned_url(signer, job.minio_bucket, job.minio_object_key, expires)
    return FileDownloadOut(
        job_id=job_id, filename=job.filename,
        presigned_url=url, expires_in_hours=expires,
    )


def _get_issues_sync(engine, job_id, issue_type, severity, status, table_id,
                     page, page_size, sort_by, sort_dir) -> IssueListOut:
    offset = (page - 1) * page_size
    with get_session(engine) as session:
        job = _require_job(session, job_id)

        q = select(Issue).where(Issue.job_id == _parse_uuid(job_id))

        if issue_type:
            q = q.where(Issue.issue_type == issue_type.upper())
        if severity:
            q = q.where(Issue.severity == severity.lower())
        if status:
            q = q.where(Issue.status == status.lower())
        if table_id:
            q = q.where(Issue.table_id == _parse_uuid(table_id))

        # Sort
        sort_col = {
            "created_at": Issue.created_at,
            "severity": Issue.severity,
            "issue_type": Issue.issue_type,
        }.get(sort_by, Issue.created_at)
        if sort_dir == "asc":
            q = q.order_by(sort_col.asc())
        else:
            q = q.order_by(sort_col.desc())

        total = session.execute(select(func.count()).select_from(q.subquery())).scalar_one()
        issues = session.execute(q.offset(offset).limit(page_size)).scalars().all()

        # Summary counts (un-filtered)
        all_issues = session.execute(
            select(Issue).where(Issue.job_id == _parse_uuid(job_id))
        ).scalars().all()
        summary = IssueSummaryOut(
            total_issues=len(all_issues),
            casting_errors=sum(1 for i in all_issues if i.issue_type == "CASTING_ERROR"),
            cross_casting_errors=sum(1 for i in all_issues if i.issue_type == "CROSS_CASTING_ERROR"),
            ocr_vlm_disagreements=sum(1 for i in all_issues if i.issue_type == "OCR_VLM_DISAGREEMENT"),
        )
        mime = job.mime_type
        return IssueListOut(
            job_id=job_id,
            file=FileSummaryOut(
                file_name=job.filename,
                file_type=_file_type(mime, job.filename),
            ),
            summary=summary,
            total=total, page=page, page_size=page_size,
            issues=[_issue_to_out(i) for i in issues],
        )


def _get_issue_detail_sync(engine, job_id, issue_id) -> IssueOut:
    with get_session(engine) as session:
        _require_job(session, job_id)
        issue = session.get(Issue, _parse_uuid(issue_id))
        if not issue or str(issue.job_id) != job_id:
            raise HTTPException(404, f"Issue {issue_id!r} not found in job {job_id!r}")
        return _issue_to_out(issue)


def _get_tables_sync(engine, job_id) -> TableListOut:
    with get_session(engine) as session:
        _require_job(session, job_id)
        tables = session.execute(
            select(ExtractedTable)
            .where(ExtractedTable.job_id == _parse_uuid(job_id))
            .order_by(ExtractedTable.page, ExtractedTable.table_name)
        ).scalars().all()
        return TableListOut(
            job_id=job_id, total=len(tables),
            items=[_table_to_out(t) for t in tables],
        )


def _get_table_detail_sync(engine, job_id, table_id) -> ExtractedTableDetailOut:
    with get_session(engine) as session:
        _require_job(session, job_id)
        et = session.get(ExtractedTable, _parse_uuid(table_id))
        if not et or str(et.job_id) != job_id:
            raise HTTPException(404, f"Table {table_id!r} not found in job {job_id!r}")
        return ExtractedTableDetailOut(**_table_to_out(et).model_dump(), raw_data=et.raw_data)


def _get_logs_sync(engine, job_id) -> JobLogsOut:
    with get_session(engine) as session:
        _require_job(session, job_id)
        logs = session.execute(
            select(ProcessingLog)
            .where(ProcessingLog.job_id == _parse_uuid(job_id))
            .order_by(ProcessingLog.timestamp.asc())
        ).scalars().all()
        return JobLogsOut(
            job_id=job_id,
            logs=[
                ProcessingLogOut(
                    stage=l.stage, status=l.status,
                    message=l.message, timestamp=l.timestamp,
                )
                for l in logs
            ],
        )


def _get_dashboard_stats_sync(engine) -> DashboardStatsOut:
    with get_session(engine) as session:
        total = session.execute(select(func.count(AuditJob.id))).scalar_one()
        completed = session.execute(
            select(func.count(AuditJob.id)).where(AuditJob.status.in_(["COMPLETED", "completed"]))
        ).scalar_one()
        failed = session.execute(
            select(func.count(AuditJob.id)).where(AuditJob.status.in_(["FAILED", "failed"]))
        ).scalar_one()
        queued = session.execute(
            select(func.count(AuditJob.id)).where(AuditJob.status == "QUEUED")
        ).scalar_one()
        processing = session.execute(
            select(func.count(AuditJob.id)).where(AuditJob.status == "PROCESSING")
        ).scalar_one()
        total_issues = session.execute(select(func.count(Issue.id))).scalar_one()
        clean_jobs = session.execute(
            select(func.count(AuditJob.id)).where(AuditJob.is_clean == True)  # noqa: E712
        ).scalar_one()
        error_jobs = session.execute(
            select(func.count(AuditJob.id)).where(AuditJob.is_clean == False)  # noqa: E712
        ).scalar_one()
        return DashboardStatsOut(
            total_jobs=total,
            completed=completed,
            failed=failed,
            queued=queued,
            processing=processing,
            total_issues=total_issues,
            clean_jobs=clean_jobs,
            error_jobs=error_jobs,
        )


def _health_check_sync(engine, minio, settings) -> dict:
    checks: dict[str, str] = {}
    try:
        db_ping(engine)
        checks["database"] = "ok"
    except Exception as exc:
        checks["database"] = f"error: {exc}"
    try:
        minio_ping(minio, settings.storage.bucket)
        checks["storage"] = "ok"
    except Exception as exc:
        checks["storage"] = f"error: {exc}"
    all_ok = all(v == "ok" for v in checks.values())
    return {"status": "ok" if all_ok else "degraded", **checks, "all_ok": all_ok}


# ── Routes ─────────────────────────────────────────────────────────────────────
# [Audit] ───────────────────────────────────────────────────────────────────────

@app.post(
    "/analyze",
    response_model=AnalyzeQueuedOut,
    status_code=202,
    summary="Upload a document and queue an audit job",
    tags=["Audit"],
)
async def analyze(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(..., description="PDF, Excel (.xlsx/.xls), PNG, or JPEG"),
    engine: Engine = Depends(get_engine),
    minio=Depends(get_minio),
    settings: Settings = Depends(get_app_settings),
) -> AnalyzeQueuedOut:
    """
    Upload a financial document. Returns immediately with a `job_id` and status
    `QUEUED`. The audit runs asynchronously in the background.

    Poll `GET /jobs/{job_id}` to check progress. When `status` is `COMPLETED`,
    full results are available at `GET /jobs/{job_id}/results`.
    """
    content = await file.read()
    if not content:
        raise HTTPException(400, "Empty file")
    if len(content) > MAX_FILE_BYTES:
        raise HTTPException(413, f"File exceeds {MAX_FILE_MB} MB limit")

    filename = file.filename or "unknown"
    mime = file.content_type or _guess_mime(filename)
    if mime == "application/octet-stream":
        mime = _guess_mime(filename)

    if not (
        mime in {PDF, PNG, JPEG, XLSX}
        or filename.lower().endswith((".xlsx", ".xls"))
    ):
        raise HTTPException(415, f"Unsupported file type: {mime!r}")

    job_id = str(uuid.uuid4())
    object_key = f"{job_id}/{filename}"

    # Upload to MinIO — do this synchronously so failure is surfaced immediately
    await run_in_threadpool(
        upload_object, minio, settings.storage.bucket, object_key, content, mime,
    )

    # Create job in DB (QUEUED)
    with get_session(engine) as session:
        job = AuditJob(
            id=uuid.UUID(job_id),
            filename=filename,
            mime_type=mime,
            file_size=len(content),
            minio_bucket=settings.storage.bucket,
            minio_object_key=object_key,
            status="QUEUED",
        )
        session.add(job)

    # Schedule background processing
    background_tasks.add_task(
        run_pipeline_task,
        job_id=job_id,
        content=content,
        mime=mime,
        filename=filename,
        engine=engine,
        settings=settings,
    )

    return AnalyzeQueuedOut(
        job_id=job_id,
        status="QUEUED",
        message=f"Job queued. Poll GET /jobs/{job_id} for status.",
    )


# [Batch] ───────────────────────────────────────────────────────────────────────
# Folder upload: the browser sends every file of the folder in one multipart
# request (field name "files", repeated). A .zip in the request is expanded.
# Each supported file becomes its own audit job; all share one batch_id.

MAX_BATCH_FILES = 500
MAX_ZIP_MB = 500
MAX_ZIP_BYTES = MAX_ZIP_MB * 1024 * 1024
MAX_ZIP_UNCOMPRESSED_BYTES = 2 * 1024 * 1024 * 1024  # zip-bomb guard (2 GB total)

_IGNORED_NAMES = {".ds_store", "thumbs.db", "desktop.ini"}


def _clean_relative_path(name: str) -> str:
    """Normalise a client-supplied path (folder/sub/file.pdf) — no '..', no leading '/'."""
    name = name.replace("\\", "/")
    parts = [p for p in posixpath.normpath(name).split("/") if p not in ("", ".", "..")]
    return "/".join(parts) or "unknown"


def _is_ignored(rel_path: str) -> bool:
    parts = rel_path.split("/")
    base = parts[-1]
    return (
        base.lower() in _IGNORED_NAMES
        or base.startswith(".")          # hidden files, macOS "._*" resource forks
        or "__MACOSX" in parts
    )


def _resolve_mime(filename: str, content_type: str | None) -> str | None:
    """Return a supported MIME type for the file, or None if unsupported."""
    mime = content_type or _guess_mime(filename)
    if mime == "application/octet-stream":
        mime = _guess_mime(filename)
    if mime in {PDF, PNG, JPEG, XLSX}:
        return mime
    if filename.lower().endswith((".xlsx", ".xls")):
        return XLSX
    return None


def _expand_zip(content: bytes, zip_path: str) -> list[tuple[str, bytes]]:
    """Return (relative_path, bytes) for every file inside a zip archive."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile:
        raise HTTPException(400, f"Invalid zip archive: {zip_path!r}")
    prefix = zip_path.rsplit(".", 1)[0]
    entries = [i for i in zf.infolist() if not i.is_dir()]
    if sum(i.file_size for i in entries) > MAX_ZIP_UNCOMPRESSED_BYTES:
        raise HTTPException(413, f"Zip {zip_path!r} expands beyond the allowed size")
    out = []
    with zf:
        for info in entries:
            out.append((f"{prefix}/{_clean_relative_path(info.filename)}", zf.read(info)))
    return out


def _run_job_from_storage(
    job_id: str, bucket: str, object_key: str, mime: str, filename: str,
    engine: Engine, settings: Settings, minio,
) -> None:
    """Background task for batch jobs — loads the file from MinIO so a large
    folder is not held in memory while its jobs wait their turn."""
    try:
        content = download_object(minio, bucket, object_key)
    except Exception as exc:  # surface storage failure on the job itself
        log.exception("Batch job %s: cannot load file from storage", job_id)
        with get_session(engine) as session:
            job = session.get(AuditJob, uuid.UUID(job_id))
            if job:
                job.status = "FAILED"
                job.error_message = f"Cannot load file from storage: {exc}"
        return
    run_pipeline_task(
        job_id=job_id, content=content, mime=mime, filename=filename,
        engine=engine, settings=settings,
    )


def _create_batch_jobs_sync(
    engine, minio, settings: Settings, batch_id: uuid.UUID,
    files: list[tuple[str, bytes, str]],
) -> list[tuple[str, str, str, str, str]]:
    """Upload each file to MinIO and create its QUEUED job.
    Returns (job_id, filename, relative_path, mime, object_key) per file."""
    created = []
    bucket = settings.storage.bucket
    for rel_path, content, mime in files:
        job_id = str(uuid.uuid4())
        filename = rel_path.rsplit("/", 1)[-1][:255]
        object_key = f"{job_id}/{filename}"
        upload_object(minio, bucket, object_key, content, mime)
        with get_session(engine) as session:
            session.add(AuditJob(
                id=uuid.UUID(job_id),
                batch_id=batch_id,
                relative_path=rel_path[:1000],
                filename=filename,
                mime_type=mime,
                file_size=len(content),
                minio_bucket=bucket,
                minio_object_key=object_key,
                status="QUEUED",
            ))
        created.append((job_id, filename, rel_path, mime, object_key))
    return created


@app.post(
    "/analyze/batch",
    response_model=BatchQueuedOut,
    status_code=202,
    summary="Upload a folder (many files, or a .zip) and queue one job per file",
    tags=["Audit"],
)
async def analyze_batch(
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = File(
        ...,
        description=(
            "All files of the folder (repeat the `files` field). Send each file's "
            "path inside the folder as its filename, e.g. `reports/2024/q1.pdf`. "
            "A `.zip` file is expanded. Unsupported files are skipped."
        ),
    ),
    engine: Engine = Depends(get_engine),
    minio=Depends(get_minio),
    settings: Settings = Depends(get_app_settings),
) -> BatchQueuedOut:
    """
    Upload a whole folder. Every PDF / Excel / PNG / JPEG inside it becomes its
    own audit job (same as `POST /analyze`), and all of them share one
    `batch_id`.

    Poll `GET /batches/{batch_id}` for overall progress, or
    `GET /jobs?batch_id=...` for the job list.
    """
    accepted: list[tuple[str, bytes, str]] = []
    skipped: list[BatchSkippedFileOut] = []

    def consider(rel_path: str, content: bytes, content_type: str | None) -> None:
        if _is_ignored(rel_path):
            return
        if not content:
            skipped.append(BatchSkippedFileOut(relative_path=rel_path, reason="Empty file"))
            return
        if len(content) > MAX_FILE_BYTES:
            skipped.append(BatchSkippedFileOut(
                relative_path=rel_path, reason=f"File exceeds {MAX_FILE_MB} MB limit",
            ))
            return
        mime = _resolve_mime(rel_path, content_type)
        if mime is None:
            skipped.append(BatchSkippedFileOut(relative_path=rel_path, reason="Unsupported file type"))
            return
        accepted.append((rel_path, content, mime))

    for f in files:
        rel_path = _clean_relative_path(f.filename or "unknown")
        content = await f.read()
        await f.close()
        if rel_path.lower().endswith(".zip"):
            if len(content) > MAX_ZIP_BYTES:
                skipped.append(BatchSkippedFileOut(
                    relative_path=rel_path, reason=f"Zip exceeds {MAX_ZIP_MB} MB limit",
                ))
                continue
            for inner_path, inner in await run_in_threadpool(_expand_zip, content, rel_path):
                consider(inner_path, inner, None)
        else:
            consider(rel_path, content, f.content_type)
        if len(accepted) > MAX_BATCH_FILES:
            raise HTTPException(413, f"Too many files — the limit is {MAX_BATCH_FILES} per batch")

    if not accepted:
        raise HTTPException(
            400,
            "No supported files found (PDF, Excel .xlsx/.xls, PNG, JPEG). "
            f"Skipped: {len(skipped)}",
        )

    batch_id = uuid.uuid4()
    created = await run_in_threadpool(
        _create_batch_jobs_sync, engine, minio, settings, batch_id, accepted,
    )
    accepted.clear()  # release file bytes — background tasks re-read from MinIO

    for job_id, filename, _rel, mime, object_key in created:
        background_tasks.add_task(
            _run_job_from_storage,
            job_id=job_id, bucket=settings.storage.bucket, object_key=object_key,
            mime=mime, filename=filename, engine=engine, settings=settings, minio=minio,
        )

    return BatchQueuedOut(
        batch_id=str(batch_id),
        status="QUEUED",
        total_queued=len(created),
        total_skipped=len(skipped),
        jobs=[
            BatchQueuedFileOut(job_id=j, filename=fn, relative_path=rp)
            for j, fn, rp, _m, _k in created
        ],
        skipped=skipped,
        message=f"{len(created)} job(s) queued. Poll GET /batches/{batch_id} for progress.",
    )


def _get_batch_sync(engine, batch_id: str) -> BatchStatusOut:
    with get_session(engine) as session:
        jobs = session.execute(
            select(AuditJob)
            .where(AuditJob.batch_id == _parse_uuid(batch_id))
            .order_by(AuditJob.relative_path, AuditJob.created_at)
        ).scalars().all()
        if not jobs:
            raise HTTPException(404, f"Batch {batch_id!r} not found")

        statuses = [j.status.upper() for j in jobs]
        queued = statuses.count("QUEUED")
        processing = statuses.count("PROCESSING")
        completed = statuses.count("COMPLETED")
        failed = statuses.count("FAILED")
        total = len(jobs)

        if completed == total:
            overall = "COMPLETED"
        elif failed == total:
            overall = "FAILED"
        elif queued == total:
            overall = "QUEUED"
        elif queued or processing:
            overall = "PROCESSING"
        else:
            overall = "PARTIALLY_FAILED"

        return BatchStatusOut(
            batch_id=batch_id,
            status=overall,
            total_jobs=total,
            queued=queued,
            processing=processing,
            completed=completed,
            failed=failed,
            jobs_with_errors=sum(1 for j in jobs if j.is_clean is False),
            clean_jobs=sum(1 for j in jobs if j.is_clean is True),
            total_casting_errors=sum(j.total_casting_errors or 0 for j in jobs),
            total_cross_casting_errors=sum(j.total_cross_casting_errors or 0 for j in jobs),
            created_at=min(j.created_at for j in jobs),
            items=[_job_to_summary(j) for j in jobs],
        )


@app.get(
    "/batches/{batch_id}",
    response_model=BatchStatusOut,
    summary="Get folder-upload (batch) progress and per-file results",
    tags=["Audit"],
)
async def get_batch(
    batch_id: str,
    engine: Engine = Depends(get_engine),
) -> BatchStatusOut:
    """
    Overall status of a folder upload:
    `QUEUED` → `PROCESSING` → `COMPLETED` | `PARTIALLY_FAILED` | `FAILED`.
    `items` lists every file's job (use `GET /jobs/{id}/...` for details).
    """
    return await run_in_threadpool(_get_batch_sync, engine, batch_id)


# [Jobs] ────────────────────────────────────────────────────────────────────────

@app.get(
    "/jobs",
    response_model=JobListOut,
    summary="List audit jobs",
    tags=["Jobs"],
)
async def list_jobs(
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Results per page"),
    status: str | None = Query(None, description="Filter by status: QUEUED|PROCESSING|COMPLETED|FAILED"),
    has_errors: bool | None = Query(None, description="true=only jobs with errors, false=only clean jobs"),
    batch_id: str | None = Query(None, description="Only jobs from this folder upload (batch)"),
    engine: Engine = Depends(get_engine),
) -> JobListOut:
    """Return a paginated list of audit jobs with summary statistics."""
    return await run_in_threadpool(
        _list_jobs_sync, engine, page, page_size, status, has_errors, batch_id,
    )


@app.get(
    "/jobs/{job_id}",
    response_model=AuditJobDetailOut,
    summary="Get job details and status",
    tags=["Jobs"],
)
async def get_job(
    job_id: str,
    engine: Engine = Depends(get_engine),
) -> AuditJobDetailOut:
    """
    Get job metadata, current status, and processing stage.

    When `status` is `COMPLETED`, the `report` field contains the full audit report.
    When `status` is `FAILED`, `error_message` explains what went wrong.
    """
    return await run_in_threadpool(_get_job_detail_sync, engine, job_id)


@app.get(
    "/jobs/{job_id}/errors",
    response_model=JobErrorsOut,
    summary="Get casting errors for a job",
    tags=["Jobs"],
)
async def get_job_errors(
    job_id: str,
    engine: Engine = Depends(get_engine),
) -> JobErrorsOut:
    """Return only the tables that contain casting or cross-casting errors."""
    return await run_in_threadpool(_get_job_errors_sync, engine, job_id)


@app.get(
    "/jobs/{job_id}/file",
    response_model=FileDownloadOut,
    summary="Get a presigned download URL for the original file",
    tags=["Jobs"],
)
async def get_job_file(
    job_id: str,
    engine: Engine = Depends(get_engine),
    minio=Depends(get_minio),
    settings: Settings = Depends(get_app_settings),
) -> FileDownloadOut:
    """Generate a time-limited presigned URL to download the original uploaded file from MinIO."""
    return await run_in_threadpool(_get_file_url_sync, engine, minio, settings, job_id)


@app.get(
    "/jobs/{job_id}/results",
    response_model=AuditJobDetailOut,
    summary="Get detailed casting and cross-casting results",
    tags=["Jobs"],
)
async def get_job_results(
    job_id: str,
    engine: Engine = Depends(get_engine),
) -> AuditJobDetailOut:
    """
    Retrieve the detailed validation results for a completed job.

    Returns casting errors, cross-casting errors, and cell-level disagreements
    per table.
    """
    detail = await run_in_threadpool(_get_job_detail_sync, engine, job_id)
    if detail.status not in ("COMPLETED", "completed"):
        raise HTTPException(
            409,
            f"Results not available yet (status={detail.status}). "
            "Wait until status is COMPLETED.",
        )
    return detail


@app.get(
    "/jobs/{job_id}/report",
    response_model=AuditReportOut,
    summary="Get the complete audit report",
    tags=["Jobs"],
)
async def get_job_report(
    job_id: str,
    engine: Engine = Depends(get_engine),
) -> AuditReportOut:
    """Return the complete structured audit report for a completed job."""
    detail = await run_in_threadpool(_get_job_detail_sync, engine, job_id)
    if detail.status not in ("COMPLETED", "completed"):
        raise HTTPException(409, f"Report not ready (status={detail.status})")
    if not detail.report:
        raise HTTPException(404, "Report data not found")
    return detail.report


@app.get(
    "/jobs/{job_id}/export",
    summary="Export the audit report as a downloadable file",
    tags=["Jobs"],
    responses={
        200: {"content": {"application/json": {}}},
    },
)
async def export_job(
    job_id: str,
    engine: Engine = Depends(get_engine),
) -> Response:
    """
    Download the audit report as a JSON file.

    Returns `Content-Disposition: attachment` so the browser triggers a download.
    """
    detail = await run_in_threadpool(_get_job_detail_sync, engine, job_id)
    if detail.status not in ("COMPLETED", "completed"):
        raise HTTPException(409, f"Report not ready (status={detail.status})")
    if not detail.report:
        raise HTTPException(404, "Report data not found")

    report_json = detail.report.model_dump_json(indent=2)
    return Response(
        content=report_json,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="audit-{job_id}.json"'},
    )


@app.get(
    "/jobs/{job_id}/logs",
    response_model=JobLogsOut,
    summary="Get job processing stage history",
    tags=["Jobs"],
)
async def get_job_logs(
    job_id: str,
    engine: Engine = Depends(get_engine),
) -> JobLogsOut:
    """Return the processing stage log for a job in chronological order."""
    return await run_in_threadpool(_get_logs_sync, engine, job_id)


@app.post(
    "/jobs/{job_id}/retry",
    response_model=RetryJobOut,
    status_code=202,
    summary="Re-run a failed or completed job",
    tags=["Jobs"],
)
async def retry_job(
    job_id: str,
    background_tasks: BackgroundTasks,
    engine: Engine = Depends(get_engine),
    minio=Depends(get_minio),
    settings: Settings = Depends(get_app_settings),
) -> RetryJobOut:
    """
    Re-queue a `FAILED` or `COMPLETED` job for processing.

    The original file is re-downloaded from MinIO and processed with the current
    engine and settings. The previous report, issues, extracted tables and stage
    log are discarded and replaced by the new run.

    Returns **409** while the job is still `QUEUED` or `PROCESSING`.
    """
    with get_session(engine) as session:
        job = _require_job(session, job_id)
        _require_rerunnable(job)
        bucket = job.minio_bucket
        object_key = job.minio_object_key
        mime = job.mime_type
        filename = job.filename

    content = await run_in_threadpool(download_object, minio, bucket, object_key)

    with get_session(engine) as session:
        job = _require_job(session, job_id)
        _require_rerunnable(job)  # another request may have re-queued it meanwhile
        clear_job_results(session, job.id, include_logs=True)
        job.status = "QUEUED"
        job.stage = None
        job.error_message = None
        job.started_at = None
        job.completed_at = None
        job.tables_processed = None
        job.total_casting_errors = None
        job.total_cross_casting_errors = None
        job.is_clean = None
        job.report = None

    background_tasks.add_task(
        run_pipeline_task,
        job_id=job_id, content=content, mime=mime, filename=filename,
        engine=engine, settings=settings,
    )

    return RetryJobOut(
        job_id=job_id, status="QUEUED",
        message=f"Job re-queued. Poll GET /jobs/{job_id} for status.",
    )


@app.delete(
    "/jobs/{job_id}",
    status_code=204,
    summary="Delete a job and all associated data",
    tags=["Jobs"],
)
async def delete_job(
    job_id: str,
    engine: Engine = Depends(get_engine),
    minio=Depends(get_minio),
) -> Response:
    """
    Delete a job, its issues, extracted tables, and processing logs from PostgreSQL.
    Also removes the original file from MinIO.
    """
    with get_session(engine) as session:
        job = _require_job(session, job_id)
        bucket = job.minio_bucket
        object_key = job.minio_object_key
        session.delete(job)

    # Remove from MinIO — best-effort
    try:
        minio.remove_object(bucket, object_key)
    except Exception as exc:
        log.warning("Could not remove MinIO object %s/%s: %s", bucket, object_key, exc)

    return Response(status_code=204)


# [Issues] ──────────────────────────────────────────────────────────────────────

@app.get(
    "/jobs/{job_id}/issues",
    response_model=IssueListOut,
    summary="Get all issues discovered during the audit",
    tags=["Issues"],
)
async def get_job_issues(
    job_id: str,
    issue_type: str | None = Query(
        None,
        description="Filter by type: CASTING_ERROR|CROSS_CASTING_ERROR|"
                    "OCR_VLM_DISAGREEMENT|EXTRACTION_ERROR|TABLE_STRUCTURE_ERROR|HUMAN_REVIEW",
    ),
    severity: str | None = Query(None, description="Filter by severity: critical|high|medium|low"),
    status: str | None = Query(None, description="Filter by status: open|acknowledged|resolved"),
    table_id: str | None = Query(None, description="Filter by extracted table ID"),
    sort_by: str = Query("created_at", description="Sort field: created_at|severity|issue_type"),
    sort_dir: str = Query("desc", description="Sort direction: asc|desc"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    engine: Engine = Depends(get_engine),
) -> IssueListOut:
    """
    Return all issues found during the audit with filtering and pagination.

    Includes CASTING_ERROR, CROSS_CASTING_ERROR, and OCR_VLM_DISAGREEMENT issues.
    Every issue includes source location details (page/row/column for PDF;
    sheet/cell for Excel).
    """
    return await run_in_threadpool(
        _get_issues_sync, engine, job_id,
        issue_type, severity, status, table_id,
        page, page_size, sort_by, sort_dir,
    )


@app.get(
    "/jobs/{job_id}/issues/{issue_id}",
    response_model=IssueOut,
    summary="Get complete details for a specific issue",
    tags=["Issues"],
)
async def get_issue_detail(
    job_id: str,
    issue_id: str,
    engine: Engine = Depends(get_engine),
) -> IssueOut:
    """Return complete details for a single issue including source reference."""
    return await run_in_threadpool(_get_issue_detail_sync, engine, job_id, issue_id)


# [Tables] ──────────────────────────────────────────────────────────────────────

@app.get(
    "/jobs/{job_id}/tables",
    response_model=TableListOut,
    summary="List extracted tables",
    tags=["Tables"],
)
async def get_job_tables(
    job_id: str,
    engine: Engine = Depends(get_engine),
) -> TableListOut:
    """List all financial tables extracted from the uploaded document."""
    return await run_in_threadpool(_get_tables_sync, engine, job_id)


@app.get(
    "/jobs/{job_id}/tables/{table_id}",
    response_model=ExtractedTableDetailOut,
    summary="Get detailed table data",
    tags=["Tables"],
)
async def get_table_detail(
    job_id: str,
    table_id: str,
    engine: Engine = Depends(get_engine),
) -> ExtractedTableDetailOut:
    """
    Return full table data including headers, rows, and cell values as extracted
    by the VLM/Excel adapter.
    """
    return await run_in_threadpool(_get_table_detail_sync, engine, job_id, table_id)


# [Dashboard] ───────────────────────────────────────────────────────────────────

@app.get(
    "/dashboard/stats",
    response_model=DashboardStatsOut,
    summary="Dashboard statistics",
    tags=["Dashboard"],
)
async def dashboard_stats(
    engine: Engine = Depends(get_engine),
) -> DashboardStatsOut:
    """
    Aggregate statistics for the dashboard: job counts by status, issue totals,
    and clean vs. error job breakdown.
    """
    return await run_in_threadpool(_get_dashboard_stats_sync, engine)


# [System] ──────────────────────────────────────────────────────────────────────

@app.get(
    "/health",
    summary="Health check",
    tags=["System"],
)
async def health(
    engine: Engine = Depends(get_engine),
    minio=Depends(get_minio),
    settings: Settings = Depends(get_app_settings),
) -> JSONResponse:
    """
    Check connectivity to PostgreSQL and MinIO.

    Returns HTTP 200 when all services are reachable, HTTP 503 when degraded.
    """
    result = await run_in_threadpool(_health_check_sync, engine, minio, settings)
    all_ok = result.pop("all_ok")
    return JSONResponse(status_code=200 if all_ok else 503, content=result)


# [Review] ──────────────────────────────────────────────────────────────────────
# Frontend-oriented findings (casting / cross-casting), PDF export, status updates.

from api.review_routes import router as review_router  # noqa: E402

app.include_router(review_router)
