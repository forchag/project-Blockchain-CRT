"""Frame codecs, CRT and airtime for the gateway. Byte-for-byte the same as
firmware/esp32/lora_protocol.cpp; tests/test_protocol_vectors.py checks both
against fixed protocol vectors."""
from __future__ import annotations

import math
from dataclasses import dataclass

PROTOCOL_VERSION = 1
FRAME_RESIDUE, FRAME_POLL, FRAME_ACK, FRAME_CHANNEL = 0x1, 0x2, 0x3, 0x4
RESIDUE_LEN, POLL_LEN, ACK_LEN, CHANNEL_LEN = 8, 10, 8, 12
NUM_QUANTITIES, NUM_RESIDUES = 5, 3
# Frames per reading in each framing: 15 = one residue frame per quantity per channel,
# 3 = one channel frame per channel carrying all five residues.
FRAME_MODES = (15, 3)
PACKETS_PER_READING = NUM_QUANTITIES * NUM_RESIDUES  # FRAME_MODE 15
RESIDUE_INVALID = 0xFF   # channel frame: quantity has no valid reading
ACK_WHOLE_READING = 7    # ACK quantity code in FRAME_MODE 3
MODULI = (97, 101, 103)
MODULUS_PRODUCT = 97 * 101 * 103
LEGITIMATE_RANGE = 97 * 101
CHANNEL_HZ = (868_100_000, 868_300_000, 868_500_000)

QUANTITIES = ("soil_moisture", "temperature", "humidity", "ph", "illuminance")
UNITS = ("%VWC", "degC", "%RH", "pH", "lx")
# (offset, factor, max_scaled): scaled = round((value + offset) * factor)
SCALE = ((0.0, 10.0, 1000), (40.0, 10.0, 1250), (0.0, 10.0, 1000), (0.0, 100.0, 1400), (0.0, 0.2, 9796))

CRT_OK, CRT_INSUFFICIENT, CRT_INCONSISTENT, CRT_BAD_RESIDUE, CRT_OUT_OF_RANGE = range(5)
CRT_STATUS = ("OK", "INSUFFICIENT", "INCONSISTENT", "BAD_RESIDUE", "OUT_OF_RANGE")


def _round_half_away(x: float) -> int:  # lroundf semantics
    return int(math.floor(x + 0.5)) if x >= 0 else -int(math.floor(-x + 0.5))


def scale_value(q: int, value: float) -> int | None:
    off, fac, mx = SCALE[q]
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    # float32 arithmetic like the firmware, so rounding at .5 boundaries matches
    import struct
    f32 = lambda v: struct.unpack("f", struct.pack("f", v))[0]  # noqa: E731
    v = _round_half_away(f32(f32(f32(value) + f32(off)) * f32(fac)))
    return v if 0 <= v <= mx else None


def unscale_value(q: int, scaled: int) -> float:
    off, fac, _ = SCALE[q]
    return scaled / fac - off


def crt_residues(scaled: int) -> tuple[int, int, int]:
    return tuple(scaled % m for m in MODULI)


def crt_reconstruct(residues, mask: int) -> tuple[int, int | None]:
    """Returns (status, scaled). mask bit i = residue i present."""
    idx = [i for i in range(NUM_RESIDUES) if mask & (1 << i)]
    for i in idx:
        if not 0 <= residues[i] < MODULI[i]:
            return CRT_BAD_RESIDUE, None
    if len(idx) < 2:
        return CRT_INSUFFICIENT, None
    x, m = residues[idx[0]], MODULI[idx[0]]
    for i in idx[1:]:
        mk = MODULI[i]
        t = ((residues[i] - x) % mk) * pow(m % mk, -1, mk) % mk
        x, m = x + m * t, m * mk
    if x >= LEGITIMATE_RANGE:
        return (CRT_INCONSISTENT if len(idx) == 3 else CRT_OUT_OF_RANGE), None
    return CRT_OK, x


def crc16_ccitt(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def _with_crc(body: bytes) -> bytes:
    return body + crc16_ccitt(body).to_bytes(2, "big")


def _check(buf: bytes, want: int, ftype: int) -> bool:
    return (len(buf) == want and buf[0] >> 4 == ftype and buf[0] & 0x0F == PROTOCOL_VERSION
            and int.from_bytes(buf[-2:], "big") == crc16_ccitt(buf[:-2]))


@dataclass(frozen=True)
class ResidueFrame:
    node_id: int
    seq: int
    quantity: int
    residue_index: int
    valid: bool
    event: bool
    retx: bool
    residue: int

    def encode(self) -> bytes:
        flags = (self.quantity << 5) | (self.residue_index << 3) | (4 if self.valid else 0) | (2 if self.event else 0) | (
            1 if self.retx else 0)
        return _with_crc(bytes([(FRAME_RESIDUE << 4) | PROTOCOL_VERSION, self.node_id]) + self.seq.to_bytes(2, "big")
                         + bytes([flags, self.residue]))

    @staticmethod
    def decode(buf: bytes) -> "ResidueFrame | None":
        if not _check(buf, RESIDUE_LEN, FRAME_RESIDUE):
            return None
        f = ResidueFrame(buf[1], int.from_bytes(buf[2:4], "big"), buf[4] >> 5, (buf[4] >> 3) & 3, bool(buf[4] & 4),
                         bool(buf[4] & 2), bool(buf[4] & 1), buf[5])
        if f.node_id in (0, 0xFF) or f.quantity >= NUM_QUANTITIES or f.residue_index >= NUM_RESIDUES:
            return None
        if f.residue >= MODULI[f.residue_index]:
            return None
        return f


@dataclass(frozen=True)
class PollFrame:
    node_id: int
    poll_id: int
    gateway_time: int

    def encode(self) -> bytes:
        return _with_crc(bytes([(FRAME_POLL << 4) | PROTOCOL_VERSION, self.node_id]) + self.poll_id.to_bytes(2, "big")
                         + self.gateway_time.to_bytes(4, "big"))

    @staticmethod
    def decode(buf: bytes) -> "PollFrame | None":
        if not _check(buf, POLL_LEN, FRAME_POLL) or buf[1] == 0:
            return None
        return PollFrame(buf[1], int.from_bytes(buf[2:4], "big"), int.from_bytes(buf[4:8], "big"))


@dataclass(frozen=True)
class AckFrame:
    node_id: int
    seq: int
    quantity: int
    reconstructed: bool
    retx_request: bool
    received_mask: int
    crt_status: int

    def encode(self) -> bytes:
        b4 = (self.quantity << 5) | (0x10 if self.reconstructed else 0) | (0x08 if self.retx_request else 0) | (
            self.received_mask & 7)
        return _with_crc(bytes([(FRAME_ACK << 4) | PROTOCOL_VERSION, self.node_id]) + self.seq.to_bytes(2, "big")
                         + bytes([b4, self.crt_status]))

    @staticmethod
    def decode(buf: bytes) -> "AckFrame | None":
        if not _check(buf, ACK_LEN, FRAME_ACK) or buf[1] == 0 or (
                buf[4] >> 5 >= NUM_QUANTITIES and buf[4] >> 5 != ACK_WHOLE_READING):
            return None
        return AckFrame(buf[1], int.from_bytes(buf[2:4], "big"), buf[4] >> 5, bool(buf[4] & 0x10), bool(buf[4] & 0x08),
                        buf[4] & 7, buf[5])


@dataclass(frozen=True)
class ChannelFrame:
    """FRAME_MODE 3: the five residues of one channel. residues[q] is None if quantity q is invalid."""
    node_id: int
    seq: int
    residue_index: int
    event: bool
    retx: bool
    residues: tuple

    def encode(self) -> bytes:
        flags = (self.residue_index << 6) | (0x20 if self.event else 0) | (0x10 if self.retx else 0)
        body = bytes(RESIDUE_INVALID if r is None else r for r in self.residues)
        return _with_crc(bytes([(FRAME_CHANNEL << 4) | PROTOCOL_VERSION, self.node_id]) + self.seq.to_bytes(2, "big")
                         + bytes([flags]) + body)

    @staticmethod
    def decode(buf: bytes) -> "ChannelFrame | None":
        if not _check(buf, CHANNEL_LEN, FRAME_CHANNEL) or buf[4] & 0x0F:
            return None
        idx = buf[4] >> 6
        if buf[1] in (0, 0xFF) or idx >= NUM_RESIDUES:
            return None
        res = tuple(None if r == RESIDUE_INVALID else r for r in buf[5:10])
        if any(r is not None and r >= MODULI[idx] for r in res):
            return None
        return ChannelFrame(buf[1], int.from_bytes(buf[2:4], "big"), idx, bool(buf[4] & 0x20), bool(buf[4] & 0x10), res)


def frame_type(buf: bytes) -> int | None:
    return buf[0] >> 4 if buf else None


def lora_airtime_ms(payload_len: int, sf: int = 9, bw_hz: int = 125_000, cr: int = 1, crc_on: bool = True,
                    implicit_header: bool = False, low_dr_optimize: bool = False, preamble: int = 8) -> float:
    """Semtech AN1200.13 time on air."""
    t_sym = (1 << sf) / bw_hz * 1000.0
    num = 8 * payload_len - 4 * sf + 28 + (16 if crc_on else 0) - (20 if implicit_header else 0)
    n = max(math.ceil(num / (4 * (sf - (2 if low_dr_optimize else 0)))) * (cr + 4), 0) + 8
    return (preamble + 4.25 + n) * t_sym


def seq_newer(a: int, b: int) -> bool:
    """RFC 1982 for 16-bit counters."""
    return a != b and ((a - b) & 0xFFFF) < 0x8000
