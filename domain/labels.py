"""
Layer: Domain — financial label semantics (English + Arabic).

Pure functions that read a row label or column header and say what it most
likely means.  They only provide *hints*: the structure analyser
(domain.structure) confirms or rejects every hint against the arithmetic.

All matching is done on normalised text (Arabic letter folding, case folding,
punctuation removed).  English keywords match on word boundaries so that, for
example, "sum" does not match "consumables" and "net" does not match
"cabinet".  Arabic keywords match as substrings because Arabic attaches
prefixes (ال / و / ب / ل) to words.
"""
from __future__ import annotations

import re
from decimal import Decimal
from typing import Literal

from .normalize import normalize_arabic

LabelKind = Literal["total", "subtotal", "data"]


def norm(text: str | None) -> str:
    """Normalise a label for keyword matching."""
    if not text:
        return ""
    t = normalize_arabic(str(text))
    t = re.sub(r"[^\w\s]", " ", t)      # punctuation → space (\w keeps Arabic letters)
    t = re.sub(r"_", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _ar(*words: str) -> tuple[str, ...]:
    return tuple(norm(w) for w in words)


def _any_ar(text: str, words: tuple[str, ...]) -> bool:
    return any(w and w in text for w in words)


# ── Aggregate rows ────────────────────────────────────────────────────────────

_SUBTOTAL_EN = re.compile(r"\bsub ?totals?\b")
_SUBTOTAL_AR = _ar("إجمالي فرعي", "مجموع فرعي", "المجموع الفرعي", "الإجمالي الفرعي")

_TOTAL_EN = re.compile(r"\b(?:grand total|totals?|sum)\b")
_TOTAL_AR = _ar("الإجمالي", "إجمالي", "الإجمالية", "المجموع", "مجموع", "الجملة", "جملة")

# Result lines of an income statement — aggregates even without the word "total"
_RESULT_EN = (
    re.compile(r"\b(?:gross|operating|net)\s+(?:profit|loss|income|earnings|surplus|deficit)\b"),
    re.compile(r"\b(?:profit|loss|earnings|income|surplus|deficit)\s+(?:before|after|for)\b"),
    re.compile(r"\btotal comprehensive (?:income|loss)\b"),
    re.compile(r"\bebitda?\b"),
)
_RESULT_AR_CORE = _ar("ربح", "الربح", "أرباح", "خسارة", "الخسارة", "خسائر", "دخل", "الدخل", "فائض", "عجز")
_RESULT_AR_QUAL = _ar("صافي", "مجمل", "إجمالي", "قبل", "بعد", "السنة", "الفترة", "التشغيلي", "التشغيلية")

_NET_START_EN = re.compile(r"^net\b")
_NET_AR = _ar("صافي")

_OPENING_EN = re.compile(
    r"\b(?:opening|beginning|brought forward|b f|at (?:the )?(?:beginning|start))\b"
)
_OPENING_AR = _ar("رصيد افتتاحي", "الرصيد الافتتاحي", "أول المدة", "أول الفترة", "بداية")
_CLOSING_EN = re.compile(r"\b(?:closing|ending|carried forward|c f|at (?:the )?end)\b")
_CLOSING_AR = _ar("رصيد ختامي", "الرصيد الختامي", "آخر المدة", "آخر الفترة", "نهاية")
_BALANCE_EN = re.compile(r"\bbalance\b")
_BALANCE_AR = _ar("الرصيد", "رصيد")

# ── Signs ─────────────────────────────────────────────────────────────────────

_EXPENSE_EN = re.compile(
    r"\b(?:costs?|expenses?|expenditures?|tax|taxes|taxation|zakat|depreciation|"
    r"amorti[sz]ation|impairments?|loss|losses|purchases|cogs|dividends?|drawings|"
    r"withdrawals|charges?|provisions?|allowances?|deductions?|discounts?|returns)\b"
)
_EXPENSE_AR = _ar(
    "تكلفة", "تكاليف", "مصروف", "مصاريف", "مصروفات", "ضريبة", "ضرائب", "زكاة",
    "استهلاك", "إطفاء", "انخفاض قيمة", "خسارة", "خسائر", "أعباء", "مشتريات",
    "توزيعات", "مخصص", "مسحوبات", "خصم", "مردودات",
)

# Obligations: added in "Total liabilities", but *deducted* in a net position
# (net working capital, net contract position, net assets, net debt …).
_LIABILITY_EN = re.compile(
    r"\b(?:liabilit(?:y|ies)|payables?|creditors?|accruals?|accrued|overdrafts?|"
    r"borrowings?|loans? payable|deferred (?:income|revenue)|unearned)\b"
)
_LIABILITY_AR = _ar(
    "مطلوبات", "المطلوبات", "التزامات", "الالتزامات", "دائنون", "الدائنون", "ذمم دائنة",
    "مستحقات", "مصروفات مستحقة", "قروض", "إيرادات مؤجلة",
)

# Movements that reduce a balance in a roll-forward (opening + additions − disposals …)
_REDUCTION_EN = re.compile(
    r"\b(?:disposals?|disposed|repayments?|repaid|written off|write offs?|utili[sz]ed|"
    r"reversals?|derecogni[sz]ed|transfers? out|redemptions?|settlements?|"
    r"withdrawals?)\b"
)
_REDUCTION_AR = _ar("استبعادات", "استبعاد", "سداد", "مسدد", "شطب", "المستخدم", "تحويلات إلى")

# A "net" position: the result of deducting one group from another
_NET_POSITION_EN = re.compile(r"(?:^net\b|\bnet (?:of|position|assets|liabilit|debt|funds)|\bposition\b)")

# Detail lines that *contain* a result-line phrase but are not result lines:
# "Other operating income", "Sundry income", "Miscellaneous operating income".
_OTHER_PREFIX_EN = re.compile(r"^(?:other|sundry|miscellaneous)\b")
_RESULT_OVERRIDE_EN = re.compile(r"\b(?:for the|total)\b")
_OTHER_PREFIX_AR = _ar("أخرى", "اخرى", "متفرقة")

# Cash-flow subtotals that carry no "total" / "net" word
_CASH_SUBTOTAL_EN = re.compile(
    r"\bcash (?:generated|used|flows?) (?:from|in|by) (?:\(used in\) )?operations\b"
)
_CASH_SUBTOTAL_AR = _ar("النقد الناتج من العمليات", "النقد المتولد من العمليات")

# Column headers
_MOVEMENT_HEADER_EN = re.compile(
    r"\b(?:movements?|changes?|variances?|difference|increase|decrease|net|"
    r"inc dec|incr decr)\b"
)
_MOVEMENT_HEADER_AR = _ar("الحركة", "حركة", "التغير", "التغيير", "الفرق", "الفروقات", "صافي", "الزيادة", "النقص")
_DEBIT_HEADER_EN = re.compile(r"^(?:debits?|dr)\b")
_DEBIT_HEADER_AR = _ar("مدين", "المدين")
_CREDIT_HEADER_EN = re.compile(r"^(?:credits?|cr)\b")
_CREDIT_HEADER_AR = _ar("دائن", "الدائن")

# Suspense / unmapped balances
_SUSPENSE_EN = re.compile(r"\b(?:suspense|unmapped|unallocated|unreconciled|clearing account)\b")
_SUSPENSE_AR = _ar("معلق", "المعلق", "حساب وسيط", "غير مصنف", "غير مخصص")

# Expense words that never appear in a result line ("loss" words excluded on purpose)
_EXPENSE_AR_STRICT = _ar(
    "تكلفة", "تكاليف", "مصروف", "مصاريف", "مصروفات", "ضريبة", "ضرائب", "زكاة",
    "استهلاك", "إطفاء", "أعباء", "مشتريات", "مخصص",
)

# ── Headers / footnotes ───────────────────────────────────────────────────────

_TOTAL_HEADER_EN = re.compile(r"\b(?:grand total|totals?)\b")
_TOTAL_HEADER_AR = _ar("المجموع", "مجموع", "الإجمالي", "إجمالي")
_NOTE_HEADER_EN = re.compile(r"^(?:notes?|ref|reference)$")
_NOTE_HEADER_AR = _ar("إيضاح", "إيضاحات", "ملاحظة")
_LABEL_HEADER_EN = re.compile(
    r"^(?:accounts?|items?|line items?|descriptions?|particulars|details|captions?|"
    r"categor(?:y|ies)|departments?|names?)$"
)
_LABEL_HEADER_AR = _ar("البند", "البنود", "الحساب", "الحسابات", "البيان", "التفاصيل", "الوصف")
_PERCENT_HEADER = re.compile(r"(?:%|\bpercent(?:age)?\b|\bratio\b|\bmargin\b|نسبة)")
_FOOTNOTE_EN = re.compile(
    r"^(?:\*|†|‡|notes?\b|source\b|see note\b|the accompanying\b|\(?[a-z0-9]\)\s)"
)
_FOOTNOTE_AR = _ar("ملاحظة", "ملاحظات", "المصدر", "الإيضاحات المرفقة", "إيضاح")

_PERIOD = re.compile(
    r"(?i)\b(fy\s?\d{2,4}|\d{4}|q[1-4](?:\s?\d{2,4})?|h[12](?:\s?\d{2,4})?)\b"
)
_CURRENCY = re.compile(r"\b(SAR|USD|EUR|GBP|AED|KWD|QAR|BHD|OMR|EGP|JOD)\b|[$€£]|ريال|دولار")
_SCALE = re.compile(
    r"(?i)('?000s?|\bthousands?\b|\bmillions?\b|\bbillions?\b|بالآلاف|بالالاف|بالملايين)"
)


# ── Public API ────────────────────────────────────────────────────────────────

def is_subtotal_label(label: str) -> bool:
    t = norm(label)
    return bool(_SUBTOTAL_EN.search(t)) or _any_ar(t, _SUBTOTAL_AR)


def is_total_label(label: str) -> bool:
    t = norm(label)
    return bool(_TOTAL_EN.search(t)) or _any_ar(t, _TOTAL_AR)


def is_result_label(label: str) -> bool:
    """Income-statement result line: gross profit, operating profit, net income…"""
    t = norm(label)
    if _is_other_line(t):
        return False
    if any(p.search(t) for p in _RESULT_EN):
        return True
    return (
        _any_ar(t, _RESULT_AR_CORE)
        and _any_ar(t, _RESULT_AR_QUAL)
        and not _any_ar(t, _EXPENSE_AR_STRICT)   # e.g. "ضريبة الدخل" (income tax) is not a result line
    )


def is_net_label(label: str) -> bool:
    t = norm(label)
    return bool(_NET_START_EN.search(t)) or _any_ar(t, _NET_AR)


def is_opening_balance(label: str) -> bool:
    t = norm(label)
    return bool(_OPENING_EN.search(t)) or _any_ar(t, _OPENING_AR)


def is_closing_balance(label: str) -> bool:
    t = norm(label)
    return bool(_CLOSING_EN.search(t)) or _any_ar(t, _CLOSING_AR)


def is_balance_label(label: str) -> bool:
    t = norm(label)
    return bool(_BALANCE_EN.search(t)) or _any_ar(t, _BALANCE_AR)


def is_expense_label(label: str) -> bool:
    """Lines normally presented as positive amounts that are *deducted*."""
    t = norm(label)
    return bool(_EXPENSE_EN.search(t)) or _any_ar(t, _EXPENSE_AR)


def is_liability_label(label: str) -> bool:
    """Obligations (payables, contract liabilities, accruals, borrowings …)."""
    t = norm(label)
    return bool(_LIABILITY_EN.search(t)) or _any_ar(t, _LIABILITY_AR)


def is_reduction_label(label: str) -> bool:
    """Roll-forward movements that reduce a balance (disposals, repayments …)."""
    t = norm(label)
    return bool(_REDUCTION_EN.search(t)) or _any_ar(t, _REDUCTION_AR)


def is_deduction_label(label: str) -> bool:
    """A line that is subtracted when it takes part in a *net* result.

    Covers expenses / costs, obligations (only deducted in a net position — the
    structure analyser decides from the figures whether that is the case) and
    reducing movements.  Result lines are never deductions themselves.
    """
    if is_result_label(label):
        return False
    return is_expense_label(label) or is_liability_label(label) or is_reduction_label(label)


def is_net_position_label(label: str) -> bool:
    """"Net working capital", "Net contract position", "Net assets", "صافي …"."""
    t = norm(label)
    return bool(_NET_POSITION_EN.search(t)) or _any_ar(t, _NET_AR)


def is_profit_type(label: str) -> bool:
    """A result line whose value is a running balance (income − deductions)."""
    return is_result_label(label)


def is_cash_subtotal_label(label: str) -> bool:
    t = norm(label)
    return bool(_CASH_SUBTOTAL_EN.search(t)) or _any_ar(t, _CASH_SUBTOTAL_AR)


def is_movement_header(header: str) -> bool:
    """Column that holds a difference: "Movement", "Change", "Net", "Variance" …"""
    t = norm(header)
    return bool(t) and (bool(_MOVEMENT_HEADER_EN.search(t)) or _any_ar(t, _MOVEMENT_HEADER_AR))


def is_debit_header(header: str) -> bool:
    t = norm(header)
    return bool(_DEBIT_HEADER_EN.match(t)) or (bool(t) and _any_ar(t, _DEBIT_HEADER_AR))


def is_credit_header(header: str) -> bool:
    t = norm(header)
    return bool(_CREDIT_HEADER_EN.match(t)) or (bool(t) and _any_ar(t, _CREDIT_HEADER_AR))


def is_suspense_label(text: str) -> bool:
    t = norm(text)
    return bool(t) and (bool(_SUSPENSE_EN.search(t)) or _any_ar(t, _SUSPENSE_AR))


def _is_other_line(t: str) -> bool:
    """"Other operating income" & co. — detail lines despite the result phrase."""
    if _OTHER_PREFIX_EN.match(t) and not _RESULT_OVERRIDE_EN.search(t):
        return True
    words = t.split()
    return bool(words) and words[-1] in _OTHER_PREFIX_AR and not _any_ar(t, _TOTAL_AR)


def classify_label(label: str) -> LabelKind:
    """'total' / 'subtotal' / 'data' from the label text alone."""
    if not label or not label.strip():
        return "data"
    if is_subtotal_label(label):
        return "subtotal"
    if is_opening_balance(label):
        return "data"
    if (
        is_total_label(label)
        or is_result_label(label)
        or is_net_label(label)
        or is_closing_balance(label)
        or is_cash_subtotal_label(label)
    ):
        return "total"
    return "data"


def is_total_header(header: str) -> bool:
    t = norm(header)
    return bool(_TOTAL_HEADER_EN.search(t)) or _any_ar(t, _TOTAL_HEADER_AR)


_ELIMINATION_EN = re.compile(
    r"\b(?:eliminat(?:ion|ing)s?|adjustments?|reconciling items?|"
    r"inter[- ]?segment|consolidation adjustments?)\b"
)
_ELIMINATION_AR = _ar("حذف", "الحذف", "استبعاد", "الاستبعادات", "تسويات", "التسويات")


def is_elimination_header(header: str) -> bool:
    """Columns whose figures repeat across rows by construction (Eliminations,
    Adjustments, Inter-segment, Reconciling items).  They must not count as
    confirmation of an *inferred* vertical formula — a formula that matches only
    there is almost always coincidence."""
    t = norm(header)
    return bool(_ELIMINATION_EN.search(t)) or _any_ar(t, _ELIMINATION_AR)


def is_note_header(header: str) -> bool:
    t = norm(header)
    return bool(_NOTE_HEADER_EN.match(t)) or (bool(t) and _any_ar(t, _NOTE_HEADER_AR) and len(t) <= 12)


def is_label_header(header: str) -> bool:
    t = norm(header)
    return bool(_LABEL_HEADER_EN.match(t)) or _any_ar(t, _LABEL_HEADER_AR)


def is_percent_header(header: str) -> bool:
    return bool(header) and bool(_PERCENT_HEADER.search(header.lower()))


def is_footnote_text(text: str) -> bool:
    raw = (text or "").strip().lower()
    if not raw:
        return False
    if _FOOTNOTE_EN.match(raw):
        return True
    t = norm(text)
    return any(t.startswith(w) for w in _FOOTNOTE_AR)


def is_year_like(value: object) -> bool:
    """A header cell that is a bare year: 2024, "2024", 2024.0."""
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float, Decimal)):
        try:
            return float(value).is_integer() and 1900 <= int(value) <= 2100
        except (ValueError, OverflowError):
            return False
    if isinstance(value, str):
        s = normalize_arabic(value).strip()
        return bool(re.fullmatch(r"(?:19|20|21)\d{2}", s))
    return False


def extract_period(header: str) -> str | None:
    m = _PERIOD.search(normalize_arabic(header or ""))
    return m.group(1).upper().replace(" ", "") if m else None


def extract_currency(*texts: str | None) -> str | None:
    for text in texts:
        if text:
            m = _CURRENCY.search(text)
            if m:
                return m.group(0)
    return None


def extract_scale(header: str | None) -> str | None:
    if not header:
        return None
    m = _SCALE.search(header)
    return m.group(1) if m else None
