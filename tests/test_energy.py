"""Transmission energy: analytical model, bench-measured values kept separate, radio-only vs complete node."""
import pytest

import energy_reconciliation as er
from common import ANALYTICAL, BENCH, analytical_parameters, derived
from gateway.mac import protocol as p

PARAMS = analytical_parameters()


def by_id(rows):
    return {r["id"]: r for r in rows}


def test_analytical_energy_is_power_times_airtime():
    e = PARAMS["energy_model"]
    assert e["assumed"] is True
    pw = e["supply_v"] * e["tx_current_ma"]
    for n in (8, 12, 13, 34):
        assert er.frame_energy_mj(n, PARAMS) == pytest.approx(pw * p.lora_airtime_ms(n) / 1000)
    r = by_id(er.results(bench_path=None)[0])
    assert r["energy.analytical_crt_reading_mj"]["value"] == pytest.approx(15 * r["energy.analytical_residue_8B_mj"]["value"], abs=1e-3)  # 4-decimal storage
    # constant TX power: the analytical energy ratio equals the airtime ratio
    assert r["energy.analytical_ratio_crt_over_raw"]["value"] == pytest.approx(15 * p.lora_airtime_ms(8) / p.lora_airtime_ms(34), abs=1e-4)
    assert r["energy.analytical_residue_8B_mj"]["label"] == ANALYTICAL
    assert "assumed" in r["energy.analytical_residue_8B_mj"]["note"]


def test_bench_values_are_labelled_and_never_analytical():
    r = by_id(er.results()[0])
    for rid, v in [("energy.bench_residue_8B_mj", 12.32), ("energy.bench_raw_34B_mj", 24.86),
                   ("energy.bench_fixed_13B_mj", 16.36), ("energy.bench_crt_reading_mj", 184.8)]:
        assert r[rid]["value"] == v and r[rid]["label"] == BENCH
        assert "not reproducible" in r[rid]["note"]
    assert r["energy.bench_ratio_crt_over_raw"]["value"] == pytest.approx(7.4336, abs=1e-4)   # manuscript ~7.4x
    assert r["energy.bench_ratio_crt_over_raw"]["label"] == derived(BENCH)
    assert r["energy.bench_check_15x_residue"]["value"] == pytest.approx(184.8)
    # the analytical model does not claim to reproduce the bench numbers
    assert r["energy.analytical_residue_8B_mj"]["value"] != pytest.approx(12.32, abs=1e-3)


def test_missing_bench_file_gives_placeholder_not_a_number(tmp_path):
    r = by_id(er.results(bench_path=str(tmp_path / "absent.yaml"))[0])
    for rid in ("energy.bench_residue_8B_mj", "energy.bench_crt_reading_mj"):
        assert r[rid]["value"] is None and r[rid]["label"] == BENCH
        assert r[rid]["note"] == "historical bench value not included"


def test_bench_file_must_declare_its_label(tmp_path):
    f = tmp_path / "bad.yaml"
    f.write_text("label: analytical\nper_packet_mj: {residue_8B: 1}\n")
    with pytest.raises(ValueError):
        er.load_bench(str(f))


def test_radio_only_and_complete_node_are_separate():
    out, table = er.results()
    assert {t["boundary"] for t in table} == {"radio-only"}
    r = by_id(out)
    node = r["energy.node_cycle_total_mj"]
    assert "ASSUMED" in node["note"]
    tx = r["energy.analytical_crt_reading_mj"]["value"]
    assert node["value"] > tx                                   # complete node includes sleep, MCU, RX
    share = r["energy.node_tx_share_pct"]["value"]
    assert share == pytest.approx(100 * tx / node["value"], abs=1e-3) and 0 < share < 100
    n = PARAMS["node_energy_model"]
    parts = er.node_cycle_energy_mj(PARAMS, tx)
    assert parts["sleep_mj"] == pytest.approx(n["supply_v"] * n["sleep_current_ua"] / 1000 * (n["cycle_s"] - n["awake_s"]))
    assert parts["total_mj"] == pytest.approx(parts["sleep_mj"] + parts["awake_mj"] + parts["rx_mj"] + parts["tx_mj"])
