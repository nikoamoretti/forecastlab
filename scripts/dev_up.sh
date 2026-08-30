#!/bin/zsh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

API_HOST="${FORECASTLAB_API_HOST:-127.0.0.1}"
API_PORT="${FORECASTLAB_API_PORT:-8765}"
WEB_HOST="${FORECASTLAB_WEB_HOST:-127.0.0.1}"
WEB_PORT="${FORECASTLAB_WEB_PORT:-3000}"
DATA_DIR="${FORECASTLAB_DATA_DIR:-$ROOT/data}"
LOG_DIR="${FORECASTLAB_LOG_DIR:-$ROOT/logs}"
NO_BROWSER="${FORECASTLAB_NO_BROWSER:-0}"
EXIT_AFTER_READY="${FORECASTLAB_STARTUP_EXIT_AFTER_READY:-0}"
SKIP_SYNC="${FORECASTLAB_SKIP_DEPENDENCY_SYNC:-0}"

if [[ ! -f uv.lock || ! -f apps/web/package-lock.json ]]; then
  echo "ForecastLab startup failed: frozen dependency lockfiles are missing."
  exit 1
fi
if ! command -v uv >/dev/null 2>&1; then
  echo "ForecastLab startup failed: install uv first (https://docs.astral.sh/uv/)."
  exit 1
fi
if ! command -v npm >/dev/null 2>&1; then
  echo "ForecastLab startup failed: npm is required."
  exit 1
fi

if [[ "$SKIP_SYNC" != "1" ]]; then
  echo "Installing locked ForecastLab dependencies…"
  uv sync --extra dev --frozen
  if [[ ! -d apps/web/node_modules ]]; then
    (cd apps/web && npm ci)
  fi
fi
if [[ ! -x .venv/bin/python || ! -x apps/web/node_modules/.bin/next ]]; then
  echo "ForecastLab startup failed: locked dependencies are not installed."
  exit 1
fi

mkdir -p "$DATA_DIR/local" "$LOG_DIR"
export PYTHONPATH="$ROOT/packages/forecasting:$ROOT/apps/api:${PYTHONPATH:-}"
export FORECASTLAB_DATA_DIR="$DATA_DIR"
export FORECASTLAB_DATABASE_URL="${FORECASTLAB_DATABASE_URL:-sqlite:///$DATA_DIR/forecastlab.db}"
export FORECASTLAB_CREDENTIALS_PATH="${FORECASTLAB_CREDENTIALS_PATH:-$DATA_DIR/local/credentials.json}"
export FORECASTLAB_WEB_ORIGIN="http://$WEB_HOST:$WEB_PORT"
export FORECASTLAB_API_ORIGIN="http://$API_HOST:$API_PORT"
export PYTHONUNBUFFERED=1

FORECASTLAB_LOG_DIR="$LOG_DIR" ./scripts/dev_down.sh >/dev/null 2>&1 || true

cleanup_on_signal() {
  echo "Stopping ForecastLab…"
  FORECASTLAB_LOG_DIR="$LOG_DIR" ./scripts/dev_down.sh >/dev/null 2>&1 || true
  exit 0
}
trap cleanup_on_signal INT TERM

nohup .venv/bin/python -m uvicorn forecastlab_api.main:app \
  --host "$API_HOST" --port "$API_PORT" >> "$LOG_DIR/api.log" 2>&1 &
echo $! > "$LOG_DIR/api.pid"

api_ready=0
for _ in {1..80}; do
  if curl -sf "http://$API_HOST:$API_PORT/health" >/dev/null; then
    api_ready=1
    break
  fi
  sleep 0.25
done
if [[ "$api_ready" != "1" ]]; then
  echo "ForecastLab startup failed: API readiness timed out. See $LOG_DIR/api.log"
  FORECASTLAB_LOG_DIR="$LOG_DIR" ./scripts/dev_down.sh >/dev/null 2>&1 || true
  exit 1
fi

nohup .venv/bin/python -m forecastlab_api.worker >> "$LOG_DIR/worker.log" 2>&1 &
echo $! > "$LOG_DIR/worker.pid"
(
  cd apps/web
  nohup ./node_modules/.bin/next dev --hostname "$WEB_HOST" --port "$WEB_PORT" \
    >> "$LOG_DIR/web.log" 2>&1 &
  echo $! > "$LOG_DIR/web.pid"
)

ready=0
for _ in {1..120}; do
  if curl -sf "http://$API_HOST:$API_PORT/health" >/dev/null \
    && curl -sf "http://$WEB_HOST:$WEB_PORT" >/dev/null; then
    worker="$(curl -sf "http://$API_HOST:$API_PORT/health/worker" || true)"
    if [[ "$worker" == *'"fresh": true'* || "$worker" == *'"fresh":true'* ]]; then
      ready=1
      break
    fi
  fi
  sleep 0.5
done

if [[ "$ready" != "1" ]]; then
  echo "ForecastLab startup failed: stack readiness timed out. See $LOG_DIR"
  FORECASTLAB_LOG_DIR="$LOG_DIR" ./scripts/dev_down.sh >/dev/null 2>&1 || true
  exit 1
fi

LOCAL_URL="http://$WEB_HOST:$WEB_PORT"
echo "ForecastLab is ready at $LOCAL_URL"
echo "API health: http://$API_HOST:$API_PORT/health"
echo "Use Stop ForecastLab.command for a clean shutdown."

if [[ "$NO_BROWSER" != "1" ]]; then
  FORECASTLAB_LOCAL_URL="$LOCAL_URL" .venv/bin/python - <<'PY'
import os
import webbrowser

webbrowser.open(os.environ["FORECASTLAB_LOCAL_URL"])
PY
fi

if [[ "$EXIT_AFTER_READY" == "1" ]]; then
  FORECASTLAB_LOG_DIR="$LOG_DIR" ./scripts/dev_down.sh >/dev/null
  echo "ForecastLab isolated startup verification passed and shut down cleanly."
  exit 0
fi

wait
