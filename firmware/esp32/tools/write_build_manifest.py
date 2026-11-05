#!/usr/bin/env python3
"""Writes build_manifest.json for a firmware build (called by firmware/esp32/build_firmware.sh).

    python3 firmware/esp32/tools/write_build_manifest.py --env node --binary build/firmware/node/firmware.bin \
        --out build/firmware/node/build_manifest.json

The manifest records the source commit, toolchain, pinned library versions and the SHA-256 of the
binary just built. It carries no device roster: the public branch has no field inventory, and a
fresh build says nothing about which binary was flashed on the deployed nodes.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import re
import subprocess

HERE = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))


def ini_value(text, key):
    m = re.search(rf"^{key}\s*=\s*(\S+)", text, re.M)
    return m.group(1) if m else None


def manifest(env, binary):
    ini = open(os.path.join(HERE, "platformio.ini")).read()
    deps = re.findall(r"^\s{4}(\S.*@\S+)\s*$", ini.split("lib_deps =")[1].split("lib_ldf_mode")[0], re.M)
    try:
        commit = subprocess.check_output(["git", "-C", ROOT, "rev-parse", "HEAD"], text=True,
                                         stderr=subprocess.DEVNULL).strip()
    except Exception:
        commit = None
    sha = size = None
    if binary and os.path.exists(binary):
        data = open(binary, "rb").read()
        sha, size = hashlib.sha256(data).hexdigest(), len(data)
    version = re.search(r'FIRMWARE_VERSION=\\"([^\\"]+)', ini).group(1)
    frame_mode = 3 if env == "node_3frame" else 15
    return {
        "_README": "Build record of the public reference firmware. Not evidence of what was flashed on field nodes.",
        "build_id": f"agri-node-{version}-{env}-{(commit or 'nogit')[:12]}",
        "build_date": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds") if sha else None,
        "git_commit": commit,
        "pio_env": env,
        "frame_mode": frame_mode,
        "toolchain": "PlatformIO Core 6.1.16, xtensa-esp32-elf-gcc 8.4.0 (toolchain-xtensa-esp32 8.4.0+2021r2-patch5)",
        "framework_version": f"{ini_value(ini, 'platform')} (Arduino-ESP32 2.0.17, ESP-IDF 4.4.7)",
        "dependencies": deps,
        "board": ini_value(ini, "board"),
        "firmware_version": version,
        "binary": "firmware.bin" if sha else None,
        "binary_size_bytes": size,
        "binary_sha256": sha,
        "evidence_status": "built_from_public_source" if sha else "not_built",
        "field_build_identity": "not established: no binary recovered from a deployed node is published",
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", default="node")
    ap.add_argument("--binary", default=None)
    ap.add_argument("--out", default=os.path.join(ROOT, "build", "firmware", "build_manifest.json"))
    a = ap.parse_args()
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w") as f:
        json.dump(manifest(a.env, a.binary), f, indent=2)
        f.write("\n")
    print("wrote", a.out)
