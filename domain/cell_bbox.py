"""
Layer: Domain — narrow a row-wide bbox to the x-range of each column.

The VLM sometimes gives one bounding box that spans the whole row instead of one
per cell: on page 8 of the Amiantit statements the "Weighted average number of
shares" row carries ``bbox=[160, 1273, 1065, 1289]`` for both the 2025 and the
2024 cells.  If OCR is then compared against that full bbox, the only digits it
reads in that span are the note number "16" and the "OCR value" of the finding
becomes the whole row text, "Basic ('000 shares) 16".

This module splits such shared bboxes column by column.  The rule is deliberately
simple: use OCR word positions on the page to find out where each column's
headers and figures sit, derive one x-range per column, then crop each shared
bbox to its own column.  If the x-ranges cannot be decided (no OCR, too few
hits, columns overlap) the bboxes are left as they are — a conservative fallback
that only ever gives up, never widens a cell.

Cells are mutated in place (dataclasses.replace).  Pure Python; no external
dependencies.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import replace
from decimal import Decimal

from .models import Bbox, FinancialTable, OcrPage, OcrWord, TableRow
from .money import parse_amount
from .normalize import fold_digits
from .structure import TableStructure

_MIN_SHARED = 2      # a row is "row-wide" only when ≥ 2 amount cells share the same bbox
_MIN_HITS = 2        # need at least 2 figure hits per column to place it reliably


def narrow_bbox_per_column(
    row: TableRow, structure: TableStructure, pages: Mapping[int, OcrPage],
    table: FinancialTable | None = None,
) -> tuple:
    """Return the row's cells with each shared row-wide bbox cropped to its column.

    Cells whose bbox is already column-specific, cells of non-amount columns,
    and rows where OCR cannot locate at least two columns are returned unchanged.
    The row itself (frozen) is not modified.  Column x-ranges are derived from
    every amount row in `table` (positional evidence); a single row rarely has
    enough hits on its own.
    """
    shared = _shared_bboxes(row, structure)
    if not shared:
        return row.cells
    page_number = next((c.page for c in row.cells if c.page), None)
    page = pages.get(page_number) if page_number else None
    if page is None or not page.words:
        return row.cells
    sample_rows = table.rows if table is not None else (row,)
    xranges = _column_xranges(sample_rows, page, structure)
    if not xranges:
        return row.cells
    new_cells = list(row.cells)
    for bbox, positions in shared.items():
        for pos in positions:
            xr = xranges.get(pos)
            if xr is None:
                continue
            x1, y1, x2, y2 = bbox
            left, right = xr
            new_bbox: Bbox = (max(x1, left), y1, min(x2, right), y2)
            new_cells[pos] = replace(new_cells[pos], bbox=new_bbox)
    return tuple(new_cells)


# ── Internals ────────────────────────────────────────────────────────────────

def _shared_bboxes(row: TableRow, structure: TableStructure) -> dict[Bbox, list[int]]:
    """Positions grouped by the bbox they share, only for amount cells."""
    amount_positions = {
        c.index for c in structure.columns if c.role in ("value", "total") and c.has_amounts
    }
    groups: dict[Bbox, list[int]] = {}
    for pos, cell in enumerate(row.cells):
        if pos not in amount_positions or cell.bbox is None:
            continue
        groups.setdefault(cell.bbox, []).append(pos)
    return {b: p for b, p in groups.items() if len(p) >= _MIN_SHARED}


def _column_xranges(
    rows, page: OcrPage, structure: TableStructure,
) -> dict[int, tuple[int, int]] | None:
    """For each amount column, the x-range inferred from where its figures land in OCR.

    The approach: for every table cell whose value OCR can locate on this page,
    record the x-centre of the matching OCR word.  When enough cells of a column
    have been located, keep the interval covering them (plus a small margin).
    Degenerate columns (< `_MIN_HITS`) are skipped; if two columns overlap, the
    x-ranges are discarded altogether (unsafe to split).
    """
    hits: dict[int, list[int]] = {}
    for r in rows:
        for pos, cell in enumerate(r.cells):
            column_role = next(
                (c.role for c in structure.columns if c.index == pos), None,
            )
            if column_role not in ("value", "total") or cell.value is None:
                continue
            for w in page.words:
                if _word_matches(w, cell.value):
                    xc = (w.bbox[0] + w.bbox[2]) // 2
                    hits.setdefault(pos, []).append(xc)
                    break
    reliable = {pos: xs for pos, xs in hits.items() if len(xs) >= _MIN_HITS}
    if len(reliable) < 2:
        return None
    ranges: dict[int, tuple[int, int]] = {}
    for pos, xs in reliable.items():
        xs.sort()
        span = xs[-1] - xs[0]
        margin = max(20, span // 2)
        ranges[pos] = (xs[0] - margin, xs[-1] + margin)
    # Reject if any two columns overlap — we cannot split safely.
    ordered = sorted(ranges.items(), key=lambda kv: kv[1][0])
    for (_, a), (_, b) in zip(ordered, ordered[1:]):
        if a[1] >= b[0]:
            return None
    return ranges


_DIGITS = re.compile(r"\d")


def _word_matches(word: OcrWord, value: Decimal) -> bool:
    """OCR word carries the same figure as `value` (same amount, or same digit run)."""
    if not _DIGITS.search(word.text):
        return False
    parsed = parse_amount(word.text)
    if parsed is not None and parsed == value:
        return True
    key = re.sub(r"\D", "", fold_digits(str(abs(value))))
    if not key or len(key) < 2:
        return False
    return re.sub(r"\D", "", fold_digits(word.text)) == key
