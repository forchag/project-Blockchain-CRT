# CRT-Based Multi-Channel Residue Transmission — reproducibility project

Code, tests, analytical models and example configuration for *CRT-Based Multi-Channel Residue Transmission with Gateway-Side Parallel Demodulation for Distributed Edge Blockchain Computing: A Scalable IoT Cluster Architecture for Arid Agriculture*.

**Three goals:** (1) reproduce the five-sensor firmware, gateway protocol and Hyperledger Fabric implementation; (2) independently regenerate reported tables and figures from properly identified input datasets; (3) distinguish independently verified original 2025 records from reconstructed material and later experiments.

## Repositories and evidence

- **Software project (this repository):** source code, example network, tests, analytical reproduction and documentation.
- **Bundled candidate data:** [bundled dataset](external/CRT-Based-Multi-Channel-Residue/data). It contains field-labeled CSVs, calibration tables, peer/orderer logs, device logs, and Fabric material. **A file's location, date string, or `measured` label does not independently prove that it originated in the 2025 deployment.** Verify capture provenance before describing any item as authentic historical evidence.
- **Examples:** `examples/` contains illustrative inputs or transcriptions of published numbers. Never merge those files with observations or count them as original experiments.

The complete source snapshot is included in this repository under `external/CRT-Based-Multi-Channel-Residue/`. Follow [docs/DATA_INTEGRATION_PLAN.md](docs/DATA_INTEGRATION_PLAN.md) when selecting records for analysis. In particular, inspect the origin of files in `data/raw/` and preserve original source hashes. The software project can reproduce protocol behavior without establishing which firmware binary or configuration was deployed in 2025.

## Architecture implemented in the reference software

The firmware in `firmware/esp32/` handles soil moisture, soil pH, temperature, humidity and light. The reference encoding uses three residues per valid quantity (moduli 97, 101 and 103), giving up to fifteen eight-byte LoRa residue frames per five-quantity reading on 868.1, 868.3 and 868.5 MHz. The gateway reference in `gateway/` implements polling, acknowledgements, reconstruction, signing and Fabric submission. The example blockchain network in `blockchain/` runs a four-peer, three-orderer test environment with role-based authorization.

**Implementation fidelity is not deployment provenance:** passing the firmware, gateway and Fabric tests establishes the behavior of the current code. It does not prove these precise files or a particular binary operated the historical nodes. See [firmware scope](docs/FIRMWARE_REPRODUCIBILITY.md) and [evidence boundaries](docs/EVIDENCE_BOUNDARIES.md).

## Reproducibility status

| Goal | What is available now | Remaining validation |
|---|---|---|
| Code reproducibility | ESP32 source and tests; gateway MAC and submitter; chaincode and example Fabric network | Run full tests/CI; compare recovered deployment builds and configurations with source; resolve any mismatch with manuscript methods |
| Results reproducibility | Analytical scripts, expected analytical results, benchmark drivers and illustrative test inputs | Integrate eligible data; map source rows to tables/figures; run independent re-computation; report mismatches and missing inputs |
| Data authenticity | Candidate datasets and self-described provenance metadata in the bundled source snapshot | Verify original collection chain, creation history, checksums, source instruments/hosts and whether files were reprocessed or reconstructed |

The manuscript's benchmark and field figures are **reported claims pending per-source provenance verification** in this project. Treat results produced from formula assumptions as analytical, from reference code as software tests, and from new experiments as new results; do not substitute one for another.

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

- [Data integration: copy, link, quarantine, verify](docs/DATA_INTEGRATION_PLAN.md)
- [Data availability and the three research goals](docs/DATA_AVAILABILITY.md)
- [Evidence limitations](docs/EVIDENCE_BOUNDARIES.md)
- [Firmware reproducibility](docs/FIRMWARE_REPRODUCIBILITY.md)
- [Gateway protocol](docs/GATEWAY_PROTOCOL.md)
- [Blockchain reproducibility](docs/BLOCKCHAIN_REPRODUCIBILITY.md)
- [Analytical reproducibility](docs/ANALYTICAL_REPRODUCIBILITY.md)
- [Illustrative inputs](examples/README.md)

## Research integrity and security

Do not relabel reconstructed or newly generated files as original field measurements. Never publish operational private keys, passwords, enrollment secrets, identifying participant information or unreviewed certificate/ledger materials. Preserve file provenance and document discrepancies rather than changing numbers simply to match the manuscript.

## Integrated 2025 field and 2026 cloud data

The project now includes 66 **byte-identical** research files under `data/field_2025/`, `data/bench_experiments_2025/`, `data/cloud_experiments_2026/`, `data/modelled/`, and `data/source_processed/`. See [data/README.md](data/README.md) and the [full source inventory](data/manifests/SOURCE_FILE_INDEX.csv). The full source snapshot, including the large datasets, is tracked directly at [external/CRT-Based-Multi-Channel-Residue](external/CRT-Based-Multi-Channel-Residue). A normal clone includes these files; no submodule initialization or separate repository is needed.

The 2025 field/bench and 2026 cloud records belong to distinct campaigns, and their results must not be pooled. Historical label strings were preserved in `data/source_metadata/`; the new directory classification does not change any data value. Monte Carlo results are in `data/modelled/`, not filed as physical radio measurements.

```bash
make data-check
make field-results  # requires R; outputs are written to ignored results/manuscript/
```

Byte hashes verify faithful copying, not independent historical data acquisition. The source file index preserves the original import statuses and Git blob hashes. Files historically marked as externally pinned are now included in the bundled source snapshot.
