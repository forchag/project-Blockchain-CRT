# Data availability and repository scope

Research datasets are not included in this code repository. The top-level `data/` directory and bundled `external/` source archive were removed from the current branch to keep the repository focused on firmware, gateway software, chaincode and analysis scripts.

Small illustrative fixtures in `examples/` are retained for executable demonstrations and tests. They are not original field evidence. `make reproduce` runs with those fixtures and public parameters alone.

## Separately supplied analysis inputs

Supply your own agronomic CSV directory with `--data-dir`:

```bash
python scripts/prepare_field_plots.py --data-dir /absolute/path/to/agronomic
python scripts/reconcile_field_water.py --data-dir /absolute/path/to/agronomic
make field-results FIELD_DATA=/absolute/path/to/agronomic
```

The scripts expect `plot_assignment.csv`, `per_plot_yield.csv`, `per_plot_water_application.csv`, `per_plot_labour.csv` and `flow_meter_readings.csv`, as applicable. Results are written under `results/manuscript/`; source values are not modified. Nothing is downloaded automatically.

The 2025 field/bench and 2026 cloud campaigns must be assessed separately. Removing their files from this repository does not resolve provenance questions or numerical discrepancies. See [evidence boundaries](EVIDENCE_BOUNDARIES.md).
