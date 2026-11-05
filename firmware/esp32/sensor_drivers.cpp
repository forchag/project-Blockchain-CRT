// Sensor driver implementations: ADC, I2C (SHT31 humidity, BH1750 illuminance),
// 1-Wire (DS18B20 temperature), conversion, error detection, range checking.
// See sensor_drivers.h for parts and pins, calibration.h for formulas.
#include "sensor_drivers.h"

#include <Adafruit_SHT31.h>
#include <Arduino.h>
#include <BH1750.h>
#include <DallasTemperature.h>
#include <OneWire.h>
#include <Preferences.h>
#include <Wire.h>

#include "calibration.h"

namespace agri {

namespace {
Adafruit_SHT31 g_sht31;
BH1750 g_bh1750(I2C_ADDR_BH1750);
OneWire g_onewire(PIN_DS18B20);
DallasTemperature g_ds18b20(&g_onewire);
Calibration g_cal;

bool in_range(float v, Range r) { return !isnan(v) && v >= r.lo && v <= r.hi; }
}  // namespace

const char* sensor_status_name(SensorStatus s) {
  switch (s) {
    case SENSOR_OK: return "OK";
    case SENSOR_NOT_FOUND: return "NOT_FOUND";
    case SENSOR_DISCONNECTED: return "DISCONNECTED";
    case SENSOR_READ_FAILED: return "READ_FAILED";
    case SENSOR_OUT_OF_RANGE: return "OUT_OF_RANGE";
    case SENSOR_UNSTABLE: return "UNSTABLE";
    case SENSOR_NOT_CALIBRATED: return "NOT_CALIBRATED";
  }
  return "UNKNOWN";
}

// NVS view of a calibration record: every getter checks the stored type, so a key
// written with another type, or a float blob of the wrong length, is refused rather
// than read as a default.
class NvsCalibrationSource : public CalibrationSource {
 public:
  explicit NvsCalibrationSource(Preferences& p) : p_(p) {}
  bool has(const char* k) const override { return p_.isKey(k); }
  bool get_u32(const char* k, uint32_t* v) const override {
    if (p_.getType(k) != PT_U32) return false;
    *v = p_.getUInt(k, 0);
    return true;
  }
  bool get_u16(const char* k, uint16_t* v) const override {
    if (p_.getType(k) != PT_U16) return false;
    *v = p_.getUShort(k, 0);
    return true;
  }
  bool get_u8(const char* k, uint8_t* v) const override {
    if (p_.getType(k) != PT_U8) return false;
    *v = p_.getUChar(k, 0);
    return true;
  }
  bool get_f32(const char* k, float* v) const override {
    if (p_.getType(k) != PT_BLOB || p_.getBytesLength(k) != sizeof(float)) return false;
    return p_.getBytes(k, v, sizeof(float)) == sizeof(float);
  }

 private:
  Preferences& p_;
};

CalStatus load_calibration(Calibration* c, const char** bad_key) {
  Preferences p;
  if (!p.begin("calib", true)) {
    *c = default_calibration();
    if (bad_key) *bad_key = "calib";
    return CAL_NO_RECORD;
  }
  NvsCalibrationSource src(p);
  const CalStatus st = read_calibration(src, c, bad_key);
  p.end();
  return st;
}

uint8_t SensorDrivers::begin() {
  pinMode(PIN_SENSOR_PWR, OUTPUT);
  digitalWrite(PIN_SENSOR_PWR, HIGH);  // front end off until a reading
  analogReadResolution(ADC_WIDTH_BITS);
  analogSetPinAttenuation(PIN_SOIL_ADC, ADC_11db);
  analogSetPinAttenuation(PIN_PH_ADC, ADC_11db);
  Wire.begin(PIN_I2C_SDA, PIN_I2C_SCL, 100000);

  const char* bad_key = nullptr;
  cal_status_ = load_calibration(&g_cal, &bad_key);
  if (cal_status_ != CAL_OK)
    log_w("calibration record rejected: %s%s%s; analog channels report NOT_CALIBRATED", cal_status_name(cal_status_),
          bad_key ? " at key " : "", bad_key ? bad_key : "");

  // SHT31 is used for humidity only; its temperature output is read for calibration
  // diagnostics but does not feed Reading::q[Q_TEMPERATURE].
  sht31_ok_ = g_sht31.begin(I2C_ADDR_SHT31);
  if (sht31_ok_) g_sht31.heater(false);

  // DS18B20 is the temperature sensor. begin() scans the 1-Wire bus. A device count
  // of zero (bad wiring, missing pull-up) leaves ds18b20_ok_ false and temperature
  // reports NOT_FOUND; pH compensation then falls back to 25 degC in read_analog.
  g_ds18b20.begin();
  if (g_ds18b20.getDeviceCount() > 0) {
    g_ds18b20.setResolution(12);
    g_ds18b20.setWaitForConversion(true);  // requestTemperatures blocks DS18B20_CONVERSION_MS
    ds18b20_ok_ = true;
  } else {
    log_w("no DS18B20 on the 1-Wire bus at pin %d; temperature will report NOT_FOUND", PIN_DS18B20);
  }

  bh1750_ok_ = g_bh1750.begin(BH1750::ONE_TIME_HIGH_RES_MODE, I2C_ADDR_BH1750, &Wire);
  if (bh1750_ok_) g_bh1750.setMTreg(g_cal.bh1750_mtreg);

  uint8_t present = (1u << Q_SOIL_MOISTURE) | (1u << Q_PH);  // analog: presence is checked per read
  if (sht31_ok_) present |= (1u << Q_HUMIDITY);
  if (ds18b20_ok_) present |= (1u << Q_TEMPERATURE);
  if (bh1750_ok_) present |= (1u << Q_ILLUMINANCE);
  return present;
}

QuantityValue SensorDrivers::read_analog(int pin, uint16_t* raw_mv, bool is_ph, float temperature_c) {
  uint32_t sum = 0;
  uint16_t lo = 0xFFFF, hi = 0;
  for (int i = 0; i < ADC_SAMPLES; ++i) {
    const uint16_t mv = static_cast<uint16_t>(analogReadMilliVolts(pin));
    sum += mv;
    lo = min(lo, mv);
    hi = max(hi, mv);
    delayMicroseconds(500);
  }
  const float mean = static_cast<float>(sum) / ADC_SAMPLES;
  *raw_mv = static_cast<uint16_t>(lroundf(mean));
  const SensorStatus st = analog_sample_status(mean, lo, hi, g_cal.loaded);  // rails, spread, calibration
  if (st != SENSOR_OK) return {NAN, st};
  if (is_ph) {
    const float t = isnan(temperature_c) ? 25.0f : temperature_c;  // no compensation without a temperature
    const float v = round_to(ph_from_mv(g_cal, mean, t), STEP_PH);
    return in_range(v, RANGE_PH) ? QuantityValue{v, SENSOR_OK} : QuantityValue{v, SENSOR_OUT_OF_RANGE};
  }
  // Soil: outside the dry/wet reference span by more than 10 % of the span means the
  // probe is out of the soil or damaged rather than very dry/very wet soil.
  if (!soil_probe_in_span(mean, g_cal)) return {NAN, SENSOR_OUT_OF_RANGE};
  const float v = round_to(clipf(soil_moisture_from_mv(g_cal, mean), RANGE_SOIL_MOISTURE), STEP_SOIL);
  return {v, SENSOR_OK};
}

QuantityValue SensorDrivers::read_sht31_humidity() {
  if (!sht31_ok_) return {NAN, SENSOR_NOT_FOUND};
  float hraw = NAN;
  for (int attempt = 0; attempt < 3 && isnan(hraw); ++attempt) {
    hraw = g_sht31.readHumidity();  // single-shot, high repeatability, CRC checked by the library
    if (isnan(hraw)) delay(20);
  }
  if (isnan(hraw)) return {NAN, SENSOR_READ_FAILED};
  // DS18B20 is factory-trimmed; the humidity correction table is temperature-independent
  // in this design, so no separate temperature input is needed here.
  const float hc = round_to(clipf(humidity_corrected(g_cal, hraw), RANGE_HUMIDITY), STEP_RH);
  return {hc, SENSOR_OK};
}

QuantityValue SensorDrivers::read_ds18b20(int16_t* raw_x16) {
  *raw_x16 = 0;
  if (!ds18b20_ok_) return {NAN, SENSOR_NOT_FOUND};
  g_ds18b20.requestTemperatures();  // blocking DS18B20_CONVERSION_MS at 12-bit
  const float t = g_ds18b20.getTempCByIndex(0);  // DEVICE_DISCONNECTED_C (-127) on failure
  if (!ds18b20_reading_valid(t)) return {NAN, SENSOR_READ_FAILED};  // includes DEVICE_DISCONNECTED_C (-127)
  // Preserve the 12-bit raw form (value x 16) for diagnostics and recalibration.
  *raw_x16 = static_cast<int16_t>(roundf(t * 16.0f));
  // DS18B20 is factory-trimmed to +-0.5 degC: no gain/offset applied. Field range is
  // enforced by RANGE_TEMPERATURE, the same bound the previous SHT31 path used.
  return in_range(t, RANGE_TEMPERATURE) ? QuantityValue{round_to(t, STEP_TEMP), SENSOR_OK}
                                       : QuantityValue{t, SENSOR_OUT_OF_RANGE};
}

QuantityValue SensorDrivers::read_temperature_only(int16_t* raw_x16) {
  return read_ds18b20(raw_x16);
}

QuantityValue SensorDrivers::read_bh1750(uint16_t* raw) {
  *raw = 0;
  if (!bh1750_ok_) return {NAN, SENSOR_NOT_FOUND};
  g_bh1750.configure(BH1750::ONE_TIME_HIGH_RES_MODE);
  const uint32_t t0 = millis();
  while (!g_bh1750.measurementReady(true)) {
    if (millis() - t0 > BH1750_MEAS_MS + 100) return {NAN, SENSOR_READ_FAILED};
    delay(10);
  }
  const float lib_lux = g_bh1750.readLightLevel();  // -1 = no valid data, -2 = device not configured
  if (lib_lux < 0) return {NAN, SENSOR_READ_FAILED};
  // Recover the raw count so our own calibration factor applies (library uses 1.2 and MTreg).
  const float count = lib_lux * 1.2f * (g_cal.bh1750_mtreg / 69.0f);
  *raw = static_cast<uint16_t>(min(65535.0f, roundf(count)));
  float lux = lux_from_count(g_cal, *raw);
  if (lux > RANGE_ILLUMINANCE.hi) return {RANGE_ILLUMINANCE.hi, SENSOR_OUT_OF_RANGE};  // saturated above the scale cap
  return {round_to(lux, STEP_LUX), SENSOR_OK};
}

void SensorDrivers::read_all(Reading* r) {
  digitalWrite(PIN_SENSOR_PWR, LOW);  // analog front end on
  const uint32_t t_on = millis();
  // Fast digital reads first; SHT31 and BH1750 finish while the analog front end settles
  // and while the DS18B20 conversion below is still pending.
  r->q[Q_HUMIDITY] = read_sht31_humidity();
  r->q[Q_ILLUMINANCE] = read_bh1750(&r->raw_lux_count);
  // DS18B20 blocks DS18B20_CONVERSION_MS (750 ms at 12-bit). By the time it returns,
  // ANALOG_SETTLE_MS (300 ms) has long elapsed, so the guard below is a no-op.
  r->q[Q_TEMPERATURE] = read_ds18b20(&r->raw_ds18b20_c_x16);
  while (millis() - t_on < ANALOG_SETTLE_MS) delay(5);
  const float temp = r->q[Q_TEMPERATURE].valid() ? r->q[Q_TEMPERATURE].value : NAN;
  r->q[Q_SOIL_MOISTURE] = read_analog(PIN_SOIL_ADC, &r->raw_soil_mv, false, temp);
  r->q[Q_PH] = read_analog(PIN_PH_ADC, &r->raw_ph_mv, true, temp);
  power_down();
}

QuantityValue SensorDrivers::read_soil_moisture_only(uint16_t* raw_mv) {
  digitalWrite(PIN_SENSOR_PWR, LOW);
  delay(ANALOG_SETTLE_MS);
  QuantityValue v = read_analog(PIN_SOIL_ADC, raw_mv, false, NAN);
  power_down();
  return v;
}

void SensorDrivers::power_down() { digitalWrite(PIN_SENSOR_PWR, HIGH); }

}  // namespace agri
