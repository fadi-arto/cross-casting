"""Regression tests: Excel table-structure recognition, end to end through the pipeline."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

import io
from decimal import Decimal

import openpyxl
from openpyxl.styles import Font

from adapters.excel_reader import read_excel
from application.pipeline import CrossCastingPipeline
from domain.models import RowKind

BOLD = "bold"


def _xlsx(rows, sheet="Sheet1", fmt=None, merge=None):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet
    for r_idx, row in enumerate(rows, start=1):
        bold = bool(row) and row[-1] == BOLD
        values = row[:-1] if bold else row
        for c_idx, v in enumerate(values, start=1):
            cell = ws.cell(r_idx, c_idx, v)
            if bold:
                cell.font = Font(bold=True)
            if fmt and isinstance(v, (int, float)):
                cell.number_format = fmt
    for rng in merge or []:
        ws.merge_cells(rng)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _audit(content):
    tables = read_excel(content, "t.xlsx")
    report = CrossCastingPipeline(None, None, None, None).process_tables(tables)
    return tables, report


def _kinds(table):
    return [(r.kind, r.label) for r in table.rows]


class TestHeaderAndTitleRows:
    def test_title_subtitle_header_detected_and_never_summed(self):
        tables, report = _audit(_xlsx([
            ["ACME — Balance Sheet", BOLD],
            ["As at 31 December"],
            ["Account", "FY2024", "FY2025", BOLD],
            ["Cash", 100, 120],
            ["Inventory", 50, 60],
            ["Total assets", 150, 180],
        ]))
        (t,) = tables
        assert t.title == "ACME — Balance Sheet"
        assert t.subtitle == "As at 31 December"
        assert t.headers == ("FY2024", "FY2025")
        assert [k for k, _ in _kinds(t)][:3] == [RowKind.TITLE, RowKind.SUBTITLE, RowKind.HEADER]
        assert report.is_clean

    def test_numeric_year_headers(self):
        (t,), report = _audit(_xlsx([
            [None, 2025, 2024],
            ["Revenue", 500, 400],
            ["Other income", 20, 10],
            ["Total income", 520, 410],
        ]))
        assert t.headers == ("2025", "2024")
        assert [c.period for c in t.columns] == ["2025", "2024"]
        assert report.is_clean

    def test_merged_title_is_not_copied_into_columns(self):
        (t,), report = _audit(_xlsx(
            [["Statement title", None, None], ["Item", "2024", "2025"],
             ["A", 1, 2], ["Total", 1, 2]],
            merge=["A1:C1"],
        ))
        assert t.title == "Statement title"
        assert t.headers == ("2024", "2025")
        assert report.is_clean

    def test_two_row_header_merges(self):
        (t,), _ = _audit(_xlsx(
            [[None, "Year ended 31 December", None], ["Item", "2025", "2024"],
             ["A", 1, 2], ["Total", 1, 2]],
            merge=["B1:C1"],
        ))
        assert t.headers == ("Year ended 31 December 2025", "Year ended 31 December 2024")


class TestEmptySectionFootnoteRows:
    def test_blank_rows_and_sections_do_not_split_the_statement(self):
        tables, report = _audit(_xlsx([
            ["Item", "2025"],
            ["Assets"],
            ["Cash", 10],
            ["Inventory", 20],
            ["Total assets", 30],
            [],
            ["Liabilities"],
            ["Loans", 12],
            ["Total liabilities", 12],
            [],
            ["* Figures are unaudited"],
        ]))
        assert len(tables) == 1
        kinds = dict((label, kind) for kind, label in _kinds(tables[0]))
        assert kinds["Assets"] == RowKind.SECTION_HEADER
        assert kinds["Liabilities"] == RowKind.SECTION_HEADER
        assert kinds["* Figures are unaudited"] == RowKind.FOOTNOTE
        assert RowKind.EMPTY in [k for k, _ in _kinds(tables[0])]
        assert report.is_clean

    def test_nil_placeholders(self):
        (_,), report = _audit(_xlsx([
            ["Item", "2024", "2025"],
            ["A", 10, 20],
            ["B", "-", 5],
            ["Total", 10, 25],
        ]))
        assert report.is_clean


class TestLayouts:
    def test_label_in_column_b_with_note_column(self):
        (t,), report = _audit(_xlsx([
            [None, "Account", "Note", "2025", "2024"],
            [None, "Cash", 4, 100, 90],
            [None, "Receivables", 5, 50, 40],
            [None, "Total assets", None, 150, 130],
        ]))
        assert [c.role for c in t.columns] == ["note", "value", "value"]
        assert report.is_clean
        assert any("note-reference" in w for r in report.validation_results for w in r.warnings)

    def test_two_tables_on_one_sheet(self):
        tables, report = _audit(_xlsx([
            ["Revenue by region"],
            ["Region", "2025"],
            ["North", 10], ["South", 20], ["Total", 30],
            [],
            ["Headcount"],
            ["Team", "2025"],
            ["Ops", 5], ["Sales", 7], ["Total", 13],   # wrong: 12
        ]))
        assert [t.title for t in tables] == ["Revenue by region", "Headcount"]
        errs = [e for r in report.validation_results for e in r.casting_errors]
        assert [(e.table_title, e.row_label, e.declared) for e in errs] == [
            ("Headcount", "Total", Decimal("13")),
        ]

    def test_parenthesised_negative_text_values(self):
        (_,), report = _audit(_xlsx([
            ["Item", "2025"],
            ["Revenue", "1,000"],
            ["Cost of sales", "(600)"],
            ["Gross profit", "400"],
        ]))
        assert report.is_clean

    def test_arabic_statement(self):
        tables, report = _audit(_xlsx([
            ["قائمة المركز المالي"],
            ["البند", "2025", "2024"],
            ["النقد", 100, 80],
            ["المخزون", 50, 40],
            ["إجمالي الأصول", 150, 125],   # 2024 should be 120
        ]))
        (t,) = tables
        assert t.title == "قائمة المركز المالي"
        assert t.label_header == "البند"
        errs = report.validation_results[0].casting_errors
        assert [(e.row_label, e.label, e.delta) for e in errs] == [
            ("إجمالي الأصول", "2024", Decimal("5.00")),
        ]

    def test_cell_reference_points_at_the_wrong_cell(self):
        (_,), report = _audit(_xlsx([
            ["Title"],
            ["Item", "2024", "2025"],
            ["A", 1, 2], ["B", 1, 2], ["Total", 2, 5],
        ], sheet="BS"))
        (e,) = report.validation_results[0].casting_errors
        assert e.cell_ref == "BS!C5"


class TestPrecisionAndCurrency:
    def test_number_format_precision_and_currency(self):
        (t,), report = _audit(_xlsx(
            [["Item", "SAR '000 2025"], ["A", 0.1], ["B", 0.2], ["Total", 0.3]],
            fmt='"SAR" #,##0.000',
        ))
        col = t.columns[0]
        assert (col.decimals, col.currency, col.scale, col.period) == (3, "SAR", "'000", "2025")
        assert report.is_clean          # 0.1 + 0.2 == 0.3 in Decimal


class TestNoHeader:
    def test_headerless_table_warns(self):
        (_,), report = _audit(_xlsx([["A", 1], ["B", 2], ["Total", 3]]))
        warnings = report.validation_results[0].warnings
        assert any("No column-heading row" in w for w in warnings)
        assert report.is_clean
