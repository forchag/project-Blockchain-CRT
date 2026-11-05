#!/usr/bin/env python3
"""Reconcile 2025 scheduled and meter-recorded plot water with reported manuscript totals.

This script NEVER edits deposited observations. Discrepancies are evidence requiring
source-range, unit and methodology reconciliation, not values to overwrite.
"""
from __future__ import annotations
import argparse
import csv
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Values transcribed from the reviewed manuscript; NOT substituted for recorded data.
PAPER_TOTALS = {
    ("tomato", "blockchain_iot"): 5574.5,
    ("tomato", "traditional"): 9346.2,
    ("pepper", "blockchain_iot"): 3258.9,
    ("pepper", "traditional"): 4984.6,
}


def read(path):
    with path.open(newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", type=Path, required=True, help="External agronomic CSV directory (not included)")
    ap.add_argument("--out", type=Path, default=ROOT / "results/manuscript/water_reconciliation.csv")
    ap.add_argument("--tolerance-m3", type=float, default=0.5)
    ap.add_argument("--strict", action="store_true", help="fail when manuscript/source totals disagree")
    opt = ap.parse_args()

    assignments = {r["plot_id"]: (r["crop"], r["treatment"]) for r in read(opt.data_dir / "plot_assignment.csv")}
    scheduled = defaultdict(float)
    for r in read(opt.data_dir / "per_plot_water_application.csv"):
        scheduled[assignments[r["plot_id"]]] += float(r["water_volume_l"]) / 1000
    meter = defaultdict(float)
    meter_events = defaultdict(int)
    for r in read(opt.data_dir / "flow_meter_readings.csv"):
        if r["reading_type"] == "event_end" and r["interval_volume_l"]:
            group = assignments[r["plot_id"]]
            meter[group] += float(r["interval_volume_l"]) / 1000
            meter_events[group] += 1

    rows = []
    for (crop, treatment), target in PAPER_TOTALS.items():
        actual, measured = scheduled[(crop, treatment)], meter[(crop, treatment)]
        rows.append(dict(
            crop=crop, treatment=treatment, scheduled_m3=round(actual, 3),
            flow_meter_m3=round(measured, 3), flow_meter_events=meter_events[(crop, treatment)],
            manuscript_m3=target, scheduled_minus_manuscript_m3=round(actual - target, 3),
            meter_minus_scheduled_m3=round(measured - actual, 3),
            manuscript_match=abs(actual - target) <= opt.tolerance_m3,
            explanation="Different ranges, scale, definitions or manuscript source need investigation",
        ))
    opt.out.parent.mkdir(parents=True, exist_ok=True)
    with opt.out.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    for crop in ("tomato", "pepper"):
        treated, control = scheduled[(crop, "blockchain_iot")], scheduled[(crop, "traditional")]
        manuscript_treated = PAPER_TOTALS[(crop, "blockchain_iot")]
        manuscript_control = PAPER_TOTALS[(crop, "traditional")]
        print(f"{crop}: source scheduled reduction {100*(1-treated/control):.2f}%, "
              f"manuscript {100*(1-manuscript_treated/manuscript_control):.2f}%")
    disagreements = sum(not r["manuscript_match"] for r in rows)
    print(f"{disagreements}/{len(rows)} grouped source totals differ from manuscript; report: {opt.out}")
    print("No source values changed. Do not treat agreement or disagreement as proof of capture authenticity.")
    return 1 if disagreements and opt.strict else 0


if __name__ == "__main__":
    raise SystemExit(main())
