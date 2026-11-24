"""
Layer: Application — join tables that continue across PDF pages.

The VLM reads one page at a time, so a statement that runs onto the next page
arrives as two tables. Casting the halves separately would compare a total on
page 2 with only the rows that happen to be on page 2.

The last table of page N and the first table of page N+1 are joined when
  • the VLM marked the second one `continues_previous_page`, or
  • both have the same title (ignoring "continued" / "تابع") and the same
    column headers,
and both have the same number of amount columns. A table flagged as a
continuation whose columns do not match is kept separate with a warning.
"""
from __future__ import annotations

import re
from dataclasses import replace

from domain.labels import norm
from domain.models import FinancialTable

_CONTINUED = re.compile(r"\b(?:continued|cont d|contd)\b|تابع|تتمة|مستمر")


def merge_page_continuations(tables: list[FinancialTable]) -> list[FinancialTable]:
    out: list[FinancialTable] = []
    for i, table in enumerate(tables):
        prev = out[-1] if out else None
        first_on_page = i == 0 or tables[i - 1].page != table.page
        if prev is not None and first_on_page and table.page == prev.page + 1:
            if _same_columns(prev, table) and (table.continues_previous or _same_table(prev, table)):
                out[-1] = _join(prev, table)
                continue
            if table.continues_previous:
                table = replace(table, extraction_warnings=table.extraction_warnings + (
                    f"Marked as continuing the table on page {prev.page}, but the columns do not "
                    "match; the two parts were checked separately.",
                ))
        out.append(table)
    return out


def _width(t: FinancialTable) -> int:
    return max((len(r.cells) for r in t.rows), default=0)


def _same_columns(a: FinancialTable, b: FinancialTable) -> bool:
    if _width(a) != _width(b):
        return False
    ha = [norm(h) for h in a.headers]
    hb = [norm(h) for h in b.headers]
    return not hb or not ha or ha == hb


def _same_table(a: FinancialTable, b: FinancialTable) -> bool:
    ta = _CONTINUED.sub("", norm(a.title)).strip()
    tb = _CONTINUED.sub("", norm(b.title)).strip()
    return bool(ta) and ta == tb and [norm(h) for h in a.headers] == [norm(h) for h in b.headers]


def _join(a: FinancialTable, b: FinancialTable) -> FinancialTable:
    offset = len(a.rows)
    moved = tuple(replace(r, index=r.index + offset) for r in b.rows)
    return replace(
        a,
        rows=a.rows + moved,
        headers=a.headers or b.headers,
        extraction_warnings=a.extraction_warnings + b.extraction_warnings + (
            f"Rows from page {b.page} were joined to this table (continuation of page {a.page}).",
        ),
    )
