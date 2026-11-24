"""Unit tests for the Excel reader — creates real .xlsx files in memory."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

import io
from decimal import Decimal

import openpyxl
import pytest

from adapters.excel_reader import read_excel
from domain.models import RowKind


def _make_xlsx(data: list[list]) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    for row in data:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


class TestBasicExtraction:
    def test_simple_table_parsed(self):
        xlsx = _make_xlsx([
            ["Department", "2023", "2022"],
            ["Sales",      1000,   900],
            ["Other",      500,    450],
            ["Total",      1500,   1350],
        ])
        tables = read_excel(xlsx, "test.xlsx")
        assert len(tables) == 1
        tbl = tables[0]
        assert len(tbl.rows) >= 3

    def test_total_row_identified(self):
        xlsx = _make_xlsx([
            ["Item",    "Amount"],
            ["Revenue", 5000],
            ["Cost",    3000],
            ["Total",   8000],
        ])
        tables = read_excel(xlsx, "test.xlsx")
        tbl = tables[0]
        total_rows = [r for r in tbl.rows if r.kind == RowKind.TOTAL]
        assert len(total_rows) >= 1

    def test_arabic_total_label(self):
        xlsx = _make_xlsx([
            ["البند",        "المبلغ"],
            ["الإيرادات",    5000],
            ["المصاريف",     3000],
            ["الإجمالي",     8000],
        ])
        tables = read_excel(xlsx, "test.xlsx")
        tbl = tables[0]
        total_rows = [r for r in tbl.rows if r.kind == RowKind.TOTAL]
        assert len(total_rows) >= 1

    def test_decimal_values_parsed(self):
        xlsx = _make_xlsx([
            ["Item", "Value"],
            ["A",    1234.56],
            ["B",    789.01],
            ["Total", 2023.57],
        ])
        tables = read_excel(xlsx, "test.xlsx")
        tbl = tables[0]
        data_rows = [r for r in tbl.rows if r.kind == RowKind.DATA]
        assert data_rows[0].cells[0].value == Decimal("1234.56")

    def test_empty_cells_are_none(self):
        xlsx = _make_xlsx([
            ["Dept", "Q1", "Q2"],
            ["A",    100,  None],
            ["B",    None, 200],
            ["T",    100,  200],
        ])
        tables = read_excel(xlsx, "test.xlsx")
        tbl = tables[0]
        data_rows = [r for r in tbl.rows if r.kind == RowKind.DATA]
        assert data_rows[0].cells[1].value is None  # Q2 of A
        assert data_rows[1].cells[0].value is None  # Q1 of B


class TestEdgeCases:
    def test_negative_values(self):
        xlsx = _make_xlsx([
            ["Item",    "Value"],
            ["Income",  5000],
            ["Loss",   -2000],
            ["Net",     3000],
        ])
        tables = read_excel(xlsx, "test.xlsx")
        tbl = tables[0]
        data_rows = [r for r in tbl.rows if r.kind == RowKind.DATA]
        loss_row = next(r for r in data_rows if r.label == "Loss")
        assert loss_row.cells[0].value == Decimal("-2000")

    def test_multiple_sheets(self):
        wb = openpyxl.Workbook()
        ws1 = wb.active
        ws1.title = "Income"
        ws1.append(["Item", "Amount"])
        ws1.append(["Rev", 1000])
        ws1.append(["Total", 1000])

        ws2 = wb.create_sheet("Balance")
        ws2.append(["Asset", "Value"])
        ws2.append(["Cash", 500])
        ws2.append(["Total", 500])

        buf = io.BytesIO()
        wb.save(buf)
        tables = read_excel(buf.getvalue(), "multi.xlsx")
        assert len(tables) >= 2
        sheet_names = {t.sheet for t in tables}
        assert "Income" in sheet_names
        assert "Balance" in sheet_names
