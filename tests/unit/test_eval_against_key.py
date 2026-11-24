"""Unit tests for scripts/eval_against_key.py."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from eval_against_key import evaluate  # noqa: E402


REVIEW = {
    "tables": [{
        "page": 23,
        "items": [{
            "id": "x1", "category": "casting", "check": "vertical", "severity": "high",
            "title": {"en": "Row totals and column totals do not agree"},
            "location": {"page": 23, "row": "Total revenue",
                         "column": "Pipe manufacturing", "section": None},
        }],
    }],
}


def test_perfect_hit_gives_precision_recall_one():
    key = [{"id": "E07", "page": 23, "row": "Total revenue",
            "column": "Pipe manufacturing", "expected": 561218, "shown": 560018}]
    r = evaluate(REVIEW, key)
    assert (r["tp"], r["fp"], r["fn"]) == (1, 0, 0)
    assert r["precision"] == 1.0 and r["recall"] == 1.0 and r["f1"] == 1.0


def test_false_positive_pushes_precision_below_one():
    r = evaluate(REVIEW, [])
    assert (r["tp"], r["fp"], r["fn"]) == (0, 1, 0)
    assert r["precision"] == 0.0 and r["recall"] == 1.0


def test_false_negative_pushes_recall_below_one():
    key = [{"id": "E07", "page": 23, "row": "Total revenue",
            "column": "Pipe manufacturing"},
           {"id": "E08", "page": 10, "row": "Cost of sales", "column": "2025"}]
    r = evaluate(REVIEW, key)
    assert (r["tp"], r["fp"], r["fn"]) == (1, 0, 1)
    assert r["precision"] == 1.0 and r["recall"] == 0.5


def test_label_matching_is_whitespace_and_case_insensitive():
    key = [{"id": "E07", "page": 23, "row": "  TOTAL  REVENUE ",
            "column": "pipe manufacturing"}]
    assert evaluate(REVIEW, key)["tp"] == 1
