// CRT scaling, residue generation, frame construction and LoRa transmission.
//
// One reading = five quantities x three residues = fifteen 8-byte frames.
// Residue index i always goes out on CHANNEL_HZ[i] (868.1 / 868.3 / 868.5 MHz),
// SF9, 125 kHz, CR 4/5, explicit header, CRC on: 123.904 ms on air per frame.
//
// Worked example (also a unit-test vector):
//   temperature 25.3 degC -> scaled (25.3 + 40) x 10 = 653
//   653 mod 97 = 71, 653 mod 101 = 47, 653 mod 103 = 35  -> residues 71, 47, 35
//
// Frames carry no node-side signature. Integrity is the LoRa PHY CRC plus the
// frame CRC-16 plus the CRT cross-check; authenticity is established when the
// gateway signs the reconstructed reading into a Fabric proposal.
#include "lora_protocol.h"

#include <math.h>

namespace agri {

// Every max_scaled is below LEGITIMATE_RANGE (9,797) so two residues suffice.
const ScaleSpec SCALE[NUM_QUANTITIES] = {
    {0.0f, 10.0f, 1000},   // soil moisture 0.0..100.0 %VWC, 0.1 % resolution
    {40.0f, 10.0f, 1250},  // temperature  -40.0..85.0 degC, 0.1 degC
    {0.0f, 10.0f, 1000},   // humidity     0.0..100.0 %RH, 0.1 %
    {0.0f, 100.0f, 1400},  // pH           0.00..14.00, 0.01
    {0.0f, 0.2f, 9796},    // illuminance  0..48,980 lx, 5 lx resolution
};

bool scale_value(Quantity q, float value, uint16_t* scaled) {
  if (q >= NUM_QUANTITIES || isnan(value)) return false;
  const ScaleSpec& s = SCALE[q];
  const long v = lroundf((value + s.offset) * s.factor);
  if (v < 0 || v > s.max_scaled) return false;
  *scaled = static_cast<uint16_t>(v);
  return true;
}

float unscale_value(Quantity q, uint16_t scaled) {
  const ScaleSpec& s = SCALE[q];
  return static_cast<float>(scaled) / s.factor - s.offset;
}

void crt_residues(uint16_t scaled, uint8_t residues[NUM_RESIDUES]) {
  for (uint8_t i = 0; i < NUM_RESIDUES; ++i) residues[i] = static_cast<uint8_t>(scaled % MODULI[i]);
}

static uint32_t mod_inverse(uint32_t a, uint32_t m) {  // m prime here, small: extended Euclid
  int64_t t = 0, new_t = 1, r = m, new_r = a % m;
  while (new_r != 0) {
    const int64_t q = r / new_r;
    int64_t tmp = t - q * new_t;
    t = new_t;
    new_t = tmp;
    tmp = r - q * new_r;
    r = new_r;
    new_r = tmp;
  }
  if (t < 0) t += m;
  return static_cast<uint32_t>(t);
}

CrtStatus crt_reconstruct(const uint8_t residues[NUM_RESIDUES], uint8_t mask, uint16_t* scaled) {
  uint8_t idx[NUM_RESIDUES];
  uint8_t n = 0;
  for (uint8_t i = 0; i < NUM_RESIDUES; ++i) {
    if (mask & (1u << i)) {
      if (residues[i] >= MODULI[i]) return CRT_BAD_RESIDUE;
      idx[n++] = i;
    }
  }
  if (n < 2) return CRT_INSUFFICIENT;
  // Garner's mixed-radix reconstruction over the present moduli.
  uint64_t x = residues[idx[0]];
  uint64_t m = MODULI[idx[0]];
  for (uint8_t k = 1; k < n; ++k) {
    const uint32_t mk = MODULI[idx[k]];
    const uint32_t xm = static_cast<uint32_t>(x % mk);
    const uint32_t diff = (residues[idx[k]] + mk - xm) % mk;
    const uint32_t t = static_cast<uint32_t>((static_cast<uint64_t>(diff) * mod_inverse(static_cast<uint32_t>(m % mk), mk)) % mk);
    x += m * t;
    m *= mk;
  }
  if (x >= LEGITIMATE_RANGE) return n == 3 ? CRT_INCONSISTENT : CRT_OUT_OF_RANGE;
  *scaled = static_cast<uint16_t>(x);
  return CRT_OK;
}

uint16_t crc16_ccitt(const uint8_t* data, size_t len) {
  uint16_t crc = 0xFFFF;
  for (size_t i = 0; i < len; ++i) {
    crc ^= static_cast<uint16_t>(data[i]) << 8;
    for (int b = 0; b < 8; ++b) crc = (crc & 0x8000) ? static_cast<uint16_t>((crc << 1) ^ 0x1021) : static_cast<uint16_t>(crc << 1);
  }
  return crc;
}

static void put_crc(uint8_t* buf, size_t body_len) {
  const uint16_t c = crc16_ccitt(buf, body_len);
  buf[body_len] = static_cast<uint8_t>(c >> 8);
  buf[body_len + 1] = static_cast<uint8_t>(c & 0xFF);
}

static bool check_header(const uint8_t* in, size_t len, size_t want, FrameType type) {
  if (len != want) return false;
  if ((in[0] >> 4) != type || (in[0] & 0x0F) != PROTOCOL_VERSION) return false;
  const uint16_t c = crc16_ccitt(in, want - 2);
  return in[want - 2] == (c >> 8) && in[want - 1] == (c & 0xFF);
}

void encode_residue_frame(const ResidueFrame& f, uint8_t out[RESIDUE_FRAME_LEN]) {
  out[0] = static_cast<uint8_t>((FRAME_RESIDUE << 4) | PROTOCOL_VERSION);
  out[1] = f.node_id;
  out[2] = static_cast<uint8_t>(f.seq >> 8);
  out[3] = static_cast<uint8_t>(f.seq & 0xFF);
  out[4] = static_cast<uint8_t>(((f.quantity & 0x07) << 5) | ((f.residue_index & 0x03) << 3) | (f.valid ? 0x04 : 0) |
                                (f.event ? 0x02 : 0) | (f.retx ? 0x01 : 0));
  out[5] = f.residue;
  put_crc(out, 6);
}

bool decode_residue_frame(const uint8_t* in, size_t len, ResidueFrame* f) {
  if (!check_header(in, len, RESIDUE_FRAME_LEN, FRAME_RESIDUE)) return false;
  f->node_id = in[1];
  f->seq = static_cast<uint16_t>((in[2] << 8) | in[3]);
  f->quantity = in[4] >> 5;
  f->residue_index = (in[4] >> 3) & 0x03;
  f->valid = in[4] & 0x04;
  f->event = in[4] & 0x02;
  f->retx = in[4] & 0x01;
  f->residue = in[5];
  if (f->node_id == 0 || f->node_id == 0xFF) return false;
  if (f->quantity >= NUM_QUANTITIES || f->residue_index >= NUM_RESIDUES) return false;
  if (f->residue >= MODULI[f->residue_index]) return false;
  return true;
}

void encode_channel_frame(const ChannelFrame& f, uint8_t out[CHANNEL_FRAME_LEN]) {
  out[0] = static_cast<uint8_t>((FRAME_CHANNEL << 4) | PROTOCOL_VERSION);
  out[1] = f.node_id;
  out[2] = static_cast<uint8_t>(f.seq >> 8);
  out[3] = static_cast<uint8_t>(f.seq & 0xFF);
  out[4] = static_cast<uint8_t>(((f.residue_index & 0x03) << 6) | (f.event ? 0x20 : 0) | (f.retx ? 0x10 : 0));
  for (uint8_t q = 0; q < NUM_QUANTITIES; ++q)
    out[5 + q] = (f.valid_mask & (1u << q)) ? f.residues[q] : RESIDUE_INVALID;
  put_crc(out, 10);
}

bool decode_channel_frame(const uint8_t* in, size_t len, ChannelFrame* f) {
  if (!check_header(in, len, CHANNEL_FRAME_LEN, FRAME_CHANNEL)) return false;
  f->node_id = in[1];
  f->seq = static_cast<uint16_t>((in[2] << 8) | in[3]);
  f->residue_index = in[4] >> 6;
  f->event = in[4] & 0x20;
  f->retx = in[4] & 0x10;
  if (in[4] & 0x0F) return false;  // reserved bits
  if (f->node_id == 0 || f->node_id == 0xFF || f->residue_index >= NUM_RESIDUES) return false;
  f->valid_mask = 0;
  for (uint8_t q = 0; q < NUM_QUANTITIES; ++q) {
    const uint8_t r = in[5 + q];
    if (r == RESIDUE_INVALID) {
      f->residues[q] = 0;
      continue;
    }
    if (r >= MODULI[f->residue_index]) return false;
    f->residues[q] = r;
    f->valid_mask |= static_cast<uint8_t>(1u << q);
  }
  return true;
}

void encode_poll_frame(const PollFrame& f, uint8_t out[POLL_FRAME_LEN]) {
  out[0] = static_cast<uint8_t>((FRAME_POLL << 4) | PROTOCOL_VERSION);
  out[1] = f.node_id;
  out[2] = static_cast<uint8_t>(f.poll_id >> 8);
  out[3] = static_cast<uint8_t>(f.poll_id & 0xFF);
  for (int i = 0; i < 4; ++i) out[4 + i] = static_cast<uint8_t>(f.gateway_time >> (24 - 8 * i));
  put_crc(out, 8);
}

bool decode_poll_frame(const uint8_t* in, size_t len, PollFrame* f) {
  if (!check_header(in, len, POLL_FRAME_LEN, FRAME_POLL)) return false;
  f->node_id = in[1];
  f->poll_id = static_cast<uint16_t>((in[2] << 8) | in[3]);
  f->gateway_time = (static_cast<uint32_t>(in[4]) << 24) | (static_cast<uint32_t>(in[5]) << 16) |
                    (static_cast<uint32_t>(in[6]) << 8) | in[7];
  return f->node_id != 0;
}

void encode_ack_frame(const AckFrame& f, uint8_t out[ACK_FRAME_LEN]) {
  out[0] = static_cast<uint8_t>((FRAME_ACK << 4) | PROTOCOL_VERSION);
  out[1] = f.node_id;
  out[2] = static_cast<uint8_t>(f.seq >> 8);
  out[3] = static_cast<uint8_t>(f.seq & 0xFF);
  out[4] = static_cast<uint8_t>(((f.quantity & 0x07) << 5) | (f.reconstructed ? 0x10 : 0) | (f.retx_request ? 0x08 : 0) |
                                (f.received_mask & 0x07));
  out[5] = f.crt_status;
  put_crc(out, 6);
}

bool decode_ack_frame(const uint8_t* in, size_t len, AckFrame* f) {
  if (!check_header(in, len, ACK_FRAME_LEN, FRAME_ACK)) return false;
  f->node_id = in[1];
  f->seq = static_cast<uint16_t>((in[2] << 8) | in[3]);
  f->quantity = in[4] >> 5;
  f->reconstructed = in[4] & 0x10;
  f->retx_request = in[4] & 0x08;
  f->received_mask = in[4] & 0x07;
  f->crt_status = in[5];
  return (f->quantity < NUM_QUANTITIES || f->quantity == ACK_WHOLE_READING) && f->node_id != 0;
}

double lora_airtime_ms(size_t payload_len, uint8_t sf, uint32_t bw_hz, uint8_t cr, bool crc_on, bool implicit_header,
                       bool low_dr_optimize, uint16_t preamble_symbols) {
  const double t_sym = static_cast<double>(1UL << sf) / bw_hz * 1000.0;
  const double num = 8.0 * payload_len - 4.0 * sf + 28 + (crc_on ? 16 : 0) - (implicit_header ? 20 : 0);
  const double den = 4.0 * (sf - (low_dr_optimize ? 2 : 0));
  double n_payload = ceil(num / den) * (cr + 4);
  if (n_payload < 0) n_payload = 0;
  n_payload += 8;
  return (preamble_symbols + 4.25 + n_payload) * t_sym;
}

}  // namespace agri

// ---------------------------------------------------------------------------
#ifdef ARDUINO
#include <Arduino.h>
#include <LoRa.h>
#include <SPI.h>

#include "sensor_drivers.h"

namespace agri {

namespace {
constexpr long SF = 9;
constexpr long BW_HZ = 125000;
constexpr int CR_DENOM = 5;            // 4/5
constexpr int TX_POWER_DBM = 10;       // PA_BOOST, RegPaConfig 0xF8
constexpr int SYNC_WORD = 0x12;        // private network
constexpr long PREAMBLE = 8;
}  // namespace

bool LoraRadio::begin() {
  SPI.begin(PIN_LORA_SCK, PIN_LORA_MISO, PIN_LORA_MOSI, PIN_LORA_NSS);
  LoRa.setPins(PIN_LORA_NSS, PIN_LORA_RST, PIN_LORA_DIO0);
  current_channel_ = -1;
  if (!LoRa.begin(CHANNEL_HZ[0])) return false;
  LoRa.setSpreadingFactor(SF);
  LoRa.setSignalBandwidth(BW_HZ);
  LoRa.setCodingRate4(CR_DENOM);
  LoRa.setPreambleLength(PREAMBLE);
  LoRa.setSyncWord(SYNC_WORD);
  LoRa.setTxPower(TX_POWER_DBM, PA_OUTPUT_PA_BOOST_PIN);
  LoRa.enableCrc();
  current_channel_ = 0;
  return true;
}

bool LoraRadio::reinit() {
  ++stats_.reinit_count;
  LoRa.end();
  digitalWrite(PIN_LORA_RST, LOW);
  delay(2);
  digitalWrite(PIN_LORA_RST, HIGH);
  delay(10);
  return begin();
}

bool LoraRadio::configure(uint8_t channel_index) {
  if (channel_index >= NUM_RESIDUES) return false;
  if (current_channel_ != channel_index) {
    LoRa.idle();
    LoRa.setFrequency(CHANNEL_HZ[channel_index]);
    current_channel_ = channel_index;
  }
  return true;
}

bool LoraRadio::send(uint8_t channel_index, const uint8_t* buf, size_t len) {
  if (!configure(channel_index)) return false;
  if (!LoRa.beginPacket()) {  // radio still busy transmitting: treat as fault
    ++stats_.tx_failures;
    return false;
  }
  LoRa.write(buf, len);
  const uint32_t t0 = millis();
  const bool ok = LoRa.endPacket(false) == 1;  // blocking until TxDone
  // TxDone must arrive within ~2x the computed airtime, otherwise the radio is hung.
  if (!ok || millis() - t0 > 2 * lora_airtime_ms(len) + 50) {
    ++stats_.tx_failures;
    return false;
  }
  ++stats_.tx_frames;
  return true;
}

size_t LoraRadio::receive(uint8_t channel_index, uint8_t* buf, size_t cap, uint32_t timeout_ms, int16_t* rssi,
                          float* snr) {
  if (!configure(channel_index)) return 0;
  LoRa.receive();
  const uint32_t t0 = millis();
  while (millis() - t0 < timeout_ms) {
    const int n = LoRa.parsePacket();
    if (n > 0) {
      size_t k = 0;
      while (LoRa.available() && k < cap) buf[k++] = static_cast<uint8_t>(LoRa.read());
      while (LoRa.available()) LoRa.read();  // drop oversize tail
      if (rssi) *rssi = static_cast<int16_t>(LoRa.packetRssi());
      if (snr) *snr = LoRa.packetSnr();
      ++stats_.rx_frames;
      if (static_cast<size_t>(n) > cap) {
        ++stats_.rx_crc_errors;
        return 0;
      }
      return k;
    }
    delay(1);
  }
  LoRa.idle();
  return 0;
}

bool LoraRadio::channel_clear(uint8_t channel_index) {
  if (!configure(channel_index)) return false;
  // RSSI-based listen-before-talk: -90 dBm threshold, 5 ms sample (ETSI EN 300 220 LBT)
  LoRa.receive();
  delay(5);
  const int rssi = LoRa.rssi();
  LoRa.idle();
  return rssi < -90;
}

void LoraRadio::sleep() {
  LoRa.sleep();
  current_channel_ = -1;
}

}  // namespace agri
#endif
