"""
OCR/VLM verification regression (Amiantit page 8, income statement):
the Note column must never become the "OCR value" of a disagreement, and
row-wide bboxes must be cropped column by column before comparison.
"""
from decimal import Decimal

from domain.cell_bbox import narrow_bbox_per_column
from domain.crosscheck import _numeric_evidence, verify_cell
from domain.models import (
    Bbox, Cell, ColumnInfo, FinancialTable, OcrPage, OcrWord, RowKind, TableRow,
)
from domain.structure import analyze_structure


def _word(text: str, x: int, y: int = 1280, w: int = 50, h: int = 16) -> OcrWord:
    return OcrWord(text, Decimal("0.9"), (x, y, x + w, y + h))


def _cell(value, raw, i, j, bbox: Bbox | None = None, page: int = 8) -> Cell:
    return Cell(
        value=None if value is None else Decimal(str(value)),
        raw_text=raw, row_index=i, col_index=j, bbox=bbox, page=page,
    )


# ── _numeric_evidence: strip row labels and lone note refs ───────────────────

class TestNumericEvidence:
    def test_row_label_is_stripped(self):
        words = [_word("Basic", 200), _word("('000", 260), _word("shares)", 320), _word("16", 820)]
        assert _numeric_evidence(words, Decimal("44512")) is None     # only the note was numeric

    def test_note_reference_is_not_evidence(self):
        words = [_word("(1.75)", 920)]                                 # a real figure
        words += [_word("16", 820)]                                    # a note number
        got = _numeric_evidence(words, Decimal("44512"))
        assert got and "(1.75)" in got and "16" not in got

    def test_dotted_note_reference_2_3_is_stripped(self):
        words = [_word("2.3", 820), _word("(241)", 920)]
        got = _numeric_evidence(words, Decimal("-241"))
        assert got == "(241)"

    def test_matching_token_is_kept_even_if_small(self):
        words = [_word("16", 820)]
        assert _numeric_evidence(words, Decimal("16")) == "16"         # the VLM value itself


# ── verify_cell: no counter-evidence → not a disagreement ────────────────────

class TestVerifyCellValueNotFound:
    def _run(self, vlm_value: str, words: list[OcrWord]) -> tuple:
        cell = _cell(vlm_value.replace(",", ""), vlm_value, 20, 1,
                     bbox=(160, 1273, 1065, 1289))
        page = OcrPage(8, tuple(words))
        v = verify_cell(cell, {8: page})
        return v.ocr_value, v.is_discrepancy

    def test_only_note_ref_in_region_is_not_a_discrepancy(self):
        words = [_word("Basic", 200), _word("('000", 260), _word("shares)", 320), _word("16", 820)]
        ocr, discrep = self._run("44,512", words)
        assert ocr is None and not discrep

    def test_real_counter_evidence_is_a_discrepancy(self):
        words = [_word("44,572", 900)]                                 # OCR read a DIFFERENT figure
        ocr, discrep = self._run("44,512", words)
        assert ocr == "44,572" and discrep


# ── narrow_bbox_per_column: row-wide → per-column ────────────────────────────

def _row(cells, kind=RowKind.DATA, index=20, label=""):
    return TableRow(index=index, label=label, cells=tuple(cells), kind=kind)


def _table_eps(bbox_2025: Bbox, bbox_2024: Bbox):
    return FinancialTable(
        title="EPS",
        headers=("Note", "2025 SR '000", "2024 SR '000"),
        rows=(
            _row([_cell("7", "7", 0, 0), _cell("691100", "691,100", 0, 1, bbox=(160, 300, 500, 316)),
                  _cell("848298", "848,298", 0, 2, bbox=(900, 300, 1100, 316))], index=0, label="Revenue"),
            _row([_cell(None, "", 1, 0), _cell("78100", "78,100", 1, 1, bbox=(160, 400, 500, 416)),
                  _cell("439836", "439,836", 1, 2, bbox=(900, 400, 1100, 416))], index=1,
                 label="Attributable to shareholders"),
            _row([_cell("16", "16", 20, 0), _cell("44512", "44,512", 20, 1, bbox=bbox_2025),
                  _cell("44332", "44,332", 20, 2, bbox=bbox_2024)],
                 index=20, label="Weighted average number of shares outstanding - Basic"),
        ),
        columns=(
            ColumnInfo(index=0, role="note", header="Note"),
            ColumnInfo(index=1, role="value", header="2025 SR '000"),
            ColumnInfo(index=2, role="value", header="2024 SR '000"),
        ),
    )


def _page_8_shares() -> OcrPage:
    """Enough OCR evidence to locate the 2025 and 2024 columns (≥ 2 hits each)."""
    words = [
        _word("Note", 820, y=200), _word("2025", 920, y=200), _word("2024", 1030, y=200),
        _word("7", 820, y=300), _word("691,100", 920, y=300), _word("848,298", 1030, y=300),
        _word("78,100", 920, y=400), _word("439,836", 1030, y=400),
        _word("16", 820, y=1280),
        _word("Basic", 200, y=1280), _word("('000", 260, y=1280), _word("shares)", 320, y=1280),
    ]
    return OcrPage(8, tuple(words))


class TestNarrowBbox:
    def test_row_wide_bbox_is_cropped_to_each_column(self):
        shared: Bbox = (160, 1273, 1065, 1289)
        tbl = _table_eps(shared, shared)
        cells = narrow_bbox_per_column(
            tbl.rows[2], analyze_structure(tbl), {8: _page_8_shares()}, tbl,
        )
        left_bbox, right_bbox = cells[1].bbox, cells[2].bbox
        assert left_bbox != shared and right_bbox != shared
        assert left_bbox[2] <= right_bbox[0]       # columns no longer overlap

    def test_cells_with_own_bboxes_are_untouched(self):
        own = (900, 1273, 1000, 1289)
        tbl = _table_eps((700, 1273, 850, 1289), own)
        cells = narrow_bbox_per_column(
            tbl.rows[2], analyze_structure(tbl), {8: _page_8_shares()}, tbl,
        )
        assert cells[2].bbox == own

    def test_no_ocr_leaves_cells_unchanged(self):
        shared: Bbox = (160, 1273, 1065, 1289)
        tbl = _table_eps(shared, shared)
        cells = narrow_bbox_per_column(tbl.rows[2], analyze_structure(tbl), {}, tbl)
        assert cells[1].bbox == shared


# ── End-to-end, through the pipeline ─────────────────────────────────────────

class TestNoteColumnSkippedInVerification:
    def test_note_cell_value_is_not_compared(self):
        """The '16' in the Note column must not generate an OCR/VLM finding."""
        from application.pipeline import CrossCastingPipeline

        tbl = _table_eps((160, 1273, 1065, 1289), (160, 1273, 1065, 1289))
        pipe = CrossCastingPipeline(None, None, None, None)
        result = pipe._validate_and_report([tbl], {8: _page_8_shares()}, "test.pdf")
        verifications = result.validation_results[0].cell_verifications
        notes = [v for v in verifications if v.cell.col_index == 0]
        assert notes == []                      # Note column: not verified, no finding
