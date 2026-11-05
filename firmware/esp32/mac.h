// Node-side MAC: Poll handling (poll_receiver.cpp) and ACK/retransmission
// tracking (ack_receiver.cpp). Platform-independent; timing parameters mirror
// config/radio.example.yaml.
#pragma once
#include <stddef.h>
#include <stdint.h>

#include "lora_protocol.h"

namespace agri {

// --------------------------------------------------------------------- Poll
struct PollConfig {
  uint8_t node_id = 0;
  uint32_t max_clock_skew_s = 900;    // |gateway_time - RTC| above this: poll treated as stale/replayed
  uint32_t resync_threshold_s = 2;    // RTC adjusted to gateway time when skew exceeds this
  uint32_t turnaround_ms = 60;        // RX->TX switch before the first residue frame
  uint32_t inter_frame_gap_ms = 25;   // between consecutive residue frames
  uint32_t ack_window_ms = 400;       // RX window per quantity for its ACK
  uint32_t slot_guard_ms = 2000;      // poll accepted up to this early/late around the slot
};

enum PollDecision : uint8_t {
  POLL_ACCEPT = 0,
  POLL_NOT_ADDRESSED,   // valid frame for another node: ignore silently
  POLL_MALFORMED,       // wrong length, type, version or CRC
  POLL_STALE_ID,        // poll_id not newer than the last accepted one (replay/duplicate)
  POLL_STALE_TIME,      // gateway clock too far from ours
  POLL_OUTSIDE_SLOT,    // not enough slot time left to send a full reading
};

struct PollResult {
  PollDecision decision;
  uint16_t poll_id;
  int32_t clock_offset_s;          // gateway_time - local RTC
  bool resync_rtc;
  uint32_t response_start_ms;      // node clock (millis) at which to send the first residue frame
  uint32_t response_deadline_ms;   // last frame and ACKs must finish before this
};

const char* poll_decision_name(PollDecision d);

class PollReceiver {
 public:
  explicit PollReceiver(const PollConfig& cfg) : cfg_(cfg) {}
  // rx_end_ms: millis() when the poll finished arriving. local_unix: RTC time then.
  // slot_*_ms: this node's slot in millis() terms.
  PollResult handle(const uint8_t* buf, size_t len, uint32_t rx_end_ms, uint32_t local_unix, uint32_t slot_start_ms,
                    uint32_t slot_end_ms);
  // Time the node needs for one reading with no retries (15 frames + 5 ACK windows).
  uint32_t response_duration_ms() const;
  uint16_t last_poll_id() const { return last_poll_id_; }
  void restore(uint16_t last_poll_id, bool have) {
    last_poll_id_ = last_poll_id;
    have_last_ = have;
  }

 private:
  PollConfig cfg_;
  uint16_t last_poll_id_ = 0;
  bool have_last_ = false;
};

// ---------------------------------------------------------------------- ACK
struct AckConfig {
  uint32_t ack_timeout_ms = 400;  // after a quantity's third residue frame
  uint8_t max_retries = 2;        // per quantity
};

enum QuantityState : uint8_t { QS_PENDING = 0, QS_AWAITING_ACK, QS_NEEDS_RETX, QS_ACKED, QS_FAILED };

enum AckEvent : uint8_t {
  ACK_ACCEPTED = 0,     // quantity reconstructed by the gateway
  ACK_RETX_REQUESTED,   // gateway could not reconstruct; resend the residues it lacks
  ACK_ABANDONED,        // gateway gave up on this quantity (reconstructed=0, retx_request=0): stop retrying
  ACK_DUPLICATE,        // ACK for a quantity already acknowledged: ignored
  ACK_WRONG_NODE,
  ACK_WRONG_SEQ,        // ACK for another (older) reading
  ACK_UNEXPECTED,       // ACK for a quantity not sent yet
  ACK_MALFORMED,
};

const char* ack_event_name(AckEvent e);

struct QuantityTrack {
  QuantityState state = QS_PENDING;
  uint8_t retries = 0;
  uint8_t resend_mask = 0;     // residue indices to send on the next attempt
  uint32_t sent_at_ms = 0;
};

class AckTracker {
 public:
  explicit AckTracker(const AckConfig& cfg) : cfg_(cfg) {}
  // units: NUM_QUANTITIES (FRAME_MODE 15, one ACK per quantity) or 1 (FRAME_MODE 3, one whole-reading ACK
  // whose masks are channel masks). Unit u stands for quantity u, or for the whole reading.
  void begin(uint8_t node_id, uint16_t seq, uint8_t units = FRAME_MODE == 3 ? 1 : NUM_QUANTITIES);
  uint8_t units() const { return units_; }
  void on_sent(uint8_t quantity, uint32_t now_ms);
  AckEvent on_frame(const uint8_t* buf, size_t len);
  // Advances timeouts. Returns true if the quantity must be retransmitted now;
  // *mask receives the residue indices to send.
  bool due_for_retx(uint8_t quantity, uint32_t now_ms, uint8_t* mask);
  void on_retx(uint8_t quantity, uint32_t now_ms);
  bool finished() const;            // every quantity ACKED or FAILED
  uint8_t acked_count() const;
  uint8_t failed_count() const;
  uint32_t total_retries() const;
  const QuantityTrack& track(uint8_t q) const { return q_[q]; }
  uint16_t seq() const { return seq_; }

 private:
  AckConfig cfg_;
  uint8_t node_id_ = 0;
  uint16_t seq_ = 0;
  uint8_t units_ = NUM_QUANTITIES;
  QuantityTrack q_[NUM_QUANTITIES];
};

// ---------------------------------------------------------------- schedule and sequence numbers
// Pure helpers used by main.cpp (same arithmetic as the deployed reference build), kept here so the
// host tests can exercise them. Times are Unix seconds, UTC.
struct SlotSchedule {
  uint32_t cycle_s = 1800;        // one reading per node per cycle, aligned to hh:00 / hh:30 UTC
  uint32_t zone_window_s = 450;   // four zone windows tile the cycle
  uint32_t node_slot_s = 34;      // one slot per node position inside its zone window
};

// Start of the node's slot that contains t, or of the next one if t is past it.
inline uint32_t current_or_next_slot_start(uint32_t t, uint8_t zone_index, uint8_t zone_position,
                                           const SlotSchedule& s = SlotSchedule{}) {
  const uint32_t offset = zone_index * s.zone_window_s + zone_position * s.node_slot_s;
  const uint32_t cycle_start = t - (t % s.cycle_s);
  uint32_t start = cycle_start + offset;
  if (start + s.node_slot_s <= t) start += s.cycle_s;
  return start;
}

// Deep-sleep target: the earlier of the next event check and the wake-up ahead of the next slot.
inline uint32_t next_wake_at(uint32_t now, uint32_t next_slot_start, uint32_t wake_lead_s, uint32_t event_check_s) {
  const uint32_t slot_wake = next_slot_start - wake_lead_s;
  const uint32_t event_wake = now + event_check_s;
  return event_wake < slot_wake ? event_wake : slot_wake;
}

// The sequence number is checkpointed to NVS every `every` readings; after a power loss the node
// skips past every number it may have used since the checkpoint, so none is ever reused.
inline bool sequence_checkpoint_due(uint16_t seq, uint16_t every) { return seq % every == 0; }
inline uint16_t resume_sequence(uint16_t checkpointed, uint16_t every) {
  return static_cast<uint16_t>(checkpointed + every);
}

}  // namespace agri
