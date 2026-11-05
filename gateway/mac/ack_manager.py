"""Residue intake, CRT reconstruction and ACK generation at the gateway.

Two framings, selected by frames.frame_mode in config/radio.example.yaml:
  * 15 (AckManager): one residue frame per quantity per channel, one ACK per quantity;
  * 3 (ChannelAckManager): one channel frame per channel carrying all five residues,
    one ACK per reading whose masks list channels.
make_ack_manager() picks the right one.

FRAME_MODE 15: one ACK per quantity. A quantity is decided as soon as every residue expected
in the current attempt has arrived, or ack_delay_ms after the last frame of the
attempt otherwise. Before a residue is accepted the gateway checks:
  1. the frame decodes (type, version, CRC-16, field ranges);
  2. the node is in the roster and belongs to this gateway's zone;
  3. the frame arrives inside that node's slot (scheduled) or inside the zone
     window (event-flagged);
  4. the sequence number is not older than the node's last completed reading;
  5. it is not a duplicate of a residue already held.
A completed reading (all five quantities reconstructed, invalid or abandoned) is
returned to the caller (gateway_service.py), which signs it (Ed25519) and hands it to the
Fabric submitter, which builds and signs the WriteSensor proposal.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import protocol as p
from .config import MacConfig
from .retry_manager import RetryManager


@dataclass
class QuantityAssembly:
    residues: list = field(default_factory=lambda: [None, None, None])
    expected_mask: int = 0b111
    valid_flag: bool | None = None
    last_frame_ms: int = 0
    decided: bool = False
    final_ack: bytes | None = None
    status: str = "pending"        # reconstructed | invalid_at_node | abandoned
    scaled: int | None = None
    crt_status: int | None = None

    @property
    def mask(self) -> int:
        return sum(1 << i for i, r in enumerate(self.residues) if r is not None)


@dataclass
class ReadingAssembly:
    node_id: int
    seq: int
    event: bool
    first_frame_ms: int
    quantities: list = field(default_factory=lambda: [QuantityAssembly() for _ in range(p.NUM_QUANTITIES)])

    def complete(self) -> bool:
        return all(q.decided for q in self.quantities)


@dataclass
class CompletedReading:
    node_id: int
    sensor_id: str
    device_id: str
    seq: int
    event: bool
    received_at_ms: int
    values: dict          # quantity name -> float | None
    status: dict          # quantity name -> reconstructed | invalid_at_node | abandoned
    residues_received: dict
    retries: int


class AckManager:
    def __init__(self, cfg: MacConfig, gateway_id: str, retry: RetryManager):
        self.cfg = cfg
        self.gateway_id = gateway_id
        self.zone = cfg.gateway_zone(gateway_id)
        self.retry = retry
        self.ack_delay_ms = cfg.ack["ack_delay_ms"]
        self.need = cfg.ack["request_retx_when_received_lt"]
        self.restore_redundancy = cfg.ack["restore_redundancy"]
        self._open: dict[tuple[int, int], ReadingAssembly] = {}
        self._last_done: dict[int, int] = {}
        self._slot: dict[int, tuple[int, int]] = {}
        self._finished: dict[int, ReadingAssembly] = {}
        sch = cfg.schedule
        off = sch["zone_coordination_offset_s"][self.zone]
        self._zone_window = (off, off + sch["zone_window_s"])
        self._cycle = sch["cycle_s"]

    def set_slot(self, node_id: int, start_ms: int, end_ms: int) -> None:
        self._slot[node_id] = (start_ms, end_ms)

    # ----------------------------------------------------------------- intake
    def on_frame(self, buf: bytes, rx_ms: int) -> list[bytes]:
        """Feed one received LoRa payload. Returns ACK frames to transmit now."""
        f = p.ResidueFrame.decode(buf)
        if f is None:
            self.retry.rejected(buf[1] if len(buf) > 1 else 0, "malformed_or_crc", rx_ms)
            return []
        node = self.cfg.nodes.get(f.node_id)
        if node is None:
            self.retry.rejected(f.node_id, "unknown_node", rx_ms)
            return []
        if node.gateway_id != self.gateway_id:
            self.retry.rejected(f.node_id, f"wrong_zone:{node.zone}", rx_ms)
            return []
        if not self._in_time(f, rx_ms):
            self.retry.rejected(f.node_id, "outside_slot", rx_ms)
            return []
        last = self._last_done.get(f.node_id)
        key = (f.node_id, f.seq)
        if last is not None and key not in self._open and not p.seq_newer(f.seq, last):
            # Old reading. If it is a retransmission of the reading we just finished,
            # the node missed our final ACK: repeat it.
            if f.seq == last and f.retx:
                return self._repeat_final(f)
            self.retry.rejected(f.node_id, f"stale_seq:{f.seq}<= {last}", rx_ms)
            return []
        ra = self._open.get(key)
        if ra is None:
            ra = self._open[key] = ReadingAssembly(f.node_id, f.seq, f.event, rx_ms)
            self.retry.poll_answered(f.node_id)
        qa = ra.quantities[f.quantity]
        if qa.decided:
            self.retry.is_duplicate(f.node_id, f.seq, f.quantity, f.residue_index)
            return [qa.final_ack] if qa.final_ack else []
        if self.retry.is_duplicate(f.node_id, f.seq, f.quantity, f.residue_index) and qa.residues[f.residue_index] is not None:
            return []
        qa.residues[f.residue_index] = f.residue
        qa.valid_flag = f.valid if qa.valid_flag is None else (qa.valid_flag and f.valid)
        qa.last_frame_ms = rx_ms
        if (qa.mask & qa.expected_mask) == qa.expected_mask:
            return self._decide(ra, f.quantity, rx_ms)
        return []

    def on_timer(self, now_ms: int) -> list[bytes]:
        """Decide quantities whose attempt went quiet. Call every ~50 ms."""
        out = []
        for ra in list(self._open.values()):
            for qi, qa in enumerate(ra.quantities):
                if not qa.decided and qa.last_frame_ms and now_ms - qa.last_frame_ms >= self.ack_delay_ms + 2 * p.lora_airtime_ms(8):
                    out += self._decide(ra, qi, now_ms)
        return out

    def pop_completed(self) -> list[CompletedReading]:
        done = []
        for key, ra in list(self._open.items()):
            if not ra.complete():
                continue
            node = self.cfg.nodes[ra.node_id]
            vals, st, rec = {}, {}, {}
            for qi, qa in enumerate(ra.quantities):
                name = p.QUANTITIES[qi]
                vals[name] = p.unscale_value(qi, qa.scaled) if qa.status == "reconstructed" else None
                st[name] = qa.status
                rec[name] = bin(qa.mask).count("1")
            retries = sum(self.retry.retries_used(ra.node_id, ra.seq, u) for u in self._ack_units())
            done.append(CompletedReading(ra.node_id, node.sensor_id, node.device_id, ra.seq, ra.event, ra.first_frame_ms,
                                         vals, st, rec, retries))
            self._last_done[ra.node_id] = ra.seq
            self._finished[ra.node_id] = ra
            del self._open[key]
        return done

    # --------------------------------------------------------------- internals
    def _ack_units(self):
        return range(p.NUM_QUANTITIES)

    def _in_time(self, f, rx_ms: int) -> bool:
        if f.event:
            pos = (rx_ms // 1000) % self._cycle
            return self._zone_window[0] <= pos < self._zone_window[1]
        slot = self._slot.get(f.node_id)
        if slot is None:
            return True  # scheduler not attached (bench use): accept
        guard = self.cfg.schedule["guard_interval_ms"]
        return slot[0] - guard <= rx_ms <= slot[1] + guard

    def _decide(self, ra: ReadingAssembly, qi: int, now_ms: int) -> list[bytes]:
        qa = ra.quantities[qi]
        mask = qa.mask
        if qa.valid_flag is False:
            # Node flagged the quantity invalid (sensor fault): acknowledge, nothing to reconstruct.
            qa.status, qa.crt_status = "invalid_at_node", p.CRT_OK
            return self._final(ra, qi, reconstructed=True, mask=mask)
        res = [r if r is not None else 0 for r in qa.residues]
        status, scaled = p.crt_reconstruct(res, mask)
        if status == p.CRT_OK and p.SCALE[qi][2] < scaled:
            status, scaled = p.CRT_OUT_OF_RANGE, None
        qa.crt_status = status
        got = bin(mask).count("1")
        if status == p.CRT_OK and (got >= 3 or not self.restore_redundancy):
            qa.scaled, qa.status = scaled, "reconstructed"
            return self._final(ra, qi, reconstructed=True, mask=mask)
        # Not reconstructable (or corrupt, or redundancy restoration wanted): ask again if allowed.
        missing = (~mask) & 0b111
        request = missing if status != p.CRT_INCONSISTENT and missing else 0b111
        if self.retry.may_request_retx(ra.node_id, ra.seq, qi):
            self.retry.retx_requested(ra.node_id, ra.seq, qi, request, now_ms)
            qa.expected_mask = mask | request if status != p.CRT_INCONSISTENT else 0b111
            if status == p.CRT_INCONSISTENT:
                qa.residues = [None, None, None]  # one of them is corrupt; take a fresh triplet
                self.retry.forget(ra.node_id, ra.seq, qi)
            qa.last_frame_ms = now_ms
            return [p.AckFrame(ra.node_id, ra.seq, qi, False, True, mask if status != p.CRT_INCONSISTENT else 0,
                               status).encode()]
        if status == p.CRT_OK:  # had two residues; redundancy not restored but value is good
            qa.scaled, qa.status = scaled, "reconstructed"
            return self._final(ra, qi, reconstructed=True, mask=mask)
        qa.status = "abandoned"
        self.retry.abandoned(ra.node_id, ra.seq, qi, p.CRT_STATUS[status], now_ms)
        return self._final(ra, qi, reconstructed=False, mask=mask)

    def _final(self, ra: ReadingAssembly, qi: int, reconstructed: bool, mask: int) -> list[bytes]:
        qa = ra.quantities[qi]
        qa.decided = True
        qa.final_ack = p.AckFrame(ra.node_id, ra.seq, qi, reconstructed, False, mask, qa.crt_status or 0).encode()
        return [qa.final_ack]

    def _repeat_final(self, f: p.ResidueFrame) -> list[bytes]:
        ra = self._finished.get(f.node_id)
        if ra is None or ra.seq != f.seq:
            return []
        self.retry.is_duplicate(f.node_id, f.seq, f.quantity, f.residue_index)
        ack = ra.quantities[f.quantity].final_ack
        return [ack] if ack else []


# ============================================================== FRAME_MODE 3
@dataclass
class ChannelReadingAssembly(ReadingAssembly):
    chan_mask: int = 0             # channels whose frame arrived in the current attempt
    expected_mask: int = 0b111
    last_frame_ms: int = 0
    decided: bool = False
    final_ack: bytes | None = None

    def complete(self) -> bool:
        return self.decided


class ChannelAckManager(AckManager):
    """FRAME_MODE 3 intake: three 12-byte channel frames per reading, one ACK per reading.

    The checks before a frame is accepted are the same as in FRAME_MODE 15. The reading
    is decided once every expected channel has arrived, or ack_delay_ms after the last
    frame. Any two channels reconstruct all five quantities; the retry budget
    (max_retries_per_quantity) applies to the whole reading, keyed as quantity 7.
    """
    UNIT = p.ACK_WHOLE_READING

    def _ack_units(self):
        return [self.UNIT]

    def on_frame(self, buf: bytes, rx_ms: int) -> list[bytes]:
        f = p.ChannelFrame.decode(buf)
        if f is None:
            self.retry.rejected(buf[1] if len(buf) > 1 else 0, "malformed_or_crc", rx_ms)
            return []
        node = self.cfg.nodes.get(f.node_id)
        if node is None:
            self.retry.rejected(f.node_id, "unknown_node", rx_ms)
            return []
        if node.gateway_id != self.gateway_id:
            self.retry.rejected(f.node_id, f"wrong_zone:{node.zone}", rx_ms)
            return []
        if not self._in_time(f, rx_ms):
            self.retry.rejected(f.node_id, "outside_slot", rx_ms)
            return []
        last = self._last_done.get(f.node_id)
        key = (f.node_id, f.seq)
        if last is not None and key not in self._open and not p.seq_newer(f.seq, last):
            if f.seq == last and f.retx:
                ra = self._finished.get(f.node_id)
                return [ra.final_ack] if ra is not None and ra.seq == f.seq and ra.final_ack else []
            self.retry.rejected(f.node_id, f"stale_seq:{f.seq}<= {last}", rx_ms)
            return []
        ra = self._open.get(key)
        if ra is None:
            ra = self._open[key] = ChannelReadingAssembly(f.node_id, f.seq, f.event, rx_ms)
            self.retry.poll_answered(f.node_id)
        if ra.decided:
            return [ra.final_ack] if ra.final_ack else []
        bit = 1 << f.residue_index
        if self.retry.is_duplicate(f.node_id, f.seq, self.UNIT, f.residue_index) and ra.chan_mask & bit:
            return []
        for qi, r in enumerate(f.residues):
            qa = ra.quantities[qi]
            qa.residues[f.residue_index] = 0 if r is None else r
            qa.valid_flag = (r is not None) if qa.valid_flag is None else (qa.valid_flag and r is not None)
        ra.chan_mask |= bit
        ra.last_frame_ms = rx_ms
        if (ra.chan_mask & ra.expected_mask) == ra.expected_mask:
            return self._decide_reading(ra, rx_ms)
        return []

    def on_timer(self, now_ms: int) -> list[bytes]:
        out = []
        for ra in list(self._open.values()):
            if not ra.decided and ra.last_frame_ms and \
                    now_ms - ra.last_frame_ms >= self.ack_delay_ms + 2 * p.lora_airtime_ms(p.CHANNEL_LEN):
                out += self._decide_reading(ra, now_ms)
        return out

    def _decide_reading(self, ra: ChannelReadingAssembly, now_ms: int) -> list[bytes]:
        mask = ra.chan_mask
        results = []
        for qi, qa in enumerate(ra.quantities):
            if qa.valid_flag is False:
                results.append((p.CRT_OK, None, True))
                continue
            status, scaled = p.crt_reconstruct([r if r is not None else 0 for r in qa.residues], mask)
            if status == p.CRT_OK and p.SCALE[qi][2] < scaled:
                status, scaled = p.CRT_OUT_OF_RANGE, None
            results.append((status, scaled, False))
        all_ok = all(st == p.CRT_OK for st, _, _ in results)
        inconsistent = any(st == p.CRT_INCONSISTENT for st, _, _ in results)
        worst = max(st for st, _, _ in results)
        got = bin(mask).count("1")
        if all_ok and (got >= 3 or not self.restore_redundancy):
            return self._final_reading(ra, results, True, mask, p.CRT_OK)
        if self.retry.may_request_retx(ra.node_id, ra.seq, self.UNIT):
            missing = (~mask) & 0b111
            request = 0b111 if inconsistent or not missing else missing
            self.retry.retx_requested(ra.node_id, ra.seq, self.UNIT, request, now_ms)
            if inconsistent:  # one channel frame is corrupt: take all three again
                for qa in ra.quantities:
                    qa.residues, qa.valid_flag = [None, None, None], None
                ra.chan_mask, ra.expected_mask = 0, 0b111
                self.retry.forget(ra.node_id, ra.seq, self.UNIT)
            else:
                ra.expected_mask = mask | request
            ra.last_frame_ms = now_ms
            return [p.AckFrame(ra.node_id, ra.seq, self.UNIT, False, True, 0 if inconsistent else mask, worst).encode()]
        if all_ok:  # two channels, redundancy not restored, but every value is good
            return self._final_reading(ra, results, True, mask, p.CRT_OK)
        self.retry.abandoned(ra.node_id, ra.seq, self.UNIT, p.CRT_STATUS[worst], now_ms)
        return self._final_reading(ra, results, False, mask, worst)

    def _final_reading(self, ra, results, reconstructed: bool, mask: int, status: int) -> list[bytes]:
        for qa, (st, scaled, invalid) in zip(ra.quantities, results):
            qa.decided, qa.crt_status = True, st
            if invalid:
                qa.status = "invalid_at_node"
            elif st == p.CRT_OK:
                qa.status, qa.scaled = "reconstructed", scaled
            else:
                qa.status = "abandoned"
        ra.decided = True
        ra.final_ack = p.AckFrame(ra.node_id, ra.seq, self.UNIT, reconstructed, False, mask, status).encode()
        return [ra.final_ack]


def make_ack_manager(cfg: MacConfig, gateway_id: str, retry: RetryManager) -> AckManager:
    """AckManager for frame_mode 15, ChannelAckManager for frame_mode 3."""
    return (ChannelAckManager if cfg.frame_mode == 3 else AckManager)(cfg, gateway_id, retry)
