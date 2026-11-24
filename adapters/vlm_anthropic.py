"""
Layer: Adapters — Anthropic Claude VLM for table extraction.

One request per page image (PDF pages rendered by PdfRenderer, or an uploaded
PNG/JPEG). The model returns the tables as JSON constrained by a JSON schema
(structured outputs, `output_config.format`), so no forced tool call is needed —
forced `tool_choice` is rejected by several current models (Opus 5.5, Sonnet 5.5,
Fable 5.x), which would have made the backend break on a model change.

Failures are never hidden: every API error, timeout, rate limit, refusal or
truncated answer raises `VlmProviderError` with a message that is safe to show
and log (it never contains the API key). There is no fallback to the stub.

Sampling parameters: SDK `anthropic` >= 2.0 sends API version 2026-10-21, on
which `temperature` / `top_p` / `top_k` are rejected for every model, and
current models (e.g. Claude Haiku 5.5) accept only the default sampling anyway.
The factory therefore does not pass VLM_TEMPERATURE here and logs that it is
ignored for this backend.
"""
from __future__ import annotations

import base64
import io
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import anthropic

from domain.models import FinancialTable, PageImage
from prompts.table_extract import TABLE_EXTRACT_PROMPT
from adapters.vlm_openai import _parse_tables  # reuse parsing logic

log = logging.getLogger(__name__)

_EFFORTS = frozenset({"low", "medium", "high", "xhigh", "max"})
_MAX_IMAGE_BYTES = 3_750_000      # API limit is 5 MB per base64 image (≈ 3.75 MB raw)
_MAX_IMAGE_SIDE = 8000            # API hard limit per side
_RESIZE_LONG_SIDE = 2400          # used only when an image exceeds a limit

# USD per million tokens (input, output) — Anthropic first-party list prices,
# used only for an *estimate* in logs. Unknown models → no estimate.
_PRICING_PER_MTOK: dict[str, tuple[float, float]] = {
    "claude-haiku-5-5": (0.10, 0.50),     # prompts ≤ 100K tokens
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-opus-5-5": (4.00, 20.00),
    "claude-opus-5": (5.00, 25.00),
    "claude-fable-5-2": (8.00, 40.00),
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-opus-4-8": (5.00, 25.00),
}

_NULLABLE_STRING = {"anyOf": [{"type": "string"}, {"type": "null"}]}

# Structured-output schema: every object closed (additionalProperties: false)
# and every property required, as structured outputs expect.
TABLES_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["tables"],
    "properties": {
        "tables": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["title", "headers", "continues_previous_page", "rows"],
                "properties": {
                    "title": {"type": "string"},
                    "headers": {"type": "array", "items": {"type": "string"}},
                    "continues_previous_page": {"type": "boolean"},
                    "rows": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["label", "values", "kind", "bbox"],
                            "properties": {
                                "label": {"type": "string"},
                                "values": {"type": "array", "items": _NULLABLE_STRING},
                                "kind": {
                                    "type": "string",
                                    "enum": ["data", "subtotal", "total"],
                                },
                                "bbox": {
                                    "anyOf": [
                                        {"type": "array", "items": {"type": "integer"}},
                                        {"type": "null"},
                                    ]
                                },
                            },
                        },
                    },
                },
            },
        }
    },
}


class VlmProviderError(RuntimeError):
    """A VLM call failed. `str(error)` is safe to show and log — no secrets."""

    def __init__(
        self,
        message: str,
        *,
        kind: str,
        retryable: bool,
        status_code: int | None = None,
        request_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.retryable = retryable
        self.status_code = status_code
        self.request_id = request_id


@dataclass
class VlmUsage:
    """Accumulated over every request this reader made (one per page)."""
    model: str
    requests: int = 0
    failures: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    seconds: float = 0.0
    request_ids: list[str] = field(default_factory=list)
    lock: Any = field(default_factory=threading.Lock, repr=False, compare=False)

    @property
    def estimated_cost_usd(self) -> float | None:
        price = _PRICING_PER_MTOK.get(self.model)
        if price is None:
            return None
        inp, out = price
        # cache reads bill at 0.1x, cache writes at 1.25x of the input price
        return (
            self.input_tokens * inp
            + self.cache_read_input_tokens * inp * 0.1
            + self.cache_creation_input_tokens * inp * 1.25
            + self.output_tokens * out
        ) / 1_000_000

    def summary(self) -> str:
        cost = self.estimated_cost_usd
        cost_txt = f"${cost:.6f} (estimate)" if cost is not None else "n/a (unknown model price)"
        return (
            f"model={self.model} requests={self.requests} failures={self.failures} "
            f"input_tokens={self.input_tokens} output_tokens={self.output_tokens} "
            f"cache_read={self.cache_read_input_tokens} duration={self.seconds:.2f}s cost={cost_txt}"
        )


class AnthropicTableReader:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        max_tokens: int = 8192,
        timeout: float = 180.0,
        max_retries: int = 2,
        effort: str | None = None,
        cache: Any | None = None,       # adapters.page_cache.PageCache
        client: Any | None = None,      # injectable for tests
    ) -> None:
        if not api_key or not api_key.strip():
            raise ValueError("VLM_BACKEND=anthropic requires ANTHROPIC_API_KEY (it is empty)")
        if effort is not None and effort not in _EFFORTS:
            raise ValueError(f"ANTHROPIC_EFFORT must be one of {sorted(_EFFORTS)}, got {effort!r}")
        self._model = model
        self._max_tokens = max_tokens
        self._effort = effort
        self._cache = cache
        self._max_retries = max_retries
        self._client = client or anthropic.Anthropic(
            api_key=api_key, timeout=timeout, max_retries=max_retries,
        )
        self.usage = VlmUsage(model=model)
        log.info(
            "Anthropic VLM ready: model=%s max_tokens=%d timeout=%.0fs max_retries=%d effort=%s",
            model, max_tokens, timeout, max_retries, effort or "model default",
        )

    @property
    def label(self) -> str:
        return f"anthropic/{self._model}"

    def extract_tables(self, page: PageImage) -> tuple[FinancialTable, ...]:
        mime, data = _prepare_image(page)
        cache_key = None
        if self._cache is not None:
            cache_key = self._cache.key(
                self._model, self._effort or "", TABLE_EXTRACT_PROMPT,
                json.dumps(TABLES_SCHEMA, sort_keys=True), mime, data,
            )
            cached = self._cache.get(cache_key)
            if cached is not None:
                tables = _parse_tables(cached, page.number)
                log.info("Anthropic VLM page %d: %d table(s) from cache (no API call)",
                         page.number, len(tables))
                return tables
        output_config: dict[str, Any] = {"format": {"type": "json_schema", "schema": TABLES_SCHEMA}}
        if self._effort:
            output_config["effort"] = self._effort
        request: dict[str, Any] = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "output_config": output_config,
            "messages": [{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": mime,
                            "data": base64.standard_b64encode(data).decode("ascii"),
                        },
                    },
                    {"type": "text", "text": TABLE_EXTRACT_PROMPT},
                ],
            }],
        }

        started = time.perf_counter()
        try:
            response = self._client.messages.create(**request)
        except anthropic.APIError as exc:
            elapsed = time.perf_counter() - started
            with self.usage.lock:
                self.usage.requests += 1
                self.usage.failures += 1
                self.usage.seconds += elapsed
            err = self._translate(exc)
            log.error(
                "Anthropic VLM request FAILED: page=%d model=%s duration=%.2fs kind=%s status=%s "
                "request_id=%s retryable=%s — %s",
                page.number, self._model, elapsed, err.kind, err.status_code,
                err.request_id, err.retryable, err,
            )
            raise err from exc

        elapsed = time.perf_counter() - started
        u = response.usage
        in_tok = u.input_tokens or 0
        out_tok = u.output_tokens or 0
        cache_read = getattr(u, "cache_read_input_tokens", None) or 0
        cache_write = getattr(u, "cache_creation_input_tokens", None) or 0
        request_id = getattr(response, "_request_id", None)
        with self.usage.lock:
            self.usage.requests += 1
            self.usage.seconds += elapsed
            self.usage.input_tokens += in_tok
            self.usage.output_tokens += out_tok
            self.usage.cache_read_input_tokens += cache_read
            self.usage.cache_creation_input_tokens += cache_write
            if request_id:
                self.usage.request_ids.append(request_id)

        log.info(
            "Anthropic VLM request ok: page=%d model=%s duration=%.2fs input_tokens=%d "
            "output_tokens=%d cache_read=%d stop_reason=%s request_id=%s",
            page.number, response.model, elapsed, in_tok, out_tok, cache_read,
            response.stop_reason, request_id,
        )

        if response.stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            category = getattr(details, "category", None) if details else None
            self._fail()
            raise VlmProviderError(
                f"Claude declined to process page {page.number} (refusal, category={category}).",
                kind="refusal", retryable=False, request_id=request_id,
            )
        if response.stop_reason == "max_tokens":
            self._fail()
            raise VlmProviderError(
                f"Claude's answer for page {page.number} was cut off at max_tokens="
                f"{self._max_tokens}; the extracted tables would be incomplete. Raise "
                "VLM_MAX_TOKENS (or ANTHROPIC_MAX_TOKENS).",
                kind="truncated", retryable=False, request_id=request_id,
            )

        text = "".join(b.text for b in response.content if getattr(b, "type", None) == "text")
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            self._fail()
            raise VlmProviderError(
                f"Claude returned output for page {page.number} that is not valid JSON "
                f"(stop_reason={response.stop_reason}).",
                kind="invalid_output", retryable=False, request_id=request_id,
            ) from exc
        answer = json.dumps(payload, ensure_ascii=False)
        tables = _parse_tables(answer, page.number)
        if cache_key is not None:
            self._cache.put(cache_key, answer)
        log.info("Anthropic VLM page %d: %d table(s) extracted", page.number, len(tables))
        return tables

    def _fail(self) -> None:
        with self.usage.lock:
            self.usage.failures += 1

    # ── errors ────────────────────────────────────────────────────────────────

    def _translate(self, exc: anthropic.APIError) -> VlmProviderError:
        status = getattr(exc, "status_code", None)
        rid = getattr(exc, "request_id", None)
        api_msg = _api_message(exc)
        retries = f" after {self._max_retries} automatic retr{'y' if self._max_retries == 1 else 'ies'}"

        if isinstance(exc, anthropic.AuthenticationError):
            return VlmProviderError(
                "Anthropic authentication failed (401): ANTHROPIC_API_KEY is invalid, revoked "
                "or not an API key.", kind="auth", retryable=False, status_code=status, request_id=rid)
        if isinstance(exc, anthropic.PermissionDeniedError):
            return VlmProviderError(
                f"Anthropic denied access (403) for model '{self._model}': {api_msg}",
                kind="permission", retryable=False, status_code=status, request_id=rid)
        if isinstance(exc, anthropic.NotFoundError):
            return VlmProviderError(
                f"Anthropic model or endpoint not found (404): check ANTHROPIC_MODEL="
                f"'{self._model}'. {api_msg}",
                kind="model_not_found", retryable=False, status_code=status, request_id=rid)
        if isinstance(exc, anthropic.RateLimitError):
            retry_after = None
            if getattr(exc, "response", None) is not None:
                retry_after = exc.response.headers.get("retry-after")
            hint = f" Retry after {retry_after}s." if retry_after else ""
            return VlmProviderError(
                f"Anthropic rate limit reached (429){retries}.{hint}",
                kind="rate_limit", retryable=True, status_code=status, request_id=rid)
        if isinstance(exc, anthropic.BadRequestError):
            return VlmProviderError(
                f"Anthropic rejected the request (400): {api_msg}",
                kind="bad_request", retryable=False, status_code=status, request_id=rid)
        if isinstance(exc, anthropic.APITimeoutError):
            return VlmProviderError(
                f"Anthropic request timed out{retries} (timeout per attempt: VLM_TIMEOUT_SECONDS / "
                "ANTHROPIC_TIMEOUT_SECONDS).", kind="timeout", retryable=True)
        if isinstance(exc, anthropic.APIConnectionError):
            return VlmProviderError(
                f"Could not reach the Anthropic API{retries} (network/DNS/TLS error).",
                kind="connection", retryable=True)
        if isinstance(exc, anthropic.APIStatusError) and status is not None and status >= 500:
            kind = "overloaded" if status == 529 else "server_error"
            return VlmProviderError(
                f"Anthropic service error ({status}){retries}: {api_msg}",
                kind=kind, retryable=True, status_code=status, request_id=rid)
        return VlmProviderError(
            f"Anthropic API error ({status}): {api_msg}",
            kind="api_error", retryable=False, status_code=status, request_id=rid)


def _api_message(exc: anthropic.APIError) -> str:
    """The API's own error message (never contains request headers or the key)."""
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict) and err.get("message"):
            return str(err["message"])[:500]
    return type(exc).__name__


def _prepare_image(page: PageImage) -> tuple[str, bytes]:
    """Keep the image within API limits (≤ ~3.75 MB raw, ≤ 8000 px per side)."""
    mime = page.mime_type if page.mime_type in ("image/png", "image/jpeg", "image/gif", "image/webp") \
        else "image/png"
    data = page.content
    too_large = len(data) > _MAX_IMAGE_BYTES
    too_wide = (page.width or 0) > _MAX_IMAGE_SIDE or (page.height or 0) > _MAX_IMAGE_SIDE
    if not (too_large or too_wide):
        return mime, data

    from PIL import Image
    with Image.open(io.BytesIO(data)) as img:
        img = img.convert("RGB")
        scale = min(1.0, _RESIZE_LONG_SIDE / max(img.size))
        if scale < 1.0:
            img = img.resize((int(img.width * scale), int(img.height * scale)), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        if buf.tell() > _MAX_IMAGE_BYTES:
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=90)
            mime = "image/jpeg"
        else:
            mime = "image/png"
    log.info(
        "Page %d image resized for the API limits (%d → %d bytes)",
        page.number, len(data), buf.tell(),
    )
    return mime, buf.getvalue()
