"""
Formula interpretation: signs, net positions, roll-forwards, derived columns
and how sure the engine is of each formula.

Every table here is built from labels and figures only — the engine is never
told the formula.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from decimal import Decimal

from domain.casting import check_casting
from domain.cross_casting import check_cross_casting
from domain.findings import consolidate_findings
from domain.labels import (
    classify_label,
    is_deduction_label,
    is_movement_header,
    is_net_position_label,
)
from domain.models import Cell, FinancialTable, RowKind, TableRow
from domain.structure import analyze_structure

D, T, S = RowKind.DATA, RowKind.TOTAL, RowKind.SUBTOTAL


def _table(headers, spec, title="T", sheet=None):
    rows = []
    for i, item in enumerate(spec):
        label, values = item[0], item[1]
        kind = item[2] if len(item) > 2 else D
        cells = tuple(
            Cell(value=None if v is None else Decimal(str(v)), raw_text="" if v is None else str(v),
                 row_index=i, col_index=j)
            for j, v in enumerate(values)
        )
        rows.append(TableRow(index=i, label=label, cells=cells, kind=kind))
    return FinancialTable(title=title, headers=tuple(headers), rows=tuple(rows), sheet=sheet)


def _run(table):
    s = analyze_structure(table)
    c, x = consolidate_findings(
        check_casting(table, structure=s), check_cross_casting(table, structure=s), s,
    )
    return s, c, x


def _found(c):
    return [(e.row_label, e.label, e.computed, e.delta) for e in c]


# ── Net working capital: Inventory + Receivables − Payables ──────────────────

class TestNetWorkingCapital:
    HEADERS = ["Q1", "Q2", "Q3", "Q4"]

    def test_payables_presented_positive_are_deducted(self):
        tbl = _table(self.HEADERS, [
            ("Inventory",           [420, 450, 480, 510]),
            ("Receivables",         [310, 340, 360, 395]),
            ("Payables",            [250, 270, 285, 305]),
            ("Net working capital", [480, 520, 555, 600], T),
        ])
        s, c, x = _run(tbl)
        assert c == () and x == ()
        rel = s.relations[0]
        assert rel.formula == "Net working capital = Inventory + Receivables − Payables"
        assert rel.basis == "arithmetic" and len(rel.matched_columns) == 4

    def test_error_in_one_quarter_is_reported_with_correct_formula(self):
        tbl = _table(self.HEADERS, [
            ("Inventory",           [420, 450, 480, 510]),
            ("Receivables",         [310, 340, 360, 395]),
            ("Payables",            [250, 270, 285, 305]),
            ("Net working capital", [480, 520, 565, 600], T),     # Q3 should be 555
        ])
        _, c, _ = _run(tbl)
        assert _found(c) == [("Net working capital", "Q3", Decimal("555.00"), Decimal("10.00"))]
        assert c[0].relation == "Net working capital = Inventory + Receivables − Payables"

    def test_payables_presented_negative_are_added_as_presented(self):
        tbl = _table(self.HEADERS, [
            ("Inventory",           [420, 450, 480, 510]),
            ("Receivables",         [310, 340, 360, 395]),
            ("Trade payables",      [-250, -270, -285, -305]),
            ("Net working capital", [480, 520, 555, 600], T),
        ])
        s, c, _ = _run(tbl)
        assert c == ()
        assert s.relations[0].mode == "sum"

    def test_total_liabilities_still_adds_liabilities(self):
        tbl = _table(["2024", "2025"], [
            ("Trade payables",      [250, 270]),
            ("Contract liabilities", [100, 120]),
            ("Total current liabilities", [350, 390], T),
        ])
        s, c, _ = _run(tbl)
        assert c == ()
        assert s.relations[0].formula == (
            "Total current liabilities = Trade payables + Contract liabilities"
        )


# ── Net contract position, Movement column, negative movement values ─────────

class TestContractBalances:
    def _tbl(self, net=(-530, -550, -20), movement_ca=220):
        return _table(["2024", "2025", "Movement"], [
            ("Contract assets",       [620, 840, movement_ca]),
            ("Contract liabilities",  [1150, 1390, 240]),
            ("Net contract position", list(net), T),
        ])

    def test_contract_assets_minus_liabilities(self):
        s, c, x = _run(self._tbl())
        assert c == () and x == ()
        assert s.relations[0].formula == (
            "Net contract position = Contract assets − Contract liabilities"
        )

    def test_movement_column_is_later_minus_earlier(self):
        s, _, _ = _run(self._tbl())
        assert s.horizontal_formula() == "Movement = 2025 − 2024"

    def test_negative_movement_on_net_position_is_checked_both_ways(self):
        # declared movement −20 = (−550) − (−530) and = 220 − 240
        s, c, x = _run(self._tbl())
        assert c == () and x == ()
        assert check_cross_casting(self._tbl(), structure=s) == ()

    def test_wrong_net_position_is_reported_once(self):
        _, c, x = _run(self._tbl(net=(-530, -560, -30)))     # 2025 should be −550
        assert _found(c) == [
            ("Net contract position", "2025", Decimal("-550.00"), Decimal("-10.00")),
        ]
        assert x == ()                     # the Movement-column consequence is merged
        assert any("Movement" in e for e in c[0].evidence)

    def test_wrong_movement_is_reported_on_the_row(self):
        _, c, _ = _run(self._tbl(movement_ca=230))            # should be 220
        assert [(e.axis, e.row_label, e.computed) for e in c] == [
            ("row", "Contract assets", Decimal("220.00")),
        ]
        assert c[0].relation == "Contract assets: Movement = 2025 − 2024"


# ── Roll-forward with negative movements ─────────────────────────────────────

class TestRollForward:
    def test_one_column_roll_forward_with_parenthesised_movements(self):
        tbl = _table(["FY2025"], [
            ("Opening net book value", [2450000]),
            ("Additions",              [680000]),
            ("Disposals",              [-125000]),
            ("Depreciation charge",    [-365000]),
            ("Closing net book value", [2700000], T),      # should be 2,640,000
        ])
        s, c, _ = _run(tbl)
        assert _found(c) == [
            ("Closing net book value", "FY2025", Decimal("2640000.00"), Decimal("60000.00")),
        ]
        assert s.relations[0].basis == "semantic"
        assert "roll-forward" in c[0].evidence[0]

    def test_one_column_roll_forward_with_positive_deductions(self):
        tbl = _table(["FY2025"], [
            ("Opening balance",     [2450000]),
            ("Additions",           [680000]),
            ("Disposals",           [125000]),
            ("Depreciation charge", [365000]),
            ("Closing balance",     [2700000], T),
        ])
        s, c, _ = _run(tbl)
        assert _found(c) == [
            ("Closing balance", "FY2025", Decimal("2640000.00"), Decimal("60000.00")),
        ]
        assert s.relations[0].formula == (
            "Closing balance = Opening balance + Additions − Disposals − Depreciation charge"
        )

    def test_inconsistent_presentation_is_uncertain_not_an_error(self):
        tbl = _table(["FY2025"], [
            ("Opening balance",     [100]),
            ("Disposals",           [-10]),
            ("Depreciation charge", [20]),          # one negative, one positive deduction
            ("Closing balance",     [55], T),
        ])
        s, c, _ = _run(tbl)
        assert c == ()
        assert s.relations[0].basis == "uncertain"
        assert any("Could not determine reliably" in w and "Closing balance" in w
                   for w in s.warnings)


# ── Revenue, expenses, gross profit, profit ──────────────────────────────────

class TestIncomeStatement:
    def test_profit_lines_use_declared_previous_result(self):
        tbl = _table(["FY2024", "FY2025"], [
            ("Revenue",                [12500, 14800]),
            ("Cost of sales",          [-7600, -9050]),
            ("Gross profit",           [4900, 5650], T),       # FY2025 should be 5,750
            ("Other operating income", [180, 225]),
            ("Selling expenses",       [-820, -960]),
            ("R&D",                    [-310, -390]),
            ("Operating profit",       [3950, 4525], T),       # uses declared 5,650
        ])
        s, c, _ = _run(tbl)
        assert _found(c) == [("Gross profit", "FY2025", Decimal("5750.00"), Decimal("-100.00"))]
        assert s.relations[1].formula == (
            "Operating profit = Gross profit + Other operating income + Selling expenses + R&D"
        )

    def test_other_operating_income_is_a_detail_line(self):
        assert classify_label("Other operating income") == "data"
        assert classify_label("Operating income") == "total"
        assert classify_label("Other comprehensive income for the year") == "total"

    def test_costs_presented_positive_are_subtracted(self):
        tbl = _table(["2024", "2025"], [
            ("Revenue",       [1000, 1200]),
            ("Cost of sales", [600, 700]),
            ("Gross profit",  [400, 500], T),
        ])
        s, c, _ = _run(tbl)
        assert c == ()
        assert s.relations[0].formula == "Gross profit = Revenue − Cost of sales"

    def test_cash_generated_from_operations_is_a_subtotal(self):
        assert classify_label("Cash generated from operations") == "total"
        tbl = _table(["FY2024", "FY2025"], [
            ("Profit before tax",              [2610, 2947], T),
            ("Depreciation and amortisation",  [520, 610]),
            ("Finance income",                 [-75, -92]),
            ("Increase in payables",           [95, 180]),
            ("Cash generated from operations", [3145, 3645], T),   # FY2024 should be 3,150
        ])
        _, c, _ = _run(tbl)
        assert _found(c) == [
            ("Cash generated from operations", "FY2024", Decimal("3150.00"), Decimal("-5.00")),
        ]


# ── Subtotals, grand totals, duplicate component selection ───────────────────

class TestHierarchy:
    def test_grand_total_adds_subtotals_not_their_components_again(self):
        tbl = _table(["2024", "2025"], [
            ("PPE",                      [4200, 4850]),
            ("Goodwill",                 [900, 900]),
            ("Total non-current assets", [5100, 5750], S),
            ("Inventories",              [1450, 1680]),
            ("Cash",                     [940, 1120]),
            ("Total current assets",     [2390, 2800], S),
            ("Total assets",             [7490, 8550], T),
        ])
        s, c, _ = _run(tbl)
        assert c == ()
        assert s.relation_for(6).formula == (
            "Total assets = Total non-current assets + Total current assets"
        )
        used = [comp.row.index for rel in s.relations for comp in rel.components]
        assert len(used) == len(set(used)), "a row was used in two totals"

    def test_wrong_subtotal_does_not_also_flag_the_grand_total(self):
        tbl = _table(["2024", "2025"], [
            ("PPE",                      [4200, 4850]),
            ("Goodwill",                 [900, 900]),
            ("Total non-current assets", [5100, 5760], S),    # 2025 should be 5,750
            ("Cash",                     [940, 1120]),
            ("Total current assets",     [940, 1120], S),
            ("Total assets",             [6040, 6880], T),    # agrees with the declared subtotal
        ])
        _, c, _ = _run(tbl)
        assert _found(c) == [
            ("Total non-current assets", "2025", Decimal("5750.00"), Decimal("10.00")),
        ]

    def test_unlabelled_subtotal_is_recognised_from_every_column(self):
        tbl = _table(["NA", "EU", "Total"], [
            ("Subscriptions",              [4250, 3180, 7430]),
            ("Services",                   [980, 720, 1700]),
            ("Gross revenue",              [5230, 3900, 9130]),
            ("Inter-segment eliminations", [-120, -80, -200]),
            ("External revenue",           [5110, 3820, 8930]),
            ("Prior year external revenue", [4900, 3600, 8500]),
        ])
        s, c, x = _run(tbl)
        assert c == () and x == ()
        assert [r.formula for r in s.relations] == [
            "Gross revenue = Subscriptions + Services",
            "External revenue = Gross revenue + Inter-segment eliminations",
        ]
        assert any("Prior year external revenue" in w for w in s.warnings)

    def test_accounting_identity_is_not_mistaken_for_a_subtotal(self):
        # Capital = Total assets − Total liabilities in every column, but it is a
        # detail line; inferring that would hide the real total below.
        tbl = _table(["FY2024", "FY2025"], [
            ("Cash", [100, 120]), ("Total assets", [100, 120], T),
            ("Loans", [40, 50]), ("Total liabilities", [40, 50], T),
            ("Capital", [60, 70]), ("Total equity", [60, 70], T),
            ("Total liabilities and equity", [100, 125], T),
        ])
        _, c, _ = _run(tbl)
        assert _found(c) == [
            ("Total liabilities and equity", "FY2025", Decimal("120.00"), Decimal("5.00")),
        ]


# ── Uncertain formulas: warned, never reported as errors ─────────────────────

class TestUncertainty:
    def test_net_line_without_recognisable_deductions_is_uncertain(self):
        tbl = _table(["2024", "2025"], [
            ("Alpha", [620, 840]),
            ("Beta",  [1150, 1390]),
            ("Net position", [-530, -550], T),
        ])
        s, c, _ = _run(tbl)
        assert c == ()
        assert s.relations[0].basis == "uncertain"
        w = next(w for w in s.warnings if "Net position" in w)
        assert "Not reported as a casting error" in w and "declared -530.00" in w

    def test_uncertain_row_does_not_absorb_rows_needed_by_the_next_total(self):
        tbl = _table(["2024", "2025"], [
            ("Gross profit",     [4900, 5650], T),
            ("Odd line",         [180, 225], S),      # reader says subtotal; nothing fits
            ("Selling expenses", [-820, -960]),
            ("Operating profit", [4260, 4915], T),
        ])
        s, c, _ = _run(tbl)
        assert c == ()
        assert s.relation_for(3).formula == (
            "Operating profit = Gross profit + Odd line + Selling expenses"
        )


# ── Debit / credit tables ────────────────────────────────────────────────────

class TestLedger:
    def _tb(self, credit_cash=0, title="ADJUSTED TRIAL BALANCE"):
        return _table(["Debit", "Credit"], [
            ("Cash",            [1120, credit_cash]),
            ("Revenue",         [0, 1500]),
            ("Cost of sales",   [380, 0]),
            ("Suspense account", [0, 0]),
        ], title=title)

    def test_balanced_trial_balance(self):
        s, c, _ = _run(self._tb())
        assert c == ()
        assert s.ledger.debit_total == s.ledger.credit_total == Decimal("1500")

    def test_unbalanced_trial_balance_is_a_finding(self):
        _, c, _ = _run(self._tb(credit_cash=85))
        assert [(e.declared, e.computed, e.delta) for e in c] == [
            (Decimal("1585"), Decimal("1500.00"), Decimal("85.00")),
        ]
        assert c[0].relation == "Σ Credit = Σ Debit (a trial balance must balance)"

    def test_other_debit_credit_schedules_only_warn(self):
        s, c, _ = _run(self._tb(credit_cash=85, title="Mapping summary"))
        assert c == ()
        assert any("credits exceed debits" in w for w in s.warnings)

    def test_net_balance_column_is_debit_minus_credit(self):
        tbl = _table(["Debit", "Credit", "Net balance"], [
            ("Cash",           [1120, 0, 1120]),
            ("PPE",            [6200, 1350, 4850]),
            ("Trade payables", [0, 1100, -1100]),
        ])
        s, c, _ = _run(tbl)
        assert c == ()
        assert s.horizontal_formula() == "Net balance = Debit − Credit"

    def test_non_zero_suspense_balance_is_flagged(self):
        tbl = _table(["Debit", "Credit"], [
            ("Cash", [100, 0]), ("Suspense account", [0, 85]),
        ], title="Trial balance")
        s, _, _ = _run(tbl)
        assert any("Suspense / unmapped balance: 'Suspense account'" in w for w in s.warnings)


# ── Label hints ──────────────────────────────────────────────────────────────

class TestLabelHints:
    def test_deductions(self):
        for label in ("Payables", "Contract liabilities", "Disposals", "Cost of sales",
                      "Borrowings repaid", "ذمم دائنة"):
            assert is_deduction_label(label), label
        for label in ("Inventory", "Contract assets", "Additions", "Gross profit",
                      "Net cash used in investing"):
            assert not is_deduction_label(label), label

    def test_net_positions_and_movement_headers(self):
        assert is_net_position_label("Net working capital")
        assert is_net_position_label("Net contract position")
        assert not is_net_position_label("Total assets")
        assert is_movement_header("Movement") and is_movement_header("Net balance")
        assert not is_movement_header("2025")
