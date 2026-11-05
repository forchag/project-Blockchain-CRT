"""Shared helpers for the analysis scripts: repository paths, configuration loading, provenance
labels, and output writing.

Every numeric quantity the public branch stores, prints or documents carries one provenance label
(docs/EVIDENCE_BOUNDARIES.md):

  analytical       computed here from a published formula and stated parameters
  bench-measured   measured on controlled lab hardware; NOT reproducible from public inputs
  field-measured   measured in the 2025 deployment; needs restricted field records
  measured         a measurement whose method or boundary is undocumented in surviving records
  derived (from X) arithmetic on quantities of class X (e.g. a ratio of two bench values)

Scripts never read an original-data directory. Inputs come from config/*.yaml and examples/.
Outputs go to results/ (git-ignored) unless --out says otherwise.
"""
from __future__ import annotations

import csv
import json
import math
import os
import statistics

import yaml

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
RESULTS = os.path.join(ROOT, "results")

ANALYTICAL = "analytical"
BENCH = "bench-measured"
FIELD = "field-measured"
MEASURED = "measured"
ILLUSTRATIVE = "illustrative"
BASE_LABELS = (ANALYTICAL, BENCH, FIELD, MEASURED)
NOT_REPRODUCIBLE = {
    BENCH: "bench-measured; not reproducible from public inputs",
    FIELD: "field-measured; requires restricted field records",
    MEASURED: "measured; measurement boundary undocumented, no public derivation",
}


def derived(*sources: str) -> str:
    """Label for arithmetic on quantities of the given classes, e.g. derived(BENCH)."""
    for s in sources:
        if s not in BASE_LABELS and not s.startswith("public "):
            raise ValueError(f"unknown source class {s!r}")
    return "derived (from " + " + ".join(sources) + ")"


def is_valid_label(label: str) -> bool:
    if label in BASE_LABELS:
        return True
    if label.startswith("derived (from ") and label.endswith(")"):
        parts = label[len("derived (from "):-1].split(" + ")
        return all(p in BASE_LABELS or p.startswith("public ") for p in parts)
    return False


def tag(label: str) -> str:
    """Console tag. Non-reproducible classes say so in the tag itself."""
    if label == BENCH:
        return "[bench-measured, not reproducible]"
    if label == FIELD:
        return "[field-measured, restricted data]"
    if label == MEASURED:
        return "[measured, boundary undocumented]"
    return f"[{label}]"


def path(*parts: str) -> str:
    return os.path.join(ROOT, *parts)


def load_yaml(rel_or_abs: str) -> dict:
    p = rel_or_abs if os.path.isabs(rel_or_abs) else path(rel_or_abs)
    with open(p) as f:
        return yaml.safe_load(f)


def radio_config(rel: str = "config/radio.example.yaml") -> dict:
    return load_yaml(rel)


def analytical_parameters(rel: str = "config/analytical_parameters.yaml") -> dict:
    return load_yaml(rel)


def record(rid: str, quantity: str, value, unit: str, label: str, basis: str, note: str = "") -> dict:
    """One labelled output quantity. `value` None means the value is deliberately not provided."""
    if not is_valid_label(label):
        raise ValueError(f"{rid}: invalid provenance label {label!r}")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"{rid}: non-finite value")
    return {"id": rid, "quantity": quantity, "value": value, "unit": unit, "label": label, "basis": basis, "note": note}


def print_record(r: dict) -> None:
    v = r["value"]
    if v is None:
        shown = "not included"
    elif isinstance(v, float):
        shown = f"{v:,.6g}"
    else:
        shown = str(v)
    unit = f" {r['unit']}" if r["unit"] else ""
    note = f"  ({r['note']})" if r["note"] else ""
    print(f"{tag(r['label']):38s} {r['quantity']}: {shown}{unit}{note}")


def write_csv(rows: list[dict], out_path: str) -> str:
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    cols = list(rows[0]) if rows else []
    for r in rows:
        cols += [k for k in r if k not in cols]
    with open(out_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    return out_path


def write_json(obj, out_path: str) -> str:
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(obj, f, indent=2, sort_keys=False)
        f.write("\n")
    return out_path


def read_csv(rel_or_abs: str) -> list[dict]:
    p = rel_or_abs if os.path.isabs(rel_or_abs) else path(rel_or_abs)
    with open(p, newline="") as f:
        return [r for r in csv.DictReader(line for line in f if not line.startswith("#"))]


def describe(values) -> dict:
    x = [float(v) for v in values if v is not None and v == v]
    if not x:
        return {"n": 0}
    xs = sorted(x)

    def q(p):  # linear interpolation, same as pandas' default
        k = (len(xs) - 1) * p
        lo, hi = math.floor(k), math.ceil(k)
        return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)

    return {"n": len(x), "mean": round(statistics.fmean(x), 3), "median": round(q(0.5), 3),
            "sd": round(statistics.stdev(x), 3) if len(x) > 1 else None, "p90": round(q(0.9), 3),
            "p95": round(q(0.95), 3), "min": round(xs[0], 3), "max": round(xs[-1], 3)}
