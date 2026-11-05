# Evidence boundaries

This repository demonstrates the encoding, the protocol and the arithmetic. The manuscript also reports bench and field measurements, but the records behind them are withheld for privacy reasons (see [DATA_AVAILABILITY.md](DATA_AVAILABILITY.md)). This document separates what the public code establishes from what only those records could.

A passing test shows the code works on public inputs. It doesn't reconstruct a field observation.

## What a passing run establishes

`make -C firmware/esp32/tests` builds the node's platform-independent code twice (FRAME_MODE 15 and 3) and runs 277 checks per build. When it passes, the CRT residues of every scaled value from 0 to 9,796 reconstruct from every pair and from the full triplet, every single-residue corruption of a triplet is detected, the frame codecs match fixed hex vectors, the POLL and ACK state machines take the documented decisions, and the slot schedule, sequence-number resume and sensor-fault classification behave as specified. The tests establish the behavior of this reference firmware; they do not prove which binary was flashed on any physical field node.

`python -m pytest tests` adds the gateway: residue intake, two-of-three reconstruction, duplicate suppression, retransmission requests, abandonment, the Ed25519 record signature, and the Go chaincode and submitter tests. Passing means a gateway built from this source handles the illustrative inputs in `examples/` as the protocol says. It does not re-run the field gateways against the field inputs, which are withheld.

`python analysis/reproduce_analytical_results.py` followed by `python analysis/validate_analytical_outputs.py` recomputes every result that follows from a published formula and stated parameters: Semtech time on air, CRT bounds, the independent-erasure recovery model, the M/D/1 stress ceiling, the downlink duty-cycle ceiling, the Raft election window, the economic identities. These numbers are reproduced in the full sense. Anyone with the formula and the parameters gets the same value, and the validator checks 47 of them against `examples/expected_analytical_results.json`.

`gateway/fabric-submitter/integration.sh` against the example Fabric 2.5.16 network shows that the software path works end to end on a real Fabric deployment: readings produced by the gateway code are endorsed, ordered and committed; an undelegated sensor is refused locally and by the chaincode; a peer outage and an orderer outage are survived without duplicate transactions; the ledger record carries a verifiable gateway signature. The transaction IDs and block numbers it prints belong to that run.

## What this repository does not re-derive

Bench-measured values (INA219 energy per packet, 40.4 and 54.9 TPS, 321.5 ms contract execution, 8.2 s Raft recovery, 600 residue packets per 60 s) were measured on the original bench hardware in the original deployment. The repository carries the procedures (`benchmarks/`) and prints the reported numbers under the label `bench-measured`. Running those procedures on other hardware produces other numbers, and those numbers describe that other hardware, not the original bench. The example network here, for instance, gave 17.5 TPS at concurrency 10 on a shared cloud container. That is a measurement of that container, not a substitute for the bench-measured 40.4 TPS, and it neither confirms nor contradicts it.

Field-measured values (1.22 s median endorsement latency, 99.4 % uptime, 10.5 % rejected writes, 9.8 % two-residue records, 40.4 / 34.6 % water reduction, 33.9 % labour reduction, the yield changes, the adaptive-mode logs) were measured in the 2025 Sfax deployment. Those records are withheld for privacy reasons, and the analytical-output tests are not a field replication. Where the study reports both a count and a ratio (15,414 of 146,311), the analysis checks that the ratio follows from the count and labels the result `derived (from field-measured)`: the arithmetic is public, the counts are not.

Measured values with no published derivation (the Table 10 hardware percentages, the 439 B ledger record estimate) are reported as measured. The repository offers no derivation for them; that reflects what can be published, not doubt about the measurement.

## How the labels enforce the boundary

Every number the analysis writes carries one of `analytical`, `bench-measured`, `field-measured`, `measured`, or `derived (from X)` naming the classes it was computed from (`analysis/common.py`). The console tags make the non-reproducible classes impossible to miss (`[bench-measured, not reproducible]`, `[field-measured, restricted data]`, where "restricted" means withheld for privacy). `analysis/validate_analytical_outputs.py` fails if any bench, field or measured quantity is relabelled `analytical` or `derived`, and `tests/test_reproducibility.py` fails if the traceability table classifies a field-measured result as anything other than "Reproducible only with restricted source data".

The console tag `not reproducible` in these labels means "not reconstructable from this repository alone". It does not mean "not measured". The bench and field figures were measured in the original deployment; the labels distinguish what this package can recompute from what it can only report.

Outputs of procedures you run yourself (benchmarks, failover trials, the integration script, the crypto micro-benchmark) carry `example-network-run` or `this-host-measurement`. They are measurements of your setup and are never merged with the study's values.

## Reading a result correctly

If a test named after a study quantity passes, the code that computes or handles that quantity works on public inputs. The corresponding field result is reported in the manuscript and rests on the withheld records. This repository demonstrates code and arithmetic; it does not authenticate a historical observation.
