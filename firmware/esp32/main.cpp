// Field node firmware entry point (ESP32-WROOM-32 + RFM95W + DS3231).
//
// Duty cycle (all times UTC, cycle aligned to :00 and :30):
//   cycle 1800 s -> 4 zone windows of 450 s -> 34 s node slots (position 0..12 in the zone)
//   wake WAKE_LEAD_S before the slot, take the reading, listen for this node's Poll on
//   868.1 MHz, answer with the reading, retransmit what the gateway lacks, sleep. Framing is set
//   at build time:
//     FRAME_MODE 15: 15 residue frames (5 quantities x 3 residues, residue i on channel i), one ACK per quantity;
//     FRAME_MODE 3:  3 channel frames (all five residues for channel i on channel i), one ACK per reading.
//   Between slots the node wakes every EVENT_CHECK_S, samples soil moisture only, and
//   sends an unsolicited (event-flagged) reading if it moved by EVENT_DELTA_VWC or more.
//
// Order inside a reading: sense (SHT31 humidity, BH1750 illuminance, DS18B20 temperature,
// then analog after settle) -> calibrate and range-check -> scale -> CRT residues -> frames
// -> transmit -> ACK/retry. The DS18B20 12-bit conversion blocks for 750 ms; the analog
// front-end settle time (300 ms) therefore runs concurrently with the conversion, so the
// 3 s WAKE_LEAD_S window accommodates sense (~1.5 s) plus radio init (~0.25 s) with margin.
//
// State that must survive deep sleep lives in RTC slow memory; the sequence number is
// also checkpointed to NVS so a power loss never reuses a sequence number.
#include <Arduino.h>
#include <Preferences.h>
#include <RTClib.h>
#include <esp_sleep.h>
#include <esp_task_wdt.h>

#include "calibration.h"
#include "lora_protocol.h"
#include "mac.h"
#include "sensor_drivers.h"

using namespace agri;

#ifndef FIRMWARE_VERSION
#define FIRMWARE_VERSION "1.0.0"
#endif

namespace {
// ---------------------------------------------------------------- schedule (config/radio.example.yaml)
constexpr uint32_t CYCLE_S = 1800;
constexpr uint32_t ZONE_WINDOW_S = 450;
constexpr uint32_t NODE_SLOT_S = 34;
constexpr uint32_t WAKE_LEAD_S = 3;           // wake before slot start (sensing + radio init)
constexpr uint32_t POLL_LISTEN_S = 6;         // listen for the poll from slot start - 1 s
constexpr uint32_t EVENT_CHECK_S = 300;
constexpr float EVENT_DELTA_VWC = 5.0f;       // %VWC change that triggers an event report
constexpr uint32_t EVENT_MIN_SPACING_S = 600; // no more than one event report per 10 min
constexpr uint32_t SEQ_CHECKPOINT_EVERY = 16;
constexpr uint32_t WDT_TIMEOUT_S = 30;
constexpr uint8_t RADIO_INIT_ATTEMPTS = 3;

// ---------------------------------------------------------------- RTC-memory state
RTC_DATA_ATTR uint32_t rtc_magic = 0;
RTC_DATA_ATTR uint16_t rtc_seq = 0;
RTC_DATA_ATTR uint16_t rtc_last_poll_id = 0;
RTC_DATA_ATTR bool rtc_have_poll = false;
RTC_DATA_ATTR float rtc_last_reported_vwc = NAN;
RTC_DATA_ATTR uint32_t rtc_last_event_at = 0;
RTC_DATA_ATTR uint32_t rtc_missed_polls = 0;
RTC_DATA_ATTR uint32_t rtc_radio_faults = 0;
RTC_DATA_ATTR uint32_t rtc_failed_quantities = 0;
constexpr uint32_t RTC_MAGIC = 0xA6E1C0DE;

struct NodeIdentity {
  uint8_t node_id;        // 1..254 (0 and 0xFF are reserved); e.g. sensor ex-sns-001 -> node 1
  uint8_t zone_index;     // 0 North, 1 South, 2 East, 3 West (window order)
  uint8_t zone_position;  // 0..12 within the zone
};

NodeIdentity g_id;
RTC_DS3231 g_rtc;
SensorDrivers g_sensors;
LoraRadio g_radio;

NodeIdentity load_identity() {
  Preferences p;
  NodeIdentity id{0, 0, 0};
  if (p.begin("node", true)) {
    id.node_id = p.getUChar("node_id", 0);
    id.zone_index = p.getUChar("zone", 0);
    id.zone_position = p.getUChar("position", 0);
    p.end();
  }
#ifdef NODE_ID
  if (id.node_id == 0) {  // build-time fallback for bench units
    id.node_id = NODE_ID;
    id.zone_index = NODE_ZONE;
    id.zone_position = NODE_POSITION;
  }
#endif
  return id;
}

uint16_t next_seq() {
  const uint16_t s = ++rtc_seq;
  if (sequence_checkpoint_due(s, SEQ_CHECKPOINT_EVERY)) {
    Preferences p;
    if (p.begin("node", false)) {
      p.putUShort("seq", s);
      p.end();
    }
  }
  return s;
}

void cold_boot_state() {
  Preferences p;
  uint16_t saved = 0;
  if (p.begin("node", true)) {
    saved = p.getUShort("seq", 0);
    p.end();
  }
  // Up to SEQ_CHECKPOINT_EVERY-1 readings may have been sent since the checkpoint: skip past them.
  rtc_seq = resume_sequence(saved, SEQ_CHECKPOINT_EVERY);
  rtc_have_poll = false;
  rtc_last_reported_vwc = NAN;
  rtc_magic = RTC_MAGIC;
}

// Slot start (Unix s) of the next slot at or after t.
// Pure arithmetic lives in mac.h (current_or_next_slot_start) so the host tests cover it.
uint32_t next_slot_start(uint32_t t) {
  SlotSchedule sched;
  sched.cycle_s = CYCLE_S;
  sched.zone_window_s = ZONE_WINDOW_S;
  sched.node_slot_s = NODE_SLOT_S;
  return current_or_next_slot_start(t, g_id.zone_index, g_id.zone_position, sched);
}

bool radio_up() {
  for (uint8_t i = 0; i < RADIO_INIT_ATTEMPTS; ++i) {
    if (i == 0 ? g_radio.begin() : g_radio.reinit()) return true;
    delay(50);
  }
  ++rtc_radio_faults;
  return false;
}

// Encodes and sends one quantity's residues (those in mask) on their channels.
bool send_quantity(const Reading& r, uint8_t q, uint8_t mask, bool event, bool retx) {
  uint16_t scaled = 0;
  const bool valid = r.q[q].valid() && scale_value(static_cast<Quantity>(q), r.q[q].value, &scaled);
  uint8_t res[NUM_RESIDUES] = {0, 0, 0};
  if (valid) crt_residues(scaled, res);
  bool ok = true;
  for (uint8_t i = 0; i < NUM_RESIDUES; ++i) {
    if (!(mask & (1u << i))) continue;
    ResidueFrame f{g_id.node_id, r.seq, q, i, valid, event, retx, res[i]};
    uint8_t buf[RESIDUE_FRAME_LEN];
    encode_residue_frame(f, buf);
    if (!g_radio.send(i, buf, sizeof buf)) {
      ok = false;
      if (!g_radio.reinit()) return false;  // radio hung: give up this quantity's attempt
    }
    delay(25);  // inter-frame gap (config/radio.example.yaml: inter_frame_gap_ms)
  }
  return ok;
}

// FRAME_MODE 3: one frame per channel in mask, each carrying all five residues for that channel.
bool send_channels(const Reading& r, uint8_t mask, bool event, bool retx) {
  ChannelFrame f{g_id.node_id, r.seq, 0, event, retx, 0, {0, 0, 0, 0, 0}};
  uint8_t res[NUM_QUANTITIES][NUM_RESIDUES] = {};
  for (uint8_t q = 0; q < NUM_QUANTITIES; ++q) {
    uint16_t scaled = 0;
    if (r.q[q].valid() && scale_value(static_cast<Quantity>(q), r.q[q].value, &scaled)) {
      crt_residues(scaled, res[q]);
      f.valid_mask |= static_cast<uint8_t>(1u << q);
    }
  }
  bool ok = true;
  for (uint8_t i = 0; i < NUM_RESIDUES; ++i) {
    if (!(mask & (1u << i))) continue;
    f.residue_index = i;
    for (uint8_t q = 0; q < NUM_QUANTITIES; ++q) f.residues[q] = res[q][i];
    uint8_t buf[CHANNEL_FRAME_LEN];
    encode_channel_frame(f, buf);
    if (!g_radio.send(i, buf, sizeof buf)) {
      ok = false;
      if (!g_radio.reinit()) return false;
    }
    delay(25);
  }
  return ok;
}

// Sends one ACK unit: quantity u (FRAME_MODE 15) or the whole reading (FRAME_MODE 3).
bool send_unit(const Reading& r, uint8_t u, uint8_t mask, bool event, bool retx) {
#if FRAME_MODE == 3
  (void)u;
  return send_channels(r, mask, event, retx);
#else
  return send_quantity(r, u, mask, event, retx);
#endif
}

// Unit-by-unit transmission with ACK and bounded retries (5 units in FRAME_MODE 15, 1 in FRAME_MODE 3).
void transmit_reading(const Reading& r, bool event) {
  AckConfig acfg;
  AckTracker acks(acfg);
  acks.begin(g_id.node_id, r.seq);
  for (uint8_t q = 0; q < acks.units(); ++q) {
    send_unit(r, q, 0x07, event, false);
    acks.on_sent(q, millis());
    while (true) {
      uint8_t buf[16];
      int16_t rssi;
      float snr;
      const uint32_t waited = millis() - acks.track(q).sent_at_ms;
      const uint32_t remain = waited < acfg.ack_timeout_ms ? acfg.ack_timeout_ms - waited : 0;
      const size_t n = remain ? g_radio.receive(0, buf, sizeof buf, remain, &rssi, &snr) : 0;
      if (n) {
        const AckEvent e = acks.on_frame(buf, n);
        if ((e == ACK_ACCEPTED || e == ACK_ABANDONED) && acks.track(q).state != QS_AWAITING_ACK) break;
        if (e != ACK_RETX_REQUESTED) continue;  // stray/duplicate ACK: keep waiting
      }
      uint8_t mask = 0;
      if (acks.due_for_retx(q, millis(), &mask)) {
        send_unit(r, q, mask, event, true);
        acks.on_retx(q, millis());
        continue;
      }
      if (acks.track(q).state == QS_ACKED || acks.track(q).state == QS_FAILED) break;
    }
    esp_task_wdt_reset();
  }
  // a failed whole-reading unit loses all five quantities
  rtc_failed_quantities += acks.failed_count() * (acks.units() == 1 ? NUM_QUANTITIES : 1);
}

void take_reading(Reading* r) {
  r->seq = next_seq();
  r->captured_at = g_rtc.now().unixtime();
  g_sensors.read_all(r);  // SHT31 humidity, BH1750, DS18B20 (blocks 750 ms), analog
  if (r->q[Q_SOIL_MOISTURE].valid()) rtc_last_reported_vwc = r->q[Q_SOIL_MOISTURE].value;
}

void handle_slot(uint32_t slot_start) {
  Reading r{};
  take_reading(&r);
  if (!radio_up()) return;  // reading lost; counted in rtc_radio_faults
  PollConfig pcfg;
  pcfg.node_id = g_id.node_id;
  PollReceiver poll(pcfg);
  poll.restore(rtc_last_poll_id, rtc_have_poll);
  // Express the slot in millis() terms for the receiver's timing checks.
  const uint32_t now_unix = g_rtc.now().unixtime();
  const uint32_t now_ms = millis();
  const uint32_t slot_start_ms = now_ms + (slot_start > now_unix ? (slot_start - now_unix) * 1000 : 0);
  const uint32_t slot_end_ms = slot_start_ms + NODE_SLOT_S * 1000;
  const uint32_t listen_until = slot_start_ms + POLL_LISTEN_S * 1000;
  while (static_cast<int32_t>(listen_until - millis()) > 0) {
    uint8_t buf[16];
    int16_t rssi;
    float snr;
    const size_t n = g_radio.receive(0, buf, sizeof buf, listen_until - millis(), &rssi, &snr);
    if (!n) break;
    const PollResult pr = poll.handle(buf, n, millis(), g_rtc.now().unixtime(), slot_start_ms, slot_end_ms);
    if (pr.decision == POLL_NOT_ADDRESSED || pr.decision == POLL_MALFORMED) continue;
    if (pr.decision != POLL_ACCEPT) {
      Serial.printf("poll rejected: %s\n", poll_decision_name(pr.decision));
      continue;
    }
    rtc_last_poll_id = poll.last_poll_id();
    rtc_have_poll = true;
    if (pr.resync_rtc) g_rtc.adjust(DateTime(g_rtc.now().unixtime() + pr.clock_offset_s));
    while (static_cast<int32_t>(pr.response_start_ms - millis()) > 0) delay(1);
    transmit_reading(r, false);
    return;
  }
  ++rtc_missed_polls;
}

void handle_event_check(uint32_t now) {
  uint16_t raw = 0;
  const QuantityValue v = g_sensors.read_soil_moisture_only(&raw);
  if (!v.valid() || isnan(rtc_last_reported_vwc)) return;
  if (fabsf(v.value - rtc_last_reported_vwc) < EVENT_DELTA_VWC) return;
  if (now - rtc_last_event_at < EVENT_MIN_SPACING_S) return;
  // Never transmit inside another zone's window: only send if our own zone window is open
  // or no slot traffic is expected; otherwise the reading waits for the scheduled slot.
  const uint32_t in_cycle = now % CYCLE_S;
  if (in_cycle / ZONE_WINDOW_S != g_id.zone_index) return;
  Reading r{};
  take_reading(&r);
  if (!radio_up()) return;
  for (int attempt = 0; attempt < 5; ++attempt) {  // listen-before-talk with random backoff
    if (g_radio.channel_clear(0)) {
      transmit_reading(r, true);
      rtc_last_event_at = now;
      return;
    }
    delay(random(200, 2000));
  }
}

void sleep_until(uint32_t now, uint32_t wake_at) {
  g_radio.sleep();
  g_sensors.power_down();
  const uint64_t us = static_cast<uint64_t>(wake_at > now ? wake_at - now : 1) * 1000000ULL;
  esp_sleep_enable_timer_wakeup(us);
  Serial.flush();
  esp_deep_sleep_start();
}
}  // namespace

void setup() {
  Serial.begin(115200);
  esp_task_wdt_init(WDT_TIMEOUT_S, true);
  esp_task_wdt_add(nullptr);
  pinMode(PIN_LORA_RST, OUTPUT);
  digitalWrite(PIN_LORA_RST, HIGH);
  if (rtc_magic != RTC_MAGIC) cold_boot_state();

  g_id = load_identity();
  g_sensors.begin();
  const bool rtc_ok = g_rtc.begin();
  if (g_id.node_id == 0 || !rtc_ok) {
    // Unprovisioned or no clock: cannot find our slot. Retry in one cycle.
    Serial.printf("fatal: node_id=%u rtc=%d, sleeping %u s\n", g_id.node_id, rtc_ok, CYCLE_S);
    esp_sleep_enable_timer_wakeup(static_cast<uint64_t>(CYCLE_S) * 1000000ULL);
    esp_deep_sleep_start();
  }
  if (g_rtc.lostPower()) Serial.println("warning: DS3231 lost power, waiting for poll resync");

  const uint32_t now = g_rtc.now().unixtime();
  const uint32_t slot = next_slot_start(now);
  if (slot - now <= WAKE_LEAD_S + 1) {
    handle_slot(slot);
  } else {
    handle_event_check(now);
  }
  esp_task_wdt_reset();

  const uint32_t after = g_rtc.now().unixtime();
  const uint32_t next_slot = next_slot_start(after + 1);
  sleep_until(after, next_wake_at(after, next_slot, WAKE_LEAD_S, EVENT_CHECK_S));
}

void loop() {}  // never reached: every wake ends in deep sleep
