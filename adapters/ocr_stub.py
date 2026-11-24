"""Layer: Adapters — Stub OCR for testing (no Tesseract required)."""
from __future__ import annotations

from domain.models import OcrPage, RenderedDocument


class StubOcr:
    name = "stub"

    def read(self, document: RenderedDocument) -> tuple[OcrPage, ...]:
        return tuple(OcrPage(p.number, ()) for p in document.pages)

    def read_page(self, page) -> OcrPage:
        return OcrPage(page.number, ())
