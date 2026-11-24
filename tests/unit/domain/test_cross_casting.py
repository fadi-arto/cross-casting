"""Unit tests for the cross-casting engine."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from decimal import Decimal

import pytest

from domain.cross_casting import check_cross_casting
from domain.models import Cell, FinancialTable, RowKind, TableRow


def _cell(value, row=0, col=0):
    v = Decimal(str(value)) if value is not None else None
    return Cell(value=v, raw_text=str(value) if value is not None else "", row_index=row, col_index=col)


def _row(label, values, kind=RowKind.DATA, idx=0):
    cells = tuple(_cell(v, idx, ci) for ci, v in enumerate(values))
    return TableRow(index=idx, label=label, cells=cells, kind=kind)


def _table(rows):
    return FinancialTable(
        title="Test",
        headers=("", "Q1", "Q2", "Q3", "Total"),
        rows=tuple(rows),
    )


class TestCleanCrossCasting:
    def test_all_match(self):
        """
        Sales  100 150 200 | 450
        Other   50  60  70 | 180
        ────────────────────────
        Total  150 210 270 | 630
        """
        tbl = _table([
            _row("Sales",  [None, 100, 150, 200, 450], kind=RowKind.DATA,  idx=0),
            _row("Other",  [None, 50,  60,  70,  180], kind=RowKind.DATA,  idx=1),
            _row("Total",  [None, 150, 210, 270, 630], kind=RowKind.TOTAL, idx=2),
        ])
        errors = check_cross_casting(tbl)
        assert errors == ()

    def test_negative_values(self):
        tbl = _table([
            _row("Income",    [None,  500,  600,  700, 1800], kind=RowKind.DATA,  idx=0),
            _row("Expense",   [None, -200, -300, -400, -900], kind=RowKind.DATA,  idx=1),
            _row("Net",       [None,  300,  300,  300,  900], kind=RowKind.TOTAL, idx=2),
        ])
        errors = check_cross_casting(tbl)
        assert errors == ()


class TestDirtyCrossCasting:
    def test_row_total_ne_col_total(self):
        """Grand total cell has wrong value."""
        tbl = _table([
            _row("Sales",  [None, 100, 150, 200, 450], kind=RowKind.DATA,  idx=0),
            _row("Other",  [None, 50,  60,  70,  180], kind=RowKind.DATA,  idx=1),
            _row("Total",  [None, 150, 210, 270, 999], kind=RowKind.TOTAL, idx=2),  # 999 ≠ 630
        ])
        errors = check_cross_casting(tbl)
        assert len(errors) >= 1
        assert any(e.grand_total == Decimal("999") for e in errors)

    def test_row_total_ne_sum_of_row_totals(self):
        """One row total is wrong, so sum-of-row-totals ≠ sum-of-col-totals."""
        tbl = _table([
            _row("Sales",  [None, 100, 150, 200, 999], kind=RowKind.DATA,  idx=0),  # 999 ≠ 450
            _row("Other",  [None, 50,  60,  70,  180], kind=RowKind.DATA,  idx=1),
            _row("Total",  [None, 150, 210, 270, 630], kind=RowKind.TOTAL, idx=2),
        ])
        errors = check_cross_casting(tbl)
        # sum_row_totals = 999+180 = 1179; sum_col_totals = 150+210+270 = 630
        assert any(e.row_total == Decimal("1179") for e in errors)

    def test_delta_direction(self):
        tbl = _table([
            _row("A", [None, 100, 100, 100, 300], kind=RowKind.DATA,  idx=0),
            _row("B", [None, 100, 100, 100, 300], kind=RowKind.DATA,  idx=1),
            _row("T", [None, 200, 200, 200, 601], kind=RowKind.TOTAL, idx=2),  # 601 ≠ 600
        ])
        errors = check_cross_casting(tbl)
        assert any(abs(e.delta) >= Decimal("1") for e in errors)


class TestEdgeCases:
    def test_too_few_columns(self):
        tbl = FinancialTable(
            title="Simple",
            headers=("Label", "Amount"),
            rows=(
                _row("Revenue", [1000], kind=RowKind.DATA,  idx=0),
                _row("Total",   [1000], kind=RowKind.TOTAL, idx=1),
            ),
        )
        errors = check_cross_casting(tbl)
        assert errors == ()  # single-column table: no cross-cast

    def test_no_total_row(self):
        tbl = _table([
            _row("Sales",  [None, 100, 150, 200, 450], kind=RowKind.DATA, idx=0),
            _row("Other",  [None, 50,  60,  70,  180], kind=RowKind.DATA, idx=1),
        ])
        errors = check_cross_casting(tbl)
        assert errors == ()
