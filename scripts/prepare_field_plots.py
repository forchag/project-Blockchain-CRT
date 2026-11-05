#!/usr/bin/env python3
"""Build twelve plot-level analysis rows from deposited 2025 crop/irrigation/labor CSVs.

Preserves original source bytes. Water is **scheduled application**, not independently
meter-confirmed consumption unless separately reconciled with the meter records.
"""
from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "results/manuscript"


def rows(source: Path, filename: str):
    with (source / filename).open(newline="", encoding="utf-8-sig") as fh:
        yield from csv.DictReader(fh)


def accumulate(source: Path, filename: str, field: str, divisor: float = 1.0) -> dict[str, float]:
    totals = defaultdict(float)
    for r in rows(source, filename):
        if r[field] != "":
            totals[r["plot_id"]] += float(r[field]) / divisor
    return dict(totals)


def write(path: Path, columns: list[str], values: list[dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, columns)
        writer.writeheader()
        writer.writerows(values)
    print("wrote", path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", type=Path, required=True, help="External agronomic CSV directory (not included)")
    ap.add_argument("--out", type=Path, default=OUTPUT / "field_plot_inputs.csv")
    args = ap.parse_args()

    plots = list(rows(args.data_dir, "plot_assignment.csv"))
    if len(plots) != 12 or len({x["plot_id"] for x in plots}) != 12:
        raise ValueError("Expected 12 unique plots in the 2025 randomized block design")
    ids = {x["plot_id"] for x in plots}

    yields = accumulate(args.data_dir, "per_plot_yield.csv", "yield_kg")
    scheduled_water = accumulate(args.data_dir, "per_plot_water_application.csv", "water_volume_l", divisor=1000.0)
    labor_hours = accumulate(args.data_dir, "per_plot_labour.csv", "hours")
    labor_costs = accumulate(args.data_dir, "per_plot_labour.csv", "labour_cost_usd")

    for name, totals in (("yield", yields), ("scheduled water", scheduled_water),
                         ("labor hours", labor_hours), ("labor costs", labor_costs)):
        missing = ids - totals.keys()
        unknown = totals.keys() - ids
        if missing or unknown:
            raise ValueError(f"{name}: missing plot IDs={sorted(missing)}; unexpected IDs={sorted(unknown)}")

    output = []
    for p in plots:
        pid = p["plot_id"]
        output.append(dict(
            plot_id=pid, block_id=p["block_id"], crop=p["crop"],
            treatment=p["treatment"], area_ha=p["area_ha"],
            yield_kg=round(yields[pid], 6),
            water_m3=round(scheduled_water[pid], 6),
            labour_h=round(labor_hours[pid], 6),
            labour_cost_usd=round(labor_costs[pid], 6),
            provenance="field_2025_user_attested_scheduled_water",
        ))

    write(args.out, list(output[0]), output)
    grouped = defaultdict(lambda: defaultdict(float))
    for r in output:
        t = grouped[(r["crop"], r["treatment"])]
        for col in ("yield_kg", "water_m3", "labour_h", "labour_cost_usd"):
            t[col] += float(r[col])
    summary = [
        {"crop": crop, "treatment": treatment, **{k: round(v, 6) for k, v in sums.items()}}
        for (crop, treatment), sums in sorted(grouped.items())
    ]
    write(args.out.parent / "field_plot_aggregate_summary.csv",
          ["crop", "treatment", "yield_kg", "water_m3", "labour_h", "labour_cost_usd"], summary)
    print("Source measurement values were not modified. Compare totals to the manuscript before interpreting.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
