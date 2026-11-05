#!/usr/bin/env python3
"""Transmission energy: what is analytical, what is bench-measured, and where the boundaries are.

Analytical (reproducible from public inputs):
  E_frame(b)     = V x I_tx x T_air(b)                                  radio rail only   [mJ]
  E_reading      = frames_per_reading x E_frame(bytes)                                     [mJ]
  E_node_cycle   = V_bat x (I_sleep x t_sleep + I_awake x t_awake + I_rx x t_rx) + E_reading_tx
                   complete-node model per 1800 s cycle, ASSUMED parameters                 [mJ]
  V, I_tx and the node-model currents come from config/analytical_parameters.yaml and are stated
  as assumptions. Analytical ratios between frame types equal the airtime ratios, because the model
  holds TX power constant.

Bench-measured (NOT reproducible from public inputs):
  12.32 / 24.86 / 16.36 mJ per residue / raw / fixed-point packet, and 184.8 mJ per complete CRT
  reading, are INA219 bench figures. They are read from examples/bench_energy_values.example.yaml,
  printed with the [bench-measured, not reproducible] tag, and used only for derived ratios that are
  themselves labelled derived (from bench-measured). If the file is absent, the script says that the
  historical bench value is not included.

Radio-only energy and complete-node energy are reported separately and never mixed: the bench
figures are radio-rail energy per frame; the node model adds MCU, sensing, receive windows and
sleep. Whether the manuscript's bench boundary and its node-profile boundary share a measurement
protocol is not recorded.

    python3 analysis/energy_reconciliation.py [--bench examples/bench_energy_values.example.yaml]
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from airtime_analysis import airtime  # noqa: E402
from common import (ANALYTICAL, BENCH, RESULTS, analytical_parameters, derived, load_yaml, path,  # noqa: E402
                    print_record, record, write_csv)

DEFAULT_BENCH = path("examples", "bench_energy_values.example.yaml")
# name: (frames per reading, bytes per frame, description)
FRAMINGS = {
    "crt_residue_15": (15, 8, "FRAME_MODE 15: one 8 B residue frame per quantity per channel"),
    "crt_channel_3": (3, 12, "FRAME_MODE 3: one 12 B channel frame per channel"),
    "fixed_point_13B": (1, 13, "compact fixed-point baseline, one 13 B frame"),
    "raw_34B": (1, 34, "uncompressed reference, one 34 B frame"),
}


def tx_power_mw(p: dict) -> float:
    e = p["energy_model"]
    return e["supply_v"] * e["tx_current_ma"]


def frame_energy_mj(nbytes: int, p: dict) -> float:
    return tx_power_mw(p) * airtime(nbytes, p["lora"]) / 1000.0


def node_cycle_energy_mj(p: dict, reading_tx_mj: float) -> dict:
    n = p["node_energy_model"]
    t_awake, t_rx = n["awake_s"], n["rx_s_per_reading"]
    t_sleep = n["cycle_s"] - t_awake
    e_sleep = n["supply_v"] * n["sleep_current_ua"] / 1000.0 * t_sleep
    e_awake = n["supply_v"] * n["awake_current_ma"] * t_awake
    e_rx = n["supply_v"] * n["rx_current_ma"] * t_rx
    total = e_sleep + e_awake + e_rx + reading_tx_mj
    return dict(sleep_mj=e_sleep, awake_mj=e_awake, rx_mj=e_rx, tx_mj=reading_tx_mj, total_mj=total,
                mean_power_mw=total / n["cycle_s"])


def load_bench(path_: str | None) -> dict | None:
    if not path_ or not os.path.exists(path_):
        return None
    b = load_yaml(path_)
    if b.get("label") != BENCH:
        raise ValueError(f"{path_}: label must be '{BENCH}'")
    return b


def results(params: dict | None = None, bench_path: str | None = DEFAULT_BENCH) -> tuple[list[dict], list[dict]]:
    p = params or analytical_parameters()
    e = p["energy_model"]
    assumed = f"assumed V = {e['supply_v']} V, I_tx = {e['tx_current_ma']} mA (P_tx = {tx_power_mw(p):.2f} mW)"
    out = [record("energy.tx_power_assumed_mw", "Radio TX power assumed by the analytical model", round(tx_power_mw(p), 4),
                  "mW", ANALYTICAL, "V x I_tx", "assumption, see config/analytical_parameters.yaml energy_model")]
    table = []
    for name, (k, nbytes, desc) in FRAMINGS.items():
        ef = frame_energy_mj(nbytes, p)
        table.append(dict(framing=name, description=desc, frames_per_reading=k, frame_bytes=nbytes,
                          frame_airtime_ms=round(airtime(nbytes, p["lora"]), 6), radio_energy_per_frame_mj=round(ef, 4),
                          radio_energy_per_reading_mj=round(k * ef, 4), boundary="radio-only", label=ANALYTICAL))
    t = {r["framing"]: r for r in table}
    out += [
        record("energy.analytical_residue_8B_mj", "Analytical radio energy per 8 B residue packet",
               t["crt_residue_15"]["radio_energy_per_frame_mj"], "mJ", ANALYTICAL, "P_tx x T_air(8 B)", assumed),
        record("energy.analytical_raw_34B_mj", "Analytical radio energy per 34 B raw packet",
               t["raw_34B"]["radio_energy_per_frame_mj"], "mJ", ANALYTICAL, "P_tx x T_air(34 B)", assumed),
        record("energy.analytical_fixed_13B_mj", "Analytical radio energy per 13 B fixed-point packet",
               t["fixed_point_13B"]["radio_energy_per_frame_mj"], "mJ", ANALYTICAL, "P_tx x T_air(13 B)", assumed),
        record("energy.analytical_crt_reading_mj", "Analytical radio energy per 15-packet CRT reading",
               t["crt_residue_15"]["radio_energy_per_reading_mj"], "mJ", ANALYTICAL, "15 x P_tx x T_air(8 B)", assumed),
        record("energy.analytical_ratio_crt_over_raw", "Analytical radio energy ratio per reading, CRT / raw",
               round(t["crt_residue_15"]["radio_energy_per_reading_mj"] / t["raw_34B"]["radio_energy_per_reading_mj"], 4),
               "x", derived(ANALYTICAL), "constant P_tx: equals the airtime ratio"),
    ]
    node = node_cycle_energy_mj(p, t["crt_residue_15"]["radio_energy_per_reading_mj"])
    out += [
        record("energy.node_cycle_total_mj", "Complete-node energy per 1800 s cycle (assumed node model)",
               round(node["total_mj"], 3), "mJ", ANALYTICAL, "sleep + awake + RX + CRT reading TX",
               "ASSUMED node currents; not a node-profile measurement"),
        record("energy.node_mean_power_mw", "Complete-node mean power (assumed node model)", round(node["mean_power_mw"], 4),
               "mW", ANALYTICAL, "E_node_cycle / 1800 s", "ASSUMED node currents; not a node-profile measurement"),
        record("energy.node_tx_share_pct", "Radio TX share of complete-node energy (assumed node model)",
               round(100 * node["tx_mj"] / node["total_mj"], 3), "%", derived(ANALYTICAL),
               "radio-only vs complete-node boundary", "ASSUMED node currents"),
    ]

    bench = load_bench(bench_path)
    if bench is None:
        for rid, q in [("energy.bench_residue_8B_mj", "Bench radio energy per residue packet"),
                       ("energy.bench_raw_34B_mj", "Bench radio energy per raw packet"),
                       ("energy.bench_fixed_13B_mj", "Bench radio energy per fixed-point packet"),
                       ("energy.bench_crt_reading_mj", "Bench radio energy per CRT complete reading")]:
            out.append(record(rid, q, None, "mJ", BENCH, "INA219 bench", "historical bench value not included"))
        return out, table
    pp, pr = bench["per_packet_mj"], bench["per_reading_mj"]
    note = "bench-measured; not reproducible from public inputs"
    out += [
        record("energy.bench_residue_8B_mj", "Bench radio energy per residue packet", pp["residue_8B"], "mJ", BENCH,
               bench["measurement"], note),
        record("energy.bench_raw_34B_mj", "Bench radio energy per raw packet", pp["raw_34B"], "mJ", BENCH,
               bench["measurement"], note),
        record("energy.bench_fixed_13B_mj", "Bench radio energy per fixed-point packet", pp["fixed_point_13B"], "mJ", BENCH,
               bench["measurement"], note),
        record("energy.bench_crt_reading_mj", "Bench radio energy per CRT complete reading", pr["crt_15_packets"], "mJ",
               BENCH, "15 x 12.32 mJ (bench)", note),
        record("energy.bench_ratio_crt_over_raw", "Energy ratio per reading, CRT / raw (bench)",
               round(pr["crt_15_packets"] / pp["raw_34B"], 4), "x", derived(BENCH), "184.8 / 24.86",
               "ratio of bench-measured values"),
        record("energy.bench_check_15x_residue", "15 x bench residue packet energy", round(15 * pp["residue_8B"], 4), "mJ",
               derived(BENCH), "consistency check of the per-reading figure", "arithmetic on a bench-measured value"),
        record("energy.bench_implied_tx_power_mw", "TX power implied by the bench residue figure",
               round(pp["residue_8B"] / airtime(8, p["lora"]) * 1000, 3), "mW", derived(BENCH, ANALYTICAL),
               "12.32 mJ / 123.904 ms", "for comparison with the assumed P_tx; not a measurement"),
    ]
    return out, table


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bench", default=DEFAULT_BENCH, help="bench-measured values file ('' to omit)")
    ap.add_argument("--out", default=os.path.join(RESULTS, "analytical", "energy_framings.csv"))
    a = ap.parse_args()
    out, table = results(bench_path=a.bench or None)
    for r in out:
        print_record(r)
    print("wrote", write_csv(table, a.out))


if __name__ == "__main__":
    main()
