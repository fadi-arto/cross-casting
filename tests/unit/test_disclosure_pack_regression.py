"""
Regression: the five-file disclosure test pack (tests/fixtures/disclosure_pack).

  01 Northstar  — two intentional income-statement errors; nothing else invented.
  02 Riverbend  — trial-balance balance and suspense account, checked separately.
  03 Orbital    — clean control note: zero findings.
  04 Cedar      — clean control workbook: zero findings.
  05 Meridian   — PDF: both intentional errors kept (replays the recorded Claude
                  extraction in 05_Meridian_vlm_extraction.json, so no API call).

The expected figures below come from the workbooks / answer key; the engine is
never told them.  Also checks that a table read from a PDF (VLM) and the same
table read from Excel produce the same formulas and findings.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import io
import json
from decimal import Decimal
from pathlib import Path

import openpyxl
import pytest

from adapters.excel_reader import read_excel
from adapters.vlm_openai import _parse_tables
from application.page_merge import merge_page_continuations
from application.pipeline import CrossCastingPipeline
from domain.structure import analyze_structure

PACK = Path(__file__).resolve().parents[1] / "fixtures" / "disclosure_pack"
MERIDIAN_VLM = PACK / "05_Meridian_vlm_extraction.json"


def _excel(name):
    return read_excel((PACK / name).read_bytes())


def _meridian_tables():
    rec = json.loads(MERIDIAN_VLM.read_text())
    tables = []
    for p in rec["pages"]:
        tables.extend(_parse_tables(json.dumps(p["payload"]), p["page"]))
    return merge_page_continuations(tables)


def _report(tables, name="t"):
    return CrossCastingPipeline(None, None, None, None).process_tables(list(tables), name)


def _by_table(report):
    return {(r.table.sheet or r.table.title): r for r in report.validation_results}


def _found(result):
    return sorted(
        (e.row_label, e.label, e.declared, e.computed, e.delta) for e in result.casting_errors
    )


def _d(x):
    return Decimal(str(x))


def _uncertain(result):
    return [w for w in result.warnings if "Could not determine reliably" in w]


# ── 01 Northstar ──────────────────────────────────────────────────────────────

class TestNorthstar:
    @pytest.fixture(scope="class")
    def res(self):
        return _by_table(_report(_excel("01_Northstar_Consolidated_Financials.xlsx")))

    def test_income_statement_only_the_intentional_errors(self, res):
        assert _found(res["Income Statement"]) == [
            ("Gross profit", "FY2025", _d(5650000), _d(5750000), _d(-100000)),
            ("Profit before tax", "FY2025", _d(2947000), _d(2937000), _d(10000)),
        ]
        assert res["Income Statement"].cross_casting_errors == ()

    def test_income_statement_formulas(self, res):
        s = analyze_structure(res["Income Statement"].table)
        assert [r.formula for r in s.relations] == [
            "Gross profit = Revenue + Cost of sales",
            "Operating profit = Gross profit + Other operating income + Selling expenses "
            "+ Administrative expenses + R&D + Impairment loss",
            "Profit before tax = Operating profit + Finance income + Finance costs "
            "+ Share of associate profit",
            "Profit for year = Profit before tax + Income tax",
        ]
        assert all(r.basis == "arithmetic" for r in s.relations)
        assert _uncertain(res["Income Statement"]) == []

    def test_balance_sheet_is_clean_and_fully_cast(self, res):
        r = res["Balance Sheet"]
        assert r.casting_errors == () and r.cross_casting_errors == ()
        s = analyze_structure(r.table)
        assert len(s.relations) == 6 and all(x.basis == "arithmetic" for x in s.relations)
        assert not any("Not included in any recognised total" in w for w in r.warnings)

    def test_cash_flow_is_now_fully_cast(self, res):
        r = res["Cash Flow"]
        # "Cash generated from operations" was not recognised before, so the
        # eight working-capital / non-cash lines were never cast.
        assert not any("Not included in any recognised total" in w for w in r.warnings)
        # FY2025 casts exactly as declared (3,405,000); FY2024 does not:
        # 2,610 + 520 + 90 + 285 − 75 − 110 − 180 − 210 + 95 = 3,025 (declared 3,020).
        assert _found(r) == [
            ("Cash generated from operations", "FY2024", _d(3020000), _d(3025000), _d(-5000)),
        ]


# ── 02 Riverbend ──────────────────────────────────────────────────────────────

class TestRiverbend:
    @pytest.fixture(scope="class")
    def res(self):
        return _by_table(_report(_excel("02_Riverbend_Trial_Balance.xlsx")))

    def test_trial_balance_imbalance_is_reported(self, res):
        r = res["Trial Balance"]
        assert [(e.declared, e.computed, e.delta) for e in r.casting_errors] == [
            (_d(27020000), _d(24850000), _d(2170000)),
        ]
        assert "trial balance must balance" in r.casting_errors[0].relation

    def test_suspense_account_is_flagged_independently(self, res):
        for sheet, label in (("Trial Balance", "Suspense account"),
                             ("Mapping Summary", "Suspense / unmapped")):
            assert any(
                w.startswith(f"Suspense / unmapped balance: '{label}'") and "85,000.00" in w
                for w in res[sheet].warnings
            ), sheet

    def test_mapping_summary_net_balance_is_debit_minus_credit(self, res):
        r = res["Mapping Summary"]
        assert r.casting_errors == ()
        assert analyze_structure(r.table).horizontal_formula() == "Net balance = Debit − Credit"
        assert any("credits exceed debits" in w for w in r.warnings)


# ── 03 Orbital ────────────────────────────────────────────────────────────────

class TestOrbital:
    @pytest.fixture(scope="class")
    def report(self):
        return _report(_excel("03_Orbital_Revenue_and_Segment_Notes.xlsx"))

    def test_zero_findings(self, report):
        assert report.total_casting_errors == 0
        assert report.total_cross_casting_errors == 0

    def test_no_uncertain_formulas(self, report):
        for r in report.validation_results:
            assert _uncertain(r) == [], r.table.sheet

    def test_contract_balances_formulas(self, report):
        r = _by_table(report)["Contract Balances"]
        s = analyze_structure(r.table)
        assert [x.formula for x in s.relations] == [
            "Net contract position = Contract assets − Contract liabilities",
        ]
        assert s.horizontal_formula() == "Movement = 2025 − 2024"

    def test_segment_subtotals_are_cast(self, report):
        s = analyze_structure(_by_table(report)["Revenue by Segment"].table)
        assert [x.formula for x in s.relations] == [
            "Gross revenue = Cloud subscriptions + Implementation + Managed services "
            "+ Hardware resale",
            "External revenue = Gross revenue + Inter-segment eliminations",
        ]


# ── 04 Cedar ──────────────────────────────────────────────────────────────────

class TestCedar:
    @pytest.fixture(scope="class")
    def res(self):
        return _by_table(_report(_excel("04_Cedar_Management_Accounts.xlsx")))

    def test_working_capital_zero_findings(self, res):
        r = res["Working Capital"]
        assert r.casting_errors == () and r.cross_casting_errors == ()
        assert [x.formula for x in analyze_structure(r.table).relations] == [
            "Net working capital = Inventory + Receivables − Payables",
        ]
        assert _uncertain(r) == []

    def test_monthly_pl_zero_findings(self, res):
        r = res["Monthly P&L"]
        assert r.casting_errors == () and r.cross_casting_errors == ()
        s = analyze_structure(r.table)
        assert s.horizontal_formula() == "FY2025 = Q1 + Q2 + Q3 + Q4"
        assert len(s.relations) == 4 and all(x.basis == "arithmetic" for x in s.relations)


# ── 05 Meridian (PDF, recorded VLM extraction) ────────────────────────────────

class TestMeridian:
    @pytest.fixture(scope="class")
    def report(self):
        return _report(_meridian_tables(), "05_Meridian_Financial_Statements.pdf")

    def test_both_intentional_errors_detected(self, report):
        found = sorted(
            (e.row_label, e.label, e.declared, e.computed, e.delta)
            for r in report.validation_results for e in r.casting_errors
        )
        assert found == [
            ("Closing net book value", "FY2025 USD", _d(2700000), _d(2640000), _d(60000)),
            ("Gross profit", "FY2025", _d(3900000), _d(3800000), _d(100000)),
        ]
        assert report.total_cross_casting_errors == 0

    def test_ppe_roll_forward_basis_is_recorded(self, report):
        r = next(r for r in report.validation_results if "Property" in r.table.title)
        assert "roll-forward" in r.casting_errors[0].evidence[0]

    def test_later_lines_use_the_declared_gross_profit(self, report):
        r = next(r for r in report.validation_results if "Profit or Loss" in r.table.title)
        s = analyze_structure(r.table)
        op = next(x for x in s.relations if x.row.label == "Operating profit")
        assert op.components[0].row.label == "Gross profit" and op.basis == "arithmetic"


# ── PDF (VLM) ↔ Excel consistency ────────────────────────────────────────────

def _to_xlsx(tables) -> bytes:
    """Write tables to a workbook the way an accountant would type them."""
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for i, t in enumerate(tables):
        ws = wb.create_sheet(f"S{i + 1}")
        ws.append([t.title])
        ws.append(["Line item", *t.headers])
        for r in t.rows:
            ws.append([r.label, *[None if c.value is None else float(c.value) for c in r.cells]])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _fmt_pdf(v: Decimal | None) -> str | None:
    if v is None:
        return None
    s = f"{abs(v):,.0f}"
    return f"({s})" if v < 0 else s


def _to_vlm(tables) -> list:
    """The same tables as the VLM returns them from a PDF page (strings, parentheses)."""
    payload = {"tables": [
        {
            "title": t.title,
            "headers": list(analyze_structure(t).column_label(c.index) for c in t.columns
                            if c.role != "text"),
            "continues_previous_page": False,
            "rows": [
                {"label": r.label, "kind": "data", "bbox": None,
                 "values": [_fmt_pdf(c.value) for c, info in zip(r.cells, t.columns)
                            if info.role != "text"]}
                for r in t.rows if r.kind.is_amount_row
            ],
        }
        for t in tables
    ]}
    return list(_parse_tables(json.dumps(payload), 1))


def _signature(report):
    out = []
    for r in report.validation_results:
        s = analyze_structure(r.table)
        out.append((
            tuple(x.formula for x in s.relations if x.checkable),
            s.horizontal_formula(),
            tuple(sorted((e.row_label, e.label, e.computed, e.delta) for e in r.casting_errors)),
            len(r.cross_casting_errors),
        ))
    return out


class TestPdfExcelConsistency:
    def test_meridian_pdf_equals_same_tables_in_excel(self):
        pdf = _report(_meridian_tables())
        xlsx = _report(read_excel(_to_xlsx(_meridian_tables())))
        assert _signature(pdf) == _signature(xlsx)
        assert pdf.total_casting_errors == xlsx.total_casting_errors == 2

    @pytest.mark.parametrize("name,sheets", [
        ("04_Cedar_Management_Accounts.xlsx", ("Working Capital", "Monthly P&L")),
        ("03_Orbital_Revenue_and_Segment_Notes.xlsx", ("Contract Balances", "Revenue by Segment")),
        ("01_Northstar_Consolidated_Financials.xlsx", ("Income Statement", "Cash Flow")),
    ])
    def test_excel_tables_read_as_pdf_give_the_same_result(self, name, sheets):
        tables = [t for t in _excel(name) if t.sheet in sheets]
        assert _signature(_report(tables)) == _signature(_report(_to_vlm(tables)))
