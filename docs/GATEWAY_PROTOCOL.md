# Gateway protocol

The gateway side of the LoRa CRT protocol lives in `gateway/mac` (Python 3.13) and `gateway/fabric-submitter` (Go 1.22+). This is the gateway code used in the 2025 deployment. The deployment's gateway logs are withheld from this repository for privacy reasons and are available from the corresponding author on reasonable request.

## Frames

All frames start with `(type << 4) | version` (version 1) and end with CRC-16/CCITT-FALSE (poly 0x1021, init 0xFFFF, check value 0x29B1 for "123456789"), big-endian. Multi-byte integers are big-endian.

| Frame | Type | Bytes | Layout after the type byte |
|---|---|---|---|
| Residue (FRAME_MODE 15) | 0x1 | 8 | node, seq (2), `quantity:3 residue_index:2 valid event retx`, residue, CRC (2) |
| POLL | 0x2 | 10 | node, poll_id (2), gateway Unix time (4), CRC (2) |
| ACK | 0x3 | 8 | node, seq (2), `quantity:3 reconstructed retx_request received_mask:3`, crt_status, CRC (2) |
| Channel (FRAME_MODE 3) | 0x4 | 12 | node, seq (2), `residue_index:2 event retx 0000`, five residues (0xFF = sensor invalid), CRC (2) |

Fixed vectors for every frame type are in `tests/test_protocol_vectors.py` and `examples/packet_encoding_examples.json`; the firmware tests check the same bytes.

## Schedule and polling

`poll_scheduler.py` aligns cycles to hh:00 and hh:30 UTC (1800 s). Zone windows start at 0 / 450 / 900 / 1350 s for North / South / East / West; inside its window a gateway polls its nodes in position order, one 34 s slot each, sending the POLL at slot start plus a uniform 0 to 250 ms jitter. Poll IDs are per-node 16-bit counters. A node that answers nothing within 2,500 ms is polled once more if the slot still has room for a full response (`retry_manager.py`, `max_poll_attempts: 2`).

Gateway downlink duty cycle per cycle is `n_nodes x (T(POLL) + 5 x T(ACK)) / 1800 s`. With 10-byte POLLs and 8-byte ACKs that is 763.9 ms per node, so one gateway stays under the 1 % ETSI g1 limit up to 23 nodes per cycle (four gateways: 94). The study's 96-node figure assumes an 8-byte POLL.

## Receiving and decoding residue packets

`semtech_udp.py` speaks the Semtech GWMP v2 protocol: uplinks arrive as PUSH_DATA `rxpk` (base64 payload, CRC status), downlinks leave as PULL_RESP `txpk` on 868.1 MHz at the configured gateway power. `gateway_service.py` drops frames with a failed PHY CRC or of the wrong type for the configured framing.

`ack_manager.py` (`AckManager`, FRAME_MODE 15) accepts a residue frame only if:

1. it decodes (type, version, CRC-16, node not 0 or 0xFF, quantity < 5, residue index < 3, residue < its modulus);
2. the node is in the roster and belongs to this gateway's zone;
3. it arrives inside the node's slot plus the 2 s guard (event-flagged frames: inside the zone window);
4. its sequence number is not older than the node's last completed reading (RFC 1982 comparison);
5. it is not a duplicate of a residue already held (the last 8 readings per node are remembered).

## Grouping and reconstruction

Residues are grouped by (node, sequence, quantity). A quantity is decided as soon as every residue expected in the current attempt has arrived, or 30 ms plus two frame airtimes after the last frame of the attempt otherwise:

- the node flagged the quantity invalid (sensor fault): acknowledged, stored as `invalid_at_node`, value null;
- two or three residues reconstruct to a value within the quantity's range: reconstructed, final ACK with `reconstructed = 1`;
- fewer than two residues (INSUFFICIENT): ACK with `retx_request = 1` and the mask of residues held, so the node resends only the missing ones;
- three residues that decode to 9,797 or more (INCONSISTENT, a corrupted residue): the held residues are discarded and all three are requested again;
- out of retransmission budget (two requests per quantity): final ACK with neither flag set, quantity `abandoned`, value null.

`restore_redundancy: false` means two residues are accepted without asking for the third. A retransmitted residue of a reading that is already complete gets its final ACK again, which covers the case of a lost final ACK.

FRAME_MODE 3 (`ChannelAckManager`) applies the same checks to 12-byte channel frames and sends one ACK per reading (quantity code 7) whose masks list channels; any two channels reconstruct all five quantities.

## Completed readings, validation and signing

When all five quantities are decided, `pop_completed()` returns a `CompletedReading`: node, sensor and device IDs, sequence, event flag, first-frame time, the five values in engineering units (or null), per-quantity status, residues received per quantity, and retransmission requests used. Value validation happens twice before anything reaches the ledger: the gateway rejects reconstructions above the quantity's `max_scaled`, and the chaincode rejects any reading whose values fall outside the protocol ranges (`readingRanges` in `policy.go`).

`gateway_service.py` writes each completed reading as one JSON line. With `--signing-key`, the line carries `gateway_signature = {alg: "Ed25519", key_id, sha256, sig}` over the canonical JSON of the reading without that field (sorted keys, separators `,` and `:`). Keys come from `--generate-signing-key` (PKCS#8 PEM, mode 0600) and are never committed. `--verify readings.jsonl --public-key key.pub.pem` checks a file. Schema: `schemas/example_gateway_record.schema.json`.

## Submission to Fabric

`gateway/fabric-submitter` reads `readings.jsonl`, journals each line (fsync) before any network call, and converts it with `ToSubmission`: reading ID `R<node:03>-<seq:04>-<UTC date>`, residue counts as `"3,3,2,3,3"`, values rounded to the protocol resolution and renamed (`temperature` to `temp_c`, `humidity` to `humidity_pct`, `illuminance` to `light_lux`), non-reconstructed quantities sent as null with their status under `quality`, and the gateway signature copied as is. It refuses locally any sensor its certificate's `sensors` attribute does not list, then endorses, submits and waits for the commit status. A chaincode denial or validation error is final; anything else is an outage, retried with exponential backoff up to 5 minutes, oldest reading first. The signed transaction is reused until it has a final status, so an ambiguous submit cannot produce a second record; a transaction invalidated at commit (e.g. MVCC conflict) is replaced, up to five times. Receipts (`receipts.jsonl`) join reading ID, transaction ID, commit status and block number.

## Running

```bash
python -m pytest tests/test_gateway_mac.py tests/test_protocol_vectors.py
```

```bash
(cd gateway/fabric-submitter && go test ./...)
```

```bash
python -m gateway.mac.poll_scheduler --gateway ex-gw-south --at 2030-01-15T00:00:00Z --seed 1
```

With a concentrator pointed at this host:

```bash
python -m gateway.gateway_service --generate-signing-key build/keys/ex-gw-north.ed25519.pem
```

```bash
python -m gateway.gateway_service --gateway ex-gw-north --port 1700 --out readings.jsonl --signing-key build/keys/ex-gw-north.ed25519.pem
```

The roster defaults to `config/deployment.example.yaml` (illustrative); pass `--deployment` with your own. The software can be tested with physical LoRa hardware or illustrative traffic. Running it reproduces its protocol behavior, not the paper's field measurements. Field logs and performance records are not in this repository.
