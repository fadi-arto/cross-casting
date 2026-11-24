"""
Layer: Application — Main orchestration pipeline.

Wires all ports together; depends only on Protocol interfaces (never on concrete adapters).
"""
from __future__ import annotations

import hashlib
import logging
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from decimal import Decimal

from domain.casting import confirmed_cells, run_casting
from domain.cross_casting import run_cross_casting
from domain.cell_bbox import narrow_bbox_per_column
from domain.crosscheck import verify_cell
from domain.findings import consolidate_findings
from domain.page_filter import amount_count, looks_tableless
from domain.structure import analyze_structure
from application.page_merge import merge_page_continuations
from domain.models import (
    AuditReport,
    CellVerification,
    FinancialTable,
    OcrPage,
    RenderedDocument,
    RowKind,
    ValidationResult,
)
from ports.interpreter import ErrorInterpreter
from ports.ocr import OcrEngine
from ports.reader import TableReader
from ports.renderer import PageRenderer

log = logging.getLogger(__name__)

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PDF = "application/pdf"
PNG = "image/png"
JPEG = "image/jpeg"


class CrossCastingPipeline:
    def __init__(
        self,
        renderer: PageRenderer,
        table_reader: TableReader,
        ocr: OcrEngine | None,
        interpreter: ErrorInterpreter | None,
        *,
        abs_tolerance: Decimal = Decimal("0.01"),
        page_concurrency: int = 1,
        skip_page_max_amounts: int = -1,
    ) -> None:
        self._renderer = renderer
        self._reader = table_reader
        self._ocr = ocr
        self._interpreter = interpreter
        self._tol = abs_tolerance
        self._workers = max(1, int(page_concurrency))
        self._skip_max = int(skip_page_max_amounts)

    # ── VLM visibility (never let a stub pass for a real model) ──────────────

    @property
    def vlm_label(self) -> str:
        return getattr(self._reader, "label", type(self._reader).__name__)

    def vlm_usage(self):
        """Usage accumulated by the table reader (tokens, time, cost) if it tracks it."""
        return getattr(self._reader, "usage", None)

    # ── PDF path ──────────────────────────────────────────────────────────────

    def process_pdf(self, content: bytes, source_file: str = "") -> AuditReport:
        fingerprint = hashlib.sha256(content).digest()
        doc = self._renderer.render(content, PDF, fingerprint=fingerprint)
        return self._process_rendered(doc, source_file)

    def process_image(self, content: bytes, mime_type: str, source_file: str = "") -> AuditReport:
        fingerprint = hashlib.sha256(content).digest()
        doc = self._renderer.render(content, mime_type, fingerprint=fingerprint)
        return self._process_rendered(doc, source_file)

    def _process_rendered(self, doc: RenderedDocument, source_file: str) -> AuditReport:
        if self.vlm_label.startswith("stub") or type(self._reader).__name__ == "StubTableReader":
            log.warning(
                "VLM_BACKEND=stub: no tables are read from PDF/image pages — the report "
                "will be empty. Set VLM_BACKEND=anthropic (or local) for real extraction."
            )

        # Each page is handled by one worker, `page_concurrency` pages at a time:
        #   1. OCR (optional, local, ~2 s) — evidence for cell verification, and
        #   2. unless OCR clearly shows a narrative page with no amounts
        #      (domain/page_filter.py), one table-reader (VLM) call.
        # Results keep page order; any provider error propagates and fails the job
        # (it never falls back to another reader).
        with ThreadPoolExecutor(max_workers=self._workers) as pool:
            per_page = list(pool.map(self._process_page, doc.pages))

        all_tables: list[FinancialTable] = [t for tables, _, _ in per_page for t in tables]
        skipped = tuple(page.number for page, (_, _, skip) in zip(doc.pages, per_page) if skip)
        ocr_pages = {o.number: o for _, o, _ in per_page if o is not None and o.words}
        if self._ocr is not None:
            empty = [page.number for page, (_, o, _) in zip(doc.pages, per_page)
                     if o is None or not o.words]
            if empty:
                # No OCR text gives nothing to verify against: "OCR unavailable"
                # is not a disagreement with the VLM, so those pages are left out.
                log.warning(
                    "OCR returned no text for page(s) %s (OCR_BACKEND=%s); cell-level "
                    "OCR/VLM verification skipped there.",
                    empty, getattr(self._ocr, "name", type(self._ocr).__name__),
                )
        if skipped:
            log.info(
                "Skipped %d page(s) with no financial table (OCR found ≤ %d amounts): %s",
                len(skipped), self._skip_max, list(skipped),
            )

        merged = merge_page_continuations(all_tables)
        if len(merged) != len(all_tables):
            log.info("Joined %d table(s) that continue across pages", len(all_tables) - len(merged))

        report = self._validate_and_report(merged, ocr_pages, source_file)
        return replace(report, pages_skipped=skipped) if skipped else report

    def _process_page(self, page) -> tuple[tuple[FinancialTable, ...], OcrPage | None, bool]:
        orient = getattr(self._ocr, "orient", None)
        if orient is not None:
            page = orient(page)            # sideways scans → upright, for OCR and VLM alike
        ocr_page = self._ocr_page(page) if self._ocr is not None else None
        if ocr_page is not None and looks_tableless(ocr_page, self._skip_max):
            log.info(
                "Page %d: no financial table (OCR: %d words, %d amounts) — not sent to %s",
                page.number, len(ocr_page.words), amount_count(ocr_page), self.vlm_label,
            )
            return (), ocr_page, True
        return self._extract_page(page), ocr_page, False

    def _ocr_page(self, page) -> OcrPage:
        read_page = getattr(self._ocr, "read_page", None)
        if read_page is not None:
            return read_page(page)
        return self._ocr.read(RenderedDocument(b"", (page,)))[0]

    def _extract_page(self, page) -> tuple[FinancialTable, ...]:
        tables = self._reader.extract_tables(page)
        log.info("Page %d: extracted %d table(s) via %s", page.number, len(tables), self.vlm_label)
        return tuple(tables)

    # ── Excel path ────────────────────────────────────────────────────────────

    def process_tables(
        self,
        tables: list[FinancialTable],
        source_file: str = "",
        ocr_pages: dict[int, OcrPage] | None = None,
    ) -> AuditReport:
        return self._validate_and_report(tables, ocr_pages or {}, source_file)

    # ── Shared validation ─────────────────────────────────────────────────────

    def _validate_and_report(
        self,
        tables: list[FinancialTable],
        ocr_pages: Mapping[int, OcrPage],
        source_file: str,
    ) -> AuditReport:
        results: list[ValidationResult] = []
        for table in tables:
            result = self._validate_table(table, ocr_pages)
            results.append(result)
            if result.has_errors:
                log.warning(
                    "Table '%s': %d casting + %d cross-casting errors",
                    table.title,
                    len(result.casting_errors),
                    len(result.cross_casting_errors),
                )

        # LLM interpretation (optional)
        findings = ()
        if self._interpreter is not None:
            findings = self._interpreter.interpret(results)

        return _build_report(source_file, results, findings)

    def _validate_table(
        self,
        table: FinancialTable,
        ocr_pages: Mapping[int, OcrPage],
    ) -> ValidationResult:
        # Recognise the table structure once (columns, totals and their components)
        # and run every check against it — see domain/structure.py.
        structure = analyze_structure(table, abs_tolerance=self._tol)
        casting_errors, casting_checks = run_casting(
            table, abs_tolerance=self._tol, structure=structure,
        )
        cross_errors, cross_checks = run_cross_casting(
            table, abs_tolerance=self._tol, structure=structure,
        )
        casting_errors, cross_errors = consolidate_findings(
            casting_errors, cross_errors, structure, abs_tolerance=self._tol,
        )
        warnings = tuple(dict.fromkeys(table.extraction_warnings + structure.warnings))

        # Cell-level OCR/VLM verification (only for PDF-sourced tables)
        # A figure is verified when OCR reads the same digits, or when it takes part in
        # a sum that foots (a misread figure would break that sum).  Only figures with
        # neither confirmation and contrary OCR evidence become review items.
        verifications: list[CellVerification] = []
        if ocr_pages:
            vouched = confirmed_cells(table, structure, self._tol)
            # Columns of note references ("Note", "إيضاح") are not amounts and must not
            # become OCR disagreements — their values are 7, 8, 2.3 … not figures.
            note_cols = {c.index for c in structure.columns if c.role == "note"}
            for row in table.rows:
                if not row.kind.is_amount_row:
                    continue
                cells = narrow_bbox_per_column(row, structure, ocr_pages, table)
                for pos, cell in enumerate(cells):
                    if not cell.raw_text or pos in note_cols:
                        continue
                    v = verify_cell(cell, ocr_pages)
                    if not v.agreed and (row.index, pos) in vouched:
                        v = replace(v, agreed=True, status="AGREEMENT",
                                    reason="confirmed_by_arithmetic")
                    verifications.append(v)

        return ValidationResult(
            table=table,
            casting_errors=casting_errors,
            cross_casting_errors=cross_errors,
            cell_verifications=tuple(verifications),
            warnings=warnings,
            casting_checks=casting_checks,
            cross_casting_checks=cross_checks,
        )


# ── Report builder ────────────────────────────────────────────────────────────

def _build_report(source_file, results, findings) -> AuditReport:
    total_casting = sum(len(r.casting_errors) for r in results)
    total_cross = sum(len(r.cross_casting_errors) for r in results)

    if total_casting == 0 and total_cross == 0:
        summary_ar = "لم تُكتشف أخطاء حسابية في الجداول المالية."
        summary_en = "No arithmetic errors detected in financial tables."
    else:
        summary_ar = (
            f"اكتُشف {total_casting} خطأ تدقيق عمودي/أفقي "
            f"و{total_cross} خطأ تقاطع في {len(results)} جدول."
        )
        summary_en = (
            f"Found {total_casting} casting error(s) and "
            f"{total_cross} cross-casting error(s) across {len(results)} table(s)."
        )

    return AuditReport(
        source_file=source_file,
        tables_processed=len(results),
        validation_results=tuple(results),
        findings=findings,
        summary_ar=summary_ar,
        summary_en=summary_en,
    )
