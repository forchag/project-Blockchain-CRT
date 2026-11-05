"""Blockchain package: chaincode and submitter Go tests, the example network's configuration, the
example transactions, and (opt-in) the end-to-end run against a live example network.

    FABRIC_EXAMPLE_NETWORK=1 python -m pytest tests/test_blockchain_integration.py   # network must be up

Outcomes on the example network describe that run only (example-network-run); they are not the
manuscript's bench- or field-measured figures."""
import json
import os
import re
import subprocess

import pytest
import yaml

from conftest import ROOT, need

NET = os.path.join(ROOT, "blockchain", "example-network")
FABRIC = "2.5.16"


def go_test(module):
    need("go")
    r = subprocess.run(["go", "test", "-count=1", "./..."], cwd=os.path.join(ROOT, module), capture_output=True, text=True)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]
    return r.stdout


def test_chaincode_policy_and_example_transactions():
    """decideWrite / buildRecord unit tests, including examples/example_transactions.json."""
    assert "ok" in go_test("blockchain/chaincode/hrbac-cc")


def test_submitter_outages_retries_duplicates_signature_passthrough():
    assert "ok" in go_test("gateway/fabric-submitter")


def test_example_transactions_schema_and_placeholders():
    jsonschema = pytest.importorskip("jsonschema")
    doc = json.load(open(os.path.join(ROOT, "examples", "example_transactions.json")))
    schema = json.load(open(os.path.join(ROOT, "schemas", "example_transaction.schema.json")))
    jsonschema.validate(doc, schema)
    ids = [t["txid"] for t in doc["transactions"]]
    assert len(set(ids)) == len(ids)
    assert not any(re.fullmatch(r"[0-9a-f]{64}", t) for t in ids)    # never a real-looking Fabric txid
    decisions = {t["expected"]["decision"] for t in doc["transactions"]}
    assert decisions == {"allowed", "denied"}


def test_network_is_pinned_to_fabric_25():
    compose = yaml.safe_load(open(os.path.join(NET, "docker-compose.yaml")))
    images = {s: v.get("image", "") for s, v in compose["services"].items()}
    for s, img in images.items():
        if "fabric-peer" in img or "fabric-orderer" in img:
            assert img.startswith(f"hyperledger/fabric-{'peer' if 'peer' in img else 'orderer'}:{FABRIC}$"), (s, img)
    env = dict(line.split("=", 1) for line in open(os.path.join(NET, ".env.example")).read().splitlines()
               if "=" in line and not line.startswith("#"))
    for k in ("FABRIC_PEER_DIGEST", "FABRIC_ORDERER_DIGEST", "FABRIC_CA_DIGEST", "CADVISOR_DIGEST"):
        assert re.fullmatch(r"@sha256:[0-9a-f]{64}", env[k]), k
    pin = open(os.path.join(NET, "pin_images.sh")).read()
    assert f"hyperledger/fabric-peer:{FABRIC}" in pin and f"hyperledger/fabric-orderer:{FABRIC}" in pin
    assert env["CA_ADMIN_PASSWORD"] == "change-me-before-use"        # placeholder only
    assert FABRIC in open(os.path.join(NET, "network.sh")).read()


def test_channel_config_is_fabric_25_compatible():
    ctx = yaml.safe_load(open(os.path.join(NET, "configtx.yaml")))
    assert ctx["Capabilities"]["Channel"] == {"V2_0": True}               # V3_0 would need Fabric 3.x
    assert ctx["Capabilities"]["Application"] == {"V2_5": True}
    o = ctx["Orderer"]
    assert o["OrdererType"] == "etcdraft" and len(o["EtcdRaft"]["Consenters"]) == 3
    assert o["BatchTimeout"] == "500ms" and o["BatchSize"]["MaxMessageCount"] == 100
    opts = o["EtcdRaft"]["Options"]
    assert (opts["TickInterval"], opts["ElectionTick"], opts["HeartbeatTick"]) == ("500ms", 10, 1)
    orderer = yaml.safe_load(open(os.path.join(NET, "orderer.yaml")))
    assert "Throttling" not in orderer["General"]                         # Fabric 3.x-only key
    assert orderer["General"]["BootstrapMethod"] == "none"


def test_analytical_raft_parameters_match_network():
    from common import analytical_parameters
    raft = analytical_parameters()["raft"]
    o = yaml.safe_load(open(os.path.join(NET, "configtx.yaml")))["Orderer"]
    assert f"{raft['tick_interval_ms']}ms" == o["EtcdRaft"]["Options"]["TickInterval"]
    assert raft["election_tick"] == o["EtcdRaft"]["Options"]["ElectionTick"]
    assert f"{raft['batch_timeout_ms']}ms" == o["BatchTimeout"]


def test_gateway_identity_roster_comes_from_the_deployment_file():
    sh = open(os.path.join(NET, "network.sh")).read()
    assert "../../config/deployment.example.yaml" in sh and "mac_config" not in sh


def test_no_generated_material_is_tracked():
    r = subprocess.run(["git", "ls-files", "blockchain"], cwd=ROOT, capture_output=True, text=True)
    if r.returncode != 0:
        pytest.skip("not a git checkout")
    tracked = r.stdout.split()
    assert not [f for f in tracked if re.search(r"(/crypto/|/fabric-ca/|\.block$|\.env$|_sk$|\.pem$|\.key$)", f)]


@pytest.mark.skipif(os.environ.get("FABRIC_EXAMPLE_NETWORK") != "1", reason="set FABRIC_EXAMPLE_NETWORK=1 with the example network up")
def test_end_to_end_on_live_example_network(tmp_path):
    """8 steps: commit, local and chaincode refusal, peer/orderer outages, duplicates, nulls, Ed25519 signature."""
    import sys
    env = {**os.environ, "OUT": str(tmp_path / "submitter"), "PYTHON": sys.executable}
    r = subprocess.run([os.path.join(ROOT, "gateway", "fabric-submitter", "integration.sh")], cwd=ROOT, env=env,
                       capture_output=True, text=True, timeout=1800)
    assert r.returncode == 0, r.stdout[-4000:] + r.stderr[-4000:]
    passed = re.findall(r"^PASS (\d)", open(tmp_path / "submitter" / "summary.txt").read(), re.M)
    assert sorted(set(passed)) == [str(i) for i in range(1, 9)]
