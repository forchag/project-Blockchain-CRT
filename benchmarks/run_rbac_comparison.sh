#!/usr/bin/env bash
# RBAC comparison: the identical workload against the same network with the chaincode's
# access-control checks disabled (baseline, HRBAC_ENFORCE=false) and enabled (hrbac).
# Conditions are interleaved within every repetition so drift affects both equally.
#
#   benchmarks/run_rbac_comparison.sh [--campaign NAME] [--concurrency "..."] [--reps N]
#
# Output: results/benchmarks/<campaign>/{baseline,hrbac}/c<NNN>-r<k>/  (both configurations)
#         results/benchmarks/<campaign>/throughput_runs.csv
#         results/benchmarks/<campaign>/rbac_overhead.csv
# Every number in these files describes this run on this host (provenance example-network-run).
# Overhead (benchmarks/rbac_overhead.py), per concurrency level:
#   latency_overhead_pct     = (L_hrbac - L_baseline) / L_baseline x 100
#   throughput_reduction_pct = (T_baseline - T_hrbac) / T_baseline x 100
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
CAMPAIGN="rbac-$(date -u +%Y%m%dT%H%M%SZ)"
ARGS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --campaign) CAMPAIGN="$2"; shift 2 ;;
    --concurrency|--reps) ARGS+=("$1" "$2"); shift 2 ;;
    *) echo "unknown option $1" >&2; exit 2 ;;
  esac
done
"$HERE/run_throughput.sh" --campaign "$CAMPAIGN" --conditions baseline,hrbac "${ARGS[@]}"
RC=$?
[ "$RC" -eq 2 ] && exit 2
"${PYTHON:-python3}" "$HERE/rbac_overhead.py" "$ROOT/results/benchmarks/$CAMPAIGN/throughput_runs.csv" \
  --out "$ROOT/results/benchmarks/$CAMPAIGN/rbac_overhead.csv"
exit "$RC"
