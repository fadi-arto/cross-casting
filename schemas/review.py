"""Review-report schemas (Pydantic v2) — what the frontend renders to resolve findings.

Every user-facing string is bilingual (`L10n` / `L10nList`): the frontend picks
`.en` or `.ar` and never has to compose sentences itself.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class L10n(BaseModel):
    en: str
    ar: str


class L10nList(BaseModel):
    en: list[str] = []
    ar: list[str] = []


# ── One finding ───────────────────────────────────────────────────────────────

Category = Literal["casting", "cross_casting", "verification", "other"]
Check = Literal[
    "vertical", "horizontal", "trial_balance",           # casting
    "row_vs_col", "row_vs_grand", "col_vs_grand",         # cross-casting
    "ocr_vlm",                                            # verification
    "other",
]
Direction = Literal["first_higher", "first_lower", "equal", "n/a"]


class ReviewLocationOut(BaseModel):
    """Where to look in the source document."""
    source: Literal["pdf", "excel"] | None = None
    page: int | None = None               # PDF/image page index (position only)
    sheet: str | None = None              # Excel sheet
    row: str | None = None                # row label
    section: str | None = None            # block the row is in, e.g. "Balance as on 1 January, 2024"
    column: str | None = None             # column header
    cell: str | None = None               # e.g. "Balance Sheet!C8" (Excel)
    relation: str | None = None           # e.g. "Total assets = Cash + Inventory"
    bbox: list[float] | None = None       # PDF/image: region of the cell


class ReviewValueOut(BaseModel):
    """A labelled figure shown on the card (declared / computed / ...)."""
    label: L10n
    value: str | None = None              # formatted, e.g. "1,234.50"


class ReviewItemOut(BaseModel):
    id: str                               # = issue id; use it with PATCH …/issues/{id}
    priority: int                         # 1 = fix first (ranked over the whole job)
    category: Category
    check: Check
    issue_type: str
    severity: Literal["critical", "high", "medium", "low"] | str
    status: Literal["open", "acknowledged", "resolved"] | str
    table_id: str | None
    table_name: str | None
    location: ReviewLocationOut
    values: list[ReviewValueOut]          # [0] vs [1] are the two sides compared
    delta: str | None                     # values[0] − values[1], formatted
    direction: Direction
    title: L10n
    explanation: L10n
    likely_causes: L10nList
    hints: L10nList                       # data-driven hints (rounding, transposition, sign…)
    steps: L10nList                       # ordered actions to resolve
    related: list[str] = []               # consequences merged into this finding


# ── Grouping / report ─────────────────────────────────────────────────────────

class ReviewTableCountsOut(BaseModel):
    total: int
    open: int
    casting: int
    cross_casting: int
    verification: int


class ReviewTableGroupOut(BaseModel):
    table_id: str | None
    table_name: str
    page: int | None = None
    sheet: str | None = None
    max_severity: str
    counts: ReviewTableCountsOut
    items: list[ReviewItemOut]


class ReviewSummaryOut(BaseModel):
    total: int
    open: int
    acknowledged: int
    resolved: int
    casting: int
    cross_casting: int
    verification: int
    by_severity: dict[str, int]
    tables_with_issues: int


class ReviewCoverageOut(BaseModel):
    """What was actually checked (so 'no errors' is meaningful)."""
    tables_processed: int | None = None
    total_checks: int | None = None
    casting_checks: int | None = None
    cross_casting_checks: int | None = None
    # Pages not sent to the table reader because OCR showed no financial table on them.
    pages_skipped: list[int] = []


class ReviewLabelsOut(BaseModel):
    """Display names the UI can use for filters / badges."""
    status: dict[str, L10n]
    severity: dict[str, L10n]
    category: dict[str, L10n]


class ReviewReportOut(BaseModel):
    job_id: str
    filename: str
    file_type: str
    job_status: str
    scope: Literal["all", "casting", "cross_casting", "verification"]
    generated_at: datetime
    is_clean: bool                        # no OPEN issues in this scope
    conclusion: L10n
    summary: ReviewSummaryOut
    coverage: ReviewCoverageOut
    tables: list[ReviewTableGroupOut]     # groups ordered by their highest-priority item
    labels: ReviewLabelsOut
    how_to_resolve: L10nList = Field(default_factory=L10nList)


# ── Status update ─────────────────────────────────────────────────────────────

class IssueStatusUpdateIn(BaseModel):
    status: Literal["open", "acknowledged", "resolved"]
