#!/usr/bin/env python3
"""Reproduces every manuscript result that is calculable from public parameters alone, and labels
every number it writes.

    python3 analysis/reproduce_analytical_results.py [--out results/analytical] [--no-figures] [--no-r]
    python3 analysis/validate_analytical_outputs.py   # compares the output with the expected values

Sections (numbering follows docs/ANALYTICAL_REPRODUCIBILITY.md):
  9.1 CRT encoding                   analytical
  9.2 LoRa airtime                   analytical
  9.3 Transmission energy            analytical model + bench-measured values (printed, not reproduced)
  9.4 Reliability                    analytical
  9.5 Capacity and duty cycle        analytical capacity, not measured field capacity
  9.6 Transmission cost index        derived (illustrative processing inputs)
  9.7 Economics                      derived from public tariff assumptions (illustrative season inputs)
  9.8 Agronomic statistics           R script on illustrative plot data (needs Rscript)
  R   Raft timing window             analytical (+ bench-measured value printed, not reproduced)
  N   Reported, not reproduced       bench-measured / field-measured / measured values, transcribed,
                                     with the arithmetic that links published numbers checked

Outputs: <out>/analytical_results.json and .csv (one row per labelled quantity), per-section tables,
<out>/SUMMARY.md, and <out>/figures/*.svg when matplotlib is installed. Nothing reads an original
data directory; the script runs with none present.
"""
from __future__ import annotations

import argparse
import datetime as dt
import math
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, ".."))

import airtime_analysis  # noqa: E402
import collision_analysis  # noqa: E402
import economic_reconciliation  # noqa: E402
import energy_reconciliation  # noqa: E402
import packet_loss_recovery  # noqa: E402
import raft_recovery  # noqa: E402
import transmission_cost_index  # noqa: E402
from common import (ANALYTICAL, BENCH, FIELD, MEASURED, NOT_REPRODUCIBLE, RESULTS, analytical_parameters,  # noqa: E402
                    derived, load_yaml, path, print_record, radio_config, record, write_csv, write_json)
from gateway.mac import protocol as proto  # noqa: E402

CAPACITY_LABEL_NOTE = "analytical capacity, not measured field capacity"


# ================================================================== 9.1 CRT encoding
def crt_section(p: dict) -> list[dict]:
    c = p["crt"]
    m = c["moduli"]
    assert tuple(m) == proto.MODULI, "config and protocol moduli differ"
    we = c["worked_example"]
    scaled = proto.scale_value(proto.QUANTITIES.index(we["quantity"]), we["value_c"])
    res = proto.crt_residues(scaled)
    pairs_ok = all(proto.crt_reconstruct(res, mk) == (proto.CRT_OK, scaled) for mk in (0b011, 0b101, 0b110, 0b111))
    a, b = c["independence_example"]["a"], c["independence_example"]["b"]
    ra, rb = proto.crt_residues(a), proto.crt_residues(b)
    # Exhaustive check: every legitimate value reconstructs from every permitted residue subset.
    exhaustive = all(proto.crt_reconstruct(proto.crt_residues(v), mk) == (proto.CRT_OK, v)
                     for v in range(proto.LEGITIMATE_RANGE) for mk in (0b011, 0b101, 0b110, 0b111))
    # Single-residue corruption of a full triplet is always detected (decodes >= 9,797).
    undetected = 0
    for v in range(proto.LEGITIMATE_RANGE):
        r = list(proto.crt_residues(v))
        for i in range(3):
            bad = r.copy()
            bad[i] = (bad[i] + 1) % m[i]
            undetected += proto.crt_reconstruct(bad, 0b111)[0] == proto.CRT_OK
    bounds = {q: proto.SCALE[i][2] for i, q in enumerate(proto.QUANTITIES)}
    basis = "x mod (97, 101, 103); Garner reconstruction (gateway/mac/protocol.py)"
    out = [
        record("crt.modulus_product", "Product of the three moduli", math.prod(m), "", ANALYTICAL, "97 x 101 x 103"),
        record("crt.legitimate_range", "Pairwise reconstruction bound (smallest two-modulus product)", m[0] * m[1], "",
               ANALYTICAL, "97 x 101: any two residues determine x < 9,797"),
        record("crt.max_scaled_bound", "Largest scaled value any quantity transmits", max(bounds.values()), "", ANALYTICAL,
               "max over SCALE[q].max_scaled", "illuminance 9,796 = 48,980 lx at 5 lx; all < 9,797"),
        record("crt.worked_example_scaled", f"Worked example: {we['value_c']} degC scaled ({we['offset']} offset, x{we['factor']})",
               scaled, "", ANALYTICAL, "round((25.3 + 40) x 10)"),
        record("crt.worked_example_residues", "Worked example residues mod (97, 101, 103)", list(res), "", ANALYTICAL, basis),
        record("crt.worked_example_all_pairs_reconstruct", "Worked example reconstructs from every pair and the triplet",
               pairs_ok, "", ANALYTICAL, basis),
        record("crt.independence_shared_residues_97_101", f"{a} and {b} share residues mod 97 and 101",
               ra[0] == rb[0] and ra[1] == rb[1], "", ANALYTICAL, f"{b} = {a} + 97 x 101",
               f"residues {list(ra)} vs {list(rb)}: mod-103 residue differs, so the pair (97, 101) alone cannot tell them apart above 9,797"),
        record("crt.independence_residue_103_differs", f"{a} and {b} differ mod 103", ra[2] != rb[2], "", ANALYTICAL,
               f"{a} mod 103 = {ra[2]}, {b} mod 103 = {rb[2]}"),
        record("crt.exhaustive_pairs_reconstruct", "Every value 0..9,796 reconstructs from every pair and the triplet",
               exhaustive, "", ANALYTICAL, "exhaustive, 9,797 x 4 subsets"),
        record("crt.single_corruption_undetected", "Single-residue corruptions of a full triplet left undetected",
               undetected, "", ANALYTICAL, "exhaustive, 9,797 values x 3 residues, +1 corruption"),
        record("crt.failure_one_residue", "Reconstruction status with one residue", proto.CRT_STATUS[
            proto.crt_reconstruct(res, 0b001)[0]], "", ANALYTICAL, "fewer than two residues"),
        record("crt.failure_bad_residue", "Reconstruction status with a residue >= its modulus", proto.CRT_STATUS[
            proto.crt_reconstruct((97, 0, 0), 0b111)[0]], "", ANALYTICAL, "residue 97 on modulus 97"),
    ]
    return out


# ================================================================== 9.5 capacity and duty cycle
def stress_ceiling(window_s: float, channels: int, packets_per_reading: int, airtime_ms: float) -> int:
    """Largest number of readings in the window with M/D/1 utilisation rho < 1 over the channels.

    rho = N * k * T / (C * W) < 1  =>  N_max = ceil(C * W / (k * T)) - 1 (= floor unless exact)."""
    x = channels * window_s * 1000.0 / (packets_per_reading * airtime_ms)
    return math.ceil(x) - 1


def md1_mean_wait_ms(rho: float, service_ms: float) -> float:
    """M/D/1 mean waiting time in queue (Pollaczek-Khinchine, deterministic service)."""
    if not 0 <= rho < 1:
        return float("inf")
    return rho * service_ms / (2 * (1 - rho))


def downlink_node_ceiling(poll_bytes: int, ack_bytes: int, acks_per_node: int, window_s: float, duty: float,
                          gateways: int, retx_fraction: float = 0.0) -> dict:
    """Nodes the gateways can serve per window within the duty-cycle limit.

        t_node  = (T_air(poll) + acks_per_node x T_air(ack)) x (1 + retx_fraction)
        per_gw  = duty x window / t_node
        network = floor(gateways x per_gw)          (each gateway has its own duty-cycle budget)
    """
    t_node = (proto.lora_airtime_ms(poll_bytes) + acks_per_node * proto.lora_airtime_ms(ack_bytes)) * (1 + retx_fraction)
    per_gw = duty * window_s * 1000.0 / t_node
    return dict(t_node_ms=t_node, per_gateway=per_gw, per_gateway_floor=math.floor(per_gw),
                network=math.floor(gateways * per_gw), network_from_floored=gateways * math.floor(per_gw))


def capacity_section(p: dict, radio: dict) -> tuple[list[dict], list[dict]]:
    cap, q = radio["capacity"], p["capacity_queue"]
    t8 = proto.lora_airtime_ms(p["frame_bytes"]["residue"])
    t34 = proto.lora_airtime_ms(cap["raw_frame_bytes"])
    W, C = q["stress_window_s"], q["channels"]
    n_crt = stress_ceiling(W, C, q["packets_per_reading"]["crt"], t8)
    n_raw = stress_ceiling(W, C, q["packets_per_reading"]["raw"], t34)
    d8 = downlink_node_ceiling(cap["poll_frame_bytes_assumed"], cap["ack_frame_bytes"], cap["acks_per_node"],
                               cap["duty_cycle_window_s"], cap["duty_cycle_limit"], cap["gateways"], cap["downlink_retx_fraction"])
    d10 = downlink_node_ceiling(cap["poll_frame_bytes_implemented"], cap["ack_frame_bytes"], cap["acks_per_node"],
                                cap["duty_cycle_window_s"], cap["duty_cycle_limit"], cap["gateways"], cap["downlink_retx_fraction"])
    note = CAPACITY_LABEL_NOTE
    basis_q = f"M/D/1 stability rho < 1 over {C} channels, {W} s window"
    out = [
        record("capacity.stress_ceiling_crt_readings", f"{W}-s stress ceiling, CRT (k = 15, T = {t8:.3f} ms)", n_crt,
               "readings", ANALYTICAL, basis_q, note),
        record("capacity.stress_ceiling_raw_readings", f"{W}-s stress ceiling, raw (k = 1, T = {t34:.3f} ms)", n_raw,
               "readings", ANALYTICAL, basis_q, note),
        record("capacity.downlink_ms_per_node_8B_poll", "Gateway airtime per node and cycle, 8 B POLL + 5 x 8 B ACK",
               round(d8["t_node_ms"], 3), "ms", ANALYTICAL, "T(8) + 5 T(8)", note),
        record("capacity.downlink_nodes_per_gateway_8B_poll", "Nodes per gateway within 1 % of 1800 s, 8 B POLL (assumed)",
               round(d8["per_gateway"], 4), "nodes", ANALYTICAL, "0.01 x 1800 s / t_node", note),
        record("capacity.downlink_node_ceiling_8B_poll", f"Downlink duty-cycle node ceiling, {cap['gateways']} gateways, 8 B POLL (assumed)",
               d8["network"], "nodes", ANALYTICAL, "floor(G x 0.01 x 1800 s / t_node)", note + "; POLL size assumed (8 B)"),
        record("capacity.downlink_node_ceiling_10B_poll", f"Downlink duty-cycle node ceiling, {cap['gateways']} gateways, 10 B POLL (implemented)",
               d10["network"], "nodes", ANALYTICAL, "floor(G x 0.01 x 1800 s / t_node)",
               note + "; PollFrame as implemented in gateway/mac and firmware/esp32"),
    ]
    rows = []
    for load in q["example_loads_readings_per_window"]:
        rho = load * q["packets_per_reading"]["crt"] * t8 / (C * W * 1000)
        rows.append(dict(model="M/D/1 per channel", scheme="crt", readings_per_window=load, window_s=W, channels=C,
                         utilisation=round(rho, 5), mean_wait_ms=round(md1_mean_wait_ms(rho, t8), 3),
                         label=ANALYTICAL, note=note))
    for poll in range(8, 17):
        d = downlink_node_ceiling(poll, cap["ack_frame_bytes"], cap["acks_per_node"], cap["duty_cycle_window_s"],
                                  cap["duty_cycle_limit"], cap["gateways"], cap["downlink_retx_fraction"])
        rows.append(dict(model="downlink duty cycle", scheme=f"poll_{poll}B", readings_per_window=None, window_s=cap["duty_cycle_window_s"],
                         channels=None, utilisation=None, mean_wait_ms=None, poll_bytes=poll,
                         nodes_per_gateway=round(d["per_gateway"], 4), node_ceiling=d["network"], label=ANALYTICAL, note=note))
    return out, rows


# ================================================================== N reported, not reproduced
def reported_section(path_: str) -> list[dict]:
    if not os.path.exists(path_):
        return []
    rep = load_yaml(path_)
    out = []
    for cls, label in (("bench_measured", BENCH), ("field_measured", FIELD), ("measured", MEASURED)):
        for key, v in rep.get(cls, {}).items():
            out.append(record(f"reported.{key}", key.replace("_", " "), v.get("value"), v.get("unit", ""), label,
                              v.get("basis", "manuscript"), NOT_REPRODUCIBLE[label]))
    f, b = rep["field_measured"], rep["bench_measured"]
    out += [
        record("reported_check.rejected_share_pct", "Rejected write attempts, 15,414 / 146,311",
               round(100 * f["attempts_rejected"]["value"] / f["attempts_total"]["value"], 3), "%", derived(FIELD),
               "arithmetic on published counts", "checks the published 10.5 %; the counts themselves need restricted data"),
        record("reported_check.two_residue_share_pct", "Two-residue records, 12,833 / 130,897",
               round(100 * f["two_residue_records"]["value"] / f["committed_records"]["value"], 3), "%", derived(FIELD),
               "arithmetic on published counts", "unit ambiguous: one quantity vs all five not established"),
        record("reported_check.uptime_pct", "Gateway uptime, 1 - 9.3 h / 1,464 h",
               round(100 * (1 - f["outage_hours"]["value"] / f["deployment_hours"]["value"]), 3), "%", derived(FIELD),
               "arithmetic on published durations", "checks the published 99.365 %"),
        record("reported_check.arrival_rate_tps", "Sensing arrival rate, 146,311 attempts / 61 days",
               round(f["attempts_total"]["value"] / (f["deployment_days"]["value"] * 86400), 5), "TPS", derived(FIELD),
               "arithmetic on published counts", "published as ~0.03 TPS"),
        record("reported_check.access_control_overhead_pct", "Access-control overhead, (54.9 - 40.4) / 54.9",
               round(100 * (b["throughput_enforcement_off_tps"]["value"] - b["throughput_enforcement_on_tps"]["value"])
                     / b["throughput_enforcement_off_tps"]["value"], 3), "%", derived(BENCH),
               "arithmetic on published TPS",
               "published as 26.5 %; the rounded TPS give 26.4 %, so the paper presumably used unrounded TPS (not verifiable here)"),
    ]
    return out


# ================================================================== 9.8 agronomic statistics (R)
def run_r(out_dir: str) -> list[dict]:
    rs = shutil.which("Rscript")
    if rs is None:
        return [record("agronomy.rcbd_example", "RCBD example on illustrative plot data", None, "", derived(ANALYTICAL),
                       "analysis/agronomic_statistics.R", "skipped: Rscript not installed")]
    r = subprocess.run([rs, path("analysis", "agronomic_statistics.R"), "--out", out_dir], capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit("agronomic_statistics.R failed:\n" + r.stdout + r.stderr)
    print(r.stdout.rstrip())
    return [record("agronomy.rcbd_example", "RCBD example on illustrative plot data (see agronomic_rcbd_example.csv)",
                   True, "", derived(ANALYTICAL), "y ~ block + treatment per crop and response",
                   "illustrative plots; not the field's 40.4 / 34.6 / 33.9 % results")]


# ================================================================== figures
def figures(out_dir: str, params: dict) -> list[str]:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib not installed: figures skipped")
        return []
    matplotlib.rcParams.update({"svg.hashsalt": "public-reproducibility", "svg.fonttype": "path", "font.family": "DejaVu Sans"})
    fig_dir = os.path.join(out_dir, "figures")
    os.makedirs(fig_dir, exist_ok=True)
    ps = [i / 200 for i in range(0, 101)]
    fig, ax = plt.subplots(figsize=(6.4, 4.0))
    ax.plot([100 * p for p in ps], [100 * packet_loss_recovery.p_quantity(p) for p in ps], label="per quantity, no retry")
    ax.plot([100 * p for p in ps], [100 * packet_loss_recovery.p_five(p) for p in ps], label="per reading, no retry")
    ax.plot([100 * p for p in ps], [100 * packet_loss_recovery.p_quantity_with_retries(p, 2) ** 5 for p in ps],
            label="per reading, 2 retry rounds", linestyle="--")
    for p in params["reliability"]["headline_loss_probabilities"]:
        ax.axvline(100 * p, color="grey", linewidth=0.5)
    ax.set_xlabel("independent residue-frame loss (%)")
    ax.set_ylabel("recovery probability (%)")
    ax.set_title("CRT recovery, independent-erasure model [analytical]")
    ax.legend()
    fig.tight_layout()
    f1 = os.path.join(fig_dir, "recovery_probability.svg")
    fig.savefig(f1, metadata={"Date": None})
    plt.close(fig)

    lora = params["lora"]
    sizes = list(range(1, 41))
    fig, ax = plt.subplots(figsize=(6.4, 4.0))
    ax.step(sizes, [airtime_analysis.airtime(n, lora) for n in sizes], where="mid")
    for n, name in ((8, "residue"), (13, "fixed-point"), (34, "raw")):
        ax.annotate(f"{name} {n} B: {airtime_analysis.airtime(n, lora):.3f} ms", (n, airtime_analysis.airtime(n, lora)),
                    textcoords="offset points", xytext=(4, -12), fontsize=8)
    ax.set_xlabel("payload (bytes)")
    ax.set_ylabel("time on air (ms)")
    ax.set_title("LoRa time on air, SF9/125 kHz/CR4/5 [analytical]")
    fig.tight_layout()
    f2 = os.path.join(fig_dir, "airtime.svg")
    fig.savefig(f2, metadata={"Date": None})
    plt.close(fig)
    return [f1, f2]


# ================================================================== driver
def summary_md(records: list[dict], out_dir: str) -> str:
    lines = ["# Analytical results (public inputs only)", "",
             f"Generated {dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')} by "
             "analysis/reproduce_analytical_results.py.", "",
             "Every value carries its provenance label. `bench-measured`, `field-measured` and `measured` rows are "
             "transcribed from the manuscript and are **not** reproduced by this script; a passing run does not show "
             "that any historical experiment occurred (docs/EVIDENCE_BOUNDARIES.md).", "",
             "| id | quantity | value | unit | label | note |", "|---|---|---|---|---|---|"]
    for r in records:
        v = "not included" if r["value"] is None else r["value"]
        lines.append(f"| `{r['id']}` | {r['quantity']} | {v} | {r['unit']} | {r['label']} | {r['note']} |")
    p = os.path.join(out_dir, "SUMMARY.md")
    with open(p, "w") as f:
        f.write("\n".join(lines) + "\n")
    return p


def run(out_dir: str, make_figures: bool = True, with_r: bool = True) -> list[dict]:
    p, radio = analytical_parameters(), radio_config()
    os.makedirs(out_dir, exist_ok=True)
    sections = []

    def section(title, recs):
        print(f"\n== {title}")
        for r in recs:
            r["section"] = title
            print_record(r)
        sections.extend(recs)

    section("9.1 CRT encoding", crt_section(p))
    section("9.2 LoRa airtime", airtime_analysis.results(p))
    energy, energy_table = energy_reconciliation.results(p)
    section("9.3 Transmission energy", energy)
    section("9.4 Reliability", packet_loss_recovery.results(p))
    cap, cap_rows = capacity_section(p, radio)
    section("9.5 Capacity and duty cycle", cap)
    tci, tci_table = transmission_cost_index.results(p)
    section("9.6 Transmission cost index", tci)
    econ, econ_table = economic_reconciliation.results()
    section("9.7 Economics", econ)
    if with_r:
        section("9.8 Agronomic statistics", run_r(out_dir))
    section("R Raft timing window", raft_recovery.results(p))
    section("N Reported, not reproduced", reported_section(path("examples", "manuscript_reported_values.yaml")))

    write_csv(energy_table, os.path.join(out_dir, "energy_framings.csv"))
    write_csv(cap_rows, os.path.join(out_dir, "capacity.csv"))
    write_csv(tci_table, os.path.join(out_dir, "transmission_cost_index.csv"))
    write_csv(econ_table, os.path.join(out_dir, "economic_cases.csv"))
    write_csv(packet_loss_recovery.table(p), os.path.join(out_dir, "packet_loss_recovery.csv"))
    write_csv(collision_analysis.rows(radio), os.path.join(out_dir, "collision_analysis.csv"))
    flat = [{**r, "value": r["value"] if not isinstance(r["value"], list) else " ".join(map(str, r["value"]))} for r in sections]
    write_csv(flat, os.path.join(out_dir, "analytical_results.csv"))
    meta = {"generator": "analysis/reproduce_analytical_results.py",
            "generated_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "inputs": ["config/analytical_parameters.yaml", "config/radio.example.yaml", "config/economics.example.yaml",
                       "examples/bench_energy_values.example.yaml", "examples/manuscript_reported_values.yaml"],
            "evidence_boundary": "Reproduces formulas and software behaviour from public inputs. Does not reproduce "
                                 "bench-measured or field-measured values and does not show that a historical "
                                 "experiment occurred.",
            "results": sections}
    write_json(meta, os.path.join(out_dir, "analytical_results.json"))
    summary_md(sections, out_dir)
    if make_figures:
        for f in figures(out_dir, p):
            print("figure", os.path.relpath(f, path()))
    print(f"\n{len(sections)} labelled quantities -> {os.path.relpath(out_dir, path())}/analytical_results.json")
    return sections


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(RESULTS, "analytical"))
    ap.add_argument("--no-figures", action="store_true")
    ap.add_argument("--no-r", action="store_true", help="skip the R agronomic example")
    a = ap.parse_args()
    run(a.out, make_figures=not a.no_figures, with_r=not a.no_r)


if __name__ == "__main__":
    main()
