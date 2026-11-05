"""Timeouts, retries, duplicate suppression and retry accounting at the gateway.

Two retry loops run here:
  * poll retries: a polled node that sends nothing within poll_response_timeout_ms
    is re-polled, up to max_poll_attempts, while its slot still has room;
  * quantity retries: a quantity the gateway cannot reconstruct gets a
    retransmission request, up to max_retries_per_quantity, then is abandoned.
Every decision is counted per node and emitted as an event for the gateway log.
"""
from __future__ import annotations

import collections
import time
from dataclasses import dataclass, field


@dataclass
class RetryStats:
    polls_sent: int = 0
    poll_retries: int = 0
    polls_unanswered: int = 0
    retx_requests: int = 0
    quantities_abandoned: int = 0
    duplicate_frames: int = 0
    rejected_frames: int = 0


@dataclass
class RetryEvent:
    t_ms: int
    node_id: int
    kind: str
    detail: str = ""


@dataclass
class _PollState:
    slot_end_ms: int
    attempts: int = 0
    last_poll_ms: int = 0
    answered: bool = False


class RetryManager:
    def __init__(self, retry_cfg: dict, response_budget_ms: int, clock=lambda: int(time.time() * 1000)):
        self.ack_timeout_ms = retry_cfg["ack_timeout_ms"]
        self.max_retries = retry_cfg["max_retries_per_quantity"]
        self.max_poll_attempts = retry_cfg["max_poll_attempts"]
        self.poll_timeout_ms = retry_cfg["poll_response_timeout_ms"]
        self.dup_window = retry_cfg["duplicate_window_readings"]
        self.response_budget_ms = response_budget_ms  # time a full reading needs
        self.clock = clock
        self.stats: dict[int, RetryStats] = collections.defaultdict(RetryStats)
        self.events: list[RetryEvent] = []
        self._polls: dict[int, _PollState] = {}
        self._retx: dict[tuple[int, int, int], int] = {}
        self._seen: dict[int, collections.OrderedDict] = collections.defaultdict(collections.OrderedDict)

    # ------------------------------------------------------------------ polls
    def poll_sent(self, node_id: int, slot_end_ms: int, now_ms: int | None = None) -> None:
        now = self.clock() if now_ms is None else now_ms
        st = self._polls.get(node_id)
        if st is None or st.slot_end_ms != slot_end_ms:
            st = self._polls[node_id] = _PollState(slot_end_ms)
        else:
            self.stats[node_id].poll_retries += 1
        st.attempts += 1
        st.last_poll_ms = now
        self.stats[node_id].polls_sent += 1

    def poll_answered(self, node_id: int) -> None:
        if node_id in self._polls:
            self._polls[node_id].answered = True

    def repoll_due(self, node_id: int, now_ms: int | None = None) -> bool:
        """True if the node should be polled again now."""
        now = self.clock() if now_ms is None else now_ms
        st = self._polls.get(node_id)
        if st is None or st.answered or now - st.last_poll_ms < self.poll_timeout_ms:
            return False
        if st.attempts >= self.max_poll_attempts or now + self.response_budget_ms > st.slot_end_ms:
            if st.attempts and not st.answered:
                self.stats[node_id].polls_unanswered += 1
                self._event(now, node_id, "poll_unanswered", f"attempts={st.attempts}")
                st.answered = True  # stop tracking this slot
            return False
        return True

    # ------------------------------------------------------------- quantities
    def may_request_retx(self, node_id: int, seq: int, quantity: int) -> bool:
        return self._retx.get((node_id, seq, quantity), 0) < self.max_retries

    def retx_requested(self, node_id: int, seq: int, quantity: int, mask: int, now_ms: int | None = None) -> None:
        k = (node_id, seq, quantity)
        self._retx[k] = self._retx.get(k, 0) + 1
        self.stats[node_id].retx_requests += 1
        self._event(self.clock() if now_ms is None else now_ms, node_id, "retx_request",
                    f"seq={seq} q={quantity} mask={mask:03b} attempt={self._retx[k]}")

    def retries_used(self, node_id: int, seq: int, quantity: int) -> int:
        return self._retx.get((node_id, seq, quantity), 0)

    def abandoned(self, node_id: int, seq: int, quantity: int, reason: str, now_ms: int | None = None) -> None:
        self.stats[node_id].quantities_abandoned += 1
        self._event(self.clock() if now_ms is None else now_ms, node_id, "quantity_abandoned",
                    f"seq={seq} q={quantity} {reason}")

    # -------------------------------------------------------------- duplicates
    def is_duplicate(self, node_id: int, seq: int, quantity: int, residue_index: int) -> bool:
        """Remembers (seq, quantity, residue) for the last dup_window readings of each node."""
        seen = self._seen[node_id]
        key = (seq, quantity, residue_index)
        if key in seen:
            self.stats[node_id].duplicate_frames += 1
            return True
        seen[key] = True
        seqs = {k[0] for k in seen}
        while len(seqs) > self.dup_window:
            oldest = next(iter(seen))[0]
            for k in [k for k in seen if k[0] == oldest]:
                del seen[k]
            seqs.discard(oldest)
        return False

    def forget(self, node_id: int, seq: int, quantity: int) -> None:
        """Drop remembered residues of one quantity so a requested fresh triplet is not treated as duplicates."""
        seen = self._seen[node_id]
        for k in [k for k in seen if k[0] == seq and k[1] == quantity]:
            del seen[k]

    def rejected(self, node_id: int, reason: str, now_ms: int | None = None) -> None:
        self.stats[node_id].rejected_frames += 1
        self._event(self.clock() if now_ms is None else now_ms, node_id, "frame_rejected", reason)

    def _event(self, t_ms: int, node_id: int, kind: str, detail: str) -> None:
        self.events.append(RetryEvent(t_ms, node_id, kind, detail))

    def summary(self) -> dict[int, dict]:
        return {n: vars(s).copy() for n, s in sorted(self.stats.items())}
