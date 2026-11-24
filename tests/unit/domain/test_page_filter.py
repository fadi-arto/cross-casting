"""A page is skipped only when OCR clearly read it and found (almost) no amounts."""
from decimal import Decimal

from domain.models import OcrPage, OcrWord
from domain.page_filter import MIN_WORDS, amount_count, looks_tableless


def page(*texts):
    return OcrPage(1, tuple(OcrWord(t, Decimal("0.9"), (0, 0, 1, 1)) for t in texts))


NARRATIVE = ["word"] * 40


def test_narrative_page_is_skipped():
    assert looks_tableless(page(*NARRATIVE, "2025", "note", "12"), 4)


def test_table_page_is_kept():
    figures = ["1,234", "(5,383)", "44,370", "1.312", "220,000", "٢٠٢٥", "180000"]
    p = page(*NARRATIVE, *figures)
    assert amount_count(p) == 7
    assert not looks_tableless(p, 4)


def test_little_or_no_ocr_text_is_never_skipped():
    assert not looks_tableless(page(), 4)                          # OCR read nothing
    assert not looks_tableless(page(*["w"] * (MIN_WORDS - 1)), 4)  # too little to judge


def test_negative_threshold_disables():
    assert not looks_tableless(page(*NARRATIVE), -1)
