#!/usr/bin/env python3
"""Runs the Raft failover and split-vote trials on the example Fabric 2.5 network.

Every output row is a fresh trial on the reader's host (provenance example-network-run). The
manuscript's 8.2 s mean recovery (sd 2.1 s, n = 20) is bench-measured on the deployed orderer
cluster and is not reproduced by running this script; analysis/raft_recovery.py summarises the
trials this script writes and states the analytical etcdraft election window they should fall in.

    python3 benchmarks/run_failover.py --campaign failover-<date> [--recovery 20] [--split 22]

Per trial: background load (4 closed-loop workers, 3 s timeout) -> identify the leader from
consensus_etcdraft_is_leader -> inject -> sample the metric every 100 ms until a surviving
node is leader -> let the load run on -> restore -> wait for catch-up. Election attempts and
split votes come from the surviving orderers' logs ("became candidate at term T",
"elected leader"). Output (provenance=example-network-run):
  results/benchmarks/<campaign>/recovery_trials.csv
  results/benchmarks/<campaign>/split_vote_trials.csv
  results/benchmarks/<campaign>/<trial>/  load generator output, orderer log excerpts, metric samples
"""
import argparse
import csv
import datetime as dt
import glob
import json
import os
import random
import re
import socket
import subprocess
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
NET = os.path.join(ROOT, "blockchain", "example-network")
NODES = {"osn0.orderer.bench": 9450, "osn1.orderer.bench": 9451, "osn2.orderer.bench": 9452}
RAFT_ID = {"osn0.orderer.bench": 1, "osn1.orderer.bench": 2, "osn2.orderer.bench": 3}
NETWORK = "agri-bench"
NETSHOOT = "nicolaka/netshoot:v0.13"
LOG_TS = re.compile(r"^(?:\x1b\[[0-9;]*m)?(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}) UTC")


def sh(*cmd, check=True, **kw):
    return subprocess.run(cmd, check=check, capture_output=True, text=True, **kw)


def now():
    return dt.datetime.now(dt.timezone.utc)


def iso(t):
    return t.isoformat(timespec="milliseconds") if t else ""


def is_leader(node):
    try:
        body = urllib.request.urlopen(f"http://localhost:{NODES[node]}/metrics", timeout=0.5).read().decode()
    except OSError:
        return None
    m = re.search(r'^consensus_etcdraft_is_leader\{channel="benchchannel"\} (\d+)', body, re.M)
    return int(m.group(1)) if m else None


def height(node):
    try:
        body = urllib.request.urlopen(f"http://localhost:{NODES[node]}/metrics", timeout=0.5).read().decode()
    except OSError:
        return None
    m = re.search(r'^consensus_etcdraft_committed_block_number\{channel="benchchannel"\} (\d+)', body, re.M)
    return int(m.group(1)) if m else None


def current_leader():
    leaders = [n for n in NODES if is_leader(n) == 1]
    return leaders[0] if len(leaders) == 1 else None


def wait_healthy_cluster(timeout=180):
    t0 = time.time()
    while time.time() - t0 < timeout:
        hs = [height(n) for n in NODES]
        if current_leader() and None not in hs and max(hs) - min(hs) <= 1:
            return True
        time.sleep(1)
    return False


def start_load(trial_dir, seconds):
    ident = os.path.join(NET, "fabric-ca", "bench-sns-001", "msp")
    key = glob.glob(os.path.join(ident, "keystore", "*_sk"))[0]
    tls = os.path.join(NET, "crypto/peerOrganizations/north.bench/peers/peer0.north.bench/tls/ca.crt")
    log = open(os.path.join(trial_dir, "loadgen.log"), "w")
    return subprocess.Popen([os.path.join(HERE, "loadgen", "loadgen"), "-peer", "localhost:7051", "-tls-ca", tls,
                             "-cert", os.path.join(ident, "signcerts", "cert.pem"), "-key", key, "-msp", "NorthMSP",
                             # 4 workers with a 3 s per-transaction timeout keep submitting through the failure, so the
                             # first post-failure commit measures the ordering service, not a client stuck on a
                             # transaction sent to the failed leader (a 30 s timeout dominated an earlier pilot run).
                             "-concurrency", "4", "-duration", f"{seconds}s", "-warmup", "0s", "-timeout", "3s",
                             "-out", trial_dir], stdout=log, stderr=subprocess.STDOUT)


def netem(node, ms, on):
    if on:
        sh("docker", "run", "--rm", "--net", f"container:{node}", "--cap-add", "NET_ADMIN", NETSHOOT,
           "tc", "qdisc", "add", "dev", "eth0", "root", "netem", "delay", f"{ms}ms")
    else:
        sh("docker", "run", "--rm", "--net", f"container:{node}", "--cap-add", "NET_ADMIN", NETSHOOT,
           "tc", "qdisc", "del", "dev", "eth0", "root", check=False)


def netem_supported():
    """The netem qdisc needs the sch_netem kernel module; some VM kernels (Firecracker) ship without it."""
    r = sh("docker", "run", "--rm", "--net", f"container:{next(iter(NODES))}", "--cap-add", "NET_ADMIN", NETSHOOT,
           "tc", "qdisc", "add", "dev", "eth0", "root", "netem", "delay", "1ms", check=False)
    if r.returncode == 0:
        netem(next(iter(NODES)), 0, False)
        return True, ""
    return False, (r.stderr or r.stdout).strip()


def inject(kind, leader):
    if kind == "abrupt_kill":
        cmd = ["docker", "kill", "--signal", "SIGKILL", leader]
    elif kind == "graceful_stop":
        cmd = ["docker", "stop", "--time", "10", leader]
    else:  # leader_partition: detach from the only network it shares with the followers and peers
        cmd = ["docker", "network", "disconnect", NETWORK, leader]
    t = now()
    sh(*cmd)
    return t, " ".join(cmd)


def restore(kind, leader):
    if kind == "leader_partition":
        sh("docker", "network", "connect", "--alias", leader, NETWORK, leader, check=False)
    else:
        sh("docker", "start", leader, check=False)


def orderer_log(node, since):
    out = sh("docker", "logs", "--since", since.strftime("%Y-%m-%dT%H:%M:%SZ"), node, check=False)
    return (out.stdout + out.stderr).splitlines()


def parse_ts(line):
    m = LOG_TS.match(line)
    return dt.datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S.%f").replace(tzinfo=dt.timezone.utc) if m else None


def elections(survivors, since):
    """Campaign terms and outcomes from the survivors' logs."""
    cands, elected = {}, {}
    for n in survivors:
        rid = RAFT_ID[n]
        for line in orderer_log(n, since):
            t = parse_ts(line)
            m = re.search(rf"\b{rid} became candidate at term (\d+)", line)
            if m and t and t >= since:
                cands.setdefault(int(m.group(1)), []).append((n, t))
            m = re.search(r"raft\.node: (\d) elected leader (\d) at term (\d+)", line)
            if m and t and t >= since:
                term = int(m.group(3))
                if term not in elected or t < elected[term][1]:
                    elected[term] = (next(k for k, v in RAFT_ID.items() if v == int(m.group(2))), t)
    attempts = []
    for term in sorted(set(cands) | set(elected)):
        cs = sorted(cands.get(term, []), key=lambda x: x[1])
        attempts.append(dict(term=term, candidates=[c[0] for c in cs], first=cs[0][1] if cs else elected[term][1],
                             leader=elected.get(term, (None, None))[0], at=elected.get(term, (None, None))[1]))
    return attempts


def first_commit_after(trial_dir, t_fail):
    p = os.path.join(trial_dir, "transactions.csv")
    best = None
    for r in csv.DictReader(open(p)):
        st = dt.datetime.fromisoformat(r["start_utc"].replace("Z", "+00:00"))
        if r["status"] == "VALID" and st >= t_fail:
            done = st + dt.timedelta(milliseconds=float(r["total_ms"]))
            best = done if best is None or done < best else best
    return best


def trial(campaign_dir, tid, kind, delay_ms=0):
    d = os.path.join(campaign_dir, tid)
    os.makedirs(d)
    if not wait_healthy_cluster():
        return None, "invalid_trial: cluster not healthy before injection"
    leader = current_leader()
    survivors = [n for n in NODES if n != leader]
    if delay_ms:
        for n in survivors:
            netem(n, delay_ms, True)
    load = start_load(d, 150)  # 135 s observable after the failure (procedure: not_recovered after 120 s)
    time.sleep(15)
    t_fail, cmd = inject("abrupt_kill" if kind.startswith("leader_kill") else kind, leader)
    samples, t_elect = [], None
    while (now() - t_fail).total_seconds() < 60:
        s = {n: is_leader(n) for n in survivors}
        samples.append((iso(now()), s))
        if t_elect is None and any(v == 1 for v in s.values()):
            t_elect = now()
        time.sleep(0.1)
    load.wait(timeout=260)
    if delay_ms:
        for n in survivors:
            netem(n, delay_ms, False)
    atts = elections(survivors, t_fail - dt.timedelta(seconds=1))
    restore("abrupt_kill" if kind.startswith("leader_kill") else kind, leader)
    for n in NODES:
        open(os.path.join(d, f"{n}.log"), "w").write("\n".join(orderer_log(n, t_fail - dt.timedelta(seconds=20))))
    json.dump(samples, open(os.path.join(d, "is_leader_samples.json"), "w"))
    t_first = first_commit_after(d, t_fail)
    log_elect = next((a["at"] for a in atts if a["leader"]), None)
    rec = dict(failure_command=cmd, leader_before=leader, survivors=survivors, t_fail=t_fail,
               t_elect=log_elect or t_elect, t_elect_metric=t_elect, t_first=t_first, attempts=atts)
    wait_healthy_cluster(240)
    time.sleep(30)
    return rec, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--campaign", default=f"failover-{now():%Y%m%dT%H%M%SZ}")
    ap.add_argument("--recovery", type=int, default=20)
    ap.add_argument("--split", type=int, default=22)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--resume-split", action="store_true",
                    help="skip the recovery phase and run only split-vote trials not yet in split_vote_trials.csv")
    a = ap.parse_args()
    out = os.path.join(ROOT, "results", "benchmarks", a.campaign)
    os.makedirs(out, exist_ok=True)
    sh("docker", "pull", "-q", NETSHOOT)
    rnd = random.Random(a.seed)
    rtypes = (["abrupt_kill"] * 8 + ["graceful_stop"] * 6 + ["leader_partition"] * 6)[: a.recovery]
    rnd.shuffle(rtypes)
    host = socket.gethostname()
    if not a.resume_split:
        recovery_phase(out, rtypes, host)
    split_phase(out, rnd, a)


def recovery_phase(out, rtypes, host):
    with open(os.path.join(out, "recovery_trials.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["trial_id", "failure_type", "affected_orderer", "leader_before", "leader_after", "failure_command",
                    "failure_time", "leader_election_completion_time", "first_successful_tx_time", "election_attempts",
                    "split_votes", "recovery_duration_s", "outcome", "note", "provenance"])
        for i, kind in enumerate(rtypes):
            tid = f"RT-{i + 1:02d}"
            r, err = trial(out, tid, kind)
            if err:
                w.writerow([tid, kind, "", "", "", "", "", "", "", "", "", "", "invalid_trial", err, "example-network-run"])
                continue
            splits = sum(1 for x in r["attempts"] if not x["leader"])
            leader_after = next((x["leader"] for x in r["attempts"] if x["leader"]), current_leader())
            dur = (r["t_first"] - r["t_fail"]).total_seconds() if r["t_first"] else None
            outcome = ("not_recovered" if not r["t_first"] else "recovered_after_split_vote" if splits else "recovered")
            w.writerow([tid, kind, r["leader_before"], r["leader_before"], leader_after, r["failure_command"], iso(r["t_fail"]),
                        iso(r["t_elect"]), iso(r["t_first"]), len(r["attempts"]), splits,
                        round(dur, 3) if dur is not None else "", outcome,
                        f"host {host}; metric-detected election {iso(r['t_elect_metric'])}", "example-network-run"])
            f.flush()
            print(tid, kind, outcome, dur, flush=True)


def split_phase(out, rnd, a):
    acts = (["leader_kill"] * 8 + ["leader_kill+netem_600ms_between_followers"] * 7
            + ["leader_kill+netem_1200ms_between_followers"] * 7)[: a.split]
    rnd.shuffle(acts)
    p = os.path.join(out, "split_vote_trials.csv")
    done = set()
    if a.resume_split and os.path.exists(p):
        done = {r["trial_id"] for r in csv.DictReader(open(p))}
    has_netem, netem_err = netem_supported()
    with open(p, "a" if done else "w", newline="") as f:
        w = csv.writer(f)
        if not done:
            w.writerow(["trial_id", "cluster_membership", "failed_leader", "failure_action", "injected_delay_ms", "election_attempt",
                    "raft_term", "campaign_time", "competing_candidates", "vote_outcome", "split_vote", "recovery_result",
                    "time_to_leader_s", "provenance"])
        for i, act in enumerate(acts):
            tid = f"SV-{i + 1:02d}"
            if tid in done:
                continue
            delay = 600 if "600ms" in act else 1200 if "1200ms" in act else 0
            if delay and not has_netem:
                w.writerow([tid, "", "", act, delay, "", "", "", "", "not_run", "",
                            f"not_run: tc netem unavailable on this host ({netem_err})", "", "example-network-run"])
                f.flush()
                print(tid, act, "not_run (no netem)", flush=True)
                continue
            r, err = trial(out, tid, act, delay)
            if err:
                w.writerow([tid, "", "", act, delay, "", "", "", "", "invalid_trial", "", err, "", "example-network-run"])
                continue
            for k, x in enumerate(r["attempts"]):
                w.writerow([tid, "3 consenters (osn0, osn1, osn2); 2 alive after failure", r["leader_before"], act, delay, k + 1,
                            x["term"], iso(x["first"]), ";".join(x["candidates"]), "elected" if x["leader"] else "split",
                            not x["leader"], f"leader_elected:{x['leader']}" if x["leader"] else "retry",
                            round((x["at"] - r["t_fail"]).total_seconds(), 3) if x["leader"] else "", "example-network-run"])
            f.flush()
            print(tid, act, [(x["term"], x["leader"]) for x in r["attempts"]], flush=True)


if __name__ == "__main__":
    main()
