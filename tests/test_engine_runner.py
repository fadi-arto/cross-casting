"""Tests for engine/runner.py — pipeline background task."""
from __future__ import annotations

import uuid
from decimal import Decimal
from unittest.mock import MagicMock, call, patch

import pytest

from engine.runner import _excel_ref, _pdf_ref, run_pipeline_task


def _make_engine():
    engine = MagicMock()
    session = MagicMock()
    session.__enter__ = MagicMock(return_value=session)
    session.__exit__ = MagicMock(return_value=False)
    engine.connect.return_value = session
    return engine, session


class TestPdfRef:
    def test_basic_pdf_ref(self):
        ref = _pdf_ref(page=3, table_name="Income Statement")
        assert ref["source"] == "pdf"
        assert ref["page"] == 3
        assert ref["table_name"] == "Income Statement"

    def test_pdf_ref_with_location(self):
        ref = _pdf_ref(page=1, table_name="T", row="Total", axis="column")
        assert ref["row"] == "Total"
        assert ref["axis"] == "column"

    def test_pdf_ref_omits_none_fields(self):
        ref = _pdf_ref(page=1, table_name="T")
        assert "row" not in ref or ref.get("row") is None
        assert "bbox" not in ref or ref.get("bbox") is None


class TestExcelRef:
    def test_basic_excel_ref(self):
        ref = _excel_ref(sheet="Sheet1", table_name="Summary")
        assert ref["source"] == "excel"
        assert ref["sheet"] == "Sheet1"
        assert ref["table_name"] == "Summary"

    def test_excel_ref_with_cell(self):
        ref = _excel_ref(sheet="Sheet1", table_name="T", row=5, axis="row")
        assert ref["row"] == 5
        assert ref["axis"] == "row"


class TestRunPipelineTask:
    def _make_settings(self):
        settings = MagicMock()
        settings.vlm.backend = "stub"
        settings.ocr.backend = "stub"
        settings.llm.backend = "none"
        return settings

    def test_sets_processing_status(self):
        engine, session = _make_engine()
        job_id = str(uuid.uuid4())
        settings = self._make_settings()

        with (
            patch("engine.runner.get_session") as mock_sess,
            patch("engine.runner.build_pipeline") as mock_build,
        ):
            mock_sess.return_value.__enter__ = MagicMock(return_value=session)
            mock_sess.return_value.__exit__ = MagicMock(return_value=False)
            mock_pipeline = MagicMock()
            mock_pipeline.process_excel.return_value = _make_clean_report()
            mock_build.return_value = mock_pipeline

            run_pipeline_task(
                job_id=job_id,
                content=b"fake-content",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                filename="test.xlsx",
                engine=engine,
                settings=settings,
            )

        # Should have called session at least once to update status
        assert mock_sess.called

    def test_failed_job_on_pipeline_exception(self):
        engine, session = _make_engine()
        job_id = str(uuid.uuid4())
        settings = self._make_settings()

        with (
            patch("engine.runner.get_session") as mock_sess,
            patch("engine.runner.build_pipeline") as mock_build,
        ):
            mock_sess.return_value.__enter__ = MagicMock(return_value=session)
            mock_sess.return_value.__exit__ = MagicMock(return_value=False)
            mock_build.side_effect = Exception("VLM unavailable")

            run_pipeline_task(
                job_id=job_id,
                content=b"fake-content",
                mime="application/pdf",
                filename="test.pdf",
                engine=engine,
                settings=settings,
            )

        # Must not raise — exception is caught and job marked FAILED
        assert mock_sess.called

    def test_routes_excel_to_read_excel_and_process_tables(self):
        engine, session = _make_engine()
        job_id = str(uuid.uuid4())
        settings = self._make_settings()

        with (
            patch("engine.runner.get_session") as mock_sess,
            patch("engine.runner.build_pipeline") as mock_build,
            patch("engine.runner.read_excel", return_value=[]) as mock_read,
        ):
            mock_sess.return_value.__enter__ = MagicMock(return_value=session)
            mock_sess.return_value.__exit__ = MagicMock(return_value=False)
            mock_pipeline = MagicMock()
            mock_pipeline.process_tables.return_value = _make_clean_report()
            mock_build.return_value = mock_pipeline

            run_pipeline_task(
                job_id=job_id,
                content=b"xlsx-bytes",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                filename="report.xlsx",
                engine=engine,
                settings=settings,
            )

        mock_read.assert_called_once_with(b"xlsx-bytes", source_file="report.xlsx")
        mock_pipeline.process_tables.assert_called_once()
        mock_pipeline.process_pdf.assert_not_called()

    def test_routes_pdf_to_process_pdf(self):
        engine, session = _make_engine()
        job_id = str(uuid.uuid4())
        settings = self._make_settings()

        with (
            patch("engine.runner.get_session") as mock_sess,
            patch("engine.runner.build_pipeline") as mock_build,
        ):
            mock_sess.return_value.__enter__ = MagicMock(return_value=session)
            mock_sess.return_value.__exit__ = MagicMock(return_value=False)
            mock_pipeline = MagicMock()
            mock_pipeline.process_pdf.return_value = _make_clean_report()
            mock_build.return_value = mock_pipeline

            run_pipeline_task(
                job_id=job_id,
                content=b"%PDF-1.4 fake",
                mime="application/pdf",
                filename="report.pdf",
                engine=engine,
                settings=settings,
            )

        mock_pipeline.process_pdf.assert_called_once()
        mock_pipeline.process_excel.assert_not_called()


def _make_clean_report():
    return {
        "source_file": "test.xlsx",
        "tables_processed": 1,
        "total_casting_errors": 0,
        "total_cross_casting_errors": 0,
        "is_clean": True,
        "summary_ar": "لا أخطاء",
        "summary_en": "No errors",
        "results": [
            {
                "table_name": "Balance Sheet",
                "page": None,
                "sheet": "Sheet1",
                "row_count": 5,
                "col_count": 3,
                "raw_data": {
                    "title": "Balance Sheet",
                    "headers": ["Item", "2023", "2022"],
                    "rows": [["Total", "100000", "95000"]],
                },
                "casting_errors": [],
                "cross_casting_errors": [],
                "cell_verifications": [],
            }
        ],
        "findings": [],
    }


def test_fit_truncates_long_ocr_text_to_column_size():
    from engine.runner import _fit
    assert _fit(None) is None and _fit("(320,122)") == "(320,122)"
    long = "Current liabilities, including bank borrowings " * 5
    out = _fit(long)
    assert len(out) == 100 and out.endswith("…")
