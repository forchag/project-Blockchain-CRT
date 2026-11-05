"""Calibration provisioning refuses incomplete or invalid records (firmware/esp32/tools/gen_calibration_nvs.py),
with the same bounds as the firmware loader (firmware/esp32/calibration.h, read_calibration).

Inputs: examples/calibration/*.csv, invented coefficients for the 12 illustrative sensors."""
import csv
import os
import re
import shutil
import subprocess
import sys

import pandas as pd
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
TOOL = os.path.join(ROOT, "firmware", "esp32", "tools", "gen_calibration_nvs.py")
CAL_H = os.path.join(ROOT, "firmware", "esp32", "calibration.h")
N_NODES = 12  # config/deployment.example.yaml
sys.path.insert(0, os.path.dirname(TOOL))
import gen_calibration_nvs as tool  # noqa: E402


def run(cal_dir, out):
    return subprocess.run([sys.executable, TOOL, "--cal-dir", str(cal_dir), "--out", str(out), "--allow-illustrative"],
                          capture_output=True, text=True)


@pytest.fixture
def cal(tmp_path):
    d = tmp_path / "cal"
    shutil.copytree(os.path.join(ROOT, "examples", "calibration"), d)
    return d


def edit(cal_dir, name, fn):
    p = cal_dir / f"{name}_calibration.csv"
    df = pd.read_csv(p, comment="#")
    df = fn(df)
    df.to_csv(p, index=False)


def test_limits_come_from_the_firmware_header():
    lim = tool.load_limits()
    # every definition must be parseable by the tool, or the tool would silently skip a bound
    assert len(lim) == len(re.findall(r"^constexpr Range CAL_LIMIT_\w+ =", open(CAL_H).read(), re.M)) == 12
    assert lim["PH_SLOPE_MV"] == (-200.0, -120.0)


def test_written_keys_are_exactly_the_keys_the_loader_reads(cal, tmp_path):
    r = run(cal, tmp_path / "nvs")
    assert r.returncode == 0, r.stderr
    assert len(os.listdir(tmp_path / "nvs")) == N_NODES
    rows = list(csv.reader(open(tmp_path / "nvs" / "ex-sns-001.csv")))
    calib = [k for k, *_ in rows[rows.index(["calib", "namespace", "", ""]) + 1:]]
    body = open(CAL_H).read()
    loader = body[body.index("inline CalStatus read_calibration"):]
    read = set(re.findall(r'\{"(\w+)", &c\.', loader)) | set(re.findall(r'src\.(?:has|get_\w+)\("(\w+)"', loader))
    assert set(calib) == read


@pytest.mark.parametrize("name,fn,why", [
    ("soil_moisture", lambda d: d[d.sensor_id != "ex-sns-007"], "no soil_moisture calibration rows"),
    ("ph", lambda d: d.assign(slope_mv_per_ph=d.slope_mv_per_ph.where(d.sensor_id != "ex-sns-007")), "slope_mv_per_ph missing"),
    ("humidity", lambda d: d.assign(rh_gain=d.rh_gain.mask((d.sensor_id == "ex-sns-007") & (d.index % 2 == 0), 1.2)),
     "rh_gain has 2 different values"),
    ("ph", lambda d: d.assign(calibration_result=d.calibration_result.mask(d.sensor_id == "ex-sns-007", "fail")), "did not pass"),
    ("ph", lambda d: d.assign(slope_mv_per_ph=d.slope_mv_per_ph.mask(d.sensor_id == "ex-sns-007", 163.9)), "ph_slope"),
    ("soil_moisture", lambda d: d.assign(coef_a2=d.coef_a2.mask(d.sensor_id == "ex-sns-007", 6e-5)), "soil:"),
    ("light", lambda d: d.assign(lux_factor=d.lux_factor.mask(d.sensor_id == "ex-sns-007", float("nan"))), "lux_factor missing"),
    ("light", lambda d: d.assign(mtreg=d.mtreg.mask(d.sensor_id == "ex-sns-007", 10)), "mtreg=10"),
])
def test_invalid_record_is_refused_and_others_still_written(cal, tmp_path, name, fn, why):
    out = tmp_path / "nvs"
    out.mkdir()
    (out / "ex-sns-007.csv").write_text("stale record from an earlier run\n")
    edit(cal, name, fn)
    r = run(cal, out)
    assert r.returncode == 1
    assert "refused ex-sns-007" in r.stderr and why in r.stderr, r.stderr
    files = set(os.listdir(out))
    assert "ex-sns-007.csv" not in files          # no file, and the stale one is gone
    assert len(files) == N_NODES - 1


def test_illustrative_records_need_explicit_permission(tmp_path):
    """The example coefficients must never be flashed onto a sensor by accident."""
    r = subprocess.run([sys.executable, TOOL, "--out", str(tmp_path / "nvs")], capture_output=True, text=True)
    assert r.returncode != 0 and "--allow-illustrative" in r.stderr
    assert not (tmp_path / "nvs").exists() or not os.listdir(tmp_path / "nvs")


def test_recalibration_uses_latest_session(cal, tmp_path):
    """A later session with new coefficients supersedes the old one instead of conflicting with it."""
    def recal(d):
        new = d[d.sensor_id == "ex-sns-007"].copy()
        new["calibration_date"] = "2030-02-15"
        new["slope_mv_per_ph"] = -158.0
        return pd.concat([d, new])
    edit(cal, "ph", recal)
    r = run(cal, tmp_path / "nvs")
    assert r.returncode == 0, r.stderr
    rows = {k: v for k, *_, v in csv.reader(open(tmp_path / "nvs" / "ex-sns-007.csv"))}
    assert rows["ph_slope"] == __import__("struct").pack("<f", -158.0).hex()
