"""Figures inside a sum that foots are confirmed; OCR separator noise is not a mismatch."""
from decimal import Decimal

from domain.casting import confirmed_cells
from domain.crosscheck import _same_figure
from domain.models import Cell, FinancialTable, OcrWord, RowKind, TableRow
from domain.structure import analyze_structure


def row(i, label, values, kind=RowKind.DATA):
    cells = tuple(Cell(Decimal(v) if v is not None else None, v or "", i, c) for c, v in enumerate(values))
    return TableRow(i, label, cells, kind)


def table(total_2025):
    return FinancialTable("BS", ("2025", "2024"), (
        row(0, "Cash", ["100", "80"]),
        row(1, "Inventory", ["50", "40"]),
        row(2, "Total assets", [total_2025, "120"], RowKind.TOTAL),
    ))


def test_footing_column_confirms_its_figures():
    t = table("150")
    got = confirmed_cells(t, analyze_structure(t))
    assert {(0, 0), (1, 0), (2, 0), (0, 1), (1, 1), (2, 1)} <= got


def test_column_that_does_not_foot_confirms_nothing():
    t = table("155")                                   # 2025 is wrong, 2024 foots
    got = confirmed_cells(t, analyze_structure(t))
    assert (2, 0) not in got and (0, 0) not in got and (0, 1) in got


def w(text):
    return OcrWord(text, Decimal("0.9"), (0, 0, 1, 1))


def test_ocr_separator_and_bracket_noise_is_the_same_figure():
    for ocr in ("(8.520)", "8,520)", "_(8,520)", "(8520)"):
        assert _same_figure("(8,520)", Decimal("-8520"), w(ocr)), ocr


def test_a_different_digit_is_not_the_same_figure():
    assert not _same_figure("(5,383)", Decimal("-5383"), w("(6,383)"))


def test_bracketed_negative_matches_ocr_with_lost_bracket():
    from domain.crosscheck import _find
    words = [w("plan"), w("34"), w("2,413)"), w("7,000")]
    assert _find("(2,413)", words)[0].text == "2,413)"
    assert _find("(5,383)", [w("(6,383)")]) is None
