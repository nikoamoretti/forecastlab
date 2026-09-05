#!/bin/zsh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="${FORECASTLAB_LOG_DIR:-$ROOT/logs}"

for name in worker api web; do
  pid_file="$LOG_DIR/${name}.pid"
  if [[ ! -f "$pid_file" ]]; then
    continue
  fi
  pid="$(tr -cd '0-9' < "$pid_file")"
  if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
    kill "$pid" 2>/dev/null || true
    for _ in {1..20}; do
      if ! kill -0 "$pid" 2>/dev/null; then
        break
      fi
      sleep 0.1
    done
  fi
  rm -f "$pid_file"
done

echo "ForecastLab stopped."
