#!/usr/bin/env python3
"""Analytical recovery probability of the three-residue CRT scheme.

A quantity is recoverable when at least two of its three residues arrive:

    P_quantity = (1 - p)^3 + 3 p (1 - p)^2
    P_reading  = P_quantity^5            (all five quantities of a reading)

Assumption: each 8-byte residue frame is lost independently with the same probability p
(no burst losses, no correlation across the three channels or across quantities).
With r retransmission rounds (the gateway asks for the missing residues), a quantity fails only
if it is still short after every round; for r = 0 the formulas above apply unchanged.

Structured single-channel loss: if one whole channel is lost (all frames on it), every quantity
still holds two residues, and any two residues reconstruct x exactly while x < 97 x 101 = 9,797
(the smallest pairwise modulus product). Recovery is therefore 1 for every scaled value the
protocol transmits (all max_scaled values are below 9,797; see gateway/mac/protocol.py SCALE).

The source repository estimated p from the field record; that estimate needs restricted data and
is not computed here. Loss probabilities come from config/analytical_parameters.yaml.

    python3 analysis/packet_loss_recovery.py [--out results/analytical/packet_loss_recovery.csv]
"""
from __future__ import annotations

import argparse
import os
import sys
from math import comb

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from common import ANALYTICAL, RESULTS, analytical_parameters, print_record, record, write_csv  # noqa: E402
from gateway.mac import protocol as proto  # noqa: E402


def p_quantity(p: float) -> float:
    return (1 - p) ** 3 + 3 * p * (1 - p) ** 2


def p_five(p: float) -> float:
    return p_quantity(p) ** 5


def p_quantity_with_retries(p: float, rounds: int) -> float:
    """Exact under independence: state = residues held (0..3); each round resends the missing ones."""
    dist = {k: comb(3, k) * (1 - p) ** k * p ** (3 - k) for k in range(4)}  # first attempt: binomial(3, 1-p)
    for _ in range(rounds):
        new = {k: 0.0 for k in range(4)}
        for held, pr in dist.items():
            if held >= 2:
                new[held] += pr
                continue
            missing = 3 - held
            for got in range(missing + 1):
                new[held + got] += pr * comb(missing, got) * (1 - p) ** got * p ** (missing - got)
        dist = new
    return dist[2] + dist[3]


def structured_channel_loss_recovery(lost_channel: int) -> float:
    """Share of all legitimate scaled values (0..9796) reconstructed when one channel is lost entirely."""
    mask = 0b111 & ~(1 << lost_channel)
    ok = 0
    for v in range(proto.LEGITIMATE_RANGE):
        st, x = proto.crt_reconstruct(proto.crt_residues(v), mask)
        ok += st == proto.CRT_OK and x == v
    return ok / proto.LEGITIMATE_RANGE


def table(params: dict | None = None) -> list[dict]:
    p = params or analytical_parameters()
    rows = []
    for loss in p["reliability"]["loss_probabilities"]:
        rows.append(dict(loss_probability=loss, p_quantity=round(p_quantity(loss), 6), p_reading=round(p_five(loss), 6),
                         p_quantity_1_retry=round(p_quantity_with_retries(loss, 1), 6),
                         p_reading_1_retry=round(p_quantity_with_retries(loss, 1) ** 5, 6),
                         p_quantity_2_retries=round(p_quantity_with_retries(loss, 2), 6),
                         p_reading_2_retries=round(p_quantity_with_retries(loss, 2) ** 5, 6),
                         label=ANALYTICAL, model="independent erasures, >= 2 of 3 residues"))
    return rows


def results(params: dict | None = None) -> list[dict]:
    p = params or analytical_parameters()
    out = []
    basis_q = "P_q = (1-p)^3 + 3p(1-p)^2, independent residue loss, no retransmission"
    for loss in p["reliability"]["headline_loss_probabilities"]:
        pct = int(round(loss * 100))
        out.append(record(f"recovery.per_quantity_{pct}pct", f"Per-quantity recovery at {pct} % independent loss",
                          round(100 * p_quantity(loss), 3), "%", ANALYTICAL, basis_q))
    for loss in p["reliability"]["headline_loss_probabilities"]:
        pct = int(round(loss * 100))
        out.append(record(f"recovery.per_reading_{pct}pct", f"Per-reading recovery at {pct} % independent loss",
                          round(100 * p_five(loss), 3), "%", ANALYTICAL, "P_reading = P_q^5 (five quantities, independent)"))
    worst = min(structured_channel_loss_recovery(c) for c in range(3))
    out.append(record("recovery.structured_single_channel_pct", "Recovery under loss of one entire channel",
                      round(100 * worst, 3), "%", ANALYTICAL,
                      "exhaustive over all scaled values 0..9796 and each lost channel; any two residues suffice for x < 9,797"))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(RESULTS, "analytical", "packet_loss_recovery.csv"))
    a = ap.parse_args()
    for r in results():
        print_record(r)
    rows = table()
    print("\n[analytical] recovery table (independent erasures)")
    for r in rows:
        print("  p={loss_probability:<5} P_q={p_quantity:.4f} P_reading={p_reading:.4f} "
              "| 1 retry {p_reading_1_retry:.4f} | 2 retries {p_reading_2_retries:.4f}".format(**r))
    print("wrote", write_csv(rows, a.out))


if __name__ == "__main__":
    main()
