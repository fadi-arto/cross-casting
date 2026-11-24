"""
Layer: Domain — OCR vs VLM cell-level verification.
Adapted from auditagent_domain.crosscheck; now operates on Cell objects.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from decimal import Decimal

from .models import Bbox, Cell, CellVerification, OcrPage, OcrWord, VerificationStatus
from .money import ZERO, parse_amount
from .normalize import fold_digits, normalize_arabic

_BBOX_MARGIN = Decimal("0.15")
_NUMBER = re.compile(r"-?\d[\d,]*(?:\.\d+)?")

AGREEMENT: VerificationStatus = "AGREEMENT"
DISAGREEMENT: VerificationStatus = "DISAGREEMENT"
HUMAN_REVIEW: VerificationStatus = "HUMAN_REVIEW"
NOT_FOUND: VerificationStatus = "NOT_FOUND"


def verify_cell(cell: Cell, pages: Mapping[int, OcrPage]) -> CellVerification:
    """Compare a VLM-extracted cell value against OCR evidence on the same page."""
    if cell.value is None:
        return _not_found(cell, "empty_cell")

    vlm_text = cell.raw_text
    page = pages.get(cell.page)
    if page is None or not page.words:
        return _human_review(cell, None, "ocr_unavailable")

    match = _match_in_region(cell, page)
    if match is not None:
        return _agree(cell, match, cell.bbox, "region")

    hits = _all_matches(vlm_text, page)
    if len(hits) == 1:
        return _agree(cell, hits[0], _union([w.bbox for w in hits[0]]), "page_unique")
    if len(hits) > 1:
        return _human_review(cell, None, "ambiguous_on_page")

    # value not found anywhere; report only numeric OCR evidence so note numbers
    # and row-label words never become the "OCR value" of a finding.  With no
    # numeric evidence the result is not reported as a disagreement.
    if cell.bbox is not None:
        region_words = _words_in(cell.bbox, page)
    else:
        region_words = list(page.words)
    return _human_review(cell, _numeric_evidence(region_words, cell.value), "value_not_found")


# ── Internal helpers ──────────────────────────────────────────────────────────

def _numbers_in(text: str) -> set[Decimal]:
    out: set[Decimal] = set()
    for m in _NUMBER.finditer(fold_digits(text)):
        parsed = parse_amount(m.group())
        if parsed is not None:
            out.add(parsed)
    return out


def _words_in(bbox: Bbox, page: OcrPage) -> list[OcrWord]:
    x1, y1, x2, y2 = bbox
    mx, my = (x2 - x1) * _BBOX_MARGIN, (y2 - y1) * _BBOX_MARGIN
    left, right, top, bottom = x1 - mx, x2 + mx, y1 - my, y2 + my
    return [w for w in page.words if left <= w.center[0] <= right and top <= w.center[1] <= bottom]


def _match_in_region(cell: Cell, page: OcrPage) -> list[OcrWord] | None:
    if cell.bbox is None:
        return None
    words = _words_in(cell.bbox, page)
    return _find(cell.raw_text, words)


def _numeric_evidence(words: list[OcrWord], vlm_value: Decimal | None) -> str | None:
    """OCR evidence to show next to the VLM value: numeric tokens only.

    Row labels ("Basic ('000 shares)") and lone note references (small integers,
    or refs like 2.3, 7.2) are stripped, so the finding compares figures with
    figures.  If nothing numeric remains, returns None — the verifier then knows
    there was no counter-evidence and does not report a disagreement.
    """
    out: list[str] = []
    for w in words:
        text = fold_digits(w.text)
        if not any(ch.isdigit() for ch in text):
            continue
        parsed = parse_amount(text)
        if parsed is not None and vlm_value is not None and parsed == vlm_value:
            out.append(w.text)
            continue
        # Only clean figures become evidence: things like "('000" (scale marker) or
        # words that happen to contain digits are not comparable amounts.
        if not _is_figure(w.text):
            continue
        # drop lone note references: small integers 1–99 and "2.3"/"7.2" style refs
        if re.fullmatch(r"\(?\d{1,2}\)?", text) and (parsed is None or abs(parsed) < 100):
            continue
        if re.fullmatch(r"\d{1,2}\.\d{1,2}", text):
            continue
        out.append(w.text)
    return " ".join(out) or None


def _all_matches(vlm_text: str, page: OcrPage) -> list[list[OcrWord]]:
    parsed = parse_amount(vlm_text)
    if parsed is not None or _is_figure(vlm_text):
        return [[w] for w in page.words if _same_figure(vlm_text, parsed, w)]
    found = _find(vlm_text, list(page.words))
    return [found] if found else []


def _digits(text: str) -> str:
    return re.sub(r"\D", "", fold_digits(text))


_FIGURE_TEXT = re.compile(r"[\s()\-\u2212\u2013,.\u066B\u066C\d\u0660-\u0669\u06F0-\u06F9]+")


def _is_figure(value: str) -> bool:
    """"(2,413)", "-8,520", "1.312" … — an amount, whatever the bracket convention."""
    v = value.strip()
    return bool(v) and _FIGURE_TEXT.fullmatch(v) is not None and any(ch.isdigit() for ch in v)


def _same_figure(value: str, parsed: Decimal | None, word: OcrWord) -> bool:
    """OCR shows the same figure: same amount, or the same digit sequence.

    OCR routinely mangles separators and brackets on scans — "(8.520)", "27,518)",
    "(1317)", "_(108,277)" — while the digits themselves are right.  The digit
    sequence is what a misread changes, so that is what is compared.
    """
    if parsed is not None and parsed in _numbers_in(word.text):
        return True
    key = _digits(value)
    return len(key) >= 2 and any(_digits(tok) == key for tok in word.text.split())


def _find(value: str, words: list[OcrWord]) -> list[OcrWord] | None:
    parsed = parse_amount(value)
    if parsed is not None or _is_figure(value):
        hits = [w for w in words if _same_figure(value, parsed, w)]
        return hits[:1] or None
    needed = normalize_arabic(value).split()
    if not needed:
        return None
    available: dict[str, list[OcrWord]] = {}
    for w in words:
        for token in normalize_arabic(w.text).split():
            available.setdefault(token, []).append(w)
    if not all(token in available for token in needed):
        return None
    chosen: list[OcrWord] = []
    for token in needed:
        word = available[token][0]
        if word not in chosen:
            chosen.append(word)
    return chosen


def _region_text(cell: Cell, page: OcrPage) -> str | None:
    if cell.bbox is None:
        return None
    words = _words_in(cell.bbox, page)
    return " ".join(w.text for w in words) or None


def _union(boxes: list[Bbox]) -> Bbox:
    return (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    )


def _agree(
    cell: Cell,
    words: list[OcrWord],
    vlm_bbox: Bbox | None,
    how: str,
) -> CellVerification:
    ocr_confidence = min(w.confidence for w in words)
    vlm_confidence = Decimal("0.9")
    return CellVerification(
        cell=cell,
        ocr_value=" ".join(w.text for w in words),
        vlm_value=cell.raw_text,
        agreed=True,
        status=AGREEMENT,
        confidence=min(vlm_confidence, ocr_confidence),
        bbox=vlm_bbox if how == "region" else _union([w.bbox for w in words]),
        reason=how,
    )


def _human_review(cell: Cell, ocr_value: str | None, reason: str) -> CellVerification:
    return CellVerification(
        cell=cell,
        ocr_value=ocr_value,
        vlm_value=cell.raw_text,
        agreed=False,
        status=HUMAN_REVIEW,
        confidence=ZERO,
        bbox=cell.bbox,
        reason=reason,
    )


def _not_found(cell: Cell, reason: str) -> CellVerification:
    return CellVerification(
        cell=cell,
        ocr_value=None,
        vlm_value=cell.raw_text,
        agreed=False,
        status=NOT_FOUND,
        confidence=ZERO,
        bbox=cell.bbox,
        reason=reason,
    )
