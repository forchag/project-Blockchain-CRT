// Calibration constants and conversion formulas.
//
// Per-node coefficients live in NVS namespace "calib" and are written at
// provisioning by firmware/esp32/tools/gen_calibration_nvs.py from calibration CSVs
// (public branch: illustrative CSVs in examples/calibration/; the deployment's
// per-node calibration records are not published).
// A stored record is used only if it is complete and valid as a whole
// (read_calibration below): every key present with its exact NVS type, the
// version matching, every value finite and inside the CAL_LIMIT_* bounds. One
// missing or implausible coefficient rejects the whole record; stored values are
// never mixed with defaults. Without an accepted record the analog channels
// (soil moisture, pH) report SENSOR_NOT_CALIBRATED, and the factory-calibrated
// digital sensors (DS18B20, SHT31, BH1750) run within their datasheet accuracy
// without an additional correction.
//
// Temperature sensor note: Reading::q[Q_TEMPERATURE] and the Nernst compensation
// input to ph_from_mv() are sourced from a Maxim DS18B20 (1-Wire, factory-trimmed
// to +-0.5 degC, 12-bit resolution 0.0625 degC). The SHT31's own temperature
// output is still read for the bench calibration workflow (t_gain/t_offset below,
// temperature_corrected()) and is recorded in humidity_calibration.csv as
// raw_temperature_c, but it does not feed the field reading. Keeping t_gain and
// t_offset in the calibration record preserves the NVS layout and the schema of
// humidity_calibration.csv, so existing provisioning and validation continue to
// work unchanged.
//
// firmware/esp32/tools/gen_calibration_nvs.py parses the CAL_LIMIT_* lines below and
// refuses to provision a record that would be rejected here.
#pragma once
#include <math.h>
#include <stdint.h>

namespace agri {

// ----------------------------------------------------------------- ADC settings
// ESP32 ADC1, 12-bit, 11 dB attenuation (~150..3100 mV usable), eFuse two-point
// calibration via analogReadMilliVolts().
constexpr uint8_t ADC_WIDTH_BITS = 12;
constexpr uint8_t ADC_ATTENUATION_DB = 11;

// ----------------------------------------------------------------- admissible ranges
struct Range {
  float lo, hi;
};
constexpr Range RANGE_SOIL_MOISTURE = {0.0f, 100.0f};   // %VWC
constexpr Range RANGE_TEMPERATURE = {-40.0f, 85.0f};    // degC, field-site plausibility bound (DS18B20 span is -55..+125)
constexpr Range RANGE_HUMIDITY = {0.0f, 100.0f};        // %RH
constexpr Range RANGE_PH = {3.0f, 10.0f};               // electrode linear range in soil slurry
constexpr Range RANGE_ILLUMINANCE = {0.0f, 48980.0f};   // lx, capped by the CRT scale (5 lx x 9796)

struct Calibration {
  uint32_t version;          // bump when the layout changes
  bool loaded;               // true only when read from NVS
  // Soil moisture: VWC = a0 + a1*mV + a2*mV^2 (quadratic fit to gravimetric references)
  float soil_a0, soil_a1, soil_a2;
  uint16_t soil_dry_mv, soil_wet_mv;  // reference points, used for the disconnected/out-of-range check
  // pH: Nernstian, E = E7 + S(T) * (pH - 7)  =>  pH = 7 + (E - E7) / S(T),  S(T) = S25 * (T + 273.15) / 298.15
  // The temperature T in the Nernst term comes from the DS18B20 (sensor_drivers.cpp).
  float ph_e7_mv;            // electrode output at pH 7.00, 25 degC (amplified, mV at the ADC)
  float ph_slope_mv;         // S25: mV per pH at 25 degC at the ADC (negative)
  // SHT31 humidity: corrected = gain * raw + offset
  float rh_gain, rh_offset;
  // SHT31 temperature: corrected = gain * raw + offset. Retained for the bench
  // calibration workflow and for compatibility with existing NVS records and
  // humidity_calibration.csv; the firmware reads temperature from DS18B20 and
  // does not apply these coefficients to the field reading.
  float t_gain, t_offset;
  // BH1750: lux = count / 1.2 * (69 / mtreg) * lux_factor
  uint8_t bh1750_mtreg;
  float lux_factor;
};

constexpr uint32_t CALIBRATION_VERSION = 1;

inline Calibration default_calibration() {
  Calibration c{};
  c.version = CALIBRATION_VERSION;
  c.loaded = false;
  // DFRobot SEN0193 nominal: ~2,600 mV in air, ~1,200 mV in water at 3V3 supply
  c.soil_a0 = 185.7f;
  c.soil_a1 = -0.0714f;
  c.soil_a2 = 0.0f;
  c.soil_dry_mv = 2600;
  c.soil_wet_mv = 1200;
  // SEN0161-V2 board: ~1,500 mV at pH 7, about -165 mV/pH after the amplifier
  c.ph_e7_mv = 1500.0f;
  c.ph_slope_mv = -165.0f;
  c.rh_gain = 1.0f;
  c.rh_offset = 0.0f;
  c.t_gain = 1.0f;
  c.t_offset = 0.0f;
  c.bh1750_mtreg = 69;
  c.lux_factor = 1.0f;
  return c;
}

// ----------------------------------------------------------------- record validation
// Plausibility bounds for this hardware (SEN0193 capacitive soil probe, SEN0161-V2
// pH board behind its amplifier, SHT31 humidity, BH1750). The SHT31 temperature
// bounds (CAL_LIMIT_T_*) apply to the bench calibration record only; the field
// temperature comes from DS18B20 and is not calibrated.
// Format is parsed by gen_calibration_nvs.py: keep one "constexpr Range CAL_LIMIT_<NAME> = {lo, hi};" per line.
constexpr Range CAL_LIMIT_SOIL_REF_MV = {150.0f, 3100.0f};     // dry and wet reference voltages (usable ADC span)
constexpr Range CAL_LIMIT_SOIL_SPAN_MV = {300.0f, 3000.0f};    // dry_mv - wet_mv
constexpr Range CAL_LIMIT_SOIL_VWC_DRY = {-5.0f, 15.0f};       // fitted %VWC at the dry reference
constexpr Range CAL_LIMIT_SOIL_VWC_WET = {30.0f, 100.0f};      // fitted %VWC at the wet reference
constexpr Range CAL_LIMIT_PH_E7_MV = {1000.0f, 2000.0f};       // amplified electrode output at pH 7
constexpr Range CAL_LIMIT_PH_SLOPE_MV = {-200.0f, -120.0f};    // mV/pH at 25 degC after the amplifier
constexpr Range CAL_LIMIT_RH_GAIN = {0.90f, 1.10f};
constexpr Range CAL_LIMIT_RH_OFFSET = {-5.0f, 5.0f};           // %RH
constexpr Range CAL_LIMIT_T_GAIN = {0.95f, 1.05f};             // SHT31 temperature (bench calibration only)
constexpr Range CAL_LIMIT_T_OFFSET = {-2.0f, 2.0f};            // degC, SHT31 temperature (bench calibration only)
constexpr Range CAL_LIMIT_BH1750_MTREG = {31.0f, 254.0f};      // BH1750 MTreg register range
constexpr Range CAL_LIMIT_LUX_FACTOR = {0.5f, 2.0f};

enum CalStatus : uint8_t {
  CAL_OK = 0,
  CAL_NO_RECORD = 1,         // namespace or version key absent
  CAL_VERSION_MISMATCH = 2,  // record written for another layout
  CAL_MISSING_KEY = 3,       // a coefficient is absent
  CAL_WRONG_TYPE = 4,        // a key has the wrong NVS type or blob length
  CAL_NOT_FINITE = 5,        // NaN or infinity
  CAL_SOIL_INVALID = 6,
  CAL_PH_INVALID = 7,
  CAL_SHT31_INVALID = 8,
  CAL_BH1750_INVALID = 9,
};

inline const char* cal_status_name(CalStatus s) {
  switch (s) {
    case CAL_OK: return "OK";
    case CAL_NO_RECORD: return "NO_RECORD";
    case CAL_VERSION_MISMATCH: return "VERSION_MISMATCH";
    case CAL_MISSING_KEY: return "MISSING_KEY";
    case CAL_WRONG_TYPE: return "WRONG_TYPE";
    case CAL_NOT_FINITE: return "NOT_FINITE";
    case CAL_SOIL_INVALID: return "SOIL_INVALID";
    case CAL_PH_INVALID: return "PH_INVALID";
    case CAL_SHT31_INVALID: return "SHT31_INVALID";
    case CAL_BH1750_INVALID: return "BH1750_INVALID";
  }
  return "UNKNOWN";
}

inline bool within(float v, Range r) { return isfinite(v) && v >= r.lo && v <= r.hi; }

inline float soil_moisture_at(const Calibration& c, float mv) {
  return c.soil_a0 + c.soil_a1 * mv + c.soil_a2 * mv * mv;
}

// Checks a complete record's values. Pure; also used by the host tests.
inline CalStatus validate_calibration(const Calibration& c) {
  const float f[] = {c.soil_a0, c.soil_a1, c.soil_a2, c.ph_e7_mv, c.ph_slope_mv, c.rh_gain, c.rh_offset,
                     c.t_gain, c.t_offset, c.lux_factor};
  for (float v : f)
    if (!isfinite(v)) return CAL_NOT_FINITE;
  const float dry = c.soil_dry_mv, wet = c.soil_wet_mv;
  if (!within(dry, CAL_LIMIT_SOIL_REF_MV) || !within(wet, CAL_LIMIT_SOIL_REF_MV) ||
      !within(dry - wet, CAL_LIMIT_SOIL_SPAN_MV))
    return CAL_SOIL_INVALID;
  if (!within(soil_moisture_at(c, dry), CAL_LIMIT_SOIL_VWC_DRY) || !within(soil_moisture_at(c, wet), CAL_LIMIT_SOIL_VWC_WET))
    return CAL_SOIL_INVALID;
  // capacitive probe: output falls as moisture rises, so the fit must decrease across the whole span
  // (a quadratic is monotone on an interval iff its derivative has one sign at both ends)
  if (c.soil_a1 + 2 * c.soil_a2 * wet >= 0 || c.soil_a1 + 2 * c.soil_a2 * dry >= 0) return CAL_SOIL_INVALID;
  if (!within(c.ph_e7_mv, CAL_LIMIT_PH_E7_MV) || !within(c.ph_slope_mv, CAL_LIMIT_PH_SLOPE_MV)) return CAL_PH_INVALID;
  if (!within(c.rh_gain, CAL_LIMIT_RH_GAIN) || !within(c.rh_offset, CAL_LIMIT_RH_OFFSET) ||
      !within(c.t_gain, CAL_LIMIT_T_GAIN) || !within(c.t_offset, CAL_LIMIT_T_OFFSET))
    return CAL_SHT31_INVALID;
  if (!within(c.bh1750_mtreg, CAL_LIMIT_BH1750_MTREG) || !within(c.lux_factor, CAL_LIMIT_LUX_FACTOR))
    return CAL_BH1750_INVALID;
  return CAL_OK;
}

// Typed key/value access to a stored record (NVS on the node, a map in the host tests).
// Each getter returns false if the key is absent or stored with another type/length.
struct CalibrationSource {
  virtual ~CalibrationSource() = default;
  virtual bool has(const char* key) const = 0;
  virtual bool get_u32(const char* key, uint32_t* v) const = 0;
  virtual bool get_u16(const char* key, uint16_t* v) const = 0;
  virtual bool get_u8(const char* key, uint8_t* v) const = 0;
  virtual bool get_f32(const char* key, float* v) const = 0;  // 4-byte little-endian blob
};

// Reads and validates a whole record. On CAL_OK, *out holds it with loaded = true.
// On any other status, *out is default_calibration() with loaded = false, and
// *bad_key (if given) names the first offending key where there is one.
inline CalStatus read_calibration(const CalibrationSource& src, Calibration* out, const char** bad_key = nullptr) {
  *out = default_calibration();
  if (bad_key) *bad_key = nullptr;
  auto fail = [&](CalStatus st, const char* key) {
    if (bad_key) *bad_key = key;
    *out = default_calibration();
    return st;
  };
  if (!src.has("version")) return fail(CAL_NO_RECORD, "version");
  Calibration c{};
  if (!src.get_u32("version", &c.version)) return fail(CAL_WRONG_TYPE, "version");
  if (c.version != CALIBRATION_VERSION) return fail(CAL_VERSION_MISMATCH, "version");
  struct F { const char* key; float* dst; } floats[] = {
      {"soil_a0", &c.soil_a0}, {"soil_a1", &c.soil_a1}, {"soil_a2", &c.soil_a2}, {"ph_e7", &c.ph_e7_mv},
      {"ph_slope", &c.ph_slope_mv}, {"rh_gain", &c.rh_gain}, {"rh_off", &c.rh_offset}, {"t_gain", &c.t_gain},
      {"t_off", &c.t_offset}, {"lux_f", &c.lux_factor}};
  for (const F& f : floats) {
    if (!src.has(f.key)) return fail(CAL_MISSING_KEY, f.key);
    if (!src.get_f32(f.key, f.dst)) return fail(CAL_WRONG_TYPE, f.key);
  }
  struct U16 { const char* key; uint16_t* dst; } shorts[] = {{"soil_dry", &c.soil_dry_mv}, {"soil_wet", &c.soil_wet_mv}};
  for (const U16& u : shorts) {
    if (!src.has(u.key)) return fail(CAL_MISSING_KEY, u.key);
    if (!src.get_u16(u.key, u.dst)) return fail(CAL_WRONG_TYPE, u.key);
  }
  if (!src.has("mtreg")) return fail(CAL_MISSING_KEY, "mtreg");
  if (!src.get_u8("mtreg", &c.bh1750_mtreg)) return fail(CAL_WRONG_TYPE, "mtreg");
  const CalStatus st = validate_calibration(c);
  if (st != CAL_OK) return fail(st, nullptr);
  c.loaded = true;
  *out = c;
  return CAL_OK;
}

inline float clipf(float v, Range r) { return v < r.lo ? r.lo : (v > r.hi ? r.hi : v); }
inline float round_to(float v, float step) { return roundf(v / step) * step; }

inline float soil_moisture_from_mv(const Calibration& c, float mv) {
  return c.soil_a0 + c.soil_a1 * mv + c.soil_a2 * mv * mv;
}

inline float ph_from_mv(const Calibration& c, float mv, float temperature_c) {
  // temperature_c comes from the DS18B20 in the field (see sensor_drivers.cpp);
  // a NaN falls back to 25 degC at the call site in read_analog().
  const float slope_t = c.ph_slope_mv * (temperature_c + 273.15f) / 298.15f;  // temperature compensation
  return 7.0f + (mv - c.ph_e7_mv) / slope_t;
}

inline float humidity_corrected(const Calibration& c, float raw) { return c.rh_gain * raw + c.rh_offset; }

// Applies the SHT31 temperature gain/offset. Retained for the bench calibration
// workflow; the field sensing path (sensor_drivers.cpp::read_ds18b20) reads
// DS18B20 directly and does not call this.
inline float temperature_corrected(const Calibration& c, float raw) { return c.t_gain * raw + c.t_offset; }

inline float lux_from_count(const Calibration& c, uint16_t count) {
  return static_cast<float>(count) / 1.2f * (69.0f / c.bh1750_mtreg) * c.lux_factor;
}

// Resolution each quantity is rounded to before scaling (matches agri::SCALE).
constexpr float STEP_SOIL = 0.1f, STEP_TEMP = 0.1f, STEP_RH = 0.1f, STEP_PH = 0.01f, STEP_LUX = 5.0f;

#ifdef ARDUINO
// Reads NVS namespace "calib" through read_calibration(). Returns the status; *c is
// the accepted record, or defaults with loaded = false.
CalStatus load_calibration(Calibration* c, const char** bad_key = nullptr);
#endif

}  // namespace agri
