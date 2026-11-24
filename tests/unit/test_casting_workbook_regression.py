"""
Regression: tests/fixtures/Casting_Test_With_Errors.xlsx (Income Statement + Balance Sheet).

Expected results were verified by hand from the workbook's own figures:

Balance Sheet (FY2024 | FY2025 | Total)
  Total assets FY2024  = 180,000 + 240,000 + 310,000 + 870,000 = 1,600,000  ✓
  Total assets FY2025  = 220,000 + 280,000 + 350,000 + 950,000 = 1,800,000  declared 1,815,000 ✗ (+15,000)
  Total liabilities    = 600,000 / 660,000                                  ✓
  Total equity         = 1,000,000 / 1,140,000                              ✓
  Total L&E            = Total liabilities + Total equity = 1,600,000 / 1,800,000 ✓

Income Statement
  Gross profit FY2025      = 1,480,000 − 845,000 = 635,000   declared 645,000 ✗ (+10,000)
  Profit before tax FY2025 =   400,000 −  30,000 = 370,000   declared 360,000 ✗ (−10,000)
  every other line reconciles; the Total column = FY2024 + FY2025 on every row.

Before the fix the engine reported 12 casting + 2 cross-casting errors on the
Balance Sheet only (column sums of 3,200,000 / 3,600,000 / 6,800,000 compared
with every total row, columns labelled with the sheet title, '1' and '2') and
missed both Income Statement errors.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from decimal import Decimal
from pathlib import Path

import pytest

from adapters.excel_reader import read_excel
from application.pipeline import CrossCastingPipeline
from domain.models import RowKind
from domain.structure import analyze_structure
from schemas.api import report_to_out

WORKBOOK = Path(__file__).resolve().parents[1] / "fixtures" / "Casting_Test_With_Errors.xlsx"
pytestmark = pytest.mark.skipif(not WORKBOOK.exists(), reason="test workbook not present")


class _NoRender:
    def render(self, *a, **k):  # Excel path never renders
        raise AssertionError("renderer must not be used for Excel")


class _NoReader:
    def extract_tables(self, page):
        raise AssertionError("VLM must not be used for Excel")


@pytest.fixture(scope="module")
def tables():
    return read_excel(WORKBOOK.read_bytes(), WORKBOOK.name)


@pytest.fixture(scope="module")
def report(tables):
    pipeline = CrossCastingPipeline(_NoRender(), _NoReader(), None, None)
    return pipeline.process_tables(tables, source_file=WORKBOOK.name)


def _result(report, sheet):
    return next(r for r in report.validation_results if r.table.sheet == sheet)


# ── Structure recognition ─────────────────────────────────────────────────────

class TestStructure:
    def test_one_table_per_statement(self, tables):
        assert [t.sheet for t in tables] == ["Income Statement", "Balance Sheet"]

    def test_title_subtitle_and_header_rows(self, tables):
        bs = tables[1]
        assert bs.title.startswith("ACME Trading Ltd. — Statement of Financial Position")
        assert bs.subtitle == "As at 31 December 2025 and 2024"
        assert bs.label_header == "Account"
        kinds = [r.kind for r in bs.rows[:3]]
        assert kinds == [RowKind.TITLE, RowKind.SUBTITLE, RowKind.HEADER]
        # title / subtitle / header rows never carry amounts
        assert all(c.value is None for r in bs.rows[:3] for c in r.cells)

    def test_real_column_headers_and_periods(self, tables):
        for t in tables:
            assert t.headers == ("FY2024", "FY2025", "Total")
            assert [c.period for c in t.columns] == ["FY2024", "FY2025", None]
            assert [c.role for c in t.columns] == ["value", "value", "total"]
            assert all(c.decimals == 2 for c in t.columns)
            assert [c.source_col for c in t.columns] == [1, 2, 3]   # B, C, D

    def test_balance_sheet_row_kinds(self, tables):
        kinds = {r.label: r.kind for r in tables[1].rows}
        for label in ("Total assets", "Total liabilities", "Total equity",
                      "Total liabilities and equity"):
            assert kinds[label] == RowKind.TOTAL
        for label in ("Cash and cash equivalents", "Inventory", "Borrowings", "Retained earnings"):
            assert kinds[label] == RowKind.DATA

    def test_balance_sheet_relationships(self, tables):
        s = analyze_structure(tables[1])
        formulas = [r.formula for r in s.relations]
        assert formulas == [
            "Total assets = Cash and cash equivalents + Trade receivables + Inventory + "
            "Property, plant and equipment",
            "Total liabilities = Trade payables + Borrowings",
            "Total equity = Share capital + Retained earnings",
            "Total liabilities and equity = Total liabilities + Total equity",
        ]
        assert all(r.confirmed for r in s.relations)
        assert s.horizontal_formula() == "Total = FY2024 + FY2025"

    def test_income_statement_relationships(self, tables):
        s = analyze_structure(tables[0])
        assert [r.formula for r in s.relations] == [
            "Gross profit = Revenue − Cost of sales",
            "Operating profit = Gross profit − Operating expenses",
            "Profit before tax = Operating profit − Finance costs",
            "Profit for the year = Profit before tax − Income tax expense",
        ]


# ── Findings ──────────────────────────────────────────────────────────────────

class TestFindings:
    def test_balance_sheet_single_total_assets_error(self, report):
        bs = _result(report, "Balance Sheet")
        assert bs.cross_casting_errors == ()
        assert len(bs.casting_errors) == 1
        e = bs.casting_errors[0]
        assert (e.axis, e.label, e.row_label) == ("column", "FY2025", "Total assets")
        assert e.declared == Decimal("1815000")
        assert e.computed == Decimal("1800000.00")
        assert e.delta == Decimal("15000.00")
        assert e.cell_ref == "Balance Sheet!C8"

    def test_total_column_and_cross_cast_merged_as_evidence(self, report):
        e = _result(report, "Balance Sheet").casting_errors[0]
        joined = " ".join(e.evidence)
        assert "'Total' column of 'Total assets'" in joined
        assert "3,415,000.00" in joined and "3,400,000.00" in joined
        assert "cross-cast of 'Total assets'" in joined

    def test_income_statement_errors(self, report):
        inc = _result(report, "Income Statement")
        assert inc.cross_casting_errors == ()
        got = sorted((e.row_label, e.label, e.declared, e.computed, e.delta) for e in inc.casting_errors)
        assert got == [
            ("Gross profit", "FY2025", Decimal("645000"), Decimal("635000.00"), Decimal("10000.00")),
            ("Profit before tax", "FY2025", Decimal("360000"), Decimal("370000.00"), Decimal("-10000.00")),
        ]

    def test_no_false_positives(self, report):
        """The old symptoms must be gone."""
        bad_labels = {"1", "2", "0"}
        for r in report.validation_results:
            for e in r.casting_errors:
                assert e.label not in bad_labels
                assert e.label != r.table.title
                assert e.computed not in (Decimal("3200000"), Decimal("3600000"), Decimal("6800000"))
            for x in r.cross_casting_errors:
                assert x.row_total != Decimal("6800000")
        assert report.total_casting_errors == 3
        assert report.total_cross_casting_errors == 0

    def test_no_warnings_for_a_fully_recognised_workbook(self, report):
        for r in report.validation_results:
            assert r.warnings == ()

    def test_api_report_carries_traceability(self, report):
        out = report_to_out(report).model_dump(mode="json")
        bs = next(r for r in out["results"] if r["sheet"] == "Balance Sheet")
        err = bs["casting_errors"][0]
        assert err["row_label"] == "Total assets"
        assert err["cell_ref"] == "Balance Sheet!C8"
        assert err["relation"].startswith("Total assets = Cash and cash equivalents")
        assert bs["warnings"] == []
