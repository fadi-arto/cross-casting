"""Regression tests for table structure recognition and hierarchical casting (domain only)."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from decimal import Decimal

from domain.casting import check_casting
from domain.cross_casting import check_cross_casting
from domain.findings import consolidate_findings
from domain.labels import classify_label, is_expense_label, is_profit_type
from domain.models import Cell, ColumnInfo, FinancialTable, RowKind, TableRow
from domain.normalize import normalize_arabic
from domain.structure import analyze_structure

D, T, S = RowKind.DATA, RowKind.TOTAL, RowKind.SUBTOTAL


def _row(idx, label, values, kind=D, evidence="label"):
    cells = tuple(
        Cell(
            value=Decimal(str(v)) if v is not None else None,
            raw_text="" if v is None else str(v),
            row_index=idx, col_index=i,
        )
        for i, v in enumerate(values)
    )
    return TableRow(index=idx, label=label, cells=cells, kind=kind, kind_evidence=evidence)


def _table(headers, spec, title="T"):
    rows = tuple(_row(i, *r) for i, r in enumerate(spec))
    return FinancialTable(title=title, headers=tuple(headers), rows=rows)


def _run(table):
    s = analyze_structure(table)
    c, x = consolidate_findings(check_casting(table, structure=s), check_cross_casting(table, structure=s), s)
    return s, c, x


# ── Detail vs subtotal vs total ───────────────────────────────────────────────

class TestDetailAndSubtotalRows:
    def test_total_not_added_into_the_next_block(self):
        """The old engine summed every data row and compared it with every total row."""
        tbl = _table(["2025"], [
            ("Cash",              [100]),
            ("Inventory",         [200]),
            ("Total assets",      [300], T),
            ("Payables",          [50]),
            ("Total liabilities", [50],  T),
        ])
        s, c, x = _run(tbl)
        assert c == () and x == ()
        assert [r.formula for r in s.relations] == [
            "Total assets = Cash + Inventory",
            "Total liabilities = Payables",
        ]

    def test_subtotal_rows_are_not_double_counted(self):
        tbl = _table(["2025"], [
            ("Cash",                [100]),
            ("Receivables",         [200]),
            ("Current assets",      [300], S),
            ("PPE",                 [700]),
            ("Total assets",        [1000], T),   # = 300 + 700, not 100+200+300+700
        ])
        s, c, _ = _run(tbl)
        assert c == ()
        assert s.relations[-1].formula == "Total assets = Current assets + PPE"


class TestNestedTotals:
    TABLE = [
        ("Cash",                      [100, 90]),
        ("Receivables",               [200, 180]),
        ("Total current assets",      [300, 270], T),
        ("PPE",                       [500, 450]),
        ("Intangibles",               [200, 150]),
        ("Total non-current assets",  [700, 600], T),
        ("Total assets",              [1000, 870], T),
        ("Payables",                  [400, 370]),
        ("Total liabilities",         [400, 370], T),
        ("Share capital",             [500, 500]),
        ("Retained earnings",         [100, 0]),
        ("Total equity",              [600, 500], T),
        ("Total liabilities and equity", [1000, 870], T),
    ]

    def test_clean_nested_balance_sheet(self):
        s, c, x = _run(_table(["2025", "2024"], self.TABLE))
        assert c == () and x == ()
        f = {r.row.label: r.formula for r in s.relations}
        assert f["Total assets"] == "Total assets = Total current assets + Total non-current assets"
        assert f["Total liabilities and equity"] == (
            "Total liabilities and equity = Total liabilities + Total equity"
        )
        assert s.warnings == ()

    def test_error_in_nested_subtotal_is_reported_once(self):
        rows = list(self.TABLE)
        rows[2] = ("Total current assets", [300, 280], T)   # 2024 should be 270
        s = analyze_structure(_table(["2025", "2024"], rows))
        raw = check_casting(_table(["2025", "2024"], rows), structure=s)
        # Raw checks: the subtotal is wrong AND Total assets (870 ≠ 280 + 600) fails
        assert len(raw) == 2
        _, c, _ = _run(_table(["2025", "2024"], rows))
        # Total assets reconciles once the subtotal is corrected → one root finding
        assert [(e.row_label, e.label, e.delta) for e in c] == [
            ("Total current assets", "2024", Decimal("10.00")),
        ]
        assert any("'Total assets' reconciles once" in ev for ev in c[0].evidence)

    def test_wrong_parent_with_correct_subtotals_is_its_own_finding(self):
        rows = list(self.TABLE)
        rows[6] = ("Total assets", [1000, 880], T)          # should be 870
        _, c, _ = _run(_table(["2025", "2024"], rows))
        assert [(e.row_label, e.label, e.delta) for e in c] == [
            ("Total assets", "2024", Decimal("10.00")),
        ]


# ── Statements ────────────────────────────────────────────────────────────────

class TestBalanceSheetValidation:
    def test_total_liabilities_and_equity_is_sum_of_the_two_totals(self):
        tbl = _table(["FY2024", "FY2025"], [
            ("Cash",                [100, 120]),
            ("Total assets",        [100, 120], T),
            ("Loans",               [40, 50]),
            ("Total liabilities",   [40, 50], T),
            ("Capital",             [60, 70]),
            ("Total equity",        [60, 70], T),
            ("Total liabilities and equity", [100, 125], T),   # FY2025 should be 120
        ])
        s, c, _ = _run(tbl)
        assert [(e.row_label, e.label, e.declared, e.computed) for e in c] == [
            ("Total liabilities and equity", "FY2025", Decimal("125"), Decimal("120.00")),
        ]
        assert s.relations[-1].formula == (
            "Total liabilities and equity = Total liabilities + Total equity"
        )


class TestIncomeStatementValidation:
    def test_costs_shown_positive_are_deducted(self):
        tbl = _table(["2024", "2025"], [
            ("Revenue",              [1000, 1200]),
            ("Cost of sales",        [600, 700]),
            ("Gross profit",         [400, 500], T),
            ("Administrative expenses", [100, 120]),
            ("Operating profit",     [300, 380], T),
            ("Income tax expense",   [60, 76]),
            ("Profit for the year",  [240, 304], T),
        ])
        s, c, x = _run(tbl)
        assert c == () and x == ()
        assert s.relations[0].formula == "Gross profit = Revenue − Cost of sales"
        assert s.relations[1].formula == "Operating profit = Gross profit − Administrative expenses"

    def test_costs_shown_negative_are_added(self):
        tbl = _table(["2025"], [
            ("Revenue",       [1000]),
            ("Cost of sales", [-600]),
            ("Gross profit",  [400], T),
        ])
        s, c, _ = _run(tbl)
        assert c == ()
        assert s.relations[0].mode == "sum"

    def test_error_does_not_cascade_into_following_lines(self):
        """Running lines use the declared previous result, so one wrong line → one finding."""
        tbl = _table(["2024", "2025"], [
            ("Revenue",            [1000, 1200]),
            ("Cost of sales",      [600, 700]),
            ("Gross profit",       [400, 510], T),     # 2025 should be 500
            ("Operating expenses", [100, 110]),
            ("Operating profit",   [300, 400], T),     # = declared 510 − 110 ✓
        ])
        _, c, _ = _run(tbl)
        assert [(e.row_label, e.label, e.delta) for e in c] == [
            ("Gross profit", "2025", Decimal("10.00")),
        ]


class TestMultiplePeriods:
    def test_each_period_checked_independently_with_its_header(self):
        tbl = _table(["Q1", "Q2", "Q3", "Q4"], [
            ("Sales",   [10, 20, 30, 40]),
            ("Service", [1, 2, 3, 4]),
            ("Total",   [11, 22, 33, 45], T),       # Q4 wrong
        ])
        _, c, _ = _run(tbl)
        assert [(e.label, e.index, e.delta) for e in c] == [("Q4", 3, Decimal("1.00"))]


# ── Columns ───────────────────────────────────────────────────────────────────

class TestColumnLabels:
    def test_label_column_header_is_not_shifted_onto_amounts(self):
        """VLM-style headers include the label column: ["Account", "2024", "2025"]."""
        tbl = _table(["Account", "2024", "2025"], [
            ("A",     [1, 2]),
            ("B",     [1, 2]),
            ("Total", [2, 5], T),
        ])
        _, c, _ = _run(tbl)
        assert [e.label for e in c] == ["2025"]

    def test_missing_headers_produce_a_warning_not_an_invented_label(self):
        tbl = _table([], [
            ("A",     [1, 2]),
            ("Total", [1, 3], T),
        ])
        s, c, _ = _run(tbl)
        assert [e.label for e in c] == ["[unidentified column 2]"]
        assert any("no identifiable header" in w for w in s.warnings)

    def test_unalignable_headers_warn(self):
        tbl = _table(["Report title"], [
            ("A",     [1, 2, 3]),
            ("Total", [1, 2, 3], T),
        ])
        s, _, _ = _run(tbl)
        assert any("could not be matched" in w for w in s.warnings)
        assert all(not c.identified for c in s.columns)

    def test_note_column_excluded(self):
        tbl = FinancialTable(
            title="BS", headers=("Note", "2025"),
            columns=(ColumnInfo(0, "Note", role="note"), ColumnInfo(1, "2025")),
            rows=(
                _row(0, "Cash", [5, 100]),
                _row(1, "Inventory", [6, 200]),
                _row(2, "Total assets", [None, 300], T),
            ),
        )
        _, c, _ = _run(tbl)
        assert c == ()


# ── Findings ──────────────────────────────────────────────────────────────────

class TestDuplicateFindings:
    def test_total_column_consequence_merged(self):
        tbl = _table(["", "2024", "2025", "Total"], [
            ("A",     [None, 10, 20, 30]),
            ("B",     [None, 5, 5, 10]),
            ("Total", [None, 15, 30, 45], T),    # 2025 should be 25 → Total col 45 vs 40
        ])
        s = analyze_structure(tbl)
        raw = check_casting(tbl, structure=s)
        raw_x = check_cross_casting(tbl, structure=s)
        assert len(raw) == 2 and len(raw_x) == 2          # without consolidation
        c, x = consolidate_findings(raw, raw_x, s)
        assert x == ()
        assert [(e.label, e.delta) for e in c] == [("2025", Decimal("5.00"))]
        # formula basis + the three merged consequences
        assert c[0].evidence[0].startswith("Formula basis: formula confirmed by the figures")
        assert len(c[0].evidence) == 4

    def test_wrong_row_total_reported_once(self):
        tbl = _table(["", "2024", "2025", "Total"], [
            ("A",     [None, 10, 20, 31]),            # row total should be 30
            ("B",     [None, 5, 5, 10]),
            ("Total", [None, 15, 25, 40], T),
        ])
        _, c, x = _run(tbl)
        assert x == ()
        assert [(e.axis, e.label, e.delta) for e in c] == [("row", "A", Decimal("1.00"))]
        assert c[0].evidence

    def test_independent_errors_are_all_kept(self):
        tbl = _table(["", "2024", "2025", "Total"], [
            ("A",     [None, 10, 20, 30]),
            ("B",     [None, 5, 5, 10]),
            ("Total", [None, 16, 26, 40], T),    # both periods wrong, Total col right
        ])
        _, c, x = _run(tbl)
        col_errs = [(e.axis, e.label) for e in c]
        assert ("column", "2024") in col_errs and ("column", "2025") in col_errs
        assert ("row", "Total") in col_errs        # 16 + 26 ≠ 40: a real, separate issue


class TestNegativeAndPrecision:
    def test_negative_amounts(self):
        tbl = _table(["2025"], [
            ("Operating cash flow", [5000]),
            ("Investing cash flow", [-7000]),
            ("Net cash flow",       [-2000], T),
        ])
        _, c, _ = _run(tbl)
        assert c == ()

    def test_decimal_precision_no_float_drift(self):
        tbl = _table(["2025"], [
            ("A", ["0.1"]), ("B", ["0.2"]), ("Total", ["0.3"], T),
        ])
        _, c, _ = _run(tbl)
        assert c == ()

    def test_sub_cent_differences_within_tolerance_but_cent_reported(self):
        ok = _table(["2025"], [("A", ["100.004"]), ("B", ["200.003"]), ("Total", ["300.01"], T)])
        bad = _table(["2025"], [("A", ["100.00"]), ("B", ["200.00"]), ("Total", ["300.02"], T)])
        assert _run(ok)[1] == ()
        (e,) = _run(bad)[1]
        assert e.delta == Decimal("0.02") and isinstance(e.computed, Decimal)


class TestNoTotalRows:
    def test_no_vertical_check_and_warning(self):
        tbl = _table(["2024", "2025"], [("Headcount", [10, 12]), ("Branches", [3, 4])])
        s, c, x = _run(tbl)
        assert c == () and x == () and s.relations == ()
        assert any("No total or subtotal rows" in w for w in s.warnings)

    def test_horizontal_still_checked_without_total_rows(self):
        tbl = _table(["", "2024", "2025", "Total"], [
            ("A", [None, 1, 2, 3]),
            ("B", [None, 1, 2, 4]),
        ])
        _, c, _ = _run(tbl)
        assert [(e.axis, e.label) for e in c] == [("row", "B")]


class TestAmbiguity:
    def test_unreconcilable_block_total_is_reported_with_structural_basis(self):
        # one column, no reconciliation possible — but a total over plain detail
        # rows has only one reading, so the error is still reported, with its basis
        tbl = _table(["2025"], [("A", [1]), ("B", [2]), ("Total", [10], T)])
        s, c, _ = _run(tbl)
        assert len(c) == 1 and c[0].computed == Decimal("3.00")
        assert s.relations[0].basis == "semantic"
        assert "block total" in c[0].evidence[0]

    def test_bold_row_that_does_not_reconcile_becomes_detail_with_warning(self):
        tbl = _table(["2025"], [
            ("A", [1]), ("Highlighted item", [50], S, "bold formatting"), ("B", [2]),
            ("Total", [53], T),
        ])
        s, c, _ = _run(tbl)
        assert c == ()
        assert any("formatted like a subtotal" in w for w in s.warnings)

    def test_rows_outside_any_total_are_reported_not_dropped(self):
        tbl = _table(["2025"], [
            ("A", [1]), ("B", [2]), ("Total", [3], T), ("Earnings per share", [0.5]),
        ])
        s, _, _ = _run(tbl)
        assert any("Earnings per share" in w and "Not included" in w for w in s.warnings)


# ── Labels ────────────────────────────────────────────────────────────────────

class TestLabels:
    def test_arabic_normalisation_keeps_letters(self):
        assert normalize_arabic("الإجماليُّ") == "الاجمالي"

    def test_classification(self):
        assert classify_label("Total assets") == "total"
        assert classify_label("الإجمالي") == "total"
        assert classify_label("إجمالي الأصول") == "total"
        assert classify_label("Gross profit") == "total"
        assert classify_label("صافي الربح") == "total"
        assert classify_label("Subtotal") == "subtotal"
        assert classify_label("Opening balance") == "data"
        # substrings that used to match "sum" / "net"
        assert classify_label("Consumables") == "data"
        assert classify_label("Cabinet") == "data"
        assert classify_label("Internet expenses") == "data"

    def test_signs(self):
        assert is_expense_label("Cost of sales") and is_expense_label("Income tax expense")
        assert is_expense_label("تكلفة المبيعات") and not is_expense_label("Revenue")
        assert is_profit_type("Profit before tax") and not is_profit_type("Retained earnings")
