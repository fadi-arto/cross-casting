"""Layer: Adapters — pypdfium2 PDF renderer (adapted from auditagent_infra)."""
from __future__ import annotations

import io

import pypdfium2 as pdfium
from PIL import Image, UnidentifiedImageError

from domain.models import PageImage, RenderedDocument

PNG = "image/png"
JPEG = "image/jpeg"
PDF = "application/pdf"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class PdfRenderer:
    def __init__(self, *, max_pages: int = 50, dpi: int = 150) -> None:
        self._max_pages = max_pages
        self._scale = dpi / 72

    def render(self, content: bytes, mime_type: str, *, fingerprint: bytes) -> RenderedDocument:
        if mime_type in {PNG, JPEG}:
            w, h = _image_size(content)
            return RenderedDocument(fingerprint, (PageImage(1, mime_type, content, w, h),))
        if mime_type == PDF:
            return RenderedDocument(fingerprint, self._render_pdf(content))
        raise ValueError(f"Unsupported mime type for PDF renderer: {mime_type!r}")

    def _render_pdf(self, content: bytes) -> tuple[PageImage, ...]:
        try:
            pdf = pdfium.PdfDocument(content)
        except pdfium.PdfiumError as exc:
            raise ValueError("Corrupt or encrypted PDF") from exc
        try:
            count = len(pdf)
            if count == 0:
                raise ValueError("PDF has no pages")
            if count > self._max_pages:
                raise ValueError(
                    f"PDF has {count} pages, exceeds limit of {self._max_pages}"
                )
            pages: list[PageImage] = []
            for i in range(count):
                image = pdf[i].render(scale=self._scale).to_pil()
                buf = io.BytesIO()
                image.save(buf, format="PNG")
                pages.append(PageImage(i + 1, PNG, buf.getvalue(), image.width, image.height))
            return tuple(pages)
        finally:
            pdf.close()


def _image_size(content: bytes) -> tuple[int | None, int | None]:
    try:
        with Image.open(io.BytesIO(content)) as img:
            return img.width, img.height
    except (UnidentifiedImageError, OSError):
        return None, None
