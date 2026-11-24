"""
Layer: Domain — zero external dependencies.
All core data types for the Cross-Casting audit pipeline.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Literal

Bbox = tuple[int, int, int, int]


class RowKind(str, Enum):
    """Structural role of a table row.

    Only DATA (detail), SUBTOTAL and TOTAL rows carry amounts that take part in
    casting; every other kind is kept for traceability but never summed.
    """
    DATA = "data"                      # DETAIL_ROW
    SUBTOTAL = "subtotal"              # SUBTOTAL_ROW
    TOTAL = "total"                    # TOTAL_ROW
    HEADER = "header"                  # HEADER_ROW (column headings)
    TITLE = "title"                    # TABLE_TITLE
    SUBTITLE = "subtitle"              # TABLE_SUBTITLE
    SECTION_HEADER = "section_header"  # e.g. "Assets", "Current liabilities"
    EMPTY = "empty"                    # EMPTY_ROW inside a table
    FOOTNOTE = "footnote"              # notes / sources below the figures

    @property
    def is_amount_row(self) -> bool:
        return self in (RowKind.DATA, RowKind.SUBTOTAL, RowKind.TOTAL)

    @property
    def is_aggregate(self) -> bool:
        return self in (RowKind.SUBTOTAL, RowKind.TOTAL)


# ── OCR primitives (ported from auditagent_domain.crosscheck) ─────────────────

@dataclass(frozen=True)
class OcrWord:
    text: str
    confidence: Decimal
    bbox: Bbox

    @property
    def center(self) -> tuple[Decimal, Decimal]:
        x1, y1, x2, y2 = self.bbox
        return Decimal(x1 + x2) / 2, Decimal(y1 + y2) / 2


@dataclass(frozen=True)
class OcrPage:
    number: int
    words: tuple[OcrWord, ...]


# ── Document rendering ────────────────────────────────────────────────────────

@dataclass(frozen=True)
class PageImage:
    number: int
    mime_type: str
    content: bytes = field(repr=False)
    width: int | None = None
    height: int | None = None


@dataclass(frozen=True)
class RenderedDocument:
    fingerprint: bytes
    pages: tuple[PageImage, ...]


# ── Financial table domain ────────────────────────────────────────────────────

@dataclass(frozen=True)
class Cell:
    value: Decimal | None
    raw_text: str
    row_index: int
    col_index: int
    bbox: Bbox | None = None
    page: int = 1
    sheet: str | None = None


@dataclass(frozen=True)
class TableRow:
    index: int
    label: str
    cells: tuple[Cell, ...]
    kind: RowKind = RowKind.DATA
    source_row: int | None = None   # 1-based worksheet row (Excel) for traceability
    kind_evidence: str = ""         # why this kind was assigned, e.g. "label", "bold formatting"


ColumnRole = Literal["value", "total", "note", "percent", "text"]


@dataclass(frozen=True)
class ColumnInfo:
    """Describes one position of TableRow.cells."""
    index: int                      # position in TableRow.cells
    header: str                     # original header text ("" when none was found)
    role: ColumnRole = "value"      # value = amount column; total = row-total column
    period: str | None = None       # e.g. "FY2024", "2025", "Q1"
    currency: str | None = None
    scale: str | None = None        # e.g. "'000"
    decimals: int | None = None     # display precision from the source number format
    source_col: int | None = None   # 0-based worksheet column (Excel) for traceability


@dataclass(frozen=True)
class FinancialTable:
    title: str
    headers: tuple[str, ...]
    rows: tuple[TableRow, ...]
    page: int = 1
    sheet: str | None = None
    source_file: str = ""
    subtitle: str = ""
    label_header: str = ""                      # header of the row-label column, e.g. "Account"
    columns: tuple[ColumnInfo, ...] = ()        # aligned with TableRow.cells when known
    extraction_warnings: tuple[str, ...] = ()
    continues_previous: bool = False            # VLM: table continues from the previous page


# ── Cell OCR/VLM verification ─────────────────────────────────────────────────

VerificationStatus = Literal["AGREEMENT", "DISAGREEMENT", "HUMAN_REVIEW", "NOT_FOUND"]


@dataclass(frozen=True)
class CellVerification:
    cell: Cell
    ocr_value: str | None
    vlm_value: str
    agreed: bool
    status: VerificationStatus
    confidence: Decimal
    bbox: Bbox | None
    reason: str

    @property
    def is_discrepancy(self) -> bool:
        """True only when OCR gives counter-evidence against the VLM reading.

        "Could not verify" is not a disagreement: an empty cell, a value that occurs
        several times on the page, or no OCR text at the cell's position all leave
        nothing to compare.  A discrepancy is when OCR *did* read numbers where the
        cell sits and none of them is the VLM's value.
        """
        return (
            not self.agreed
            and self.reason == "value_not_found"
            and bool(self.ocr_value)
            and any(ch.isdigit() for ch in self.ocr_value)
        )


# ── Casting errors ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class CastingError:
    """A declared amount that disagrees with the amount computed from its components.

    axis="column": vertical check — `index`/`label` identify the column and
                   `row_index`/`row_label` the total/subtotal row being checked.
    axis="row":    horizontal check — `index`/`label` identify the row and
                   `column_index`/`column_label` the row-total column.
    delta = declared − computed.
    """
    table_title: str
    axis: Literal["row", "column"]
    index: int
    label: str
    declared: Decimal | None
    computed: Decimal
    delta: Decimal
    row_index: int | None = None
    row_label: str | None = None
    column_index: int | None = None
    column_label: str | None = None
    relation: str = ""                    # e.g. "Total assets = Cash + Inventory"
    cell_ref: str | None = None           # e.g. "Balance Sheet!C8"
    evidence: tuple[str, ...] = ()        # related consequences merged into this finding
    # "low" = the formula was never confirmed on a column that can tell it apart
    # from the alternatives: "structure unresolved — needs review", not HIGH.
    confidence: Literal["high", "low"] = "high"


CrossCheckKind = Literal["row_vs_col", "row_vs_grand", "col_vs_grand", ""]


@dataclass(frozen=True)
class CrossCastingError:
    table_title: str
    description: str
    row_total: Decimal
    col_total: Decimal
    grand_total: Decimal | None
    delta: Decimal
    row_index: int | None = None          # the total/subtotal row whose block was cross-cast
    row_label: str | None = None
    check: CrossCheckKind = ""
    evidence: tuple[str, ...] = ()


# ── Validation result ─────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ValidationResult:
    table: FinancialTable
    casting_errors: tuple[CastingError, ...]
    cross_casting_errors: tuple[CrossCastingError, ...]
    cell_verifications: tuple[CellVerification, ...]
    warnings: tuple[str, ...] = ()   # extraction / interpretation warnings (not errors)
    # Arithmetic comparisons actually made (passed + failed), independent of
    # cell_verifications, which are OCR-vs-VLM checks and only exist for PDFs/images.
    casting_checks: int = 0
    cross_casting_checks: int = 0

    @property
    def total_checks(self) -> int:
        return self.casting_checks + self.cross_casting_checks

    @property
    def has_errors(self) -> bool:
        return bool(self.casting_errors or self.cross_casting_errors)

    @property
    def disagreements(self) -> tuple[CellVerification, ...]:
        return tuple(v for v in self.cell_verifications if v.is_discrepancy)


# ── Interpreted findings (LLM output) ────────────────────────────────────────

Severity = Literal["critical", "high", "medium", "low"]


@dataclass(frozen=True)
class InterpretedFinding:
    severity: Severity
    finding_type: str
    description_ar: str
    description_en: str
    table: str
    details: str


# ── Final report ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class AuditReport:
    source_file: str
    tables_processed: int
    validation_results: tuple[ValidationResult, ...]
    findings: tuple[InterpretedFinding, ...]
    summary_ar: str
    summary_en: str
    # Pages not sent to the table reader because OCR showed no financial table.
    pages_skipped: tuple[int, ...] = ()

    @property
    def total_casting_errors(self) -> int:
        return sum(len(r.casting_errors) for r in self.validation_results)

    @property
    def total_cross_casting_errors(self) -> int:
        return sum(len(r.cross_casting_errors) for r in self.validation_results)

    @property
    def total_checks(self) -> int:
        return sum(r.total_checks for r in self.validation_results)

    @property
    def is_clean(self) -> bool:
        return self.total_casting_errors == 0 and self.total_cross_casting_errors == 0
