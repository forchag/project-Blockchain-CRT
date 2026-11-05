#!/usr/bin/env bash
# End-to-end check of the Fabric submitter on the example Fabric 2.5 network (blockchain/example-network).
# Needs the network up with hrbac-cc deployed (HRBAC_ENFORCE=true) and the gateway identity:
#   blockchain/example-network/network.sh gateway-identity ex-gw-north
# All inputs are ILLUSTRATIVE: readings are synthesised here from fixed example values and the
# example roster (config/deployment.example.yaml), then pushed through the real gateway MAC code.
# Steps (each prints PASS/FAIL; the script exits non-zero on the first failure):
#   1  readings produced by the real gateway MAC code (FRAME_MODE 15 and 3) are committed VALID
#   2  a reading for a sensor outside the gateway's roster is refused locally, never sent
#   3  the chaincode itself refuses the gateway writing for an undelegated sensor
#   4  gateway peer down: readings stay queued; after the peer returns they commit
#   5  orderers down after endorsement: the same transaction ID is resubmitted, one transaction per reading
#   6  duplicate lines in readings.jsonl are not submitted again
#   7  the stored records name the gateway as submitter and keep non-reconstructed quantities as null
#   8  the stored record carries the gateway's Ed25519 signature, which verifies against the signed reading
# Output: $OUT (default results/submitter-<UTC time>), git-ignored. Its receipts (transaction IDs,
# block numbers) describe this run of the example network only.
set -euo pipefail
ROOT="$(cd "$(dirname "$(readlink -f "$0")")/../.." && pwd)"
NET="$ROOT/blockchain/example-network"
OUT="${OUT:-$ROOT/results/submitter-$(date -u +%Y%m%dT%H%M%SZ)}"
GW_MSP="$NET/fabric-ca/ex-gw-north/msp"
PY="${PYTHON:-python3}"
mkdir -p "$OUT"
S=$(( $(date +%s) / 60 % 60000 ))   # fresh sequence numbers per run, so reruns never touch earlier records
cd "$ROOT"
pass() { echo "PASS $*" | tee -a "$OUT/summary.txt"; }
fail() { echo "FAIL $*" | tee -a "$OUT/summary.txt"; exit 1; }
[ -f "$GW_MSP/signcerts/cert.pem" ] || { echo "run: blockchain/example-network/network.sh gateway-identity ex-gw-north" >&2; exit 2; }
SIGNKEY="$OUT/keys/ex-gw-north.ed25519.pem"   # fresh per run, inside the git-ignored output directory
"$PY" -m gateway.gateway_service --generate-signing-key "$SIGNKEY" >/dev/null

(cd gateway/fabric-submitter && go build -o "$OUT/fabric-submitter" .)
KEY=$(ls "$GW_MSP"/keystore/*_sk)
TLS="$NET/crypto/peerOrganizations/north.bench/peers/peer0.north.bench/tls/ca.crt"
submit() {
  "$OUT/fabric-submitter" -readings "$OUT/readings.jsonl" -state "$OUT/state" -peer localhost:7051 -tls-ca "$TLS" \
    -cert "$GW_MSP/signcerts/cert.pem" -key "$KEY" -msp NorthMSP -timeout "${TIMEOUT:-15s}" -once -max-backoff 2s \
    >>"$OUT/submitter.log" 2>&1
}
receipts() { "$PY" -c "import json,sys; print(sum(1 for l in open('$OUT/state/receipts.jsonl') if json.loads(l)['commit_status']==sys.argv[1]))" "$1"; }

# Readings from the real gateway intake: residue frames (15) and channel frames (3) through AckManager/ChannelAckManager.
produce() {  # produce <first seq> <frame mode> <nodes...>
  "$PY" - "$OUT/readings.jsonl" "$SIGNKEY" "$@" <<'PY'
import copy, json, sys, time
sys.path.insert(0, ".")
from gateway.mac import config as cfgmod, protocol as p
from gateway.mac.ack_manager import make_ack_manager
from gateway.mac.retry_manager import RetryManager
from gateway.gateway_service import completed_record, load_private_key
out, key = sys.argv[1], load_private_key(sys.argv[2])
seq, mode, nodes = int(sys.argv[3]), int(sys.argv[4]), [int(n) for n in sys.argv[5:]]
cfg = copy.deepcopy(cfgmod.load()); cfg.raw["frames"]["frame_mode"] = mode
am = make_ack_manager(cfg, "ex-gw-north", RetryManager(cfg.retry, 4000))
vals = [41.4, 25.3, 64.3, 6.82, 1500.0]
now = int(time.time() * 1000)
for n in nodes:
    res = [p.crt_residues(p.scale_value(q, v)) for q, v in enumerate(vals)]
    if mode == 3:  # pH sensor failed on this node: sent as 0xFF, must arrive on the ledger as null
        frames = [p.ChannelFrame(n, seq, i, False, False, tuple(None if q == 3 else res[q][i] for q in range(5))).encode() for i in range(3)]
    else:
        frames = [p.ResidueFrame(n, seq, q, i, True, False, False, res[q][i]).encode() for q in range(5) for i in range(3)]
    for f in frames:
        am.on_frame(f, now)
with open(out, "a") as fh:
    for r in am.pop_completed():
        fh.write(json.dumps(completed_record("ex-gw-north", r, key)) + "\n")
PY
}

# ---- 1 + 2: normal operation and local refusal
produce $((S+1)) 15 1 2 3
produce $((S+2)) 3 1 2
"$PY" - "$OUT/readings.jsonl" "$((S+1))" <<'PY'
import json, sys, time
r = {"gateway_id": "ex-gw-north", "node_id": 4, "sensor_id": "ex-sns-004", "device_id": "ex-node-004", "seq": int(sys.argv[2]), "event": False,
     "received_at_ms": int(time.time() * 1000), "values": {"soil_moisture": 40.0, "temperature": 20.0, "humidity": 50.0, "ph": 6.5,
     "illuminance": 1000.0}, "status": {k: "reconstructed" for k in ["soil_moisture", "temperature", "humidity", "ph", "illuminance"]},
     "residues_received": {k: 3 for k in ["soil_moisture", "temperature", "humidity", "ph", "illuminance"]}, "retries": 0}
open(sys.argv[1], "a").write(json.dumps(r) + "\n")
PY
submit || fail "step 1: submitter exited $?"
[ "$(receipts VALID)" = 5 ] && pass "1 five gateway-produced readings committed VALID (FRAME_MODE 15 and 3)" || fail "1 VALID count $(receipts VALID)"
grep -q "\"reading_id\":\"R004-$(printf %04d $((S+1)))-.*\"commit_status\":\"REJECTED\".*not delegated" "$OUT/state/receipts.jsonl" \
  && pass "2 ex-sns-004 (South) refused locally, no transaction" || fail "2 local refusal missing"

# ---- 3: chaincode-side refusal (bypasses the service's local check with the peer CLI)
export FABRIC_CFG_PATH="$NET" CORE_PEER_TLS_ENABLED=true CORE_PEER_LOCALMSPID=NorthMSP CORE_PEER_ADDRESS=localhost:7051
export CORE_PEER_TLS_ROOTCERT_FILE="$TLS"
TMPMSP="$OUT/gw-msp"; rm -rf "$TMPMSP"; cp -r "$GW_MSP" "$TMPMSP"; cp "$NET/crypto/peerOrganizations/north.bench/msp/config.yaml" "$TMPMSP/" 2>/dev/null || true
export CORE_PEER_MSPCONFIGPATH="$TMPMSP"
ORD_CA="$NET/crypto/ordererOrganizations/orderer.bench/orderers/osn0.orderer.bench/tls/ca.crt"
if peer chaincode invoke -o localhost:7050 --tls --cafile "$ORD_CA" -C benchchannel -n hrbac-cc \
     -c '{"Args":["WriteSensor","ex-sns-004","R004-9999-20990101","","{\"captured_at\":\"2099-01-01T00:00:00Z\",\"temp_c\":20}"]}' \
     >"$OUT/cc_denial.log" 2>&1; then
  fail "3 chaincode accepted ex-gw-north writing for ex-sns-004"
fi
grep -q "SENSOR_NOT_DELEGATED" "$OUT/cc_denial.log" && pass "3 chaincode refused ex-gw-north for ex-sns-004: SENSOR_NOT_DELEGATED" \
  || { cat "$OUT/cc_denial.log"; fail "3 unexpected chaincode response"; }

# ---- 4: gateway peer outage
produce $((S+3)) 15 1 2
docker stop peer0.north.bench >/dev/null
set +e; TIMEOUT=5s submit; rc=$?; set -e
[ "$rc" = 3 ] && [ "$(receipts VALID)" = 5 ] && pass "4a peer down: submitter kept 2 readings queued (exit 3)" || fail "4a rc=$rc VALID=$(receipts VALID)"
docker start peer0.north.bench >/dev/null
until docker inspect -f '{{.State.Health.Status}}' peer0.north.bench | grep -q healthy; do sleep 2; done
# a restarted peer needs a few seconds of gossip before discovery finds the other organisations' peers;
# until then the Gateway answers FailedPrecondition and the submitter keeps the readings queued
for _ in $(seq 1 40); do submit && break; sleep 5; done
[ "$(receipts VALID)" = 7 ] && pass "4b peer back: both queued readings committed" || fail "4b VALID=$(receipts VALID)"

# ---- 5: orderers down after endorsement
produce $((S+4)) 15 3
docker stop osn0.orderer.bench osn1.orderer.bench osn2.orderer.bench >/dev/null
set +e; TIMEOUT=5s submit; rc=$?; set -e
docker start osn0.orderer.bench osn1.orderer.bench osn2.orderer.bench >/dev/null
[ "$rc" = 3 ] || fail "5a expected outage exit, got $rc"
R8="R003-$(printf %04d $((S+4)))"
grep -q "\"event\":\"endorsed\",\"reading_id\":\"$R8" "$OUT/state/journal.jsonl" || fail "5a reading not endorsed before the outage"
until [ "$(docker inspect -f '{{.State.Health.Status}}' osn0.orderer.bench osn1.orderer.bench osn2.orderer.bench | sort -u)" = healthy ]; do sleep 2; done
for _ in $(seq 1 40); do submit && break; sleep 5; done
"$PY" - "$OUT/state" "$R8" <<'PY' || fail "5b"
import json, sys
j = [json.loads(l) for l in open(sys.argv[1] + "/journal.jsonl")]
e = [x for x in j if x["reading_id"].startswith(sys.argv[2]) and x["event"] == "endorsed"]
r = [json.loads(l) for l in open(sys.argv[1] + "/receipts.jsonl") if json.loads(l)["reading_id"].startswith(sys.argv[2])]
assert len(r) == 1 and r[0]["commit_status"] == "VALID", r
assert len(e) == 1 and r[0]["tx_id"] == e[0]["tx_id"], (e, r)
print("PASS 5 orderers down after endorsement: one transaction", r[0]["tx_id"][:16], "resubmitted until VALID in block", r[0]["block_number"])
PY
echo "PASS 5 orderer outage" >>"$OUT/summary.txt"

# ---- 6: duplicates in the source
head -n 2 "$OUT/readings.jsonl" >>"$OUT/readings.jsonl"
submit || fail "6 submitter exited $?"
n=$(wc -l <"$OUT/state/receipts.jsonl")
[ "$n" = 9 ] && pass "6 duplicate source lines skipped (9 receipts for 9 distinct readings)" || fail "6 receipts=$n"

# ---- 7: what is on the ledger (evaluate as a North farmer)
source "$NET/.env"
export FABRIC_CA_CLIENT_HOME="$NET/fabric-ca/client"
secret=$(openssl rand -hex 16)
FARMER="ex-usr-check-$(date +%s)-$$"   # fresh name per run: an earlier run's registration has another secret
fabric-ca-client register --caname ca-north --id.name "$FARMER" --id.secret "$secret" --id.type client \
  --id.attrs 'role=Farmer:ecert,zone=North:ecert' --tls.certfiles "$NET/fabric-ca/north/tls-cert.pem" >/dev/null 2>&1 || true
rm -rf "$OUT/farmer-msp"
fabric-ca-client enroll -u "https://${FARMER}:${secret}@localhost:7054" --caname ca-north --enrollment.attrs 'role,zone' \
  -M "$OUT/farmer-msp" --tls.certfiles "$NET/fabric-ca/north/tls-cert.pem" >/dev/null 2>&1 || fail "7 farmer enrollment"
cp "$NET/crypto/peerOrganizations/north.bench/msp/config.yaml" "$OUT/farmer-msp/" 2>/dev/null || true
CORE_PEER_MSPCONFIGPATH="$OUT/farmer-msp" peer chaincode query -C benchchannel -n hrbac-cc \
  -c '{"Args":["ReadSensorData","ex-sns-001","North","10"]}' >"$OUT/ledger_ex-sns-001.json" 2>"$OUT/query.err" || { cat "$OUT/query.err"; fail "7 query"; }
"$PY" - "$OUT/ledger_ex-sns-001.json" "R001-$(printf %04d $((S+2)))" <<'PY' || fail "7"
import json, sys
recs = [json.loads(x) for x in json.load(open(sys.argv[1]))]
mine = [r for r in recs if r.get("reading_id", "").startswith(sys.argv[2])]
assert len(mine) == 1, f"{len(mine)} records for {sys.argv[2]}"
r = mine[0]
assert r["submitter"] == "ex-gw-north" and r["submitter_role"] == "Gateway", r
assert r["ph"] is None and r["quality"]["ph"] == "invalid_at_node", r
assert r["temp_c"] == 25.3 and r["light_lux"] == 1500, r
print("PASS 7 ledger record:", json.dumps({k: r[k] for k in ["reading_id", "sensor_id", "submitter", "submitter_role", "temp_c", "ph"]}))
PY
echo "PASS 7 ledger record" >>"$OUT/summary.txt"

# ---- 8: gateway Ed25519 signature on the ledger record
"$PY" - "$OUT/ledger_ex-sns-001.json" "$OUT/readings.jsonl" "R001-$(printf %04d $((S+2)))" "${SIGNKEY%.pem}.pub.pem" <<'PY' || fail "8"
import json, sys
sys.path.insert(0, ".")
from gateway.gateway_service import load_public_key, verify_record
ledger = [json.loads(x) for x in json.load(open(sys.argv[1]))]
rec = next(r for r in ledger if r.get("reading_id", "").startswith(sys.argv[3]))
src = [json.loads(line) for line in open(sys.argv[2])]
signed = next(g for g in src if g["gateway_signature"]["sha256"] == rec["gateway_signature"]["sha256"])
ok, why = verify_record(signed, load_public_key(sys.argv[4]))
assert ok, why
print("PASS 8 ledger record's gateway_signature verifies (Ed25519, key", rec["gateway_signature"]["key_id"] + ")")
PY
echo "PASS 8 gateway signature" >>"$OUT/summary.txt"
cp "$OUT/state/receipts.jsonl" "$OUT/receipts.jsonl"
echo "all steps passed; output in $OUT"
