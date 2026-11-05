# Software versions

Everything the package builds or runs is pinned. The table lists where each pin lives; change pins only together with the file that records them.

## Python (3.13)

`requirements.in` lists direct dependencies; `requirements-lock.txt` pins all 24 packages with SHA-256 hashes (generated with `pip-compile --generate-hashes --allow-unsafe`). Install with `pip install --require-hashes -r requirements-lock.txt`.

| Package | Version | Used for |
|---|---|---|
| pandas | 3.0.5 | calibration provisioning, benchmark aggregation, RBAC overhead |
| numpy | 2.5.3 | pandas dependency |
| PyYAML | 6.0.3 | configuration |
| cryptography | 50.0.1 | Ed25519 gateway signatures, crypto micro-benchmark |
| matplotlib | 3.10.0 | analytical figures (optional) |
| jsonschema | 4.25.1 | example-data schema tests |
| pytest | 8.3.4 | test suite |

The original project's lock also pinned `protobuf` for ledger-export tooling, which isn't part of this package.

## R

R 4.3 or newer, base packages only (`stats`), for `analysis/agronomic_statistics.R`. Tested with R 4.3.3 (Ubuntu 24.04 `r-base-core`).

## Firmware

| Item | Version | Pinned in |
|---|---|---|
| PlatformIO Core | 6.1.16 | `firmware/esp32/build_firmware.sh`, CI |
| Platform | espressif32 6.9.0 (Arduino-ESP32 2.0.17, ESP-IDF 4.4.7) | `platformio.ini` |
| Toolchain | xtensa-esp32-elf-gcc 8.4.0 (toolchain-xtensa-esp32 8.4.0+2021r2-patch5) | resolved by the platform |
| sandeepmistry/LoRa | 0.8.0 | `platformio.ini` |
| adafruit/Adafruit SHT31 Library | 2.2.2 | `platformio.ini` |
| adafruit/Adafruit BusIO | 1.16.1 | `platformio.ini` |
| claws/BH1750 | 1.3.0 | `platformio.ini` |
| adafruit/RTClib | 2.1.4 | `platformio.ini` |
| paulstoffregen/OneWire | 2.3.8 | `platformio.ini` (was `^2.3.8` in the original project) |
| milesburton/DallasTemperature | 3.11.0 | `platformio.ini` (was `^3.11.0`) |
| Host test compiler | any C++17 compiler; CI uses the Ubuntu 24.04 g++ (13.3) | `firmware/esp32/tests/Makefile` |

## Go

| Module | Go | Key dependencies | Pinned in |
|---|---|---|---|
| `blockchain/chaincode/hrbac-cc` | 1.22 | fabric-chaincode-go/v2 v2.0.0, fabric-contract-api-go/v2 v2.0.0 | `go.mod`, `go.sum` |
| `gateway/fabric-submitter` | 1.22.0 (toolchain 1.24.7) | fabric-gateway v1.7.0, grpc v1.67.1 | `go.mod`, `go.sum` |
| `benchmarks/loadgen` | 1.22.0 (toolchain 1.24.7) | fabric-gateway v1.7.0, grpc v1.67.1 | `go.mod`, `go.sum` |

Chaincode image: `golang:1.22.5-bookworm` build stage, `gcr.io/distroless/static-debian12:nonroot` runtime (`blockchain/chaincode/hrbac-cc/Dockerfile`).

## Fabric

Hyperledger Fabric 2.5.16 (peer, orderer, CLI binaries), Fabric CA 1.5.13, cAdvisor v0.49.1, nicolaka/netshoot v0.13 (failover trials with injected delay). Image digests are in [BLOCKCHAIN_REPRODUCIBILITY.md](BLOCKCHAIN_REPRODUCIBILITY.md) and `blockchain/example-network/.env.example`. These pins are for the example network; the field deployment ran Fabric 2.5.1 on Raspberry Pi 4B (ARM64).

## Environment used to prepare the package

Ubuntu 24.04 container, Python 3.13.18, Go 1.24.7, g++ 13.3.0, Docker 29.9.2, R 4.3.3. The full Python suite, the C++ host tests, the three Go modules, the example Fabric network bring-up, the eight-step integration script, a one-level RBAC benchmark and one failover trial ran there. The ESP32 cross-compile did not (PlatformIO registry unreachable); CI performs it.
