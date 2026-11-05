#!/usr/bin/env python3
"""Collision analysis for the polled LoRa network (analytical).

A  Scheduled traffic (polling TDMA). Within a zone one node transmits at a time; zones use
   disjoint 450 s windows. A collision needs two slots to overlap, which requires clock error
   larger than the guard. Reports slot occupancy, guard and worst-case drift margins.
B  Event-triggered reports. Unsolicited readings sent inside the node's own zone window after
   listen-before-talk (RSSI, 5 ms). They collide when another transmission starts inside the
   LBT blind interval, or when the other sender is hidden (h = share of node pairs out of
   carrier-sense range). The per-cycle event probability is an INPUT here; the source repository
   estimated it from field soil-moisture records, which are not public.
C  External interference. 868.1/868.3/868.5 MHz are the LoRaWAN EU868 default channels. Foreign
   devices are modelled as pure ALOHA at the same spreading factor (other SFs orthogonal, no
   capture effect):
        p_c = 1 - exp(-lambda_sf9_per_channel * (T_ours + T_ext))
   p_c is combined with a link loss (p_eff = 1 - (1 - p_link)(1 - p_c)) and fed through the
   recovery model of analysis/packet_loss_recovery.py. p_link is an assumed input.

All parameters: config/radio.example.yaml (schedule, ack, retry) and the `collision` block below.
Outputs are [analytical]; B and C inherit the assumptions stated in their notes.

    python3 analysis/collision_analysis.py [--out results/analytical/collision_analysis.csv]
"""
from __future__ import annotations

import argparse
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from common import ANALYTICAL, RESULTS, radio_config, write_csv  # noqa: E402
from gateway.mac.protocol import lora_airtime_ms  # noqa: E402
from packet_loss_recovery import p_five, p_quantity_with_retries  # noqa: E402

DEFAULTS = {
    "p_link": 0.05,               # ASSUMED link frame loss (illustrative; the field estimate is restricted)
    "drift_ppm": 2.0,             # DS3231 datasheet, 0-40 degC
    "lbt_blind_ms": 6.0,          # 5 ms RSSI sample + 1 ms RX->TX switch
    "hidden_share": 0.10,         # ASSUMED share of in-zone node pairs that cannot hear each other
    "event_probability_per_cycle": 0.02,  # ASSUMED (illustrative)
    "external_devices": [0, 10, 50, 100, 250, 500, 1000],
    "external_period_s": 600.0,   # one uplink per foreign device every 10 min
    "external_payload_bytes": 20, # 13 B LoRaWAN header/MIC + 7 B application payload
    "sf_share": 1 / 6,            # share of foreign traffic at SF9 (SF7-SF12 equally)
}


def rows(mac: dict | None = None, c: dict | None = None) -> list[dict]:
    mac = mac or radio_config()
    c = {**DEFAULTS, **(c or {})}
    s, ack, retry = mac["schedule"], mac["ack"], mac["retry"]
    t8 = lora_airtime_ms(8)
    nodes_zone = s["zone_window_s"] // s["node_slot_s"]  # slot capacity of one window (13)
    out = []

    def add(part, metric, value, devices=None, note=""):
        out.append(dict(part=part, metric=metric, value=value, external_devices=devices, label=ANALYTICAL, note=note))

    # ---------------- A: TDMA margins
    resp_ms = s["turnaround_ms"] + 15 * (t8 + s["inter_frame_gap_ms"]) + 5 * (ack["ack_delay_ms"] + t8)
    worst_ms = resp_ms + 5 * retry["max_retries_per_quantity"] * (3 * (t8 + s["inter_frame_gap_ms"]) + ack["ack_window_ms"])
    drift_ms = c["drift_ppm"] * 1e-6 * s["cycle_s"] * 1000  # between resyncs (every poll)
    slack = s["node_slot_s"] * 1000 - worst_ms - s["guard_interval_ms"] - drift_ms
    add("A_scheduled", "A_slot_capacity_per_zone", nodes_zone, note="floor(zone_window_s / node_slot_s)")
    add("A_scheduled", "A_slot_s", s["node_slot_s"])
    add("A_scheduled", "A_response_no_retry_ms", round(resp_ms, 1))
    add("A_scheduled", "A_response_max_retries_ms", round(worst_ms, 1))
    add("A_scheduled", "A_guard_ms", s["guard_interval_ms"])
    add("A_scheduled", "A_drift_per_cycle_ms", round(drift_ms, 2))
    add("A_scheduled", "A_slack_ms", round(slack, 1))
    add("A_scheduled", "A_collision_probability", 0.0 if slack > 0 else None,
        note="0 while slack > 0: slots cannot overlap" if slack > 0 else "slack <= 0: slots may overlap")

    # ---------------- B: event-triggered reports
    p_event = c["event_probability_per_cycle"]
    sched_frames_per_s = nodes_zone * (15 + 1 + 5) / s["zone_window_s"]  # residues + poll + ACKs in the window
    p_blind = 1 - math.exp(-sched_frames_per_s * c["lbt_blind_ms"] / 1000)
    busy = nodes_zone * (resp_ms / 1000) / s["zone_window_s"]
    p_event_collision = c["hidden_share"] * busy + (1 - c["hidden_share"]) * p_blind
    add("B_event_reports", "B_event_probability_per_cycle", p_event, note="ASSUMED input (illustrative)")
    add("B_event_reports", "B_events_per_node_day", round(p_event * 86400 / s["cycle_s"], 3))
    add("B_event_reports", "B_zone_frame_start_rate_per_s", round(sched_frames_per_s, 4))
    add("B_event_reports", "B_zone_busy_fraction", round(busy, 4))
    add("B_event_reports", "B_collision_probability_per_event", round(p_event_collision, 5),
        note=f"hidden-node share {c['hidden_share']} assumed")

    # ---------------- C: external ALOHA interference
    t_ext = lora_airtime_ms(c["external_payload_bytes"])
    for n in c["external_devices"]:
        lam = n / c["external_period_s"] / 3 * c["sf_share"]  # same-SF frames per second per channel
        pc = 1 - math.exp(-lam * (t8 + t_ext) / 1000)
        pe = 1 - (1 - c["p_link"]) * (1 - pc)
        add("C_external_aloha", "residue_collision_probability", round(pc, 6), n)
        add("C_external_aloha", "effective_frame_loss", round(pe, 6), n, f"p_link {c['p_link']} assumed")
        add("C_external_aloha", "P_reading_no_retry", round(p_five(pe), 6), n)
        add("C_external_aloha", "P_reading_2_retries", round(p_quantity_with_retries(pe, 2) ** 5, 6), n)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(RESULTS, "analytical", "collision_analysis.csv"))
    a = ap.parse_args()
    out = rows()
    for r in out:
        dev = f" [{r['external_devices']} ext. devices]" if r["external_devices"] is not None else ""
        note = f"  ({r['note']})" if r["note"] else ""
        print(f"[analytical] {r['part']:17s} {r['metric']}{dev}: {r['value']}{note}")
    print("wrote", write_csv(out, a.out))


if __name__ == "__main__":
    main()
