// Host-side unit tests for the platform-independent firmware code.
//   make -C firmware/esp32/tests   (see firmware/esp32/tests/Makefile)
#include <math.h>

#include <initializer_list>
#include <stdio.h>
#include <string.h>

#include <map>
#include <string>

#include "calibration.h"
#include "lora_protocol.h"
#include "mac.h"
#include "sensor_drivers.h"

using namespace agri;

static int failures = 0, checks = 0;
#define CHECK(cond)                                                     \
  do {                                                                  \
    ++checks;                                                           \
    if (!(cond)) {                                                      \
      ++failures;                                                       \
      fprintf(stderr, "FAIL %s:%d  %s\n", __FILE__, __LINE__, #cond);   \
    }                                                                   \
  } while (0)

static void test_worked_example() {
  uint16_t s = 0;
  CHECK(scale_value(Q_TEMPERATURE, 25.3f, &s));
  CHECK(s == 653);
  uint8_t r[3];
  crt_residues(s, r);
  CHECK(r[0] == 71 && r[1] == 47 && r[2] == 35);
  uint16_t back = 0;
  CHECK(crt_reconstruct(r, 0x07, &back) == CRT_OK && back == 653);
  CHECK(fabsf(unscale_value(Q_TEMPERATURE, back) - 25.3f) < 1e-4f);
}

static void test_all_values_all_pairs() {
  // Every legitimate scaled value reconstructs from any two residues and from all three.
  for (uint16_t v = 0; v < LEGITIMATE_RANGE; ++v) {
    uint8_t r[3];
    crt_residues(v, r);
    for (uint8_t mask : {0x07, 0x03, 0x05, 0x06}) {
      uint16_t out = 0xFFFF;
      if (crt_reconstruct(r, mask, &out) != CRT_OK || out != v) {
        CHECK(false);
        return;
      }
    }
  }
  CHECK(true);
}

static void test_insufficient_and_corruption() {
  uint8_t r[3];
  crt_residues(653, r);
  uint16_t out;
  CHECK(crt_reconstruct(r, 0x01, &out) == CRT_INSUFFICIENT);
  CHECK(crt_reconstruct(r, 0x00, &out) == CRT_INSUFFICIENT);
  uint8_t bad[3] = {r[0], static_cast<uint8_t>((r[1] + 1) % 101), r[2]};
  CHECK(crt_reconstruct(bad, 0x07, &out) == CRT_INCONSISTENT);  // single corrupted residue detected
  uint8_t over[3] = {97, 0, 0};
  CHECK(crt_reconstruct(over, 0x07, &out) == CRT_BAD_RESIDUE);
  // Every single-residue corruption of every legitimate value is detected.
  int undetected = 0;
  for (uint16_t v = 0; v < LEGITIMATE_RANGE; v += 7) {
    uint8_t rr[3];
    crt_residues(v, rr);
    for (int i = 0; i < 3; ++i) {
      uint8_t c[3] = {rr[0], rr[1], rr[2]};
      c[i] = static_cast<uint8_t>((c[i] + 1) % MODULI[i]);
      if (crt_reconstruct(c, 0x07, &out) == CRT_OK) ++undetected;
    }
  }
  CHECK(undetected == 0);
}

static void test_scaling_ranges() {
  uint16_t s;
  CHECK(scale_value(Q_SOIL_MOISTURE, 41.39f, &s) && s == 414);
  CHECK(scale_value(Q_HUMIDITY, 100.0f, &s) && s == 1000);
  CHECK(scale_value(Q_PH, 6.79f, &s) && s == 679);
  CHECK(scale_value(Q_ILLUMINANCE, 45150.0f, &s) && s == 9030);
  CHECK(scale_value(Q_TEMPERATURE, -40.0f, &s) && s == 0);
  CHECK(!scale_value(Q_TEMPERATURE, 90.0f, &s));
  CHECK(!scale_value(Q_ILLUMINANCE, 49000.0f, &s));
  CHECK(!scale_value(Q_PH, NAN, &s));
  for (int q = 0; q < NUM_QUANTITIES; ++q) CHECK(SCALE[q].max_scaled < LEGITIMATE_RANGE);
}

static void test_crc_and_frames() {
  const uint8_t check[] = {'1', '2', '3', '4', '5', '6', '7', '8', '9'};
  CHECK(crc16_ccitt(check, 9) == 0x29B1);  // CRC-16/CCITT-FALSE check value

  ResidueFrame f{17, 470, Q_TEMPERATURE, 1, true, false, false, 47};
  uint8_t b[RESIDUE_FRAME_LEN];
  encode_residue_frame(f, b);
  CHECK(b[0] == 0x11 && b[1] == 0x11 && b[2] == 0x01 && b[3] == 0xD6 && b[4] == 0x2C && b[5] == 0x2F);
  ResidueFrame g;
  CHECK(decode_residue_frame(b, sizeof b, &g));
  CHECK(g.node_id == 17 && g.seq == 470 && g.quantity == 1 && g.residue_index == 1 && g.valid && g.residue == 47);
  b[5] ^= 0x01;
  CHECK(!decode_residue_frame(b, sizeof b, &g));  // CRC catches a flipped bit
  CHECK(!decode_residue_frame(b, 7, &g));

  PollFrame p{17, 0x0102, 1894665600u}, p2;
  uint8_t pb[POLL_FRAME_LEN];
  encode_poll_frame(p, pb);
  CHECK(pb[0] == 0x21 && decode_poll_frame(pb, sizeof pb, &p2) && p2.gateway_time == 1894665600u && p2.poll_id == 0x0102);

  AckFrame a{17, 470, Q_PH, false, true, 0x05, CRT_INSUFFICIENT}, a2;
  uint8_t ab[ACK_FRAME_LEN];
  encode_ack_frame(a, ab);
  CHECK(ab[0] == 0x31 && decode_ack_frame(ab, sizeof ab, &a2));
  CHECK(a2.retx_request && !a2.reconstructed && a2.received_mask == 0x05 && a2.quantity == Q_PH);
}

static void test_airtime() {
  CHECK(fabs(lora_airtime_ms(8) - 123.904) < 1e-6);
  CHECK(fabs(lora_airtime_ms(10) - 144.384) < 1e-6);
  CHECK(fabs(lora_airtime_ms(13) - 164.864) < 1e-6);
  CHECK(fabs(lora_airtime_ms(34) - 246.784) < 1e-6);
#if FRAME_MODE == 3
  CHECK(PACKETS_PER_READING == 3);
  CHECK(fabs(PACKETS_PER_READING * lora_airtime_ms(CHANNEL_FRAME_LEN) - 433.152) < 1e-6);
#else
  CHECK(PACKETS_PER_READING == 15);
  CHECK(fabs(PACKETS_PER_READING * lora_airtime_ms(RESIDUE_FRAME_LEN) - 1858.56) < 1e-6);
#endif
}

static void test_poll_receiver() {
  PollConfig cfg;
  cfg.node_id = 17;
  PollReceiver rx(cfg);
  uint8_t b[POLL_FRAME_LEN];
  const uint32_t now_unix = 1894665600u, slot_start = 10000, slot_end = 44000;
  encode_poll_frame({17, 5, now_unix + 1}, b);
  PollResult r = rx.handle(b, sizeof b, 10500, now_unix, slot_start, slot_end);
  CHECK(r.decision == POLL_ACCEPT && !r.resync_rtc && r.response_start_ms == 10500 + cfg.turnaround_ms);
  CHECK(rx.handle(b, sizeof b, 10600, now_unix, slot_start, slot_end).decision == POLL_STALE_ID);  // replay
  encode_poll_frame({18, 6, now_unix}, b);
  CHECK(rx.handle(b, sizeof b, 10700, now_unix, slot_start, slot_end).decision == POLL_NOT_ADDRESSED);
  encode_poll_frame({17, 7, now_unix + 5000}, b);
  CHECK(rx.handle(b, sizeof b, 10800, now_unix, slot_start, slot_end).decision == POLL_STALE_TIME);
  encode_poll_frame({17, 8, now_unix + 30}, b);
  r = rx.handle(b, sizeof b, 10900, now_unix, slot_start, slot_end);
  CHECK(r.decision == POLL_ACCEPT && r.resync_rtc && r.clock_offset_s == 30);
  encode_poll_frame({17, 9, now_unix}, b);
  CHECK(rx.handle(b, sizeof b, slot_end - 1000, now_unix, slot_start, slot_end).decision == POLL_OUTSIDE_SLOT);
  b[3] ^= 0xFF;
  CHECK(rx.handle(b, sizeof b, 11000, now_unix, slot_start, slot_end).decision == POLL_MALFORMED);
  // Sequence wrap-around: 0x0001 is newer than 0xFFF0.
  PollReceiver wrap(cfg);
  wrap.restore(0xFFF0, true);
  encode_poll_frame({17, 0x0001, now_unix}, b);
  CHECK(wrap.handle(b, sizeof b, 10500, now_unix, slot_start, slot_end).decision == POLL_ACCEPT);
  CHECK(rx.response_duration_ms() < 34000);  // a full reading fits a 34 s slot
}

static void test_ack_tracker() {
  AckConfig cfg;
  AckTracker t(cfg);
  t.begin(17, 470, NUM_QUANTITIES);  // per-quantity tracking, whatever the build mode
  uint8_t b[ACK_FRAME_LEN], mask = 0;
  t.on_sent(0, 1000);
  encode_ack_frame({17, 470, 0, true, false, 0x07, CRT_OK}, b);
  CHECK(t.on_frame(b, sizeof b) == ACK_ACCEPTED);
  CHECK(t.on_frame(b, sizeof b) == ACK_DUPLICATE);
  encode_ack_frame({17, 469, 1, true, false, 0x07, CRT_OK}, b);
  CHECK(t.on_frame(b, sizeof b) == ACK_WRONG_SEQ);
  encode_ack_frame({16, 470, 1, true, false, 0x07, CRT_OK}, b);
  CHECK(t.on_frame(b, sizeof b) == ACK_WRONG_NODE);
  encode_ack_frame({17, 470, 2, true, false, 0x07, CRT_OK}, b);
  CHECK(t.on_frame(b, sizeof b) == ACK_UNEXPECTED);
  // Quantity 1: gateway got only residue 0 -> resend 1 and 2.
  t.on_sent(1, 2000);
  encode_ack_frame({17, 470, 1, false, true, 0x01, CRT_INSUFFICIENT}, b);
  CHECK(t.on_frame(b, sizeof b) == ACK_RETX_REQUESTED);
  CHECK(t.due_for_retx(1, 2100, &mask) && mask == 0x06);
  t.on_retx(1, 2100);
  // Quantity 2: no ACK at all -> timeout -> resend all three, until retries run out.
  t.on_sent(2, 3000);
  CHECK(!t.due_for_retx(2, 3100, &mask));
  CHECK(t.due_for_retx(2, 3000 + cfg.ack_timeout_ms, &mask) && mask == 0x07);
  t.on_retx(2, 3500);
  CHECK(t.due_for_retx(2, 3500 + cfg.ack_timeout_ms, &mask));
  t.on_retx(2, 4000);
  CHECK(!t.due_for_retx(2, 4000 + cfg.ack_timeout_ms, &mask));
  CHECK(t.track(2).state == QS_FAILED && t.total_retries() == 3);
  // Quantity 3: gateway abandons explicitly.
  t.on_sent(3, 5000);
  encode_ack_frame({17, 470, 3, false, false, 0x01, CRT_INSUFFICIENT}, b);
  CHECK(t.on_frame(b, sizeof b) == ACK_ABANDONED && t.track(3).state == QS_FAILED);
  CHECK(!t.finished());
}

// FRAME_MODE 3: one 12-byte channel frame per channel; fixed vectors below.
static void test_channel_frames() {
  const float vals[NUM_QUANTITIES] = {41.4f, 25.3f, 64.3f, 6.82f, 1500.0f};
  uint8_t res[NUM_QUANTITIES][NUM_RESIDUES];
  for (uint8_t q = 0; q < NUM_QUANTITIES; ++q) {
    uint16_t s = 0;
    CHECK(scale_value(static_cast<Quantity>(q), vals[q], &s));
    crt_residues(s, res[q]);
  }
  const uint8_t want[3][CHANNEL_FRAME_LEN] = {
      {0x41, 0x11, 0x01, 0xD6, 0x00, 0x1A, 0x47, 0x3D, 0x03, 0x09, 0xDD, 0xE8},
      {0x41, 0x11, 0x01, 0xD6, 0x40, 0x0A, 0x2F, 0x25, 0x4C, 0x62, 0x45, 0x4E},
      {0x41, 0x11, 0x01, 0xD6, 0x80, 0x02, 0x23, 0x19, 0x40, 0x5E, 0xB4, 0x17}};
  ChannelFrame f{17, 470, 0, false, false, 0x1F, {0, 0, 0, 0, 0}};
  uint8_t got[NUM_RESIDUES][NUM_QUANTITIES];  // residues recovered per channel
  for (uint8_t i = 0; i < NUM_RESIDUES; ++i) {
    f.residue_index = i;
    for (uint8_t q = 0; q < NUM_QUANTITIES; ++q) f.residues[q] = res[q][i];
    uint8_t b[CHANNEL_FRAME_LEN];
    encode_channel_frame(f, b);
    CHECK(memcmp(b, want[i], sizeof b) == 0);
    ChannelFrame d;
    CHECK(decode_channel_frame(b, sizeof b, &d) && d.residue_index == i && d.valid_mask == 0x1F);
    for (uint8_t q = 0; q < NUM_QUANTITIES; ++q) got[i][q] = d.residues[q];
  }
  // Losing any one channel frame still reconstructs all five quantities from the other two.
  for (uint8_t lost = 0; lost < NUM_RESIDUES; ++lost) {
    const uint8_t mask = static_cast<uint8_t>(0x07 & ~(1u << lost));
    for (uint8_t q = 0; q < NUM_QUANTITIES; ++q) {
      uint8_t r[3] = {got[0][q], got[1][q], got[2][q]};
      uint16_t v = 0, s = 0;
      scale_value(static_cast<Quantity>(q), vals[q], &s);
      CHECK(crt_reconstruct(r, mask, &v) == CRT_OK && v == s);
    }
  }
  // Failed pH sensor -> 0xFF sentinel, decoded as invalid.
  f.residue_index = 0;
  f.valid_mask = 0x17;
  for (uint8_t q = 0; q < NUM_QUANTITIES; ++q) f.residues[q] = res[q][0];
  uint8_t b[CHANNEL_FRAME_LEN];
  encode_channel_frame(f, b);
  const uint8_t ph_failed[CHANNEL_FRAME_LEN] = {0x41, 0x11, 0x01, 0xD6, 0x00, 0x1A, 0x47, 0x3D, 0xFF, 0x09, 0x8B, 0x44};
  CHECK(memcmp(b, ph_failed, sizeof b) == 0);
  ChannelFrame d;
  CHECK(decode_channel_frame(b, sizeof b, &d) && d.valid_mask == 0x17);
  // A residue >= its channel's modulus is rejected even with a valid CRC.
  uint8_t bad[CHANNEL_FRAME_LEN] = {0x41, 0x11, 0x01, 0xD6, 0x80, 0x67, 0x23, 0x19, 0x40, 0x5E, 0, 0};
  const uint16_t c = crc16_ccitt(bad, 10);
  bad[10] = static_cast<uint8_t>(c >> 8);
  bad[11] = static_cast<uint8_t>(c & 0xFF);
  CHECK(!decode_channel_frame(bad, sizeof bad, &d));  // 0x67 = 103 on channel 2 (modulus 103)
  CHECK(fabs(lora_airtime_ms(CHANNEL_FRAME_LEN) - 144.384) < 1e-6);
}

// FRAME_MODE 3 ACK handling: one whole-reading ACK whose mask lists channels.
static void test_ack_tracker_whole_reading() {
  AckConfig cfg;
  AckTracker t(cfg);
  t.begin(17, 470, 1);
  CHECK(t.units() == 1);
  t.on_sent(0, 1000);
  uint8_t b[ACK_FRAME_LEN];
  encode_ack_frame({17, 470, 1, true, false, 0x07, CRT_OK}, b);
  CHECK(t.on_frame(b, sizeof b) == ACK_UNEXPECTED);  // per-quantity ACK is not valid in this mode
  encode_ack_frame({17, 470, ACK_WHOLE_READING, false, true, 0x01, CRT_INSUFFICIENT}, b);
  const uint8_t want_retx[ACK_FRAME_LEN] = {0x31, 0x11, 0x01, 0xD6, 0xE9, 0x01, 0xD1, 0xBC};
  CHECK(memcmp(b, want_retx, sizeof b) == 0);
  CHECK(t.on_frame(b, sizeof b) == ACK_RETX_REQUESTED);
  uint8_t mask = 0;
  CHECK(t.due_for_retx(0, 1100, &mask) && mask == 0x06);  // resend channels 1 and 2
  t.on_retx(0, 1100);
  encode_ack_frame({17, 470, ACK_WHOLE_READING, true, false, 0x07, CRT_OK}, b);
  const uint8_t want_ok[ACK_FRAME_LEN] = {0x31, 0x11, 0x01, 0xD6, 0xF7, 0x00, 0xE1, 0xE1};
  CHECK(memcmp(b, want_ok, sizeof b) == 0);
  CHECK(t.on_frame(b, sizeof b) == ACK_ACCEPTED && t.finished() && t.acked_count() == 1);
}

// ---------------------------------------------------------------- calibration record loader
// In-memory stand-in for NVS: each key has a type tag, like Preferences::getType().
struct MapSource : CalibrationSource {
  enum T { U8, U16, U32, F32, F64 };
  std::map<std::string, std::pair<T, double>> kv;
  bool has(const char* k) const override { return kv.count(k); }
  bool get(const char* k, T t, double* v) const {
    auto it = kv.find(k);
    if (it == kv.end() || it->second.first != t) return false;
    *v = it->second.second;
    return true;
  }
  bool get_u32(const char* k, uint32_t* v) const override { double d; return get(k, U32, &d) && (*v = d, true); }
  bool get_u16(const char* k, uint16_t* v) const override { double d; return get(k, U16, &d) && (*v = d, true); }
  bool get_u8(const char* k, uint8_t* v) const override { double d; return get(k, U8, &d) && (*v = d, true); }
  bool get_f32(const char* k, float* v) const override { double d; return get(k, F32, &d) && (*v = d, true); }
};

// Illustrative coefficients, authored for this test (not a field sensor's calibration record).
// They satisfy every CAL_LIMIT_* bound: VWC(2550 mV) = 2.3 %, VWC(1400 mV) = 82.4 %, fit decreasing.
static MapSource good_record() {
  MapSource m;
  m.kv = {{"version", {MapSource::U32, CALIBRATION_VERSION}}, {"soil_a0", {MapSource::F32, 230.0}},
          {"soil_a1", {MapSource::F32, -0.125}}, {"soil_a2", {MapSource::F32, 1.4e-05}},
          {"soil_dry", {MapSource::U16, 2550}}, {"soil_wet", {MapSource::U16, 1400}},
          {"ph_e7", {MapSource::F32, 1500.0}}, {"ph_slope", {MapSource::F32, -162.0}},
          {"rh_gain", {MapSource::F32, 1.01}}, {"rh_off", {MapSource::F32, 0.5}},
          {"t_gain", {MapSource::F32, 1.0}}, {"t_off", {MapSource::F32, 0.0}},
          {"mtreg", {MapSource::U8, 69}}, {"lux_f", {MapSource::F32, 1.0}}};
  return m;
}

static bool is_default(const Calibration& c) {
  const Calibration d = default_calibration();
  return !c.loaded && c.soil_a0 == d.soil_a0 && c.ph_slope_mv == d.ph_slope_mv && c.rh_gain == d.rh_gain &&
         c.lux_factor == d.lux_factor && c.soil_dry_mv == d.soil_dry_mv;
}

static void test_calibration_loader() {
  Calibration c;
  const char* key = nullptr;
  CHECK(read_calibration(good_record(), &c, &key) == CAL_OK && c.loaded && key == nullptr);
  CHECK(fabsf(c.ph_slope_mv + 162.0f) < 1e-3f && c.soil_dry_mv == 2550 && c.bh1750_mtreg == 69);
  CHECK(read_calibration(MapSource{}, &c, &key) == CAL_NO_RECORD && is_default(c));

  // Every key, removed one at a time: the record is rejected, naming the key, with no stored value kept.
  for (const auto& kv : good_record().kv) {
    if (kv.first == "version") continue;
    MapSource m = good_record();
    m.kv.erase(kv.first);
    CHECK(read_calibration(m, &c, &key) == CAL_MISSING_KEY && key && kv.first == key && is_default(c));
  }
  // Right key, wrong type: a u16 reference written as a float, a float written as a double-size blob.
  MapSource m = good_record();
  m.kv["soil_dry"].first = MapSource::F32;
  CHECK(read_calibration(m, &c, &key) == CAL_WRONG_TYPE && std::string(key) == "soil_dry" && is_default(c));
  m = good_record();
  m.kv["ph_e7"].first = MapSource::F64;
  CHECK(read_calibration(m, &c, &key) == CAL_WRONG_TYPE && is_default(c));
  m = good_record();
  m.kv["version"].second = CALIBRATION_VERSION + 1;
  CHECK(read_calibration(m, &c, &key) == CAL_VERSION_MISMATCH && is_default(c));

  // Present and typed, but not plausible.
  struct Bad { const char* key; double value; CalStatus want; } bad[] = {
      {"soil_a0", NAN, CAL_NOT_FINITE},
      {"lux_f", INFINITY, CAL_NOT_FINITE},
      {"soil_wet", 2500, CAL_SOIL_INVALID},     // span dry - wet only 50 mV
      {"soil_dry", 3300, CAL_SOIL_INVALID},     // above the usable ADC range
      {"soil_a0", 34.0, CAL_SOIL_INVALID},      // fit gives -194 %VWC at the dry reference
      {"soil_a2", 6e-5, CAL_SOIL_INVALID},      // fit turns upward inside the span (not monotone)
      {"ph_slope", 165.0, CAL_PH_INVALID},      // wrong sign: electrode or amplifier reversed
      {"ph_slope", -60.0, CAL_PH_INVALID},      // dead electrode
      {"ph_e7", 2900, CAL_PH_INVALID},
      {"rh_gain", 1.5, CAL_SHT31_INVALID},
      {"t_off", -8.0, CAL_SHT31_INVALID},
      {"mtreg", 10, CAL_BH1750_INVALID},
      {"lux_f", 0.0, CAL_BH1750_INVALID},
  };
  for (const Bad& b : bad) {
    m = good_record();
    m.kv[b.key].second = b.value;
    const CalStatus st = read_calibration(m, &c, &key);
    if (st != b.want || !is_default(c)) fprintf(stderr, "  calibration case %s=%g -> %s\n", b.key, b.value, cal_status_name(st));
    CHECK(st == b.want && is_default(c));
  }
  // Defaults are never an accepted calibration: the analog channels stay NOT_CALIBRATED on them.
  CHECK(!default_calibration().loaded);
}


// ---------------------------------------------------------------- channels and sequence numbers
static void test_channels_and_sequence() {
  // Residue index i always goes out on channel i: three distinct EU868 g1 channels.
  CHECK(CHANNEL_HZ[0] == 868100000UL && CHANNEL_HZ[1] == 868300000UL && CHANNEL_HZ[2] == 868500000UL);
  CHECK(NUM_RESIDUES == 3 && MODULI[0] == 97 && MODULI[1] == 101 && MODULI[2] == 103);
  CHECK(MODULUS_PRODUCT == 1009091UL && LEGITIMATE_RANGE == 9797);
  // RFC 1982 comparison, including wrap-around of the 16-bit counter.
  CHECK(seq_newer(2, 1) && !seq_newer(1, 2) && !seq_newer(5, 5));
  CHECK(seq_newer(0x0000, 0xFFFF) && seq_newer(0x0010, 0xFFF0) && !seq_newer(0xFFF0, 0x0010));
  // Checkpoint every 16 readings; after a power loss the node resumes 16 past the checkpoint.
  CHECK(sequence_checkpoint_due(32, 16) && !sequence_checkpoint_due(33, 16));
  CHECK(resume_sequence(48, 16) == 64);
  CHECK(resume_sequence(0xFFF8, 16) == 0x0008);  // wraps like the on-air counter
  // Every number a node could have sent since checkpoint c (c+1 .. c+15) is below the resume value.
  for (uint16_t c = 0; c < 64; c += 16)
    for (uint16_t used = c + 1; used < c + 16; ++used) CHECK(seq_newer(resume_sequence(c, 16), used));
}

// ---------------------------------------------------------------- sleep / wake schedule
static void test_slot_schedule_and_wake() {
  const SlotSchedule s;  // 1800 s cycle, 450 s zone windows, 34 s slots
  const uint32_t cycle = 1894665600u;  // 2030-01-15T00:00:00Z, cycle-aligned (illustrative)
  CHECK(cycle % s.cycle_s == 0);
  // zone 0 position 0 starts at the cycle; zone 2 position 3 at 900 + 3 x 34 s.
  CHECK(current_or_next_slot_start(cycle, 0, 0, s) == cycle);
  CHECK(current_or_next_slot_start(cycle, 2, 3, s) == cycle + 900 + 102);
  // Inside the slot: the current slot. After it: the next cycle's slot.
  CHECK(current_or_next_slot_start(cycle + 900 + 102 + 20, 2, 3, s) == cycle + 900 + 102);
  CHECK(current_or_next_slot_start(cycle + 900 + 102 + 34, 2, 3, s) == cycle + 1800 + 900 + 102);
  // Every slot of a 13-node zone ends inside its 450 s window.
  for (uint8_t z = 0; z < 4; ++z)
    for (uint8_t pos = 0; pos < 13; ++pos) {
      const uint32_t st = current_or_next_slot_start(cycle, z, pos, s);
      CHECK(st >= cycle + z * s.zone_window_s && st + s.node_slot_s <= cycle + (z + 1) * s.zone_window_s);
    }
  // Wake: the earlier of the 300 s event check and 3 s before the next slot.
  CHECK(next_wake_at(cycle, cycle + 1000, 3, 300) == cycle + 300);
  CHECK(next_wake_at(cycle + 800, cycle + 1000, 3, 300) == cycle + 997);
}

// ---------------------------------------------------------------- sensor fault classification
static void test_sensor_fault_classification() {
  CHECK(analog_sample_status(1800.0f, 1790, 1810, true) == SENSOR_OK);
  CHECK(analog_sample_status(20.0f, 10, 30, true) == SENSOR_DISCONNECTED);      // floating input
  CHECK(analog_sample_status(3200.0f, 3190, 3210, true) == SENSOR_DISCONNECTED);  // shorted / saturated
  CHECK(analog_sample_status(1800.0f, 1700, 1900, true) == SENSOR_UNSTABLE);      // 200 mV spread > 120 mV
  CHECK(analog_sample_status(1800.0f, 1790, 1810, false) == SENSOR_NOT_CALIBRATED);
  // Rail and spread faults are reported before a missing calibration.
  CHECK(analog_sample_status(20.0f, 10, 30, false) == SENSOR_DISCONNECTED);
  Calibration c = default_calibration();  // dry 2600 mV, wet 1200 mV, span 1400 mV
  CHECK(soil_probe_in_span(2600.0f, c) && soil_probe_in_span(1200.0f, c));
  CHECK(soil_probe_in_span(2740.0f, c) && !soil_probe_in_span(2741.0f, c));  // 10 % of span = 140 mV
  CHECK(soil_probe_in_span(1060.0f, c) && !soil_probe_in_span(1059.0f, c));
  CHECK(ds18b20_reading_valid(25.0625f) && ds18b20_reading_valid(-55.0f) && ds18b20_reading_valid(125.0f));
  CHECK(!ds18b20_reading_valid(-127.0f) && !ds18b20_reading_valid(NAN) && !ds18b20_reading_valid(126.0f));
  // Range checks applied after conversion (calibration.h): pH electrode linear range, temperature plausibility.
  CHECK(within(6.5f, RANGE_PH) && !within(2.9f, RANGE_PH) && !within(10.1f, RANGE_PH));
  CHECK(within(-40.0f, RANGE_TEMPERATURE) && !within(85.1f, RANGE_TEMPERATURE));
  // Conversions: Nernst pH at 25 degC, temperature compensation at 35 degC, BH1750 count -> lux.
  CHECK(fabsf(ph_from_mv(c, 1500.0f, 25.0f) - 7.0f) < 1e-5f);
  CHECK(fabsf(ph_from_mv(c, 1335.0f, 25.0f) - 8.0f) < 1e-4f);
  CHECK(fabsf(ph_from_mv(c, 1335.0f, 35.0f) - (7.0f + 165.0f / (165.0f * 308.15f / 298.15f))) < 1e-4f);
  CHECK(fabsf(lux_from_count(c, 1200) - 1000.0f) < 1e-3f);
  CHECK(fabsf(soil_moisture_from_mv(c, 2000.0f) - (185.7f - 0.0714f * 2000.0f)) < 1e-3f);
}

int main() {
  test_worked_example();
  test_all_values_all_pairs();
  test_insufficient_and_corruption();
  test_scaling_ranges();
  test_crc_and_frames();
  test_airtime();
  test_poll_receiver();
  test_ack_tracker();
  test_channel_frames();
  test_ack_tracker_whole_reading();
  test_calibration_loader();
  test_channels_and_sequence();
  test_slot_schedule_and_wake();
  test_sensor_fault_classification();
  printf("%d checks, %d failures\n", checks, failures);
  return failures ? 1 : 0;
}
