#!/usr/bin/env bash
# Throughput experiment on the example Fabric 2.5 network (benchmarks/workload.example.yaml).
# Results describe THIS run on THIS host (provenance example-network-run). They are not the
# manuscript's bench-measured figures (40.4 / 54.9 TPS), which came from a specific 4-peer deployment.
#
#   benchmarks/run_throughput.sh [--campaign NAME] [--conditions hrbac|baseline|baseline,hrbac]
#                                [--concurrency "10 20 ..."] [--reps N] [--only-rep K] [--fail-fast]
# --only-rep K runs repetition K only (to repeat invalidated trials inside an existing campaign).
#
# Every trial keeps its own directory (transactions.csv, summary.json, resources.csv,
# trial.json, loadgen.log); existing trial directories are never overwritten.
# Exit code: 0 all trials succeeded, 1 at least one trial failed, 2 environment problem.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
NET="$ROOT/blockchain/example-network"
WORKLOAD="${WORKLOAD:-$HERE/workload.example.yaml}"
PY="${PYTHON:-python3}"

yq() { "$PY" -c "import yaml,sys;d=yaml.safe_load(open('$WORKLOAD'));print(eval(sys.argv[1],{},{'d':d}))" "$1"; }

CAMPAIGN="throughput-$(date -u +%Y%m%dT%H%M%SZ)"
CONDITIONS="hrbac"
CONC="$(yq "' '.join(map(str,d['load_model']['concurrency_levels']))")"
REPS="$(yq "d['load_model']['repetitions']")"
FAIL_FAST=0
ONLY_REP=""
while [ $# -gt 0 ]; do
  case "$1" in
    --campaign) CAMPAIGN="$2"; shift 2 ;;
    --conditions) CONDITIONS="$2"; shift 2 ;;
    --concurrency) CONC="$2"; shift 2 ;;
    --reps) REPS="$2"; shift 2 ;;
    --only-rep) ONLY_REP="$2"; shift 2 ;;
    --fail-fast) FAIL_FAST=1; shift ;;
    *) echo "unknown option $1" >&2; exit 2 ;;
  esac
done
DURATION="$(yq "d['load_model']['duration_s']")"
WARMUP="$(yq "d['load_model']['warmup_s']")"
TX_TIMEOUT="$(yq "d['timeout_policy']['per_transaction_s']")"
SEED_BASE="$(yq "d['load_model']['seed_base']")"
OUT="$ROOT/results/benchmarks/$CAMPAIGN"

# ---------------------------------------------------------------- environment checks
fail_env() { echo "environment: $*" >&2; exit 2; }
command -v docker >/dev/null || fail_env "docker not found"
command -v "$PY" >/dev/null || fail_env "$PY not found"
"$PY" -c "import yaml" 2>/dev/null || fail_env "pip install --require-hashes -r requirements-lock.txt"
LOADGEN="$HERE/loadgen/loadgen"
if [ ! -x "$LOADGEN" ]; then
  command -v go >/dev/null || fail_env "go not found and loadgen not built"
  (cd "$HERE/loadgen" && go build -o loadgen .) || fail_env "loadgen build failed"
fi
for s in osn0.orderer.bench osn1.orderer.bench osn2.orderer.bench peer0.north.bench peer0.south.bench peer0.east.bench peer0.west.bench; do
  [ "$(docker inspect -f '{{.State.Health.Status}}' "$s" 2>/dev/null)" = healthy ] || fail_env "$s not healthy (network.sh up)"
done
docker inspect -f '{{.State.Running}}' hrbac-cc 2>/dev/null | grep -q true || fail_env "hrbac-cc not running (network.sh deploy)"
curl -sf http://localhost:8080/healthz >/dev/null || fail_env "cAdvisor not reachable on :8080"
ID_DIR="$NET/fabric-ca/bench-sns-001/msp"
[ -f "$ID_DIR/signcerts/cert.pem" ] || fail_env "bench identity missing (network.sh identity)"
KEY="$(ls "$ID_DIR"/keystore/*_sk 2>/dev/null | head -1)"
TLS_CA="$NET/crypto/peerOrganizations/north.bench/peers/peer0.north.bench/tls/ca.crt"
[ -n "$KEY" ] && [ -f "$TLS_CA" ] || fail_env "identity key or peer TLS CA missing"

mkdir -p "$OUT"
[ -f "$OUT/workload.yaml" ] || cp "$WORKLOAD" "$OUT/workload.yaml"
echo "campaign $CAMPAIGN: conditions=[$CONDITIONS] concurrency=[$CONC] reps=$REPS -> $OUT"
FAILED=0
CURRENT=""
for r in $( [ -n "$ONLY_REP" ] && echo "$ONLY_REP" || seq 1 "$REPS" ); do
  for c in $CONC; do
    for cond in ${CONDITIONS//,/ }; do
      if [ "$cond" != "$CURRENT" ]; then  # identical workload; only the chaincode's HRBAC_ENFORCE changes
        "$NET/network.sh" rbac "$([ "$cond" = hrbac ] && echo on || echo off)" >/dev/null || fail_env "could not switch RBAC"
        CURRENT="$cond"
      fi
      ID="$(printf 'c%03d-%s-r%d' "$c" "$cond" "$r")"
      DIR="$OUT/$cond/$(printf 'c%03d-r%d' "$c" "$r")"
      [ -e "$DIR" ] && { echo "refusing to overwrite $DIR" >&2; exit 2; }
      mkdir -p "$DIR"
      SEED=$((r * 1000 + SEED_BASE))
      printf '{"trial_id":"%s","campaign":"%s","condition":"%s","concurrency":%d,"repetition":%d,"seed":%d,"host":"%s","started_utc":"%s","exit_code":null}\n' \
        "$ID" "$CAMPAIGN" "$cond" "$c" "$r" "$SEED" "$(hostname)" "$(date -u +%FT%TZ)" > "$DIR/trial.json"
      "$PY" "$HERE/collect_metrics.py" --experiment "$CAMPAIGN" --trial "$ID" --out "$DIR/resources.csv" --stop-file "$DIR/.done" &
      COLLECTOR=$!
      timeout $((DURATION + WARMUP + TX_TIMEOUT + 60)) "$LOADGEN" -peer localhost:7051 -tls-host peer0.north.bench -tls-ca "$TLS_CA" \
        -cert "$ID_DIR/signcerts/cert.pem" -key "$KEY" -msp NorthMSP -channel benchchannel -chaincode hrbac-cc \
        -concurrency "$c" -duration "${DURATION}s" -warmup "${WARMUP}s" -timeout "${TX_TIMEOUT}s" -seed "$SEED" -out "$DIR" \
        > "$DIR/loadgen.log" 2>&1
      RC=$?
      touch "$DIR/.done"; wait "$COLLECTOR" 2>/dev/null; rm -f "$DIR/.done"
      "$PY" - "$DIR/trial.json" "$RC" <<'PY'
import json, sys
p, rc = sys.argv[1], int(sys.argv[2])
t = json.load(open(p)); t["exit_code"] = rc
json.dump(t, open(p, "w")); open(p, "a").write("\n")
PY
      if [ "$RC" -ne 0 ]; then
        FAILED=$((FAILED + 1)); echo "trial $ID FAILED (exit $RC), see $DIR/loadgen.log" >&2
        [ "$FAIL_FAST" = 1 ] && break 3
      else
        "$PY" -c "import json;s=json.load(open('$DIR/summary.json'));print('$ID', round(s['throughput_tps'],2),'tps', round(s['latency_ms']['mean'],1),'ms')"
      fi
    done
  done
done
"$PY" "$HERE/aggregate_trials.py" "$OUT"
[ "$FAILED" -eq 0 ] || { echo "$FAILED trial(s) failed" >&2; exit 1; }
