"""
LIVE tests against the real Anthropic API — they spend real (small) money.

Skipped unless explicitly enabled:

    RUN_ANTHROPIC_LIVE=1 pytest -s tests/integration/test_anthropic_live.py

Uses ANTHROPIC_API_KEY / ANTHROPIC_MODEL from the environment (.env). Ground truth
is the test workbook itself: every label and value Claude reads from the rendered
PDF/PNG must equal the workbook cell exactly.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from decimal import Decimal
from pathlib import Path

import anthropic
import pytest

from adapters.excel_reader import read_excel
from adapters.pdf_renderer import PdfRenderer
from application.factory import _build_reader
from application.pipeline import CrossCastingPipeline
from config.settings import get_settings
from domain.models import PageImage

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
LIVE = os.environ.get("RUN_ANTHROPIC_LIVE") == "1"
pytestmark = pytest.mark.skipif(
    not LIVE or not os.environ.get("ANTHROPIC_API_KEY"),
    reason="live Anthropic test: set RUN_ANTHROPIC_LIVE=1 and ANTHROPIC_API_KEY",
)


@pytest.fixture(scope="module")
def settings():
    return get_settings()


@pytest.fixture(scope="module")
def reader(settings):
    assert settings.vlm.backend == "anthropic", "set VLM_BACKEND=anthropic for the live test"
    return _build_reader(settings)


@pytest.fixture(scope="module")
def truth():
    """{sheet: {label: (FY2024, FY2025, Total)}} from the workbook."""
    tables = read_excel((FIXTURES / "Casting_Test_With_Errors.xlsx").read_bytes())
    return {
        t.sheet: {r.label: tuple(c.value for c in r.cells) for r in t.rows if r.kind.is_amount_row}
        for t in tables
    }


def _compare(table, expected: dict) -> list[str]:
    """Every mismatch between an extracted table and the workbook, as text."""
    problems = []
    got = {r.label.strip(): tuple(c.value for c in r.cells) for r in table.rows if r.kind.is_amount_row}
    for label, values in expected.items():
        if label not in got:
            problems.append(f"missing row {label!r}")
        elif tuple(Decimal(v) for v in got[label]) != tuple(Decimal(v) for v in values):
            problems.append(f"{label!r}: got {got[label]} expected {values}")
    for label in got:
        if label not in expected:
            problems.append(f"unexpected row {label!r}")
    return problems


def test_connection_and_model(settings):
    client = anthropic.Anthropic(api_key=settings.anthropic.api_key.get_secret_value(), max_retries=0)
    model = client.models.retrieve(settings.anthropic.model)
    print(f"\nmodel: {model.id} ({model.display_name}) max_input={model.max_input_tokens} "
          f"max_output={model.max_tokens}")
    assert model.id == settings.anthropic.model
    assert model.capabilities.image_input.supported
    assert model.capabilities.structured_outputs.supported


def test_png_extraction_is_exact(reader, truth):
    content = (FIXTURES / "balance_sheet.png").read_bytes()
    before = reader.usage.requests
    tables = reader.extract_tables(PageImage(1, "image/png", content, 1240, 900))
    assert reader.usage.requests == before + 1
    assert len(tables) == 1
    t = tables[0]
    print(f"\nPNG → title={t.title!r} headers={t.headers}")
    assert "Statement of Financial Position" in t.title
    assert [h.strip() for h in t.headers][-3:] == ["FY2024", "FY2025", "Total"]
    assert _compare(t, truth["Balance Sheet"]) == []


def test_pdf_two_pages_full_pipeline(reader, truth):
    pipeline = CrossCastingPipeline(PdfRenderer(dpi=150), reader, None, None)
    report = pipeline.process_pdf((FIXTURES / "financial_statements_2pages.pdf").read_bytes(), "fs.pdf")
    titles = [r.table.title for r in report.validation_results]
    print(f"\nPDF tables: {titles}")
    print(f"usage: {reader.usage.summary()}")
    assert len(report.validation_results) == 2, "Balance Sheet halves must be joined"

    inc = next(r for r in report.validation_results if "Income" in r.table.title)
    bs = next(r for r in report.validation_results if "Financial Position" in r.table.title)
    assert _compare(inc.table, truth["Income Statement"]) == []
    assert _compare(bs.table, truth["Balance Sheet"]) == []

    found = sorted(
        (e.row_label, e.label, e.delta) for r in report.validation_results for e in r.casting_errors
    )
    assert found == [
        ("Gross profit", "FY2025", Decimal("10000.00")),
        ("Profit before tax", "FY2025", Decimal("-10000.00")),
        ("Total assets", "FY2025", Decimal("15000.00")),
    ]
    assert report.total_cross_casting_errors == 0
