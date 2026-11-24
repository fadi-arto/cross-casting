"""Layer: Adapters — Tesseract OCR (adapted from auditagent_infra)."""
from __future__ import annotations

import io
import logging
import os
import re
from dataclasses import replace
from decimal import ROUND_HALF_UP, Decimal

import pytesseract
from PIL import Image, UnidentifiedImageError

from domain.models import OcrPage, OcrWord, PageImage, RenderedDocument

log = logging.getLogger(__name__)

# One thread per Tesseract process: pages are OCR'd in parallel by the pipeline.
os.environ.setdefault("OMP_THREAD_LIMIT", "1")


# Orientation detection (Tesseract OSD). Measured on a 66-page scanned report:
# the 8 landscape pages scanned sideways scored 2.97–13.1, upright pages are
# reported as 0. Below this confidence the page is left as it is.
MIN_ORIENTATION_CONFIDENCE = 2.0
_ROTATE = re.compile(r"Rotate:\s*(\d+)")
_CONF = re.compile(r"Orientation confidence:\s*([\d.]+)")


class TesseractOcr:
    name = "tesseract"

    def orient(self, page: PageImage) -> PageImage:
        """Return the page turned upright (landscape tables are often scanned sideways).

        Both readers then see an upright page: Tesseract cannot read sideways text at
        all, and the VLM misaligns rows on it.  Any problem → the page unchanged.
        """
        try:
            image = Image.open(io.BytesIO(page.content))
            image.load()
            osd = pytesseract.image_to_osd(image, timeout=self._timeout)
        except Exception as exc:                      # no text, OSD data missing, timeout …
            log.debug("Page %d: orientation not detected (%s)", page.number, exc)
            return page
        rot, conf = _ROTATE.search(osd), _CONF.search(osd)
        if not rot or not conf:
            return page
        degrees, confidence = int(rot.group(1)) % 360, float(conf.group(1))
        if degrees == 0 or confidence < MIN_ORIENTATION_CONFIDENCE:
            return page
        upright = image.rotate(-degrees, expand=True)     # PIL turns counter-clockwise
        buf = io.BytesIO()
        upright.save(buf, format="PNG")
        log.info("Page %d: turned %d° upright (orientation confidence %.1f)",
                 page.number, degrees, confidence)
        return replace(page, mime_type="image/png", content=buf.getvalue(),
                       width=upright.width, height=upright.height)

    def __init__(self, langs: str = "ara+eng", timeout_seconds: int = 60) -> None:
        self._langs = langs
        self._timeout = timeout_seconds

    def read(self, document: RenderedDocument) -> tuple[OcrPage, ...]:
        return tuple(self.read_page(p) for p in document.pages)

    def read_page(self, page) -> OcrPage:
        return OcrPage(page.number, self._read_page(page))

    def _read_page(self, page) -> tuple[OcrWord, ...]:
        try:
            image = Image.open(io.BytesIO(page.content))
            image.load()
        except (UnidentifiedImageError, OSError):
            log.warning("Page %d: image unreadable — treated as unverified", page.number)
            return ()
        try:
            data = pytesseract.image_to_data(
                image,
                lang=self._langs,
                output_type=pytesseract.Output.DICT,
                timeout=self._timeout,
            )
        except pytesseract.TesseractNotFoundError as exc:
            raise RuntimeError(
                "tesseract not installed. Install with: apt install tesseract-ocr tesseract-ocr-ara"
            ) from exc
        except pytesseract.TesseractError as exc:
            if "language" in str(exc).lower():
                raise RuntimeError(
                    f"Missing Tesseract language pack for OCR_LANGS={self._langs!r}"
                ) from exc
            raise RuntimeError("Tesseract failed") from exc
        return _parse_words(data)


def _parse_words(data: dict) -> tuple[OcrWord, ...]:
    words: list[OcrWord] = []
    for i, raw in enumerate(data["text"]):
        text = str(raw).strip()
        confidence = float(str(data["conf"][i]))
        if not text or confidence < 0:
            continue
        left = int(str(data["left"][i]))
        top = int(str(data["top"][i]))
        width = int(str(data["width"][i]))
        height = int(str(data["height"][i]))
        words.append(OcrWord(
            text=text,
            confidence=(Decimal(str(confidence)) / 100).quantize(
                Decimal("0.001"), rounding=ROUND_HALF_UP
            ),
            bbox=(left, top, left + width, top + height),
        ))
    return tuple(words)
