"""Unit tests for the casting engine — zero external dependencies."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from decimal import Decimal

import pytest

from domain.casting import check_casting
from domain.models import Cell, FinancialTable, RowKind, TableRow


def _cell(value, row=0, col=0):
    v = Decimal(str(value)) if value is not None else None
    return Cell(value=v, raw_text=str(value) if value is not None else "", row_index=row, col_index=col)


def _row(label, values, kind=RowKind.DATA, idx=0):
    cells = tuple(_cell(v, idx, ci) for ci, v in enumerate(values))
    return TableRow(index=idx, label=label, cells=cells, kind=kind)


def _table(title, headers, rows):
    return FinancialTable(title=title, headers=tuple(headers), rows=tuple(rows))


# ── Vertical casting ──────────────────────────────────────────────────────────

class TestVerticalCasting:
    def test_clean_single_column(self):
        tbl = _table("P&L", ["Amount"],
            [
                _row("Revenue",  [1000], kind=RowKind.DATA,  idx=0),
                _row("Expenses", [600],  kind=RowKind.DATA,  idx=1),
                _row("Net",      [1600], kind=RowKind.TOTAL, idx=2),
            ])
        errors = check_casting(tbl)
        assert len(errors) == 0

    def test_dirty_single_column(self):
        tbl = _table("P&L", ["Amount"],
            [
                _row("Revenue",  [1000], kind=RowKind.DATA,  idx=0),
                _row("Expenses", [600],  kind=RowKind.DATA,  idx=1),
                _row("Net",      [1500], kind=RowKind.TOTAL, idx=2),  # Net = 1000 − 600 = 400
            ])
        errors = check_casting(tbl)
        assert len(errors) == 1
        assert errors[0].axis == "column"
        # A net line deducts expenses presented as positive amounts — it is not
        # Revenue + Expenses (= 1600, the old blind-sum reading).
        assert errors[0].computed == Decimal("400.00")
        assert errors[0].delta == Decimal("1100.00")
        assert errors[0].relation == "Net = Revenue − Expenses"

    def test_clean_multi_column(self):
        tbl = _table("Budget", ["Q1", "Q2", "Q3"],
            [
                _row("Sales",    [100, 150, 200], kind=RowKind.DATA,  idx=0),
                _row("Other",    [50,  60,  70],  kind=RowKind.DATA,  idx=1),
                _row("Total",    [150, 210, 270], kind=RowKind.TOTAL, idx=2),
            ])
        errors = check_casting(tbl)
        assert errors == ()

    def test_dirty_multi_column_one_bad(self):
        tbl = _table("Budget", ["Q1", "Q2", "Q3"],
            [
                _row("Sales",    [100, 150, 200], kind=RowKind.DATA,  idx=0),
                _row("Other",    [50,  60,  70],  kind=RowKind.DATA,  idx=1),
                _row("Total",    [150, 999, 270], kind=RowKind.TOTAL, idx=2),  # Q2 wrong
            ])
        errors = check_casting(tbl)
        assert len(errors) == 1
        assert errors[0].index == 1  # Q2 column


# ── Horizontal casting (row totals) ──────────────────────────────────────────

class TestHorizontalCasting:
    def test_clean_row_totals(self):
        tbl = _table("Matrix", ["", "Q1", "Q2", "Q3", "Total"],
            [
                _row("Sales",  [None, 100, 150, 200, 450], kind=RowKind.DATA, idx=0),
                _row("Other",  [None, 50,  60,  70,  180], kind=RowKind.DATA, idx=1),
                _row("GTotal", [None, 150, 210, 270, 630], kind=RowKind.TOTAL, idx=2),
            ])
        errors = check_casting(tbl)
        assert errors == ()

    def test_dirty_row_total(self):
        tbl = _table("Matrix", ["", "Q1", "Q2", "Q3", "Total"],
            [
                _row("Sales",  [None, 100, 150, 200, 999], kind=RowKind.DATA, idx=0),  # 999 ≠ 450
                _row("Other",  [None, 50,  60,  70,  180], kind=RowKind.DATA, idx=1),
                _row("GTotal", [None, 150, 210, 270, 630], kind=RowKind.TOTAL, idx=2),
            ])
        errors = check_casting(tbl)
        row_errors = [e for e in errors if e.axis == "row"]
        assert len(row_errors) == 1
        assert row_errors[0].label == "Sales"


# ── Tolerance ─────────────────────────────────────────────────────────────────

class TestTolerance:
    def test_within_tolerance_passes(self):
        tbl = _table("T", ["Amount"],
            [
                _row("A", ["100.004"], kind=RowKind.DATA,  idx=0),
                _row("B", ["200.003"], kind=RowKind.DATA,  idx=1),
                _row("C", ["300.01"],  kind=RowKind.TOTAL, idx=2),  # actual 300.007
            ])
        errors = check_casting(tbl, abs_tolerance=Decimal("0.01"))
        assert errors == ()

    def test_beyond_tolerance_fails(self):
        tbl = _table("T", ["Amount"],
            [
                _row("A", [100], kind=RowKind.DATA,  idx=0),
                _row("B", [200], kind=RowKind.DATA,  idx=1),
                _row("C", [301], kind=RowKind.TOTAL, idx=2),
            ])
        errors = check_casting(tbl, abs_tolerance=Decimal("0.01"))
        assert len(errors) == 1


# ── Negative numbers ──────────────────────────────────────────────────────────

class TestNegativeValues:
    def test_negative_data_rows(self):
        tbl = _table("CF", ["Amount"],
            [
                _row("Operating", [5000],  kind=RowKind.DATA,  idx=0),
                _row("Investing", [-2000], kind=RowKind.DATA,  idx=1),
                _row("Net CF",    [3000],  kind=RowKind.TOTAL, idx=2),
            ])
        errors = check_casting(tbl)
        assert errors == ()
