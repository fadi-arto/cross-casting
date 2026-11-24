"""Tests for GET /jobs/{id}/tables endpoints."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from api.routes import app
from schemas.api import ExtractedTableDetailOut, ExtractedTableOut, TableListOut


def _now():
    return datetime.now(timezone.utc)


def _make_table(with_raw_data=False):
    t = ExtractedTableOut(
        id=str(uuid.uuid4()),
        job_id=str(uuid.uuid4()),
        table_name="Balance Sheet",
        page=2,
        sheet=None,
        row_count=10,
        col_count=4,
    )
    if with_raw_data:
        return ExtractedTableDetailOut(
            **t.model_dump(),
            raw_data={
                "title": "Balance Sheet",
                "headers": ["Item", "2023", "2022", "Change"],
                "rows": [
                    ["Total Assets", "100000", "95000", "5000"],
                    ["Total Liabilities", "60000", "58000", "2000"],
                ],
            },
        )
    return t


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


class TestListTables:
    def test_returns_table_list(self, client):
        job_id = str(uuid.uuid4())
        table = _make_table()
        with patch("api.routes._get_tables_sync") as mock_tables:
            mock_tables.return_value = TableListOut(
                job_id=job_id, total=1, items=[table],
            )
            resp = client.get(f"/jobs/{job_id}/tables")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert len(data["items"]) == 1
        assert data["items"][0]["table_name"] == "Balance Sheet"

    def test_empty_list_for_queued_job(self, client):
        job_id = str(uuid.uuid4())
        with patch("api.routes._get_tables_sync") as mock_tables:
            mock_tables.return_value = TableListOut(job_id=job_id, total=0, items=[])
            resp = client.get(f"/jobs/{job_id}/tables")
        assert resp.status_code == 200
        assert resp.json()["total"] == 0

    def test_table_has_page_number_for_pdf(self, client):
        job_id = str(uuid.uuid4())
        table = _make_table()
        with patch("api.routes._get_tables_sync") as mock_tables:
            mock_tables.return_value = TableListOut(job_id=job_id, total=1, items=[table])
            resp = client.get(f"/jobs/{job_id}/tables")
        item = resp.json()["items"][0]
        assert item["page"] == 2


class TestGetTableDetail:
    def test_returns_table_with_raw_data(self, client):
        job_id = str(uuid.uuid4())
        table = _make_table(with_raw_data=True)
        with patch("api.routes._get_table_detail_sync") as mock_table:
            mock_table.return_value = table
            resp = client.get(f"/jobs/{job_id}/tables/{table.id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["raw_data"] is not None
        assert data["raw_data"]["title"] == "Balance Sheet"
        assert len(data["raw_data"]["headers"]) == 4
        assert len(data["raw_data"]["rows"]) == 2

    def test_raw_data_rows_are_strings(self, client):
        job_id = str(uuid.uuid4())
        table = _make_table(with_raw_data=True)
        with patch("api.routes._get_table_detail_sync") as mock_table:
            mock_table.return_value = table
            resp = client.get(f"/jobs/{job_id}/tables/{table.id}")
        rows = resp.json()["raw_data"]["rows"]
        for row in rows:
            for cell in row:
                # Cells must be strings, not numeric types (financial precision)
                assert isinstance(cell, str)
