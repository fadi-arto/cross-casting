"""
Layer: Domain — financial table structure analysis.

Turns a FinancialTable into an explicit, evidence-backed structure that the
casting and cross-casting engines check against:

* Columns  — which cell positions are amount columns, which one is derived
  from the others (a row-total column, or a difference column such as
  "Movement" = 2025 − 2024 or "Net balance" = Debit − Credit), and the real
  header of each.
* Relations — for every subtotal / total row, the exact rows it is made of,
  the sign with which each one enters, and *how sure we are* of that formula.

How a formula is interpreted
────────────────────────────
Financial totals are not all simple sums.  Every total row is reconciled
against the rows still "open" above it (not yet absorbed by an earlier total),
under two sign conventions:

    sum     every component added with the sign it is presented with —
            "Cost of sales (7,600,000)" is already negative;
    signed  deduction lines are subtracted although they are presented as
            positive amounts — expenses and costs, obligations in a net
            position (Net working capital = Inventory + Receivables − Payables,
            Net contract position = Contract assets − Contract liabilities),
            and reducing movements (disposals, repayments …).

Every relation records the *basis* on which its formula was accepted:

    arithmetic  the formula reconciles exactly in at least one column; any
                other column where it does not is a casting error.
    semantic    no column reconciles (e.g. a one-column note that contains an
                error), but the structure leaves only one reading: a
                roll-forward (opening balance + movements = closing balance),
                a block total over plain detail rows, a total named after the
                totals it adds, or a result / net line whose deduction lines
                are presented consistently.
    uncertain   neither — the formula cannot be determined reliably.  No
                casting error is raised; a warning shows the most likely
                formula and the difference it would give, for manual review.
                An uncertain row does not absorb any rows, so later totals can
                still reconcile over them.

A detail row that equals the sum of a run of open rows above it in *every*
amount column (at least two non-zero columns) is recognised as an unlabelled
subtotal ("Gross revenue", "External revenue" …) — coincidence across several
columns is not plausible, and if it does not reconcile it stays a detail row,
so this can never create a finding by itself.

Running values (income-statement result lines) work naturally: after
"Gross profit" absorbs Revenue and Cost of sales, the open rows are
[Gross profit, Operating expenses], so "Operating profit" reconciles to
Gross profit − Operating expenses using the *declared* Gross profit.
An error therefore never cascades into the following lines.

Pure Python + Decimal; no external dependencies.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from decimal import Decimal
from itertools import combinations
from typing import Callable, Literal

from .labels import (
    is_cash_subtotal_label,
    is_closing_balance,
    is_credit_header,
    is_debit_header,
    is_deduction_label,
    is_elimination_header,
    is_liability_label,
    is_movement_header,
    is_net_position_label,
    is_note_header,
    is_opening_balance,
    is_percent_header,
    is_profit_type,
    is_subtotal_label,
    is_suspense_label,
    is_total_header,
    is_total_label,
    norm,
)
from .models import ColumnInfo, ColumnRole, FinancialTable, RowKind, TableRow
from .money import ZERO, money, within_tolerance
from .normalize import fold_digits

ABS_TOLERANCE = Decimal("0.01")
_FORMULA_MAX_TERMS = 12
_STOP_WORDS = frozenset(norm(w) for w in (
    "total", "totals", "grand", "and", "the", "of", "for", "إجمالي", "الإجمالي", "مجموع",
    "المجموع", "و",
))
_TRIAL_BALANCE = ("trial balance", "ميزان المراجعة")

RelationMode = Literal["sum", "signed"]
RelationBasis = Literal["arithmetic", "semantic", "uncertain"]


# ── Result types ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ColumnSpec:
    index: int
    label: str                 # user-facing label (real header, or an explicit "[unidentified …]")
    header: str                # original header text, "" if none
    role: ColumnRole
    identified: bool           # True when a real header was found
    has_amounts: bool
    period: str | None = None
    source_col: int | None = None


@dataclass(frozen=True)
class Component:
    row: TableRow
    sign: int                  # +1 added, -1 deducted


@dataclass(frozen=True)
class AggregateRelation:
    row: TableRow
    components: tuple[Component, ...]
    mode: RelationMode
    basis: RelationBasis
    matched_columns: tuple[int, ...]
    checked_columns: tuple[int, ...]
    basis_note: str = ""       # why this formula was accepted (traceability)
    # Components whose sign no reconciling column could test: each is 0 / blank in
    # every column where the formula reconciles, so "+" and "−" fit equally well.
    unpinned: tuple[str, ...] = ()
    # The row was classified as an aggregate from its format alone (bold / unlabelled),
    # with no "Total"-like label.  Findings against such a formula default to "needs
    # review", never HIGH — the structure is the plausible source of the mismatch.
    inferred: bool = False

    @property
    def low_confidence(self) -> bool:
        """Formula never confirmed on a column that can tell it apart from alternatives.

        Findings against it are "structure unresolved — needs review", not HIGH.
        """
        return self.basis == "semantic" or bool(self.unpinned) or self.inferred

    @property
    def confirmed(self) -> bool:
        """Reconciled exactly in at least one column."""
        return self.basis == "arithmetic"

    @property
    def checkable(self) -> bool:
        """Reliable enough to raise casting errors against."""
        return self.basis != "uncertain"

    @property
    def formula(self) -> str:
        if not self.components:
            return f"{_row_name(self.row)} = (no component rows)"
        parts: list[str] = []
        shown = self.components[:_FORMULA_MAX_TERMS]
        for i, comp in enumerate(shown):
            name = _row_name(comp.row)
            if i == 0:
                parts.append(f"−{name}" if comp.sign < 0 else name)
            else:
                parts.append(f"{'−' if comp.sign < 0 else '+'} {name}")
        if len(self.components) > len(shown):
            parts.append(f"… (+{len(self.components) - len(shown)} more rows)")
        return f"{_row_name(self.row)} = {' '.join(parts)}"


@dataclass(frozen=True)
class LedgerBalance:
    """Debit / credit columns of a ledger-style table and whether they balance."""
    debit_column: int
    credit_column: int
    debit_total: Decimal
    credit_total: Decimal
    source: str                # "declared total row '…'" or "sum of N rows"
    trial_balance: bool        # the table says it is a trial balance → must balance
    total_row: TableRow | None = None

    @property
    def difference(self) -> Decimal:
        return self.debit_total - self.credit_total


@dataclass(frozen=True)
class TableStructure:
    columns: tuple[ColumnSpec, ...]
    amount_columns: tuple[int, ...]        # checked vertically (value + total columns)
    component_columns: tuple[int, ...]     # combined across to form the derived column
    total_column: int | None               # established derived (row-total / difference) column
    relations: tuple[AggregateRelation, ...]
    treated_as_detail: tuple[int, ...]     # row indices: looked like subtotals but did not reconcile
    warnings: tuple[str, ...] = field(default=())
    column_signs: tuple[int, ...] = field(default=())   # aligned with component_columns
    ledger: LedgerBalance | None = None

    def column_label(self, index: int) -> str:
        for c in self.columns:
            if c.index == index:
                return c.label
        return f"[unidentified column {index + 1}]"

    def column_sign(self, index: int) -> int:
        for c, s in zip(self.component_columns, self.signs):
            if c == index:
                return s
        return 1

    @property
    def signs(self) -> tuple[int, ...]:
        return self.column_signs or tuple(1 for _ in self.component_columns)

    @property
    def checked_relations(self) -> tuple[AggregateRelation, ...]:
        return tuple(r for r in self.relations if r.checkable)

    def relation_for(self, row_index: int) -> AggregateRelation | None:
        for rel in self.relations:
            if rel.row.index == row_index:
                return rel
        return None

    def horizontal_formula(self) -> str:
        if self.total_column is None:
            return ""
        parts: list[str] = []
        for i, (c, s) in enumerate(zip(self.component_columns, self.signs)):
            name = self.column_label(c)
            if i == 0:
                parts.append(f"−{name}" if s < 0 else name)
            else:
                parts.append(f"{'−' if s < 0 else '+'} {name}")
        return f"{self.column_label(self.total_column)} = {' '.join(parts)}"


# ── Public entry point ────────────────────────────────────────────────────────

def analyze_structure(
    table: FinancialTable,
    *,
    abs_tolerance: Decimal = ABS_TOLERANCE,
) -> TableStructure:
    warnings: list[str] = []
    columns = _resolve_columns(table, warnings)
    period_col = _period_column(table, columns)
    if period_col is not None:
        # A column of years (2025 / 2024 …) labels the rows; it is not an amount.
        columns = [replace(c, role="text") if c.index == period_col else c for c in columns]
    by_index = {c.index: c for c in columns}

    amount_cols = tuple(
        c.index for c in columns if c.role in ("value", "total") and c.has_amounts
    )
    value_cols = tuple(c.index for c in columns if c.role == "value" and c.has_amounts)

    amount_rows = [
        r for r in table.rows
        if r.kind.is_amount_row and any(_val(r, c) is not None for c in amount_cols)
    ]

    total_col, component_cols, col_signs = _resolve_total_column(
        table, by_index, amount_cols, value_cols, amount_rows, abs_tolerance, warnings,
    )

    names = {c.index: c.label for c in columns}
    deduct = _deduction_rule(table)
    periods = _split_periods(table, amount_rows, period_col)
    if periods is None:
        relations, treated = _resolve_relations(
            amount_rows, amount_cols, abs_tolerance, warnings, names, deduct=deduct,
        )
    else:
        # Rows of two periods are interleaved (entity 2025, entity 2024, …, Total 2025,
        # Total 2024).  Each period's totals are reconciled only against rows of the
        # same period — never summed across periods.
        relations, treated = [], []
        for period, rows in periods.items():
            rels, tr = _resolve_relations(
                rows, amount_cols, abs_tolerance, warnings, names, deduct=deduct,
            )
            relations += [replace(r, basis_note=f"{r.basis_note} (period {period} rows only)")
                          for r in rels]
            treated += tr
        relations.sort(key=lambda r: r.row.index)
        warnings.append(
            f"Rows alternate between periods ({', '.join(periods)}); each period's totals "
            "were reconciled only against the rows of the same period."
        )

    if not any(r.kind.is_aggregate for r in amount_rows) and not relations and amount_rows:
        warnings.append(
            "No total or subtotal rows were recognised in this table, so vertical "
            "(column) casting was not applied."
        )

    ledger = _resolve_ledger(table, columns, amount_rows, abs_tolerance, warnings)
    _flag_suspense(table, amount_cols, warnings, names)

    return TableStructure(
        columns=tuple(columns),
        amount_columns=amount_cols,
        component_columns=component_cols,
        total_column=total_col,
        relations=tuple(relations),
        treated_as_detail=tuple(treated),
        warnings=tuple(warnings),
        column_signs=col_signs,
        ledger=ledger,
    )


def cell_ref(table: FinancialTable, structure: TableStructure, row: TableRow, col: int) -> str | None:
    """Spreadsheet reference such as "Balance Sheet!C8", when known."""
    spec = next((c for c in structure.columns if c.index == col), None)
    if row.source_row is None or spec is None or spec.source_col is None:
        return None
    ref = f"{_col_letter(spec.source_col)}{row.source_row}"
    return f"{table.sheet}!{ref}" if table.sheet else ref


# ── Sections and periods ─────────────────────────────────────────────────────

_YEAR = re.compile(r"(?<!\d)(19[5-9]\d|20\d\d)(?!\d)")
_YEAR_ONLY = re.compile(r"\s*(19[5-9]\d|20\d\d)\s*")


def _is_heading(row: TableRow) -> bool:
    """A caption row with no amounts, e.g. "Liabilities:" or "The cost"."""
    return bool((row.label or "").strip()) and all(
        c.value is None and not (c.raw_text or "").strip() for c in row.cells
    )


def _deduction_rule(table: FinancialTable) -> Callable[[TableRow], bool]:
    """Whether a row is deducted in a *net* total, using its own label or its section.

    An unlabelled subtotal under "Liabilities:" is the liabilities total, so in
    "Net assets" it is deducted — the label alone ("") cannot say so.
    """
    section: dict[int, str] = {}
    current = ""
    for r in sorted(table.rows, key=lambda r: r.index):
        if _is_heading(r):
            current = r.label
        else:
            section[r.index] = current

    def deduct(row: TableRow) -> bool:
        if is_deduction_label(row.label):
            return True
        own = (row.label or "").strip()
        heading = section.get(row.index, "")
        # the section decides for unlabelled rows and for rows inside an obligations block
        return bool(heading) and is_liability_label(heading) and (
            not own or not is_profit_type(own)
        )
    return deduct


def _period_column(table: FinancialTable, columns: list[ColumnSpec]) -> int | None:
    """A column whose cells are only years, repeating (2025, 2024, 2025, 2024 …)."""
    for c in columns:
        if c.role not in ("value", "total"):
            continue
        texts = [
            (r.cells[c.index].raw_text or "").strip()
            for r in table.rows if c.index < len(r.cells)
        ]
        texts = [t for t in texts if t and t not in ("-", "–", "—")]
        if len(texts) < 4 or not all(_YEAR_ONLY.fullmatch(fold_digits(t)) for t in texts):
            continue
        counts: dict[str, int] = {}
        for t in texts:
            counts[fold_digits(t).strip()] = counts.get(fold_digits(t).strip(), 0) + 1
        if len(counts) >= 2 and min(counts.values()) >= 2:
            return c.index
    return None


def _row_period(row: TableRow, period_col: int | None) -> str | None:
    if period_col is not None and period_col < len(row.cells):
        m = _YEAR_ONLY.fullmatch(fold_digits(row.cells[period_col].raw_text or ""))
        if m:
            return m.group(1)
    years = set(_YEAR.findall(fold_digits(row.label or "")))
    return years.pop() if len(years) == 1 else None


def _split_periods(
    table: FinancialTable, amount_rows: list[TableRow], period_col: int | None,
) -> dict[str, list[TableRow]] | None:
    """Rows grouped by period when the table interleaves periods row by row.

    Recognised only when nearly every amount row carries a year (from a year
    column or its label), at least two periods have their own total row and
    detail rows of at least two periods are present — a roll-forward whose
    opening / closing lines merely mention a date does not qualify.  An entity
    name printed only on the first row of its pair is carried down to the next.
    """
    if not amount_rows:
        return None
    tags = {r.index: _row_period(r, period_col) for r in amount_rows}
    tagged = [r for r in amount_rows if tags[r.index]]
    if len(tagged) < max(4, 0.8 * len(amount_rows)):
        return None
    aggs = [r for r in amount_rows if r.kind.is_aggregate]
    details = [r for r in amount_rows if not r.kind.is_aggregate]
    agg_periods = {tags[r.index] for r in aggs}
    detail_periods = {tags[r.index] for r in details} - {None}
    if None in agg_periods or len(agg_periods) < 2 or len(detail_periods) < 2:
        return None

    by_index = {r.index: r for r in table.rows}
    groups: dict[str, list[TableRow]] = {}
    for r in amount_rows:
        period = tags[r.index]
        if period is None:
            continue
        if not (r.label or "").strip():
            above = by_index.get(r.index - 1)
            if above is not None and (above.label or "").strip():
                r = replace(r, label=above.label)          # name printed once per pair
        groups.setdefault(period, []).append(r)
    return dict(sorted(groups.items(), reverse=True))      # latest period first


# ── Columns ───────────────────────────────────────────────────────────────────

def _resolve_columns(table: FinancialTable, warnings: list[str]) -> list[ColumnSpec]:
    n = max((len(r.cells) for r in table.rows), default=0)
    has_amounts = [
        any(
            r.kind.is_amount_row and i < len(r.cells) and r.cells[i].value is not None
            for r in table.rows
        )
        for i in range(n)
    ]

    infos: list[ColumnInfo | None]
    if table.columns and len(table.columns) == n:
        infos = list(table.columns)
        headers = [c.header for c in table.columns]
    else:
        infos = [None] * n
        hdrs = [h.strip() for h in table.headers]
        if len(hdrs) == n:
            headers = hdrs
        elif len(hdrs) == n + 1:
            headers = hdrs[1:]             # first header belongs to the label column
        else:
            headers = [""] * n
            if hdrs and any(has_amounts):
                warnings.append(
                    f"The table has {len(hdrs)} column header(s) for {n} amount column(s); "
                    "headers could not be matched to columns, so columns are reported "
                    "by position only."
                )

    specs: list[ColumnSpec] = []
    for i in range(n):
        header = headers[i] if i < len(headers) else ""
        info = infos[i]
        if info is not None:
            role: ColumnRole = info.role
        elif not has_amounts[i]:
            role = "text"
        elif is_note_header(header):
            role = "note"
        elif is_percent_header(header):
            role = "percent"
        elif is_total_header(header):
            role = "total"
        else:
            role = "value"
        if not has_amounts[i] and role in ("value", "total"):
            role = "text"

        identified = bool(header)
        label = header if identified else f"[unidentified column {i + 1}]"
        if not identified and role in ("value", "total"):
            warnings.append(
                f"Amount column {i + 1} has no identifiable header; findings in it are "
                f"labelled '{label}'."
            )
        specs.append(ColumnSpec(
            index=i,
            label=label,
            header=header,
            role=role,
            identified=identified,
            has_amounts=has_amounts[i],
            period=info.period if info else None,
            source_col=info.source_col if info else None,
        ))
    return specs


def _resolve_total_column(
    table, by_index, amount_cols, value_cols, amount_rows, tol, warnings,
) -> tuple[int | None, tuple[int, ...], tuple[int, ...]]:
    """Find the column derived from the others, if any.

    * a row-total column: "Total" header, or the last of ≥3 value columns,
      equal to the sum of the other value columns;
    * a difference column: header such as "Movement", "Change", "Net", equal
      to one value column minus another (2025 − 2024, Debit − Credit).

    A formula is accepted only when it holds in at least half of the rows
    (two rows when nothing in the header suggests it).
    """
    explicit = [c for c in amount_cols if by_index[c].role == "total"]

    def agreement(target: int, comps: tuple[int, ...], signs: tuple[int, ...]) -> tuple[int, int]:
        checkable = agreeing = 0
        for row in amount_rows:
            declared = _val(row, target)
            vals = [_val(row, c) for c in comps]
            if declared is None or all(v is None for v in vals):
                continue
            checkable += 1
            computed = sum((s * v for s, v in zip(signs, vals) if v is not None), ZERO)
            if within_tolerance(computed, declared, abs_tolerance=tol):
                agreeing += 1
        return checkable, agreeing

    def accepted(checkable: int, agreeing: int, header_based: bool) -> bool:
        return checkable >= (1 if header_based else 2) and agreeing / checkable >= 0.5

    if explicit:
        candidate = explicit[-1]
        components = tuple(c for c in value_cols if c != candidate)
        if components:
            signs = tuple(1 for _ in components)
            checkable, agreeing = agreement(candidate, components, signs)
            if accepted(checkable, agreeing, True):
                return candidate, components, signs
            if checkable:
                comp_names = ", ".join(by_index[c].label for c in components)
                warnings.append(
                    f"Column '{by_index[candidate].label}' equals the sum of {comp_names} in "
                    f"only {agreeing} of {checkable} rows, so it was not treated as a "
                    "row-total column and horizontal casting was not applied."
                )
        return None, (), ()

    # Difference column: Movement = later − earlier, Net = Debit − Credit
    for candidate in reversed(value_cols):
        if not is_movement_header(by_index[candidate].header):
            continue
        others = [c for c in value_cols if c != candidate]
        best: tuple[tuple[int, int], tuple[int, int]] | None = None
        for a in others:
            for b in others:
                if a == b:
                    continue
                checkable, agreeing = agreement(candidate, (a, b), (1, -1))
                if accepted(checkable, agreeing, True) and (best is None or agreeing > best[1][1]):
                    best = ((a, b), (checkable, agreeing))
        if best is not None:
            (a, b), _ = best
            return candidate, (a, b), (1, -1)          # e.g. 2025 − 2024, Debit − Credit

    if len(value_cols) >= 3:
        candidate = value_cols[-1]
        components = tuple(value_cols[:-1])
        signs = tuple(1 for _ in components)
        checkable, agreeing = agreement(candidate, components, signs)
        if accepted(checkable, agreeing, False):
            return candidate, components, signs
    return None, (), ()


# ── Rows / relations ──────────────────────────────────────────────────────────

@dataclass
class _Candidate:
    start: int
    mode: RelationMode
    signs: tuple[int, ...]
    matched: tuple[int, ...]
    checked: tuple[int, ...]
    unpinned: tuple[TableRow, ...] = ()
    alternative: str = ""          # why a non-default sign combination was chosen


def _resolve_relations(
    amount_rows: list[TableRow],
    cols: tuple[int, ...],
    tol: Decimal,
    warnings: list[str],
    names: dict[int, str] | None = None,
    *,
    deduct: Callable[[TableRow], bool] | None = None,
) -> tuple[list[AggregateRelation], list[int]]:
    names = names or {}
    deduct = deduct or (lambda r: is_deduction_label(r.label))

    def col_names(cs) -> str:
        return ", ".join(f"'{names.get(c, f'column {c + 1}')}'" for c in cs)

    pending: list[TableRow] = []         # rows not yet absorbed by a total
    relations: list[AggregateRelation] = []
    treated: list[int] = []
    consumed: set[int] = set()
    implicit: set[int] = set()
    uncertain: set[int] = set()

    def absorb(rel: AggregateRelation, start: int) -> None:
        nonlocal pending
        relations.append(rel)
        consumed.update(c.row.index for c in rel.components)
        pending = pending[:start] + [rel.row]

    for row in amount_rows:
        if not row.kind.is_aggregate:
            sub = _implicit_subtotal(row, pending, cols, tol)
            if sub is None:
                pending.append(row)
                continue
            implicit.add(row.index)
            absorb(sub, _start_of(sub, pending))
            continue

        declared_cols = tuple(c for c in cols if _val(row, c) is not None)
        # A row the reader tagged as an aggregate but whose label is not one of
        # the aggregate-signalling kinds (Total, Grand total, Net, Gross profit,
        # Balance at, Opening / closing balance, Net position …).  "Segment
        # liabilities" / "Segment assets" fall here: the reader marked them as
        # `total` from their bold presentation, with no label evidence at all.
        # Findings on these rows apply the strict inferred-formula rules.
        format_only = (
            row.kind_evidence.startswith(("bold", "unlabelled"))
            or not _label_signals_aggregate(row.label)
        )

        if not pending or not declared_cols:
            if format_only:
                treated.append(row.index)
                pending.append(_as_detail(row))
            else:
                warnings.append(
                    f"Total row '{_row_name(row)}' has no rows above it to reconcile with; "
                    "it was not checked."
                )
                pending.append(row)
            continue

        best = _best_candidate(
            row, pending, declared_cols, tol, deduct,
            format_only=format_only,
            elimination_cols=frozenset(
                c for c in cols if is_elimination_header(names.get(c, ""))
            ),
        )
        if best is not None:
            start = _extend_over_zero_rows(pending, best.start, cols)
            # zero rows absorbed in front take "+": they add nothing either way
            signs = (1,) * (best.start - start) + best.signs
            note = (f"formula confirmed by the figures: reconciles exactly in "
                    f"{col_names(best.matched)}")
            if best.alternative:
                note += f"; {best.alternative}"
            unpinned = tuple(_row_name(r) for r in best.unpinned)
            if unpinned:
                note += (
                    f"; UNCONFIRMED: the sign of {', '.join(repr(u) for u in unpinned)} "
                    "could not be tested — it is 0 or blank in every column that "
                    "reconciles, where adding or subtracting it gives the same result"
                )
            absorb(AggregateRelation(
                row=row,
                components=tuple(Component(r, s) for r, s in zip(pending[start:], signs)),
                mode=best.mode, basis="arithmetic",
                matched_columns=best.matched, checked_columns=best.checked,
                basis_note=note, unpinned=unpinned, inferred=format_only,
            ), start)
            continue

        # A formula read from the layout alone, with no column confirming it, is only
        # credible when a single column is checked (e.g. a one-column note with a real
        # error).  When several columns are checked, an error in *every* one of them is
        # not plausible — a wrong reading of the structure is — so the row falls through
        # to "uncertain" (a warning for manual review, never a casting error).  A real
        # error in one column of a multi-column table is still found by the arithmetic
        # path, which only needs one column to confirm the formula.
        sem = _semantic_relation(row, pending, cols) if len(declared_cols) <= 1 else None
        if sem is not None:
            start, mode, note = sem
            signs = _signs(pending[start:], mode, deduct)
            absorb(AggregateRelation(
                row=row,
                components=tuple(Component(r, s) for r, s in zip(pending[start:], signs)),
                mode=mode, basis="semantic",
                matched_columns=(), checked_columns=declared_cols,
                basis_note=f"{note}; no column reconciles, so the formula comes from the "
                           "table structure",
            ), start)
            continue

        if format_only:
            treated.append(row.index)
            warnings.append(
                f"Row '{_row_name(row)}' is formatted like a subtotal but does not "
                "reconcile with the rows above it in any column; it was treated as a "
                "detail row."
            )
            pending.append(_as_detail(row))
            continue

        # Uncertain: report the most likely reading, raise no casting error, and
        # leave the open rows untouched so later totals can still use them.
        start, mode = _default_relation(row, pending, cols)
        signs = _signs(pending[start:], mode, deduct)
        rel = AggregateRelation(
            row=row,
            components=tuple(Component(r, s) for r, s in zip(pending[start:], signs)),
            mode=mode, basis="uncertain",
            matched_columns=(), checked_columns=declared_cols,
            basis_note="no candidate formula reconciles in any column and the structure "
                       "is ambiguous",
        )
        relations.append(rel)
        uncertain.add(row.index)
        warnings.append(_uncertain_warning(rel, declared_cols, names))
        pending.append(_as_detail(row))

    if relations:
        orphans = [
            r for r in amount_rows
            if r.kind == RowKind.DATA and r.index not in consumed and r.index not in implicit
        ]
        if orphans:
            names = ", ".join(f"'{_row_name(r)}'" for r in orphans[:10])
            more = f" and {len(orphans) - 10} more" if len(orphans) > 10 else ""
            warnings.append(
                f"Not included in any recognised total: {names}{more}. These rows were not "
                "cast."
            )
    return relations, treated


def _start_of(rel: AggregateRelation, pending: list[TableRow]) -> int:
    first = rel.components[0].row.index
    return next(i for i, r in enumerate(pending) if r.index == first)


_MAX_FLIPPED_SUBTOTALS = 4


def _label_signals_aggregate(label: str) -> bool:
    """The row's label itself names an aggregate.

    These labels tell the engine that an aggregate formula is *intended*: Total,
    Grand total, Subtotal, Net …, Gross profit, Profit / loss for the year,
    Balance at …, Opening / closing balance, Net assets / position.  When the
    reader tagged a row as an aggregate but *none* of these patterns matches, it
    may have confused a bold detail row for a subtotal — the inferred-formula
    rules then apply (≥ 2 components, ≥ 2 confirming non-eliminations columns).
    """
    return (
        is_total_label(label)
        or is_subtotal_label(label)
        or is_profit_type(label)
        or is_net_position_label(label)
        or is_opening_balance(label)
        or is_closing_balance(label)
        or is_cash_subtotal_label(label)
    )


def _best_candidate(
    row: TableRow, pending: list[TableRow], cols: tuple[int, ...], tol: Decimal,
    deduct: Callable[[TableRow], bool] | None = None,
    *,
    format_only: bool = False,
    elimination_cols: frozenset[int] = frozenset(),
) -> _Candidate | None:
    """The formula over the open rows that the figures support best.

    Sign combinations tried for each run of open rows: all added ("sum"), deduction
    lines subtracted ("signed", section-aware), and — for a net total — each subset
    of section subtotals subtracted.  A column only *confirms* a component's sign
    when that component is non-zero there: where it is 0 / blank, "+" and "−" give
    the same result (a degenerate column).  Among candidates that reconcile in the
    same number of columns, one whose every sign was tested wins.

    When `format_only` — the row was classified as an aggregate from its format
    alone (bold or unlabelled) with no "Total"-like label — the bar is much higher:
    the formula must have ≥ 2 components, must reconcile in ≥ 2 *confirming*
    columns, and `elimination_cols` (Eliminations, Adjustments, …) do not count as
    confirmation.  Figures in an eliminations column repeat by construction and a
    single-column match there is almost always coincidence: without this rule,
    "Segment liabilities = Segment assets" gets confirmed by the Eliminations
    column (−24,600 = −24,600) and every real column is then flagged.
    """
    deduct = deduct or (lambda r: is_deduction_label(r.label))
    net_total = is_profit_type(row.label) or is_net_position_label(row.label)
    prefer_signed = net_total
    mode_pref = {"signed": 0, "sum": 1} if prefer_signed else {"sum": 0, "signed": 1}
    best: _Candidate | None = None
    best_key = None

    for start in range(len(pending) - 1, -1, -1):
        suffix = pending[start:]
        variants: list[tuple[RelationMode, tuple[int, ...], str]] = [
            ("sum", tuple(1 for _ in suffix), ""),
        ]
        signed = _signs(suffix, "signed", deduct)
        if any(x < 0 for x in signed):
            variants.append(("signed", signed, ""))
        if net_total:
            # "Net assets = Assets total − Liabilities total" when the subtotals carry
            # no label that tells which one is deducted.
            subs = [i for i, r in enumerate(suffix) if r.kind.is_aggregate]
            if 2 <= len(subs) <= _MAX_FLIPPED_SUBTOTALS:
                for k in range(1, len(subs)):
                    for chosen in combinations(subs, k):
                        flip = tuple(-1 if i in chosen else 1 for i in range(len(suffix)))
                        if flip not in {v[1] for v in variants}:
                            names = ", ".join(repr(_row_name(suffix[i])) for i in chosen)
                            variants.append(("signed", flip, f"subtotal(s) {names} deducted"))

        for mode, signs, why in variants:
            matched, checked = _reconcile(row, suffix, signs, cols, tol)
            if not matched:
                continue
            if format_only:
                # An inferred formula needs solid confirmation.  Either every
                # tested column reconciles (exhaustive — no column tells against
                # the formula), OR at least two *non-eliminations* columns
                # reconcile.  Figures in Eliminations / Adjustments columns
                # repeat across rows by construction, so matching only there is
                # almost always coincidence.
                exhaustive = len(matched) == len(checked)
                confirming = [c for c in matched if c not in elimination_cols]
                if not exhaustive and len(confirming) < 2:
                    continue
                # A single component with no exhaustive match is "X = Y", an
                # equality rather than a footing — never infer it.
                if len(suffix) < 2 and not exhaustive:
                    continue
            unpinned = _unpinned(suffix, matched, checked)
            if why and (unpinned or len(matched) < len(checked)):
                continue          # an inferred flip must reconcile every column, fully tested
            # more reconciling columns first, then every sign tested, then the closest
            # (shortest) run, then the mode that fits the row's meaning
            key = (len(matched), not unpinned, -len(suffix), -mode_pref[mode], not why)
            if best_key is None or key > best_key:
                best_key = key
                best = _Candidate(start, mode, signs, tuple(matched), tuple(checked),
                                  unpinned=unpinned,
                                  alternative=f"{why}: the only sign combination that "
                                              "reconciles every column" if why else "")
    return best


def _unpinned(rows: list[TableRow], matched, checked) -> tuple[TableRow, ...]:
    """Rows that are non-zero in some checked column but 0 / blank in every matched one."""
    def nonzero(r, c):
        v = _val(r, c)
        return v is not None and v != ZERO
    return tuple(
        r for r in rows
        if any(nonzero(r, c) for c in checked) and not any(nonzero(r, c) for c in matched)
    )


def _reconcile(row, rows, signs, cols, tol) -> tuple[list[int], list[int]]:
    matched: list[int] = []
    checked: list[int] = []
    for c in cols:
        if _val(row, c) is None:
            continue
        vals = [(s, _val(r, c)) for r, s in zip(rows, signs)]
        if all(v is None for _, v in vals):
            continue
        checked.append(c)
        computed = sum((s * v for s, v in vals if v is not None), ZERO)
        if within_tolerance(computed, _val(row, c), abs_tolerance=tol):
            matched.append(c)
    return matched, checked


def _implicit_subtotal(
    row: TableRow, pending: list[TableRow], cols: tuple[int, ...], tol: Decimal,
) -> AggregateRelation | None:
    """A detail row that equals a run of open rows in every amount column.

    Plain sums only: a signed match can be an accounting identity rather than
    a subtotal (equity = assets − liabilities), so it is never inferred.
    """
    declared = [c for c in cols if _val(row, c) is not None]
    if len([c for c in declared if _val(row, c) != ZERO]) < 2:
        return None
    for start in range(len(pending) - 2, -1, -1):        # runs of ≥ 2 rows, closest first
        suffix = pending[start:]
        signs = tuple(1 for _ in suffix)
        matched, checked = _reconcile(row, suffix, signs, tuple(declared), tol)
        if checked and len(matched) == len(declared) == len(checked):
            sub = replace(row, kind=RowKind.SUBTOTAL,
                          kind_evidence="arithmetic (equals the rows above in every column)")
            return AggregateRelation(
                row=sub,
                components=tuple(Component(r, 1) for r in suffix),
                mode="sum", basis="arithmetic",
                matched_columns=tuple(matched), checked_columns=tuple(checked),
                basis_note="recognised as a subtotal because it equals the sum of these "
                           f"rows in all {len(matched)} amount columns",
            )
    return None


def _semantic_relation(
    row: TableRow, pending: list[TableRow], cols: tuple[int, ...],
) -> tuple[int, RelationMode, str] | None:
    """The single formula the structure allows when no column reconciles."""
    label = row.label

    # 1. Roll-forward: opening balance + movements = closing balance
    if is_closing_balance(label):
        opening = [j for j, r in enumerate(pending) if is_opening_balance(r.label)]
        if opening and len(pending) - opening[-1] >= 2:
            j = opening[-1]
            mode = _convention(pending[j + 1:], cols)
            if mode is not None:
                return j, mode, (
                    "roll-forward: opening balance + movements = closing balance"
                )
        return None

    profit = is_profit_type(label)
    net = is_net_position_label(label)
    explicit_total = (is_total_label(label) or is_subtotal_label(label)) and not (profit or net)

    # 2. Block total over plain detail rows (nothing else is open).  Applies to any
    #    total that is not a result / net line — the reader marked it as a total
    #    (label, or the VLM's reading of the layout), and plain rows leave one reading.
    #    The block is the run of detail rows after the last total still open — e.g. the
    #    lines under "Liabilities:" once the assets subtotal is closed.
    last_agg = max((j for j, r in enumerate(pending) if r.kind.is_aggregate), default=-1)
    block = pending[last_agg + 1:]
    if not (profit or net) and len(block) >= 2:
        deductions = [r for r in block if is_deduction_label(r.label)]
        mixed = 0 < len(deductions) < len(block)
        if not mixed or _convention(block, cols) == "sum":
            return last_agg + 1, "sum", "block total: the sum of every detail row in the block"
        return None

    # 3. Total named after the totals it adds ("Total equity and liabilities")
    if explicit_total and pending and all(r.kind.is_aggregate for r in pending):
        wanted = {w for w in norm(label).split() if w not in _STOP_WORDS}
        named = [
            j for j, r in enumerate(pending)
            if wanted & {w for w in norm(r.label).split() if w not in _STOP_WORDS}
        ]
        if len(named) >= 2 and named == list(range(named[0], len(pending))):
            return named[0], "sum", "total of the totals named in its label"
        return None

    # 4. Result line / net position: previous result ± the lines since it
    if profit or net:
        prev = [j for j, r in enumerate(pending) if r.kind.is_aggregate]
        start = prev[-1] if prev else 0
        lines = pending[start + 1:] if prev else pending
        if not lines or any(r.kind.is_aggregate for r in lines):
            return None
        if not any(is_deduction_label(r.label) for r in lines):
            return None                 # nothing tells which lines are deducted
        mode = _convention(lines, cols)
        if mode is None or len(pending) - start < 2:
            return None
        what = "result line" if profit else "net position"
        return start, mode, (
            f"{what}: {'previous result ± ' if prev else ''}the lines above it, with "
            "deduction lines " + ("as presented (negative)" if mode == "sum" else "subtracted")
        )
    return None


def _convention(rows: list[TableRow], cols: tuple[int, ...]) -> RelationMode | None:
    """How deduction lines are presented: negative (sum) or positive (signed).

    None when the presentation is inconsistent, so the sign cannot be inferred.
    """
    signs = {
        v > ZERO
        for r in rows if is_deduction_label(r.label)
        for c in cols if (v := _val(r, c)) is not None and v != ZERO
    }
    if not signs:
        return "sum"
    if signs == {False}:
        return "sum"
    if signs == {True}:
        return "signed"
    return None


def _uncertain_warning(rel: AggregateRelation, cols: tuple[int, ...], names: dict[int, str]) -> str:
    diffs: list[str] = []
    for c in cols:
        values = [(comp.sign, _val(comp.row, c)) for comp in rel.components]
        if all(v is None for _, v in values):
            continue
        computed = sum((s * v for s, v in values if v is not None), ZERO)
        declared = _val(rel.row, c)
        diffs.append(
            f"'{names.get(c, f'column {c + 1}')}': declared {_fmt(declared)}, "
            f"computed {_fmt(computed)}"
        )
    detail = f" Under that formula — {'; '.join(diffs)}." if diffs else ""
    return (
        f"Could not determine reliably which rows make up '{_row_name(rel.row)}' or with "
        f"which signs (no candidate formula reconciles in any column). Most likely "
        f"formula: {rel.formula}.{detail} Not reported as a casting error — verify this "
        "total manually."
    )


def _default_relation(
    row: TableRow, pending: list[TableRow], cols: tuple[int, ...],
) -> tuple[int, RelationMode]:
    """Most likely reading when nothing reconciles (shown in the uncertain warning)."""
    trailing_start = len(pending)
    while trailing_start > 0 and pending[trailing_start - 1].kind == RowKind.DATA:
        trailing_start -= 1

    if is_profit_type(row.label) or is_net_position_label(row.label):
        start = 0
        for j in range(len(pending) - 1, -1, -1):
            if pending[j].kind.is_aggregate:
                start = j
                break
        else:
            start = trailing_start if trailing_start < len(pending) else 0
        return start, _convention(pending[start:], cols) or "signed"

    if trailing_start < len(pending):
        return trailing_start, "sum"

    wanted = {w for w in norm(row.label).split() if w not in _STOP_WORDS}
    named = [
        j for j, r in enumerate(pending)
        if wanted & {w for w in norm(r.label).split() if w not in _STOP_WORDS}
    ]
    if len(named) >= 2 and named == list(range(named[0], len(pending))):
        return named[0], "sum"
    return 0, "sum"


def _extend_over_zero_rows(pending: list[TableRow], start: int, cols: tuple[int, ...]) -> int:
    """Absorb immediately preceding detail rows that are zero/blank in every column."""
    while start > 0:
        prev = pending[start - 1]
        if prev.kind != RowKind.DATA:
            break
        if any((v := _val(prev, c)) is not None and v != ZERO for c in cols):
            break
        start -= 1
    return start


def _signs(
    rows: list[TableRow], mode: RelationMode,
    deduct: Callable[[TableRow], bool] | None = None,
) -> tuple[int, ...]:
    if mode == "sum":
        return tuple(1 for _ in rows)
    deduct = deduct or (lambda r: is_deduction_label(r.label))
    return tuple(-1 if deduct(r) else 1 for r in rows)


def _as_detail(row: TableRow) -> TableRow:
    return replace(row, kind=RowKind.DATA)


# ── Ledger tables (debit / credit) and suspense balances ─────────────────────

def _resolve_ledger(
    table: FinancialTable,
    columns: list[ColumnSpec],
    amount_rows: list[TableRow],
    tol: Decimal,
    warnings: list[str],
) -> LedgerBalance | None:
    debit = [c.index for c in columns if c.has_amounts and is_debit_header(c.header)]
    credit = [c.index for c in columns if c.has_amounts and is_credit_header(c.header)]
    if len(debit) != 1 or len(credit) != 1:
        return None
    d, c = debit[0], credit[0]
    context = norm(" ".join(filter(None, (table.title, table.subtitle, table.sheet))))
    is_tb = any(norm(k) in context for k in _TRIAL_BALANCE)

    totals = [
        r for r in amount_rows
        if r.kind.is_aggregate and _val(r, d) is not None and _val(r, c) is not None
    ]
    if totals:
        row = totals[-1]
        ledger = LedgerBalance(d, c, _val(row, d), _val(row, c),
                               f"declared total row '{_row_name(row)}'", is_tb, row)
    else:
        rows = [r for r in amount_rows if r.kind == RowKind.DATA]
        if len(rows) < 2:
            return None
        ledger = LedgerBalance(
            d, c,
            sum((_val(r, d) or ZERO for r in rows), ZERO),
            sum((_val(r, c) or ZERO for r in rows), ZERO),
            f"sum of {len(rows)} account rows (the table has no total row)", is_tb,
        )

    if not within_tolerance(ledger.debit_total, ledger.credit_total, abs_tolerance=tol):
        if not is_tb:
            more = "credits exceed debits" if ledger.difference < ZERO else "debits exceed credits"
            warnings.append(
                f"Debits ({_fmt(ledger.debit_total)}) and credits ({_fmt(ledger.credit_total)}) "
                f"differ by {_fmt(abs(ledger.difference))} — {more} ({ledger.source}). Not "
                "reported as a "
                "casting error because the table is not identified as a trial balance — "
                "investigate if this schedule should balance."
            )
    return ledger


def _flag_suspense(
    table: FinancialTable, cols: tuple[int, ...], warnings: list[str], names: dict[int, str],
) -> None:
    for row in table.rows:
        if not row.kind.is_amount_row:
            continue
        texts = [row.label] + [c.raw_text for c in row.cells if c.value is None and c.raw_text]
        if not any(is_suspense_label(t) for t in texts):
            continue
        amounts = [(c, v) for c in cols if (v := _val(row, c)) is not None and v != ZERO]
        if not amounts:
            continue
        shown = ", ".join(f"{_fmt(v)} in '{names.get(c, c + 1)}'" for c, v in amounts)
        warnings.append(
            f"Suspense / unmapped balance: '{_row_name(row)}' carries {shown}. It is not "
            "mapped to a financial-statement caption — investigate and clear it before "
            "relying on the totals."
        )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _val(row: TableRow, col: int) -> Decimal | None:
    return row.cells[col].value if col < len(row.cells) else None


def _fmt(value: Decimal | None) -> str:
    return "—" if value is None else f"{money(value):,}"


def _row_name(row: TableRow) -> str:
    return row.label.strip() or f"[unlabelled row {row.index + 1}]"


def _col_letter(col0: int) -> str:
    n, out = col0 + 1, ""
    while n:
        n, rem = divmod(n - 1, 26)
        out = chr(65 + rem) + out
    return out
