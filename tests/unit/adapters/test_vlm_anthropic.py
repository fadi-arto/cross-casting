"""Mock tests for the Anthropic VLM adapter — no API key, no network."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

import json
import logging
from decimal import Decimal
from types import SimpleNamespace

import anthropic
import httpx2
import pytest
from pydantic import SecretStr

from adapters.vlm_anthropic import TABLES_SCHEMA, AnthropicTableReader, VlmProviderError
from application.factory import _build_reader
from application.page_merge import merge_page_continuations
from config.settings import AnthropicSettings, AppSettings, LlmSettings, OcrSettings, Settings, VlmSettings
from domain.models import Cell, FinancialTable, PageImage, RowKind, TableRow

FAKE_KEY = "sk-ant-test-THIS-MUST-NEVER-BE-LOGGED-0123456789"
PAGE = PageImage(number=1, mime_type="image/png", content=b"\x89PNG fake", width=100, height=100)

GOOD_JSON = {
    "tables": [{
        "title": "Balance Sheet",
        "headers": ["FY2024", "FY2025"],
        "continues_previous_page": False,
        "rows": [
            {"label": "Cash", "values": ["180,000.00", "220,000.00"], "kind": "data", "bbox": None},
            {"label": "Inventory", "values": ["(1,000.50)", None], "kind": "data", "bbox": [1, 2, 3, 4]},
            {"label": "Total assets", "values": ["179,000.00", "220,000.00"], "kind": "total", "bbox": None},
        ],
    }]
}


def _response(payload=GOOD_JSON, stop_reason="end_turn", text=None, **usage):
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text if text is not None else json.dumps(payload))],
        usage=SimpleNamespace(
            input_tokens=usage.get("input_tokens", 1500), output_tokens=usage.get("output_tokens", 300),
            cache_read_input_tokens=0, cache_creation_input_tokens=0,
        ),
        stop_reason=stop_reason,
        stop_details=SimpleNamespace(category="cyber") if stop_reason == "refusal" else None,
        model="claude-haiku-5-5",
        _request_id="req_test123",
    )


class FakeClient:
    def __init__(self, result):
        self.calls = []
        self._result = result
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


def _reader(result, **kw):
    client = FakeClient(result)
    return AnthropicTableReader(api_key=FAKE_KEY, model="claude-haiku-5-5", client=client, **kw), client


_REQ = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


def _status_error(cls, status, message="boom", headers=None):
    resp = httpx2.Response(status, request=_REQ, headers=headers or {"request-id": "req_err"})
    return cls(message, response=resp, body={"type": "error", "error": {"type": "x", "message": message}})


# ── Request shape ─────────────────────────────────────────────────────────────

class TestRequest:
    def test_image_and_structured_output(self):
        reader, client = _reader(_response(), max_tokens=4096, effort="low")
        reader.extract_tables(PAGE)
        (req,) = client.calls
        assert req["model"] == "claude-haiku-5-5"
        assert req["max_tokens"] == 4096
        assert req["output_config"]["format"] == {"type": "json_schema", "schema": TABLES_SCHEMA}
        assert req["output_config"]["effort"] == "low"
        image, text = req["messages"][0]["content"]
        assert image["type"] == "image" and image["source"]["media_type"] == "image/png"
        assert text["type"] == "text"

    def test_no_sampling_params_and_no_forced_tool(self):
        reader, client = _reader(_response())
        reader.extract_tables(PAGE)
        req = client.calls[0]
        for banned in ("temperature", "top_p", "top_k", "tool_choice", "tools", "extra_body"):
            assert banned not in req

    def test_schema_is_closed_for_structured_outputs(self):
        def walk(node):
            if isinstance(node, dict):
                if node.get("type") == "object":
                    assert node.get("additionalProperties") is False
                    assert set(node["required"]) == set(node["properties"])
                for v in node.values():
                    walk(v)
            elif isinstance(node, list):
                for v in node:
                    walk(v)
        walk(TABLES_SCHEMA)


# ── Parsing ───────────────────────────────────────────────────────────────────

class TestParsing:
    def test_values_preserved_exactly(self):
        reader, _ = _reader(_response())
        (t,) = reader.extract_tables(PAGE)
        assert t.headers == ("FY2024", "FY2025")
        assert [r.label for r in t.rows] == ["Cash", "Inventory", "Total assets"]
        assert t.rows[0].cells[0].value == Decimal("180000.00")
        assert t.rows[1].cells[0].value == Decimal("-1000.50")       # (1,000.50) → negative
        assert t.rows[1].cells[1].value is None
        assert t.rows[1].cells[0].raw_text == "(1,000.50)"           # original text kept
        assert t.rows[2].kind == RowKind.TOTAL

    def test_usage_is_accumulated(self):
        reader, _ = _reader(_response(input_tokens=1000, output_tokens=200))
        reader.extract_tables(PAGE)
        reader.extract_tables(PAGE)
        u = reader.usage
        assert (u.requests, u.input_tokens, u.output_tokens, u.failures) == (2, 2000, 400, 0)
        assert u.request_ids == ["req_test123", "req_test123"]
        assert u.estimated_cost_usd == pytest.approx((2000 * 0.10 + 400 * 0.50) / 1e6)

    def test_logs_model_duration_tokens_status(self, caplog):
        caplog.set_level(logging.INFO, logger="adapters.vlm_anthropic")
        reader, _ = _reader(_response(input_tokens=1234, output_tokens=56))
        reader.extract_tables(PAGE)
        line = next(r.getMessage() for r in caplog.records if "request ok" in r.getMessage())
        for part in ("model=claude-haiku-5-5", "duration=", "input_tokens=1234", "output_tokens=56",
                     "stop_reason=end_turn", "request_id=req_test123"):
            assert part in line


# ── Failures are explicit ─────────────────────────────────────────────────────

class TestErrors:
    @pytest.mark.parametrize("exc, kind, retryable", [
        (_status_error(anthropic.AuthenticationError, 401), "auth", False),
        (_status_error(anthropic.PermissionDeniedError, 403), "permission", False),
        (_status_error(anthropic.NotFoundError, 404, "model: claude-x"), "model_not_found", False),
        (_status_error(anthropic.RateLimitError, 429, headers={"retry-after": "7"}), "rate_limit", True),
        (_status_error(anthropic.BadRequestError, 400), "bad_request", False),
        (_status_error(anthropic.InternalServerError, 500), "server_error", True),
        (_status_error(anthropic.APIStatusError, 529, "Overloaded"), "overloaded", True),
        (anthropic.APITimeoutError(request=_REQ), "timeout", True),
        (anthropic.APIConnectionError(request=_REQ), "connection", True),
    ])
    def test_api_errors_raise_provider_error(self, exc, kind, retryable):
        reader, _ = _reader(exc)
        with pytest.raises(VlmProviderError) as info:
            reader.extract_tables(PAGE)
        assert info.value.kind == kind
        assert info.value.retryable is retryable
        assert reader.usage.failures == 1

    def test_rate_limit_message_has_retry_after(self):
        reader, _ = _reader(_status_error(anthropic.RateLimitError, 429, headers={"retry-after": "7"}))
        with pytest.raises(VlmProviderError, match="Retry after 7s"):
            reader.extract_tables(PAGE)

    def test_truncated_output_is_an_error_not_partial_tables(self):
        reader, _ = _reader(_response(stop_reason="max_tokens", text='{"tables": [{"title": "T'))
        with pytest.raises(VlmProviderError) as info:
            reader.extract_tables(PAGE)
        assert info.value.kind == "truncated"

    def test_refusal(self):
        reader, _ = _reader(_response(stop_reason="refusal", text=""))
        with pytest.raises(VlmProviderError, match="category=cyber"):
            reader.extract_tables(PAGE)

    def test_invalid_json(self):
        reader, _ = _reader(_response(text="not json"))
        with pytest.raises(VlmProviderError) as info:
            reader.extract_tables(PAGE)
        assert info.value.kind == "invalid_output"


# ── Secrets ───────────────────────────────────────────────────────────────────

class TestSecrets:
    def test_key_never_logged_or_in_errors(self, caplog):
        caplog.set_level(logging.DEBUG)
        reader, _ = _reader(_status_error(anthropic.AuthenticationError, 401, "invalid x-api-key"))
        with pytest.raises(VlmProviderError) as info:
            reader.extract_tables(PAGE)
        ok_reader, _ = _reader(_response())
        ok_reader.extract_tables(PAGE)
        assert FAKE_KEY not in str(info.value)
        assert FAKE_KEY not in repr(info.value)
        for record in caplog.records:
            assert FAKE_KEY not in record.getMessage()
        assert FAKE_KEY not in ok_reader.usage.summary()

    def test_empty_key_rejected(self):
        with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
            AnthropicTableReader(api_key="  ", model="claude-haiku-5-5", client=FakeClient(None))


# ── Factory: configuration, precedence, no silent fallback ────────────────────

def _settings_obj(backend, key, temperature, anth):
    # Settings requires db/storage models; build them without reading .env
    from config.settings import DatabaseSettings, StorageSettings
    return Settings(
        app=AppSettings(_env_file=None),
        vlm=VlmSettings(_env_file=None, backend=backend, timeout_seconds=180, max_tokens=8192,
                        temperature=temperature),
        anthropic=AnthropicSettings(_env_file=None, api_key=SecretStr(key) if key is not None else None,
                                    model="claude-haiku-5-5", **anth),
        ocr=OcrSettings(_env_file=None), llm=LlmSettings(_env_file=None),
        db=DatabaseSettings(_env_file=None), storage=StorageSettings(_env_file=None),
    )


class TestFactory:
    @pytest.mark.parametrize("key", [None, "", "   "])
    def test_missing_key_fails_loudly(self, key):
        with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
            _build_reader(_settings_obj("anthropic", key, 0.0, {}))

    def test_vlm_settings_apply_when_no_override(self):
        r = _build_reader(_settings_obj("anthropic", "sk-ant-x", 1.0, {}))
        assert r._max_tokens == 8192
        assert r._client.timeout == 180

    def test_anthropic_overrides_win(self):
        r = _build_reader(_settings_obj("anthropic", "sk-ant-x", 1.0,
                                        {"timeout_seconds": 60, "max_tokens": 2048, "effort": "LOW"}))
        assert (r._max_tokens, r._client.timeout, r._effort) == (2048, 60, "low")

    def test_temperature_ignored_with_warning(self, caplog):
        caplog.set_level(logging.WARNING, logger="application.factory")
        _build_reader(_settings_obj("anthropic", "sk-ant-x", 0.0, {}))
        assert any("VLM_TEMPERATURE=0.0 is ignored" in r.getMessage() for r in caplog.records)

    def test_invalid_effort_rejected(self):
        with pytest.raises(ValueError, match="ANTHROPIC_EFFORT"):
            _build_reader(_settings_obj("anthropic", "sk-ant-x", 1.0, {"effort": "turbo"}))

    def test_stub_is_labelled(self):
        assert _build_reader(_settings_obj("stub", None, 1.0, {})).label.startswith("stub")


# ── Multi-page tables ─────────────────────────────────────────────────────────

def _tbl(page, title, labels, headers=("FY2024", "FY2025"), cont=False):
    rows = tuple(
        TableRow(i, lab, tuple(Cell(Decimal(v), str(v), i, j, page=page) for j, v in enumerate(vals)),
                 kind=RowKind.TOTAL if lab.startswith("Total") else RowKind.DATA)
        for i, (lab, vals) in enumerate(labels)
    )
    return FinancialTable(title=title, headers=headers, rows=rows, page=page, continues_previous=cont)


class TestPageMerge:
    def test_flagged_continuation_is_joined(self):
        a = _tbl(1, "Balance Sheet", [("Cash", (1, 2)), ("Total assets", (1, 2))])
        b = _tbl(2, "Balance Sheet (continued)", [("Loans", (1, 2)), ("Total liabilities", (1, 2))], cont=True)
        (m,) = merge_page_continuations([a, b])
        assert [r.label for r in m.rows] == ["Cash", "Total assets", "Loans", "Total liabilities"]
        assert [r.index for r in m.rows] == [0, 1, 2, 3]
        assert m.rows[2].cells[0].page == 2                         # traceability kept
        assert any("joined" in w for w in m.extraction_warnings)

    def test_same_title_and_headers_joined_without_flag(self):
        a = _tbl(1, "Balance Sheet", [("Cash", (1, 2))])
        b = _tbl(2, "Balance Sheet — continued", [("Loans", (1, 2))])
        assert len(merge_page_continuations([a, b])) == 1

    def test_different_tables_not_joined(self):
        a = _tbl(1, "Income Statement", [("Revenue", (1, 2))])
        b = _tbl(2, "Balance Sheet", [("Cash", (1, 2))])
        assert len(merge_page_continuations([a, b])) == 2

    def test_flagged_but_incompatible_columns_warns(self):
        a = _tbl(1, "BS", [("Cash", (1, 2))])
        b = _tbl(2, "BS", [("Loans", (1, 2, 3))], headers=("A", "B", "C"), cont=True)
        out = merge_page_continuations([a, b])
        assert len(out) == 2 and any("do not match" in w for w in out[1].extraction_warnings)

    def test_same_page_tables_never_joined(self):
        a = _tbl(1, "BS", [("Cash", (1, 2))])
        b = _tbl(1, "BS", [("Loans", (1, 2))], cont=True)
        assert len(merge_page_continuations([a, b])) == 2


# ── Page cache ────────────────────────────────────────────────────────────────

class TestPageCache:
    def test_second_read_of_same_page_makes_no_api_call(self, tmp_path):
        from adapters.page_cache import PageCache
        reader, client = _reader(_response(), cache=PageCache(tmp_path))
        first = reader.extract_tables(PAGE)
        second = reader.extract_tables(PAGE)
        assert len(client.calls) == 1
        assert [t.title for t in first] == [t.title for t in second] == ["Balance Sheet"]
        assert [c.raw_text for r in second[0].rows for c in r.cells] == \
               [c.raw_text for r in first[0].rows for c in r.cells]

    def test_page_number_comes_from_the_page_not_the_cache(self, tmp_path):
        from adapters.page_cache import PageCache
        reader, _ = _reader(_response(), cache=PageCache(tmp_path))
        reader.extract_tables(PAGE)
        same_image_page_7 = PageImage(7, PAGE.mime_type, PAGE.content, 100, 100)
        assert reader.extract_tables(same_image_page_7)[0].page == 7

    def test_other_model_or_image_is_a_miss(self, tmp_path):
        from adapters.page_cache import PageCache
        cache = PageCache(tmp_path)
        r1, c1 = _reader(_response(), cache=cache)
        r1.extract_tables(PAGE)
        r2 = AnthropicTableReader(api_key=FAKE_KEY, model="claude-sonnet-5-5",
                                  client=FakeClient(_response()), cache=cache)
        r2.extract_tables(PAGE)
        assert len(r2._client.calls) == 1
        r1.extract_tables(PageImage(1, "image/png", b"\x89PNG other", 100, 100))
        assert len(c1.calls) == 2

    def test_failed_answer_is_not_cached(self, tmp_path):
        from adapters.page_cache import PageCache
        cache = PageCache(tmp_path)
        bad, _ = _reader(_response(stop_reason="max_tokens"), cache=cache)
        with pytest.raises(VlmProviderError):
            bad.extract_tables(PAGE)
        good, client = _reader(_response(), cache=cache)
        good.extract_tables(PAGE)
        assert len(client.calls) == 1

    def test_unwritable_cache_never_fails_the_read(self, tmp_path):
        from adapters.page_cache import PageCache
        cache = PageCache(tmp_path)
        cache.put = lambda k, t: (_ for _ in ()).throw(AssertionError) if False else None
        cache._dir = tmp_path / "missing" / "x"
        reader, _ = _reader(_response(), cache=cache)
        assert reader.extract_tables(PAGE)
