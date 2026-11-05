// LoRa CRT protocol: value scaling, residue generation, CRT reconstruction,
// frame codecs and airtime. Platform-independent: compiled into the firmware
// and into the host-side unit tests (firmware/esp32/tests, CI).
//
#pragma once
#include <stddef.h>
#include <stdint.h>

namespace agri {

constexpr uint8_t PROTOCOL_VERSION = 1;

enum FrameType : uint8_t { FRAME_RESIDUE = 0x1, FRAME_POLL = 0x2, FRAME_ACK = 0x3, FRAME_CHANNEL = 0x4 };

// Two framings of the same reading:
//   FRAME_MODE 15: one 8-byte residue frame per quantity per channel (5 x 3 = 15 frames), one ACK per quantity;
//   FRAME_MODE 3:  one 12-byte channel frame per channel carrying all five residues (3 frames), one ACK per reading.
// Both use the same moduli, scaling and channels; any two of the three channels reconstruct every quantity.
#ifndef FRAME_MODE
#define FRAME_MODE 15
#endif
static_assert(FRAME_MODE == 15 || FRAME_MODE == 3, "FRAME_MODE must be 15 or 3");

constexpr size_t RESIDUE_FRAME_LEN = 8;
constexpr size_t CHANNEL_FRAME_LEN = 12;
constexpr uint8_t RESIDUE_INVALID = 0xFF;  // channel frame: sensor failed (no residue is ever >= 103)
constexpr uint8_t ACK_WHOLE_READING = 7;   // ACK quantity code in FRAME_MODE 3
constexpr size_t POLL_FRAME_LEN = 10;
constexpr size_t ACK_FRAME_LEN = 8;

constexpr uint8_t NUM_QUANTITIES = 5;
constexpr uint8_t NUM_RESIDUES = 3;
constexpr uint8_t PACKETS_PER_READING = FRAME_MODE == 15 ? NUM_QUANTITIES * NUM_RESIDUES : NUM_RESIDUES;  // 15 or 3

// Pairwise-coprime moduli. Each residue fits one unsigned byte.
constexpr uint16_t MODULI[NUM_RESIDUES] = {97, 101, 103};
constexpr uint32_t MODULUS_PRODUCT = 97UL * 101UL * 103UL;  // 1,009,091
// Smallest product of two moduli. Every scaled value is kept below this, so any
// two of the three residues reconstruct it, and a full triplet that decodes to
// a value >= this bound reveals a corrupted residue.
constexpr uint16_t LEGITIMATE_RANGE = 97 * 101;  // 9,797

// Residue index i is always sent on channel i.
constexpr uint32_t CHANNEL_HZ[NUM_RESIDUES] = {868100000UL, 868300000UL, 868500000UL};

enum Quantity : uint8_t {
  Q_SOIL_MOISTURE = 0,  // % volumetric water content
  Q_TEMPERATURE = 1,    // degC
  Q_HUMIDITY = 2,       // % relative humidity
  Q_PH = 3,             // pH units
  Q_ILLUMINANCE = 4,    // lux
};

// scaled = round((value + offset) * factor), valid range 0..max_scaled
struct ScaleSpec {
  float offset;
  float factor;
  uint16_t max_scaled;
};
extern const ScaleSpec SCALE[NUM_QUANTITIES];

bool scale_value(Quantity q, float value, uint16_t* scaled);  // false if out of range
float unscale_value(Quantity q, uint16_t scaled);

void crt_residues(uint16_t scaled, uint8_t residues[NUM_RESIDUES]);

enum CrtStatus : uint8_t {
  CRT_OK = 0,
  CRT_INSUFFICIENT = 1,   // fewer than two residues received
  CRT_INCONSISTENT = 2,   // three residues decode outside the legitimate range: corruption detected
  CRT_BAD_RESIDUE = 3,    // a residue >= its modulus
  CRT_OUT_OF_RANGE = 4,   // decoded value above the quantity's max_scaled
};
// mask bit i set = residue i present. If all three are present the result is
// cross-checked; if exactly two are present the result is unique by construction.
CrtStatus crt_reconstruct(const uint8_t residues[NUM_RESIDUES], uint8_t mask, uint16_t* scaled);

uint16_t crc16_ccitt(const uint8_t* data, size_t len);  // CRC-16/CCITT-FALSE

struct ResidueFrame {
  uint8_t node_id;
  uint16_t seq;
  uint8_t quantity;
  uint8_t residue_index;
  bool valid;
  bool event;
  bool retx;
  uint8_t residue;
};
void encode_residue_frame(const ResidueFrame& f, uint8_t out[RESIDUE_FRAME_LEN]);
bool decode_residue_frame(const uint8_t* in, size_t len, ResidueFrame* f);

// FRAME_MODE 3: all five quantities' residues for one channel. valid_mask bit q = quantity q has a reading.
struct ChannelFrame {
  uint8_t node_id;
  uint16_t seq;
  uint8_t residue_index;  // = channel index
  bool event;
  bool retx;
  uint8_t valid_mask;     // derived from / encoded as RESIDUE_INVALID bytes
  uint8_t residues[NUM_QUANTITIES];
};
void encode_channel_frame(const ChannelFrame& f, uint8_t out[CHANNEL_FRAME_LEN]);
bool decode_channel_frame(const uint8_t* in, size_t len, ChannelFrame* f);

struct PollFrame {
  uint8_t node_id;
  uint16_t poll_id;
  uint32_t gateway_time;  // Unix seconds, UTC
};
void encode_poll_frame(const PollFrame& f, uint8_t out[POLL_FRAME_LEN]);
bool decode_poll_frame(const uint8_t* in, size_t len, PollFrame* f);

struct AckFrame {
  uint8_t node_id;
  uint16_t seq;
  uint8_t quantity;       // 0-4, or ACK_WHOLE_READING (7) in FRAME_MODE 3: received_mask then lists channels
  bool reconstructed;
  bool retx_request;
  uint8_t received_mask;  // bits 0..2: residues the gateway holds
  uint8_t crt_status;     // CrtStatus of the gateway's reconstruction attempt
};
void encode_ack_frame(const AckFrame& f, uint8_t out[ACK_FRAME_LEN]);
bool decode_ack_frame(const uint8_t* in, size_t len, AckFrame* f);

// Semtech SX127x time-on-air (AN1200.13), milliseconds.
double lora_airtime_ms(size_t payload_len, uint8_t sf = 9, uint32_t bw_hz = 125000, uint8_t cr = 1,
                       bool crc_on = true, bool implicit_header = false, bool low_dr_optimize = false,
                       uint16_t preamble_symbols = 8);

// RFC 1982 serial-number comparison for 16-bit counters: true if a is newer than b.
inline bool seq_newer(uint16_t a, uint16_t b) {
  return a != b && static_cast<uint16_t>(a - b) < 0x8000;
}

#ifdef ARDUINO
// RFM95W (SX1276) driver wrapper. Radio settings mirror config/radio.example.yaml.
struct RadioStats {
  uint32_t tx_frames = 0;
  uint32_t tx_failures = 0;
  uint32_t rx_frames = 0;
  uint32_t rx_crc_errors = 0;
  uint32_t reinit_count = 0;
};

class LoraRadio {
 public:
  bool begin();                       // SPI, reset, modem settings; false if the chip does not answer
  bool reinit();                      // hard reset + begin, used after a TX/RX fault
  bool send(uint8_t channel_index, const uint8_t* buf, size_t len);
  // Waits up to timeout_ms on the channel for one frame; returns its length (0 = none).
  size_t receive(uint8_t channel_index, uint8_t* buf, size_t cap, uint32_t timeout_ms, int16_t* rssi, float* snr);
  bool channel_clear(uint8_t channel_index);  // LoRa CAD, used before unsolicited (event) reports
  void sleep();
  const RadioStats& stats() const { return stats_; }

 private:
  bool configure(uint8_t channel_index);
  int8_t current_channel_ = -1;
  RadioStats stats_;
};
#endif

}  // namespace agri
