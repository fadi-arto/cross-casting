"""Tests for GET /health endpoint."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from api.routes import app


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


class TestHealth:
    def test_healthy_returns_200(self, client):
        with (
            patch("api.routes.db_ping") as mock_db,
            patch("api.routes.minio_ping") as mock_minio,
        ):
            mock_db.return_value = None
            mock_minio.return_value = None
            resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["database"] == "ok"
        assert data["storage"] == "ok"

    def test_db_failure_returns_503(self, client):
        with (
            patch("api.routes.db_ping") as mock_db,
            patch("api.routes.minio_ping") as mock_minio,
        ):
            mock_db.side_effect = Exception("connection refused")
            mock_minio.return_value = None
            resp = client.get("/health")
        assert resp.status_code == 503
        data = resp.json()
        assert data["status"] == "degraded"
        assert data["database"] != "ok"

    def test_minio_failure_returns_503(self, client):
        with (
            patch("api.routes.db_ping") as mock_db,
            patch("api.routes.minio_ping") as mock_minio,
        ):
            mock_db.return_value = None
            mock_minio.side_effect = Exception("bucket not found")
            resp = client.get("/health")
        assert resp.status_code == 503
        data = resp.json()
        assert data["status"] == "degraded"
        assert data["storage"] != "ok"

    def test_response_includes_version_field(self, client):
        with (
            patch("api.routes.db_ping"),
            patch("api.routes.minio_ping"),
        ):
            resp = client.get("/health")
        data = resp.json()
        assert "status" in data
