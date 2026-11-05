# Results reproducibility — distinct workflows

## Analytical model
```bash
make reproduce
```
The analytical workflow reproduces mathematical LoRa airtime, CRT reconstruction, theoretical capacity, recoverability and modeled economic quantities from configuration and example inputs.

## 2025 field plot results
```bash
make data-check
make field-results
```

The field script creates `results/manuscript/field_plot_inputs.csv` by joining the 12 plots from `data/field_2025/agronomic/`. Its scheduled-water input is `per_plot_water_application.csv`, converted from litres to cubic metres; it does **not** silently substitute `flow_meter_readings.csv`. It produces a crop-by-treatment summary and invokes the existing R randomized-block analysis.

The output does **not** automatically prove agreement with the manuscript; compare the actual totals and methods and publish a discrepancy report where necessary.

## Benchmark and cloud

Example command for 2025 benchmark source:
```bash
python benchmarks/rbac_overhead.py data/bench_experiments_2025/benchmarks/throughput_runs.csv --out results/manuscript/rbac_2025.csv
```

Example command for independently conducted 2026 cloud benchmark:
```bash
python benchmarks/rbac_overhead.py data/cloud_experiments_2026/benchmarks/throughput_runs.csv --out results/manuscript/rbac_cloud_2026.csv
```

These outputs must remain separate; they reflect different hosts and experiments. The numbers need not match one another.

To run other analyses against the complete source dataset, use the bundled files under `external/CRT-Based-Multi-Channel-Residue/` and appropriate data paths with scripts that support explicit input arguments. Do not overwrite curated source data.

## Evidence integrity

`python scripts/verify_imported_data.py` checks exact source-file Git blob identity for the 66 imports. The source inventory documents the remaining large records by original path, SHA and pinned commit. Verification of historical acquisition provenance is a separate task.

## Manuscript reconciliation still required

Prepare a claim-to-evidence table for every manuscript table/figure: reported value, experiment group/year, input hashes, script/command, calculated value, tolerance, discrepancy, and conclusion. Preserve disagreements rather than silently correcting field measurements.

## Current 2025 agricultural reconciliation issue

A direct aggregation of the copied **2025 weekly application CSV** yields 92.2384 m³ (tomato treated), 111.8369 m³ (tomato control), 49.0773 m³ (pepper treated), and 58.1641 m³ (pepper control). That implies scheduled-water reductions of about 17.5% for tomatoes and 15.6% for peppers, rather than the manuscript's 40.4% and 34.6%. The flow-meter interval records also give totals near these imported weekly CSV values (approximately 92.1483, 111.4598, 49.0337 and 58.0737 m³ respectively). These are **within the 2025 data group**, not a 2026 cloud-vs-field discrepancy.

`python scripts/reconcile_field_water.py` writes `results/manuscript/water_reconciliation.csv` and prints both observed and manuscript reductions. It does **not** modify any source values. Determine whether the manuscript and files use different seasonal scope, measurement units, plot totals or datasets before resolving this difference. Use `--strict` to fail on discrepancies.
