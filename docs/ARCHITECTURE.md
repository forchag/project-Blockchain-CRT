# Architecture

The system moves five soil and climate quantities from a battery-powered ESP32 node to a Hyperledger Fabric ledger. On the radio, each quantity travels as three Chinese Remainder Theorem residues on three LoRa channels, so a reading survives the loss of any one residue per quantity, or of a whole channel.

> **Scope of this document.** This is the architecture of the deployed system as implemented in this code. `blockchain/example-network` is a fresh single-host test environment with generated identities, not the field network. Field data and logs are withheld for privacy reasons; see [DATA_AVAILABILITY.md](DATA_AVAILABILITY.md).

```
 ESP32 node (firmware/esp32)                Gateway (gateway/)                          Fabric 2.5 (blockchain/)
 ───────────────────────────                ──────────────────                          ────────────────────────
 sense 5 quantities                         poll_scheduler: POLL in the node's slot
 calibrate, range-check, scale                    │ 10 B POLL on 868.1 MHz
 x mod (97, 101, 103)          ◄──────────────────┘
 15 x 8 B residue frames ──── ch0 868.1 ─┐
   residue i on channel i ─── ch1 868.3 ─┼─► semtech_udp ─► ack_manager: group by
                         ──── ch2 868.5 ─┘   (concentrator)  (node, seq, quantity),
 per-quantity ACK / retry ◄──── 8 B ACK ─────────────────── reconstruct from >= 2,
 deep sleep until next slot                                   ACK / ask for missing
                                            gateway_service: completed reading,
                                            Ed25519 signature ─► readings.jsonl
                                            fabric-submitter: journal, WriteSensor ──► hrbac-cc chaincode
                                            (ECDSA P-256 Fabric identity)              role / delegation check,
                                                                                       record stored on 4 peers,
                                                                                       3 Raft orderers
```

## Node

`firmware/esp32/main.cpp` runs one reading per 1800 s cycle. The cycle splits into four 450 s zone windows and each window into 34 s node slots. The node wakes 3 s before its slot, reads the SHT31-D (humidity), BH1750FVI (illuminance) and DS18B20 (temperature), then the power-gated analog front end (SEN0193 soil moisture, SEN0161-V2 pH), and listens for its POLL. On a valid POLL it sends, quantity by quantity, three 8-byte residue frames (residue i on channel i) and waits up to 400 ms for that quantity's ACK, retransmitting what the gateway lacks at most twice. Between slots it wakes every 300 s to check soil moisture and may send an event-flagged reading inside its own zone window after listen-before-talk.

The scaled integer of every quantity is below 9,797 = 97 x 101, the smallest product of two moduli, so any two residues determine it; the third residue turns reconstruction into a consistency check that catches any single corrupted residue. Details: [FIRMWARE_REPRODUCIBILITY.md](FIRMWARE_REPRODUCIBILITY.md) and [GATEWAY_PROTOCOL.md](GATEWAY_PROTOCOL.md).

## Gateway

`gateway/gateway_service.py` couples a Semtech UDP packet forwarder (any SX1301/SX1302 concentrator running `lora_pkt_fwd`) to the MAC in `gateway/mac`. The poll scheduler sends each node's POLL at its slot start plus up to 250 ms of jitter. The ACK manager checks each frame (CRC-16, roster, zone, slot timing, sequence freshness, duplicates), groups residues by node, reading and quantity, reconstructs with Garner's algorithm, and answers per quantity: reconstructed, retransmit these residues, or abandoned. Completed readings are signed with the gateway's Ed25519 key and appended to `readings.jsonl`.

`gateway/fabric-submitter` (Go, Fabric Gateway SDK 1.7.0) journals every reading with fsync before any network call, converts it into `WriteSensor(sensorID, readingID, residues, readingJSON)`, endorses, submits and waits for the commit status. It reuses one signed transaction per reading until that transaction has a final status, so outages and restarts cannot create a second ledger record. Radio and ledger run as separate processes so a Fabric outage never blocks the radio.

## Ledger

`blockchain/chaincode/hrbac-cc` enforces hierarchical role-based access from the caller's Fabric CA attributes: a Sensor identity writes only its own readings, a Gateway identity writes only the sensors listed in its `sensors` attribute, every other role is refused. The stored record names the submitter and its role, keeps non-reconstructed quantities as `null`, and carries the gateway's Ed25519 signature unchanged. `blockchain/example-network` re-creates the deployment topology on one host: Fabric 2.5.16 with three etcdraft orderers, four peer organisations (North, South, East, West, one peer each), MAJORITY endorsement, a Fabric CA for role-carrying identities, the chaincode as an external service, and cAdvisor for resource sampling. The manuscript describes a multi-host field topology. The example network demonstrates the corresponding software structure; it doesn't reproduce the field hosts' configuration. Details: [BLOCKCHAIN_REPRODUCIBILITY.md](BLOCKCHAIN_REPRODUCIBILITY.md).

## Analysis and benchmarks

`analysis/` computes every result that follows from published formulas and labels each number by provenance; `benchmarks/` holds the procedures used in the original bench measurements (closed-loop load, RBAC on/off comparison, Raft failover, crypto micro-benchmark, fixed-point baseline). The manuscript reports benchmark and field results whose records are withheld for privacy reasons; the analysis prints them under their labels and doesn't re-derive them. See [ANALYTICAL_REPRODUCIBILITY.md](ANALYTICAL_REPRODUCIBILITY.md).

## Repository map

| Path | Content |
|---|---|
| `firmware/esp32/` | Node firmware (PlatformIO, Arduino-ESP32 2.0.17), host tests, calibration provisioning tool |
| `gateway/` | MAC service (Python) and Fabric submitter (Go) |
| `blockchain/` | HRBAC chaincode (Go) and the example Fabric 2.5 network |
| `config/` | Radio parameters, illustrative roster, analytical parameters, economic assumptions |
| `analysis/` | Analytical models, reproduction driver and validator, R RCBD script |
| `benchmarks/` | Load generator, benchmark and failover procedures, aggregation |
| `examples/` | Illustrative inputs and expected outputs |
| `schemas/` | JSON schemas of the example data |
| `tests/` | Python test suite (also runs the C++ and Go suites) |
| `docs/` | This documentation |
