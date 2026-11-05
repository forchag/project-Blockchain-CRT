#!/usr/bin/env bash
# Resolves the pinned image tags to registry digests and records them in .env, so docker compose
# pulls image:tag@sha256:<digest> and every rebuild of the example network uses identical images.
#   blockchain/example-network/pin_images.sh            # write digests into .env
#   blockchain/example-network/pin_images.sh --check    # verify .env against the registry, change nothing
# Pinned versions (docs/BLOCKCHAIN_REPRODUCIBILITY.md): Fabric 2.5.16, Fabric CA 1.5.13, cAdvisor v0.49.1.
set -euo pipefail
cd "$(dirname "$0")"
[ -f .env ] || cp .env.example .env
MODE="${1:-write}"
FAILED=0
digest_of() {  # image:tag -> sha256:... of the multi-arch index (what `docker pull image:tag` resolves)
  docker buildx imagetools inspect "$1" --format '{{json .Manifest}}' |
    python3 -c 'import json,sys;print(json.load(sys.stdin)["digest"])'
}
pin() {  # var image:tag
  local d cur
  d=$(digest_of "$2")
  cur=$(sed -n "s|^$1=@||p" .env)
  if [ "$MODE" = --check ]; then
    if [ "$cur" = "$d" ]; then echo "ok   $2 $d"; else echo "DIFF $2 pinned=${cur:-none} registry=$d"; FAILED=1; fi
  else
    sed -i "s|^$1=.*|$1=@$d|" .env
    echo "$2 -> $d"
  fi
}
pin FABRIC_PEER_DIGEST hyperledger/fabric-peer:2.5.16
pin FABRIC_ORDERER_DIGEST hyperledger/fabric-orderer:2.5.16
pin FABRIC_CA_DIGEST hyperledger/fabric-ca:1.5.13
pin CADVISOR_DIGEST gcr.io/cadvisor/cadvisor:v0.49.1
exit "$FAILED"
