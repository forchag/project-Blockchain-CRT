// ACK / retransmission handling on the node.
//
// FRAME_MODE 15: the gateway sends one ACK per quantity once it has tried to reconstruct it.
// FRAME_MODE 3: it sends one ACK per reading (quantity code 7); masks then refer to channels.
// The retry rules below are the same in both modes; only the unit acknowledged differs.
// reconstructed=1: done. retx_request=1: resend the residues missing from
// received_mask. No ACK within ack_timeout_ms: resend all three residues.
// reconstructed=0 and retx_request=0: the gateway abandoned the quantity.
// After max_retries the quantity is marked failed and the reading continues.
#include "mac.h"

namespace agri {

const char* ack_event_name(AckEvent e) {
  switch (e) {
    case ACK_ACCEPTED: return "ACCEPTED";
    case ACK_RETX_REQUESTED: return "RETX_REQUESTED";
    case ACK_ABANDONED: return "ABANDONED";
    case ACK_DUPLICATE: return "DUPLICATE";
    case ACK_WRONG_NODE: return "WRONG_NODE";
    case ACK_WRONG_SEQ: return "WRONG_SEQ";
    case ACK_UNEXPECTED: return "UNEXPECTED";
    case ACK_MALFORMED: return "MALFORMED";
  }
  return "UNKNOWN";
}

void AckTracker::begin(uint8_t node_id, uint16_t seq, uint8_t units) {
  node_id_ = node_id;
  seq_ = seq;
  units_ = units == 1 ? 1 : NUM_QUANTITIES;
  for (auto& t : q_) t = QuantityTrack{};
}

void AckTracker::on_sent(uint8_t quantity, uint32_t now_ms) {
  if (quantity >= units_) return;
  q_[quantity].state = QS_AWAITING_ACK;
  q_[quantity].sent_at_ms = now_ms;
  q_[quantity].resend_mask = 0;
}

AckEvent AckTracker::on_frame(const uint8_t* buf, size_t len) {
  AckFrame a;
  if (!decode_ack_frame(buf, len, &a)) return ACK_MALFORMED;
  if (a.node_id != node_id_) return ACK_WRONG_NODE;
  if (a.seq != seq_) return ACK_WRONG_SEQ;
  uint8_t unit;
  if (units_ == 1) {
    if (a.quantity != ACK_WHOLE_READING) return ACK_UNEXPECTED;  // per-quantity ACK in whole-reading mode
    unit = 0;
  } else {
    if (a.quantity >= NUM_QUANTITIES) return ACK_UNEXPECTED;  // whole-reading ACK in per-quantity mode
    unit = a.quantity;
  }
  QuantityTrack& t = q_[unit];
  if (t.state == QS_ACKED || t.state == QS_FAILED) return ACK_DUPLICATE;
  if (t.state == QS_PENDING) return ACK_UNEXPECTED;
  if (a.reconstructed) {
    t.state = QS_ACKED;
    t.resend_mask = 0;
    return ACK_ACCEPTED;
  }
  if (a.retx_request) {
    const uint8_t missing = static_cast<uint8_t>(~a.received_mask & 0x07);
    // A CRT_INCONSISTENT triplet has no missing residue but one corrupt one: resend all three.
    t.resend_mask = missing ? missing : 0x07;
    t.state = QS_NEEDS_RETX;
    return ACK_RETX_REQUESTED;
  }
  t.state = QS_FAILED;  // neither accepted nor retransmission requested: the gateway abandoned it
  return ACK_ABANDONED;
}

bool AckTracker::due_for_retx(uint8_t quantity, uint32_t now_ms, uint8_t* mask) {
  QuantityTrack& t = q_[quantity];
  if (t.state == QS_AWAITING_ACK && now_ms - t.sent_at_ms >= cfg_.ack_timeout_ms) {
    t.state = QS_NEEDS_RETX;
    t.resend_mask = 0x07;  // no ACK at all: the gateway may have nothing
  }
  if (t.state != QS_NEEDS_RETX) return false;
  if (t.retries >= cfg_.max_retries) {
    t.state = QS_FAILED;
    return false;
  }
  *mask = t.resend_mask;
  return true;
}

void AckTracker::on_retx(uint8_t quantity, uint32_t now_ms) {
  QuantityTrack& t = q_[quantity];
  ++t.retries;
  t.state = QS_AWAITING_ACK;
  t.sent_at_ms = now_ms;
}

bool AckTracker::finished() const {
  for (uint8_t u = 0; u < units_; ++u)
    if (q_[u].state != QS_ACKED && q_[u].state != QS_FAILED) return false;
  return true;
}

uint8_t AckTracker::acked_count() const {
  uint8_t n = 0;
  for (uint8_t u = 0; u < units_; ++u) n += q_[u].state == QS_ACKED;
  return n;
}

uint8_t AckTracker::failed_count() const {
  uint8_t n = 0;
  for (uint8_t u = 0; u < units_; ++u) n += q_[u].state == QS_FAILED;
  return n;
}

uint32_t AckTracker::total_retries() const {
  uint32_t n = 0;
  for (uint8_t u = 0; u < units_; ++u) n += q_[u].retries;
  return n;
}

}  // namespace agri
