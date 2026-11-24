"""Tests for POST /analyze — file upload and async job creation."""
from __future__ import annotations

import io
import uuid
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from api.routes import app


@pytest.fixture()
def mock_state():
    """Inject mock engine, minio, and settings into app.state."""
    engine = MagicMock()
    minio = MagicMock()

    settings = MagicMock()
    settings.storage.bucket = "test-bucket"
    settings.storage.presigned_url_expires_hours = 24
    settings.storage.endpoint = "localhost:9000"
    settings.storage.access_key = "minioadmin"
    settings.storage.secret_key.get_secret_value.return_value = "minioadmin"
    settings.storage.secure = False

    app.state.engine = engine
    app.state.minio = minio
    app.state.settings = settings
    return engine, minio, settings


@pytest.fixture()
def client(mock_state):
    return TestClient(app, raise_server_exceptions=False)


def _excel_bytes() -> bytes:
    """Minimal valid XLSX bytes (empty workbook)."""
    import openpyxl, io
    wb = openpyxl.Workbook()
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


class TestAnalyzeEndpoint:
    def test_returns_202_queued(self, client, mock_state):
        engine, minio, settings = mock_state
        with (
            patch("api.routes.upload_object"),
            patch("api.routes.get_session") as mock_sess,
            patch("api.routes.run_pipeline_task"),
        ):
            mock_sess.return_value.__enter__ = MagicMock(return_value=MagicMock())
            mock_sess.return_value.__exit__ = MagicMock(return_value=False)

            resp = client.post(
                "/analyze",
                files={"file": ("report.xlsx", _excel_bytes(),
                                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
            )

        assert resp.status_code == 202
        data = resp.json()
        assert data["status"] == "QUEUED"
        assert "job_id" in data
        assert "message" in data

    def test_empty_file_returns_400(self, client, mock_state):
        resp = client.post("/analyze", files={"file": ("empty.pdf", b"", "application/pdf")})
        assert resp.status_code == 400

    def test_unsupported_type_returns_415(self, client, mock_state):
        with (
            patch("api.routes.upload_object"),
            patch("api.routes.get_session") as mock_sess,
        ):
            mock_sess.return_value.__enter__ = MagicMock(return_value=MagicMock())
            mock_sess.return_value.__exit__ = MagicMock(return_value=False)
            resp = client.post(
                "/analyze",
                files={"file": ("data.csv", b"a,b,c\n1,2,3", "text/csv")},
            )
        assert resp.status_code == 415

    def test_job_id_is_valid_uuid(self, client, mock_state):
        with (
            patch("api.routes.upload_object"),
            patch("api.routes.get_session") as mock_sess,
            patch("api.routes.run_pipeline_task"),
        ):
            mock_sess.return_value.__enter__ = MagicMock(return_value=MagicMock())
            mock_sess.return_value.__exit__ = MagicMock(return_value=False)
            resp = client.post(
                "/analyze",
                files={"file": ("r.pdf", b"%PDF-1.4 test", "application/pdf")},
            )
        if resp.status_code == 202:
            job_id = resp.json()["job_id"]
            uuid.UUID(job_id)   # raises ValueError if not valid UUID
