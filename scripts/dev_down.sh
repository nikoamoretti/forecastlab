#!/bin/zsh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
for name in worker api web; do
  if [[ -f "logs/${name}.pid" ]]; then
    kill "$(cat "logs/${name}.pid")" 2>/dev/null || true
    rm -f "logs/${name}.pid"
  fi
done
pkill -f "forecastlab_api.worker" 2>/dev/null || true
pkill -f "uvicorn forecastlab_api.main:app" 2>/dev/null || true
pkill -f "next dev -p 3000" 2>/dev/null || true
echo "ForecastLab stopped."
