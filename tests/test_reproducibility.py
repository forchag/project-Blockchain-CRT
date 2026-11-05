"""The analytical reproduction runs from public inputs alone, is deterministic, labels every number,
and the documentation classifies results by evidence class without overclaiming."""
import glob
import json
import os
import re
import shutil

import pytest

from conftest import ROOT, run_py

DOCS = os.path.join(ROOT, "docs")


@pytest.fixture(scope="module")
def results(tmp_path_factory):
    out = tmp_path_factory.mktemp("analytical")
    run_py("analysis/reproduce_analytical_results.py", "--out", str(out), "--no-figures", "--no-r")
    return out, json.load(open(out / "analytical_results.json"))


def test_reproduction_validates(results):
    out, _ = results
    r = run_py("analysis/validate_analytical_outputs.py", "--results", str(out / "analytical_results.json"))
    assert ", 0 problems" in r.stdout


def test_every_number_is_labelled(results):
    from common import is_valid_label
    _, doc = results
    assert "does not show that a historical experiment occurred" in doc["evidence_boundary"]
    for r in doc["results"]:
        assert is_valid_label(r["label"]), r
        assert r["section"]
    labels = {r["label"] for r in doc["results"]}
    assert {"analytical", "bench-measured", "field-measured", "measured"} <= labels


def test_deterministic(results, tmp_path):
    out, first = results
    run_py("analysis/reproduce_analytical_results.py", "--out", str(tmp_path), "--no-figures", "--no-r")
    second = json.load(open(tmp_path / "analytical_results.json"))
    assert first["results"] == second["results"]


def test_runs_with_no_original_data_directory(tmp_path):
    """Copy only the public tree into a fresh directory with no data/ and run the reproduction there."""
    dst = tmp_path / "repo"
    shutil.copytree(ROOT, dst, ignore=shutil.ignore_patterns(".git", "data", "external", "results", "build", "__pycache__", ".pytest_cache",
                                                              "crypto", "fabric-ca", ".env"))
    assert not (dst / "data").exists()
    r = run_py("analysis/reproduce_analytical_results.py", "--out", str(dst / "results" / "analytical"), "--no-figures",
               "--no-r", cwd=str(dst))
    assert "labelled quantities" in r.stdout
    assert not (dst / "data").exists()


def test_no_script_reads_original_data_paths():
    pat = re.compile(r"data/(raw|processed|calibration|labels\.json)|data\", \"(raw|processed)")
    for f in glob.glob(os.path.join(ROOT, "**", "*.*"), recursive=True):
        if f.endswith((".py", ".sh", ".R", ".go", ".yaml", ".yml")) and "/tests/" not in f and "/results/" not in f and "/data/" not in f and "/external/" not in f:
            assert not pat.search(open(f, errors="ignore").read()), f


def test_r_example_when_available(tmp_path):
    if shutil.which("Rscript") is None:
        pytest.skip("Rscript not installed")
    import subprocess
    r = subprocess.run(["Rscript", os.path.join(ROOT, "analysis", "agronomic_statistics.R"), "--out", str(tmp_path)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "not a reproduction of field results" in r.stdout
    rows = open(tmp_path / "agronomic_rcbd_example.csv").read()
    assert "illustrative" in rows and rows.count("\n") == 9    # header + 2 crops x 4 responses


# ------------------------------------------------------------------ documentation boundaries
def test_evidence_boundary_statements():
    eb = open(os.path.join(DOCS, "EVIDENCE_BOUNDARIES.md")).read()
    assert "do not prove" in eb or "does not prove" in eb
    readme = open(os.path.join(ROOT, "README.md")).read()
    assert "not" in readme and "2026" in readme
    da = open(os.path.join(DOCS, "DATA_AVAILABILITY.md")).read()
    assert "not included" in da
    assert "--data-dir" in da
