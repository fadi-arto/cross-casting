"""
Layer: Domain — consolidation of findings that share one root cause.

One wrong number in a cross-cast table breaks several checks at once.  For
example, a wrong "Total assets" FY2025 figure also makes the "Total" column of
that row disagree with the asset lines, and makes the cross-cast of that block
fail.  Reporting each of those separately inflates the error count and hides
the actual cell to fix.

A secondary finding is merged into its root finding only when the arithmetic
proves it is fully explained by it (exact delta identity, within tolerance).
Merged findings are not discarded: they are kept as `evidence` on the root.
Anything that is not provably explained is reported as before.

Identities used (delta = declared − computed; s = component sign):
  (w = column sign of a period column: +1 for a row total, ±1 for a difference
   column such as Movement = 2025 − 2024)
  total-column vertical delta of row A  =  Σ w·(period-column vertical delta of A)
                                           (when A itself adds across correctly)
  total-column vertical delta of row A  = −Σ s·(horizontal delta of component c)
  cross  row_vs_col    = Σ s·h_c − Σ w·d_p
  cross  row_vs_grand  = −d_T
  cross  col_vs_grand  = −h_A
"""
from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from .models import CastingError, CrossCastingError
from .money import ZERO, money
from .structure import TableStructure

ABS_TOLERANCE = Decimal("0.01")


def consolidate_findings(
    casting: tuple[CastingError, ...],
    cross: tuple[CrossCastingError, ...],
    structure: TableStructure,
    *,
    abs_tolerance: Decimal = ABS_TOLERANCE,
) -> tuple[tuple[CastingError, ...], tuple[CrossCastingError, ...]]:
    errors = _dedupe_exact(list(casting))
    tc = structure.total_column

    # Index the raw (pre-merge) deltas used by the identities.
    vertical: dict[tuple[int, int], CastingError] = {
        (e.row_index, e.index): e for e in errors if e.axis == "column" and e.row_index is not None
    }
    horizontal: dict[int, CastingError] = {e.index: e for e in errors if e.axis == "row"}
    evidence: dict[int, list[str]] = {}      # id(error) → notes
    dropped: set[int] = set()

    def note(err: CastingError, text: str) -> None:
        evidence.setdefault(id(err), []).append(text)

    def tol_for(terms: int) -> Decimal:
        return abs_tolerance * (terms + 1)

    # A parent total that only fails because one of its own (sub)totals is wrong:
    # parent delta == −Σ sign × (child subtotal delta) in the same column.
    # The parent reconciles once the child is corrected, so the child is the root.
    relations = structure.checked_relations
    weight = structure.column_sign

    for rel in relations:
        a = rel.row.index
        for col in structure.amount_columns:
            p_err = vertical.get((a, col))
            if p_err is None:
                continue
            child = [
                (c.sign, vertical[(c.row.index, col)])
                for c in rel.components
                if (c.row.index, col) in vertical and id(vertical[(c.row.index, col)]) not in dropped
            ]
            if not child:
                continue
            explained = -sum((s * e.delta for s, e in child), ZERO)
            if explained != ZERO and abs(p_err.delta - explained) <= tol_for(len(child)):
                dropped.add(id(p_err))
                for _, e in child:
                    note(e, (
                        f"Also makes '{rel.row.label}' ({p_err.label}) disagree with its "
                        f"components (declared {_fmt(p_err.declared)}, computed "
                        f"{_fmt(p_err.computed)}); '{rel.row.label}' reconciles once "
                        f"'{e.row_label}' is corrected."
                    ))

    if tc is not None:
        for rel in relations:
            a = rel.row.index
            t_err = vertical.get((a, tc))
            if t_err is None:
                continue
            period_errs = [vertical[(a, c)] for c in structure.component_columns if (a, c) in vertical]
            if period_errs and a not in horizontal:
                explained = sum((weight(e.index) * e.delta for e in period_errs), ZERO)
                if abs(t_err.delta - explained) <= tol_for(len(period_errs)):
                    dropped.add(id(t_err))
                    for e in period_errs:
                        note(e, (
                            f"Also misstates the '{t_err.label}' column of '{rel.row.label}' "
                            f"(declared {_fmt(t_err.declared)}, computed {_fmt(t_err.computed)}); "
                            "that row adds across correctly, so this is the same error."
                        ))
                    continue
            comp_h = [
                (c.sign, horizontal[c.row.index]) for c in rel.components if c.row.index in horizontal
            ]
            if comp_h:
                explained = -sum((s * e.delta for s, e in comp_h), ZERO)
                if explained != ZERO and abs(t_err.delta - explained) <= tol_for(len(comp_h)):
                    dropped.add(id(t_err))
                    for _, e in comp_h:
                        note(e, (
                            f"Also makes the '{t_err.label}' column of '{rel.row.label}' disagree "
                            f"with its components (declared {_fmt(t_err.declared)}, computed "
                            f"{_fmt(t_err.computed)})."
                        ))

    kept_cross: list[CrossCastingError] = []
    for x in cross:
        rel = structure.relation_for(x.row_index) if x.row_index is not None else None
        if rel is None or not rel.checkable or tc is None:
            kept_cross.append(x)
            continue
        a = rel.row.index
        d_p = [vertical[(a, c)] for c in structure.component_columns if (a, c) in vertical]
        d_t = vertical.get((a, tc))
        h_a = horizontal.get(a)
        h_c = [(c.sign, horizontal[c.row.index]) for c in rel.components if c.row.index in horizontal]

        if x.check == "row_vs_col":
            roots = d_p + [e for _, e in h_c]
            expected = (
                sum((s * e.delta for s, e in h_c), ZERO)
                - sum((weight(e.index) * e.delta for e in d_p), ZERO)
            )
        elif x.check == "row_vs_grand":
            roots = [d_t] if d_t else []
            expected = -d_t.delta if d_t else ZERO
        elif x.check == "col_vs_grand":
            roots = [h_a] if h_a else []
            expected = -h_a.delta if h_a else ZERO
        else:
            roots, expected = [], ZERO

        if roots and expected != ZERO and abs(x.delta - expected) <= tol_for(len(roots)):
            # A root that was itself merged passes the note on to what it was merged into.
            live_roots = [r for r in roots if id(r) not in dropped] or [
                e for e in d_p + [e for _, e in h_c] if id(e) not in dropped
            ]
            for r in live_roots:
                note(r, (
                    f"Also breaks the cross-cast of '{rel.row.label}': {x.description} "
                    f"(rows {_fmt(x.row_total)}, columns {_fmt(x.col_total)}"
                    + (f", grand total {_fmt(x.grand_total)}" if x.grand_total is not None else "")
                    + ")."
                ))
            if live_roots:
                continue
        kept_cross.append(x)

    kept: list[CastingError] = []
    for e in errors:
        if id(e) in dropped:
            continue
        extra = evidence.get(id(e))
        kept.append(replace(e, evidence=e.evidence + tuple(extra)) if extra else e)
    return tuple(kept), tuple(kept_cross)


def _dedupe_exact(errors: list[CastingError]) -> list[CastingError]:
    seen: set[tuple] = set()
    out: list[CastingError] = []
    for e in errors:
        key = (e.axis, e.index, e.row_index, e.column_index, e.declared, e.computed)
        if key in seen:
            continue
        seen.add(key)
        out.append(e)
    return out


def _fmt(value: Decimal | None) -> str:
    if value is None:
        return "—"
    return f"{money(value):,}"
