"""Protocol vectors shared by the firmware (firmware/esp32/tests) and the gateway codec.

All vectors are self-authored: node 17, sequence 470 and the 2030 poll time are invented."""
import os
import subprocess

import pytest

from gateway.mac import protocol as p

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def test_worked_example():
    s = p.scale_value(1, 25.3)
    assert s == 653 and p.crt_residues(s) == (71, 47, 35)
    assert p.crt_reconstruct((71, 47, 35), 0b111) == (p.CRT_OK, 653)


def test_every_value_every_pair():
    for v in range(p.LEGITIMATE_RANGE):
        r = p.crt_residues(v)
        for m in (0b111, 0b011, 0b101, 0b110):
            assert p.crt_reconstruct(r, m) == (p.CRT_OK, v)


def test_single_corruption_always_detected():
    for v in range(0, p.LEGITIMATE_RANGE, 3):
        r = list(p.crt_residues(v))
        for i in range(3):
            c = r.copy()
            c[i] = (c[i] + 1) % p.MODULI[i]
            assert p.crt_reconstruct(c, 0b111)[0] == p.CRT_INCONSISTENT


@pytest.mark.parametrize("q,value,scaled", [(0, 41.39, 414), (1, -40.0, 0), (2, 100.0, 1000), (3, 6.79, 679),
                                            (4, 45150.0, 9030)])
def test_scaling(q, value, scaled):
    assert p.scale_value(q, value) == scaled
    assert max(s[2] for s in p.SCALE) < p.LEGITIMATE_RANGE


# Node 17, reading seq 470 (invented identifiers).
VECTORS = [
    (p.ResidueFrame(17, 470, 1, 0, True, False, False, 71), "111101d62447acbe"),
    (p.ResidueFrame(17, 470, 1, 1, True, False, False, 47), "111101d62c2fc8b9"),
    (p.ResidueFrame(17, 470, 1, 2, True, False, False, 35), "111101d6342383ef"),
    (p.ResidueFrame(17, 470, 3, 0, False, False, False, 0), "111101d660005595"),
    (p.ResidueFrame(17, 470, 0, 2, True, True, True, 4), "111101d6170484df"),
    (p.PollFrame(17, 0x0102, 1894665600), "2111010270ee4d8090e1"),  # 2030-01-15T00:00:00Z
    (p.AckFrame(17, 470, 1, True, False, 0b111, 0), "311101d63700f7b5"),
    (p.AckFrame(17, 470, 3, False, True, 0b001, 1), "311101d66901ca24"),
    (p.AckFrame(17, 470, 3, False, False, 0b001, 1), "311101d66101438d"),
    # FRAME_MODE 3: node 17, reading 470, values 41.4 %VWC, 25.3 degC, 64.3 %RH, pH 6.82, 1500 lx
    (p.ChannelFrame(17, 470, 0, False, False, (26, 71, 61, 3, 9)), "411101d6001a473d0309dde8"),
    (p.ChannelFrame(17, 470, 1, False, False, (10, 47, 37, 76, 98)), "411101d6400a2f254c62454e"),
    (p.ChannelFrame(17, 470, 2, False, False, (2, 35, 25, 64, 94)), "411101d680022319405eb417"),
    (p.ChannelFrame(17, 470, 0, False, False, (26, 71, 61, None, 9)), "411101d6001a473dff098b44"),
    (p.ChannelFrame(17, 470, 2, True, True, (2, 35, 25, 64, 94)), "411101d6b0022319405e9b9b"),
    (p.AckFrame(17, 470, p.ACK_WHOLE_READING, True, False, 0b111, 0), "311101d6f700e1e1"),
    (p.AckFrame(17, 470, p.ACK_WHOLE_READING, False, True, 0b001, 1), "311101d6e901d1bc"),
]


@pytest.mark.parametrize("frame,hexstr", VECTORS)
def test_hex_vectors(frame, hexstr):
    raw = frame.encode()
    assert raw.hex() == hexstr
    assert type(frame).decode(raw) == frame
    flipped = bytes([raw[0], raw[1] ^ 0x01]) + raw[2:]
    assert type(frame).decode(flipped) is None  # CRC-16 rejects a single flipped bit


def test_crc16_check_value():
    assert p.crc16_ccitt(b"123456789") == 0x29B1


def test_airtime():
    assert p.lora_airtime_ms(8) == pytest.approx(123.904)
    assert p.lora_airtime_ms(10) == pytest.approx(144.384)
    assert p.lora_airtime_ms(13) == pytest.approx(164.864)
    assert p.lora_airtime_ms(p.CHANNEL_LEN) == pytest.approx(144.384)
    assert p.PACKETS_PER_READING == 15
    # per reading: 15 x 8 B residue frames vs 3 x 12 B channel frames
    assert 15 * p.lora_airtime_ms(8) == pytest.approx(1858.56)
    assert 3 * p.lora_airtime_ms(12) == pytest.approx(433.152)


def test_channel_frame_carries_any_two_channels():
    res = [p.crt_residues(p.scale_value(q, v)) for q, v in enumerate([41.4, 25.3, 64.3, 6.82, 1500.0])]
    frames = [p.ChannelFrame.decode(p.ChannelFrame(17, 470, i, False, False, tuple(r[i] for r in res)).encode())
              for i in range(3)]
    for mask in (0b011, 0b101, 0b110, 0b111):
        for q in range(5):
            st, v = p.crt_reconstruct([f.residues[q] for f in frames], mask)
            assert st == p.CRT_OK and v == p.scale_value(q, [41.4, 25.3, 64.3, 6.82, 1500.0][q])
    bad = bytes.fromhex("411101d68067231940 5e".replace(" ", ""))
    assert p.ChannelFrame.decode(p._with_crc(bad)) is None   # 103 is not a residue mod 103


def test_firmware_unit_tests_pass():
    if subprocess.run(["which", "g++"], capture_output=True).returncode:
        pytest.skip("no C++ compiler")
    r = subprocess.run(["make", "-s", "-C", os.path.join(ROOT, "firmware", "esp32", "tests")], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.count(" 0 failures") == 2, r.stdout   # FRAME_MODE 15 and FRAME_MODE 3 builds
    subprocess.run(["make", "-s", "-C", os.path.join(ROOT, "firmware", "esp32", "tests"), "clean"], capture_output=True)


def test_packet_encoding_examples_file():
    """examples/packet_encoding_examples.json is regenerated byte for byte by the codec."""
    import json
    ex = json.load(open(os.path.join(ROOT, "examples", "packet_encoding_examples.json")))
    assert "Not original field evidence" in ex["_README"]
    node, seq = 17, 470
    vals = [ex["reading"][q] for q in p.QUANTITIES]
    res = [p.crt_residues(p.scale_value(q, v)) for q, v in enumerate(vals)]
    f15 = ex["frame_mode_15"]["frames"]
    assert len(f15) == 15 == ex["frame_mode_15"]["frames_per_reading"]
    for f in f15:
        q, i = p.QUANTITIES.index(f["quantity"]), f["residue_index"]
        assert f["channel_hz"] == p.CHANNEL_HZ[i] and f["modulus"] == p.MODULI[i] and f["residue"] == res[q][i]
        raw = bytes.fromhex(f["hex"])
        assert len(raw) == 8 and raw == p.ResidueFrame(node, seq, q, i, True, False, False, res[q][i]).encode()
    for f in ex["frame_mode_3"]["frames"]:
        i = f["channel_index"]
        assert bytes.fromhex(f["hex"]) == p.ChannelFrame(node, seq, i, False, False, tuple(r[i] for r in res)).encode()
    poll = ex["poll"]
    assert bytes.fromhex(poll["hex"]) == p.PollFrame(poll["node_id"], poll["poll_id"], poll["gateway_time"]).encode()
    assert len(bytes.fromhex(poll["hex"])) == poll["frame_bytes"] == p.POLL_LEN
    for a in ex["acks"]:
        assert p.AckFrame.decode(bytes.fromhex(a["hex"])) is not None


def test_three_channels_carry_residue_index():
    """Residue index i is always sent on channel i (868.1 / 868.3 / 868.5 MHz)."""
    assert p.CHANNEL_HZ == (868_100_000, 868_300_000, 868_500_000)
    for i in range(3):
        f = p.ResidueFrame.decode(p.ResidueFrame(1, 1, 0, i, True, False, False, 5).encode())
        assert f.residue_index == i
