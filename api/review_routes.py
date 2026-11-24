"""Review API — casting / cross-casting findings shaped for the frontend, plus PDF export.

Mounted by api.routes (`app.include_router`).  Kept in its own module so the
main routes file stays focused on upload / jobs.
"""
from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.engine import Engine
from starlette.concurrency import run_in_threadpool

from adapters.pdf_report import render_review_pdf
from application.review import build_review
from db.database import get_session
from db.models import AuditJob, ExtractedTable, Issue
from schemas.api import IssueOut
from schemas.review import IssueStatusUpdateIn, ReviewReportOut

router = APIRouter()

Status = Literal["open", "acknowledged", "resolved"]


def _engine(request: Request) -> Engine:
    return request.app.state.engine


def _uuid(val: str) -> uuid.UUID:
    try:
        return uuid.UUID(val)
    except ValueError:
        raise HTTPException(400, f"Invalid ID format: {val!r}")


def _review_sync(engine, job_id: str, scope: str, status: str | None) -> ReviewReportOut:
    with get_session(engine) as session:
        job = session.get(AuditJob, _uuid(job_id))
        if not job:
            raise HTTPException(404, f"Job {job_id!r} not found")
        if (job.status or "").upper() != "COMPLETED":
            raise HTTPException(
                409,
                f"Job is not completed yet (status={job.status}). "
                "Poll GET /jobs/{id} until status is COMPLETED.",
            )
        issues = session.execute(
            select(Issue).where(Issue.job_id == job.id)
        ).scalars().all()
        tables = session.execute(
            select(ExtractedTable).where(ExtractedTable.job_id == job.id)
        ).scalars().all()
        return build_review(job, issues, tables, scope=scope, status=status)


# ── JSON ──────────────────────────────────────────────────────────────────────

@router.get(
    "/jobs/{job_id}/review",
    response_model=ReviewReportOut,
    summary="Actionable review report (casting + cross-casting), grouped by table",
    tags=["Review"],
)
async def get_review(
    job_id: str,
    status: Status | None = Query(None, description="Only items in this status"),
    engine: Engine = Depends(_engine),
) -> ReviewReportOut:
    """
    Everything the frontend needs to help a reviewer resolve the findings:
    findings grouped by table and ranked by `priority`, each with the figures,
    the location in the source, bilingual (`en`/`ar`) explanation, likely causes,
    data-driven hints and ordered resolution steps. `summary` counts the whole
    scope; `tables` honours the `status` filter.
    """
    return await run_in_threadpool(_review_sync, engine, job_id, "all", status)


@router.get(
    "/jobs/{job_id}/casting",
    response_model=ReviewReportOut,
    summary="Casting findings only (totals vs their components)",
    tags=["Review"],
)
async def get_casting(
    job_id: str,
    status: Status | None = Query(None),
    engine: Engine = Depends(_engine),
) -> ReviewReportOut:
    """Vertical, horizontal and trial-balance findings."""
    return await run_in_threadpool(_review_sync, engine, job_id, "casting", status)


@router.get(
    "/jobs/{job_id}/cross-casting",
    response_model=ReviewReportOut,
    summary="Cross-casting findings only (rows vs columns vs grand total)",
    tags=["Review"],
)
async def get_cross_casting(
    job_id: str,
    status: Status | None = Query(None),
    engine: Engine = Depends(_engine),
) -> ReviewReportOut:
    """Row-vs-column, row-vs-grand-total and column-vs-grand-total findings."""
    return await run_in_threadpool(_review_sync, engine, job_id, "cross_casting", status)


# ── PDF ───────────────────────────────────────────────────────────────────────

@router.get(
    "/jobs/{job_id}/review/pdf",
    summary="Export the review report as a PDF file",
    tags=["Review"],
    responses={200: {"content": {"application/pdf": {}}}},
)
async def export_review_pdf(
    job_id: str,
    lang: Literal["ar", "en"] = Query("ar", description="Report language (ar = RTL)"),
    scope: Literal["all", "casting", "cross_casting", "verification"] = Query("all"),
    status: Status | None = Query(None, description="Only items in this status"),
    engine: Engine = Depends(_engine),
) -> Response:
    """Download the review as a PDF (`Content-Disposition: attachment`)."""
    report = await run_in_threadpool(_review_sync, engine, job_id, scope, status)
    pdf = await run_in_threadpool(render_review_pdf, report, lang)
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="review-{job_id}-{lang}.pdf"'},
    )


# ── Resolve ───────────────────────────────────────────────────────────────────

def _set_status_sync(engine, job_id: str, issue_id: str, status: str) -> IssueOut:
    with get_session(engine) as session:
        if not session.get(AuditJob, _uuid(job_id)):
            raise HTTPException(404, f"Job {job_id!r} not found")
        issue = session.get(Issue, _uuid(issue_id))
        if not issue or str(issue.job_id) != job_id:
            raise HTTPException(404, f"Issue {issue_id!r} not found in job {job_id!r}")
        issue.status = status
        session.flush()
        return IssueOut(
            id=str(issue.id), job_id=str(issue.job_id),
            table_id=str(issue.table_id) if issue.table_id else None,
            issue_type=issue.issue_type, severity=issue.severity, status=issue.status,
            table_name=issue.table_name, description=issue.description,
            description_ar=issue.description_ar, expected_value=issue.expected_value,
            actual_value=issue.actual_value, difference=issue.difference,
            source_reference=issue.source_reference, created_at=issue.created_at,
        )


@router.patch(
    "/jobs/{job_id}/issues/{issue_id}",
    response_model=IssueOut,
    summary="Set the review status of a finding (open / acknowledged / resolved)",
    tags=["Review"],
)
async def update_issue_status(
    job_id: str,
    issue_id: str,
    body: IssueStatusUpdateIn,
    engine: Engine = Depends(_engine),
) -> IssueOut:
    """Mark a finding as reviewed. Re-running the job resets statuses to `open`."""
    return await run_in_threadpool(_set_status_sync, engine, job_id, issue_id, body.status)
