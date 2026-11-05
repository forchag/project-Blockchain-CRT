// Node-side Poll handling: recognise an addressed Poll, validate it, reject
// stale or replayed requests, and schedule the five-quantity response inside
// the node's slot.
#include "mac.h"

namespace agri {

const char* poll_decision_name(PollDecision d) {
  switch (d) {
    case POLL_ACCEPT: return "ACCEPT";
    case POLL_NOT_ADDRESSED: return "NOT_ADDRESSED";
    case POLL_MALFORMED: return "MALFORMED";
    case POLL_STALE_ID: return "STALE_ID";
    case POLL_STALE_TIME: return "STALE_TIME";
    case POLL_OUTSIDE_SLOT: return "OUTSIDE_SLOT";
  }
  return "UNKNOWN";
}

uint32_t PollReceiver::response_duration_ms() const {
  // Quantity-major: 3 residue frames, then the ACK window, five times.
  const double frame = lora_airtime_ms(RESIDUE_FRAME_LEN);
  const double per_quantity = NUM_RESIDUES * frame + (NUM_RESIDUES - 1) * cfg_.inter_frame_gap_ms + cfg_.ack_window_ms;
  return cfg_.turnaround_ms + static_cast<uint32_t>(NUM_QUANTITIES * per_quantity + 0.5);
}

PollResult PollReceiver::handle(const uint8_t* buf, size_t len, uint32_t rx_end_ms, uint32_t local_unix,
                                uint32_t slot_start_ms, uint32_t slot_end_ms) {
  PollResult r{};
  PollFrame f;
  if (!decode_poll_frame(buf, len, &f)) {
    r.decision = POLL_MALFORMED;
    return r;
  }
  r.poll_id = f.poll_id;
  if (f.node_id != cfg_.node_id) {
    r.decision = POLL_NOT_ADDRESSED;
    return r;
  }
  if (have_last_ && !seq_newer(f.poll_id, last_poll_id_)) {
    r.decision = POLL_STALE_ID;
    return r;
  }
  const int64_t skew = static_cast<int64_t>(f.gateway_time) - static_cast<int64_t>(local_unix);
  r.clock_offset_s = static_cast<int32_t>(skew);
  if ((skew < 0 ? -skew : skew) > cfg_.max_clock_skew_s) {
    r.decision = POLL_STALE_TIME;
    return r;
  }
  // The poll must arrive within the slot (with guard), and the full response must fit before slot end.
  const bool early = static_cast<int32_t>(slot_start_ms - cfg_.slot_guard_ms - rx_end_ms) > 0;
  r.response_start_ms = rx_end_ms + cfg_.turnaround_ms;
  r.response_deadline_ms = slot_end_ms;
  const bool too_late = static_cast<int32_t>(r.response_start_ms + response_duration_ms() - slot_end_ms) > 0;
  if (early || too_late) {
    r.decision = POLL_OUTSIDE_SLOT;
    return r;
  }
  r.resync_rtc = (skew < 0 ? -skew : skew) > cfg_.resync_threshold_s;
  last_poll_id_ = f.poll_id;
  have_last_ = true;
  r.decision = POLL_ACCEPT;
  return r;
}

}  // namespace agri
