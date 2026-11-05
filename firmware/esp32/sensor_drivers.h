// Legacy analog sensor profile for ESP32-WROOM-32 (public reference firmware).
// Note carried over from the source repository: a later on-site hardware target used
// SEN0308 + SEN0603 (RS485). SEN0603 is NOT implemented here; do not connect it to PIN_PH_ADC.
// This file is the reference profile the manuscript describes; whether it is byte-identical to the
// build flashed on the field nodes is not established.
//
// Channels and parts:
//   soil moisture  capacitive probe (DFRobot SEN0193 v2.0), analog, GPIO34 / ADC1_CH6
//   soil pH        pH electrode + amplifier board (DFRobot SEN0161-V2), analog, GPIO35 / ADC1_CH7
//   temperature    Maxim DS18B20 digital thermometer, 1-Wire, factory-trimmed to +-0.5 degC
//   humidity       Sensirion SHT31-D, I2C 0x44 (humidity only; its temperature output is not used)
//   illuminance    ROHM BH1750FVI, I2C 0x23
//   real-time clock Maxim DS3231, I2C 0x68
//
// The SHT31 still produces a temperature value over I2C; that value is read for diagnostics
// only (see SHT31 raw_temperature_c in humidity_calibration.csv). The temperature that feeds
// Reading::q[TEMPERATURE] and the pH Nernst compensation always comes from the DS18B20.
//
// The analog front end (soil probe + pH amplifier) is powered through a P-MOSFET
// on PIN_SENSOR_PWR and is switched off outside the sensing window. Leaving it on
// during deep sleep is what pushes whole-node sleep current into the milliamp range.
//
// DS18B20 wiring: DQ to PIN_DS18B20, 4.7 kOhm pull-up from DQ to 3V3. Parasite-power mode
// is not used; the sensor is externally powered from 3V3 so it can convert during sleep
// if the driver chooses an async scheme.
#pragma once
#include <stdint.h>

#include "calibration.h"
#include "lora_protocol.h"

namespace agri {

// ----------------------------------------------------------------- pin mapping
constexpr int PIN_I2C_SDA = 21;
constexpr int PIN_I2C_SCL = 22;
constexpr int PIN_SOIL_ADC = 34;      // input-only pin, ADC1 (usable while Wi-Fi is off or on)
constexpr int PIN_PH_ADC = 35;        // input-only pin, ADC1
constexpr int PIN_DS18B20 = 4;        // 1-Wire data line, 4.7 kOhm pull-up to 3V3 required
constexpr int PIN_SENSOR_PWR = 25;    // LOW = analog front end powered (P-MOSFET gate)
constexpr int PIN_RTC_INT = 27;       // DS3231 SQW/INT, RTC_GPIO17, deep-sleep wake source
constexpr int PIN_LORA_SCK = 18;
constexpr int PIN_LORA_MISO = 19;
constexpr int PIN_LORA_MOSI = 23;
constexpr int PIN_LORA_NSS = 5;
constexpr int PIN_LORA_RST = 14;
constexpr int PIN_LORA_DIO0 = 26;
constexpr int PIN_STATUS_LED = 2;

constexpr uint8_t I2C_ADDR_SHT31 = 0x44;
constexpr uint8_t I2C_ADDR_BH1750 = 0x23;
constexpr uint8_t I2C_ADDR_DS3231 = 0x68;

// ----------------------------------------------------------------- status codes
enum SensorStatus : uint8_t {
  SENSOR_OK = 0,
  SENSOR_NOT_FOUND = 1,       // device did not ACK at init (I2C) or did not respond on 1-Wire
  SENSOR_DISCONNECTED = 2,    // analog input stuck at a rail
  SENSOR_READ_FAILED = 3,     // bus error / CRC error / timeout during read
  SENSOR_OUT_OF_RANGE = 4,    // converted value outside the admissible range
  SENSOR_UNSTABLE = 5,        // sample spread above the stability limit
  SENSOR_NOT_CALIBRATED = 6,  // no calibration record loaded for this channel
};

const char* sensor_status_name(SensorStatus s);

// One value per quantity, in engineering units, with its own validity.
struct QuantityValue {
  float value;            // units below
  SensorStatus status;
  bool valid() const { return status == SENSOR_OK; }
};

// Units: soil_moisture %VWC, temperature degC, humidity %RH, ph pH, illuminance lx.
struct Reading {
  uint16_t seq;           // node reading sequence number
  uint32_t captured_at;   // Unix seconds UTC from the DS3231
  QuantityValue q[NUM_QUANTITIES];  // indexed by agri::Quantity
  uint16_t raw_soil_mv;   // kept for diagnostics and recalibration
  uint16_t raw_ph_mv;
  uint16_t raw_lux_count;
  int16_t  raw_ds18b20_c_x16;  // DS18B20 raw 12-bit output, value x16 (e.g. 251 = 25.0625 degC)
};

class SensorDrivers {
 public:
  // Brings up I2C, probes the I2C devices, and probes the DS18B20 bus.
  // Returns a bitmask of quantities whose hardware answered (bit = 1 << Quantity).
  uint8_t begin();
  // Powers the analog front end, waits for it to settle, samples every channel,
  // triggers a DS18B20 conversion, waits for it to finish, converts with the loaded
  // calibration and range-checks. Worst-case blocking ~1.5 s (300 ms settle + 750 ms
  // DS18B20 conversion + margin for the remaining reads).
  void read_all(Reading* r);
  // Soil moisture only, for the event check between scheduled reports.
  QuantityValue read_soil_moisture_only(uint16_t* raw_mv);
  // Temperature only, for callers that want a single fresh reading (e.g. tests). Blocks ~750 ms.
  QuantityValue read_temperature_only(int16_t* raw_x16);
  void power_down();
  // Result of reading the NVS calibration record at begin(); anything but CAL_OK means
  // the whole record was rejected (calibration.h, read_calibration).
  CalStatus calibration_status() const { return cal_status_; }

 private:
  CalStatus cal_status_ = CAL_NO_RECORD;
  bool sht31_ok_ = false;       // SHT31 answered on I2C at begin() — used for humidity only
  bool ds18b20_ok_ = false;     // at least one DS18B20 on the 1-Wire bus at begin()
  bool bh1750_ok_ = false;
  QuantityValue read_analog(int pin, uint16_t* raw_mv, bool is_ph, float temperature_c);
  QuantityValue read_ds18b20(int16_t* raw_x16);
  QuantityValue read_sht31_humidity();
  QuantityValue read_bh1750(uint16_t* raw);
};

// ADC sampling
constexpr int ADC_SAMPLES = 16;               // averaged per reading
constexpr uint16_t ADC_RAIL_LOW_MV = 40;       // below: input floating / probe disconnected
constexpr uint16_t ADC_RAIL_HIGH_MV = 3100;    // above: shorted to 3V3 / amplifier saturated
constexpr uint16_t ADC_MAX_SPREAD_MV = 120;    // max-min across the 16 samples
constexpr uint32_t ANALOG_SETTLE_MS = 300;     // front-end warm-up after power-on
constexpr uint32_t BH1750_MEAS_MS = 180;       // one-time H-resolution mode, MTreg 69
constexpr uint32_t DS18B20_CONVERSION_MS = 750;  // 12-bit conversion
constexpr float    DS18B20_RESOLUTION_C = 0.0625f;
constexpr float    DS18B20_MIN_C = -55.0f;
constexpr float    DS18B20_MAX_C = 125.0f;
constexpr float    DS18B20_DISCONNECTED_C = -127.0f;  // DallasTemperature DEVICE_DISCONNECTED_C

// ----------------------------------------------------------------- fault classification
// Pure functions used by sensor_drivers.cpp; the host tests exercise them directly.
// Analog channel: 16-sample mean and min/max, in mV at the ADC pin.
inline SensorStatus analog_sample_status(float mean_mv, uint16_t lo_mv, uint16_t hi_mv, bool calibrated) {
  if (mean_mv < ADC_RAIL_LOW_MV || mean_mv > ADC_RAIL_HIGH_MV) return SENSOR_DISCONNECTED;
  if (hi_mv - lo_mv > ADC_MAX_SPREAD_MV) return SENSOR_UNSTABLE;
  if (!calibrated) return SENSOR_NOT_CALIBRATED;
  return SENSOR_OK;
}

// Soil probe: a mean outside the dry/wet reference span by more than 10 % of the span means the
// probe is out of the soil or damaged, not very dry or very wet soil.
inline bool soil_probe_in_span(float mean_mv, const Calibration& c) {
  const float span = static_cast<float>(c.soil_dry_mv - c.soil_wet_mv);
  return !(mean_mv > c.soil_dry_mv + 0.1f * span || mean_mv < c.soil_wet_mv - 0.1f * span);
}

// DS18B20 conversion result usable as a temperature (disconnected, NaN or outside the part's span: no).
inline bool ds18b20_reading_valid(float t) {
  return !(t == DS18B20_DISCONNECTED_C || isnan(t) || t < DS18B20_MIN_C || t > DS18B20_MAX_C);
}

}  // namespace agri
