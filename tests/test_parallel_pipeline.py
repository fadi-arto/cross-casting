"""Pages are read in parallel but results keep page order; errors still fail the job."""
from __future__ import annotations

import threading
import time
from types import SimpleNamespace as NS

import pytest

from application.pipeline import CrossCastingPipeline
from domain.models import OcrPage


class Reader:
    label = "fake/reader"

    def __init__(self, fail_on=None):
        self.active = self.peak = 0
        self.lock = threading.Lock()
        self.fail_on = fail_on

    def extract_tables(self, page):
        with self.lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
        time.sleep(0.05 * (5 - page.number))          # later pages finish first
        with self.lock:
            self.active -= 1
        if page.number == self.fail_on:
            raise RuntimeError("provider down")
        return (NS(title=f"t{page.number}", page_number=page.number),)


class Ocr:
    name = "fake"

    def read(self, doc):
        return tuple(OcrPage(p.number, ()) for p in doc.pages)


def _run(workers, reader, ocr=None):
    doc = NS(pages=[NS(number=n) for n in range(1, 5)])
    pipe = CrossCastingPipeline(None, reader, ocr, None, page_concurrency=workers)
    seen = {}
    pipe._validate_and_report = lambda tables, ocr_pages, src: seen.update(t=list(tables)) or "ok"
    import application.pipeline as mod
    mod.merge_page_continuations = lambda t: t
    assert pipe._process_rendered(doc, "x") == "ok"
    return [t.title for t in seen["t"]]


def test_parallel_keeps_page_order_and_overlaps():
    r = Reader()
    assert _run(4, r, Ocr()) == ["t1", "t2", "t3", "t4"]
    assert r.peak > 1


def test_sequential_when_one_worker():
    r = Reader()
    assert _run(1, r) == ["t1", "t2", "t3", "t4"] and r.peak == 1


def test_error_propagates():
    with pytest.raises(RuntimeError, match="provider down"):
        _run(4, Reader(fail_on=3))


class TextOcr:
    """Page 2 and 4 are narrative (no amounts); 1 and 3 are tables."""
    name = "fake"

    def read_page(self, p):
        from decimal import Decimal
        from domain.models import OcrWord
        words = ["text"] * 30 + (["1,234", "5,678", "90,000", "12,345", "6,789"] if p.number % 2 else [])
        return OcrPage(p.number, tuple(OcrWord(w, Decimal("0.9"), (0, 0, 1, 1)) for w in words))


def _run_skip(threshold):
    reader = Reader()
    calls = []
    orig = reader.extract_tables
    reader.extract_tables = lambda page: calls.append(page.number) or orig(page)
    doc = NS(pages=[NS(number=n) for n in range(1, 5)])
    pipe = CrossCastingPipeline(None, reader, TextOcr(), None, page_concurrency=4,
                                skip_page_max_amounts=threshold)
    from domain.models import AuditReport
    pipe._validate_and_report = lambda t, o, s: AuditReport(s, len(t), (), (), "", "")
    import application.pipeline as mod
    mod.merge_page_continuations = lambda t: t
    report = pipe._process_rendered(doc, "x")
    return sorted(calls), report


def test_narrative_pages_are_not_sent_and_are_reported():
    calls, report = _run_skip(4)
    assert calls == [1, 3]
    assert report.pages_skipped == (2, 4)


def test_skipping_disabled_reads_every_page():
    calls, report = _run_skip(-1)
    assert calls == [1, 2, 3, 4] and report.pages_skipped == ()
