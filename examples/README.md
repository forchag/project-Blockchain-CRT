# Example inputs

Illustrative data for testing code. Not original field evidence.

Everything in this directory was written for the public reproducibility package. Values are invented
or computed from the protocol code; none was copied, sampled, anonymised or regenerated from the
field deployment's records. Identifiers carry an `ex-` / `EX-` prefix, timestamps fall in 2030, and
transaction IDs are `example-txid-NNNN` placeholders, so no file can be mistaken for a log, a ledger
export or a measurement from the 2025 campaign. `tests/test_public_data_safety.py` enforces this.

| File | What it is | Used by |
|---|---|---|
| `crt_test_vectors.csv` | Self-authored CRT vectors: scaling, residues mod (97, 101, 103), reconstruction from every permitted subset, the 653 worked example and the 5 / 9,802 independence case | `tests/test_crt.py` |
| `five_sensor_example.csv` | Six invented five-quantity readings, physically plausible for an arid plot | `benchmarks/fixed_point_baseline.py`, schema test |
| `packet_encoding_examples.json` | Residue, channel, POLL and ACK frames (hex) for one invented reading of node 17, sequence 470 | `tests/test_protocol_vectors.py` |
| `example_gateway_events.jsonl` | Replayable uplink/timer events: clean quantities, a two-residue decision, a retransmission request, a corrupted residue, a duplicate | `tests/test_gateway_mac.py::test_replay_example_events` |
| `example_transactions.json` | `WriteSensor` submissions with placeholder txids and the decision `hrbac-cc` must return | `blockchain/chaincode/hrbac-cc/policy_test.go`, `tests/test_blockchain_integration.py` |
| `example_benchmark_config.yaml` | One-level, one-repetition variant of the benchmark workload (about 3 minutes) | `benchmarks/run_rbac_comparison.sh` via `WORKLOAD=` |
| `example_benchmark_runs.csv` | Invented throughput trials in the aggregate schema | `benchmarks/rbac_overhead.py`, `tests/test_analysis.py` |
| `example_recovery_trials.csv` | Invented Raft recovery trials (one deliberately unsuccessful) | `analysis/raft_recovery.py`, `tests/test_analysis.py` |
| `agronomic_plots.example.csv` | Invented 12-plot RCBD layout (3 blocks x 2 crops x 2 treatments) | `analysis/agronomic_statistics.R` |
| `calibration/*.csv` | Invented calibration coefficients for the 12 example sensors | `firmware/esp32/tools/gen_calibration_nvs.py --allow-illustrative`, `tests/test_calibration_nvs.py` |
| `expected_analytical_results.json` | Expected output of the analytical reproduction, with tolerances and labels | `analysis/validate_analytical_outputs.py` |
| `bench_energy_values.example.yaml` | The manuscript's **bench-measured** energy figures, transcribed and labelled; not reproducible here | `analysis/energy_reconciliation.py` |
| `manuscript_reported_values.yaml` | The manuscript's bench-measured, field-measured and measured headline values, transcribed and labelled for traceability | `analysis/reproduce_analytical_results.py` (section N) |

The two transcription files are the only place manuscript measurements appear. They hold published
numbers, not records: no raw trace, log line, ledger entry or per-plot value stands behind them in
this repository. The analysis prints them under their labels and never substitutes them for, or
derives them from, the analytical results.

Do not copy the separate [candidate dataset](https://github.com/forchag/CRT-Based-Multi-Channel-Residue/tree/main/data) into `examples/`. Its entries must pass the verification process described in [docs/DATA_INTEGRATION_PLAN.md](../docs/DATA_INTEGRATION_PLAN.md). This directory remains illustrative even if the candidate dataset is integrated elsewhere.

Regenerate nothing here from private data. To test with your own measurements, write them to a new
file outside `examples/` and point the relevant script at it (`--trials`, `--config`, `--plots`,
`--bench`); the outputs then describe your data, under your provenance label.
