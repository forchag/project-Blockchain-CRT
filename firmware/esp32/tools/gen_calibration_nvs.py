#!/usr/bin/env python3
"""Turns per-sensor calibration CSVs into one NVS partition CSV per node (namespaces
"node" and "calib", keys as read by firmware/esp32/sensor_drivers.cpp and main.cpp).

    python3 firmware/esp32/tools/gen_calibration_nvs.py --allow-illustrative --out build/nvs

The public branch ships only ILLUSTRATIVE calibration CSVs (examples/calibration/, every row
provenance=illustrative) and an illustrative roster (config/deployment.example.yaml). Records
built from them exercise the validation logic; they must never be flashed onto a real sensor,
so the tool refuses illustrative rows unless --allow-illustrative is given.
    # then, per node, with ESP-IDF:
    python $IDF_PATH/components/nvs_flash/nvs_partition_generator/nvs_partition_gen.py \
        generate build/nvs/ex-sns-001.csv build/nvs/ex-sns-001.bin 0x5000
    esptool.py --port /dev/ttyUSB0 write_flash 0x9000 build/nvs/ex-sns-001.bin

A node is provisioned only from a complete, valid record: present in all four
CSVs, one consistent value per coefficient, every value finite and inside the
CAL_LIMIT_* bounds parsed from firmware/esp32/calibration.h (the firmware's
read_calibration() applies the same bounds), and every pH calibration row
"pass". Only each sensor's latest calibration session (latest date) is used;
within it every coefficient must have one value. Values are checked after rounding to float32, exactly as the node stores
them. A node whose record fails gets no NVS file; the tool lists every problem
and exits 1.

Temperature: the field temperature sensor is a Maxim DS18B20 (1-Wire, factory-trimmed
to +-0.5 degC, 12-bit resolution). It is not calibrated here and no DS18B20 row exists
in the calibration CSVs. The t_gain / t_offset coefficients in humidity_calibration.csv
describe the SHT31's own temperature output, retained for the bench calibration
workflow and for NVS layout stability (see calibration.h). The firmware's field sensing
path (sensor_drivers.cpp::read_ds18b20) does not apply them; they are written to NVS
and validated against CAL_LIMIT_T_* for record integrity, but never touch the field
temperature reading.
"""
import argparse
import csv
import math
import os
import re
import struct
import sys

import pandas as pd
import yaml

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
CAL_VERSION = 1  # calibration.h CALIBRATION_VERSION


CAL_H = os.path.join(ROOT, "firmware", "esp32", "calibration.h")


def load_limits(path=CAL_H):
    """CAL_LIMIT_<NAME> = {lo, hi} from calibration.h, the firmware's own bounds."""
    lim = {}
    for name, lo, hi in re.findall(r"constexpr Range CAL_LIMIT_(\w+) = \{(-?[\d.]+)f, (-?[\d.]+)f\};", open(path).read()):
        lim[name] = (float(lo), float(hi))
    if not lim:
        sys.exit(f"no CAL_LIMIT_* bounds found in {path}")
    return lim


def as_f32(x):
    return struct.unpack("<f", struct.pack("<f", float(x)))[0]


def one_value(df, col, problems, label):
    """The coefficient must be identical on every row of the sensor's record."""
    vals = df[col].dropna().unique()
    if len(df[col].dropna()) != len(df):
        problems.append(f"{label}: {col} missing on {len(df) - len(df[col].dropna())} row(s)")
        return None
    if len(vals) != 1:
        problems.append(f"{label}: {col} has {len(vals)} different values")
        return None
    return vals[0]


DATE_COL = {"soil_moisture": "calibration_date", "ph": "calibration_date", "humidity": "calibration_timestamp",
            "light": "calibration_date"}


def latest_session(df, part):
    """Rows of the most recent calibration session (recalibrations append rows with a later date)."""
    col = DATE_COL[part]
    if df.empty or col not in df.columns:
        return df
    day = df[col].astype(str).str[:10]
    return df[day == day.max()]


def validate_record(sid, soil, ph, hum, light, lim):
    """Returns (record, problems). record holds the NVS values; problems is empty if it may be provisioned."""
    problems, rec = [], {}
    parts = {"soil_moisture": soil[soil.sensor_id == sid], "ph": ph[ph.sensor_id == sid],
             "humidity": hum[hum.sensor_id == sid], "light": light[light.sensor_id == sid]}
    parts = {k: latest_session(v, k) for k, v in parts.items()}
    for name, part in parts.items():
        if part.empty:
            problems.append(f"no {name} calibration rows")
    if problems:
        return None, problems
    cols = {"soil_a0": ("soil_moisture", "coef_a0"), "soil_a1": ("soil_moisture", "coef_a1"),
            "soil_a2": ("soil_moisture", "coef_a2"), "soil_dry": ("soil_moisture", "dry_reference_mv"),
            "soil_wet": ("soil_moisture", "wet_reference_mv"), "ph_e7": ("ph", "intercept_mv_at_ph7"),
            "ph_slope": ("ph", "slope_mv_per_ph"), "rh_gain": ("humidity", "rh_gain"), "rh_off": ("humidity", "rh_offset"),
            # t_gain / t_off describe the SHT31 temperature output (bench diagnostics only;
            # the DS18B20 field temperature is factory-trimmed and uses no gain/offset)
            "t_gain": ("humidity", "t_gain"), "t_off": ("humidity", "t_offset"),
            "mtreg": ("light", "mtreg"), "lux_f": ("light", "lux_factor")}
    for key, (part, col) in cols.items():
        if col not in parts[part].columns:
            problems.append(f"{part}: column {col} absent")
            continue
        v = one_value(parts[part], col, problems, part)
        if v is None:
            continue
        try:
            v = float(v)
        except (TypeError, ValueError):
            problems.append(f"{part}: {col}={v!r} is not a number")
            continue
        if not math.isfinite(v):
            problems.append(f"{part}: {col} is not finite")
            continue
        rec[key] = int(round(v)) if key in ("soil_dry", "soil_wet", "mtreg") else as_f32(v)
    if problems:
        return None, problems
    bad_ph = parts["ph"][parts["ph"].calibration_result != "pass"]
    if len(bad_ph):
        problems.append(f"ph: {len(bad_ph)} buffer point(s) did not pass ({', '.join(map(str, bad_ph.reference_ph))})")

    def within(v, name):
        lo, hi = lim[name]
        return lo <= v <= hi

    dry, wet = rec["soil_dry"], rec["soil_wet"]
    vwc = lambda mv: rec["soil_a0"] + rec["soil_a1"] * mv + rec["soil_a2"] * mv * mv  # noqa: E731
    if not (within(dry, "SOIL_REF_MV") and within(wet, "SOIL_REF_MV") and within(dry - wet, "SOIL_SPAN_MV")):
        problems.append(f"soil: references dry {dry} mV / wet {wet} mV outside {lim['SOIL_REF_MV']} or span {lim['SOIL_SPAN_MV']}")
    elif not (within(vwc(dry), "SOIL_VWC_DRY") and within(vwc(wet), "SOIL_VWC_WET")):
        problems.append(f"soil: fit gives {vwc(dry):.1f} %VWC dry / {vwc(wet):.1f} %VWC wet")
    elif rec["soil_a1"] + 2 * rec["soil_a2"] * wet >= 0 or rec["soil_a1"] + 2 * rec["soil_a2"] * dry >= 0:
        problems.append("soil: fit is not decreasing across the dry-wet span")
    for key, name in [("ph_e7", "PH_E7_MV"), ("ph_slope", "PH_SLOPE_MV"), ("rh_gain", "RH_GAIN"), ("rh_off", "RH_OFFSET"),
                      ("t_gain", "T_GAIN"), ("t_off", "T_OFFSET"), ("mtreg", "BH1750_MTREG"), ("lux_f", "LUX_FACTOR")]:
        if not within(rec[key], name):
            problems.append(f"{key}={rec[key]:g} outside {lim[name]}")
    return rec, problems


def f32(x):
    """Preferences::getFloat() reads a 4-byte blob: little-endian IEEE-754 single."""
    return ("data", "hex2bin", struct.pack("<f", float(x)).hex())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cal-dir", default=os.path.join(ROOT, "examples", "calibration"))
    ap.add_argument("--deployment", "--mac-config", dest="deployment",
                    default=os.path.join(ROOT, "config", "deployment.example.yaml"))
    ap.add_argument("--out", default=os.path.join(ROOT, "build", "nvs"))
    ap.add_argument("--allow-illustrative", action="store_true",
                    help="accept rows with provenance=illustrative (tests and demonstrations only)")
    a = ap.parse_args()
    soil = pd.read_csv(os.path.join(a.cal_dir, "soil_moisture_calibration.csv"), comment="#")
    ph = pd.read_csv(os.path.join(a.cal_dir, "ph_calibration.csv"), comment="#")
    hum = pd.read_csv(os.path.join(a.cal_dir, "humidity_calibration.csv"), comment="#")
    light = pd.read_csv(os.path.join(a.cal_dir, "light_calibration.csv"), comment="#")
    illustrative = [n for n, d in (("soil_moisture", soil), ("ph", ph), ("humidity", hum), ("light", light))
                    if "provenance" in d.columns and (d.provenance == "illustrative").any()]
    if illustrative and not a.allow_illustrative:
        sys.exit("refusing to provision from illustrative calibration rows (" + ", ".join(illustrative)
                 + "): pass --allow-illustrative for tests; never flash these records onto a sensor")
    mac = yaml.safe_load(open(a.deployment))
    lim = load_limits()
    os.makedirs(a.out, exist_ok=True)
    n, refused = 0, {}
    for zone, z in mac["zones"].items():
        for node in z["nodes"]:
            sid = node["sensor_id"]
            rec, problems = validate_record(sid, soil, ph, hum, light, lim)
            out_path = os.path.join(a.out, f"{sid}.csv")
            if problems:
                refused[sid] = problems
                if os.path.exists(out_path):
                    os.remove(out_path)  # never leave an older, now-invalid record for this node
                continue
            rows = [("key", "type", "encoding", "value"),
                    ("node", "namespace", "", ""),
                    ("node_id", "data", "u8", node["node_id"]),
                    ("zone", "data", "u8", z["zone_index"]),
                    ("position", "data", "u8", node["position"]),
                    ("calib", "namespace", "", ""),
                    ("version", "data", "u32", CAL_VERSION),
                    *[(k, *f32(rec[k])) for k in ("soil_a0", "soil_a1", "soil_a2")],
                    ("soil_dry", "data", "u16", rec["soil_dry"]),
                    ("soil_wet", "data", "u16", rec["soil_wet"]),
                    *[(k, *f32(rec[k])) for k in ("ph_e7", "ph_slope", "rh_gain", "rh_off", "t_gain", "t_off")],
                    ("mtreg", "data", "u8", rec["mtreg"]),
                    ("lux_f", *f32(rec["lux_f"]))]
            with open(out_path, "w", newline="") as f:
                csv.writer(f).writerows(rows)
            n += 1
    print(f"wrote {n} NVS CSVs to {a.out}")
    for sid, problems in refused.items():
        print(f"refused {sid}: " + "; ".join(problems), file=sys.stderr)
    if refused:
        sys.exit(f"{len(refused)} node(s) not provisioned: incomplete or invalid calibration records")


if __name__ == "__main__":
    main()
