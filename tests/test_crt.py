"""CRT encoding: scaling, residues, reconstruction from permitted pairs, failure conditions, bounds."""
import csv
import os

import pytest

from conftest import ROOT
from gateway.mac import protocol as p

PAIRS = (0b011, 0b101, 0b110)


def test_moduli_are_pairwise_coprime_and_fit_a_byte():
    from math import gcd
    m = p.MODULI
    assert m == (97, 101, 103)
    assert all(gcd(a, b) == 1 for i, a in enumerate(m) for b in m[i + 1:])
    assert max(m) <= 255 and p.MODULUS_PRODUCT == 1_009_091
    assert p.LEGITIMATE_RANGE == 97 * 101 == 9797 == min(a * b for i, a in enumerate(m) for b in m[i + 1:])


def test_worked_example_653():
    s = p.scale_value(1, 25.3)          # temperature: (25.3 + 40) x 10
    assert s == 653
    assert p.crt_residues(s) == (71, 47, 35)
    for mask in PAIRS + (0b111,):
        assert p.crt_reconstruct((71, 47, 35), mask) == (p.CRT_OK, 653)
    assert p.unscale_value(1, 653) == pytest.approx(25.3)


def test_independence_5_and_9802_share_two_residues():
    a, b = 5, 5 + 9797
    ra, rb = p.crt_residues(a), p.crt_residues(b)
    assert ra[:2] == rb[:2] and ra[2] != rb[2]
    # The pair (97, 101) alone cannot distinguish them: reconstruction returns the value below 9,797.
    assert p.crt_reconstruct(rb, 0b011) == (p.CRT_OK, 5)
    # The full triplet of 9802 decodes outside the legitimate range: flagged, never accepted.
    assert p.crt_reconstruct(rb, 0b111)[0] == p.CRT_INCONSISTENT


def test_every_legitimate_value_every_permitted_subset():
    for v in range(p.LEGITIMATE_RANGE):
        r = p.crt_residues(v)
        for mask in PAIRS + (0b111,):
            assert p.crt_reconstruct(r, mask) == (p.CRT_OK, v)


def test_failure_conditions():
    r = p.crt_residues(653)
    for mask in (0b000, 0b001, 0b010, 0b100):
        assert p.crt_reconstruct(r, mask)[0] == p.CRT_INSUFFICIENT
    assert p.crt_reconstruct((97, 0, 0), 0b111)[0] == p.CRT_BAD_RESIDUE
    assert p.crt_reconstruct((0, 101, 0), 0b011)[0] == p.CRT_BAD_RESIDUE
    # The (97, 103) pair spans 9,991 > 9,797: residues of 9,800 decode out of the legitimate range.
    assert p.crt_residues(9800)[0::2] == (3, 15)
    assert p.crt_reconstruct((3, 0, 15), 0b101)[0] == p.CRT_OUT_OF_RANGE


def test_single_residue_corruption_always_detected():
    undetected = 0
    for v in range(p.LEGITIMATE_RANGE):
        r = list(p.crt_residues(v))
        for i in range(3):
            for delta in (1, p.MODULI[i] - 1):
                c = r.copy()
                c[i] = (c[i] + delta) % p.MODULI[i]
                undetected += p.crt_reconstruct(c, 0b111)[0] == p.CRT_OK
    assert undetected == 0


@pytest.mark.parametrize("q,lo,hi,step", [(0, 0.0, 100.0, 0.1), (1, -40.0, 85.0, 0.1), (2, 0.0, 100.0, 0.1),
                                          (3, 0.0, 14.0, 0.01), (4, 0.0, 48980.0, 5.0)])
def test_scaling_bounds(q, lo, hi, step):
    off, fac, mx = p.SCALE[q]
    assert p.scale_value(q, lo) == 0 and p.scale_value(q, hi) == mx
    assert mx < p.LEGITIMATE_RANGE           # any two residues suffice for every quantity
    assert p.scale_value(q, hi + 2 * step) is None and p.scale_value(q, lo - 2 * step) is None
    assert p.scale_value(q, float("nan")) is None
    assert 1 / fac == pytest.approx(step)


def test_crt_vector_file():
    path = os.path.join(ROOT, "examples", "crt_test_vectors.csv")
    rows = list(csv.DictReader(line for line in open(path) if not line.startswith("#")))
    assert len(rows) >= 10
    masks = {"reconstruct_all": 0b111, "reconstruct_97_101": 0b011, "reconstruct_97_103": 0b101,
             "reconstruct_101_103": 0b110}
    for r in rows:
        s = int(r["scaled"])
        if r["quantity"] != "raw_integer":
            assert p.scale_value(p.QUANTITIES.index(r["quantity"]), float(r["value"])) == s, r["case"]
        res = (int(r["r97"]), int(r["r101"]), int(r["r103"]))
        assert p.crt_residues(s) == res, r["case"]
        for col, mask in masks.items():
            st, x = p.crt_reconstruct(res, mask)
            assert (str(x) if st == p.CRT_OK else p.CRT_STATUS[st]) == r[col], (r["case"], col)
