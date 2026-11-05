# Results workflows

Three workflows produce three kinds of result, and their outputs stay separate.

## Analytical model

```bash
make reproduce
```

Recomputes LoRa airtime, CRT reconstruction, theoretical capacity, recovery probability and the economic identities from `config/` and `examples/`. No withheld data is needed. Details in [ANALYTICAL_REPRODUCIBILITY.md](ANALYTICAL_REPRODUCIBILITY.md).

## Field plot analysis

```bash
make field-results FIELD_DATA=/absolute/path/to/agronomic
```

`scripts/prepare_field_plots.py` joins twelve plots from the agronomic CSVs in the directory you pass and writes `results/manuscript/field_plot_inputs.csv`. Scheduled water is converted from litres to cubic metres. The target then produces a crop-by-treatment summary and runs the randomized-block analysis in `analysis/agronomic_statistics.R`.

`python scripts/reconcile_field_water.py --data-dir /absolute/path/to/agronomic` compares scheduled and metered water against the manuscript's reported reductions and writes `results/manuscript/water_reconciliation.csv`. `--strict` makes it exit non-zero on any discrepancy.

The 2025 field records the manuscript's agronomic results come from are withheld for privacy reasons, so these commands need inputs you supply. Their output describes those inputs; a run on other data neither confirms nor contradicts the published figures.

## Benchmarks

```bash
python benchmarks/rbac_overhead.py /absolute/path/to/throughput_runs.csv --out results/manuscript/rbac.csv
```

The input is the aggregate written by `benchmarks/run_rbac_comparison.sh`. Keep runs from different hosts or campaigns (for example, bench hardware and cloud hosts) in separate input files and separate outputs; their throughput numbers measure different machines and needn't match.

## Comparing with the manuscript

For each manuscript table or figure you check, record the reported value, the campaign and year, the SHA-256 of each input, the command, the computed value, the tolerance and the discrepancy. Report disagreements as they are rather than adjusting inputs. A matching hash shows the bytes are unchanged since hashing; it says nothing about how they were collected.
