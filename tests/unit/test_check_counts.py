"""
Arithmetic check counts (casting_checks / cross_casting_checks / total_checks).

`total_verifications` only counts OCR-vs-VLM cell checks, which never run for
Excel, so a "checks made" figure must come from these counts instead.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from decimal import Decimal
from pathlib import Path

import pytest

from adapters.excel_reader import read_excel
from application.pipeline import CrossCastingPipeline
from domain.casting import check_casting, run_casting
from domain.cross_casting import check_cross_casting, run_cross_casting
from domain.models import Cell, FinancialTable, RowKind, TableRow
from schemas.api import AuditReportOut, report_to_out

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
WORKBOOK = FIXTURES / "Casting_Test_With_Errors.xlsx"
CLEAN_CONTROL = FIXTURES / "disclosure_pack" / "04_Cedar_Management_Accounts.xlsx"


def _row(i, label, values, kind=RowKind.DATA):
    cells = tuple(
        Cell(value=Decimal(v), raw_text=str(v), row_index=i, col_index=c)
        for c, v in enumerate(values)
    )
    return TableRow(index=i, label=label, cells=cells, kind=kind)


def _quarterly(total_row_values):
    #            2024  2025 │ Total
    # Sales       100   150 │  250
    # Marketing    50    60 │  110
    # Total       150   210 │  360
    return FinancialTable(
        title="T",
        headers=("2024", "2025", "Total"),
        rows=(
            _row(0, "Sales", [100, 150, 250]),
            _row(1, "Marketing", [50, 60, 110]),
            _row(2, "Total", total_row_values, RowKind.TOTAL),
        ),
    )


def _pipeline():
    return CrossCastingPipeline(None, None, None, None)


def _audit(path):
    return _pipeline().process_tables(read_excel(path.read_bytes(), path.name), source_file=path.name)


class TestEngineCounts:
    def test_clean_table_counts_every_comparison(self):
        table = _quarterly([150, 210, 360])
        errors, checks = run_casting(table)
        # vertical: Total row × 3 columns; horizontal: 3 rows against the Total column
        assert (errors, checks) == ((), 6)
        x_errors, x_checks = run_cross_casting(table)
        # one total row: rows-vs-columns, rows-vs-grand, columns-vs-grand
        assert (x_errors, x_checks) == ((), 3)

    def test_failed_comparisons_are_still_counted(self):
        table = _quarterly([150, 210, 370])   # grand total off by 10
        errors, checks = run_casting(table)
        assert checks == 6 and len(errors) >= 1
        x_errors, x_checks = run_cross_casting(table)
        assert x_checks == 3 and len(x_errors) >= 1

    def test_check_functions_unchanged(self):
        table = _quarterly([150, 210, 370])
        assert check_casting(table) == run_casting(table)[0]
        assert check_cross_casting(table) == run_cross_casting(table)[0]

    def test_pipeline_result_carries_counts(self):
        report = _pipeline().process_tables([_quarterly([150, 210, 360])])
        (result,) = report.validation_results
        assert (result.casting_checks, result.cross_casting_checks) == (6, 3)
        assert result.total_checks == report.total_checks == 9
        assert result.cell_verifications == ()   # no OCR for in-memory tables


@pytest.mark.skipif(not WORKBOOK.exists(), reason="test workbook not present")
class TestExcelReportsChecks:
    def test_excel_with_errors_reports_nonzero_checks(self):
        out = report_to_out(_audit(WORKBOOK))
        assert out.total_casting_errors == 3
        assert out.total_checks > out.total_casting_errors + out.total_cross_casting_errors
        for r in out.results:
            assert r.total_verifications == 0          # Excel: no OCR/VLM cell checks
            assert r.total_checks == r.casting_checks + r.cross_casting_checks > 0
            assert r.total_checks >= len(r.casting_errors) + len(r.cross_casting_errors)
        assert out.total_checks == sum(r.total_checks for r in out.results)

    @pytest.mark.skipif(not CLEAN_CONTROL.exists(), reason="test pack not present")
    def test_clean_workbook_reports_checks_and_no_errors(self):
        report = _audit(CLEAN_CONTROL)
        assert report.is_clean
        assert report.total_checks > 0


class TestStoredReports:
    def test_report_saved_before_counts_existed_still_loads(self):
        old = {
            "source_file": "old.xlsx", "tables_processed": 1,
            "total_casting_errors": 1, "total_cross_casting_errors": 0,
            "is_clean": False, "summary_ar": "", "summary_en": "",
            "results": [{
                "table_title": "T", "page": 1, "sheet": "S",
                "casting_errors": [], "cross_casting_errors": [],
                "disagreements": 0, "total_verifications": 0,
            }],
            "findings": [],
        }
        out = AuditReportOut(**old)
        assert out.total_checks is None
        assert out.results[0].total_checks is None
