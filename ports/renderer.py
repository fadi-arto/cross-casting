from __future__ import annotations

from typing import Protocol

from domain.models import RenderedDocument


class PageRenderer(Protocol):
    def render(self, content: bytes, mime_type: str, *, fingerprint: bytes) -> RenderedDocument: ...
