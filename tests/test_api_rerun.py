"""Tests for POST /jobs/{id}/retry — re-running FAILED and COMPLETED jobs."""
from __future__ import annotations

import uuid
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from api.routes import app


def _job(status):
    job = MagicMock()
    job.id = uuid.uuid4()
    job.status = status
    job.minio_bucket = "crosscasting-files"
    job.minio_object_key = f"{job.id}/report.xlsx"
    job.mime_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    job.filename = "report.xlsx"
    job.report = {"anything": "from the previous run"}
    return job


@pytest.fixture()
def client():
    app.state.engine = MagicMock()
    app.state.minio = MagicMock()
    app.state.settings = MagicMock()
    return TestClient(app, raise_server_exceptions=False)


@contextmanager
def _patched(job):
    session = MagicMock()
    session.get.return_value = job

    @contextmanager
    def fake_session(_engine):
        yield session

    with (
        patch("api.routes.get_session", fake_session),
        patch("api.routes.download_object", return_value=b"file-bytes") as download,
        patch("api.routes.clear_job_results") as clear,
        patch("api.routes.run_pipeline_task") as run,
    ):
        yield session, download, clear, run


@pytest.mark.parametrize("status", ["COMPLETED", "FAILED", "completed", "failed"])
def test_finished_jobs_can_be_rerun(client, status):
    job = _job(status)
    with _patched(job) as (session, download, clear, run):
        resp = client.post(f"/jobs/{job.id}/retry")

    assert resp.status_code == 202, resp.text
    assert resp.json()["status"] == "QUEUED"
    download.assert_called_once_with(app.state.minio, job.minio_bucket, job.minio_object_key)
    clear.assert_called_once_with(session, job.id, include_logs=True)
    assert job.status == "QUEUED"
    assert job.report is None
    assert job.total_casting_errors is None
    run.assert_called_once()
    assert run.call_args.kwargs["content"] == b"file-bytes"


@pytest.mark.parametrize("status", ["QUEUED", "PROCESSING"])
def test_running_jobs_cannot_be_rerun(client, status):
    job = _job(status)
    with _patched(job) as (_session, download, clear, run):
        resp = client.post(f"/jobs/{job.id}/retry")

    assert resp.status_code == 409
    assert status in resp.json()["detail"]
    download.assert_not_called()
    clear.assert_not_called()
    run.assert_not_called()


def test_unknown_job_is_404(client):
    with _patched(None) as (_session, _download, _clear, run):
        resp = client.post(f"/jobs/{uuid.uuid4()}/retry")
    assert resp.status_code == 404
    run.assert_not_called()
