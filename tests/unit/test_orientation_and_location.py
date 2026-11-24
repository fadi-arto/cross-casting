"""Sideways scans are turned upright; findings point at the row's real page and block."""
import io
from decimal import Decimal
from unittest.mock import patch

from PIL import Image

from adapters.ocr_tesseract import TesseractOcr
from domain.models import Cell, FinancialTable, PageImage, RowKind, TableRow
from engine.runner import row_page, row_section


def _png(w, h):
    b = io.BytesIO(); Image.new("RGB", (w, h), "white").save(b, "PNG"); return b.getvalue()


PAGE = PageImage(55, "image/png", _png(100, 200), 100, 200)


def _osd(rotate, conf):
    return f"Page number: 0\nOrientation in degrees: 270\nRotate: {rotate}\nOrientation confidence: {conf}\n"


def test_sideways_page_is_turned_upright():
    with patch("adapters.ocr_tesseract.pytesseract.image_to_osd", return_value=_osd(90, 12.9)):
        out = TesseractOcr().orient(PAGE)
    assert (out.width, out.height, out.number) == (200, 100, 55)


def test_upright_low_confidence_or_failure_leave_page_unchanged():
    for ret in (_osd(0, 15.0), _osd(90, 1.2)):
        with patch("adapters.ocr_tesseract.pytesseract.image_to_osd", return_value=ret):
            assert TesseractOcr().orient(PAGE) is PAGE
    with patch("adapters.ocr_tesseract.pytesseract.image_to_osd", side_effect=RuntimeError("no text")):
        assert TesseractOcr().orient(PAGE) is PAGE


def _row(i, label, values, page, kind=RowKind.DATA):
    cells = tuple(Cell(Decimal(v) if v else None, v, i, c, page=page) for c, v in enumerate(values))
    return TableRow(i, label, cells, kind)


# PPE note: 2025 on page 55, comparative 2024 joined from page 56.
TABLE = FinancialTable("26. PPE", ("Land", "Total"), (
    _row(0, "The cost", ["", ""], 55),
    _row(1, "Balance as on 1 January, 2025", ["536194", "1913704"], 55),
    _row(2, "Currency translation adjustments", ["", "3135"], 55),
    _row(3, "Accumulated depreciation and impairment:", ["", ""], 55),
    _row(4, "Currency translation adjustments", ["", "3021"], 55),
    _row(5, "Balance as on 1 January, 2024", ["531624", "1898681"], 56, RowKind.SUBTOTAL),
    _row(6, "Additions during the year", ["", "14360"], 56),
    _row(7, "Currency translation adjustments", ["", "-3869"], 56),
), page=55)


def test_row_keeps_its_own_page_after_join():
    assert row_page(TABLE, 7) == 56 and row_page(TABLE, 2) == 55 and row_page(TABLE, None) == 55


def test_repeated_label_is_disambiguated_by_section():
    assert row_section(TABLE, 2) == "The cost"
    assert row_section(TABLE, 4) == "Accumulated depreciation and impairment:"
    assert row_section(TABLE, 7) == "Balance as on 1 January, 2024"
    assert row_section(TABLE, None) is None
