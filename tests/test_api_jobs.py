"""Tests for GET /jobs endpoints."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from api.routes import app


def _now():
    return datetime.now(timezone.utc)


def _make_job(status="COMPLETED", is_clean=True):
    job = MagicMock()
    job.id = uuid.uuid4()
    job.created_at = _now()
    job.updated_at = _now()
    job.status = status
    job.stage = "REPORT_GENERATION" if status == "COMPLETED" else "CASTING"
    job.filename = "test.xlsx"
    job.mime_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    job.file_size = 1024
    job.minio_bucket = "crosscasting-files"
    job.minio_object_key = f"{job.id}/test.xlsx"
    job.has_errors = not is_clean
    job.total_casting_errors = 0 if is_clean else 2
    job.total_cross_casting_errors = 0 if is_clean else 1
    job.is_clean = is_clean
    job.started_at = _now()
    job.completed_at = _now() if status == "COMPLETED" else None
    job.error_message = None
    job.report = {
        "source_file": "test.xlsx",
        "tables_processed": 1,
        "total_casting_errors": 0,
        "total_cross_casting_errors": 0,
        "is_clean": True,
        "summary_ar": "لا أخطاء",
        "summary_en": "No errors",
        "results": [],
        "findings": [],
    } if status == "COMPLETED" else None
    return job


@pytest.fixture()
def client():
    engine = MagicMock()
    minio = MagicMock()
    settings = MagicMock()
    settings.storage.bucket = "test-bucket"
    settings.storage.presigned_url_expires_hours = 24
    app.state.engine = engine
    app.state.minio = minio
    app.state.settings = settings
    return TestClient(app, raise_server_exceptions=False)


class TestListJobs:
    def test_returns_job_list(self, client):
        job = _make_job()
        with patch("api.routes._list_jobs_sync") as mock_list:
            from schemas.api import AuditJobSummaryOut, JobListOut
            mock_list.return_value = JobListOut(
                total=1, page=1, page_size=20,
                items=[_job_to_summary(job)],
            )
            resp = client.get("/jobs")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert len(data["items"]) == 1

    def test_pagination_params(self, client):
        with patch("api.routes._list_jobs_sync") as mock_list:
            from schemas.api import JobListOut
            mock_list.return_value = JobListOut(total=0, page=2, page_size=10, items=[])
            resp = client.get("/jobs?page=2&page_size=10")
        assert resp.status_code == 200


class TestGetJob:
    def test_completed_job_has_report(self, client):
        job = _make_job(status="COMPLETED")
        with patch("api.routes._get_job_detail_sync") as mock_get:
            from schemas.api import AuditJobDetailOut, AuditReportOut
            detail = AuditJobDetailOut(
                **_job_to_summary(job).model_dump(),
                report=AuditReportOut(**job.report),
            )
            mock_get.return_value = detail
            resp = client.get(f"/jobs/{job.id}")
        assert resp.status_code == 200
        assert resp.json()["report"] is not None

    def test_queued_job_has_no_report(self, client):
        job = _make_job(status="QUEUED")
        job.report = None
        with patch("api.routes._get_job_detail_sync") as mock_get:
            from schemas.api import AuditJobDetailOut
            mock_get.return_value = AuditJobDetailOut(
                **_job_to_summary(job).model_dump(), report=None,
            )
            resp = client.get(f"/jobs/{job.id}")
        assert resp.status_code == 200
        assert resp.json()["report"] is None

    def test_invalid_uuid_returns_400(self, client):
        resp = client.get("/jobs/not-a-uuid")
        # 400 from UUID parse or 404 — either is acceptable
        assert resp.status_code in (400, 404)


def _job_to_summary(job):
    from schemas.api import AuditJobSummaryOut
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
    )
