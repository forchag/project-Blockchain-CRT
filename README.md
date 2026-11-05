# CRT-Based Multi-Channel Residue Transmission — reproducibility project

Code, tests, analytical models and example configuration for *CRT-Based Multi-Channel Residue Transmission with Gateway-Side Parallel Demodulation for Distributed Edge Blockchain Computing: A Scalable IoT Cluster Architecture for Arid Agriculture*.

The package has two aims: make the five-sensor firmware, gateway protocol and Hyperledger Fabric implementation reproducible, and recompute every result that follows from published formulas and parameters. Field and bench results depend on data that is withheld for privacy reasons, so they are reported under their labels rather than re-derived.

## Repository scope

This repository contains firmware, gateway software, chaincode and analysis scripts, with supporting configuration, tests and documentation. The research data from the 2025 field deployment and the 2026 cloud experiments is withheld for privacy reasons and is not included. Small illustrative fixtures in `examples/` support the software tests; they are invented, not field evidence.

See [data availability](docs/DATA_AVAILABILITY.md) for running the analysis on your own inputs. The software reproduces protocol behavior; it doesn't establish which firmware binary or configuration was deployed in 2025 or during the 2026 cloud experiments.

## Architecture implemented in the reference software

The firmware in `firmware/esp32/` handles soil moisture, soil pH, temperature, humidity and light. The reference encoding uses three residues per valid quantity (moduli 97, 101 and 103), giving up to fifteen eight-byte LoRa residue frames per five-quantity reading on 868.1, 868.3 and 868.5 MHz. The gateway reference in `gateway/` implements polling, acknowledgements, reconstruction, signing and Fabric submission. The example blockchain network in `blockchain/` runs a four-peer, three-orderer test environment with role-based authorization.

**Implementation fidelity is not deployment provenance:** passing the firmware, gateway and Fabric tests establishes the behavior of the current code. It does not prove these precise files or a particular binary operated the historical nodes. See [firmware scope](docs/FIRMWARE_REPRODUCIBILITY.md) and [evidence boundaries](docs/EVIDENCE_BOUNDARIES.md).

## Reproducibility status

| Goal | What is available now | Remaining validation |
|---|---|---|
| Code reproducibility | ESP32 source and tests; gateway MAC and submitter; chaincode and example Fabric network | Run full tests/CI; compare recovered deployment builds and configurations with source; resolve any mismatch with manuscript methods |
| Results reproducibility | Analytical scripts, expected analytical results, benchmark drivers and illustrative test inputs | Map inputs to tables/figures; run re-computation; report mismatches |
| Data | Withheld for privacy reasons; not in this repository | Re-computation of field and benchmark results requires access to the withheld records |

The manuscript's benchmark and field figures are reported here, not re-derived, because the records behind them are withheld. Treat results produced from formula assumptions as analytical, from reference code as software tests, and from new experiments as new results; do not substitute one for another.

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

A cross-compiled ESP32 firmware build is handled by `firmware/esp32/build_firmware.sh` and `.github/workflows/build-firmware.yml`. An integration run against the **example** Fabric network is available via `make integration`; it may require Docker and additional system dependencies. Generated outputs are not historical 2025 observations.

## Documentation

- [Architecture](docs/ARCHITECTURE.md)
- [Data availability](docs/DATA_AVAILABILITY.md)
- [Evidence limitations](docs/EVIDENCE_BOUNDARIES.md)
- [Firmware reproducibility](docs/FIRMWARE_REPRODUCIBILITY.md)
- [Gateway protocol](docs/GATEWAY_PROTOCOL.md)
- [Blockchain reproducibility](docs/BLOCKCHAIN_REPRODUCIBILITY.md)
- [Analytical reproducibility](docs/ANALYTICAL_REPRODUCIBILITY.md)
- [Results workflows](docs/RESULTS_REPRODUCIBILITY.md)
- [Software versions](docs/SOFTWARE_VERSIONS.md)
- [Illustrative inputs](examples/README.md)

## Research integrity and security

Don't relabel generated files as field measurements. Never commit operational private keys, passwords, enrollment secrets, participant information, certificates or ledger exports; `tests/test_public_data_safety.py` and the `public-safety` workflow check for them. Document discrepancies rather than changing numbers to match the manuscript.

## Analysis inputs

`make reproduce` uses public parameters and illustrative fixtures. To run the field analysis on your own agronomic CSVs, pass their directory:

```bash
make field-results FIELD_DATA=/absolute/path/to/agronomic
```

Nothing is downloaded automatically. Outputs go to the git-ignored `results/` directory. Software tests and illustrative outputs don't verify historical measurements.
