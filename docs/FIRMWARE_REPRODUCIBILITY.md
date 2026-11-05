# Firmware reproducibility

The published firmware is the CRT sensing and transmission path that ran on the field nodes, as verified by the authors; the adaptive-mode, dual-core sampling and water-control logic that also ran in the deployment is not included in this release. It lives in `firmware/esp32`; the gateway and Fabric side ran on Raspberry Pi (see [GATEWAY_PROTOCOL.md](GATEWAY_PROTOCOL.md) and [BLOCKCHAIN_REPRODUCIBILITY.md](BLOCKCHAIN_REPRODUCIBILITY.md)). Each node is an ESP32-WROOM-32 with an RFM95W (SX1276) radio and a DS3231 real-time clock. It implements the full five-sensor CRT method described in the paper.

## Hardware profile

| # | Quantity | Sensor | Interface | ESP32 pin |
|---|---|---|---|---|
| 1 | Soil moisture | DFRobot SEN0193 v2.0 capacitive | Analog | GPIO34 / ADC1_CH6, power-gated by a P-MOSFET on GPIO25 |
| 2 | Soil pH | DFRobot SEN0161-V2 + amplifier | Analog | GPIO35 / ADC1_CH7, same power gate |
| 3 | Temperature | Maxim DS18B20 | 1-Wire, 4.7 kΩ pull-up | GPIO4 |
| 4 | Humidity | Sensirion SHT31-D (0x44) | I²C | GPIO21 / GPIO22 |
| 5 | Illuminance | ROHM BH1750FVI (0x23) | I²C | GPIO21 / GPIO22, MTreg 69, one-time high resolution |

The DS18B20 replaced the SHT31 for field temperature. The SHT31's temperature channel is still read for bench calibration diagnostics and never feeds `Reading::q[Q_TEMPERATURE]` or the pH Nernst compensation. Radio: SPI on GPIO18/19/23, NSS GPIO5, RST GPIO14, DIO0 GPIO26; +14 dBm on PA_BOOST, sync word 0x12. RTC: DS3231 on I²C 0x68.

## What the firmware does, and where it is tested

Every item below is exercised by `make -C firmware/esp32/tests`, which builds the platform-independent sources twice (`-DFRAME_MODE=15` and `-DFRAME_MODE=3`) and runs 277 checks per build.

### Sensor initialisation and error handling

`SensorDrivers::begin()` probes the I²C devices and the 1-Wire bus; each quantity carries a `SensorStatus`. The fault rules are pure functions in `sensor_drivers.h` and tested directly: an analog mean outside 40 to 3,100 mV means DISCONNECTED, a 16-sample spread above 120 mV means UNSTABLE, a missing calibration record means NOT_CALIBRATED, a soil reading more than 10 % of the dry/wet span outside it means OUT_OF_RANGE, and a DS18B20 result of -127 °C, NaN or outside -55 to 125 °C means READ_FAILED.

### Calibration validation

`read_calibration()` in `calibration.h` accepts a stored record only if every key is present with its exact NVS type, the layout version matches, every value is finite and inside the `CAL_LIMIT_*` bounds, and the soil fit decreases across the dry-wet span. One bad coefficient rejects the whole record and the analog channels report NOT_CALIBRATED. The host tests remove each key in turn, change types, and feed 13 implausible values. `tools/gen_calibration_nvs.py` applies the same bounds (parsed from `calibration.h`) when it writes per-node NVS partitions, and refuses the illustrative CSVs in `examples/calibration/` unless `--allow-illustrative` is passed.

### Measurement scaling

`scaled = round((value + offset) x factor)`; soil 0.1 %VWC, temperature 0.1 °C with +40 offset, humidity 0.1 %RH, pH 0.01, illuminance 5 lx up to 48,980 lx. Every maximum is below 9,797.

### CRT encoding

Residues mod 97, 101 and 103; three per quantity; fifteen residue packets per five-quantity reading. Reconstruction (Garner) is checked for every scaled value 0 to 9,796 from every pair and from the triplet, and every single-residue corruption of a triplet is detected.

### 8-byte residue packet

`[type 0x1 | version 1] [node] [seq hi] [seq lo] [quantity:3 | residue index:2 | valid | event | retx] [residue] [CRC-16/CCITT-FALSE hi] [lo]`, checked against fixed hex vectors shared with the gateway.

### Three LoRa channels

Residue index i is always sent on `CHANNEL_HZ[i]` = 868.1 / 868.3 / 868.5 MHz at SF9, 125 kHz, CR 4/5, explicit header, CRC on, 8-symbol preamble (123.904 ms per frame).

### Poll reception (`poll_receiver.cpp`)

A POLL addressed to the node is accepted only if its 16-bit poll ID is newer than the last accepted one (RFC 1982 comparison, wrap-around tested), the gateway clock is within 900 s of the RTC, and the whole response fits the slot. Skew above 2 s triggers an RTC resynchronisation.

### Gateway acknowledgement reception (`ack_receiver.cpp`)

Per quantity, an ACK either confirms reconstruction, requests the residues missing from its mask (all three if the gateway saw an inconsistent triplet), or abandons the quantity. Duplicates, ACKs for another node or reading, and ACKs for quantities not yet sent are ignored.

### Timeouts and retry handling

No ACK within 400 ms resends all three residues; at most two retransmissions per quantity, then the quantity is marked failed and the reading continues.

### Sequence numbering

16-bit, held in RTC memory across deep sleep and checkpointed to NVS every 16 readings; after a power loss the node resumes 16 past the checkpoint so no number is reused (`resume_sequence`, tested including wrap-around).

### Sleep and wake scheduling

`current_or_next_slot_start()` and `next_wake_at()` in `mac.h` compute the slot from zone index and position and choose the earlier of the next 300 s event check and the wake 3 s before the next slot; tests cover slot placement inside all four zone windows and both wake branches.

The fifteen-packet version (FRAME_MODE 15) is the default build and corresponds to the manuscript's five-quantity, fifteen-packet method. FRAME_MODE 3 (three 12-byte channel frames, one whole-reading ACK) is also implemented as a comparison mode.

## Building

```bash
make -C firmware/esp32/tests
```

```bash
pip install platformio==6.1.16
```

```bash
firmware/esp32/build_firmware.sh node
```

The build writes `build/firmware/node/firmware.bin` and `build_manifest.json` (git-ignored); `build_manifest.example.json` shows the fields with placeholder values. Other environments: `node_3frame` (FRAME_MODE 3; the gateway must then run with `frames.frame_mode: 3`) and `bench_node` (compiled-in node identity 1, zone 0, position 0). Library versions are pinned in `platformio.ini`: espressif32 6.9.0 (Arduino-ESP32 2.0.17, ESP-IDF 4.4.7), LoRa 0.8.0, Adafruit SHT31 2.2.2, Adafruit BusIO 1.16.1, BH1750 1.3.0, RTClib 2.1.4, OneWire 2.3.8, DallasTemperature 3.11.0. The original project specified OneWire and DallasTemperature with `^` ranges; they're pinned here to the versions selected for this reproducible build.

CI (`.github/workflows/build-firmware.yml`) builds all three environments and uploads the binary and manifest as workflow artifacts. No binary is committed.

## Provisioning a node

```bash
python3 firmware/esp32/tools/gen_calibration_nvs.py --cal-dir <your calibration CSVs> --deployment <your roster> --out build/nvs
```

Then generate and flash the NVS partition with ESP-IDF's `nvs_partition_gen.py` and `esptool.py` as the tool's docstring shows. Do not flash records built from `examples/calibration/`; they're invented for testing code. The deployment's calibration records are withheld for privacy reasons ([DATA_AVAILABILITY.md](DATA_AVAILABILITY.md)).

## Scope

This release runs the fixed 1800 s cycle. The adaptive modes, dual-core sampling and water control (float-switch level sensing and MOSFET pump control) ran in the deployment but are not included in this release.

The ESP32 cross-compile of the Arduino-dependent files runs only in CI (`.github/workflows/build-firmware.yml`); the host tests cover the platform-independent logic only.
