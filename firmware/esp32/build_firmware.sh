#!/usr/bin/env bash
# Builds the ESP32 reference firmware and records what was built.
#   firmware/esp32/build_firmware.sh [env]        (default env: node; others: node_3frame, bench_node)
# Output (git-ignored, never committed):
#   build/firmware/<env>/firmware.bin
#   build/firmware/<env>/build_manifest.json   (same fields as build_manifest.example.json)
# Needs PlatformIO Core 6.1.16 and network access to registry.platformio.org.
# The binary is a fresh build of this source; it is not a binary recovered from a field node.
set -euo pipefail
ENV="${1:-node}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
OUT="$ROOT/build/firmware/$ENV"
command -v pio >/dev/null || { echo "pio not found: pip install platformio==6.1.16" >&2; exit 2; }
cd "$HERE"
pio run -e "$ENV" --build-dir "$ROOT/build/pio"
BIN="$ROOT/build/pio/$ENV/firmware.bin"
[ -f "$BIN" ] || { echo "build produced no $BIN" >&2; exit 1; }
mkdir -p "$OUT"
cp "$BIN" "$OUT/firmware.bin"
python3 "$HERE/tools/write_build_manifest.py" --env "$ENV" --binary "$OUT/firmware.bin" --out "$OUT/build_manifest.json"
echo "firmware.bin: $(stat -c %s "$OUT/firmware.bin") bytes, sha256 $(sha256sum "$OUT/firmware.bin" | cut -d' ' -f1)"
