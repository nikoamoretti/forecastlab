#!/bin/zsh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
if [[ ! -d .venv ]]; then
  python3.12 -m venv .venv
fi
source .venv/bin/activate
python -m pip install -q --upgrade pip
python -m pip install -q -e ".[dev]"
if [[ ! -d apps/web/node_modules ]]; then
  (cd apps/web && npm install)
fi
mkdir -p data/local logs
if [[ ! -f .env ]]; then
  cp .env.example .env
fi
export PYTHONPATH="$ROOT/packages/forecasting:$ROOT/apps/api:${PYTHONPATH:-}"
export FORECASTLAB_DATABASE_URL="sqlite:///$ROOT/data/forecastlab.db"
export PYTHONUNBUFFERED=1
./scripts/dev_down.sh >/dev/null 2>&1 || true
nohup python -m uvicorn forecastlab_api.main:app --host 127.0.0.1 --port 8765 >> logs/api.log 2>&1 &
echo $! > logs/api.pid
for i in {1..40}; do
  if curl -sf http://127.0.0.1:8765/health >/dev/null; then
    break
  fi
  sleep 0.25
done
nohup python -m forecastlab_api.worker >> logs/worker.log 2>&1 &
echo $! > logs/worker.pid
(
  cd apps/web
  nohup npm run dev -- --hostname 127.0.0.1 --port 3000 >> "$ROOT/logs/web.log" 2>&1 &
  echo $! > "$ROOT/logs/web.pid"
)
for i in {1..60}; do
  if curl -sf http://127.0.0.1:8765/health >/dev/null && curl -sf http://127.0.0.1:3000 >/dev/null; then
    worker="$(curl -sf http://127.0.0.1:8765/health/worker || true)"
    if [[ "$worker" == *'"fresh": true'* || "$worker" == *'"fresh":true'* ]]; then
      python - <<'PY'
import webbrowser
webbrowser.open("http://127.0.0.1:3000")
PY
      echo "ForecastLab is running at http://127.0.0.1:3000"
      echo "API: http://127.0.0.1:8765/health"
      echo "Leave this window open, or use Stop ForecastLab.command."
      wait
      exit 0
    fi
  fi
  sleep 0.5
done
echo "Timed out waiting for health checks. See logs/"
exit 1
