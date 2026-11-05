#!/usr/bin/env python3
"""Measures Ed25519 sign/verify and SHA-256 on the host running it (gateway/peer-class CPU).

    python3 benchmarks/crypto_microbench.py [--samples 300] [--batch 100]

Each sample is the mean of `batch` back-to-back operations timed with perf_counter_ns,
which spreads the Python call overhead (~0.1 us) over the batch. Message: a 120-byte
reading payload (the size of a WriteSensor reading JSON). Library: cryptography (OpenSSL).
Output: results/crypto/cryptographic_timings.csv and host.json (provenance this-host-measurement).
The gateway signs every completed reading with Ed25519 (gateway/gateway_service.py); this measures
that operation on the machine you run it on. These are NOT ESP32 timings and NOT the manuscript's
bench-measured figures.
"""
import argparse
import hashlib
import json
import os
import platform
import sys
import time

import cryptography
from cryptography.hazmat.backends.openssl.backend import backend
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "benchmarks"))
from collect_campaign import host_info  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--samples", type=int, default=300)
    ap.add_argument("--batch", type=int, default=100)
    a = ap.parse_args()
    # illustrative reading payload (invented values, 2030 timestamp), same size class as a WriteSensor reading JSON
    msg = b'{"captured_at":"2030-01-15T00:00:00Z","soil_moisture":41.40,"temp_c":25.30,"humidity_pct":64.3,"ph":6.82,"lux":1}'
    key = Ed25519PrivateKey.generate()
    pub = key.public_key()
    sig = key.sign(msg)
    ops = {"sha256": lambda: hashlib.sha256(msg).digest(), "ed25519_sign": lambda: key.sign(msg),
           "ed25519_verify": lambda: pub.verify(sig, msg)}
    for f in ops.values():  # warm-up
        for _ in range(1000):
            f()
    rows, n = [], 0
    for op, f in ops.items():
        for _ in range(a.samples):
            t0 = time.perf_counter_ns()
            for _ in range(a.batch):
                f()
            us = (time.perf_counter_ns() - t0) / a.batch / 1000
            n += 1
            rows.append((f"CRYPTO-H{n:05d}", op, round(us, 3), "this-host-measurement"))
    out = os.path.join(ROOT, "results", "crypto")
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "cryptographic_timings.csv"), "w") as fh:
        fh.write("sample_id,operation,duration_us,provenance\n")
        for r in rows:
            fh.write(",".join(map(str, r)) + "\n")
    meta = {"kind": "crypto_microbenchmark", "provenance": "this-host-measurement", "evidence_status": "this-host-measurement",
            "samples_per_operation": a.samples, "operations_per_sample": a.batch, "message_bytes": len(msg),
            "library": f"cryptography {cryptography.__version__} ({backend.openssl_version_text()})",
            "python": platform.python_version(), "host": host_info()}
    with open(os.path.join(out, "host.json"), "w") as fh:
        json.dump(meta, fh, indent=2)
        fh.write("\n")
    import statistics as st
    for op in ops:
        v = [r[2] for r in rows if r[1] == op]
        print(f"{op:15s} mean {st.mean(v):8.3f} us  median {st.median(v):8.3f}  sd {st.stdev(v):6.3f}")


if __name__ == "__main__":
    main()
