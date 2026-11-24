"""
Layer: Domain — decide, from OCR text, that a page holds no financial table.

Used to avoid sending cover pages, the auditor's report, accounting-policy text
and similar narrative pages to the (slow, paid) table reader.  The rule is
deliberately conservative — a page is skipped only when OCR clearly read it
(enough words) *and* found almost no amounts on it:

    measured on a 66-page scanned annual report: every page that has a table
    shows ≥ 9 amounts in OCR, narrative pages show 0–4.  The default threshold
    (≤ 4) keeps more than a 2× margin.

When OCR read little or nothing (blank / unreadable scan, OCR disabled) the page
is never skipped — "could not tell" always means "read it".

Pure Python; no external dependencies.
"""
from __future__ import annotations

import re

from .models import OcrPage
from .normalize import fold_digits

# Thousands-separated figures (1,234 · 1.234 · 1٬234) or any run of ≥ 3 digits
# (years count too, which only makes skipping rarer).
_AMOUNT = re.compile(r"\d{1,3}(?:[,.٬]\d{3})+|\d{3,}")
MIN_WORDS = 20


def amount_count(page: OcrPage) -> int:
    return sum(len(_AMOUNT.findall(fold_digits(w.text))) for w in page.words)


def looks_tableless(page: OcrPage, max_amounts: int) -> bool:
    """True when OCR read the page and found at most `max_amounts` amounts.

    `max_amounts < 0` disables skipping.
    """
    if max_amounts < 0 or len(page.words) < MIN_WORDS:
        return False
    return amount_count(page) <= max_amounts
