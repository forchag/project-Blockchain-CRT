#!/usr/bin/env python3
"""Checks the output of reproduce_analytical_results.py against examples/expected_analytical_results.json.

Three kinds of check:
  1. value     every expected quantity is present and within tolerance;
  2. label     every quantity carries a valid provenance label, and the expected label;
  3. boundary  bench-measured, field-measured and measured quantities are never labelled analytical,
               and no analytical quantity has the value None (an analytical result is always computed).

    python3 analysis/validate_analytical_outputs.py [--results results/analytical/analytical_results.json]

Exit 0 when every check passes. Passing means the formulas and code agree with the stated
parameters; it does not show that any historical measurement occurred (docs/EVIDENCE_BOUNDARIES.md).
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import ANALYTICAL, BENCH, FIELD, MEASURED, RESULTS, is_valid_label, path  # noqa: E402

EXPECTED = path("examples", "expected_analytical_results.json")


def close(got, want, tol: float) -> bool:
    if isinstance(want, bool) or isinstance(got, bool):
        return got is want
    if isinstance(want, (int, float)) and isinstance(got, (int, float)):
        return abs(got - want) <= tol
    return got == want


def check(results: list[dict], expected: dict) -> list[str]:
    problems = []
    by_id = {}
    for r in results:
        if r["id"] in by_id:
            problems.append(f"duplicate id {r['id']}")
        by_id[r["id"]] = r
        if not is_valid_label(r["label"]):
            problems.append(f"{r['id']}: invalid label {r['label']!r}")
        if r["label"] == ANALYTICAL and r["value"] is None:
            problems.append(f"{r['id']}: analytical quantity without a value")
        if r["id"].startswith("reported.") and r["label"] not in (BENCH, FIELD, MEASURED):
            problems.append(f"{r['id']}: reported manuscript value labelled {r['label']!r}")
    default_tol = expected.get("tolerance_default", 1e-6)
    for rid, e in expected["expected"].items():
        r = by_id.get(rid)
        if r is None:
            problems.append(f"{rid}: missing from results")
            continue
        if not close(r["value"], e["value"], e.get("tolerance", default_tol)):
            problems.append(f"{rid}: value {r['value']!r}, expected {e['value']!r}")
        if r["label"] != e["label"]:
            problems.append(f"{rid}: label {r['label']!r}, expected {e['label']!r}")
    for rid in expected.get("labels_that_must_never_be_analytical", []):
        r = by_id.get(rid)
        if r is None:
            problems.append(f"{rid}: missing from results")
        elif r["label"] == ANALYTICAL or r["label"].startswith("derived"):
            problems.append(f"{rid}: measured quantity labelled {r['label']!r}")
    return problems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default=os.path.join(RESULTS, "analytical", "analytical_results.json"))
    ap.add_argument("--expected", default=EXPECTED)
    a = ap.parse_args()
    if not os.path.exists(a.results):
        sys.exit(f"{a.results} not found: run analysis/reproduce_analytical_results.py first")
    results = json.load(open(a.results))["results"]
    expected = json.load(open(a.expected))
    problems = check(results, expected)
    counts = {}
    for r in results:
        counts[r["label"]] = counts.get(r["label"], 0) + 1
    print(f"{len(results)} quantities: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    print(f"{len(expected['expected'])} expected values checked, {len(problems)} problems")
    for p in problems:
        print("  FAIL", p)
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
