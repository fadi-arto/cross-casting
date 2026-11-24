"""Tests for GET /jobs/{id}/issues endpoints."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from api.routes import app
from schemas.api import (
    FileSummaryOut,
    IssueListOut,
    IssueOut,
    IssueSummaryOut,
)


def _now():
    return datetime.now(timezone.utc)


def _make_issue(issue_type="CASTING_ERROR", severity="high"):
    return IssueOut(
        id=str(uuid.uuid4()),
        job_id=str(uuid.uuid4()),
        table_id=str(uuid.uuid4()),
        issue_type=issue_type,
        severity=severity,
        status="open",
        table_name="Revenue Statement",
        description="Casting error detected",
        description_ar="خطأ تدقيق",
        expected_value="30000.00",
        actual_value="31000.00",
        difference="1000.00",
        source_reference={
            "source": "pdf",
            "page": 4,
            "table_name": "Revenue Statement",
            "row": "Total Revenue",
            "axis": "column",
        },
        created_at=_now(),
    )


@pytest.fixture()
def client():
    engine = MagicMock()
    minio = MagicMock()
    settings = MagicMock()
    settings.storage.bucket = "test-bucket"
    app.state.engine = engine
    app.state.minio = minio
    app.state.settings = settings
    return TestClient(app, raise_server_exceptions=False)


class TestGetIssues:
    def test_returns_issue_list(self, client):
        job_id = str(uuid.uuid4())
        issue = _make_issue()
        with patch("api.routes._get_issues_sync") as mock_issues:
            mock_issues.return_value = IssueListOut(
                job_id=job_id,
                file=FileSummaryOut(file_name="test.pdf", file_type="pdf"),
                summary=IssueSummaryOut(
                    total_issues=1, casting_errors=1,
                    cross_casting_errors=0, ocr_vlm_disagreements=0,
                ),
                total=1, page=1, page_size=20,
                issues=[issue],
            )
            resp = client.get(f"/jobs/{job_id}/issues")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert len(data["issues"]) == 1
        assert data["issues"][0]["issue_type"] == "CASTING_ERROR"

    def test_issue_has_source_reference(self, client):
        job_id = str(uuid.uuid4())
        issue = _make_issue()
        with patch("api.routes._get_issues_sync") as mock_issues:
            mock_issues.return_value = IssueListOut(
                job_id=job_id,
                file=FileSummaryOut(file_name="test.pdf", file_type="pdf"),
                summary=IssueSummaryOut(
                    total_issues=1, casting_errors=1,
                    cross_casting_errors=0, ocr_vlm_disagreements=0,
                ),
                total=1, page=1, page_size=20,
                issues=[issue],
            )
            resp = client.get(f"/jobs/{job_id}/issues")
        issue_data = resp.json()["issues"][0]
        assert issue_data["source_reference"]["page"] == 4
        assert issue_data["source_reference"]["source"] == "pdf"

    def test_issue_values_are_strings(self, client):
        job_id = str(uuid.uuid4())
        issue = _make_issue()
        with patch("api.routes._get_issues_sync") as mock_issues:
            mock_issues.return_value = IssueListOut(
                job_id=job_id,
                file=FileSummaryOut(file_name="test.pdf", file_type="pdf"),
                summary=IssueSummaryOut(
                    total_issues=1, casting_errors=1,
                    cross_casting_errors=0, ocr_vlm_disagreements=0,
                ),
                total=1, page=1, page_size=20,
                issues=[issue],
            )
            resp = client.get(f"/jobs/{job_id}/issues")
        issue_data = resp.json()["issues"][0]
        # Values must be strings, not floats
        assert isinstance(issue_data["expected_value"], str)
        assert isinstance(issue_data["actual_value"], str)
        assert isinstance(issue_data["difference"], str)

    def test_empty_issues_for_clean_job(self, client):
        job_id = str(uuid.uuid4())
        with patch("api.routes._get_issues_sync") as mock_issues:
            mock_issues.return_value = IssueListOut(
                job_id=job_id,
                file=FileSummaryOut(file_name="clean.xlsx", file_type="excel"),
                summary=IssueSummaryOut(
                    total_issues=0, casting_errors=0,
                    cross_casting_errors=0, ocr_vlm_disagreements=0,
                ),
                total=0, page=1, page_size=20,
                issues=[],
            )
            resp = client.get(f"/jobs/{job_id}/issues")
        assert resp.status_code == 200
        assert resp.json()["total"] == 0


class TestGetIssueDetail:
    def test_returns_single_issue(self, client):
        job_id = str(uuid.uuid4())
        issue = _make_issue()
        with patch("api.routes._get_issue_detail_sync") as mock_issue:
            mock_issue.return_value = issue
            resp = client.get(f"/jobs/{job_id}/issues/{issue.id}")
        assert resp.status_code == 200
        assert resp.json()["issue_type"] == "CASTING_ERROR"
