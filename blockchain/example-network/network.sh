#!/usr/bin/env bash
# Example Fabric 2.5 network lifecycle (single host, docker compose). Self-contained: every
# certificate, key and enrollment secret is generated here and git-ignored.
#   ./network.sh up        cryptogen + start orderers, peers, CA, cAdvisor
#   ./network.sh channel   create benchchannel, join 3 orderers and 4 peers
#   ./network.sh deploy    package/install/approve/commit hrbac-cc (ccaas) and start it (CC_PREBUILT=1: build binary on host)
#   ./network.sh identity  issue bench-sns-001 (role=Sensor, zone=North) via Fabric CA
#   ./network.sh gateway-identity [ex-gw-north]  issue a gateway identity (role=Gateway, its zone's sensors) via Fabric CA
#   ./network.sh rbac on|off  restart the chaincode server with HRBAC_ENFORCE=true|false
#   ./network.sh down      stop and delete everything, including created crypto
# Needs: docker compose v2, Fabric 2.5.16 binaries (peer, configtxgen, osnadmin, cryptogen)
# and fabric-ca-client 1.5.13 on PATH (curl -sSL https://raw.githubusercontent.com/hyperledger/fabric/main/scripts/install-fabric.sh | bash -s -- binary 2.5.16 1.5.13)
set -euo pipefail
SELF="$(readlink -f "$0")"
cd "$(dirname "$SELF")"
export FABRIC_CFG_PATH="$PWD"
CH=benchchannel
CC=hrbac-cc
CC_VERSION=1.0
CC_SEQ=1
ORGS=(north south east west)
declare -A PORT=([north]=7051 [south]=8051 [east]=9051 [west]=10051)
declare -A MSP=([north]=NorthMSP [south]=SouthMSP [east]=EastMSP [west]=WestMSP)
OSN_TLS=crypto/ordererOrganizations/orderer.bench/orderers/osn0.orderer.bench/tls/ca.crt
ORD_ADMIN=crypto/ordererOrganizations/orderer.bench/users/Admin@orderer.bench/tls

need() { command -v "$1" >/dev/null || { echo "missing $1 on PATH" >&2; exit 2; }; }
env_file() { [ -f .env ] || { cp .env.example .env; echo "created .env from template: set CA_ADMIN_PASSWORD" >&2; exit 2; }; }

as_peer() {  # org
  local o=$1
  export CORE_PEER_TLS_ENABLED=true CORE_PEER_LOCALMSPID=${MSP[$o]}
  export CORE_PEER_TLS_ROOTCERT_FILE=$PWD/crypto/peerOrganizations/$o.bench/peers/peer0.$o.bench/tls/ca.crt
  export CORE_PEER_MSPCONFIGPATH=$PWD/crypto/peerOrganizations/$o.bench/users/Admin@$o.bench/msp
  export CORE_PEER_ADDRESS=localhost:${PORT[$o]}
}

wait_healthy() {
  local deadline=$((SECONDS + 180))
  for s in "$@"; do
    until [ "$(docker inspect -f '{{.State.Health.Status}}' "$s" 2>/dev/null)" = healthy ]; do
      [ $SECONDS -lt $deadline ] || { echo "$s not healthy after 180 s" >&2; docker logs --tail 50 "$s" >&2; exit 1; }
      sleep 2
    done
  done
}

up() {
  need cryptogen; env_file
  [ -d crypto ] || cryptogen generate --config=crypto-config.yaml --output=crypto
  for o in "${ORGS[@]}"; do  # cryptogen names the CA key *_sk; fabric-ca-server wants a fixed name
    k=$(ls crypto/peerOrganizations/$o.bench/ca/*_sk | head -1)
    [ -f "crypto/peerOrganizations/$o.bench/ca/priv_sk" ] || cp "$k" "crypto/peerOrganizations/$o.bench/ca/priv_sk"
  done
  docker compose up -d osn0.orderer.bench osn1.orderer.bench osn2.orderer.bench
  wait_healthy osn0.orderer.bench osn1.orderer.bench osn2.orderer.bench
  docker compose up -d peer0.north.bench peer0.south.bench peer0.east.bench peer0.west.bench ca.north.bench cadvisor
  wait_healthy peer0.north.bench peer0.south.bench peer0.east.bench peer0.west.bench
}

channel() {
  need configtxgen; need osnadmin; need peer
  configtxgen -configPath . -profile BenchChannel -channelID $CH -outputBlock $CH.block
  for i in 0 1 2; do
    osnadmin channel join --channelID $CH --config-block $CH.block -o localhost:$((7053 + 1000 * i)) \
      --ca-file $OSN_TLS --client-cert $ORD_ADMIN/client.crt --client-key $ORD_ADMIN/client.key
  done
  for o in "${ORGS[@]}"; do as_peer $o; peer channel join -b $CH.block; done
}

deploy() {
  need peer; env_file
  local pkg=$CC.tar.gz tmp; tmp=$(mktemp -d)
  printf '{"address":"hrbac-cc:9999","dial_timeout":"10s","tls_required":false}' > "$tmp/connection.json"
  printf '{"type":"ccaas","label":"%s_%s"}' $CC $CC_VERSION > "$tmp/metadata.json"
  tar -C "$tmp" -czf "$tmp/code.tar.gz" connection.json
  tar -C "$tmp" -czf $pkg metadata.json code.tar.gz
  rm -rf "$tmp"
  for o in "${ORGS[@]}"; do as_peer $o; peer lifecycle chaincode install $pkg; done
  local id; id=$(peer lifecycle chaincode calculatepackageid $pkg)
  sed -i "s|^CHAINCODE_ID=.*|CHAINCODE_ID=$id|" .env
  for o in "${ORGS[@]}"; do
    as_peer $o
    peer lifecycle chaincode approveformyorg -o localhost:7050 --tls --cafile $PWD/$OSN_TLS \
      --channelID $CH --name $CC --version $CC_VERSION --package-id "$id" --sequence $CC_SEQ
  done
  as_peer north
  local targets=()
  for o in "${ORGS[@]}"; do
    targets+=(--peerAddresses "localhost:${PORT[$o]}" --tlsRootCertFiles "$PWD/crypto/peerOrganizations/$o.bench/peers/peer0.$o.bench/tls/ca.crt")
  done
  peer lifecycle chaincode commit -o localhost:7050 --tls --cafile $PWD/$OSN_TLS --channelID $CH --name $CC \
    --version $CC_VERSION --sequence $CC_SEQ "${targets[@]}"
  if [ "${CC_PREBUILT:-0}" = 1 ]; then  # build on the host, package into the same distroless runtime image
    need go
    local ctx; ctx=$(mktemp -d)
    (cd ../chaincode/hrbac-cc && CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o "$ctx/hrbac-cc" .)
    printf 'FROM gcr.io/distroless/static-debian12:nonroot\nCOPY hrbac-cc /hrbac-cc\nUSER nonroot\nEXPOSE 9999\nENTRYPOINT ["/hrbac-cc"]\n' > "$ctx/Dockerfile"
    docker build -q -t agri/hrbac-cc:1.0 "$ctx" >/dev/null
    rm -rf "$ctx"
    docker compose up -d --no-build hrbac-cc
  else
    docker compose up -d --build hrbac-cc
  fi
}

identity() {
  need fabric-ca-client; env_file
  # shellcheck disable=SC1091
  source .env
  export FABRIC_CA_CLIENT_HOME=$PWD/fabric-ca/client
  local tls=$PWD/fabric-ca/north/tls-cert.pem   # the CA server's TLS certificate
  fabric-ca-client enroll -u "https://admin:${CA_ADMIN_PASSWORD}@localhost:7054" --caname ca-north --tls.certfiles "$tls"
  local secret; secret=$(openssl rand -hex 16)
  fabric-ca-client register --caname ca-north --id.name bench-sns-001 --id.secret "$secret" --id.type client \
    --id.attrs 'role=Sensor:ecert,zone=North:ecert' --tls.certfiles "$tls" || true
  fabric-ca-client enroll -u "https://bench-sns-001:${secret}@localhost:7054" --caname ca-north \
    --enrollment.attrs 'role,zone,hf.EnrollmentID' -M "$PWD/fabric-ca/bench-sns-001/msp" --tls.certfiles "$tls"
  echo "bench identity: fabric-ca/bench-sns-001/msp (git-ignored)"
}

# A gateway submits WriteSensor for the sensors in its "sensors" attribute and no others
# (blockchain/chaincode/hrbac-cc/policy.go). The roster is taken from
# config/deployment.example.yaml (or $DEPLOYMENT), so the CA attribute and the radio roster cannot drift apart.
# The example network has one CA (NorthMSP); a multi-organisation deployment would have each CA issue its own gateway.
gateway_identity() {
  need fabric-ca-client; env_file
  # shellcheck disable=SC1091
  source .env
  local gw="${1:-ex-gw-north}" zone sensors
  read -r zone sensors < <(python3 - "$gw" "${DEPLOYMENT:-../../config/deployment.example.yaml}" <<'PY'
import sys, yaml
cfg = yaml.safe_load(open(sys.argv[2]))
gw = sys.argv[1]
for zone, z in cfg["zones"].items():
    if z["gateway_id"] == gw:
        print(zone, "|".join(n["sensor_id"] for n in z["nodes"]))
        break
else:
    sys.exit(f"unknown gateway {gw}")
PY
)
  export FABRIC_CA_CLIENT_HOME=$PWD/fabric-ca/client
  local tls=$PWD/fabric-ca/north/tls-cert.pem
  fabric-ca-client enroll -u "https://admin:${CA_ADMIN_PASSWORD}@localhost:7054" --caname ca-north --tls.certfiles "$tls"
  local secret; secret=$(openssl rand -hex 16)
  fabric-ca-client register --caname ca-north --id.name "$gw" --id.secret "$secret" --id.type client \
    --id.attrs "role=Gateway:ecert,zone=${zone}:ecert,sensors=${sensors}:ecert" --tls.certfiles "$tls" || true
  fabric-ca-client enroll -u "https://${gw}:${secret}@localhost:7054" --caname ca-north \
    --enrollment.attrs 'role,zone,sensors,hf.EnrollmentID' -M "$PWD/fabric-ca/$gw/msp" --tls.certfiles "$tls"
  echo "gateway identity: fabric-ca/$gw/msp (git-ignored), zone $zone, sensors $sensors"
}

rbac() {
  env_file
  case "${1:-}" in on) v=true ;; off) v=false ;; *) echo "usage: $0 rbac on|off" >&2; exit 2 ;; esac
  sed -i "s|^HRBAC_ENFORCE=.*|HRBAC_ENFORCE=$v|" .env
  docker compose up -d --force-recreate hrbac-cc
  until docker logs hrbac-cc 2>&1 | grep -q "HRBAC_ENFORCE=$v"; do sleep 1; done
  echo "hrbac-cc running with HRBAC_ENFORCE=$v"
}

down() {
  docker compose down -v --remove-orphans
  rm -rf crypto fabric-ca $CH.block $CC.tar.gz
}

help() { sed -n '2,11p' "$SELF" | sed 's/^# \{0,1\}//'; }

case "${1:-help}" in
  up | channel | deploy | identity | rbac | down | help) "${1:-help}" "${@:2}" ;;
  gateway-identity) gateway_identity "${@:2}" ;;
  *) help; exit 2 ;;
esac
