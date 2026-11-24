"""Layer: Adapters — LLM error interpreter (OpenAI-compatible)."""
from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from decimal import Decimal
from typing import Any

import openai
from openai import OpenAI

from domain.models import CastingError, CrossCastingError, InterpretedFinding, ValidationResult
from domain.money import display
from prompts.error_interpret import INTERPRET_SYSTEM, build_interpret_prompt

log = logging.getLogger(__name__)

_SEVERITY_MAP = frozenset(["critical", "high", "medium", "low"])


class LlmErrorInterpreter:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        temperature: float = 0.0,
        max_tokens: int = 4096,
        timeout: float = 120.0,
        max_retries: int = 2,
        json_mode: str = "json_object",
    ) -> None:
        self._model = model
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._json_mode = json_mode
        self._client = OpenAI(
            base_url=base_url, api_key=api_key, timeout=timeout, max_retries=max_retries
        )

    def interpret(self, results: Sequence[ValidationResult]) -> tuple[InterpretedFinding, ...]:
        errors = _collect_errors(results)
        if not errors:
            return ()

        errors_json = json.dumps(errors, ensure_ascii=False, indent=2)
        prompt = build_interpret_prompt(errors_json)

        messages: list[Any] = [
            {"role": "system", "content": INTERPRET_SYSTEM},
            {"role": "user", "content": prompt},
        ]
        options: dict[str, Any] = {
            "temperature": self._temperature,
            "max_tokens": self._max_tokens,
        }
        if self._json_mode == "json_object":
            options["response_format"] = {"type": "json_object"}

        try:
            resp = self._client.chat.completions.create(
                model=self._model, messages=messages, **options
            )
        except Exception as exc:
            log.warning("LLM interpreter failed: %s", exc)
            return _fallback_findings(results)

        raw = resp.choices[0].message.content or ""
        return _parse_findings(raw)


# ── Error serialisation ───────────────────────────────────────────────────────

def _collect_errors(results: Sequence[ValidationResult]) -> list[dict]:
    out: list[dict] = []
    for r in results:
        for e in r.casting_errors:
            out.append({
                "type": "casting_error",
                "table": e.table_title,
                "axis": e.axis,
                "label": e.label,
                "declared": str(e.declared) if e.declared is not None else None,
                "computed": str(e.computed),
                "delta": str(e.delta),
            })
        for e in r.cross_casting_errors:
            out.append({
                "type": "cross_casting_error",
                "table": e.table_title,
                "description": e.description,
                "row_total": str(e.row_total),
                "col_total": str(e.col_total),
                "grand_total": str(e.grand_total) if e.grand_total is not None else None,
                "delta": str(e.delta),
            })
    return out


# ── Response parsing ──────────────────────────────────────────────────────────

def _parse_findings(raw: str) -> tuple[InterpretedFinding, ...]:
    try:
        text = raw.strip()
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            return ()
        payload = json.loads(text[start:end + 1])
    except (json.JSONDecodeError, ValueError):
        return ()

    findings_raw = payload.get("findings")
    if not isinstance(findings_raw, list):
        return ()

    findings: list[InterpretedFinding] = []
    for item in findings_raw:
        if not isinstance(item, dict):
            continue
        severity = str(item.get("severity", "medium")).lower()
        if severity not in _SEVERITY_MAP:
            severity = "medium"
        findings.append(InterpretedFinding(
            severity=severity,  # type: ignore[arg-type]
            finding_type=str(item.get("finding_type", "unknown")),
            description_ar=str(item.get("description_ar", "")),
            description_en=str(item.get("description_en", "")),
            table=str(item.get("table", "")),
            details=str(item.get("details", "")),
        ))
    return tuple(findings)


def _fallback_findings(results: Sequence[ValidationResult]) -> tuple[InterpretedFinding, ...]:
    """Deterministic fallback when the LLM call fails."""
    out: list[InterpretedFinding] = []
    for r in results:
        for e in r.casting_errors:
            out.append(InterpretedFinding(
                severity="high",
                finding_type="casting_error",
                description_ar=f"خطأ في التدقيق العمودي/الأفقي للجدول «{e.table_title}»",
                description_en=f"Casting error in table '{e.table_title}'",
                table=e.table_title,
                details=f"{e.axis} '{e.label}': declared={e.declared}, computed={e.computed}, delta={e.delta}",
            ))
        for e in r.cross_casting_errors:
            out.append(InterpretedFinding(
                severity="critical",
                finding_type="cross_casting_error",
                description_ar=f"خطأ تقاطع في الجدول «{e.table_title}»: {e.description}",
                description_en=f"Cross-casting error in '{e.table_title}'",
                table=e.table_title,
                details=f"row_total={e.row_total}, col_total={e.col_total}, delta={e.delta}",
            ))
    return tuple(out)
