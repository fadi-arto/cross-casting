"""Layer: Adapters — OpenAI-compatible VLM for table extraction."""
from __future__ import annotations

import base64
import json
import logging
from decimal import Decimal
from typing import Any

import openai
from openai import OpenAI

from domain.models import Cell, FinancialTable, PageImage, RowKind, TableRow
from domain.money import parse_financial_value
from domain.normalize import classify_row_label
from prompts.table_extract import TABLE_EXTRACT_PROMPT

log = logging.getLogger(__name__)

_TRANSIENT = (
    openai.APIConnectionError,
    openai.APITimeoutError,
    openai.RateLimitError,
    openai.InternalServerError,
)
_ROW_KIND_MAP: dict[str, RowKind] = {
    "data": RowKind.DATA,
    "subtotal": RowKind.SUBTOTAL,
    "total": RowKind.TOTAL,
    # structural rows are kept but never summed
    "header": RowKind.HEADER,
    "section_header": RowKind.SECTION_HEADER,
    "title": RowKind.TITLE,
    "subtitle": RowKind.SUBTITLE,
    "footnote": RowKind.FOOTNOTE,
    "empty": RowKind.EMPTY,
}


class OpenAITableReader:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        temperature: float = 0.0,
        max_tokens: int = 8192,
        seed: int | None = 0,
        timeout: float = 180.0,
        max_retries: int = 2,
        json_mode: str = "json_object",
    ) -> None:
        self._model = model
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._seed = seed
        self._json_mode = json_mode
        self._client = OpenAI(
            base_url=base_url,
            api_key=api_key,
            timeout=timeout,
            max_retries=max_retries,
        )

    def extract_tables(self, page: PageImage) -> tuple[FinancialTable, ...]:
        raw = self._chat(page)
        return _parse_tables(raw, page.number)

    def _chat(self, page: PageImage) -> str:
        image_b64 = base64.b64encode(page.content).decode()
        messages: list[Any] = [{
            "role": "user",
            "content": [
                {"type": "text", "text": TABLE_EXTRACT_PROMPT},
                {"type": "image_url", "image_url": {"url": f"data:{page.mime_type};base64,{image_b64}"}},
            ],
        }]
        options: dict[str, Any] = {
            "temperature": self._temperature,
            "max_tokens": self._max_tokens,
        }
        if self._seed is not None:
            options["seed"] = self._seed
        if self._json_mode == "json_object":
            options["response_format"] = {"type": "json_object"}

        try:
            resp = self._client.chat.completions.create(
                model=self._model, messages=messages, **options
            )
        except _TRANSIENT as exc:
            raise RuntimeError(f"VLM server unavailable: {type(exc).__name__}") from exc
        return resp.choices[0].message.content or ""


# ── JSON → domain model ───────────────────────────────────────────────────────

def _parse_tables(raw: str, page_number: int) -> tuple[FinancialTable, ...]:
    try:
        payload = _extract_json(raw)
    except ValueError as exc:
        log.warning("Could not parse VLM table output: %s", exc)
        return ()

    tables_raw = payload.get("tables")
    if not isinstance(tables_raw, list):
        return ()

    out: list[FinancialTable] = []
    for tbl in tables_raw:
        if not isinstance(tbl, dict):
            continue
        table = _parse_table(tbl, page_number)
        if table is not None:
            out.append(table)
    return tuple(out)


def _parse_table(tbl: dict, page_number: int) -> FinancialTable | None:
    title = str(tbl.get("title", "")).strip() or "Untitled"
    headers_raw = tbl.get("headers")
    rows_raw = tbl.get("rows")
    if not isinstance(headers_raw, list) or not isinstance(rows_raw, list):
        return None

    headers = tuple(str(h) for h in headers_raw)
    rows: list[TableRow] = []
    for row_idx, row_raw in enumerate(rows_raw):
        row = _parse_row(row_raw, row_idx, page_number)
        if row is not None:
            rows.append(row)

    if not rows:
        return None
    return FinancialTable(
        title=title,
        headers=headers,
        rows=tuple(rows),
        page=page_number,
        continues_previous=bool(tbl.get("continues_previous_page", False)),
    )


def _parse_row(row_raw: dict, row_idx: int, page_number: int) -> TableRow | None:
    if not isinstance(row_raw, dict):
        return None
    label = str(row_raw.get("label", "")).strip()
    values_raw = row_raw.get("values")
    if not isinstance(values_raw, list):
        return None

    kind_str = str(row_raw.get("kind", "data")).lower()
    kind = _ROW_KIND_MAP.get(kind_str, RowKind.DATA)
    if kind == RowKind.DATA:
        # Also try label-based inference as a fallback
        inferred = classify_row_label(label)
        if inferred != "data":
            kind = _ROW_KIND_MAP.get(inferred, RowKind.DATA)

    bbox_raw = row_raw.get("bbox")
    bbox = _parse_bbox(bbox_raw)

    cells: list[Cell] = []
    for col_idx, val in enumerate(values_raw):
        raw_text = str(val).strip() if val is not None else ""
        parsed = parse_financial_value(raw_text) if raw_text else None
        cells.append(Cell(
            value=parsed,
            raw_text=raw_text,
            row_index=row_idx,
            col_index=col_idx,
            bbox=bbox,
            page=page_number,
        ))

    return TableRow(index=row_idx, label=label, cells=tuple(cells), kind=kind)


def _parse_bbox(value: object):
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        x1, y1, x2, y2 = (int(float(v)) for v in value)
    except (TypeError, ValueError):
        return None
    return (x1, y1, x2, y2) if x2 > x1 and y2 > y1 and min(x1, y1) >= 0 else None


def _extract_json(raw: str) -> dict:
    text = raw.strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("No JSON object in model output")
    return json.loads(text[start:end + 1])
