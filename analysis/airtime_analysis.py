#!/usr/bin/env python3
"""LoRa time on air for every frame the system sends, per frame and per complete reading.

Semtech SX127x formula (AN1200.13), the same function the firmware and gateway use
(gateway/mac/protocol.py::lora_airtime_ms, firmware/esp32/lora_protocol.cpp::lora_airtime_ms):

    T_sym     = 2^SF / BW
    n_payload = 8 + max(ceil((8 PL - 4 SF + 28 + 16 CRC - 20 IH) / (4 (SF - 2 DE))) (CR + 4), 0)
    T_air     = (n_preamble + 4.25 + n_payload) T_sym

Parameters (config/analytical_parameters.yaml): SF9, BW 125 kHz, CR 4/5, explicit header,
CRC on, LDRO off, 8-symbol preamble. Every output is [analytical].

    python3 analysis/airtime_analysis.py [--out results/analytical/airtime.csv]
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from common import ANALYTICAL, RESULTS, analytical_parameters, derived, print_record, record, write_csv  # noqa: E402
from gateway.mac.protocol import lora_airtime_ms  # noqa: E402


def airtime(nbytes: int, lora: dict) -> float:
    return lora_airtime_ms(nbytes, sf=lora["spreading_factor"], bw_hz=lora["bandwidth_hz"], cr=lora["coding_rate"],
                           crc_on=lora["crc"], implicit_header=not lora["explicit_header"],
                           low_dr_optimize=lora["low_data_rate_optimize"], preamble=lora["preamble_symbols"])


def symbol_time_ms(lora: dict) -> float:
    return (1 << lora["spreading_factor"]) / lora["bandwidth_hz"] * 1000.0


def results(params: dict | None = None) -> list[dict]:
    p = params or analytical_parameters()
    lora, fb, crt = p["lora"], p["frame_bytes"], p["crt"]
    k = crt["quantities"] * crt["residues_per_quantity"]
    t = {name: airtime(n, lora) for name, n in fb.items()}
    basis = (f"Semtech AN1200.13, SF{lora['spreading_factor']}/BW{lora['bandwidth_hz'] // 1000}/CR4/"
             f"{4 + lora['coding_rate']}, explicit header, CRC on, {lora['preamble_symbols']}-symbol preamble")
    out = [
        record("airtime.symbol_ms", "LoRa symbol time", round(symbol_time_ms(lora), 6), "ms", ANALYTICAL, basis),
        record("airtime.residue_8B_ms", "8-byte residue packet airtime", round(t["residue"], 6), "ms", ANALYTICAL, basis),
        record("airtime.raw_34B_ms", "34-byte raw packet airtime", round(t["raw"], 6), "ms", ANALYTICAL, basis,
               "manuscript rounds to 246.8 ms"),
        record("airtime.fixed_point_13B_ms", "13-byte fixed-point packet airtime", round(t["fixed_point"], 6), "ms",
               ANALYTICAL, basis),
        record("airtime.channel_12B_ms", "12-byte channel frame airtime (FRAME_MODE 3)", round(t["channel"], 6), "ms",
               ANALYTICAL, basis),
        record("airtime.poll_10B_ms", "10-byte POLL frame airtime (as implemented)", round(t["poll_implemented"], 6), "ms",
               ANALYTICAL, basis),
        record("airtime.crt_reading_ms", f"{k}-packet CRT complete reading airtime", round(k * t["residue"], 6), "ms",
               ANALYTICAL, f"{k} x 8-byte residue packet"),
        record("airtime.channel3_reading_ms", "3 x 12-byte channel frames per reading (FRAME_MODE 3)",
               round(3 * t["channel"], 6), "ms", ANALYTICAL, "3 x 12-byte channel frame"),
        record("airtime.per_packet_reduction_pct", "Per-packet airtime reduction, 8 B vs 34 B",
               round(100 * (t["raw"] - t["residue"]) / t["raw"], 3), "%", derived(ANALYTICAL),
               "(T34 - T8) / T34"),
        record("airtime.reading_ratio_crt_over_raw", "Airtime ratio per reading, CRT (15 x 8 B) / raw (1 x 34 B)",
               round(k * t["residue"] / t["raw"], 4), "x", derived(ANALYTICAL), "15 T8 / T34"),
    ]
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(RESULTS, "analytical", "airtime.csv"))
    a = ap.parse_args()
    rows = results()
    for r in rows:
        print_record(r)
    print("wrote", write_csv(rows, a.out))


if __name__ == "__main__":
    main()
