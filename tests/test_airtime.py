"""LoRa time on air (Semtech AN1200.13) for every frame, per frame and per reading. All [analytical]."""
import pytest

import airtime_analysis
from common import ANALYTICAL, analytical_parameters
from gateway.mac import protocol as p


@pytest.mark.parametrize("nbytes,ms", [(8, 123.904), (10, 144.384), (12, 144.384), (13, 164.864), (34, 246.784)])
def test_frame_airtime(nbytes, ms):
    assert p.lora_airtime_ms(nbytes) == pytest.approx(ms, abs=1e-9)


def test_symbol_time_and_formula_terms():
    assert airtime_analysis.symbol_time_ms(analytical_parameters()["lora"]) == pytest.approx(4.096)
    # 8 B at SF9: ceil((64 - 36 + 28 + 16) / 36) x 5 + 8 = 18 payload symbols; (8 + 4.25 + 18) x 4.096
    assert p.lora_airtime_ms(8) == pytest.approx((8 + 4.25 + 18) * 4.096)


def test_per_reading_and_reduction():
    r = {x["id"]: x for x in airtime_analysis.results()}
    assert r["airtime.crt_reading_ms"]["value"] == pytest.approx(1858.56)
    assert r["airtime.per_packet_reduction_pct"]["value"] == pytest.approx(49.793, abs=1e-3)   # manuscript 49.8 %
    assert r["airtime.reading_ratio_crt_over_raw"]["value"] == pytest.approx(7.531, abs=1e-3)  # manuscript 7.5x
    assert r["airtime.raw_34B_ms"]["value"] == pytest.approx(246.8, abs=0.05)                 # manuscript rounding
    assert all(x["label"] in (ANALYTICAL, "derived (from analytical)") for x in r.values())


def test_firmware_and_gateway_formulas_agree_on_all_sizes():
    params = analytical_parameters()
    for n in range(1, 60):
        assert airtime_analysis.airtime(n, params["lora"]) == p.lora_airtime_ms(n)


def test_other_spreading_factors_and_ldro():
    # SF12/125 kHz requires low-data-rate optimisation; 8 B -> 991.232 ms (Semtech calculator)
    assert p.lora_airtime_ms(8, sf=12, low_dr_optimize=True) == pytest.approx(991.232)
    assert p.lora_airtime_ms(8, sf=7) == pytest.approx(36.096)  # (8 + 4.25 + 23) x 1.024
