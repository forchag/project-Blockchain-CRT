#!/usr/bin/env python3
"""Transmission cost index (TCI): weighted, normalised energy / airtime / processing per READING.

    TCI_s = alpha * E_s / max_s E  +  beta * A_s / max_s A  +  gamma * P_s / max_s P     (lower is better)

with alpha = 0.5, beta = 0.3, gamma = 0.2 (manuscript weights; config/analytical_parameters.yaml).
All three components are per complete five-quantity reading:
  E_s  radio energy per reading                [mJ]  analytical (P_tx x airtime), or bench values if given
  A_s  airtime per reading                     [ms]  analytical
  P_s  processing time per reading             [ms]  = processing per packet x packets per reading

The CRT scheme's processing time is recorded PER RESIDUE PACKET. It must be multiplied by 15 before
normalisation; normalising the per-packet figure against per-reading figures of the other schemes
would understate the CRT cost fifteen-fold. tests/test_analysis.py asserts this conversion.

The processing inputs in config/analytical_parameters.yaml are ILLUSTRATIVE (the manuscript's
processing measurements are not published), so TCI values are labelled
derived (from analytical + illustrative processing inputs) in the notes; the formula and the
conversion are what this script reproduces.

    python3 analysis/transmission_cost_index.py [--energy analytical|bench]
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from airtime_analysis import airtime  # noqa: E402
from common import ANALYTICAL, BENCH, RESULTS, analytical_parameters, derived, print_record, record, write_csv  # noqa: E402
from energy_reconciliation import DEFAULT_BENCH, frame_energy_mj, load_bench  # noqa: E402

SCHEME_BYTES = {"crt": "residue", "fixed_point": "fixed_point", "raw": "raw"}
BENCH_KEYS = {"crt": "residue_8B", "fixed_point": "fixed_point_13B", "raw": "raw_34B"}


def processing_per_reading_ms(per_packet_ms: float, packets_per_reading: int) -> float:
    """Per-packet processing time -> per-reading processing time (CRT: x 15)."""
    if packets_per_reading < 1:
        raise ValueError("packets_per_reading must be >= 1")
    return per_packet_ms * packets_per_reading


def components(p: dict, energy_source: str = "analytical", bench_path: str | None = DEFAULT_BENCH) -> dict[str, dict]:
    t = p["transmission_cost_index"]
    bench = load_bench(bench_path) if energy_source == "bench" else None
    if energy_source == "bench" and bench is None:
        raise SystemExit("--energy bench needs examples/bench_energy_values.example.yaml")
    out = {}
    for s, frame in SCHEME_BYTES.items():
        k = t["packets_per_reading"][s]
        nbytes = p["frame_bytes"][frame]
        e_pkt = bench["per_packet_mj"][BENCH_KEYS[s]] if bench else frame_energy_mj(nbytes, p)
        out[s] = dict(scheme=s, packets_per_reading=k, frame_bytes=nbytes,
                      energy_per_reading_mj=k * e_pkt, airtime_per_reading_ms=k * airtime(nbytes, p["lora"]),
                      processing_per_packet_ms=t["processing_ms_per_packet"][s],
                      processing_per_reading_ms=processing_per_reading_ms(t["processing_ms_per_packet"][s], k),
                      energy_source=BENCH if bench else ANALYTICAL)
    return out


def tci(comp: dict[str, dict], alpha: float, beta: float, gamma: float, normalization: str = "max") -> dict[str, dict]:
    if abs(alpha + beta + gamma - 1) > 1e-9:
        raise ValueError("weights must sum to 1")
    if normalization != "max":
        raise ValueError("only 'max' normalisation is implemented")
    keys = ("energy_per_reading_mj", "airtime_per_reading_ms", "processing_per_reading_ms")
    mx = {k: max(c[k] for c in comp.values()) for k in keys}
    out = {}
    for s, c in comp.items():
        e, a, pr = (c[k] / mx[k] for k in keys)
        out[s] = {**c, "E_norm": round(e, 6), "A_norm": round(a, 6), "P_norm": round(pr, 6),
                  "TCI": round(alpha * e + beta * a + gamma * pr, 6)}
    return out


def results(params: dict | None = None, energy_source: str = "analytical") -> tuple[list[dict], list[dict]]:
    p = params or analytical_parameters()
    t = p["transmission_cost_index"]
    table = list(tci(components(p, energy_source), t["alpha"], t["beta"], t["gamma"], t["normalization"]).values())
    src = BENCH if energy_source == "bench" else ANALYTICAL
    label = derived(src, ANALYTICAL) if src == BENCH else derived(ANALYTICAL)
    note = f"weights {t['alpha']}/{t['beta']}/{t['gamma']}; processing inputs {t['processing_inputs_provenance']}"
    out = [record(f"tci.{r['scheme']}", f"Transmission cost index, {r['scheme']} (energy: {src})", r["TCI"], "",
                  label, "alpha E/maxE + beta A/maxA + gamma P/maxP, per reading", note) for r in table]
    crt = next(r for r in table if r["scheme"] == "crt")
    out.append(record("tci.crt_processing_per_reading_ms", "CRT processing per reading (15 x per-packet)",
                      round(crt["processing_per_reading_ms"], 6), "ms", derived(ANALYTICAL),
                      "per-packet processing x 15 packets", f"per-packet input {crt['processing_per_packet_ms']} ms is illustrative"))
    for r in table:
        r["label"] = label
    return out, table


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--energy", choices=["analytical", "bench"], default="analytical")
    ap.add_argument("--out", default=os.path.join(RESULTS, "analytical", "transmission_cost_index.csv"))
    a = ap.parse_args()
    out, table = results(energy_source=a.energy)
    for r in out:
        print_record(r)
    print("wrote", write_csv(table, a.out))


if __name__ == "__main__":
    main()
