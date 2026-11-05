"""Analytical models: transmission cost index (per-packet -> per-reading), capacity and duty cycle,
collision margins, economics identities, Raft timing window and trial statistics, fixed-point
baseline, RBAC-overhead arithmetic. These test the code and the models; none asserts that a bench
or field measurement was reproduced."""
import copy
import math
import os

import pytest

import collision_analysis
import economic_reconciliation as econ
import fixed_point_baseline as fpb
import raft_recovery
import transmission_cost_index as tci
from common import (ANALYTICAL, BENCH, FIELD, MEASURED, analytical_parameters, derived, is_valid_label, radio_config,
                    read_csv, record)
from conftest import ROOT, run_py
from gateway.mac import protocol as p
from reproduce_analytical_results import downlink_node_ceiling, md1_mean_wait_ms, stress_ceiling

PARAMS = analytical_parameters()


# ------------------------------------------------------------------ provenance labels
def test_label_vocabulary():
    for good in (ANALYTICAL, BENCH, FIELD, MEASURED, derived(BENCH), derived(FIELD, "public tariff"),
                 derived(ANALYTICAL, BENCH)):
        assert is_valid_label(good)
    for bad in ("measured-ish", "derived", "derived (from vibes)", "simulated", ""):
        assert not is_valid_label(bad)
    with pytest.raises(ValueError):
        record("x", "x", 1.0, "", "simulated", "")
    with pytest.raises(ValueError):
        derived("simulated")


# ------------------------------------------------------------------ 9.6 transmission cost index
def test_tci_processing_is_normalised_per_reading_not_per_packet():
    """The CRT processing time is recorded per residue packet and must be multiplied by 15."""
    per_packet = PARAMS["transmission_cost_index"]["processing_ms_per_packet"]["crt"]
    assert tci.processing_per_reading_ms(per_packet, 15) == pytest.approx(15 * per_packet)
    comp = tci.components(PARAMS)
    assert comp["crt"]["packets_per_reading"] == 15
    assert comp["crt"]["processing_per_reading_ms"] == pytest.approx(15 * comp["crt"]["processing_per_packet_ms"])
    assert comp["raw"]["processing_per_reading_ms"] == pytest.approx(comp["raw"]["processing_per_packet_ms"])
    # The per-reading CRT figure, not the per-packet one, enters the normalisation.
    t = PARAMS["transmission_cost_index"]
    scored = tci.tci(comp, t["alpha"], t["beta"], t["gamma"])
    max_p = max(c["processing_per_reading_ms"] for c in comp.values())
    assert scored["crt"]["P_norm"] == pytest.approx(comp["crt"]["processing_per_reading_ms"] / max_p, abs=1e-6)
    wrong = copy.deepcopy(comp)
    wrong["crt"]["processing_per_reading_ms"] = wrong["crt"]["processing_per_packet_ms"]  # the error being guarded
    assert tci.tci(wrong, t["alpha"], t["beta"], t["gamma"])["crt"]["TCI"] != scored["crt"]["TCI"]
    with pytest.raises(ValueError):
        tci.processing_per_reading_ms(per_packet, 0)


def test_tci_weights_and_bounds():
    t = PARAMS["transmission_cost_index"]
    assert (t["alpha"], t["beta"], t["gamma"]) == (0.5, 0.3, 0.2)
    scored = tci.tci(tci.components(PARAMS), t["alpha"], t["beta"], t["gamma"])
    for s in scored.values():
        assert 0 < s["TCI"] <= 1 + 1e-12
        assert s["TCI"] == pytest.approx(0.5 * s["E_norm"] + 0.3 * s["A_norm"] + 0.2 * s["P_norm"], abs=1e-5)
    with pytest.raises(ValueError):
        tci.tci(tci.components(PARAMS), 0.5, 0.5, 0.5)


def test_tci_with_bench_energy_is_labelled_derived_from_bench():
    out, _ = tci.results(energy_source="bench")
    assert all(r["label"] == derived(BENCH, ANALYTICAL) for r in out if r["id"].startswith("tci.") and "processing" not in r["id"])


# ------------------------------------------------------------------ 9.5 capacity and duty cycle
def test_stress_ceilings():
    assert stress_ceiling(60, 3, 15, p.lora_airtime_ms(8)) == 96     # manuscript: 96 readings
    assert stress_ceiling(60, 3, 1, p.lora_airtime_ms(34)) == 729    # manuscript: 729 readings
    assert stress_ceiling(60, 3, 15, 123.9) == 96                     # manuscript's rounded T
    assert stress_ceiling(10, 1, 1, 1000.0) == 9                      # exact boundary: rho = 1 is not stable


def test_md1_wait():
    assert md1_mean_wait_ms(0.0, 123.904) == 0.0
    assert md1_mean_wait_ms(0.5, 100.0) == pytest.approx(50.0)
    assert md1_mean_wait_ms(1.0, 100.0) == math.inf


def test_downlink_duty_cycle_ceiling_and_frame_size_formula():
    cap = radio_config()["capacity"]
    d8 = downlink_node_ceiling(8, 8, 5, 1800, 0.01, 4)
    assert d8["t_node_ms"] == pytest.approx(6 * 123.904)
    assert d8["network"] == 96 and d8["network_from_floored"] == 96     # manuscript: 96 nodes
    d10 = downlink_node_ceiling(10, 8, 5, 1800, 0.01, 4)
    assert d10["network"] == 94                                         # the PollFrame actually implemented
    assert cap["poll_frame_bytes_assumed"] == 8 and cap["poll_frame_bytes_implemented"] == p.POLL_LEN == 10
    # retransmissions shrink the ceiling proportionally
    assert downlink_node_ceiling(8, 8, 5, 1800, 0.01, 4, retx_fraction=0.1)["network"] == 88


# ------------------------------------------------------------------ collision analysis
def test_collision_margins():
    rows = {(r["part"], r["metric"], r["external_devices"]): r for r in collision_analysis.rows()}
    assert rows[("A_scheduled", "A_slot_capacity_per_zone", None)]["value"] == 13
    assert rows[("A_scheduled", "A_slack_ms", None)]["value"] > 0
    assert rows[("A_scheduled", "A_collision_probability", None)]["value"] == 0.0
    assert rows[("C_external_aloha", "residue_collision_probability", 0)]["value"] == 0.0
    pc = [rows[("C_external_aloha", "residue_collision_probability", n)]["value"] for n in (10, 100, 1000)]
    assert pc == sorted(pc) and pc[-1] < 0.05
    assert all(r["label"] == ANALYTICAL for r in rows.values())


# ------------------------------------------------------------------ 9.7 economics
def test_economics_identities_and_defaults():
    out, table = econ.results()
    base = econ.load_yaml(econ.DEFAULT_CONFIG)
    assert (base["capex_usd"], base["opex_annual_usd"], base["discount_rate"], base["seasons_per_year"]) == (3540, 400, 0.08, 2)
    assert base["water"]["tariff_tnd_per_m3"] == 0.100 and base["water"]["fx_tnd_per_usd"] == 3.1
    assert base["water"]["tariff_sensitivity_tnd_per_m3"] == [0.050, 0.150, 0.300]
    for x in table:
        assert x["gross_annual_benefit_usd"] == pytest.approx(
            x["annual_water_benefit_usd"] + x["annual_labour_benefit_usd"] + x["annual_yield_benefit_usd"], abs=0.02)
        assert x["net_annual_benefit_usd"] == pytest.approx(x["gross_annual_benefit_usd"] - x["opex_annual_usd"], abs=0.02)
        assert x["roi_pct"] == pytest.approx(x["net_annual_benefit_usd"] / x["capex_usd"] * 100, abs=0.01)
        npv = -x["capex_usd"] + sum(x["net_annual_benefit_usd"] / (1 + x["discount_rate"]) ** t for t in range(1, 6))
        assert x["npv_usd"] == pytest.approx(npv, abs=0.05)
        assert x["label"] == "derived (from public tariff assumptions)"
        assert x["season_inputs_provenance"] == "illustrative"
    cases = {x["case"] for x in table}
    assert {"tariff_0.050_tnd_m3", "tariff_0.150_tnd_m3", "tariff_0.300_tnd_m3", "labour_excluded"} <= cases
    water = {x["case"]: x["annual_water_benefit_usd"] for x in table}
    assert water["tariff_0.300_tnd_m3"] == pytest.approx(3 * water["base"], abs=0.02)


def test_reported_roi_and_payback_are_mutually_consistent():
    out, _ = econ.results()
    r = {x["id"]: x for x in out}
    assert r["economics.payback_implied_by_reported_roi"]["value"] == pytest.approx(1.0101, abs=1e-4)
    assert round(r["economics.payback_implied_by_reported_roi"]["value"], 2) == r["economics.reported_payback_years"]["value"]
    assert r["economics.reported_roi_pct"]["label"] == "derived (from field-measured + public tariff)"
    # the example result must not masquerade as the field result
    assert r["economics.example_roi_pct"]["value"] != pytest.approx(99.0, abs=1.0)


# ------------------------------------------------------------------ Raft
def test_raft_election_window():
    w = raft_recovery.analytical_window(PARAMS["raft"], survivors=2)
    assert (w["election_min_s"], w["election_max_s"], w["recovery_max_s"]) == (4.5, 9.5, 10.0)
    dist = raft_recovery.election_distribution(10, 2)
    assert sum(dist.values()) == pytest.approx(1.0) and min(dist) == 10 and max(dist) == 19
    # one survivor: plain uniform over 10..19 ticks, mean 14.5 ticks
    assert sum(k * v for k, v in raft_recovery.election_distribution(10, 1).items()) == pytest.approx(14.5)
    assert w["election_min_s"] < w["election_mean_s"] < w["election_max_s"]


def test_raft_statistics_pipeline_on_illustrative_trials():
    trials = read_csv(os.path.join(ROOT, "examples", "example_recovery_trials.csv"))
    rows, unsuccessful, issues = raft_recovery.summarise(trials)
    assert unsuccessful == ["EX-RT-06"] and issues == []
    allrow = next(r for r in rows if r["group"] == "recovery:all")
    assert allrow["trials"] == 6 and allrow["successful"] == 5 and allrow["input_provenance"] == "illustrative"
    assert raft_recovery.wilson(0, 0)[0] != raft_recovery.wilson(0, 0)[0]   # NaN for no trials
    lo, hi = raft_recovery.wilson(2, 22)
    assert 0 < lo < 2 / 22 < hi < 1


def test_raft_validation_flags_bad_timestamps():
    t = [dict(trial_id="X", failure_type="abrupt_kill", failure_time="2030-01-15T01:00:10Z",
              leader_election_completion_time="2030-01-15T01:00:05Z", first_successful_tx_time="2030-01-15T01:00:04Z",
              recovery_duration_s="9.0", outcome="recovered", provenance="illustrative")]
    _, issues = raft_recovery.validate(t)
    kinds = {k for _, k in issues}
    assert {"election before failure", "first transaction before election",
            "stored recovery_duration_s disagrees with timestamps"} <= kinds


def test_raft_bench_value_is_labelled_bench():
    r = {x["id"]: x for x in raft_recovery.results()}
    assert r["raft.bench_mean_recovery_s"]["label"] == BENCH and r["raft.bench_mean_recovery_s"]["value"] == 8.2


# ------------------------------------------------------------------ fixed-point baseline
def test_fixed_point_frame():
    assert (fpb.PAYLOAD_BITS, fpb.PAYLOAD_BYTES, fpb.ID_OVERHEAD_BYTES, fpb.FRAME_BYTES) == (55, 7, 6, 13)
    assert fpb.airtime_ms() == pytest.approx(164.864)
    f = fpb.encode(17, 470, [41.4, 25.3, 64.3, 6.82, 1500.0])
    assert len(f) == 13
    node, seq, vals = fpb.decode(f)
    assert (node, seq) == (17, 470)
    assert vals == pytest.approx([41.4, 25.3, 64.3, 6.82, 1500.0], abs=3.0)
    assert fpb.decode(f[:-1] + bytes([f[-1] ^ 1])) is None
    out = run_py("benchmarks/fixed_point_baseline.py").stdout
    assert "164.864 ms" in out and "illustrative readings" in out


# ------------------------------------------------------------------ RBAC overhead arithmetic
def test_rbac_overhead_on_illustrative_runs(tmp_path):
    out = tmp_path / "rbac.csv"
    r = run_py("benchmarks/rbac_overhead.py", os.path.join(ROOT, "examples", "example_benchmark_runs.csv"), "--out", str(out))
    assert "provenance illustrative" in r.stdout
    rows = read_csv(str(out))
    assert {x["provenance"] for x in rows} == {"illustrative"}
    for x in rows:
        b, h = float(x["baseline_tps"]), float(x["hrbac_tps"])
        assert float(x["throughput_reduction_pct"]) == pytest.approx((b - h) / b * 100, abs=0.06)


def test_published_overhead_arithmetic():
    """(54.9 - 40.4) / 54.9 = 26.41 %: the manuscript's 26.5 % needs unrounded TPS (documented)."""
    assert (54.9 - 40.4) / 54.9 * 100 == pytest.approx(26.412, abs=1e-3)
