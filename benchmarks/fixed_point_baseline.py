#!/usr/bin/env python3
"""Compact fixed-point baseline: one 13-byte frame per five-quantity reading.

Payload: five fixed-point fields, 10 + 11 + 10 + 11 + 13 = 55 bits, packed MSB-first
into 7 bytes (one trailing pad bit). Identifier overhead 6 bytes: type/version (1),
node_id (1), seq (2), CRC-16/CCITT-FALSE (2). Total 13 bytes:
164.864 ms on air at SF9 / 125 kHz / CR 4/5 (explicit header, CRC on, 8-symbol preamble).

| field          | bits | scale                       | range                |
|----------------|------|-----------------------------|----------------------|
| soil moisture  | 10   | 0.1 %VWC                    | 0.0-100.0 (max 1000) |
| temperature    | 11   | 0.1 degC, offset +40        | -40.0-85.0 (max 1250)|
| humidity       | 10   | 0.1 %RH                     | 0.0-100.0 (max 1000) |
| pH             | 11   | 0.01                        | 0.00-14.00 (max 1400)|
| illuminance    | 13   | 6 lx                        | 0-49,146 lx (max 8191)|

Illuminance resolution is 6 lx here vs 5 lx in the CRT scheme: 13 bits cannot hold 5 lx steps
up to the CRT scale's 48,980 lx cap. Frame type 0x4 is shared with the CRT channel frame; the
baseline is a comparison codec and never runs on the same network.

    python3 benchmarks/fixed_point_baseline.py   # self-test + round trip over examples/five_sensor_example.csv

Airtime (164.864 ms) is [analytical]; the 16.36 mJ per fixed-point packet in the manuscript is
bench-measured and is not reproduced here.
"""
from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from gateway.mac.protocol import crc16_ccitt, lora_airtime_ms  # noqa: E402

FRAME_FIXED = 0x4
VERSION = 1
BITS = (10, 11, 10, 11, 13)
OFFSET = (0.0, 40.0, 0.0, 0.0, 0.0)
FACTOR = (10.0, 10.0, 10.0, 100.0, 1 / 6.0)
PAYLOAD_BITS = sum(BITS)                       # 55
PAYLOAD_BYTES = math.ceil(PAYLOAD_BITS / 8)    # 7
ID_OVERHEAD_BYTES = 6
FRAME_BYTES = PAYLOAD_BYTES + ID_OVERHEAD_BYTES  # 13


def quantise(values):
    out = []
    for v, off, fac, b in zip(values, OFFSET, FACTOR, BITS):
        q = int(math.floor((v + off) * fac + 0.5))
        if not 0 <= q < (1 << b):
            raise ValueError(f"value {v} does not fit {b} bits")
        out.append(q)
    return out


def dequantise(fields):
    return [q / fac - off for q, off, fac in zip(fields, OFFSET, FACTOR)]


def pack(fields) -> bytes:
    acc = 0
    for q, b in zip(fields, BITS):
        acc = (acc << b) | q
    acc <<= PAYLOAD_BYTES * 8 - PAYLOAD_BITS  # pad to the byte boundary
    return acc.to_bytes(PAYLOAD_BYTES, "big")


def unpack(payload: bytes):
    acc = int.from_bytes(payload, "big") >> (PAYLOAD_BYTES * 8 - PAYLOAD_BITS)
    out = []
    for b in reversed(BITS):
        out.append(acc & ((1 << b) - 1))
        acc >>= b
    return list(reversed(out))


def encode(node_id: int, seq: int, values) -> bytes:
    body = bytes([(FRAME_FIXED << 4) | VERSION, node_id]) + seq.to_bytes(2, "big") + pack(quantise(values))
    return body + crc16_ccitt(body).to_bytes(2, "big")


def decode(frame: bytes):
    if len(frame) != FRAME_BYTES or frame[0] != (FRAME_FIXED << 4) | VERSION:
        return None
    if int.from_bytes(frame[-2:], "big") != crc16_ccitt(frame[:-2]):
        return None
    return frame[1], int.from_bytes(frame[2:4], "big"), dequantise(unpack(frame[4:4 + PAYLOAD_BYTES]))


def airtime_ms() -> float:
    return lora_airtime_ms(FRAME_BYTES)


def main():
    assert PAYLOAD_BITS == 55 and PAYLOAD_BYTES == 7 and FRAME_BYTES == 13
    t = airtime_ms()
    assert abs(t - 164.864) < 1e-9, t
    f = encode(17, 470, [41.4, 25.3, 64.3, 6.82, 1500.0])
    print(f"example frame (node 17, seq 470, 41.4 %VWC, 25.3 degC, 64.3 %RH, pH 6.82, 1500 lx): {f.hex(' ')}")
    print(f"decoded: {decode(f)}")
    print(f"frame {FRAME_BYTES} B = {PAYLOAD_BYTES} B payload ({PAYLOAD_BITS} bits) + {ID_OVERHEAD_BYTES} B identifiers; "
          f"time on air {t:.3f} ms")
    import csv
    ex = os.path.join(os.path.dirname(__file__), "..", "examples", "five_sensor_example.csv")
    cols = ["soil_moisture_vwc", "temperature_c", "humidity_rh", "ph", "illuminance_lx"]
    rows = [r for r in csv.DictReader(line for line in open(ex) if not line.startswith("#"))]
    worst = [0.0] * 5
    for r in rows:
        vals = [float(r[c]) for c in cols]
        back = decode(encode(1, 1, vals))[2]
        worst = [max(w, abs(a - b)) for w, a, b in zip(worst, vals, back)]
    print(f"round trip over {len(rows)} illustrative readings ({os.path.relpath(ex)}), max abs error per field: "
          + ", ".join(f"{w:.3g}" for w in worst))

if __name__ == "__main__":
    main()
