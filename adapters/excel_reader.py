"""
Layer: Adapters — openpyxl Excel reader.

Reads XLSX files and produces FinancialTable domain objects without calling
any LLM or OCR. All values come directly from cached cell values.

Table structure recognition
───────────────────────────
Every worksheet row is classified before anything is summed:

  TITLE / SUBTITLE   text rows above the column headings
  HEADER             the column-heading row(s): text such as "FY2024" / "Total",
                     or bare years (2024, "2025"); consecutive heading rows merge
  SECTION_HEADER     a text-only row between amount rows ("Assets", "Equity")
  DATA / SUBTOTAL /  amount rows; the kind comes from the label ("Total …",
  TOTAL              "Gross profit", "Net …"), else from bold formatting —
                     the structure analyser then confirms it against the figures
  EMPTY              blank row inside a table (blank rows no longer split a
                     statement into separate tables)
  FOOTNOTE           text after the last amount row, or "Note: …", "* …"

A new table starts at each heading row. Column headers are kept per column
(never the sheet title, never a bare index) together with the period, currency,
scale and display precision found in the header or number format.  Each row
keeps its worksheet row number and each column its worksheet column, so every
finding can be traced back to a cell such as "Balance Sheet!C8".
"""
from __future__ import annotations

import hashlib
import io
import re
from dataclasses import dataclass, field
from decimal import Decimal

import openpyxl
from openpyxl.cell.cell import MergedCell
from openpyxl.worksheet.worksheet import Worksheet

from domain.labels import (
    classify_label,
    extract_currency,
    extract_period,
    extract_scale,
    is_balance_label,
    is_closing_balance,
    is_footnote_text,
    is_label_header,
    is_note_header,
    is_opening_balance,
    is_percent_header,
    is_total_header,
    is_year_like,
)
from domain.models import Cell, ColumnInfo, ColumnRole, FinancialTable, RenderedDocument, RowKind, TableRow
from domain.money import parse_financial_value, to_decimal

_PLACEHOLDERS = frozenset({"-", "—", "–", "--", "nil", "n/a", "na", "—  ", "لا يوجد"})


def read_excel(content: bytes, source_file: str = "") -> list[FinancialTable]:
    wb = openpyxl.load_workbook(
        filename=io.BytesIO(content),
        data_only=True,  # read cached formula values, not formula strings
        read_only=False,
    )
    tables: list[FinancialTable] = []
    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        tables.extend(_extract_from_sheet(ws, sheet_name, source_file))
    return tables


def excel_fingerprint(content: bytes) -> bytes:
    return hashlib.sha256(content).digest()


# ── Raw worksheet model ───────────────────────────────────────────────────────

@dataclass
class _RawCell:
    value: object               # value of this cell (merged non-anchor cells → None)
    span_text: str              # text of the merged range covering it (headers only)
    bold: bool
    number_format: str

    @property
    def text(self) -> str:
        return str(self.value).strip() if self.value is not None else ""

    @property
    def is_blank(self) -> bool:
        return self.text == ""

    @property
    def is_placeholder(self) -> bool:
        """'-', '—', 'nil', 'n/a': an amount position shown as empty/zero."""
        return self.text.lower() in _PLACEHOLDERS

    @property
    def amount(self) -> Decimal | None:
        v = self.value
        if v is None or isinstance(v, bool):
            return None
        if isinstance(v, (int, float, Decimal)):
            return to_decimal(v)
        if isinstance(v, str):
            return parse_financial_value(v.strip()) if v.strip() else None
        return None  # dates, times, errors…


@dataclass
class _RawRow:
    number: int                 # 1-based worksheet row
    cells: list[_RawCell]

    def cell(self, col: int) -> _RawCell:
        return self.cells[col] if col < len(self.cells) else _RawCell(None, "", False, "General")

    @property
    def is_blank(self) -> bool:
        return all(c.is_blank for c in self.cells)


@dataclass
class _Shape:
    kind: str                   # blank | amount | header | text
    label: str
    bold: bool


@dataclass
class _TableDraft:
    title_rows: list[_RawRow] = field(default_factory=list)
    header_rows: list[_RawRow] = field(default_factory=list)
    body: list[_RawRow] = field(default_factory=list)


# ── Sheet-level extraction ────────────────────────────────────────────────────

def _extract_from_sheet(ws: Worksheet, sheet_name: str, source_file: str) -> list[FinancialTable]:
    rows = _read_rows(ws)
    if not any(not r.is_blank for r in rows):
        return []

    label_col = _detect_label_col(rows)
    shapes = [_shape(r, label_col) for r in rows]
    drafts = _segment(rows, shapes)

    tables: list[FinancialTable] = []
    for draft in drafts:
        tbl = _build_table(draft, label_col, sheet_name, source_file)
        if tbl is not None:
            tables.append(tbl)
    return tables


def _read_rows(ws: Worksheet) -> list[_RawRow]:
    span: dict[tuple[int, int], str] = {}
    for rng in ws.merged_cells.ranges:
        anchor = ws.cell(rng.min_row, rng.min_col).value
        text = str(anchor).strip() if anchor is not None else ""
        for r in range(rng.min_row, rng.max_row + 1):
            for c in range(rng.min_col, rng.max_col + 1):
                span[(r, c)] = text

    rows: list[_RawRow] = []
    for r_idx, row in enumerate(ws.iter_rows(), start=1):
        cells: list[_RawCell] = []
        for c_idx, cell in enumerate(row, start=1):
            if isinstance(cell, MergedCell):
                # never copy a merged value into covered cells: that duplicated
                # titles into header positions and could double-count amounts
                cells.append(_RawCell(None, span.get((r_idx, c_idx), ""), False, "General"))
            else:
                bold = bool(cell.font and cell.font.bold)
                text = span.get((r_idx, c_idx), "")
                cells.append(_RawCell(cell.value, text, bold, cell.number_format or "General"))
        rows.append(_RawRow(r_idx, cells))
    return rows


def _detect_label_col(rows: list[_RawRow]) -> int:
    """The column holding row labels: leftmost text cell of rows that carry amounts."""
    votes: dict[int, int] = {}
    for row in rows:
        first_text = next(
            (i for i, c in enumerate(row.cells) if not c.is_blank and c.amount is None), None
        )
        if first_text is None:
            continue
        if any(c.amount is not None for c in row.cells[first_text + 1:]):
            votes[first_text] = votes.get(first_text, 0) + 1
    return max(votes, key=lambda k: (votes[k], -k)) if votes else 0


def _shape(row: _RawRow, label_col: int) -> _Shape:
    if row.is_blank:
        return _Shape("blank", "", False)
    label_cell = row.cell(label_col)
    label = label_cell.text if label_cell.amount is None else ""
    values = [c for c in row.cells[label_col + 1:] if not c.is_blank]
    bold = any(c.bold for c in row.cells if not c.is_blank)

    if values:
        texts = [c for c in values if c.amount is None and not c.is_placeholder]
        years = [c for c in values if c.amount is not None and is_year_like(c.value)]
        others = [c for c in values if c.amount is not None and not is_year_like(c.value)]
        if not texts and not years and not others:
            return _Shape("amount", label, bold)          # only nil placeholders
        if not others and (texts or years):
            # years typed as text ("2025") are headings — amounts are stored as numbers;
            # numeric years need more evidence ("Inventory | 2000 | 1950" is data)
            text_years = any(isinstance(c.value, str) for c in years)
            if texts or text_years or not label or is_label_header(label) or bold:
                return _Shape("header", label, bold)
        if others or years:
            return _Shape("amount", label, bold)
        return _Shape("text", label or _row_text(row), bold)
    if label_cell.amount is not None:
        return _Shape("amount", "", bold)
    return _Shape("text", _row_text(row), bold)


def _segment(rows: list[_RawRow], shapes: list[_Shape]) -> list[_TableDraft]:
    """Split a sheet into tables. A table starts at a heading row followed by amounts."""
    # amount_after[i]: amounts follow row i before the next heading. A heading row
    # directly above another heading (multi-row header) inherits its flag.
    amount_after = [False] * len(rows)
    seen_amount = False
    below_header_flag: bool | None = None
    for i in range(len(rows) - 1, -1, -1):
        if shapes[i].kind == "header":
            amount_after[i] = seen_amount or bool(below_header_flag)
            below_header_flag = amount_after[i]
            seen_amount = False   # amounts below belong to the nearer heading
        else:
            below_header_flag = None
            amount_after[i] = seen_amount
            if shapes[i].kind == "amount":
                seen_amount = True

    drafts: list[_TableDraft] = []
    current: _TableDraft | None = None
    loose: list[_RawRow] = []           # text/blank rows not yet attached to a table

    for row, shape in zip(rows, shapes):
        if shape.kind == "header" and amount_after[_index(rows, row)]:
            if current is not None and not any(_is_amount(r, shapes, rows) for r in current.body):
                current.header_rows.append(row)     # multi-row header
                continue
            titles: list[_RawRow] = []
            if current is not None:
                # contiguous text rows right above this heading are its title;
                # earlier trailing text stays with the previous table
                tail = current.body
                k = len(tail)
                while k > 0 and shapes[_index(rows, tail[k - 1])].kind == "text":
                    k -= 1
                titles = tail[k:]
                current.body = tail[:k]
                drafts.append(current)
            else:
                titles = [r for r in loose if not r.is_blank]
            loose = []
            current = _TableDraft(title_rows=titles, header_rows=[row])
        elif shape.kind == "amount":
            if current is None:
                current = _TableDraft(title_rows=[r for r in loose if not r.is_blank])
                loose = []
            current.body.append(row)
        else:
            if current is None:
                loose.append(row)
            else:
                current.body.append(row)
    if current is not None:
        drafts.append(current)
    return drafts


# ── Table construction ────────────────────────────────────────────────────────

def _build_table(
    draft: _TableDraft, label_col: int, sheet_name: str, source_file: str,
) -> FinancialTable | None:
    body_amount_rows = [r for r in draft.body if _row_has_amount(r, label_col)]
    if not body_amount_rows:
        return None
    warnings: list[str] = []

    max_col = max(
        (i for r in draft.header_rows + draft.body for i, c in enumerate(r.cells) if not c.is_blank),
        default=label_col,
    )
    value_cols = list(range(label_col + 1, max_col + 1))
    if not value_cols:
        return None

    headers = [_header_text(draft.header_rows, c) for c in value_cols]
    label_header = _header_text(draft.header_rows, label_col)
    if not draft.header_rows:
        warnings.append(
            f"No column-heading row was found above the figures on sheet '{sheet_name}' "
            f"(row {body_amount_rows[0].number}); columns could not be identified."
        )

    columns: list[ColumnInfo] = []
    for pos, col in enumerate(value_cols):
        header = headers[pos]
        amounts = [r.cell(col) for r in body_amount_rows if r.cell(col).amount is not None]
        pct = sum(1 for c in amounts if "%" in c.number_format)
        role: ColumnRole
        if not amounts:
            role = "text"
        elif is_note_header(header):
            role = "note"
        elif is_percent_header(header) or (amounts and pct * 2 > len(amounts)):
            role = "percent"
        elif is_total_header(header):
            role = "total"
        else:
            role = "value"
        if role in ("note", "percent"):
            warnings.append(
                f"Column {_col_letter(col)} ('{header or 'no header'}') was recognised as a "
                f"{'note-reference' if role == 'note' else 'percentage'} column and excluded "
                "from casting."
            )
        fmt = next((c.number_format for c in amounts if c.number_format != "General"), None)
        columns.append(ColumnInfo(
            index=pos,
            header=header,
            role=role,
            period=extract_period(header),
            currency=extract_currency(header, fmt),
            scale=extract_scale(header),
            decimals=_decimals(fmt),
            source_col=col,
        ))

    title_texts = [_row_text(r) for r in draft.title_rows if _row_text(r)]
    title = title_texts[0] if title_texts else sheet_name
    subtitle = " | ".join(title_texts[1:])

    rows: list[TableRow] = []

    def add(raw: _RawRow, kind: RowKind, label: str, evidence: str, with_values: bool) -> None:
        idx = len(rows)
        cells = []
        for pos, col in enumerate(value_cols):
            rc = raw.cell(col)
            raw_text = rc.text or (rc.span_text if kind == RowKind.HEADER else "")
            cells.append(Cell(
                value=rc.amount if with_values else None,
                raw_text=raw_text,
                row_index=idx,
                col_index=col,
                sheet=sheet_name,
            ))
        rows.append(TableRow(
            index=idx, label=label, cells=tuple(cells), kind=kind,
            source_row=raw.number, kind_evidence=evidence,
        ))

    for i, r in enumerate(draft.title_rows):
        if _row_text(r):
            add(r, RowKind.TITLE if i == 0 else RowKind.SUBTITLE, _row_text(r), "position", False)
    for r in draft.header_rows:
        add(r, RowKind.HEADER, r.cell(label_col).text, "heading text", False)

    last_amount = max(i for i, r in enumerate(draft.body) if _row_has_amount(r, label_col))
    # Bold only hints at a subtotal when it is used selectively
    labelled = [r for r in body_amount_rows if r.cell(label_col).text]
    bold_rows = sum(1 for r in labelled if r.cell(label_col).bold)
    bold_is_signal = bool(labelled) and bold_rows * 2 <= len(labelled)
    seen_amount = False
    for i, r in enumerate(draft.body):
        if i > last_amount and r.is_blank:
            continue                                 # trailing blank rows
        if r.is_blank:
            add(r, RowKind.EMPTY, "", "blank", False)
            continue
        label_cell = r.cell(label_col)
        label = label_cell.text if label_cell.amount is None else ""
        if not _row_has_amount(r, label_col) and not _row_has_placeholder(r, label_col):
            text = label or _row_text(r)
            if is_footnote_text(text) or i > last_amount:
                add(r, RowKind.FOOTNOTE, text, "position" if i > last_amount else "text", False)
            else:
                add(r, RowKind.SECTION_HEADER, text, "text-only row", False)
            continue
        kind, evidence = _amount_row_kind(r, label, label_col, seen_amount, bold_is_signal)
        add(r, kind, label, evidence, True)
        seen_amount = True

    return FinancialTable(
        title=title,
        headers=tuple(headers),
        rows=tuple(rows),
        page=1,
        sheet=sheet_name,
        source_file=source_file,
        subtitle=subtitle,
        label_header=label_header,
        columns=tuple(columns),
        extraction_warnings=tuple(warnings),
    )


def _amount_row_kind(
    row: _RawRow, label: str, label_col: int, seen_amount: bool, bold_is_signal: bool,
) -> tuple[RowKind, str]:
    if not label:
        return RowKind.SUBTOTAL, "unlabelled amount row"
    text_kind = classify_label(label)
    if text_kind == "total":
        return RowKind.TOTAL, "label"
    if text_kind == "subtotal":
        return RowKind.SUBTOTAL, "label"
    if is_balance_label(label) and not is_opening_balance(label) and seen_amount:
        return RowKind.TOTAL, "label (closing balance)"
    if is_closing_balance(label):
        return RowKind.TOTAL, "label"
    if bold_is_signal and row.cell(label_col).bold:
        return RowKind.SUBTOTAL, "bold formatting"
    return RowKind.DATA, "label"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _row_has_amount(row: _RawRow, label_col: int) -> bool:
    return any(c.amount is not None for c in row.cells[label_col + 1:])


def _row_has_placeholder(row: _RawRow, label_col: int) -> bool:
    return any(c.is_placeholder for c in row.cells[label_col + 1:])


def _is_amount(row: _RawRow, shapes: list[_Shape], rows: list[_RawRow]) -> bool:
    return shapes[_index(rows, row)].kind == "amount"


def _index(rows: list[_RawRow], row: _RawRow) -> int:
    return row.number - rows[0].number


def _row_text(row: _RawRow) -> str:
    return " ".join(c.text for c in row.cells if not c.is_blank and c.amount is None).strip()


def _header_text(header_rows: list[_RawRow], col: int) -> str:
    parts: list[str] = []
    for r in header_rows:
        c = r.cell(col)
        text = c.text or c.span_text
        if isinstance(c.value, float) and c.value.is_integer():
            text = str(int(c.value))
        if text and (not parts or parts[-1] != text):
            parts.append(text)
    return " ".join(parts)


_DECIMALS = re.compile(r"0\.(0+)")


def _decimals(number_format: str | None) -> int | None:
    if not number_format or number_format == "General":
        return None
    first = number_format.split(";")[0]
    m = _DECIMALS.search(first)
    if m:
        return len(m.group(1))
    return 0 if "0" in first else None


def _col_letter(col0: int) -> str:
    n, out = col0 + 1, ""
    while n:
        n, rem = divmod(n - 1, 26)
        out = chr(65 + rem) + out
    return out
