#!/usr/bin/env python3
"""RBAC overhead from paired baseline/hrbac trials.

    python3 benchmarks/rbac_overhead.py results/benchmarks/<campaign>/throughput_runs.csv [--out ...]
    python3 benchmarks/rbac_overhead.py examples/example_benchmark_runs.csv   # illustrative input

The arithmetic is the manuscript's (26.5 % access-control overhead = throughput reduction).
The inputs decide what the output means: run on your own campaign it is an example-network
result for your host; on the example CSV it is a worked example. Neither reproduces the
bench-measured 40.4 / 54.9 TPS campaign.

Per concurrency level, over repetitions:
  latency_overhead_pct    = (mean_lat_hrbac - mean_lat_baseline) / mean_lat_baseline * 100
  throughput_reduction_pct = (tps_baseline - tps_hrbac) / tps_baseline * 100
  added_latency_ms        = mean_lat_hrbac - mean_lat_baseline
95 % CIs from Welch's t over the per-trial values (delta method for the ratios).
"""
import argparse
import math
import os
import sys

import pandas as pd

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

T975 = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365, 8: 2.306, 9: 2.262, 10: 2.228}


def t975(df):
    return T975.get(int(df), 1.96) if df >= 1 else float("nan")


def welch(a, b):
    va, vb, na, nb = a.var(ddof=1), b.var(ddof=1), len(a), len(b)
    se = math.sqrt(va / na + vb / nb)
    df = (va / na + vb / nb) ** 2 / ((va / na) ** 2 / (na - 1) + (vb / nb) ** 2 / (nb - 1)) if se else 1
    return se, df


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("runs", nargs="?", default=os.path.join(ROOT, "examples", "example_benchmark_runs.csv"))
    ap.add_argument("--out", default=os.path.join(ROOT, "results", "benchmarks", "rbac_overhead.csv"))
    a = ap.parse_args()
    d = pd.read_csv(a.runs, comment="#")
    rows = []
    for c, g in d.groupby("concurrency_level"):
        b, h = g[g.configuration == "baseline"], g[g.configuration == "hrbac"]
        lb, lh, tb, th = b.latency_mean_ms, h.latency_mean_ms, b.throughput_tps, h.throughput_tps
        se_l, df_l = welch(lh, lb)
        se_t, df_t = welch(tb, th)
        ovh = (lh.mean() - lb.mean()) / lb.mean() * 100
        se_ovh = 100 * math.sqrt((lh.var(ddof=1) / len(lh)) / lb.mean() ** 2
                                 + (lh.mean() ** 2) * (lb.var(ddof=1) / len(lb)) / lb.mean() ** 4)
        red = (tb.mean() - th.mean()) / tb.mean() * 100
        se_red = 100 * math.sqrt((th.var(ddof=1) / len(th)) / tb.mean() ** 2
                                 + (th.mean() ** 2) * (tb.var(ddof=1) / len(tb)) / tb.mean() ** 4)
        rows.append(dict(concurrency_level=c, n_baseline=len(b), n_hrbac=len(h),
                         baseline_latency_ms=round(lb.mean(), 1), hrbac_latency_ms=round(lh.mean(), 1),
                         added_latency_ms=round(lh.mean() - lb.mean(), 1),
                         added_latency_ci95_ms=round(t975(df_l) * se_l, 1),
                         latency_overhead_pct=round(ovh, 1), latency_overhead_ci95_pct=round(t975(df_l) * se_ovh, 1),
                         baseline_tps=round(tb.mean(), 2), hrbac_tps=round(th.mean(), 2),
                         throughput_reduction_pct=round(red, 1), throughput_reduction_ci95_pct=round(t975(df_t) * se_red, 1)))
    out = pd.DataFrame(rows)
    prov = sorted(set(d["provenance"])) if "provenance" in d else ["unknown"]
    out["provenance"] = prov[0] if len(prov) == 1 else "mixed:" + "|".join(prov)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    out.to_csv(a.out, index=False, lineterminator="\n")
    print(f"inputs: {a.runs} (provenance {', '.join(prov)})")
    print(out.to_string(index=False))


if __name__ == "__main__":
    main()
