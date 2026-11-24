from __future__ import annotations

from typing import Protocol

from domain.models import FinancialTable, PageImage


class TableReader(Protocol):
    """VLM adapter: extract financial tables from a rendered page image."""

    def extract_tables(self, page: PageImage) -> tuple[FinancialTable, ...]: ...
