"""Layer: Domain — Arabic/Unicode text normalization (adapted from auditagent_domain)."""
from __future__ import annotations

import re
import unicodedata

# Arabic diacritics (tashkeel, Quranic marks). Written as escapes: the literal
# characters were reordered by right-to-left editing into ranges that covered
# every Arabic letter, so normalize_arabic() returned "" for any Arabic text.
_DIACRITICS = re.compile(r"[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06ED]")
_IGNORABLE = frozenset("ـ​‌‍‎‏‪‫‬‭‮⁦⁧⁨⁩﻿")

_CHAR_MAP = str.maketrans({
    "أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا",
    "ى": "ي", "ی": "ي",
    "ة": "ه",
    "ؤ": "و",
    "ئ": "ي",
    "ک": "ك",
})

_DIGIT_TABLE = str.maketrans(
    {chr(0x0660 + i): str(i) for i in range(10)} |
    {chr(0x06F0 + i): str(i) for i in range(10)} |
    {"٬": ",", "٫": "."}
)


def fold_digits(text: str) -> str:
    return text.translate(_DIGIT_TABLE)


def normalize_arabic(text: str) -> str:
    out: list[str] = []
    for ch in text:
        for piece in unicodedata.normalize("NFKC", ch):
            if piece in _IGNORABLE or _DIACRITICS.match(piece):
                continue
            piece = piece.translate(_CHAR_MAP).translate(_DIGIT_TABLE)
            if piece.isspace():
                if not out or out[-1] == " ":
                    continue
                piece = " "
            out.extend(piece.casefold())
    while out and out[-1] == " ":
        out.pop()
    return "".join(out)


def classify_row_label(label: str) -> str:
    """Return 'total', 'subtotal', or 'data' based on the row label text.

    Delegates to domain.labels, which normalises keywords the same way as the
    label (the previous keyword sets were not normalised, so e.g. "الإجمالي"
    never matched) and matches English keywords on word boundaries.
    """
    from .labels import classify_label  # local import: labels imports this module
    return classify_label(label)
