"""Gateway MAC behaviour: schedule, residue intake, ACKs, retries, duplicates, record signing.

Roster: config/deployment.example.yaml (illustrative, 3 nodes per zone). Inputs are invented."""
import copy
import json
import os
import random
import re

import pytest

from conftest import ROOT

from gateway.mac import config as cfgmod
from gateway.mac import protocol as p
from gateway.mac.ack_manager import AckManager, ChannelAckManager, make_ack_manager
from gateway.mac.poll_scheduler import PollScheduler
from gateway.mac.retry_manager import RetryManager

CFG = cfgmod.load()


def test_roster_and_windows():
    sizes = {z: len(CFG.zone_nodes(z)) for z in CFG.raw["zones"]}
    assert sizes == {"North": 3, "South": 3, "East": 3, "West": 3}
    assert len(CFG.nodes) == 12 and CFG.raw["deployment_provenance"] == "illustrative"
    assert all(n.sensor_id.startswith("ex-") and n.device_id.startswith("ex-") for n in CFG.nodes.values())
    s = CFG.schedule
    assert s["cycle_s"] == 1800 and s["zone_window_s"] == 450 and 13 * s["node_slot_s"] <= 450


def test_loader_rejects_bad_rosters(tmp_path):
    import yaml
    dep = yaml.safe_load(open(cfgmod.DEFAULT_DEPLOYMENT))
    dep["zones"]["South"]["nodes"][0]["node_id"] = 1          # duplicate of a North node
    bad = tmp_path / "dup.yaml"
    bad.write_text(yaml.safe_dump(dep))
    with pytest.raises(ValueError, match="duplicate node_id"):
        cfgmod.load(cfgmod.DEFAULT_PATH, str(bad))
    dep = yaml.safe_load(open(cfgmod.DEFAULT_DEPLOYMENT))
    dep["zones"]["North"]["nodes"] = [dict(node_id=100 + i, sensor_id=f"ex-x-{i}", device_id=f"ex-d-{i}", position=i)
                                      for i in range(14)]           # 14 x 34 s > 450 s window
    big = tmp_path / "big.yaml"
    big.write_text(yaml.safe_dump(dep))
    with pytest.raises(ValueError, match="exceed the zone window"):
        cfgmod.load(cfgmod.DEFAULT_PATH, str(big))


def test_firmware_constants_match_radio_config():
    """main.cpp, mac.h and lora_protocol.h must agree with config/radio.example.yaml."""
    fw = os.path.join(ROOT, "firmware", "esp32")
    main = open(os.path.join(fw, "main.cpp")).read()
    mac = open(os.path.join(fw, "mac.h")).read()
    proto_h = open(os.path.join(fw, "lora_protocol.h")).read()
    const = lambda src, name: int(re.search(rf"\b{name}\s*=\s*(\d+)", src).group(1))  # noqa: E731
    s, r, a = CFG.schedule, CFG.retry, CFG.ack
    assert const(main, "CYCLE_S") == s["cycle_s"] and const(main, "ZONE_WINDOW_S") == s["zone_window_s"]
    assert const(main, "NODE_SLOT_S") == s["node_slot_s"] and const(main, "WAKE_LEAD_S") == s["node_wake_lead_s"]
    assert const(main, "POLL_LISTEN_S") == s["poll_listen_s"] and const(main, "EVENT_CHECK_S") == CFG.raw["event_reports"]["check_interval_s"]
    assert const(mac, "turnaround_ms") == s["turnaround_ms"] and const(mac, "inter_frame_gap_ms") == s["inter_frame_gap_ms"]
    assert const(mac, "ack_window_ms") == a["ack_window_ms"] and const(mac, "slot_guard_ms") == s["guard_interval_ms"]
    assert const(mac, "ack_timeout_ms") == r["ack_timeout_ms"] and const(mac, "max_retries") == r["max_retries_per_quantity"]
    assert const(proto_h, "RESIDUE_FRAME_LEN") == CFG.raw["frames"]["residue_bytes"] == p.RESIDUE_LEN
    assert const(proto_h, "POLL_FRAME_LEN") == CFG.raw["frames"]["poll_bytes"] == p.POLL_LEN
    assert const(proto_h, "ACK_FRAME_LEN") == CFG.raw["frames"]["ack_bytes"] == p.ACK_LEN
    assert CFG.radio["channels_hz"] == list(p.CHANNEL_HZ) and CFG.raw["frames"]["moduli"] == list(p.MODULI)


def test_schedule_order_offsets_and_duty_cycle():
    sch = PollScheduler(CFG, random.Random(1))
    c = 1894665600  # 2030-01-15T00:00:00Z (illustrative)
    slots = sch.slots(c)
    assert len(slots) == 12
    for zone, off in CFG.schedule["zone_coordination_offset_s"].items():
        z = [x for x in slots if x.zone == zone]
        assert z[0].slot_start == c + off
        assert [x.position for x in z] == list(range(len(z)))
        assert all(x.slot_end <= c + off + 450 for x in z)
        assert all(0 <= x.poll_time_ms - x.slot_start * 1000 < CFG.schedule["poll_jitter_ms"] for x in z)
    for gw in ["ex-gw-north", "ex-gw-south", "ex-gw-east", "ex-gw-west"]:
        assert sch.duty_cycle(gw, retx_fraction=0.1) < CFG.radio["duty_cycle_limit"]
    # poll ids increase per node across cycles
    nxt = sch.slots(c + 1800)
    assert all(b.poll_id == a.poll_id + 1 for a, b in zip(slots, nxt))


def mk(gw="ex-gw-north"):
    retry = RetryManager(CFG.retry, response_budget_ms=4000, clock=lambda: 0)
    return AckManager(CFG, gw, retry), retry


def frames(node, seq, q, value, idx=(0, 1, 2), retx=False, valid=True):
    scaled = p.scale_value(q, value)
    r = p.crt_residues(scaled)
    return [p.ResidueFrame(node, seq, q, i, valid, False, retx, r[i]).encode() for i in idx]


def send(am, fs, t=1000):
    acks = []
    for f in fs:
        acks += am.on_frame(f, t)
    return [p.AckFrame.decode(a) for a in acks]


VALUES = [41.4, 25.3, 64.3, 6.82, 1500.0]


def test_full_reading_reconstructs():
    am, _ = mk()
    for q, v in enumerate(VALUES):
        acks = send(am, frames(1, 10, q, v))
        assert len(acks) == 1 and acks[0].reconstructed and acks[0].quantity == q
    done = am.pop_completed()
    assert len(done) == 1 and done[0].sensor_id == "ex-sns-001"
    assert done[0].values["temperature"] == pytest.approx(25.3)
    assert done[0].values["illuminance"] == pytest.approx(1500.0)


def test_two_of_three_and_retx_and_abandon():
    am, retry = mk()
    send(am, frames(1, 11, 0, 41.4, idx=(0, 2)))
    assert am.on_timer(100000) and am.pop_completed() == []  # decided on timeout from 2 residues
    q0 = am._open[(1, 11)].quantities[0]
    assert q0.status == "reconstructed"
    send(am, frames(1, 11, 1, 25.3, idx=(1,)))
    acks = [p.AckFrame.decode(a) for a in am.on_timer(200000)]
    assert acks[0].retx_request and acks[0].received_mask == 0b010
    acks = send(am, frames(1, 11, 1, 25.3, idx=(0, 2), retx=True), 200500)
    assert acks[0].reconstructed
    # quantity 2: only one residue, ever -> two retx requests then abandon
    send(am, frames(1, 11, 2, 64.3, idx=(0,)))
    t = 300000
    for _ in range(CFG.retry["max_retries_per_quantity"]):
        a = [p.AckFrame.decode(x) for x in am.on_timer(t)]
        assert a[0].retx_request
        t += 10000
    a = [p.AckFrame.decode(x) for x in am.on_timer(t)]
    assert not a[0].retx_request and not a[0].reconstructed  # abandoned
    for q in (3, 4):
        send(am, frames(1, 11, q, VALUES[q]))
    done = am.pop_completed()[0]
    assert done.status["humidity"] == "abandoned" and done.values["humidity"] is None
    assert retry.summary()[1]["quantities_abandoned"] == 1


def test_corrupted_residue_requests_fresh_triplet():
    am, _ = mk()
    fs = frames(1, 12, 1, 25.3)
    bad = p.ResidueFrame(1, 12, 1, 1, True, False, False, (47 + 1) % 101).encode()
    acks = send(am, [fs[0], bad, fs[2]])
    assert acks[0].retx_request and acks[0].crt_status == p.CRT_INCONSISTENT
    acks = send(am, frames(1, 12, 1, 25.3, retx=True), 2000)
    assert acks[-1].reconstructed


def test_checks_before_acceptance():
    am, retry = mk("ex-gw-north")
    assert send(am, frames(4, 1, 0, 40.0)) == []         # ex-sns-004 is a South node
    assert send(am, frames(60, 1, 0, 40.0)) == []        # not in roster
    assert send(am, [b"\x11\x01\x00\x01\x00\x05\x00\x00"]) == []  # bad CRC
    am.set_slot(2, 10_000, 44_000)
    assert send(am, frames(2, 1, 0, 40.0), t=90_000) == []  # outside its slot
    ev = {e.detail.split(":")[0] for e in retry.events}
    assert {"wrong_zone", "unknown_node", "malformed_or_crc", "outside_slot"} <= ev


def test_duplicates_and_lost_final_ack():
    am, retry = mk()
    for q, v in enumerate(VALUES):
        send(am, frames(1, 20, q, v))
    assert am.pop_completed()
    # node never got the last ACK and retransmits quantity 4: gateway repeats its final ACK
    acks = send(am, frames(1, 20, 4, VALUES[4], retx=True), 5000)
    assert acks and all(a.reconstructed for a in acks)
    assert retry.summary()[1]["duplicate_frames"] >= 3
    # an older reading is rejected as stale
    assert send(am, frames(1, 19, 0, 40.0)) == []


def test_repoll_rules():
    r = RetryManager(CFG.retry, response_budget_ms=4000)
    r.poll_sent(5, slot_end_ms=34_000, now_ms=0)
    assert not r.repoll_due(5, 1000)
    assert r.repoll_due(5, CFG.retry["poll_response_timeout_ms"])
    r.poll_sent(5, slot_end_ms=34_000, now_ms=3000)
    assert not r.repoll_due(5, 6000)  # max_poll_attempts reached
    assert r.summary()[5]["polls_unanswered"] == 1


# ------------------------------------------------------------------ FRAME_MODE 3
def mk3(gw="ex-gw-north"):
    retry = RetryManager(CFG.retry, response_budget_ms=4000, clock=lambda: 0)
    return ChannelAckManager(CFG, gw, retry), retry


def chan_frames(node, seq, values=VALUES, idx=(0, 1, 2), retx=False, invalid=()):
    res = [p.crt_residues(p.scale_value(q, v)) for q, v in enumerate(values)]
    return [p.ChannelFrame(node, seq, i, False, retx, tuple(None if q in invalid else res[q][i] for q in range(5))).encode()
            for i in idx]


def test_3frame_full_reading_one_ack():
    am, _ = mk3()
    acks = send(am, chan_frames(1, 10))
    assert len(acks) == 1 and acks[0].quantity == p.ACK_WHOLE_READING and acks[0].reconstructed
    assert acks[0].received_mask == 0b111
    done = am.pop_completed()[0]
    assert done.values["temperature"] == pytest.approx(25.3) and done.values["illuminance"] == pytest.approx(1500.0)
    assert set(done.status.values()) == {"reconstructed"}


def test_3frame_any_two_channels_reconstruct_all_five():
    assert CFG.ack["restore_redundancy"] is False  # two channels are accepted without asking for the third
    for lost in range(3):
        am, _ = mk3()
        send(am, chan_frames(1, 30, idx=tuple(i for i in range(3) if i != lost)))
        acks = [p.AckFrame.decode(a) for a in am.on_timer(100_000)]
        assert acks[0].reconstructed and acks[0].received_mask == 0b111 & ~(1 << lost)
        done = am.pop_completed()[0]
        assert all(done.values[q] == pytest.approx(v) for q, v in zip(p.QUANTITIES, VALUES))


def test_3frame_single_channel_retx_and_invalid_sensor():
    am, retry = mk3()
    send(am, chan_frames(1, 31, idx=(1,), invalid=(3,)))
    acks = [p.AckFrame.decode(a) for a in am.on_timer(100_000)]
    assert acks[0].retx_request and acks[0].received_mask == 0b010
    acks = send(am, chan_frames(1, 31, idx=(0, 2), retx=True, invalid=(3,)), 100_500)
    assert acks[-1].reconstructed
    done = am.pop_completed()[0]
    assert done.status["ph"] == "invalid_at_node" and done.values["ph"] is None
    assert done.values["humidity"] == pytest.approx(64.3) and done.retries == 1


def test_3frame_corrupt_channel_and_lost_final_ack():
    am, retry = mk3()
    fs = chan_frames(1, 32)
    good = p.ChannelFrame.decode(fs[1])
    bad = p.ChannelFrame(1, 32, 1, False, False, ((good.residues[0] + 1) % 101,) + good.residues[1:]).encode()
    acks = send(am, [fs[0], bad, fs[2]])
    assert acks[0].retx_request and acks[0].crt_status == p.CRT_INCONSISTENT and acks[0].received_mask == 0
    assert send(am, chan_frames(1, 32, retx=True), 2000)[-1].reconstructed
    assert am.pop_completed()
    # node missed the final ACK and resends a channel frame: the same ACK is repeated
    again = send(am, chan_frames(1, 32, idx=(0,), retx=True), 5000)
    assert again and again[0].reconstructed
    assert send(am, chan_frames(1, 31)) == []  # older sequence: stale


def test_3frame_rejects_15frame_traffic_and_factory():
    am, _ = mk3()
    assert send(am, frames(1, 40, 0, 41.4)) == []   # residue frames are not channel frames
    assert isinstance(make_ack_manager(CFG, "ex-gw-north", RetryManager(CFG.retry, 4000)), AckManager)
    cfg3 = copy.deepcopy(CFG)
    cfg3.raw["frames"]["frame_mode"] = 3
    assert isinstance(make_ack_manager(cfg3, "ex-gw-north", RetryManager(CFG.retry, 4000)), ChannelAckManager)


# ------------------------------------------------------------------ replay of examples/example_gateway_events.jsonl
def test_replay_example_events():
    """Clean quantities, a two-residue decision, a retransmission request, a corrupted residue and a
    duplicate, replayed through the real intake. Events are invented (see the file header)."""
    lines = [json.loads(x) for x in open(os.path.join(ROOT, "examples", "example_gateway_events.jsonl"))]
    head, events = lines[0], lines[1:]
    assert "Not original field evidence" in head["_README"]
    am, retry = mk(head["gateway_id"])
    acks = []
    for e in events:
        out = am.on_frame(bytes.fromhex(e["frame_hex"]), e["t_ms"]) if e["kind"] == "uplink" else am.on_timer(e["t_ms"])
        acks += [p.AckFrame.decode(a) for a in out]
    done = am.pop_completed()
    assert len(done) == 1
    r = done[0]
    assert (r.node_id, r.seq, r.sensor_id) == (head["node_id"], head["seq"], "ex-sns-002")
    for q, v in head["values"].items():
        assert r.status[q] == "reconstructed" and r.values[q] == pytest.approx(v)
    assert r.residues_received["humidity"] == 2            # decided from two residues on the timer
    kinds = [(a.quantity, a.reconstructed, a.retx_request, a.crt_status) for a in acks]
    assert (3, False, True, p.CRT_INSUFFICIENT) in kinds    # pH: one residue -> retransmission request
    assert (4, False, True, p.CRT_INCONSISTENT) in kinds    # illuminance: corrupted residue -> fresh triplet
    assert r.retries == 2
    assert retry.summary()[2]["duplicate_frames"] >= 1


# ------------------------------------------------------------------ Ed25519 gateway record signatures
def test_sign_and_verify_completed_record(tmp_path):
    from gateway import gateway_service as gs
    key_path = tmp_path / "gw.ed25519.pem"
    pub_path = gs.generate_signing_key(str(key_path))
    assert oct(os.stat(key_path).st_mode & 0o777) == "0o600"
    key, pub = gs.load_private_key(str(key_path)), gs.load_public_key(pub_path)
    am, _ = mk()
    for q, v in enumerate(VALUES):
        send(am, frames(1, 10, q, v))
    rec = gs.completed_record("ex-gw-north", am.pop_completed()[0], key)
    sig = rec["gateway_signature"]
    assert sig["alg"] == "Ed25519" and len(sig["key_id"]) == 16 and len(sig["sha256"]) == 64
    assert gs.verify_record(rec, pub) == (True, "ok")
    # the JSONL round trip the submitter sees keeps the signature valid
    assert gs.verify_record(json.loads(json.dumps(rec)), pub)[0]
    tampered = json.loads(json.dumps(rec))
    tampered["values"]["temperature"] = 30.0
    assert gs.verify_record(tampered, pub) == (False, "record altered after signing (sha256 mismatch)")
    other = gs.load_public_key(gs.generate_signing_key(str(tmp_path / "other.pem")))
    assert gs.verify_record(rec, other) == (False, "signed with another key")
    unsigned = {k: v for k, v in rec.items() if k != "gateway_signature"}
    assert gs.verify_record(unsigned, pub)[0] is False


def test_verify_cli(tmp_path):
    from conftest import run_py
    from gateway import gateway_service as gs
    key = tmp_path / "gw.pem"
    pub = gs.generate_signing_key(str(key))
    k = gs.load_private_key(str(key))
    recs = [gs.sign_record({"gateway_id": "ex-gw-north", "node_id": n, "seq": 1}, k) for n in (1, 2)]
    f = tmp_path / "readings.jsonl"
    f.write_text("".join(json.dumps(r) + "\n" for r in recs))
    r = run_py("-m", "gateway.gateway_service", "--verify", str(f), "--public-key", pub)
    assert "2/2 records verified" in r.stdout
    recs[1]["seq"] = 2
    f.write_text("".join(json.dumps(r) + "\n" for r in recs))
    assert run_py("-m", "gateway.gateway_service", "--verify", str(f), "--public-key", pub, check=False).returncode == 1


def test_unsigned_record_when_no_key():
    from gateway import gateway_service as gs
    am, _ = mk()
    for q, v in enumerate(VALUES):
        send(am, frames(2, 11, q, v))
    rec = gs.completed_record("ex-gw-north", am.pop_completed()[0])
    assert "gateway_signature" not in rec and rec["sensor_id"] == "ex-sns-002"


def test_gateway_record_matches_schema():
    jsonschema = pytest.importorskip("jsonschema")
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from gateway import gateway_service as gs
    schema = json.load(open(os.path.join(ROOT, "schemas", "example_gateway_record.schema.json")))
    am, _ = mk()
    for q, v in enumerate(VALUES):
        send(am, frames(3, 12, q, v))
    rec = json.loads(json.dumps(gs.completed_record("ex-gw-north", am.pop_completed()[0], Ed25519PrivateKey.generate())))
    jsonschema.validate(rec, schema)
