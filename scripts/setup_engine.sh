#!/usr/bin/env bash
# Clone and pin the Battlecode 2026 engine + lectureplayer seed bot.
# Reads pins from configs/engine.lock.json. Requires: git, Java 21, Python 3.11+.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOCK="$ROOT/configs/engine.lock.json"

ENGINE_REPO=$(python3 -c "import json;print(json.load(open('$LOCK'))['repo'])")
ENGINE_COMMIT=$(python3 -c "import json;print(json.load(open('$LOCK'))['commit'])")
ENGINE_PATH=$(python3 -c "import json;print(json.load(open('$LOCK'))['local_path'])")
SEED_REPO=$(python3 -c "import json;print(json.load(open('$LOCK'))['seed_bot']['repo'])")

if [ ! -d "$ENGINE_PATH/.git" ]; then
  git clone "$ENGINE_REPO" "$ENGINE_PATH"
fi
git -C "$ENGINE_PATH" fetch origin "$ENGINE_COMMIT" 2>/dev/null || git -C "$ENGINE_PATH" fetch --unshallow || true
git -C "$ENGINE_PATH" checkout "$ENGINE_COMMIT" 2>/dev/null \
  || echo "WARN: could not check out pinned commit (shallow clone?); HEAD=$(git -C "$ENGINE_PATH" rev-parse HEAD)"

SEED_PATH="$(dirname "$ENGINE_PATH")/battlecode26-lectureplayer"
if [ ! -d "$SEED_PATH/.git" ]; then
  git clone "$SEED_REPO" "$SEED_PATH"
fi
echo "engine:        $(git -C "$ENGINE_PATH" rev-parse HEAD)"
echo "lectureplayer: $(git -C "$SEED_PATH" rev-parse HEAD)"
echo "OK. Run a smoke match with: python3 scripts/smoke_test.py"
