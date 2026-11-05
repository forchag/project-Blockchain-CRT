# Blockchain reproducibility

## Scope

The manuscript describes a multi-host deployment with a Fabric CA, certificates and identities. This repository pins exact component versions, re-creates that topology single-host on `blockchain/example-network`, and generates fresh test material for it. The re-creation matches the field deployment in chaincode, endorsement policy, channel configuration and identity model; it differs in host layout (one host instead of several), in image pinning (the field network's Fabric 2.5 patch level was not recorded) and in chaincode packaging (chaincode-as-a-service here, which the field deployment was migrated to during its life). No certificate, key, secret or ledger record from the field deployment is included, and none is derived from it. Benchmark and field results are reported in the paper; their records are withheld from this repository for privacy reasons and are available from the corresponding author on reasonable request (see [DATA_AVAILABILITY.md](DATA_AVAILABILITY.md)).

## Pinned versions

| Component | Version | Image digest (multi-arch index) |
|---|---|---|
| Hyperledger Fabric peer | 2.5.16 | `hyperledger/fabric-peer@sha256:09ee75042de9983bfde31ca88a5bf033386351f10a990e4c48264ee50172dee0` |
| Hyperledger Fabric orderer | 2.5.16 | `hyperledger/fabric-orderer@sha256:e322c57331d37e0a35ffae3cb3d3265a0e852211c0f801f2514cc15b964ffc93` |
| Hyperledger Fabric CA | 1.5.13 | `hyperledger/fabric-ca@sha256:7e9ae6f0e9be95e300206228c67501a57521aa115369823de4aae592c5e468ec` |
| cAdvisor | v0.49.1 | `gcr.io/cadvisor/cadvisor@sha256:3cde6faf0791ebf7b41d6f8ae7145466fed712ea6f252c935294d2608b1af388` |
| Fabric CLI binaries | 2.5.16 (peer, configtxgen, osnadmin, cryptogen) | `hyperledger-fabric-linux-amd64-2.5.16.tar.gz` from the GitHub release |
| fabric-ca-client | 1.5.13 | `hyperledger-fabric-ca-linux-amd64-1.5.13.tar.gz` |
| Chaincode | hrbac-cc 1.0, Go 1.22, fabric-contract-api-go v2.0.0, fabric-chaincode-go v2.0.0 | built from `blockchain/chaincode/hrbac-cc` (golang:1.22.5-bookworm, distroless static-debian12) |
| Client SDK | fabric-gateway v1.7.0 (Go) | `gateway/fabric-submitter/go.mod`, `benchmarks/loadgen/go.mod` |

They are recorded in `blockchain/example-network/.env.example`; `docker compose` pulls `image:tag@digest`, so a rebuild uses identical images. `pin_images.sh --check` compares `.env` with the registries without changing anything; `pin_images.sh` rewrites the digests.

The field deployment ran Fabric 2.5 (patch level not recorded) with ECDSA P-256 MSP identities. The original benchmark network ran Fabric 3.0.0 with Ed25519 MSP identities. This example network follows the field line: Fabric 2.5.16, channel capability V2_0, application capability V2_5, orderer capability V2_0, ECDSA P-256 identities from cryptogen. Moving the configuration from 3.0 to 2.5 required removing the 3.x-only `General.Throttling` block from `orderer.yaml`; nothing else in `core.yaml` or `orderer.yaml` changed meaning. The 2.5.16 patch pin, the digest pinning and the chaincode-as-a-service packaging are the main updates from the field deployment's last recorded state.

## Network

Single host, Docker Compose project `agri-bench`, network `agri-bench`:

- three etcdraft orderers `osn0..2.orderer.bench` (ports 7050 / 8050 / 9050, admin 7053 / 8053 / 9053, operations 9450..9452), TickInterval 500 ms, ElectionTick 10, HeartbeatTick 1;
- four peers, one per organisation: `peer0.north.bench:7051` (NorthMSP), `peer0.south.bench:8051`, `peer0.east.bench:9051`, `peer0.west.bench:10051`, LevelDB state, Fabric Gateway enabled;
- channel `benchchannel` created through the channel participation API (no system channel), BatchTimeout 500 ms, MaxMessageCount 100, AbsoluteMaxBytes 10 MB, PreferredMaxBytes 2 MB, endorsement policy MAJORITY (3 of 4 organisations);
- `hrbac-cc` as chaincode-as-a-service on port 9999, one server for all four peers; `HRBAC_ENFORCE` is the only difference between the RBAC-on and RBAC-off benchmark conditions;
- `ca.north.bench:7054` (Fabric CA, signing with the cryptogen NorthMSP CA) for identities carrying `role`, `zone`, `sensors` attributes;
- cAdvisor on 8080 for per-container resource samples.

Each container is limited to 2 CPUs and 2 GB of memory (chaincode 1 GB).

## Bringing it up

Prerequisites: Docker Engine with Compose v2, Go 1.22 or newer, Python 3.13 with `requirements-lock.txt`, `openssl`, and the Fabric binaries on `PATH`:

```bash
curl -sSL https://raw.githubusercontent.com/hyperledger/fabric/main/scripts/install-fabric.sh | bash -s -- binary 2.5.16 1.5.13
```

```bash
export PATH="$PWD/bin:$PATH"
```

From the repository root (or `make network-up`, which runs the same steps):

```bash
cp blockchain/example-network/.env.example blockchain/example-network/.env
```

Edit `CA_ADMIN_PASSWORD` in `.env`, then:

```bash
blockchain/example-network/network.sh up
```

```bash
blockchain/example-network/network.sh channel
```

```bash
CC_PREBUILT=1 blockchain/example-network/network.sh deploy
```

```bash
blockchain/example-network/network.sh identity
```

```bash
blockchain/example-network/network.sh gateway-identity ex-gw-north
```

`up` runs cryptogen (fresh ECDSA P-256 certificates and keys under `crypto/`, git-ignored) and starts the containers. `channel` creates the genesis block with configtxgen and joins three orderers and four peers. `deploy` packages, installs, approves and commits `hrbac-cc` as ccaas and starts its container; `CC_PREBUILT=1` builds the Go binary on the host instead of inside Docker, which helps behind TLS-intercepting proxies. `identity` registers `bench-sns-001` (role Sensor, zone North) with a random secret; `gateway-identity` registers the gateway with role Gateway and the sensor roster of its zone taken from `config/deployment.example.yaml`. Enrollment material lands in `fabric-ca/` (git-ignored). No certificate, key or secret is derived from the field deployment, and none is committed. `network.sh down` removes containers, volumes and all generated material.

## What can be demonstrated

| Demonstration | Command | Outcome on the example network |
|---|---|---|
| Valid example sensor transactions committed | `gateway/fabric-submitter/integration.sh` steps 1, 4, 5 | readings from the gateway MAC code (FRAME_MODE 15 and 3) commit VALID |
| Unauthorised transactions rejected | steps 2 and 3 | ex-sns-004 refused locally; the chaincode returns `SENSOR_NOT_DELEGATED` when the client check is bypassed |
| Gateway signing and verification | step 8 | the ledger record's Ed25519 signature verifies against the gateway's signed reading |
| Transaction IDs and outcomes recorded | `results/submitter-*/receipts.jsonl` | one receipt per reading: reading ID, txid, VALID or REJECTED, block |
| Outage handling without duplicates | steps 4 to 6 | peer outage and orderer outage survived; one transaction per reading; duplicate source lines skipped |
| Throughput and latency workflow | `benchmarks/run_throughput.sh` | per-trial `transactions.csv`, `summary.json`, `resources.csv`; aggregate `throughput_runs.csv` |
| RBAC overhead workflow | `benchmarks/run_rbac_comparison.sh` | baseline and hrbac trials interleaved; `rbac_overhead.csv` |
| Raft leader failure recovery | `python benchmarks/run_failover.py --recovery N --split M` | `recovery_trials.csv`, `split_vote_trials.csv`, per-trial orderer logs; `analysis/raft_recovery.py` summarises them |

All eight integration steps passed while preparing this package. A smoke benchmark (`WORKLOAD=examples/example_benchmark_config.yaml`, concurrency 10, one repetition) completed with 17.5 TPS in both conditions, and one abrupt-kill failover trial recovered in 6.2 s (election 5.1 s). These runs exercised the procedures on a shared cloud container. They are outcomes of this re-creation, labelled `example-network-run`, and their numbers depend on the host they ran on. They are not the manuscript's figures.

## Bench and field results reported in the manuscript

The bench-measured throughput (40.4 TPS with enforcement on, 54.9 TPS off, 26.5 % overhead), contract-execution latency (321.5 ms) and Raft-recovery time (8.2 s mean, sd 2.1 s, n = 20) come from the deployed four-peer Raspberry Pi network. The field-measured latencies (1.22 s median endorsement, ~27 min median commit) come from the field deployment's ledger logs. These values were measured in the original deployment; the raw records are withheld for privacy reasons ([DATA_AVAILABILITY.md](DATA_AVAILABILITY.md)). The example network runs the same measurement procedures against the same chaincode and channel configuration, so it reproduces the *method* used to obtain those values; it does not reproduce the hardware, and its own numbers describe the host it runs on. The example network uses the 500 ms batch timeout of an earlier configuration summary. The paper reports a 2 h timeout for the field channel, which the field ledger's blocks of exactly 100 transactions support. Change `BatchTimeout` in `configtx.yaml` to study either.

## Tests without a network

```bash
(cd blockchain/chaincode/hrbac-cc && go test ./...)
```

```bash
(cd gateway/fabric-submitter && go test ./...)
```

```bash
python -m pytest tests/test_blockchain_integration.py
```

The chaincode tests check the write policy (12 cases), record building and validation (null handling, determinism, rejection of spoofed attribution fields and out-of-range values), the gateway signature passthrough, and every submission in `examples/example_transactions.json`. The submitter tests run against a fake ledger that, like Fabric, commits a transaction ID at most once, and cover outages in each phase, restarts, ambiguous submits, invalidated transactions, torn journal lines and local policy refusal. The Python test checks pins, channel configuration and that no generated material is tracked; with `FABRIC_EXAMPLE_NETWORK=1` it also runs the integration script.
