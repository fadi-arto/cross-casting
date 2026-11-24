"""
Regressions from the Amiantit FY2025 review — two false positives found by hand.

1. Ameron Saudi Arabia (discontinued operation): "Net assets = Assets − Liabilities"
   was read as "Assets + Liabilities" because the 2025 column, where liabilities are
   blank, "confirmed" the + sign.  A column where a component is 0 / blank cannot
   tell + from −.
2. Note 37 related parties: rows alternate 2025 / 2024 per entity; "Total 2025" was
   compared with the detail rows of both years (diff = the 2024 total).

Exact figures from the statements (SR '000); the engine is never told the formula.
"""
from decimal import Decimal

from domain.casting import check_casting
from domain.cross_casting import check_cross_casting
from domain.findings import consolidate_findings
from domain.models import Cell, FinancialTable, RowKind, TableRow
from domain.structure import analyze_structure

D, T, S = RowKind.DATA, RowKind.TOTAL, RowKind.SUBTOTAL


def _cell(v, i, j):
    if v is None or v in ("", "-", "–"):
        return Cell(None, "" if v is None else v, i, j)
    text = str(v)
    neg = text.startswith("(") and text.endswith(")")
    num = Decimal(text.strip("()").replace(",", ""))
    return Cell(-num if neg else num, text, i, j)


def _table(headers, spec, title="T"):
    rows = []
    for i, (label, values, *kind) in enumerate(spec):
        rows.append(TableRow(i, label, tuple(_cell(v, i, j) for j, v in enumerate(values)),
                             kind[0] if kind else D))
    return FinancialTable(title=title, headers=tuple(headers), rows=tuple(rows))


def _run(table):
    s = analyze_structure(table)
    c, x = consolidate_findings(
        check_casting(table, structure=s), check_cross_casting(table, structure=s), s,
    )
    return s, c, x


# ── Bug 1: Ameron — section signs and degenerate columns ─────────────────────

def ameron(zakat_2024="8,590"):
    return _table(["2025 SR '000", "2024 SR '000"], [
        ("Assets:", [None, None]),
        ("Trade receivables", ["14,103", "22,759"]),
        ("", ["14,103", "22,759"], S),
        ("Liabilities:", [None, None]),
        ("Accrued expenses and other liabilities", [None, "66"]),
        ("Zakat and income tax payable", [None, zakat_2024]),
        ("", [None, "8,656"], S),
        ("Carrying amount of net assets directly related to the discontinued operation",
         ["14,103", "14,103"], T),
    ], title="2.2.2 Ameron Saudi Arabia Limited")


class TestAmeronNetAssets:
    def test_no_finding_in_either_year(self):
        _, c, x = _run(ameron())
        assert not c and not x

    def test_formula_is_assets_minus_liabilities(self):
        s, _, _ = _run(ameron())
        net = s.relation_for(7)
        assert [(comp.row.index, comp.sign) for comp in net.components] == [(2, 1), (6, -1)]
        assert set(net.matched_columns) == {0, 1}      # confirmed by both years …
        assert net.unpinned == () and not net.low_confidence

    def test_negative_control_zakat_misstated_by_90(self):
        _, c, _ = _run(ameron(zakat_2024="8,500"))
        assert [(e.row_index, e.label, e.delta) for e in c] == [(6, "2024 SR '000", Decimal("90.00"))]

    def test_degenerate_column_alone_never_confirms_a_sign(self):
        # Liabilities appear in 2024 only and the 2024 net total does not reconcile
        # under either sign: the 2025 column (liabilities blank) cannot confirm "+",
        # so the finding is low confidence, "structure unresolved — needs review".
        tbl = _table(["2025", "2024"], [
            ("Assets total", ["100", "200"], S),
            ("Liabilities total", [None, "50"], S),
            ("Net assets", ["100", "175"], T),
        ])
        s, c, _ = _run(tbl)
        assert len(c) == 1 and c[0].label == "2024"
        assert c[0].confidence == "low"
        assert s.relation_for(2).low_confidence
        assert any("needs review" in e for e in c[0].evidence)
        assert "UNCONFIRMED" in s.relation_for(2).basis_note


# ── Bug 2: related parties — interleaved periods ─────────────────────────────

# Layout read from the scan: the year is part of each row label.
def due_from(subor_2025="128"):
    return _table([
        "Sales to related parties SR'000", "Purchases / payments SR'000",
        "Amounts due from related parties SR'000", "Amounts due to related parties SR'000",
    ], [
        ("Amiantit Qatar Pipe Co. - 2025 (associate)", [None, None, None, None]),
        ("Amiantit Qatar Pipe Co. - 2024 (associate)", [None, None, "896", None]),
        ("Amiblu Germany GmbH - 2025 (joint venture)", [None, None, "30", None]),
        ("Amiblu Germany GmbH - 2024 (joint venture)", [None, None, "30", None]),
        ("Amiblu Holding - 2025 (joint venture)", [None, None, "81", None]),
        ("Amiblu Holding - 2024 (joint venture)", [None, None, "81", None]),
        ("Subor - 2025 (joint venture)", [None, None, subor_2025, None]),
        ("Subor - 2024 (joint venture)", [None, None, "128", None]),
        ("Others - 2025", [None, None, "2", None]),
        ("Others - 2024", [None, None, "3", None]),
        ("Total - 2025", [None, None, "241", None], T),
        ("Total - 2024", [None, None, "1,138", None], T),
    ], title="Note 37 Related party transactions")


# Layout described in the review: a Year column, entity name on the first row only.
def due_from_year_column():
    return _table(["Year", "Amounts due from related parties SR'000"], [
        ("Amiantit Qatar Pipe Co.", ["2025", "-"]),
        ("", ["2024", "896"]),
        ("Amiblu Germany GmbH", ["2025", "30"]),
        ("", ["2024", "30"]),
        ("Amiblu Holding", ["2025", "81"]),
        ("", ["2024", "81"]),
        ("Subor", ["2025", "128"]),
        ("", ["2024", "128"]),
        ("Others", ["2025", "2"]),
        ("", ["2024", "3"]),
        ("Total", ["2025", "241"], T),
        ("", ["2024", "1,138"], T),
    ])


def loans():
    return _table([
        "Purchases / payments SR'000", "Interest received / (paid) SR'000",
        "Amounts due from / (to) related parties SR'000",
    ], [
        ("Amiantit Qatar Pipe Co. - 2025 (associate)", ["(7,686)", None, "(5,038)"]),
        ("Amiantit Qatar Pipe Co. - 2024 (associate)", [None, None, None]),
        ("East Gas Co. - 2025 (associate)", ["(3,385)", None, None]),
        ("East Gas Co. - 2024 (associate)", [None, None, None]),
        ("Amiblu Tech AS - 2025 (joint venture)", ["(4,996)", None, "(2,094)"]),
        ("Amiblu Tech AS - 2024 (joint venture)", ["(10,346)", None, "(2,211)"]),
        ("Total - 2025", ["(16,067)", None, "(7,132)"], T),
        ("Total - 2024", ["(10,346)", None, "(2,211)"], T),
    ], title="Note 37 Loans from / to related parties")


def _components(s, total_row):
    return sorted(c.row.index for c in s.relation_for(total_row).components)


def _same_period_only(s, table, total_row, period_rows):
    """Components are rows of that period only, and include every non-blank one."""
    got = set(_components(s, total_row))
    non_blank = {i for i in period_rows if any(c.value for c in table.rows[i].cells)}
    return got <= set(period_rows) and non_blank <= got


class TestInterleavedPeriods:
    def test_amounts_due_from_no_finding(self):
        t = due_from()
        s, c, x = _run(t)
        assert not c and not x
        assert _same_period_only(s, t, 10, [0, 2, 4, 6, 8])     # 2025 rows only → 241
        assert _same_period_only(s, t, 11, [1, 3, 5, 7, 9])     # 2024 rows only → 1,138

    def test_year_column_layout_no_finding_and_names_carried_down(self):
        s, c, x = _run(due_from_year_column())
        assert not c and not x
        assert next(sp for sp in s.columns if sp.index == 0).role == "text"   # years ≠ amounts
        rel_2024 = s.relation_for(11)
        assert _components(s, 11) == [1, 3, 5, 7, 9]
        assert [comp.row.label for comp in rel_2024.components][:2] == [
            "Amiantit Qatar Pipe Co.", "Amiblu Germany GmbH",
        ]

    def test_loans_table_no_finding_either_year_or_column(self):
        t = loans()
        s, c, x = _run(t)
        assert not c and not x
        assert _same_period_only(s, t, 6, [0, 2, 4]) and _same_period_only(s, t, 7, [1, 3, 5])

    def test_negative_control_subor_2025_misstated_by_8(self):
        _, c, _ = _run(due_from(subor_2025="120"))
        assert [(e.row_label, e.delta) for e in c] == [("Total - 2025", Decimal("8.00"))]

    def test_roll_forward_mentioning_dates_is_not_split(self):
        tbl = _table(["Land", "Total"], [
            ("Balance as on 1 January, 2025", ["536,194", "1,913,704"]),
            ("Additions during the year", [None, "8,105"]),
            ("Balance as on December 31, 2025", ["536,194", "1,921,809"], T),
        ])
        s, c, _ = _run(tbl)
        assert not c and not any("alternate between periods" in w for w in s.warnings)


# ── Bug 3: diagnostic patterns on a remaining difference ─────────────────────

class TestDiagnostics:
    def test_difference_twice_a_component_points_at_its_sign(self):
        # Total = 100 + 30 → declared 70 (30 should have been subtracted, but the
        # label gives no hint): diff −60 = 2 × 30.
        tbl = _table(["2025"], [("A", ["100"]), ("B", ["30"]), ("Total", ["70"], T)])
        _, c, _ = _run(tbl)
        assert c and any("twice 'B'" in e for e in c[0].evidence)

    def test_difference_equal_to_another_total_points_at_mixed_periods(self):
        tbl = _table(["Amount"], [
            ("X", ["10"]), ("Y", ["20"]), ("Subtotal one", ["30"], S),
            ("Z", ["5"]), ("W", ["7"]), ("Grand total", ["42"], T),
            ("P", ["1"]), ("Q", ["2"]), ("Total two", ["33"], T),
        ])
        _, c, _ = _run(tbl)
        flagged = [e for e in c if e.row_label == "Total two"]
        assert flagged and any("another total" in e for e in flagged[0].evidence)
