# Results reproducibility

## Analytical results
```bash
make reproduce
```
Reproduces LoRa airtime, CRT reconstruction, capacity and duty-cycle ceilings, recovery
probabilities, the Raft timing window and the economic identities from `config/` and `examples/`, and
validates them against `examples/expected_analytical_results.json`.

## Field plot results
```bash
make field-results FIELD_DATA=/absolute/path/to/agronomic
```
Joins the 12 plots of a supplied agronomic directory and runs the randomised complete block analysis
in `analysis/agronomic_statistics.R`. The scheduled-water input is `per_plot_water_application.csv`,
converted from litres to cubic metres; meter readings are never substituted for it. Without supplied
records, the R analysis runs on the invented `examples/agronomic_plots.example.csv`.

## Benchmark procedures
```bash
make network-up
make benchmark-smoke     # one-level RBAC comparison
make failover-smoke      # one recovery and one split-vote Raft trial
python benchmarks/rbac_overhead.py /absolute/path/to/throughput_runs.csv --out results/manuscript/rbac.csv
```
Runs on the example network measure your host and are labelled `example-network-run`. They reproduce
the method used for the paper's bench figures, not the figures, which were measured on the original
Raspberry Pi bench.
