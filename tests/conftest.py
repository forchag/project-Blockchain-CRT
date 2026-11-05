"""Test configuration: repository root and analysis/ on sys.path, shared helpers.

Every test here runs from public inputs only (config/, examples/). Analytical tests use illustrative parameters, while repository-scope tests exclude bundled research datasets. A green suite shows internal consistency of code, protocol and arithmetic; it does
not show that any historical experiment occurred (docs/EVIDENCE_BOUNDARIES.md).
"""
import os
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "analysis"))
sys.path.insert(0, os.path.join(ROOT, "benchmarks"))


def run_py(*args, cwd=ROOT, check=True):
    r = subprocess.run([sys.executable, *args], cwd=cwd, capture_output=True, text=True)
    if check:
        assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]
    return r


@pytest.fixture
def root():
    return ROOT


def need(tool):
    if shutil.which(tool) is None:
        pytest.skip(f"{tool} not installed")
