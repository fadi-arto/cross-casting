"""
Layer: Application — Build the pipeline from config.
This is the only place that imports concrete adapters.
"""
from __future__ import annotations

import logging

from config.settings import Settings
from ports.interpreter import ErrorInterpreter
from ports.ocr import OcrEngine
from ports.reader import TableReader
from ports.renderer import PageRenderer

from .pipeline import CrossCastingPipeline

log = logging.getLogger(__name__)


def build_pipeline(settings: Settings) -> CrossCastingPipeline:
    renderer = _build_renderer(settings)
    reader = _build_reader(settings)
    ocr = _build_ocr(settings)
    interpreter = _build_interpreter(settings)
    return CrossCastingPipeline(
        renderer=renderer,
        table_reader=reader,
        ocr=ocr,
        interpreter=interpreter,
        abs_tolerance=settings.app.abs_tolerance,
        page_concurrency=settings.app.page_concurrency,
        skip_page_max_amounts=settings.app.skip_page_max_amounts,
    )


def _build_renderer(settings: Settings) -> PageRenderer:
    from adapters.pdf_renderer import PdfRenderer
    return PdfRenderer(max_pages=settings.app.max_pdf_pages, dpi=settings.app.render_dpi)


def _build_reader(settings: Settings) -> TableReader:
    backend = settings.vlm.backend
    if backend == "stub":
        from adapters.vlm_stub import StubTableReader
        return StubTableReader()
    if backend == "anthropic":
        a = settings.anthropic
        if not a.has_key:
            # No fallback to the stub: a missing key must fail loudly.
            raise ValueError("VLM_BACKEND=anthropic requires ANTHROPIC_API_KEY (missing or empty)")
        from adapters.vlm_anthropic import AnthropicTableReader
        if settings.vlm.temperature not in (None, 1, 1.0):
            log.warning(
                "VLM_TEMPERATURE=%s is ignored for the Anthropic backend: the installed SDK uses "
                "API version 2026-10-21, which rejects sampling parameters, and current Claude "
                "models only use their default sampling.", settings.vlm.temperature,
            )
        return AnthropicTableReader(
            api_key=a.api_key.get_secret_value(),
            model=a.model,
            # ANTHROPIC_* overrides when set; otherwise the shared VLM_* settings
            max_tokens=a.max_tokens if a.max_tokens is not None else settings.vlm.max_tokens,
            timeout=a.timeout_seconds if a.timeout_seconds is not None else settings.vlm.timeout_seconds,
            max_retries=a.max_retries,
            effort=(a.effort or "").strip().lower() or None,
            cache=_build_page_cache(settings),
        )
    # local (OpenAI-compatible)
    from adapters.vlm_openai import OpenAITableReader
    return OpenAITableReader(
        base_url=settings.vlm.base_url,
        api_key=settings.vlm.api_key.get_secret_value(),
        model=settings.vlm.model,
        temperature=settings.vlm.temperature,
        max_tokens=settings.vlm.max_tokens,
        seed=settings.vlm.seed,
        timeout=settings.vlm.timeout_seconds,
        max_retries=settings.vlm.max_retries,
        json_mode=settings.vlm.json_mode,
    )


def _build_page_cache(settings: Settings):
    directory = (settings.vlm.cache_dir or "").strip()
    if not directory:
        return None
    from adapters.page_cache import PageCache
    try:
        return PageCache(directory)
    except OSError as exc:
        log.warning("VLM_CACHE_DIR=%s is not usable (%s) — page cache disabled", directory, exc)
        return None


def _build_ocr(settings: Settings) -> OcrEngine | None:
    backend = settings.ocr.backend
    if backend == "none":
        return None
    if backend == "stub":
        from adapters.ocr_stub import StubOcr
        return StubOcr()
    from adapters.ocr_tesseract import TesseractOcr
    return TesseractOcr(langs=settings.ocr.langs, timeout_seconds=settings.ocr.timeout_seconds)


def _build_interpreter(settings: Settings) -> ErrorInterpreter | None:
    if not settings.llm.enabled:
        return None
    from adapters.llm_interpreter import LlmErrorInterpreter
    return LlmErrorInterpreter(
        base_url=settings.llm.base_url,
        api_key=settings.llm.api_key.get_secret_value(),
        model=settings.llm.model,
        temperature=settings.llm.temperature,
        max_tokens=settings.llm.max_tokens,
        timeout=settings.llm.timeout_seconds,
        json_mode=settings.llm.json_mode,
    )
