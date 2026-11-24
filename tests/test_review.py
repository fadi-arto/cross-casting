"""Review report: classification, ranking, guidance hints, API and PDF export."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace as NS
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from api.routes import app
from application import guidance as g
from application.review import build_review, classify
from domain.cross_casting import DESC_COL_VS_GRAND, DESC_ROW_VS_COL, DESC_ROW_VS_GRAND

TABLE_ID = uuid.uuid4()
JOB = NS(
    id=uuid.uuid4(), filename="fs.pdf", mime_type="application/pdf", status="COMPLETED",
    report={"tables_processed": 3, "total_checks": 40,
            "results": [{"casting_checks": 30, "cross_casting_checks": 10}]},
)
TABLES = [NS(id=TABLE_ID, table_name="Balance Sheet", page=8, sheet=None)]


def issue(issue_type="CASTING_ERROR", *, expected="100", actual="90", diff="10",
          severity="high", status="open", ref=None, desc="d"):
    return NS(
        id=uuid.uuid4(), job_id=JOB.id, table_id=TABLE_ID, issue_type=issue_type,
        severity=severity, status=status, table_name="Balance Sheet", description=desc,
        description_ar=desc, expected_value=expected, actual_value=actual, difference=diff,
        source_reference=ref if ref is not None else
        {"source": "pdf", "page": 8, "row": "Total assets", "axis": "column", "column": "2025"},
        created_at=datetime.now(timezone.utc),
    )


# ── classification ────────────────────────────────────────────────────────────

def test_classify_casting_kinds():
    assert classify(issue()) == ("casting", "vertical")
    assert classify(issue(ref={"axis": "row"})) == ("casting", "horizontal")
    assert classify(issue(ref={"axis": "column", "relation": "Σ Credit = Σ Debit (a trial balance must balance)"})) \
        == ("casting", "trial_balance")


def test_classify_cross_kinds():
    mk = lambda d: issue("CROSS_CASTING_ERROR", desc=d)  # noqa: E731
    assert classify(mk(DESC_ROW_VS_COL)) == ("cross_casting", "row_vs_col")
    assert classify(mk(DESC_ROW_VS_GRAND)) == ("cross_casting", "row_vs_grand")
    assert classify(mk(DESC_COL_VS_GRAND)) == ("cross_casting", "col_vs_grand")
    assert classify(issue("OCR_VLM_DISAGREEMENT")) == ("verification", "ocr_vlm")


def test_every_check_has_guidance_and_value_labels():
    from schemas.review import Check
    for check in Check.__args__:
        assert check in g.CATALOG and check in g.VALUE_LABELS
        e = g.CATALOG[check]
        for pair in (e.title, e.explanation, *e.causes, *e.steps):
            assert pair[0].strip() and pair[1].strip()


# ── report building ───────────────────────────────────────────────────────────

def test_cross_values_derived_from_delta():
    # row_vs_grand: Σrows=500, grand=450 → delta 50
    it = issue("CROSS_CASTING_ERROR", expected="500", actual="480", diff="50", desc=DESC_ROW_VS_GRAND)
    rep = build_review(JOB, [it], TABLES)
    item = rep.tables[0].items[0]
    assert [v.value for v in item.values] == ["500.00", "450.00"]
    assert item.delta == "50.00" and item.direction == "first_higher"


def test_priority_critical_cross_first_and_stable_under_filter():
    a = issue(diff="5", severity="high")
    b = issue("CROSS_CASTING_ERROR", severity="critical", diff="1", desc=DESC_ROW_VS_COL)
    c = issue(diff="500", severity="high")
    full = build_review(JOB, [a, b, c], TABLES)
    prio = {i.id: i.priority for grp in full.tables for i in grp.items}
    assert prio[str(b.id)] == 1 and prio[str(c.id)] == 2 and prio[str(a.id)] == 3
    only_casting = build_review(JOB, [a, b, c], TABLES, scope="casting")
    assert {i.id: i.priority for grp in only_casting.tables for i in grp.items} == \
        {str(c.id): 2, str(a.id): 3}
    assert only_casting.summary.total == 2 and only_casting.summary.cross_casting == 0


def test_status_filter_and_is_clean():
    open_i, done = issue(), issue(status="resolved")
    rep = build_review(JOB, [open_i, done], TABLES, status="resolved")
    assert [i.id for g_ in rep.tables for i in g_.items] == [str(done.id)]
    assert rep.summary.open == 1 and not rep.is_clean          # summary ignores status filter
    assert build_review(JOB, [done], TABLES).is_clean           # nothing open
    assert build_review(JOB, [], TABLES).conclusion.en.startswith("No open")


def test_location_and_coverage():
    rep = build_review(JOB, [issue()], TABLES)
    loc = rep.tables[0].items[0].location
    assert (loc.page, loc.row, loc.column) == (8, "Total assets", "2025")
    assert rep.tables[0].page == 8
    assert (rep.coverage.tables_processed, rep.coverage.casting_checks) == (3, 30)


def test_unknown_scope_rejected():
    with pytest.raises(ValueError):
        build_review(JOB, [], TABLES, scope="nope")


# ── hints ─────────────────────────────────────────────────────────────────────

D = Decimal


def test_hints():
    assert g.hints_for(D(100), D(100), D(0)) == []
    assert any("rounding" in h[0] for h in g.hints_for(D("100.5"), D(100), D("0.5")))
    assert any("transposed" in h[0] for h in g.hints_for(D(1000), D(954), D(46))) is False
    assert any("transposed" in h[0] for h in g.hints_for(D(1000), D(945), D(54)))      # 54 % 9 == 0
    assert not any("transposed" in h[0] for h in g.hints_for(D(482), D(-243310), D(243792)))  # delta > figures
    assert any("sign" in h[0] for h in g.hints_for(D(250), D(-250), D(500)))
    assert any("10×" in h[0] for h in g.hints_for(D(5000), D(500), D(4500)))


# ── API ───────────────────────────────────────────────────────────────────────

@pytest.fixture()
def client():
    app.state.engine, app.state.minio, app.state.settings = MagicMock(), MagicMock(), MagicMock()
    return TestClient(app, raise_server_exceptions=False)


def _report(**kw):
    return build_review(JOB, [issue(), issue("CROSS_CASTING_ERROR", severity="critical",
                                              desc=DESC_ROW_VS_COL)], TABLES, **kw)


def test_review_endpoints(client):
    jid = str(JOB.id)
    with patch("api.review_routes._review_sync", side_effect=lambda e, j, scope, st: _report(scope=scope, status=st)):
        r = client.get(f"/jobs/{jid}/review")
        assert r.status_code == 200 and r.json()["summary"]["total"] == 2
        assert r.json()["tables"][0]["items"][0]["title"]["ar"]
        assert client.get(f"/jobs/{jid}/casting").json()["summary"]["total"] == 1
        assert client.get(f"/jobs/{jid}/cross-casting").json()["summary"]["total"] == 1
        assert client.get(f"/jobs/{jid}/review?status=bogus").status_code == 422


def test_review_requires_completed_job(client):
    from fastapi import HTTPException
    with patch("api.review_routes._review_sync", side_effect=HTTPException(409, "not completed")):
        assert client.get(f"/jobs/{uuid.uuid4()}/review").status_code == 409


@pytest.mark.parametrize("lang", ["ar", "en"])
def test_pdf_export(client, lang):
    with patch("api.review_routes._review_sync", side_effect=lambda e, j, scope, st: _report(scope=scope, status=st)):
        r = client.get(f"/jobs/{JOB.id}/review/pdf?lang={lang}")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert "attachment" in r.headers["content-disposition"] and lang in r.headers["content-disposition"]
    assert r.content.startswith(b"%PDF") and len(r.content) > 2000


def test_pdf_clean_report_and_bad_lang(client):
    with patch("api.review_routes._review_sync", side_effect=lambda e, j, scope, st: build_review(JOB, [], TABLES)):
        assert client.get(f"/jobs/{JOB.id}/review/pdf").status_code == 200
        assert client.get(f"/jobs/{JOB.id}/review/pdf?lang=fr").status_code == 422


def test_update_status(client):
    out = MagicMock()
    with patch("api.review_routes._set_status_sync", return_value=out) as m:
        client.patch(f"/jobs/{JOB.id}/issues/{uuid.uuid4()}", json={"status": "bogus"})
        assert m.call_count == 0                      # validation rejects before the handler runs
