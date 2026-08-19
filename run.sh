#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if [[ ! -d .venv ]]; then
  if python3 -m venv .venv 2>/dev/null; then
    :
  elif command -v virtualenv >/dev/null; then
    virtualenv .venv
  else
    echo "Could not create .venv (python3-venv missing)."
    echo "Install it, or run: pip install virtualenv && virtualenv .venv"
    exit 1
  fi
fi
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -q -r requirements.txt

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "Created backend/.env from .env.example — edit SECRET_KEY before deploying."
fi

exec uvicorn app.main:app --reload --host 0.0.0.0 --port "${PORT:-8000}"
