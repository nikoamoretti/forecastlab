#!/bin/bash
# The ForecastLab research job, run on Nico's Mac by the LaunchAgent ai.forecastlab.research every
# 30 minutes. It updates its own clone to the base branch, then hands over to
# scripts/cursor_research.py daily, which does nothing outside weekday working hours (UTC) or once
# the day's research is pushed. Arguments pass through, e.g. --dry-run --branch <daily branch>.
#
# The whole body is one block so bash has read all of it before git rewrites this file.
{
  set -euo pipefail
  REPO="${FORECASTLAB_REPO:-$HOME/.forecastlab/repo}"
  BASE=grok/forecastlab-mvp
  cd "$REPO"
  git fetch -q origin "$BASE"
  git checkout -q --detach "origin/$BASE"
  # The job uses only the standard library (Python 3.9 or later), so it needs no virtualenv.
  exec python3 scripts/cursor_research.py daily "$@"
  exit
}
