#!/usr/bin/env python3
"""Gateway MAC service: polls its zone, collects residues, ACKs, retries, signs
completed readings, and hands them to the Fabric submitter.

    python3 -m gateway.gateway_service --gateway ex-gw-north --port 1700 --out readings.jsonl \
        --signing-key build/keys/ex-gw-north.ed25519.pem
    python3 -m gateway.gateway_service --generate-signing-key build/keys/ex-gw-north.ed25519.pem
    python3 -m gateway.gateway_service --verify readings.jsonl --public-key build/keys/ex-gw-north.ed25519.pub.pem

Completed readings are appended as JSON lines (one per reading, flushed). The
Fabric submitter (gateway/fabric-submitter) reads
that file, submits each reading as a WriteSensor transaction under the
gateway's Fabric identity, confirms the commit, and keeps readings through
outages. The two run as separate processes so that a Fabric outage never
blocks the radio side.

Gateway record signature (Ed25519). With --signing-key, every completed reading
carries a "gateway_signature" object:

    {"alg": "Ed25519", "key_id": <first 16 hex of SHA-256(raw public key)>,
     "sha256": <hex SHA-256 of the canonical record>, "sig": <base64 signature of the canonical record>}

The canonical record is the reading without "gateway_signature", serialised as
JSON with sorted keys and separators (",", ":"). The submitter copies the object
into the ledger document unchanged, so a ledger record can be tied back to the
gateway's signed record by its sha256 and checked with the gateway's public key.
This is an application-layer signature on the reading. It is separate from the
Fabric transaction signature, which Fabric 2.5 MSP identities make with ECDSA
P-256 (docs/BLOCKCHAIN_REPRODUCIBILITY.md). Keys are generated locally, kept out
of git (build/ is ignored) and never derived from public names.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import logging
import os
import sys
import time

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from .mac import config as cfgmod
from .mac import protocol as p
from .mac.ack_manager import make_ack_manager
from .mac.poll_scheduler import PollScheduler
from .mac.retry_manager import RetryManager
from .mac.semtech_udp import SemtechUdp

log = logging.getLogger("gateway")


SIGNATURE_FIELD = "gateway_signature"


def now_ms() -> int:
    return int(time.time() * 1000)


# ------------------------------------------------------------------ Ed25519 record signatures
def canonical_record_bytes(record: dict) -> bytes:
    """Bytes that are signed: the record without its signature, sorted keys, no whitespace."""
    body = {k: v for k, v in record.items() if k != SIGNATURE_FIELD}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def key_id(public_key: Ed25519PublicKey) -> str:
    raw = public_key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return hashlib.sha256(raw).hexdigest()[:16]


def sign_record(record: dict, private_key: Ed25519PrivateKey) -> dict:
    """Returns a copy of record with a gateway_signature over its canonical bytes."""
    body = canonical_record_bytes(record)
    out = {k: v for k, v in record.items() if k != SIGNATURE_FIELD}
    out[SIGNATURE_FIELD] = {"alg": "Ed25519", "key_id": key_id(private_key.public_key()),
                            "sha256": hashlib.sha256(body).hexdigest(),
                            "sig": base64.b64encode(private_key.sign(body)).decode()}
    return out


def verify_record(record: dict, public_key: Ed25519PublicKey) -> tuple[bool, str]:
    """(True, "ok") if the record's gateway_signature is valid for public_key; else (False, reason)."""
    sig = record.get(SIGNATURE_FIELD)
    if not isinstance(sig, dict) or sig.get("alg") != "Ed25519":
        return False, "no Ed25519 gateway_signature"
    if sig.get("key_id") != key_id(public_key):
        return False, "signed with another key"
    body = canonical_record_bytes(record)
    if hashlib.sha256(body).hexdigest() != sig.get("sha256"):
        return False, "record altered after signing (sha256 mismatch)"
    try:
        public_key.verify(base64.b64decode(sig["sig"]), body)
    except (InvalidSignature, ValueError, KeyError):
        return False, "bad signature"
    return True, "ok"


def load_private_key(path: str) -> Ed25519PrivateKey:
    k = serialization.load_pem_private_key(open(path, "rb").read(), password=None)
    if not isinstance(k, Ed25519PrivateKey):
        raise ValueError(f"{path} is not an Ed25519 private key")
    return k


def load_public_key(path: str) -> Ed25519PublicKey:
    k = serialization.load_pem_public_key(open(path, "rb").read())
    if not isinstance(k, Ed25519PublicKey):
        raise ValueError(f"{path} is not an Ed25519 public key")
    return k


def generate_signing_key(path: str) -> str:
    """Writes a fresh Ed25519 key (PKCS#8 PEM, mode 0600) and its public key next to it."""
    key = Ed25519PrivateKey.generate()
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                  serialization.NoEncryption()))
    pub = (path[:-4] if path.endswith(".pem") else path) + ".pub.pem"
    with open(pub, "wb") as f:
        f.write(key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
    return pub


def verify_file(readings: str, public_key_path: str) -> int:
    pk, bad, n = load_public_key(public_key_path), 0, 0
    for i, line in enumerate(open(readings), 1):
        if not line.strip():
            continue
        n += 1
        ok, why = verify_record(json.loads(line), pk)
        if not ok:
            bad += 1
            print(f"line {i}: {why}", file=sys.stderr)
    print(f"{n - bad}/{n} records verified")
    return 1 if bad or not n else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gateway")
    ap.add_argument("--config", default=cfgmod.DEFAULT_PATH)
    ap.add_argument("--deployment", default=cfgmod.DEFAULT_DEPLOYMENT)
    ap.add_argument("--port", type=int, default=1700)
    ap.add_argument("--out", default="readings.jsonl")
    ap.add_argument("--signing-key", help="Ed25519 private key (PEM); completed readings are signed with it")
    ap.add_argument("--generate-signing-key", metavar="PATH", help="write a fresh Ed25519 key and exit")
    ap.add_argument("--verify", metavar="READINGS", help="verify every record of a readings.jsonl and exit")
    ap.add_argument("--public-key", help="Ed25519 public key (PEM) for --verify")
    a = ap.parse_args()
    if a.generate_signing_key:
        print("public key:", generate_signing_key(a.generate_signing_key))
        return
    if a.verify:
        if not a.public_key:
            ap.error("--verify needs --public-key")
        sys.exit(verify_file(a.verify, a.public_key))
    if not a.gateway:
        ap.error("--gateway is required")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    signer = load_private_key(a.signing_key) if a.signing_key else None
    if signer is None:
        log.warning("no --signing-key: completed readings are written unsigned")

    cfg = cfgmod.load(a.config, a.deployment)
    sched = PollScheduler(cfg)
    probe = PollScheduler(cfg)
    budget = int(cfg.schedule["turnaround_ms"] + p.PACKETS_PER_READING * (p.lora_airtime_ms(8) + cfg.schedule["inter_frame_gap_ms"])
                 + p.NUM_QUANTITIES * cfg.ack["ack_window_ms"])
    retry = RetryManager(cfg.retry, budget)
    acks = make_ack_manager(cfg, a.gateway, retry)
    uplink_type = p.FRAME_CHANNEL if cfg.frame_mode == 3 else p.FRAME_RESIDUE 
    udp = SemtechUdp(port=a.port)
    radio = cfg.radio
    out = open(a.out, "a")
    pending = []
    cycle = None
    log.info("gateway %s zone %s, %d nodes, duty cycle %.3f%%", a.gateway, acks.zone, len(cfg.zone_nodes(acks.zone)),
             100 * probe.duty_cycle(a.gateway))
    while True:
        t = now_ms()
        c = sched.cycle_start(t // 1000)
        if c != cycle:
            cycle = c
            pending = sched.slots(c, a.gateway)
        for slot in [s for s in pending if s.poll_time_ms <= t]:
            pending.remove(slot)
            acks.set_slot(slot.node_id, slot.slot_start * 1000, slot.slot_end * 1000)
            udp.send(sched.poll_frame(slot), radio["poll_channel_hz"], power_dbm=radio["gateway_tx_power_dbm"])
            retry.poll_sent(slot.node_id, slot.slot_end * 1000, t)
        for n in cfg.zone_nodes(acks.zone):
            if retry.repoll_due(n.node_id, t):
                slot_end = retry._polls[n.node_id].slot_end_ms
                frame = p.PollFrame(n.node_id, sched.next_poll_id(n.node_id), t // 1000).encode()
                udp.send(frame, radio["poll_channel_hz"], power_dbm=radio["gateway_tx_power_dbm"])
                retry.poll_sent(n.node_id, slot_end, t)
        for up in udp.poll():
            if not up.crc_ok or p.frame_type(up.payload) != uplink_type:
                continue
            for ack in acks.on_frame(up.payload, now_ms()):
                udp.send(ack, radio["poll_channel_hz"], power_dbm=radio["gateway_tx_power_dbm"])
        for ack in acks.on_timer(now_ms()):
            udp.send(ack, radio["poll_channel_hz"], power_dbm=radio["gateway_tx_power_dbm"])
        for r in acks.pop_completed():
            rec = completed_record(a.gateway, r, signer)
            out.write(json.dumps(rec) + "\n")
            out.flush()
        for e in retry.events:
            log.info("node %d %s %s", e.node_id, e.kind, e.detail)
        retry.events.clear()


def completed_record(gateway_id: str, reading, signer: Ed25519PrivateKey | None = None) -> dict:
    """The readings.jsonl line for one CompletedReading, signed when a key is given."""
    rec = {"gateway_id": gateway_id, **reading.__dict__}
    return sign_record(rec, signer) if signer is not None else rec


if __name__ == "__main__":
    main()
