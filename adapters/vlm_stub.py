"""Layer: Adapters — Stub VLM for testing (no API calls)."""
from __future__ import annotations

from domain.models import FinancialTable, PageImage


class StubTableReader:
    label = "stub (no extraction)"

    def extract_tables(self, page: PageImage) -> tuple[FinancialTable, ...]:
        return ()
