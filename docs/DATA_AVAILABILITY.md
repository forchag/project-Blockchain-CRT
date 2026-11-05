# Data availability

The research data behind the manuscript is withheld for privacy reasons and is not included in this repository. That covers the 2025 field and bench records and the 2026 cloud experiment records: sensor readings, plot-level agronomic measurements, calibration records, device and gateway logs, ledger exports, certificates and benchmark traces. None of it is published here, linked from here, or derived into any file here.

What the repository does contain is software (firmware, gateway, chaincode, analysis and benchmark scripts), configuration, tests and small illustrative fixtures in `examples/`. Those fixtures were written for the tests; their values are invented or computed from the protocol code, and none was copied, sampled or anonymised from the withheld records. `make reproduce` runs on the fixtures and public parameters alone.

## Running the analysis on your own inputs

The field-analysis scripts take an explicit input directory with `--data-dir`. Nothing is downloaded automatically.

```bash
python scripts/prepare_field_plots.py --data-dir /absolute/path/to/agronomic
```

```bash
python scripts/reconcile_field_water.py --data-dir /absolute/path/to/agronomic
```

```bash
make field-results FIELD_DATA=/absolute/path/to/agronomic
```

Run a script with `--help` for its arguments. Results are written under the git-ignored `results/manuscript/`, and the scripts never modify their inputs. Keep your own observations outside the repository, record their SHA-256 hashes and collection metadata alongside them, and don't adjust values to match manuscript figures.

Results from the 2025 field and bench work and from the 2026 cloud experiments come from different hosts and campaigns. Analyse them separately; their numbers need not agree. See [evidence boundaries](EVIDENCE_BOUNDARIES.md) for what the public code can and can't establish without the withheld data.
