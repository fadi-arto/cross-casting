from __future__ import annotations

from typing import Protocol

from domain.models import OcrPage, RenderedDocument


class OcrEngine(Protocol):
    name: str

    def read(self, document: RenderedDocument) -> tuple[OcrPage, ...]: ...
