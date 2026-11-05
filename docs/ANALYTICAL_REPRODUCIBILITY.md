# Analytical reproducibility

```bash
python analysis/reproduce_analytical_results.py
```

```bash
python analysis/validate_analytical_outputs.py
```

The first command recomputes every result calculable from public parameters and writes `results/analytical/` (git-ignored): `analytical_results.json` and `.csv` with one labelled row per quantity, per-section tables (`capacity.csv`, `energy_framings.csv`, `packet_loss_recovery.csv`, `collision_analysis.csv`, `transmission_cost_index.csv`, `economic_cases.csv`), the R example output when `Rscript` is installed, `SUMMARY.md`, and two SVG figures (`figures/recovery_probability.svg`, `figures/airtime.svg`) when matplotlib is installed. The second compares 47 values and their labels with `examples/expected_analytical_results.json` and fails if any bench, field or measured quantity is labelled analytical. `make reproduce` runs both. The run takes a few seconds and reads nothing outside `config/` and `examples/`.

Parameters live in `config/analytical_parameters.yaml`, `config/radio.example.yaml` (capacity block) and `config/economics.example.yaml`. Values marked `assumed` there are illustrative and say so in the output notes.

> **How to read the evidence labels.** Throughout this document, `not reproducible` / `not reconstructable` means *not reconstructable from this branch alone*. It does not establish whether a value was measured. Candidate benchmark and field files are publicly present in a separate repository, but whether they are unmodified original 2025 captures requires verification under [DATA_AVAILABILITY.md](DATA_AVAILABILITY.md) and [PROVENANCE_AUDIT.md](PROVENANCE_AUDIT.md). `derived` means computed from other published values. `assumed` means an illustrative placeholder chosen for the example.

## 9.1 CRT encoding [analytical]

Scaling `x = round((v + offset) x factor)` with the per-quantity offset and factor of `gateway/mac/protocol.py`; residues `x mod (97, 101, 103)`; reconstruction by Garner's mixed-radix method from any subset of at least two residues. The product of the three moduli is 1,009,091; the smallest pairwise product, 97 x 101 = 9,797, bounds the scaled values (the largest is 9,796, illuminance at 48,980 lx). Reported: the worked example 25.3 °C, which scales to 653 and gives residues (71, 47, 35), reconstructing from each pair and the triplet; the independence case 5 and 9,802 = 5 + 97 x 101, which share residues mod 97 and 101 and differ mod 103; an exhaustive check over all 9,797 values and four residue subsets; zero undetected single-residue corruptions; and the failure statuses for one residue (INSUFFICIENT) and a residue at or above its modulus (BAD_RESIDUE).

## 9.2 LoRa airtime [analytical]

Semtech AN1200.13 with SF9, BW 125 kHz, CR 4/5, explicit header, CRC on, LDRO off, 8-symbol preamble; symbol time 4.096 ms.

| Frame | Airtime |
|---|---|
| 8-byte residue | 123.904 ms |
| 10-byte POLL, 12-byte channel frame | 144.384 ms |
| 13-byte compact fixed-point | 164.864 ms |
| 34-byte raw reference | 246.784 ms (manuscript: 246.8) |
| 15-packet CRT reading | 1,858.56 ms |

Per-packet reduction (246.784 - 123.904) / 246.784 = 49.79 % `[derived (from analytical)]`; per-reading ratio 1,858.56 / 246.784 = 7.53x.

## 9.3 Transmission energy

The analytical model is `E = V x I_tx x T_air`, with V = 3.3 V and an assumed I_tx = 29 mA (SX1276 typical at +13 dBm on RFO; the node's +10 dBm PA_BOOST setting has no datasheet typical), so P_tx = 95.7 mW and an 8-byte frame costs 11.86 mJ `[analytical]`. Ratios between frame types equal airtime ratios at constant power. A complete-node model per 1800 s cycle adds sleep (150 µA), awake (40 mA for 3 s) and receive windows (10.8 mA for 2 s) at 3.7 V, all assumed; it reports radio TX at about 10 % of node energy under those assumptions. Radio-only and complete-node energy are separate outputs.

The manuscript's 12.32 / 24.86 / 16.36 mJ per residue / raw / fixed-point packet and 184.8 mJ per reading were measured on the original bench. The script reads them from `examples/bench_energy_values.example.yaml` and prints them as `[bench-measured, not reproducible]`; the 7.43x energy ratio is `derived (from bench-measured)`. If that file is absent, the script prints "historical bench value not included" in their place and continues.

## 9.4 Reliability [analytical]

Independent erasures with per-frame loss p: `P_q = (1 - p)^3 + 3p(1 - p)^2`, `P_reading = P_q^5`. At p = 10 / 20 / 30 %: per quantity 97.2 / 89.6 / 78.4 %, per reading 86.8 / 57.7 / 29.6 %. With r retransmission rounds of the missing residues the exact distribution is enumerated (`p_quantity_with_retries`). Structured single-channel loss gives 100 % recovery: checked exhaustively for each lost channel over all scaled values, because any two residues determine x < 9,797.

## 9.5 Capacity and duty cycle [analytical capacity, not measured field capacity]

Uplink stress ceiling: M/D/1 per channel with deterministic service T and utilisation `rho = N k T / (C W)`; the largest stable load (rho < 1) in W = 60 s over C = 3 channels is N = 96 readings for the CRT scheme (k = 15, T = 123.904 ms) and 729 for raw frames (k = 1, T = 246.784 ms). `capacity.csv` adds mean queueing delay `rho T / (2(1 - rho))` at example loads.

Downlink duty-cycle node ceiling: per node and 1800 s cycle the gateway sends one POLL and five ACKs. `N = floor(G x d x W / (T(POLL) + 5 T(ACK)))` with G = 4 gateways, d = 1 % (ETSI EN 300 220-2, sub-band g1), W = 1,800 s. An 8-byte POLL (the manuscript's assumption) gives 743.4 ms per node and 96 nodes; the implemented 10-byte POLL gives 94. `capacity.csv` tabulates POLL sizes 8 to 16 bytes. Polling-frame size, ACK size, ACKs per node, retransmission share, window, duty limit and gateway count are configurable in `config/radio.example.yaml`.

`analysis/collision_analysis.py` adds TDMA slot margins (13 slots per window, positive slack after the worst-case retry sequence, guard and RTC drift), a listen-before-talk collision estimate for event reports (event rate and hidden-node share are assumed inputs) and external pure-ALOHA interference fed through the recovery model.

## 9.6 Transmission cost index

`TCI = 0.5 E/max E + 0.3 A/max A + 0.2 P/max P`, every component per complete reading. The CRT processing time is a per-packet figure and is multiplied by 15 before normalisation; `tests/test_analysis.py::test_tci_processing_is_normalised_per_reading_not_per_packet` asserts that conversion and that skipping it changes the index. Processing inputs are illustrative placeholders because the manuscript's processing-time measurements are not released, so TCI values demonstrate the method only.

## 9.7 Economics [derived from public tariff assumptions]

Water benefit = m³ saved x tariff / exchange rate; annual = season x 2; net = gross - opex; ROI = net / capex; payback = capex / net; NPV = -capex + net x sum over 5 years of 1/1.08^t (annuity factor 3.99271). Defaults: 0.100 TND/m³ with 0.050 / 0.150 / 0.300 sensitivity, 3.1 TND/USD, 8 %, two seasons, capex $3,540, opex $400. The manuscript associates the season inputs with the 2025 field experiment; the values used in `config/economics.example.yaml` are illustrative placeholders, so the output is a worked example, not a re-derivation of the field cost observation. The one check needing no field data: the published ROI 99 % implies payback 1/0.99 = 1.0101 yr, matching the published 1.01 yr.

## 9.8 Agronomic statistics

`analysis/agronomic_statistics.R` fits `y ~ block + treatment` per crop for yield, scheduled water and labour per hectare in a 3-block RCBD (treatment F test on 1 and 2 df) and writes means, differences, F, p and 95 % intervals. It runs on `examples/agronomic_plots.example.csv`, twelve invented plots. Its output demonstrates the statistical procedure; it is not a re-derivation of the field's 40.4 % / 34.6 % water, 33.9 % labour or yield results, for which original source plot records must be supplied separately.

## Raft timing window [analytical]

etcdraft draws each follower's election timeout uniformly from 10 to 19 ticks of 500 ms. After a sudden leader loss with two surviving followers, the first timer to expire triggers the election: 4.5 to 9.5 s after the failure (mean 6.2 s by exact enumeration, vote round trip not modelled), and the first block follows within the 500 ms batch timeout, so recovery should fall below about 10 s. The manuscript's 8.2 s mean (sd 2.1 s, n = 20) is printed as bench-measured. `analysis/raft_recovery.py` also runs the source repository's statistics pipeline (timestamp validation, duration recomputation, descriptive statistics, Wilson intervals for split votes) on `examples/example_recovery_trials.csv` or on the output of `benchmarks/run_failover.py`.

## Reported, not re-derived

Section N of the output prints every bench-measured, field-measured and measured headline value from `examples/manuscript_reported_values.yaml` under its label, and checks the arithmetic that links published numbers: 15,414 / 146,311 = 10.535 %, 12,833 / 130,897 = 9.804 %, 1 - 9.3 / 1,464 = 99.365 %, 146,311 / 61 days = 0.0278 TPS, and (54.9 - 40.4) / 54.9 = 26.41 % against the published 26.5 %. Consistent ratios show the published numbers agree with one another; the underlying counts must be reconciled against the field datasets linked by [DATA_AVAILABILITY.md](DATA_AVAILABILITY.md).
