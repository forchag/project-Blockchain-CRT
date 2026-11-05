#!/usr/bin/env python3
"""Builds <campaign>/throughput_runs.csv from the per-trial summary.json files.

    python3 benchmarks/aggregate_trials.py results/benchmarks/<campaign>
Schema of examples/example_benchmark_runs.csv. Provenance is `example-network-run`: a fresh run on
the reader's hardware, not the manuscript's bench-measured campaign.
"""
import glob
import json
import os
import sys

import pandas as pd


def main(root):
    rows = []
    for f in sorted(glob.glob(os.path.join(root, "*", "c*-r*", "summary.json"))):
        s = json.load(open(f))
        t = json.load(open(os.path.join(os.path.dirname(f), "trial.json")))
        c = s["counts"]
        rows.append(dict(trial_id=t["trial_id"], configuration=t["condition"], concurrency_level=s["concurrency"],
                         repetition=t["repetition"], duration_seconds=s["duration_s"],
                         submitted=c.get("submitted", 0), endorsed=c.get("endorsed", 0), committed=c.get("committed", 0),
                         failed=c.get("failed", 0), throughput_tps=round(s["throughput_tps"], 3),
                         latency_mean_ms=round(s["latency_ms"]["mean"], 1), latency_p50_ms=round(s["latency_ms"]["p50"], 1),
                         latency_p95_ms=round(s["latency_ms"]["p95"], 1), latency_p99_ms=round(s["latency_ms"]["p99"], 1),
                         latency_sd_ms=round(s["latency_ms"]["sd"], 1), start_timestamp=s["started_utc"],
                         end_timestamp=s["ended_utc"], session_id=t["campaign"], host_id=t["host"], exit_code=t["exit_code"],
                         provenance="example-network-run"))
    out = os.path.join(root, "throughput_runs.csv")
    pd.DataFrame(rows).to_csv(out, index=False)
    print(f"{len(rows)} trials -> {out}")


if __name__ == "__main__":
    main(sys.argv[1])
