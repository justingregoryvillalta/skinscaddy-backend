#!/usr/bin/env bash
# Render / production start: create tables, then listen on $PORT.
set -euo pipefail
cd "$(dirname "$0")"

python -c "from app.database import init_db; init_db()"

exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
