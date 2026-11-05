#!/usr/bin/env python3
"""Polls cAdvisor during a trial and writes per-container resource samples.

    python3 benchmarks/collect_metrics.py --trial c040-hrbac-r1 --out results/x/resources.csv --stop-file results/x/.done
One row per container and sample; provenance column `example-network-run`.
"""
import argparse
import csv
import json
import os
import socket
import time
import urllib.request

COLUMNS = ["experiment_id", "trial_id", "timestamp", "host", "container", "cpu_pct", "memory_mb", "disk_read_kbps",
           "disk_write_kbps", "net_rx_kbps", "net_tx_kbps", "system_state", "provenance"]


def snapshot(base, names):
    data = json.load(urllib.request.urlopen(f"{base}/api/v1.3/docker/", timeout=5))
    out = {}
    for c in data.values():
        alias = next((a for a in c.get("aliases", []) if a in names), None)
        if alias and c.get("stats"):
            out[alias] = c["stats"][-1]
    return out


UNITS = {"B": 1, "kB": 1e3, "KiB": 1024, "MB": 1e6, "MiB": 2**20, "GB": 1e9, "GiB": 2**30, "TB": 1e12, "TiB": 2**40}


def _bytes(s):
    import re
    m = re.match(r"([\d.]+)\s*([A-Za-z]+)", s.strip())
    return float(m.group(1)) * UNITS.get(m.group(2), 1) if m else 0.0


def docker_snapshot(names):
    """Fallback when cAdvisor cannot see the containers (e.g. nested Docker): `docker stats` counters."""
    import datetime as dt
    import subprocess
    out = subprocess.run(["docker", "stats", "--no-stream", "--format", "{{json .}}", *names], capture_output=True, text=True)
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    snap = {}
    for line in out.stdout.splitlines():
        j = json.loads(line)
        rx, tx = (j["NetIO"].split("/") + ["0B"])[:2]
        rd, wr = (j["BlockIO"].split("/") + ["0B"])[:2]
        snap[j["Name"]] = {"timestamp": now, "cpu_pct": float(j["CPUPerc"].rstrip("%") or 0),
                           "mem": _bytes(j["MemUsage"].split("/")[0]), "rx": _bytes(rx), "tx": _bytes(tx),
                           "rd": _bytes(rd), "wr": _bytes(wr)}
    return snap


def rate(a, b, f):
    dt = (parse(b["timestamp"]) - parse(a["timestamp"])) or 1e-9
    return (f(b) - f(a)) / dt


def parse(ts):
    import datetime as dt
    return dt.datetime.fromisoformat(ts.replace("Z", "+00:00")[:26] + "+00:00").timestamp()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--experiment", default="throughput")
    ap.add_argument("--trial", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--stop-file", required=True)
    ap.add_argument("--interval", type=float, default=5.0)
    ap.add_argument("--cadvisor", default="http://localhost:8080")
    ap.add_argument("--containers", default="peer0.north.bench,peer0.south.bench,peer0.east.bench,peer0.west.bench,"
                                            "osn0.orderer.bench,osn1.orderer.bench,osn2.orderer.bench,hrbac-cc")
    ap.add_argument("--state", default="trial_running")
    a = ap.parse_args()
    names = a.containers.split(",")
    host = socket.gethostname()
    try:
        prev = snapshot(a.cadvisor, names)
    except OSError:
        prev = {}
    use_docker = not prev  # cAdvisor reachable but blind, or not reachable at all
    if use_docker:
        prev = docker_snapshot(names)
    with open(a.out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(COLUMNS)
        while not os.path.exists(a.stop_file):
            time.sleep(a.interval)
            if use_docker:
                cur = docker_snapshot(names)
                for n in names:
                    if n in cur and n in prev:
                        p, c = prev[n], cur[n]
                        dt_s = max(1e-6, parse(c["timestamp"]) - parse(p["timestamp"]))
                        w.writerow([a.experiment, a.trial, c["timestamp"], host, n, round(c["cpu_pct"], 2),
                                    round(c["mem"] / 2**20, 1), round((c["rd"] - p["rd"]) / dt_s / 1024, 1),
                                    round((c["wr"] - p["wr"]) / dt_s / 1024, 1), round((c["rx"] - p["rx"]) / dt_s / 1024, 1),
                                    round((c["tx"] - p["tx"]) / dt_s / 1024, 1), a.state, "example-network-run"])
                f.flush()
                prev = cur
                continue
            cur = snapshot(a.cadvisor, names)
            for n in names:
                if n not in cur or n not in prev:
                    continue
                p, c = prev[n], cur[n]
                cpu = rate(p, c, lambda s: s["cpu"]["usage"]["total"]) / 1e9 * 100
                io = lambda s, op: sum(d["stats"].get(op, 0) for d in s.get("diskio", {}).get("io_service_bytes", []))  # noqa: E731
                net = lambda s, k: sum(i.get(k, 0) for i in s.get("network", {}).get("interfaces", []))  # noqa: E731
                w.writerow([a.experiment, a.trial, c["timestamp"], host, n, round(cpu, 2), round(c["memory"]["usage"] / 2**20, 1),
                            round(rate(p, c, lambda s: io(s, "Read")) / 1024, 1), round(rate(p, c, lambda s: io(s, "Write")) / 1024, 1),
                            round(rate(p, c, lambda s: net(s, "rx_bytes")) / 1024, 1), round(rate(p, c, lambda s: net(s, "tx_bytes")) / 1024, 1),
                            a.state, "example-network-run"])
            f.flush()
            prev = cur


if __name__ == "__main__":
    main()
