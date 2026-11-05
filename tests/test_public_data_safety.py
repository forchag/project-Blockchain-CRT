"""Privacy and safety checks for the public branch .

No original data directory, no key material or certificates, no generated network state, no
binaries, no field identifiers or field-era timestamps in example data, and every example file
declares itself illustrative. Run before every commit: python -m pytest tests/test_public_data_safety.py
"""
import json
import os
import re
import subprocess

import pytest

from conftest import ROOT

BINARY_EXT = (".bin", ".elf", ".hex", ".tar", ".gz", ".tgz", ".zip", ".7z", ".db", ".sqlite", ".parquet", ".xlsx",
              ".pdf", ".png", ".jpg", ".jpeg", ".block", ".pb")
SECRET_NAMES = re.compile(r"(^|/)(\.env|.*_sk|.*\.pem|.*\.key|.*\.p12|.*\.pfx|.*\.jks|id_rsa.*|.*\.keystore)$")
PEM = re.compile("-----BEGIN " + r"(?:[A-Z0-9 ]+ )?(?:PRIVATE KEY|CERTIFICATE|EC PARAMETERS)-----")
ILLUSTRATIVE = "Not original field evidence"
MAX_FILE_BYTES = 512 * 1024


def tracked_files():
    r = subprocess.run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], cwd=ROOT,
                       capture_output=True, text=True)
    if r.returncode == 0 and r.stdout:
        return [f for f in r.stdout.split("\0") if f and os.path.isfile(os.path.join(ROOT, f))]
    out = []  # not a git checkout: walk the tree, skipping ignored outputs
    for d, dirs, files in os.walk(ROOT):
        dirs[:] = [x for x in dirs if x not in (".git", "results", "build", "__pycache__", ".pytest_cache", "crypto", "fabric-ca")]
        out += [os.path.relpath(os.path.join(d, f), ROOT) for f in files]
    return out


FILES = tracked_files()


def text(f):
    with open(os.path.join(ROOT, f), "rb") as fh:
        b = fh.read()
    return b.decode("utf-8", errors="replace")


def test_no_bundled_research_datasets():
    assert not [f for f in FILES if f.startswith(("data/", "external/"))]


def test_no_key_material_certificates_or_env_files():
    assert not [f for f in FILES if SECRET_NAMES.search(f) and not f.endswith(".env.example")]
    hits = [f for f in FILES if PEM.search(text(f))]
    assert not hits, hits


def test_no_binaries_or_archives():
    assert not [f for f in FILES if f.lower().endswith(BINARY_EXT)]
    for f in FILES:
        with open(os.path.join(ROOT, f), "rb") as fh:
            assert b"\0" not in fh.read(8192), f"binary content in {f}"


def test_no_generated_fabric_state():
    assert not [f for f in FILES if re.search(r"blockchain/example-network/(crypto|fabric-ca)/", f)]


def test_file_sizes_are_small():
    big = [(f, os.path.getsize(os.path.join(ROOT, f))) for f in FILES if os.path.getsize(os.path.join(ROOT, f)) > MAX_FILE_BYTES]
    assert not big, big


def test_every_example_declares_itself_illustrative():
    ex = [f for f in FILES if f.startswith("examples/")]
    assert len(ex) >= 10
    for f in ex:
        assert ILLUSTRATIVE in text(f), f
    for f in ("config/deployment.example.yaml", "config/economics.example.yaml"):
        assert ILLUSTRATIVE in text(f), f


def test_example_identifiers_are_not_field_identifiers():
    field_ids = re.compile(r"(?<![\w-])(sns-\d{3}|esp32-\d{3}|gw-(north|south|east|west))(?![\w-])")
    for f in FILES:
        if f.startswith(("examples/", "config/", "tests/", "gateway/", "blockchain/", "benchmarks/", "firmware/", "analysis/")) and not f.endswith(".md"):
            m = field_ids.search(text(f))
            assert m is None, f"{f}: {m.group(0) if m else ''}"


def test_example_timestamps_are_not_from_the_campaign():
    """Illustrative data uses 2030 dates; nothing in examples/ looks like a 2024-2026 log timestamp."""
    stamp = re.compile(r"\b20(2[4-6])-\d{2}-\d{2}[T ]\d{2}:\d{2}")
    for f in FILES:
        if f.startswith("examples/") or f in ("config/deployment.example.yaml",):
            m = stamp.search(text(f))
            assert m is None, f"{f}: {m.group(0) if m else ''}"


def test_example_txids_are_placeholders():
    txid64 = re.compile(r"(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])")
    for f in FILES:
        if f.startswith("examples/"):
            hits = [h for h in txid64.findall(text(f)) if set(h) != {"0"}]
            assert not hits, f"{f}: 64-hex value {hits[:1]}"


def test_example_json_files_parse_and_carry_readme():
    for f in FILES:
        if f.startswith("examples/") and f.endswith(".json"):
            assert "_README" in json.load(open(os.path.join(ROOT, f))), f
        if f.startswith("examples/") and f.endswith(".jsonl"):
            first = json.loads(open(os.path.join(ROOT, f)).readline())
            assert "_README" in first, f


def test_env_template_has_placeholders_only():
    env = text("blockchain/example-network/.env.example")
    assert "CA_ADMIN_PASSWORD=change-me-before-use" in env
    assert not re.search(r"(?i)(secret|token|apikey)\s*=\s*\S{8,}", env)


def test_firmware_manifest_is_an_example():
    m = json.load(open(os.path.join(ROOT, "firmware", "esp32", "build_manifest.example.json")))
    assert m["evidence_status"] == "example_placeholder" and set(m["binary_sha256"]) == {"0"}
    assert "device_ids" not in m


def test_gitignore_blocks_sensitive_outputs():
    gi = text(".gitignore")
    for pat in ("results/", "build/", "*.pem", "*_sk", "*.key", "blockchain/example-network/crypto/",
                "blockchain/example-network/.env", "data/private/", "data/quarantine/", "*.bin"):
        assert pat in gi, pat


def test_no_sensitive_raw_crypto_material_inside_organised_data():
    banned = [f for f in FILES if f.startswith('data/') and
              (f.endswith(('.pem', '.key', '.block', '.p12', '.pfx')) or '/crypto/' in f)]
    assert not banned, banned
