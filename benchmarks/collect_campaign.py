#!/usr/bin/env python3
"""Packages a finished benchmark campaign with host metadata under results/collected/.

    python3 benchmarks/collect_campaign.py throughput results/benchmarks/<campaign>
    python3 benchmarks/collect_campaign.py failover   results/benchmarks/<campaign>

The package (CSV summaries, gzipped per-trial evidence, host.json) documents a run of the example
network on the reader's hardware. It never writes into an original-data directory, and host.json
states that the numbers are specific to the host that produced them.
"""
import glob
import gzip
import json
import os
import platform
import shutil
import subprocess
import sys

import pandas as pd

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def host_info():
    def run(*c):
        try:
            return subprocess.run(c, capture_output=True, text=True, timeout=20).stdout.strip()
        except Exception:
            return ""
    cpu = next((l.split(":", 1)[1].strip() for l in open("/proc/cpuinfo") if l.startswith("model name")), "")
    mem_kb = next((int(l.split()[1]) for l in open("/proc/meminfo") if l.startswith("MemTotal")), 0)
    return {"hostname": platform.node(), "cpu_model": cpu, "logical_cpus": os.cpu_count(), "memory_gib": round(mem_kb / 2**20, 1),
            "kernel": platform.release(), "os": run("sh", "-c", ". /etc/os-release && echo $PRETTY_NAME"),
            "docker": run("docker", "version", "--format", "{{.Server.Version}}"),
            "note": "Example-network run on this host (load generator on the same host). Absolute numbers are "
                    "specific to this host and configuration; they are not the manuscript's bench-measured values."}


def main(kind, src):
    name = os.path.basename(src.rstrip("/"))
    if kind == "throughput":
        dst = os.path.join(ROOT, "results", "collected", "throughput", name)
        os.makedirs(dst, exist_ok=True)
        runs = pd.read_csv(os.path.join(src, "throughput_runs.csv"))
        runs.to_csv(os.path.join(dst, "throughput_runs.csv"), index=False)
        res = []
        for f in sorted(glob.glob(os.path.join(src, "*", "c*-r*", "resources.csv"))):
            res.append(pd.read_csv(f))
        if res:
            pd.concat(res).to_csv(os.path.join(dst, "benchmark_resource_metrics.csv"), index=False)
        if os.path.exists(os.path.join(src, "rbac_overhead.csv")):
            shutil.copy(os.path.join(src, "rbac_overhead.csv"), os.path.join(dst, "rbac_overhead.csv"))
        shutil.copy(os.path.join(src, "workload.yaml"), os.path.join(dst, "workload.yaml"))
    else:
        dst = os.path.join(ROOT, "results", "collected", "failover", name)
        os.makedirs(dst, exist_ok=True)
        for f in ["recovery_trials.csv", "split_vote_trials.csv"]:
            shutil.copy(os.path.join(src, f), os.path.join(dst, f))
        ev = os.path.join(dst, "trial_evidence")
        shutil.rmtree(ev, ignore_errors=True)
        for t in sorted(glob.glob(os.path.join(src, "[RS][TV]-*"))):
            od = os.path.join(ev, os.path.basename(t))
            os.makedirs(od)
            for g in ["*.log", "is_leader_samples.json", "summary.json", "transactions.csv"]:
                for f in glob.glob(os.path.join(t, g)):
                    # gzip with mtime=0 so re-collecting the same campaign gives byte-identical files
                    with open(f, "rb") as fi, open(os.path.join(od, os.path.basename(f) + ".gz"), "wb") as raw:
                        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as fo:
                            shutil.copyfileobj(fi, fo)
    meta = {"campaign": name, "kind": kind, "provenance": "example-network-run", "evidence_status": "example-network-run",
            "source": os.path.relpath(src, ROOT), "host": host_info()}
    with open(os.path.join(dst, "host.json"), "w") as f:
        json.dump(meta, f, indent=2)
        f.write("\n")
    print(f"collected {name} -> {os.path.relpath(dst, ROOT)}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
