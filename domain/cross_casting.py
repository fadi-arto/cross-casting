"""
Layer: Domain — Cross-Casting engine.

Verifies the multi-dimensional reconciliation for every total row whose
components are known (domain.structure) and whose table has a row-total
column:

  Σ(components' row totals)  ==  Σ(total row across periods)  ==  grand total

               Q1     Q2     Q3   │ Row Total
  Sales        100    150    200  │   450
  Marketing     50     60     70  │   180
  ─────────────────────────────────────────
  Col Total    150    210    270  │   630  ← grand total
                 └────────────────┘
                 sum(col totals) = 630 = sum(row totals) ✓

The derived column may be a difference ("Movement" = 2025 − 2024): the same
identity holds with the column signs applied.

For hierarchical statements each total is cross-cast against *its own*
components only (Total assets against the asset lines, Total liabilities and
equity against Total liabilities + Total equity …), so nothing is counted twice.
"""
from __future__ import annotations

from decimal import Decimal

from .models import CrossCastingError, FinancialTable
from .money import ZERO, money, within_tolerance
from .structure import TableStructure, analyze_structure

ABS_TOLERANCE = Decimal("0.01")

DESC_ROW_VS_COL = "مجموع إجماليات الصفوف لا يساوي مجموع إجماليات الأعمدة"
DESC_ROW_VS_GRAND = "مجموع إجماليات الصفوف لا يساوي الإجمالي الكلي"
DESC_COL_VS_GRAND = "مجموع إجماليات الأعمدة لا يساوي الإجمالي الكلي"


def check_cross_casting(
    table: FinancialTable,
    *,
    abs_tolerance: Decimal = ABS_TOLERANCE,
    structure: TableStructure | None = None,
) -> tuple[CrossCastingError, ...]:
    return run_cross_casting(table, abs_tolerance=abs_tolerance, structure=structure)[0]


def run_cross_casting(
    table: FinancialTable,
    *,
    abs_tolerance: Decimal = ABS_TOLERANCE,
    structure: TableStructure | None = None,
) -> tuple[tuple[CrossCastingError, ...], int]:
    """Run every cross-casting check; return (errors, number of comparisons made).

    Each total row contributes one comparison per identity tested:
    rows vs columns, and — when a grand total is present — rows vs grand total
    and columns vs grand total.
    """
    s = structure or analyze_structure(table, abs_tolerance=abs_tolerance)
    tc = s.total_column
    if tc is None or len(s.component_columns) < 2:
        return (), 0  # needs ≥2 period/value columns + a row-total column

    errors: list[CrossCastingError] = []
    checks = 0
    for rel in s.checked_relations:
        if not rel.components:
            continue
        total_row = rel.row

        comp_totals = [(c.sign, _val(c.row, tc)) for c in rel.components]
        period_values = [(w, _val(total_row, c)) for c, w in zip(s.component_columns, s.signs)]
        if all(v is None for _, v in comp_totals) or all(v is None for _, v in period_values):
            continue

        sum_row_totals = sum((sign * v for sign, v in comp_totals if v is not None), ZERO)
        sum_col_totals = sum((w * v for w, v in period_values if v is not None), ZERO)
        grand_total = _val(total_row, tc)

        if sum_row_totals == ZERO and sum_col_totals == ZERO:
            continue

        def err(description, check, delta):
            return CrossCastingError(
                table_title=table.title,
                description=description,
                row_total=money(sum_row_totals),
                col_total=money(sum_col_totals),
                grand_total=grand_total,
                delta=money(delta),
                row_index=total_row.index,
                row_label=total_row.label,
                check=check,
            )

        checks += 1
        if not within_tolerance(sum_row_totals, sum_col_totals, abs_tolerance=abs_tolerance):
            errors.append(err(DESC_ROW_VS_COL, "row_vs_col", sum_row_totals - sum_col_totals))

        if grand_total is not None:
            checks += 2
            if not within_tolerance(sum_row_totals, grand_total, abs_tolerance=abs_tolerance):
                errors.append(err(DESC_ROW_VS_GRAND, "row_vs_grand", sum_row_totals - grand_total))
            if not within_tolerance(sum_col_totals, grand_total, abs_tolerance=abs_tolerance):
                errors.append(err(DESC_COL_VS_GRAND, "col_vs_grand", sum_col_totals - grand_total))

    return tuple(errors), checks


def _val(row, col: int) -> Decimal | None:
    return row.cells[col].value if col < len(row.cells) else None
