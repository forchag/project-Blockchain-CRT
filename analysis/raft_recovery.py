#!/usr/bin/env python3
"""Raft leader-recovery: analytical etcdraft timing window and a statistics pipeline for trials.

Analytical model (Fabric etcdraft, options in blockchain/example-network/configtx.yaml):
  * each follower's election timeout is drawn uniformly from {E, ..., 2E - 1} ticks
    (etcd raft randomizedElectionTimeout), E = ElectionTick = 10, tick = 500 ms;
  * after a sudden leader shutdown, the first surviving follower whose timer expires campaigns;
    its timer started at the last heartbeat it received, a uniform 0..1 tick before the failure;
  * the new leader cuts the first block within BatchTimeout (500 ms) of the first transaction.
  So, with S surviving followers:
      T_elect  = min over S of U{E..2E-1} ticks  -  U[0, 1) tick
      T_recover <= T_elect + BatchTimeout   (plus vote round trip and commit delivery, not modelled)
  The script enumerates the distribution exactly and reports its range and mean. These numbers are
  [analytical]; they bound what a trial should show, they are not a measurement.

The manuscript's 8.2 s mean recovery (sd 2.1 s, n = 20) is bench-measured on the deployed orderer
cluster. It is printed as such and not reproduced. The source repository's trial records are not
published.

Statistics pipeline (the source repository's method, applied to whatever trials you give it):
  validate timestamps (parseable, failure < election <= first transaction), recompute durations,
  flag stored durations that disagree, identify unsuccessful trials, describe recovery and
  election durations, and give split-vote rates with Wilson 95 % intervals. Input schema: the
  recovery_trials.csv / split_vote_trials.csv that benchmarks/run_failover.py writes. Default
  input: examples/example_recovery_trials.csv (ILLUSTRATIVE, invented trials).

    python3 analysis/raft_recovery.py [--trials results/benchmarks/<campaign>/recovery_trials.csv]
                                      [--split results/benchmarks/<campaign>/split_vote_trials.csv]
"""
from __future__ import annotations

import argparse
import datetime as dt
import math
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (ANALYTICAL, BENCH, RESULTS, analytical_parameters, describe, path, print_record,  # noqa: E402
                    read_csv, record, write_csv)

DEFAULT_TRIALS = path("examples", "example_recovery_trials.csv")
BENCH_REPORTED = {"mean_s": 8.2, "sd_s": 2.1, "n": 20}  # manuscript, bench-measured


# ------------------------------------------------------------------ analytical model
def election_distribution(election_tick: int, survivors: int) -> dict[int, float]:
    """P(min over `survivors` iid U{E..2E-1} = k ticks)."""
    ks = range(election_tick, 2 * election_tick)
    n = len(ks)
    surv = {k: ((2 * election_tick - k) / n) ** survivors for k in ks}  # P(min >= k)
    return {k: surv[k] - surv.get(k + 1, 0.0) for k in ks}


def analytical_window(raft: dict, survivors: int = 2) -> dict:
    tick_s = raft["tick_interval_ms"] / 1000
    dist = election_distribution(raft["election_tick"], survivors)
    mean_ticks = sum(k * p for k, p in dist.items()) - 0.5  # minus mean elapsed time since last heartbeat
    lo = (raft["election_tick"] - 1) * tick_s               # timer nearly expired at failure
    hi = (2 * raft["election_tick"] - 1) * tick_s           # longest timeout, heartbeat just before failure
    batch_s = raft["batch_timeout_ms"] / 1000
    return dict(survivors=survivors, tick_s=tick_s, election_min_s=round(lo, 3), election_max_s=round(hi, 3),
                election_mean_s=round(mean_ticks * tick_s, 4), recovery_max_s=round(hi + batch_s, 3),
                recovery_mean_upper_s=round(mean_ticks * tick_s + batch_s, 4))


# ------------------------------------------------------------------ statistics pipeline
def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n
    c = (p + z * z / (2 * n)) / (1 + z * z / n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return (round(max(0.0, c - h), 4), round(min(1.0, c + h), 4))


def _ts(s: str | None):
    if not s:
        return None
    try:
        return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def validate(trials: list[dict]) -> tuple[list[dict], list[tuple[str, str]]]:
    issues = []
    for r in trials:
        f, e, x = _ts(r.get("failure_time")), _ts(r.get("leader_election_completion_time")), _ts(r.get("first_successful_tx_time"))
        if f is None:
            issues.append((r["trial_id"], "unparseable failure_time"))
        if f and e and e <= f:
            issues.append((r["trial_id"], "election before failure"))
        if e and x and x < e:
            issues.append((r["trial_id"], "first transaction before election"))
        r["duration_s"] = (x - f).total_seconds() if f and x else None
        r["election_s"] = (e - f).total_seconds() if f and e else None
        stored = r.get("recovery_duration_s")
        if r["duration_s"] is not None and stored not in (None, "") and abs(float(stored) - r["duration_s"]) > 0.002:
            issues.append((r["trial_id"], "stored recovery_duration_s disagrees with timestamps"))
    return trials, issues


def summarise(trials: list[dict], split: list[dict] | None = None) -> tuple[list[dict], list[str], list]:
    trials, issues = validate(trials)
    unsuccessful = [r["trial_id"] for r in trials
                    if not r.get("outcome", "").startswith("recovered") or r["election_s"] is None or r["duration_s"] is None]
    groups = defaultdict(list)
    for r in trials:
        groups[r.get("failure_type", "unknown")].append(r)
    groups["all"] = trials
    prov = sorted({r.get("provenance", "unknown") for r in trials})
    rows = []
    for g, rs in groups.items():
        ok = [r for r in rs if r["trial_id"] not in unsuccessful]
        d, e = describe(r["duration_s"] for r in ok), describe(r["election_s"] for r in ok)
        rows.append(dict(group=f"recovery:{g}", trials=len(rs), successful=len(ok),
                         **{f"recovery_s_{k}": v for k, v in d.items() if k != "n"},
                         **{f"election_s_{k}": v for k, v in e.items() if k in ("mean", "median", "sd")},
                         input_provenance="|".join(prov)))
    if split:
        per = defaultdict(lambda: {"splits": 0, "action": None, "elected": False, "attempts": 0})
        for r in split:
            t = per[r["trial_id"]]
            t["action"] = r.get("failure_action")
            t["attempts"] += 1
            t["splits"] += str(r.get("split_vote", "")).lower() == "true"
            t["elected"] |= r.get("vote_outcome") == "elected"
        by_action = defaultdict(list)
        for tid, t in per.items():
            by_action[t["action"]].append(t)
        by_action["all"] = list(per.values())
        for act, ts in by_action.items():
            k, n = sum(t["splits"] > 0 for t in ts), len(ts)
            lo, hi = wilson(k, n)
            rows.append(dict(group=f"split_vote:{act}", trials=n, successful=sum(t["elected"] for t in ts),
                             trials_with_split=k, split_rate=round(k / n, 4) if n else None, split_rate_ci95_low=lo,
                             split_rate_ci95_high=hi, election_attempts=sum(t["attempts"] for t in ts),
                             input_provenance="|".join(sorted({r.get("provenance", "unknown") for r in split}))))
    return rows, unsuccessful, issues


def results(params: dict | None = None) -> list[dict]:
    p = params or analytical_parameters()
    w = analytical_window(p["raft"], survivors=2)
    basis = (f"etcdraft, tick {p['raft']['tick_interval_ms']} ms, ElectionTick {p['raft']['election_tick']}, "
             f"2 surviving followers, BatchTimeout {p['raft']['batch_timeout_ms']} ms")
    return [
        record("raft.election_min_s", "Earliest leader election after sudden leader loss", w["election_min_s"], "s",
               ANALYTICAL, basis),
        record("raft.election_max_s", "Latest leader election after sudden leader loss", w["election_max_s"], "s",
               ANALYTICAL, basis),
        record("raft.election_mean_s", "Mean leader election time (model)", w["election_mean_s"], "s", ANALYTICAL, basis,
               "vote round trip not modelled"),
        record("raft.recovery_max_s", "Upper bound: failure to first block on the new leader", w["recovery_max_s"], "s",
               ANALYTICAL, basis, "plus vote round trip and commit delivery"),
        record("raft.bench_mean_recovery_s", "Raft leader-recovery time (manuscript)", BENCH_REPORTED["mean_s"], "s", BENCH,
               f"sudden-shutdown trials on the deployed orderer, n = {BENCH_REPORTED['n']}, sd {BENCH_REPORTED['sd_s']} s",
               "bench-measured; not reproducible from public inputs"),
    ]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", default=DEFAULT_TRIALS)
    ap.add_argument("--split", default=None, help="split_vote_trials.csv from run_failover.py")
    ap.add_argument("--out", default=os.path.join(RESULTS, "analytical", "raft_recovery_summary.csv"))
    a = ap.parse_args()
    for r in results():
        print_record(r)
    trials = read_csv(a.trials)
    split = read_csv(a.split) if a.split else None
    rows, unsuccessful, issues = summarise(trials, split)
    prov = sorted({r.get("provenance", "unknown") for r in trials})
    print(f"\ntrial statistics over {a.trials} (input provenance: {', '.join(prov)})")
    print(f"  unsuccessful: {unsuccessful or 'none'}")
    print(f"  validation issues: {issues or 'none'}")
    for r in rows:
        print("  " + ", ".join(f"{k}={v}" for k, v in r.items()))
    print("wrote", write_csv(rows, a.out))


if __name__ == "__main__":
    main()
