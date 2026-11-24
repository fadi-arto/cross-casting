"""
Layer: Domain — Casting engine.

Verifies that declared totals match the totals computed from their own
components.  Pure Python Decimal arithmetic only; never calls an external API.

Casting rules (relationships come from domain.structure, never from
"sum every number in the column"):
  Vertical  : for every subtotal / total row and every amount column,
              declared value == Σ(sign × component value) of the rows that
              make up that total.
  Horizontal: when a derived column is established (a row-total column,
              or a difference column such as "Movement" = 2025 − 2024 or
              "Net balance" = Debit − Credit, confirmed by the figures), for
              every amount row declared value == Σ(sign × the row's other
              columns).
  Balance   : in a table identified as a trial balance, total debits ==
              total credits.

Only relations whose formula is reliable (confirmed by the figures, or the
only reading the structure allows) are checked.  Uncertain formulas raise no
casting error; domain.structure reports them as warnings instead.
"""
from __future__ import annotations

from decimal import Decimal

from .models import CastingError, FinancialTable
from .money import ZERO, money, within_tolerance
from .structure import TableStructure, analyze_structure, cell_ref

ABS_TOLERANCE = Decimal("0.01")


def check_casting(
    table: FinancialTable,
    *,
    abs_tolerance: Decimal = ABS_TOLERANCE,
    structure: TableStructure | None = None,
) -> tuple[CastingError, ...]:
    return run_casting(table, abs_tolerance=abs_tolerance, structure=structure)[0]


def run_casting(
    table: FinancialTable,
    *,
    abs_tolerance: Decimal = ABS_TOLERANCE,
    structure: TableStructure | None = None,
) -> tuple[tuple[CastingError, ...], int]:
    """Run every casting check; return (errors, number of comparisons made).

    One comparison = one declared amount tested against the amount computed
    from its components, whether it passed or failed.
    """
    s = structure or analyze_structure(table, abs_tolerance=abs_tolerance)
    errors: list[CastingError] = []
    checks = 0
    for check in (_vertical_check, _horizontal_check, _balance_check):
        found, made = check(table, s, abs_tolerance)
        errors.extend(found)
        checks += made
    return tuple(errors), checks


# ── Vertical: each total against its own components ──────────────────────────

def _vertical_check(
    table: FinancialTable, s: TableStructure, tol: Decimal,
) -> tuple[list[CastingError], int]:
    errors: list[CastingError] = []
    checks = 0
    for rel in s.checked_relations:
        if not rel.components:
            continue
        total_row = rel.row
        basis = (f"Formula basis: {rel.basis_note}.",) if rel.basis_note else ()
        for col in s.amount_columns:
            declared = _val(total_row, col)
            if declared is None:
                continue
            values = [(c.sign, _val(c.row, col)) for c in rel.components]
            if all(v is None for _, v in values):
                continue
            checks += 1
            computed = sum((sign * v for sign, v in values if v is not None), ZERO)
            if within_tolerance(computed, declared, abs_tolerance=tol):
                continue
            label = s.column_label(col)
            delta = declared - computed
            evidence = basis + _diagnose(table, s, rel, col, delta, tol)
            if rel.low_confidence:
                evidence = basis + (
                    "Structure unresolved — needs review: the formula was never confirmed "
                    "on a column that can tell it apart from the alternatives, so this "
                    "difference may come from reading the table, not from the figures.",
                ) + evidence[len(basis):]
            errors.append(CastingError(
                table_title=table.title,
                axis="column",
                index=col,
                label=label,
                declared=declared,
                computed=money(computed),
                delta=money(delta),
                row_index=total_row.index,
                row_label=total_row.label,
                column_index=col,
                column_label=label,
                relation=rel.formula,
                cell_ref=cell_ref(table, s, total_row, col),
                evidence=evidence,
                confidence="low" if rel.low_confidence else "high",
            ))
    return errors, checks


def _diagnose(table, s, rel, col: int, delta: Decimal, tol: Decimal) -> tuple[str, ...]:
    """Patterns in the difference that point at the cause (shown with the finding)."""
    if not delta:
        return ()
    hints: list[str] = []
    for comp in rel.components:
        v = _val(comp.row, col)
        if v is None or v == ZERO:
            continue
        name = comp.row.label or "[unlabelled row]"
        if within_tolerance(abs(delta), abs(2 * v), abs_tolerance=tol):
            hints.append(f"The difference is twice '{name}' ({money(v)}): that line is "
                         "probably added where it should be subtracted, or the reverse.")
        elif comp.row.kind.is_aggregate and within_tolerance(abs(delta), abs(v), abs_tolerance=tol):
            hints.append(f"The difference equals subtotal '{name}' ({money(v)}): it may be "
                         "counted twice, or left out.")
    own = {c.row.index for c in rel.components} | {rel.row.index}
    for other in s.relations:
        if other.row.index in own:
            continue
        v = _val(other.row, col)
        if v is not None and v != ZERO and within_tolerance(abs(delta), abs(v), abs_tolerance=tol):
            hints.append(f"The difference equals another total, '{other.row.label}' "
                         f"({money(v)}): rows of another period or block may be mixed in.")
            break
    return tuple(hints)


# ── Horizontal: row-total column against the period columns ───────────────────

def _horizontal_check(
    table: FinancialTable, s: TableStructure, tol: Decimal,
) -> tuple[list[CastingError], int]:
    errors: list[CastingError] = []
    checks = 0
    tc = s.total_column
    if tc is None:
        return errors, checks
    relation = s.horizontal_formula()
    total_label = s.column_label(tc)
    for row in table.rows:
        if not row.kind.is_amount_row:
            continue
        declared = _val(row, tc)
        values = [(sign, _val(row, c)) for c, sign in zip(s.component_columns, s.signs)]
        if declared is None or all(v is None for _, v in values):
            continue
        checks += 1
        computed = sum((sign * v for sign, v in values if v is not None), ZERO)
        if within_tolerance(computed, declared, abs_tolerance=tol):
            continue
        errors.append(CastingError(
            table_title=table.title,
            axis="row",
            index=row.index,
            label=row.label,
            declared=declared,
            computed=money(computed),
            delta=money(declared - computed),
            row_index=row.index,
            row_label=row.label,
            column_index=tc,
            column_label=total_label,
            relation=f"{row.label}: {relation}",
            cell_ref=cell_ref(table, s, row, tc),
        ))
    return errors, checks


# ── Balance: a trial balance must balance ────────────────────────────────────

def _balance_check(
    table: FinancialTable, s: TableStructure, tol: Decimal,
) -> tuple[list[CastingError], int]:
    ledger = s.ledger
    if ledger is None or not ledger.trial_balance:
        return [], 0
    if within_tolerance(ledger.debit_total, ledger.credit_total, abs_tolerance=tol):
        return [], 1
    debit_label = s.column_label(ledger.debit_column)
    credit_label = s.column_label(ledger.credit_column)
    row = ledger.total_row
    return [CastingError(
        table_title=table.title,
        axis="column",
        index=ledger.credit_column,
        label=credit_label,
        declared=ledger.credit_total,
        computed=money(ledger.debit_total),
        delta=money(ledger.credit_total - ledger.debit_total),
        row_index=row.index if row else None,
        row_label=row.label if row else "Trial balance (total debits vs total credits)",
        column_index=ledger.credit_column,
        column_label=credit_label,
        relation=f"Σ {credit_label} = Σ {debit_label} (a trial balance must balance)",
        cell_ref=cell_ref(table, s, row, ledger.credit_column) if row else None,
        evidence=(
            f"Total {debit_label} {money(ledger.debit_total):,} vs total {credit_label} "
            f"{money(ledger.credit_total):,} — {ledger.source}.",
        ),
    )], 1


def _val(row, col: int) -> Decimal | None:
    return row.cells[col].value if col < len(row.cells) else None


# ── Cells the arithmetic vouches for ──────────────────────────────────────────

def confirmed_cells(
    table: FinancialTable, s: TableStructure, tol: Decimal = Decimal("0.01"),
) -> frozenset[tuple[int, int]]:
    """(row index, cell position) of every figure that takes part in a sum that foots.

    If the reader had misread one of these figures, that sum would not reconcile
    (barring two errors that cancel exactly), so the arithmetic itself confirms the
    reading.  Used to tell real reading doubts from OCR noise: an OCR mismatch on a
    confirmed figure is OCR's mistake, not the model's.
    """
    out: set[tuple[int, int]] = set()

    def keep(cells: list[tuple[int, int, Decimal | None]], declared: Decimal | None) -> None:
        values = [v for _, _, v in cells]
        if declared is None or all(v is None for v in values):
            return
        if not any(v for v in values if v is not None) and not declared:
            return                                   # 0 = 0 + … proves nothing
        out.update((r, c) for r, c, v in cells if v is not None)

    for rel in s.relations:
        if not rel.confirmed or not rel.components:
            continue
        for col in s.amount_columns:
            declared = _val(rel.row, col)
            parts = [(c.row.index, col, _val(c.row, col)) for c in rel.components]
            computed = sum((c.sign * v for c, (_, _, v) in zip(rel.components, parts) if v is not None), ZERO)
            if declared is not None and within_tolerance(computed, declared, abs_tolerance=tol):
                keep(parts + [(rel.row.index, col, declared)], declared)

    tc = s.total_column
    if tc is not None:
        for row in table.rows:
            if not row.kind.is_amount_row:
                continue
            declared = _val(row, tc)
            parts = [(row.index, c, _val(row, c)) for c in s.component_columns]
            computed = sum((sign * v for sign, (_, _, v) in zip(s.signs, parts) if v is not None), ZERO)
            if declared is not None and within_tolerance(computed, declared, abs_tolerance=tol):
                keep(parts + [(row.index, tc, declared)], declared)
    return frozenset(out)
