# Data availability — 2025 field, 2025 bench and 2026 cloud

The code and a curated set of **66 byte-identical dataset copies** are now in this repository's `data/` directory. The complete source files remain in [forchag/CRT-Based-Multi-Channel-Residue](https://github.com/forchag/CRT-Based-Multi-Channel-Residue/tree/f21339dcdf5d8cb3ba73d61b145baa2d6b4ca384/data), pinned as an optional Git submodule at `external/CRT-Based-Multi-Channel-Residue`.

## Experiment groups

| Directory | What the collection represents |
|---|---|
| `data/field_2025/` | 2025 sensor calibration, field operations, agronomic, radio and power records |
| `data/bench_experiments_2025/` | 2025 benchmark/recovery source records and some research assumptions |
| `data/cloud_experiments_2026/` | Separate 2026 virtual/cloud-host benchmark, crypto, gateway and failover experiments |
| `data/modelled/` | Simulation results, not physical captures |
| `data/source_processed/` | Source-supplied summary outputs, requiring regeneration from raw source inputs |
| `data/source_metadata/` | Unmodified original MANIFEST and labels |
| `data/manifests/` | Source paths, hashes, classifications, copy status and immutable source commit |

The author confirms that the 2025 field collection comes from the field deployment and that there was an additional cloud experiment in 2026. That distinction is retained. This repository records the author's origin declaration and exact source hashes; it does not independently certify a particular sensor capture, firmware flash event or collection chain.

## Integrity

```bash
python scripts/verify_imported_data.py
```

Each local imported blob is compared with its Git blob hash from the pinned source commit. All 66 staged files were compared at the time of migration, and matched exactly. Large records were not replicated: the source index links them through the optional submodule.

## Safe data handling

The source contains large Fabric ledger blockfiles and certificate/identity directories. Do not bulk-copy or republish them here without reviewing identity exposure, confidential data and access rights. This project's ordinary tracked `data/` directories contain selected CSV, JSON, YAML and text records; generated Fabric keys and private material are prohibited.

## Reproducibility boundaries

- Code tests prove how the available implementation behaves.
- Analytical models reproduce formula-based results with documented assumptions.
- Input-specific scripts can reproduce descriptive and statistical outputs from the staged data.
- Comparisons between 2025 ARM64 field/bench experiments and 2026 cloud-host runs must identify the different environments.
- Data acquired in 2026 must never be represented as evidence captured in the 2025 field trial.
- Source-provided output is not the same as a freshly regenerated result.

See [data/README.md](../data/README.md), [RESULTS_REPRODUCIBILITY.md](RESULTS_REPRODUCIBILITY.md), and [PROVENANCE_AUDIT.md](PROVENANCE_AUDIT.md).
