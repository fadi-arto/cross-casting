"""
Regression: Note 27 Segment information (NajdRidge FS 2025, page 23).

"Segment liabilities" was wrongly confirmed as "= Segment assets" because the
Eliminations column had the same figure on both rows ((24,600) = (24,600)).  The
engine then raised a finding per real column (Pipe manufacturing, Water
management, Contracting, Total).

An *inferred* total (no "Total"-like label — a row is tagged as aggregate from
its bold formatting alone) now needs:
  * ≥ 2 components,
  * ≥ 2 *confirming* columns, where Eliminations / Adjustments / Inter-segment /
    Reconciling-items columns are not confirmation.

A real error in a labelled total (e.g. Total revenue) is still caught.
"""
from decimal import Decimal

from domain.casting import check_casting
from domain.cross_casting import check_cross_casting
from domain.findings import consolidate_findings
from domain.labels import is_elimination_header
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
    for i, (label, values, *kind_evidence) in enumerate(spec):
        kind = kind_evidence[0] if kind_evidence else D
        evidence = kind_evidence[1] if len(kind_evidence) > 1 else ""
        rows.append(TableRow(
            index=i, label=label,
            cells=tuple(_cell(v, i, j) for j, v in enumerate(values)),
            kind=kind, kind_evidence=evidence,
        ))
    return FinancialTable(title=title, headers=tuple(headers), rows=tuple(rows))


def _run(table):
    s = analyze_structure(table)
    c, x = consolidate_findings(
        check_casting(table, structure=s), check_cross_casting(table, structure=s), s,
    )
    return s, c, x


# Figures taken verbatim from the synthetic CLEAN statements.
HEADERS = [
    "Pipe manufacturing SR'000", "Water management SR'000",
    "Contracting SR'000", "Eliminations SR'000", "Total SR'000",
]


def segment_table(seg_liab_pipe="405,329"):
    # VLM marks the two "Segment" rows as aggregates because they appear in bold
    # on the scan ("bold"); their labels are *not* total-like.
    return _table(HEADERS, [
        ("External revenue",       ["340,329",  "60,332",  "180,444",  None,       "581,105"]),
        ("Inter-segment revenue",  ["220,889",  "25,000",  "40,000",   "(285,889)", None]),
        ("Total revenue",          ["561,218",  "85,332",  "220,444",  "(285,889)", "581,105"], S),
        ("Cost of revenue",        ["(410,000)","(60,000)","(170,000)", "280,000",  "(360,000)"]),
        ("Gross profit",           ["151,218",  "25,332",   "50,444",  "(5,889)",   "221,105"], S),
        ("Segment assets",         ["710,363",  "130,000",  "300,000", "(24,600)", "1,115,763"], S, "bold"),
        ("Segment liabilities",    [seg_liab_pipe, "80,000", "190,000","(24,600)",  "650,729"], S, "bold"),
    ], title="Note 27 Segment information")


# ── Core false positive ─────────────────────────────────────────────────────

class TestSegmentLiabilitiesNotConfirmedByEliminations:
    def test_elimination_column_alone_does_not_confirm_an_inferred_formula(self):
        """Clean table → no vertical findings."""
        s, c, _ = _run(segment_table())
        # No finding on Segment liabilities' row.
        assert not any(err.row_label == "Segment liabilities" for err in c)
        # And the engine did not pin a 1-component "= Segment assets" formula on it.
        rel = s.relation_for(6)
        if rel is not None:                                   # often rejected outright
            assert len(rel.components) >= 2
            confirming = [
                i for i in rel.matched_columns
                if not is_elimination_header(s.column_label(i))
            ]
            assert len(confirming) >= 2

    def test_a_real_error_in_the_subtotal_is_still_caught(self):
        """E07 (Total revenue, Pipe manufacturing: shown 560,018 vs 561,218)."""
        tbl = segment_table()
        rows = list(tbl.rows)
        rows[2] = TableRow(
            index=2, label="Total revenue",
            cells=tuple(_cell(v, 2, j) for j, v in enumerate(
                ["560,018", "85,332", "220,444", "(285,889)", "581,105"]
            )),
            kind=S,
        )
        tbl = FinancialTable(
            title=tbl.title, headers=tbl.headers, rows=tuple(rows),
        )
        _, c, _ = _run(tbl)
        flagged = [
            e for e in c
            if e.row_label == "Total revenue" and "Pipe" in (e.label or "")
        ]
        assert flagged and flagged[0].declared == Decimal("560018") \
               and flagged[0].computed == Decimal("561218.00")


# ── Rule-level unit tests ────────────────────────────────────────────────────

class TestInferredFormulaRules:
    def test_single_component_equality_is_never_inferred(self):
        """2-row table where B equals A in one column only must not infer B = A."""
        tbl = _table(["Q1", "Q2"], [
            ("Row A", ["100", "200"]),
            ("Row B", ["100", "500"], S, "bold"),             # matches A only in Q1
        ])
        s, _, _ = _run(tbl)
        rel = s.relation_for(1)
        # Either the row was demoted to detail, or the relation has ≥ 2 components.
        assert rel is None or len(rel.components) >= 2

    def test_labelled_total_still_matches_with_one_column(self):
        """An explicit 'Total' label does not need the stricter rule."""
        tbl = _table(["Q1"], [
            ("A", ["100"]),
            ("B", ["200"]),
            ("Total", ["300"], T),                            # explicit label
        ])
        s, c, _ = _run(tbl)
        rel = s.relation_for(2)
        assert rel is not None and len(rel.components) == 2 and not c


class TestEliminationHeader:
    def test_recognised_english_variants(self):
        for h in ("Eliminations", "Adjustments", "Reconciling items",
                  "Inter-segment", "Inter segment", "Consolidation adjustments"):
            assert is_elimination_header(h), h

    def test_recognised_arabic_variants(self):
        for h in ("حذف", "الاستبعادات", "تسويات"):
            assert is_elimination_header(h), h

    def test_normal_period_columns_are_not_eliminations(self):
        for h in ("2025 SR'000", "Pipe manufacturing", "Total", "Note"):
            assert not is_elimination_header(h), h


# ── Severity ─────────────────────────────────────────────────────────────────

class TestInferredFinding_Severity:
    def test_inferred_finding_has_low_confidence_so_severity_is_medium(self):
        """A real mismatch against an *inferred* formula is 'needs review'."""
        # Craft a 3-column table where bold rows do produce an inferred formula
        # (matching in 2 real columns, ≥ 2 components) and one column fails.
        tbl = _table(["C1", "C2", "C3"], [
            ("A",       ["100", "200", "400"]),
            ("B",       ["100", "200", "500"]),
            ("Combined",["200", "400", "901"], S, "bold"),  # off by 1 in C3
        ])
        _, c, _ = _run(tbl)
        assert c and c[0].confidence == "low"              # → stored severity = "medium"
