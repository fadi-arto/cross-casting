"""Layer: Domain — Decimal arithmetic for financial values."""
from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

QUANT = Decimal("0.0001")
MONEY_QUANT = Decimal("0.01")
ZERO = Decimal("0")

_ARABIC_DIGITS = str.maketrans(
    {chr(0x0660 + i): str(i) for i in range(10)} |
    {chr(0x06F0 + i): str(i) for i in range(10)} |
    {"٬": ",", "٫": "."}
)
_NUMERIC = re.compile(r"-?\d+(?:[.,]\d{3})*(?:\.\d+)?")


def fold_digits(text: str) -> str:
    return text.translate(_ARABIC_DIGITS)


def to_decimal(value: str | int | float | Decimal | None) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value.quantize(QUANT, rounding=ROUND_HALF_UP)
    try:
        return Decimal(str(value)).quantize(QUANT, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError, ArithmeticError):
        return None


def q(value: Decimal) -> Decimal:
    return value.quantize(QUANT, rounding=ROUND_HALF_UP)


def money(value: Decimal) -> Decimal:
    return value.quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)


def display(value: Decimal) -> str:
    cents = value.quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)
    return format(cents if cents == value else value.normalize(), "f")


def pct_diff(actual: Decimal, expected: Decimal) -> Decimal | None:
    if expected == ZERO:
        return None
    return q((actual - expected) / expected * Decimal("100"))


def within_tolerance(
    actual: Decimal,
    expected: Decimal,
    *,
    abs_tolerance: Decimal = ZERO,
    pct_tolerance: Decimal = ZERO,
) -> bool:
    delta = abs(actual - expected)
    if abs_tolerance > ZERO and delta <= abs_tolerance:
        return True
    if pct_tolerance > ZERO and expected != ZERO:
        return abs(delta / expected * Decimal("100")) <= pct_tolerance
    return delta == ZERO


def parse_amount(text: str | None) -> Decimal | None:
    """Parse a plain numeric string (e.g. '-1234.56' or '1,234.56')."""
    if text is None:
        return None
    cleaned = fold_digits(text).replace(",", "").replace(" ", "")
    if not re.fullmatch(r"-?\d+(?:\.\d+)?", cleaned):
        return None
    return to_decimal(cleaned)


def parse_financial_value(text: str | None) -> Decimal | None:
    """
    Parse financial values including parenthesised negatives.

    Handles: -100 / (100) / 1,000.50 / ١٬٠٠٠ / (1,234.56)
    """
    if text is None:
        return None
    cleaned = fold_digits(text).strip()

    # Parenthesised negative: (100) → -100
    if cleaned.startswith("(") and cleaned.endswith(")"):
        cleaned = "-" + cleaned[1:-1]

    # Remove thousand separators and spaces
    cleaned = cleaned.replace(",", "").replace(" ", "").replace(" ", "")

    # Dashes / blanks represent zero or missing
    if cleaned in {"", "-", "—", "–"}:
        return None

    return to_decimal(cleaned)
