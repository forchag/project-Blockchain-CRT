# CRT-Based Multi-Channel Residue Transmission — reproducibility project

Code, tests, analytical models and example configuration for *CRT-Based Multi-Channel Residue Transmission with Gateway-Side Parallel Demodulation for Distributed Edge Blockchain Computing: A Scalable IoT Cluster Architecture for Arid Agriculture*.

**Purpose:** make the five-sensor firmware, the gateway protocol, the Hyperledger Fabric implementation, the benchmark procedures and the paper's analytical results reproducible.

## Repository scope

This repository contains firmware, gateway software, chaincode and analysis scripts, with supporting configuration, tests and documentation. The research data from the 2025 field deployment and the 2026 cloud experiments is withheld for privacy reasons and is not included; it is available from the corresponding author on reasonable request. Small illustrative fixtures in `examples/` support the software tests; they are invented, not field evidence.

See [data availability](docs/DATA_AVAILABILITY.md) for running the field analysis with separately supplied records.

## Architecture

In the field, the code in this repository ran on two kinds of hardware: ESP32 sensor nodes running `firmware/esp32/`, and Raspberry Pi boards running the gateway software and the Hyperledger Fabric peers. The nodes measure soil moisture, soil pH, temperature, humidity and light. The encoding uses three residues per valid quantity (moduli 97, 101 and 103), giving up to fifteen eight-byte LoRa residue frames per five-quantity reading on 868.1, 868.3 and 868.5 MHz. The gateway software in `gateway/` implements polling, acknowledgements, reconstruction, signing and Fabric submission. The example blockchain network in `blockchain/` runs a four-peer, three-orderer test environment with role-based authorization.

See [firmware](docs/FIRMWARE_REPRODUCIBILITY.md) and [evidence boundaries](docs/EVIDENCE_BOUNDARIES.md) for what the tests cover.

## Reproducibility status

| Goal | What is available now | Remaining validation |
|---|---|---|
| Code reproducibility | ESP32 source and tests; gateway MAC and submitter; chaincode and example Fabric network | Run full tests/CI; compare recovered deployment builds and configurations with source; resolve any mismatch with manuscript methods |
| Results reproducibility | Analytical scripts, expected analytical results, benchmark drivers and illustrative test inputs | Field and bench results need the original records, available from the corresponding author on request |

The paper's benchmark and field figures were measured on the original Raspberry Pi bench and in the 2025 field deployment. `make reproduce` prints them under their labels (`bench-measured`, `field-measured`) and never re-derives them from the analytical model. Treat results produced from formula assumptions as analytical, from the code as software tests, and from new runs as measurements of your own host.

## Quick start

Requirements: Python 3.13, a C++17 compiler, Go 1.22+; Docker for the optional Fabric integration.

```bash
make install
make check
make reproduce
```

Firmware host tests:

```bash
make -C firmware/esp32/tests
```

A cross-compiled ESP32 firmware build is handled by `firmware/esp32/build_firmware.sh` and `.github/workflows/build-firmware.yml`. An integration run against the **example** Fabric network is available via `make integration`; it may require Docker and additional system dependencies. Its outputs describe the host it runs on, not the 2025 deployment.

## Documentation

- [Architecture](docs/ARCHITECTURE.md)
- [Data availability](docs/DATA_AVAILABILITY.md)
- [Evidence limitations](docs/EVIDENCE_BOUNDARIES.md)
- [Firmware reproducibility](docs/FIRMWARE_REPRODUCIBILITY.md)
- [Gateway protocol](docs/GATEWAY_PROTOCOL.md)
- [Blockchain reproducibility](docs/BLOCKCHAIN_REPRODUCIBILITY.md)
- [Analytical reproducibility](docs/ANALYTICAL_REPRODUCIBILITY.md)
- [Results reproducibility](docs/RESULTS_REPRODUCIBILITY.md)
- [Software versions](docs/SOFTWARE_VERSIONS.md)
- [Illustrative inputs](examples/README.md)

## Research integrity and security

Keep illustrative fixtures and outputs of new runs separate from original field measurements. Never publish operational private keys, passwords, enrolment secrets, identifying participant information or certificate and ledger material.

## Analysis inputs

`make reproduce` uses public parameters and illustrative fixtures. To run the field analysis on your own agronomic CSVs, pass their directory:

```bash
make field-results FIELD_DATA=/absolute/path/to/agronomic
```

Nothing is downloaded automatically. Outputs go to the git-ignored `results/` directory. Software tests and illustrative outputs don't reproduce the field measurements.
