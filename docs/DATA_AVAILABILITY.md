# Data availability and repository scope

Research datasets are not included in this code repository. It holds firmware, gateway software,
chaincode, an example Fabric network, benchmark procedures and analysis scripts, with configuration,
tests and documentation.

The field and bench records behind the paper's measured results, including the per-transaction
latency extract and the field orderer configuration, are withheld from this repository for privacy
reasons. They are available from the corresponding author on reasonable request. The complete peer
logs, the orderer configuration of the controlled benchmarks and the per-trial leader-failure records
were not retained.

Small illustrative fixtures in `examples/` support executable demonstrations and tests. They are not
field evidence. `make reproduce` runs with those fixtures and public parameters alone.

## Running the field analysis on supplied records

```bash
python scripts/prepare_field_plots.py --data-dir /absolute/path/to/agronomic
python scripts/reconcile_field_water.py --data-dir /absolute/path/to/agronomic
make field-results FIELD_DATA=/absolute/path/to/agronomic
```

The scripts expect `plot_assignment.csv`, `per_plot_yield.csv`, `per_plot_water_application.csv`,
`per_plot_labour.csv` and, where available, `flow_meter_readings.csv`. Results are written under
`results/manuscript/`; input values are never modified and nothing is downloaded.
