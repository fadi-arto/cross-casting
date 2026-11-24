#!/usr/bin/env python3
"""
Evaluate a review report against a ground-truth answer key.

Usage:
    python scripts/eval_against_key.py <job_result.json> <answer_key.json>

`job_result.json` is whatever `GET /jobs/{id}/review` returned (or, equivalently,
the file saved from the review page).  `answer_key.json` is:

    [
        {
            "id": "E07",
            "page": 23,
            "row": "Total revenue",
            "column": "Pipe manufacturing",
            "expected": 561218,
            "shown": 560018
        },
        ...
    ]

A finding matches an answer-key entry when:
    - the finding's `location.page` equals the entry's `page`,
    - the finding's `location.row` equals the entry's `row` (case-insensitive,
      whitespace-collapsed), and
    - the finding's `location.column`, or the row-total column for horizontal
      checks, equals the entry's `column`.

Any finding without a matching answer-key entry is a false positive.
Any answer-key entry no finding reached is a false negative.

The script prints TP, FP, FN, precision, recall, F1 and lists each miss, so a
regression can be acted on immediately.  Exit code 0 ⇔ TP == len(answer_key) and
FP == 0; otherwise 1.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


def _norm(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _iter_findings(review: dict):
    for group in review.get("tables", []):
        for item in group.get("items", []):
            loc = item.get("location") or {}
            yield {
                "id": item.get("id"),
                "category": item.get("category"),
                "check": item.get("check"),
                "severity": item.get("severity"),
                "page": loc.get("page"),
                "row": loc.get("row"),
                "column": loc.get("column"),
                "section": loc.get("section"),
                "title": (item.get("title") or {}).get("en"),
            }


def _key(entry: dict) -> tuple[int | None, str, str]:
    return (
        entry.get("page"),
        _norm(entry.get("row")),
        _norm(entry.get("column")),
    )


def evaluate(review: dict, answer_key: list[dict]) -> dict:
    findings = list(_iter_findings(review))
    key_by_tuple = {_key(e): e for e in answer_key}

    true_positives: list[tuple[dict, dict]] = []
    false_positives: list[dict] = []
    matched_keys: set[tuple] = set()

    for f in findings:
        k = (f["page"], _norm(f["row"]), _norm(f["column"]))
        if k in key_by_tuple:
            true_positives.append((key_by_tuple[k], f))
            matched_keys.add(k)
        else:
            false_positives.append(f)
    false_negatives = [e for k, e in key_by_tuple.items() if k not in matched_keys]

    tp, fp, fn = len(true_positives), len(false_positives), len(false_negatives)
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "tp": tp, "fp": fp, "fn": fn,
        "precision": precision, "recall": recall, "f1": f1,
        "true_positives": true_positives,
        "false_positives": false_positives,
        "false_negatives": false_negatives,
    }


def _print_report(result: dict) -> None:
    print(f"TP={result['tp']}  FP={result['fp']}  FN={result['fn']}  "
          f"precision={result['precision']:.3f}  recall={result['recall']:.3f}  "
          f"F1={result['f1']:.3f}")

    if result["false_positives"]:
        print("\nFalse positives (findings not in the answer key):")
        for f in result["false_positives"]:
            print(f"  p{f['page']:>3}  row={f['row']!r:<40}  col={f['column']!r:<25}  "
                  f"{f['category']}/{f['check']}  — {f['title']}")

    if result["false_negatives"]:
        print("\nFalse negatives (answer-key entries no finding reached):")
        for e in result["false_negatives"]:
            print(f"  [{e.get('id','?')}] p{e.get('page'):>3}  row={e.get('row')!r:<40}  "
                  f"col={e.get('column')!r:<25}  "
                  f"expected={e.get('expected')}  shown={e.get('shown')}")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument("review", type=Path, help="GET /jobs/{id}/review response JSON")
    ap.add_argument("answer_key", type=Path, help="Ground-truth entries")
    args = ap.parse_args(argv)

    review = json.loads(args.review.read_text(encoding="utf-8"))
    answer_key = json.loads(args.answer_key.read_text(encoding="utf-8"))

    result = evaluate(review, answer_key)
    _print_report(result)
    return 0 if result["fp"] == 0 and result["fn"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
