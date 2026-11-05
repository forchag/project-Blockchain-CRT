"""Poll scheduler: which node each gateway polls, and when.

1800 s cycle aligned to UTC :00/:30, split into four 450 s zone windows
(North, South, East, West). Inside its window a gateway polls its zone's nodes
in roster order, one 34 s slot each; each poll is sent at slot start plus a
small random jitter so neighbouring nodes' clocks never line up.

    python3 -m gateway.mac.poll_scheduler --gateway ex-gw-south --at 2030-01-15T00:00:00Z
"""
from __future__ import annotations

import argparse
import datetime as dt
import random
from dataclasses import dataclass

from . import config as cfgmod
from .protocol import POLL_LEN, PollFrame, lora_airtime_ms


@dataclass(frozen=True)
class PollSlot:
    cycle_start: int       # Unix s
    zone: str
    gateway_id: str
    node_id: int
    sensor_id: str
    position: int
    slot_start: int        # Unix s
    slot_end: int
    poll_time_ms: int      # Unix ms at which the poll is transmitted
    poll_id: int


class PollScheduler:
    def __init__(self, cfg: cfgmod.MacConfig, rng: random.Random | None = None):
        self.cfg = cfg
        self.rng = rng or random.SystemRandom()
        self._poll_id: dict[int, int] = {}
        s = cfg.schedule
        self.cycle_s, self.window_s, self.slot_s = s["cycle_s"], s["zone_window_s"], s["node_slot_s"]
        self.offsets = s["zone_coordination_offset_s"]
        self.jitter_ms = s["poll_jitter_ms"]

    def cycle_start(self, t: int) -> int:
        return t - t % self.cycle_s

    def next_poll_id(self, node_id: int) -> int:
        # Per-node 16-bit counter; the node accepts only ids newer than the last one it saw.
        nid = (self._poll_id.get(node_id, 0) + 1) & 0xFFFF
        self._poll_id[node_id] = nid
        return nid

    def restore_poll_ids(self, last: dict[int, int]) -> None:
        self._poll_id.update(last)

    def slots(self, cycle_start: int, gateway_id: str | None = None) -> list[PollSlot]:
        out = []
        for zone in self.cfg.schedule["zone_order"]:
            gw = self.cfg.raw["zones"][zone]["gateway_id"]
            if gateway_id and gw != gateway_id:
                continue
            base = cycle_start + self.offsets[zone]
            for n in self.cfg.zone_nodes(zone):
                start = base + n.position * self.slot_s
                jitter = int(self.rng.uniform(0, self.jitter_ms))
                out.append(PollSlot(cycle_start, zone, gw, n.node_id, n.sensor_id, n.position, start,
                                    start + self.slot_s, start * 1000 + jitter, self.next_poll_id(n.node_id)))
        return out

    def poll_frame(self, slot: PollSlot) -> bytes:
        return PollFrame(slot.node_id, slot.poll_id, slot.poll_time_ms // 1000).encode()

    def duty_cycle(self, gateway_id: str, retx_fraction: float = 0.0) -> float:
        """Gateway transmit duty cycle per cycle: one poll + five ACKs per node."""
        zone = self.cfg.gateway_zone(gateway_id)
        n = len(self.cfg.zone_nodes(zone))
        tx_ms = n * (lora_airtime_ms(POLL_LEN) + 5 * (1 + retx_fraction) * lora_airtime_ms(8))
        return tx_ms / (self.cycle_s * 1000)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=cfgmod.DEFAULT_PATH)
    ap.add_argument("--deployment", default=cfgmod.DEFAULT_DEPLOYMENT)
    ap.add_argument("--gateway")
    ap.add_argument("--at", default=None, help="ISO time inside the cycle (default: now)")
    ap.add_argument("--seed", type=int, default=None, help="fixed jitter (testing only)")
    a = ap.parse_args()
    cfg = cfgmod.load(a.config, a.deployment)
    sched = PollScheduler(cfg, random.Random(a.seed) if a.seed is not None else None)
    t = dt.datetime.fromisoformat(a.at.replace("Z", "+00:00")) if a.at else dt.datetime.now(dt.timezone.utc)
    c = sched.cycle_start(int(t.timestamp()))
    print("zone,gateway,node_id,sensor_id,slot_start_utc,poll_time_utc,poll_id")
    for s in sched.slots(c, a.gateway):
        st = dt.datetime.fromtimestamp(s.slot_start, dt.timezone.utc).isoformat()
        pt = dt.datetime.fromtimestamp(s.poll_time_ms / 1000, dt.timezone.utc).isoformat(timespec="milliseconds")
        print(f"{s.zone},{s.gateway_id},{s.node_id},{s.sensor_id},{st},{pt},{s.poll_id}")
    for gw in sorted({z["gateway_id"] for z in cfg.raw["zones"].values()}):
        print(f"# {gw} duty cycle {sched.duty_cycle(gw):.4%} (limit {cfg.radio['duty_cycle_limit']:.0%})")


if __name__ == "__main__":
    main()
