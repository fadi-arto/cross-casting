"""Unit tests for OCR/VLM cell-level verification."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from decimal import Decimal

from domain.crosscheck import verify_cell, AGREEMENT, HUMAN_REVIEW, NOT_FOUND
from domain.models import Cell, OcrPage, OcrWord


def _word(text, x1=0, y1=0, x2=50, y2=20, conf=0.9):
    return OcrWord(text=text, confidence=Decimal(str(conf)), bbox=(x1, y1, x2, y2))


def _cell(value, raw_text, bbox=None, page=1):
    v = Decimal(str(value)) if value is not None else None
    return Cell(value=v, raw_text=raw_text, row_index=0, col_index=0, bbox=bbox, page=page)


class TestAgreement:
    def test_number_found_in_page(self):
        cell = _cell(1000, "1000", page=1)
        page = OcrPage(1, (_word("1000"),))
        result = verify_cell(cell, {1: page})
        assert result.agreed is True
        assert result.status == AGREEMENT

    def test_number_found_in_region(self):
        cell = _cell(1000, "1000", bbox=(0, 0, 60, 30), page=1)
        word = _word("1,000", x1=5, y1=5, x2=55, y2=25)
        page = OcrPage(1, (word,))
        result = verify_cell(cell, {1: page})
        assert result.agreed is True


class TestHumanReview:
    def test_no_ocr_page(self):
        cell = _cell(1000, "1000", page=1)
        result = verify_cell(cell, {})
        assert result.status == HUMAN_REVIEW
        assert result.reason == "ocr_unavailable"

    def test_ambiguous_multiple_hits(self):
        cell = _cell(500, "500", page=1)
        words = (_word("500", x1=0, y1=0), _word("500", x1=200, y1=0))
        page = OcrPage(1, words)
        result = verify_cell(cell, {1: page})
        assert result.status == HUMAN_REVIEW
        assert result.reason == "ambiguous_on_page"

    def test_value_not_found(self):
        cell = _cell(9999, "9999", page=1)
        page = OcrPage(1, (_word("1000"), _word("2000")))
        result = verify_cell(cell, {1: page})
        assert result.status == HUMAN_REVIEW
        assert result.reason == "value_not_found"


class TestNotFound:
    def test_empty_cell(self):
        cell = _cell(None, "", page=1)
        page = OcrPage(1, (_word("1000"),))
        result = verify_cell(cell, {1: page})
        assert result.status == NOT_FOUND


class TestIsDiscrepancy:
    """Only counter-evidence from OCR is a disagreement; "unverifiable" is not."""

    def _cv(self, reason, ocr, agreed=False):
        from domain.models import CellVerification
        return CellVerification(cell=None, ocr_value=ocr, vlm_value="5,383", agreed=agreed,
                                status="HUMAN_REVIEW", confidence=0, bbox=None, reason=reason)

    def test_counter_evidence_is_a_discrepancy(self):
        assert self._cv("value_not_found", "Reversal (6,383) 24,330").is_discrepancy

    def test_unverifiable_cases_are_not(self):
        assert not self._cv("value_not_found", None).is_discrepancy        # no OCR text there
        assert not self._cv("value_not_found", "Warranty provision").is_discrepancy  # no digits
        assert not self._cv("empty_cell", None).is_discrepancy
        assert not self._cv("ambiguous_on_page", None).is_discrepancy
        assert not self._cv("region", "5,383", agreed=True).is_discrepancy
